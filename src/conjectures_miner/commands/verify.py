"""`conjectures verify` -- the validator's own verifier, running on the miner's host.

    verify --setup                        clone the validator and the tasks repo, build, say ready
    verify                                report what the last setup left behind
    verify --proof Main.lean --task <id>  is this proof correct?

The third line is the one nothing else answers. `check` asks the validator whether the envelope is
acceptable; only the verifier can say whether the proof proves the stated theorem.

An answer from here is about the proof and not about the submission: the local build runs the
development sandbox, not the isolation a validator applies to a proof it did not write. Every
report says so rather than leaving it to be inferred.
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from pathlib import Path
from typing import Annotated, Any

import typer

from conjectures_miner import bundle as bundle_module
from conjectures_miner import verifier as verifier_module
from conjectures_miner.cache import complete_task_id
from conjectures_miner.commands import context
from conjectures_miner.commands.tasks import refresh_cache
from conjectures_miner.context import AppContext
from conjectures_miner.errors import CliError
from conjectures_miner.verifier import (
    Check,
    Paths,
    TaskBundle,
    TaskNotVerifiableError,
    VerifierError,
)

FIRST_RUN = (
    "First run: about 5 GB downloaded, 20 GB on disk and half an hour of Lean. Interrupting is "
    "safe -- re-running resumes rather than starting over."
)
NOT_AN_ATTESTATION = (
    "This runs the development sandbox, so it answers whether the proof is correct -- not whether "
    "a validator would accept the submission. `conjectures check` is the question about the "
    "envelope."
)


def verify(
    ctx: typer.Context,
    proof: Annotated[Path | None, typer.Option(help="The candidate Main.lean to check.")] = None,
    task: Annotated[
        str | None,
        typer.Option(help="Task id, or a unique prefix of one.", autocompletion=complete_task_id),
    ] = None,
    setup: Annotated[
        bool, typer.Option("--setup", help="Build or refresh the local verifier.")
    ] = False,
    ref: Annotated[
        str | None, typer.Option(help="Validator ref to build from. Overrides the configured one.")
    ] = None,
    offline: Annotated[
        bool, typer.Option("--offline", help="Skip the network reachability checks.")
    ] = False,
    refresh: Annotated[bool, typer.Option("--refresh", help="Sync the task cache first.")] = False,
    task_sha256: Annotated[
        str | None, typer.Option(help="Use this digest, and take --task as a literal task id.")
    ] = None,
) -> None:
    """Check a proof locally; with --setup, build the verifier that checks it."""
    app_ctx = context(ctx)
    where = verifier_module.paths(app_ctx.settings)
    if setup:
        _setup(app_ctx, where, ref=ref, offline=offline)
    elif proof is not None or task is not None:
        if proof is None or task is None:
            raise TaskNotVerifiableError("--proof and --task go together; pass both or neither")
        _verify_proof(
            app_ctx, where, proof=proof, task=task, refresh=refresh, task_sha256=task_sha256
        )
    else:
        _report(app_ctx, where)


def _setup(app_ctx: AppContext, where: Paths, *, ref: str | None, offline: bool) -> None:
    started = time.monotonic()
    repository = app_ctx.settings.verifier_repository
    chosen = ref or app_ctx.settings.verifier_ref
    existing = verifier_module.read_record(where)

    app_ctx.render.note("[bold]building a local verifier[/]")
    app_ctx.render.log(f"  from {repository} @ {chosen}")
    app_ctx.render.log(f"  into {where.home}")
    if existing is not None:
        app_ctx.render.note(f"Refreshing the setup built from {existing.validator_commit[:12]}.")
    elif where.validator.exists():
        app_ctx.render.note("Resuming a setup that did not finish.")
    else:
        app_ctx.render.note(FIRST_RUN)

    _gate(app_ctx, verifier_module.preflight(where.home, offline=offline), title="before cloning")

    app_ctx.render.note("\n[bold]1/4[/] checkouts")
    checkouts = verifier_module.sync_checkouts(where, repository=repository, ref=chosen)
    app_ctx.render.note(
        f"validator {checkouts.validator_commit[:12]}, tasks {checkouts.tasks_commit[:12]}"
    )

    app_ctx.render.note("\n[bold]2/4[/] prerequisites")
    ready, checks = verifier_module.prerequisites(where, offline=offline)
    _gate(app_ctx, checks, title="on this host", ready=ready)

    app_ctx.render.note("\n[bold]3/4[/] build -- this is the long one")
    verifier_module.bootstrap(where, on_line=app_ctx.render.log)
    # Written before the readiness check, because the build is what it describes: a host that
    # built everything and still reports unready has something to show for the half hour.
    record = verifier_module.write_record(
        where, repository=repository, ref=chosen, checkouts=checkouts
    )

    app_ctx.render.note("\n[bold]4/4[/] readiness")
    summary = verifier_module.summarise(verifier_module.doctor(where))
    _render(app_ctx, where, record, summary, took=_elapsed(started))
    if not summary["ready"]:
        raise typer.Exit(1)
    app_ctx.render.note(NOT_AN_ATTESTATION)


def _verify_proof(
    app_ctx: AppContext,
    where: Paths,
    *,
    proof: Path,
    task: str,
    refresh: bool,
    task_sha256: str | None,
) -> None:
    if verifier_module.read_record(where) is None:
        raise VerifierError(
            f"no local verifier at {where.home}",
            hint=f"Run `conjectures verify --setup`. {FIRST_RUN}",
        )
    try:
        proof_bytes = proof.read_bytes()
    except OSError as exc:
        raise CliError(f"could not read {proof}: {exc}") from exc
    # The same byte policy `build` applies, so an oversized or non-UTF-8 proof is refused for the
    # same reason it would be at submission, in no time rather than after a Lean build.
    bundle_module.check_proof(proof_bytes)

    if task_sha256 is not None:
        task_id, digest = task, task_sha256
    else:
        if refresh:
            refresh_cache(app_ctx)
        resolved = app_ctx.cache.resolve(task)
        task_id, digest = resolved.task_id, resolved.task_bundle_sha256

    found = _locate(app_ctx, where, task_id=task_id, digest=digest)
    app_ctx.render.note(
        f"[bold]verifying[/] a {found.task_mode} target -- up to "
        f"{found.timeout_seconds // 60} minutes, and quiet until it finishes"
    )
    code, report = verifier_module.run_verification(
        where,
        task=found.path,
        submission=proof,
        expected_task_sha256=digest,
        on_line=app_ctx.render.log,
    )

    app_ctx.render.data(verifier_module.summarise_verdict(report), title="verification")
    if report.get("accepted"):
        app_ctx.render.note(NOT_AN_ATTESTATION)
        return
    tail = str(report.get("stderr_tail") or "").strip()
    if tail:
        app_ctx.render.log(tail)
    if report.get("reason_code") == "RESOURCE_LIMIT":
        raise VerifierError(_resource_limit_explanation())
    if code >= 2:
        # The verifier's own split, kept rather than re-derived: 1 is a verdict about the proof,
        # 2 is a statement about the environment, and those are not the same news.
        raise VerifierError(
            f"verification could not run: {report.get('reason_code')}",
            hint="Nothing here is about the proof. `conjectures verify` reports the host.",
        )
    raise typer.Exit(1)


def _resource_limit_explanation() -> str:
    """RESOURCE_LIMIT is exit 1 from the verifier, and reporting it as one would be a lie here.

    On a validator, running as a dedicated user, it does mean the proof outgrew its budget. Locally
    it means the host ran out of something, which is not a statement about the proof -- and telling
    a miner their correct proof was refused is the worst thing this could do.
    """
    return (
        "the verification ran out of resources before it reached a verdict, so this says nothing "
        "about the proof. Try it again on a host with more memory, and with less running."
    )


def _locate(app_ctx: AppContext, where: Paths, *, task_id: str, digest: str) -> TaskBundle:
    """Refuse a retired or drifted task in two seconds rather than after a Lean build."""
    allowed = verifier_module.allowed_tasks(where)
    if allowed is None:
        app_ctx.render.note("[yellow]could not read the pool allowlist; skipping that check[/]")
    elif task_id not in allowed:
        raise TaskNotVerifiableError(
            f"{task_id} is not in the task pool this verifier was built against",
            hint="It has most likely been retired. `conjectures tasks sync` shows the current "
            "pool; if it is still listed there, the local pool is behind -- run "
            "`conjectures verify --setup` to move it forward.",
        )
    elif allowed[task_id] != digest:
        raise VerifierError(
            f"{task_id} does not have the digest you asked for:\n"
            f"  the pinned pool has {allowed[task_id]}\n"
            f"  you asked for       {digest}",
            hint="The task pool moved under one of you. `conjectures verify --setup` refreshes "
            "the checkout; if that changes nothing, the validator's pin is behind the pool it "
            "serves and verifying locally cannot agree with it yet.",
        )
    found = verifier_module.find_task(where, task_id)
    if found is None:
        raise VerifierError(
            f"{task_id} is allowlisted but has no bundle under {where.tasks / 'pool'}",
            hint="The tasks checkout is incomplete. Re-run `conjectures verify --setup`.",
        )
    return found


def _report(app_ctx: AppContext, where: Paths) -> None:
    record = verifier_module.read_record(where)
    if record is None:
        raise VerifierError(
            f"no local verifier at {where.home}",
            hint=f"Run `conjectures verify --setup`. {FIRST_RUN}",
        )
    summary = verifier_module.summarise(verifier_module.doctor(where))
    _render(app_ctx, where, record, summary, took=None)
    if not summary["ready"]:
        raise typer.Exit(1)


def _render(
    app_ctx: AppContext,
    where: Paths,
    record: verifier_module.SetupRecord,
    summary: dict[str, Any],
    *,
    took: str | None,
) -> None:
    app_ctx.render.data(
        {
            "ready": summary["ready"],
            "root": str(where.home),
            "validator_ref": record.ref,
            # The first question any disagreement with a real verdict raises.
            "validator_commit": record.validator_commit,
            "tasks_commit": record.tasks_commit,
            "formal_conjectures_commit": summary["formal_conjectures_commit"],
            "lean": summary["lean"],
            "sandbox_mode": summary["sandbox_mode"],
            "missing_tools": summary["missing_tools"],
            "built_at": record.completed_at,
            "took": took,
        },
        title="local verifier",
    )


def _gate(
    app_ctx: AppContext, checks: Sequence[Check], *, title: str, ready: bool | None = None
) -> None:
    """Let advisory failures through with a warning; stop on anything else, having shown it all."""
    blocked = [check for check in checks if not check.ok and not check.advisory]
    if not blocked and ready is not False:
        for check in (check for check in checks if not check.ok):
            app_ctx.render.note(f"[yellow]warn[/] {check.name}: {check.detail}")
        return
    # The whole table for a human to read, and the failures again in the error itself, which is
    # the only part of this that survives `--output json`.
    app_ctx.render.preview(verifier_module.rows(checks), title=f"prerequisites {title}")
    raise VerifierError(
        f"this host cannot finish the build ({title}):\n"
        + "\n".join(f"  {check.name}: {check.detail} -> {check.remedy}" for check in blocked),
        hint="Nothing was installed. Run those, then `conjectures verify --setup` again.",
    )


def _elapsed(started: float) -> str:
    seconds = int(time.monotonic() - started)
    return f"{seconds // 60}m {seconds % 60:02d}s"
