"""`submission.json` -- the plan a build produces and a submit consumes.

The archive is built exactly once, by `build`, and is never rebuilt. This file points at it,
records its digest, and carries everything else a submission needs. `check` and `submit` are
handed the plan, resolve the archive, verify it still hashes to what was built, and send
those exact bytes.

Building once is what removes a whole class of problem. Nothing has to reproduce a
byte-identical zip later, so entry order, timestamps and platform-dependent header fields
stop mattering; what `check` approved is literally what `submit` sends. It also means the
proof source is irrelevant after `build` -- editing `Main.lean` afterwards does nothing
until you rebuild, which is a far clearer rule than rebuilding and hoping the bytes match.

What the plan does *not* get to decide is identity. `task_id`, `task_bundle_sha256`,
`proof_sha256` and `miner_hotkey` live in the archive's own `manifest.json`, because that is
the copy the validator parses and cross-checks against the authenticated request
(`verifier/bundle.py:524-532`). The `manifest` block here is a readable copy so a plan can
be reviewed without unzipping; if the two ever disagree, that is refused rather than
resolved, because the plan is what a human read and the archive is what would be sent.

The `payment.reference` slot is why this file exists rather than a bare zip: a later
`conjectures pay` writes into it, after which `conjectures submit` needs no arguments at
all.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

PLAN_SCHEMA_VERSION = 1
DEFAULT_PLAN_NAME = "submission.json"


@dataclass(frozen=True, slots=True)
class BundleRef:
    """Where the archive is, and what it must still hash to.

    `path` is stored **relative to the plan file**. Absolute paths break when the pair is
    moved or mounted somewhere else; paths relative to the working directory break as soon
    as the miner runs the command from elsewhere. Relative to the plan, the two travel
    together.
    """

    path: str
    sha256: str
    bytes: int


@dataclass(frozen=True, slots=True)
class PaymentRef:
    """The payment, once there is one.

    `reference` is null until paid, which is the normal state of a fresh plan.
    `price_rao_seen` and `recipient_seen` are what the validator reported at build time,
    kept so a change can be *detected*. Neither is what a miner should pay against -- a
    rotated recipient means TAO sent to an address no refusal will return.
    """

    reference: str | None
    price_rao_seen: int | None
    recipient_seen: str | None


@dataclass(frozen=True, slots=True)
class Provenance:
    """How the plan came to exist. Never sent anywhere."""

    built_at: str
    tool_version: str
    task_requested: str
    task_digest_source: str
    api_base_url: str
    repository_commit: str | None
    proof_source_path: str


@dataclass(frozen=True, slots=True)
class SubmissionPlan:
    """One submission, fully described except for what only the miner can supply."""

    schema_version: int
    bundle: BundleRef
    # Display copy of the archive's manifest. Readable, reviewable, and not authoritative.
    manifest: dict[str, object]
    payment: PaymentRef
    provenance: Provenance


class PlanError(Exception):
    """A plan that cannot be acted on. Always names the resolved path it tried."""


def write(plan_path: Path, plan: SubmissionPlan) -> Path:
    """Write `submission.json`, with the bundle path relativised against `plan_path`."""
    raise NotImplementedError


def read(plan_path: Path) -> SubmissionPlan:
    """Parse a plan. Raises `PlanError` on a missing file, bad JSON, or a stale schema."""
    raise NotImplementedError


def resolve_bundle_path(plan_path: Path, plan: SubmissionPlan) -> Path:
    """Turn `plan.bundle.path` into a real path, relative to the plan file's directory."""
    raise NotImplementedError


def load_verified(plan_path: Path) -> tuple[SubmissionPlan, bytes]:
    """Read a plan and return it with the archive bytes it vouches for.

    The single entry point for `check` and `submit`, so both apply the same gate:

    1. parse the plan;
    2. resolve the bundle path against the plan's directory, and report that resolved path
       in any error -- a path-relative mistake is baffling otherwise;
    3. read the archive and refuse unless it hashes to `bundle.sha256` and matches
       `bundle.bytes`; a mismatch means the archive was replaced, truncated, or rebuilt, and
       submitting bytes nobody checked is exactly what this prevents;
    4. parse the archive's own manifest and refuse if it disagrees with the plan's display
       copy, naming the fields that differ.

    Returns the bytes rather than a path so that no caller can re-read the file and get
    something else.
    """
    raise NotImplementedError


def missing_for_submit(plan: SubmissionPlan) -> list[str]:
    """What a plan still lacks before it can be submitted.

    Today that is only the payment reference. Returned as a list so the command can say
    everything that is missing at once instead of one round of trial and error per field.
    """
    raise NotImplementedError
