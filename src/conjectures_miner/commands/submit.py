"""`build`, `check`, `submit` -- the write path, in the order a miner walks it.

    build   write the archive and the plan       local, offline, no key unlock
    check   would the validator take this?       free, no auth, no key unlock
    pay     the submission price                 outside this tool, for now
    submit  spend the payment                    signed, irreversible

A stale task cache is not a hazard here: the validator resolves the task and refuses with
`TASK_NOT_ALLOWED` before it confirms the payment and before any row reaches `submissions`, and
the unique constraint on `payment_reference` lives on that table alone. So a digest that rotated
between sync and submit costs a rebuild with the same payment reference, not the payment.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import typer

from conjectures_miner import __version__
from conjectures_miner import bundle as bundle_module
from conjectures_miner import plan as plan_module
from conjectures_miner.cache import complete_task_id
from conjectures_miner.commands import context
from conjectures_miner.commands.tasks import refresh_cache
from conjectures_miner.errors import CliError
from conjectures_miner.plan import DEFAULT_PLAN_NAME
from conjectures_miner.signing import submit_headers

DEFAULT_BUNDLE = Path("submission.zip")
DEFAULT_PLAN = Path(DEFAULT_PLAN_NAME)


def build(
    ctx: typer.Context,
    proof: Annotated[Path, typer.Option(help="The candidate Main.lean.")],
    task: Annotated[
        str,
        typer.Option(help="Task id, or a unique prefix of one.", autocompletion=complete_task_id),
    ],
    output: Annotated[Path, typer.Option(help="Where to write the archive.")] = DEFAULT_BUNDLE,
    plan: Annotated[Path, typer.Option(help="Where to write the submission plan.")] = DEFAULT_PLAN,
    refresh: Annotated[bool, typer.Option("--refresh", help="Sync the task cache first.")] = False,
    task_sha256: Annotated[
        str | None, typer.Option(help="Use this digest, and take --task as a literal task id.")
    ] = None,
    hotkey_ss58: Annotated[
        str | None, typer.Option(help="Override the manifest hotkey address.")
    ] = None,
    solver_name: Annotated[str | None, typer.Option()] = None,
    solver_version: Annotated[str | None, typer.Option()] = None,
    force: Annotated[
        bool, typer.Option("--force", help="Overwrite a plan that already carries a payment.")
    ] = False,
) -> None:
    """Write the archive, and the plan that points at it. Offline unless --refresh."""
    app_ctx = context(ctx)
    cached = refresh_cache(app_ctx) if refresh else app_ctx.cache.load()

    _refuse_to_discard_payment(plan, force=force)
    if task_sha256 is not None:
        task_id, digest = task, task_sha256
    else:
        resolved = app_ctx.cache.resolve(task)
        task_id, digest = resolved.task_id, resolved.task_bundle_sha256

    try:
        proof_bytes = proof.read_bytes()
    except OSError as exc:
        raise CliError(f"could not read {proof}: {exc}") from exc
    bundle_module.check_proof(proof_bytes)

    manifest = bundle_module.build_manifest(
        task_id=task_id,
        task_bundle_sha256=digest,
        hotkey_ss58=app_ctx.hotkey_address(hotkey_ss58),
        proof=proof_bytes,
        solver_name=solver_name,
        solver_version=solver_version,
    )
    archive = bundle_module.write(output, manifest=manifest, proof=proof_bytes)

    written = plan_module.write(
        plan,
        plan_module.SubmissionPlan(
            bundle=plan_module.BundleRef(
                # Relative to the plan, so moving the pair together keeps it resolvable.
                path=_relative(output, plan),
                sha256=archive.sha256,
                bytes=len(archive.raw),
            ),
            manifest=manifest,
            payment=plan_module.PaymentRef(
                price_rao_seen=cached.submission_price_rao if cached else None,
                recipient_seen=cached.payment_recipient if cached else None,
            ),
            provenance=plan_module.Provenance(
                built_at=datetime.now(UTC),
                tool_version=__version__,
                task_requested=task,
                api_base_url=app_ctx.settings.api_root,
                repository_commit=cached.repository_commit if cached else None,
                proof_source=str(proof),
            ),
        ),
    )

    app_ctx.render.data(
        {
            "bundle": str(output),
            "bundle_sha256": archive.sha256,
            "bundle_bytes": len(archive.raw),
            "proof_sha256": archive.proof_sha256,
            "task_id": task_id,
            "task_bundle_sha256": digest,
            "miner_hotkey": archive.miner_hotkey,
            "plan": str(written),
        },
        title="built",
    )
    app_ctx.render.note(
        "Next: `conjectures check` -- it is free and it is the last step before money moves."
    )


def check(
    ctx: typer.Context,
    plan: Annotated[Path, typer.Option(help="The submission plan to test.")] = DEFAULT_PLAN,
    bundle: Annotated[
        Path | None, typer.Option(help="Test a bare archive instead, with no plan.")
    ] = None,
) -> None:
    """Ask preflight whether this submission would be accepted. Free, no credit, no auth."""
    app_ctx = context(ctx)
    loaded = plan_module.load(plan, bundle)
    archive = loaded.archive

    result = app_ctx.client.preflight(
        archive=archive.raw,
        task_id=archive.task_id,
        task_bundle_sha256=archive.task_bundle_sha256,
        hotkey_ss58=archive.miner_hotkey,
    )
    app_ctx.render.data(
        {
            "ok": result.ok,
            "task_id": archive.task_id,
            "reason_code": result.reason_code,
            "detail": result.detail,
            "line": result.line,
            "column": result.column,
            "proof_sha256": result.proof_sha256 or archive.proof_sha256,
        },
        title=str(loaded.source),
    )
    if not result.ok:
        # A refusal is a successful call, but the exit code has to gate `check && submit`.
        raise typer.Exit(1)
    app_ctx.render.note(
        "Preflight passed. It is not a guarantee, but everything static has been checked."
    )


def submit(
    ctx: typer.Context,
    plan: Annotated[Path, typer.Option(help="The submission plan to send.")] = DEFAULT_PLAN,
    bundle: Annotated[
        Path | None, typer.Option(help="Send a bare archive instead; needs --payment-ref.")
    ] = None,
    payment_ref: Annotated[
        str | None, typer.Option(help="The payment. Required only if the plan has none.")
    ] = None,
    idempotency_key: Annotated[
        str | None,
        typer.Option(help="Reuse a previous key to retry the same submission safely."),
    ] = None,
    yes: Annotated[bool, typer.Option("--yes", help="Skip the confirmation prompt.")] = False,
) -> None:
    """Sign and send the submission. This spends the payment."""
    app_ctx = context(ctx)
    loaded = plan_module.load(plan, bundle)
    archive = loaded.archive

    payment_reference = payment_ref or loaded.payment_reference
    if payment_reference is None:
        raise CliError(
            "this submission is missing: " + ", ".join(plan_module.missing_for_submit(loaded.plan))
        )
    if payment_ref and loaded.payment_reference and payment_ref != loaded.payment_reference:
        # Silently overriding a recorded payment is how the wrong extrinsic gets cited.
        app_ctx.render.note(
            f"[yellow]--payment-ref overrides the plan's {loaded.payment_reference}[/]"
        )

    signer = app_ctx.signer
    if archive.miner_hotkey != signer.ss58_address:
        raise CliError(
            f"the archive commits to {archive.miner_hotkey} but {signer.ss58_address} is signing",
            hint="Rebuild with the signing hotkey, or point --wallet/--hotkey at the bundle's.",
        )

    # Persisted before anything is sent: a key that exists only after a successful response is
    # useless for the case it exists to serve.
    record = app_ctx.state.reserve(
        hotkey=signer.ss58_address,
        task_id=archive.task_id,
        task_bundle_sha256=archive.task_bundle_sha256,
        proof_sha256=archive.proof_sha256,
        payment_reference=payment_reference,
        idempotency_key=idempotency_key,
    )

    app_ctx.render.preview(
        {
            "task_id": archive.task_id,
            "proof_sha256": archive.proof_sha256,
            "payment_reference": payment_reference,
            "idempotency_key": record.idempotency_key,
            "hotkey": signer.ss58_address,
        },
        title="about to submit",
    )
    if not yes and not app_ctx.render.confirm("Spend this payment?"):
        raise typer.Abort

    status = app_ctx.client.submit(
        archive=archive.raw,
        headers=submit_headers(
            signer,
            task_id=archive.task_id,
            task_bundle_sha256=archive.task_bundle_sha256,
            proof_sha256=archive.proof_sha256,
            payment_reference=payment_reference,
            idempotency_key=record.idempotency_key,
        ),
    )

    record.submission_id = str(status.submission_id)
    app_ctx.state.save(record)
    app_ctx.render.data(
        {
            "submission_id": str(status.submission_id),
            "verification_status": status.verification_status,
            "bounty_amount_rao": status.bounty.amount_rao,
            "idempotency_key": record.idempotency_key,
        },
        title="submitted",
    )
    app_ctx.render.note(f"Watch it: `conjectures submissions show {status.submission_id} --watch`")


def _relative(target: Path, plan: Path) -> str:
    base = plan.resolve().parent
    resolved = target.resolve()
    try:
        return resolved.relative_to(base).as_posix()
    except ValueError:
        # Somewhere else entirely; an absolute path is still resolvable, just not portable.
        return str(resolved)


def _refuse_to_discard_payment(plan: Path, *, force: bool) -> None:
    if force or not plan.is_file():
        return
    existing = plan_module.read(plan)
    if existing.payment.reference is not None:
        raise CliError(
            f"{plan} already carries payment {existing.payment.reference}",
            hint="Rebuilding would discard it. Use --plan to write elsewhere, or --force.",
        )
