"""The plan `build` writes and `submit` consumes, and the gate both `check` and `submit` go through.

The archive is built exactly once and never rebuilt. The plan points at it, records its digest,
and carries the payment slot a later `conjectures pay` will fill. Building once means nothing has
to reproduce a byte-identical zip afterwards, and what `check` approved is literally what `submit`
sends. Identity is not the plan's to decide: `task_id`, `task_bundle_sha256`, `proof_sha256` and
`miner_hotkey` live in the archive's own manifest, because that is the copy the validator parses
and cross-checks against the authenticated request.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, get_args

from pydantic import BaseModel, ConfigDict, ValidationError

from conjectures_miner import bundle as bundle_module
from conjectures_miner.digest import sha256_prefixed
from conjectures_miner.errors import CliError

PlanFormat = Literal["conjectures-submission-plan/v1"]
PlanSchemaVersion = Literal[1]
PLAN_FORMAT: PlanFormat = get_args(PlanFormat)[0]
PLAN_SCHEMA_VERSION: PlanSchemaVersion = get_args(PlanSchemaVersion)[0]
# Not `submission.json`: that name is already taken by the manifest *inside* the archive, and
# unzipping beside the plan would overwrite it.
DEFAULT_PLAN_NAME = "submission.plan.json"


class PlanError(CliError):
    exit_code = 2


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BundleRef(Model):
    """Where the archive is, and what it must still hash to.

    `path` is relative to the plan file, so the two travel together when the pair is moved.
    """

    path: str
    sha256: str
    bytes: int


class PaymentRef(Model):
    """The payment, once there is one. `price_rao_seen` and `recipient_seen` detect a change;
    neither is what a miner should pay against."""

    reference: str | None = None
    price_rao_seen: int | None = None
    recipient_seen: str | None = None


class Provenance(Model):
    """How the plan came to exist. Never sent anywhere."""

    built_at: datetime
    tool_version: str
    task_requested: str
    api_base_url: str
    repository_commit: str | None = None
    proof_source: str


class SubmissionPlan(Model):
    """One submission, fully described except for what only the miner can supply."""

    schema_version: PlanSchemaVersion = PLAN_SCHEMA_VERSION
    format: PlanFormat = PLAN_FORMAT
    bundle: BundleRef
    # A readable copy of the archive's manifest, so a plan can be reviewed without unzipping.
    # Not authoritative: a disagreement is refused rather than resolved.
    manifest: dict[str, Any]
    payment: PaymentRef = PaymentRef()
    provenance: Provenance


@dataclass(frozen=True, slots=True)
class Loaded:
    """A verified archive and, when one was used, the plan that vouched for it."""

    archive: bundle_module.Bundle
    plan: SubmissionPlan | None
    source: Path

    @property
    def payment_reference(self) -> str | None:
        return self.plan.payment.reference if self.plan else None


def write(plan_path: Path, plan: SubmissionPlan) -> Path:
    plan_path.parent.mkdir(parents=True, exist_ok=True)
    plan_path.write_text(plan.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return plan_path


def read(plan_path: Path) -> SubmissionPlan:
    try:
        payload = json.loads(plan_path.read_text("utf-8"))
    except FileNotFoundError as exc:
        raise PlanError(
            f"no submission plan at {plan_path}",
            hint="Run `conjectures build` first, or pass --plan.",
        ) from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise PlanError(f"could not read {plan_path}: {exc}") from exc
    try:
        return SubmissionPlan.model_validate(payload)
    except ValidationError as exc:
        raise PlanError(
            f"{plan_path} is not a {PLAN_FORMAT} plan: {exc.error_count()} problems"
        ) from exc


def bundle_path(plan_path: Path, plan: SubmissionPlan) -> Path:
    return (plan_path.parent / plan.bundle.path).resolve()


def load(plan_path: Path | None, bundle_override: Path | None) -> Loaded:
    """The single gate for `check` and `submit`: resolve an archive and prove it is the right one.

    A bare `--bundle` is allowed because an archive is self-describing; a plan additionally has to
    still agree with the archive it points at.
    """
    if bundle_override is not None:
        return Loaded(
            archive=bundle_module.read(bundle_override), plan=None, source=bundle_override
        )
    if plan_path is None:
        raise PlanError("nothing to submit: pass --plan or --bundle")

    plan = read(plan_path)
    archive_path = bundle_path(plan_path, plan)
    if not archive_path.is_file():
        raise PlanError(
            f"{plan_path} points at {archive_path}, which does not exist",
            hint="Rebuild with `conjectures build`, or move the archive back beside the plan.",
        )

    # Digest before parse, so bytes nobody sealed are reported as the wrong archive rather than
    # as whatever the zip reader happens to make of them.
    raw = bundle_module.load_bytes(archive_path)
    if len(raw) != plan.bundle.bytes or sha256_prefixed(raw) != plan.bundle.sha256:
        raise PlanError(
            f"{archive_path} is not the archive {plan_path} was built for",
            hint="Replaced, truncated, or rebuilt. Rebuild the pair with `conjectures build`.",
        )

    archive = bundle_module.parse(raw, source=archive_path)
    differing = sorted(
        key
        for key in set(plan.manifest) | set(archive.manifest)
        if plan.manifest.get(key) != archive.manifest.get(key)
    )
    if differing:
        raise PlanError(
            f"{plan_path} and the archive's manifest disagree on {', '.join(differing)}",
            hint="Rebuild the pair with `conjectures build`.",
        )
    return Loaded(archive=archive, plan=plan, source=plan_path)


def record_payment(
    plan_path: Path, *, reference: str, price_rao: int, recipient: str
) -> SubmissionPlan:
    """Write a confirmed payment into the plan, so `submit` needs no `--payment-ref`.

    A plan that already cites a *different* payment is refused rather than overwritten. That
    plan's reference is the only local record of money that has moved, and replacing it would
    strand a real transfer with nothing pointing at it.
    """
    existing = read(plan_path)
    if existing.payment.reference not in (None, reference):
        raise PlanError(
            f"{plan_path} already cites payment {existing.payment.reference}",
            hint=f"Overwriting it would strand that transfer. Submit it first, or record "
            f"{reference} against a different plan with --plan.",
        )
    updated = existing.model_copy(
        update={
            "payment": PaymentRef(
                reference=reference, price_rao_seen=price_rao, recipient_seen=recipient
            )
        }
    )
    write(plan_path, updated)
    return updated


def missing_for_submit(plan: SubmissionPlan | None) -> list[str]:
    """What a plan still lacks, reported all at once rather than one field per attempt."""
    if plan is None or plan.payment.reference is None:
        return ["payment reference (--payment-ref)"]
    return []
