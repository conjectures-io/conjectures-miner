"""`conjectures verify` -- the validator's own verifier, running on the miner's host.

    verify --setup    clone the validator and the tasks repository, build both, then say if ready
    verify            report what the last setup left behind, and whether it still is

Checking a proof with it is the next thing this grows. Today `--setup` is the whole of it.

An answer from here is about the proof and not about the submission: the local build runs the
development sandbox, not the isolation a validator applies to a proof it did not write. Every
report says so rather than leaving it to be inferred.
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from typing import Annotated, Any

import typer

from conjectures_miner import verifier as verifier_module
from conjectures_miner.commands import context
from conjectures_miner.context import AppContext
from conjectures_miner.verifier import Check, Paths, VerifierError

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
    setup: Annotated[
        bool, typer.Option("--setup", help="Build or refresh the local verifier.")
    ] = False,
    ref: Annotated[
        str | None, typer.Option(help="Validator ref to build from. Overrides the configured one.")
    ] = None,
    offline: Annotated[
        bool, typer.Option("--offline", help="Skip the network reachability checks.")
    ] = False,
) -> None:
    """Report the local verifier, or with --setup, build it."""
    app_ctx = context(ctx)
    where = verifier_module.paths(app_ctx.settings)
    if setup:
        _setup(app_ctx, where, ref=ref, offline=offline)
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
