"""Upload the AI chat logs behind a proof, before the proof is submitted."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from conjectures_miner import chat_logs
from conjectures_miner import plan as plan_module
from conjectures_miner.commands import auth, context
from conjectures_miner.errors import CliError
from conjectures_miner.plan import DEFAULT_PLAN

app = typer.Typer(
    help="Upload the AI chat logs that produced a proof. Every open problem requires them.",
    no_args_is_help=True,
)


@app.command("upload")
def upload(
    ctx: typer.Context,
    package: Annotated[
        Path,
        typer.Argument(exists=True, dir_okay=False, help="The chat-log package, a .zip file."),
    ],
    plan: Annotated[
        Path, typer.Option(help="The submission plan the logs must match, when there is one.")
    ] = DEFAULT_PLAN,
    submission: Annotated[
        str | None,
        typer.Option(help="A submitted proof whose reviewer asked for a new version of its logs."),
    ] = None,
    yes: Annotated[
        bool, typer.Option("--yes", help="Agree to the chat-log terms without the prompt.")
    ] = False,
) -> None:
    """Upload the logs for a proof you are about to submit. Submit it within 24 hours.

    Needs `conjectures auth login`: logs belong to the account your coldkey is linked to, and
    `conjectures submit` finds them there. After the proof is submitted, a new version can be
    uploaded only when a reviewer asks for one: pass `--submission <id>`.
    """
    app_ctx = context(ctx)
    # First, so a miner who is not signed in learns that before anything is read or shown.
    headers = auth.resolve(app_ctx)

    offer = app_ctx.client.chat_log_offer()
    if not offer.enabled:
        raise CliError("this validator is not accepting chat logs")
    pinned = (
        chat_logs.TERMS_VERSION,
        chat_logs.TERMS_SHA256,
        chat_logs.ASSENT_STATEMENTS_SHA256,
    )
    if (offer.terms_version, offer.terms_sha256, offer.assent_statements_sha256) != pinned:
        raise CliError(
            f"the validator's chat-log terms are {offer.terms_version}, not the ones this tool "
            "shows",
            hint="Upgrade conjectures-miner (`git pull && ./install.sh`), so the terms you agree "
            "to are the ones you were shown.",
        )

    raw = package.read_bytes()
    if len(raw) > offer.max_package_bytes:
        raise CliError(
            f"{package} is {len(raw)} bytes; the validator accepts at most "
            f"{offer.max_package_bytes}"
        )
    summary = chat_logs.summarise(raw)

    state = None
    if submission is not None:
        # Before the prompt: a revision nobody asked for is refused, so say so before agreeing.
        state = app_ctx.client.chat_log_state(submission, headers=headers)
        if not state.eligible or state.claim is None:
            raise CliError(
                state.eligibility_reason or "these logs cannot take a new version now",
                hint="Check `conjectures logs status --submission <id>`: a reviewer's request "
                "comes with a note saying what is missing.",
            )
        if state.claim.review_note:
            app_ctx.render.note(f"The reviewer asked: {state.claim.review_note}")
    # The validator binds logs to the exact proof and task, so a package for anything other than
    # the sealed archive is refused when the proof arrives -- better said now.
    elif plan.is_file():
        archive = plan_module.load(plan, None).archive
        expected = {
            "task_id": archive.task_id,
            "task_bundle_sha256": archive.task_bundle_sha256,
            "final_proof_sha256": archive.proof_sha256,
        }
        for field, value in expected.items():
            if getattr(summary, field) != value:
                raise CliError(
                    f"the package's {field} is {getattr(summary, field)}, but {plan} seals {value}",
                    hint="Export the logs for the proof you built, or pass the plan it belongs "
                    "to with --plan.",
                )

    app_ctx.render.preview(
        {
            "package": str(package),
            "bytes": len(raw),
            "task_id": summary.task_id,
            "final_proof_sha256": summary.final_proof_sha256,
            "terms": chat_logs.TERMS_URL,
        },
        title="about to upload chat logs" if state is None else "about to upload a new version",
    )
    app_ctx.render.note(f"Read the chat-log terms: {chat_logs.TERMS_URL}")
    app_ctx.render.note("Uploading states, on your behalf:")
    app_ctx.render.log(chat_logs.ASSENT)
    if not yes and not app_ctx.render.confirm("Agree, and upload?"):
        raise typer.Abort

    consent = {
        "terms_version": chat_logs.TERMS_VERSION,
        "terms_sha256": chat_logs.TERMS_SHA256,
        "assent_statements_sha256": chat_logs.ASSENT_STATEMENTS_SHA256,
    }
    if state is not None and state.claim is not None and submission is not None:
        revised = app_ctx.client.revise_chat_logs(
            submission,
            package=raw,
            headers=headers,
            expected_state_version=state.claim.state_version,
            **consent,
        )
        app_ctx.render.data(
            {
                "id": str(revised.id),
                "status": revised.status,
                "revision": revised.revision,
                "package_sha256": revised.package_sha256,
                "terms_version": revised.terms_version,
            },
            title="new version uploaded",
        )
        app_ctx.render.note("The reviewer will look at this version next.")
        return

    deposit = app_ctx.client.deposit_chat_logs(package=raw, headers=headers, **consent)
    app_ctx.render.data(
        {
            "id": str(deposit.id),
            "status": deposit.status,
            "package_sha256": deposit.package_sha256,
            "terms_version": deposit.terms_version,
            "expires_at": deposit.expires_at,
        },
        title="chat logs uploaded",
    )
    app_ctx.render.note(
        f"Now submit this exact proof within {offer.deposit_hours} hours: `conjectures submit`."
    )


@app.command("status")
def status(
    ctx: typer.Context,
    submission: Annotated[str, typer.Option(help="The submitted proof whose logs to show.")],
) -> None:
    """The review of a submitted proof's logs, and whether a new version may be uploaded."""
    app_ctx = context(ctx)
    state = app_ctx.client.chat_log_state(submission, headers=auth.resolve(app_ctx))
    claim = state.claim
    app_ctx.render.data(
        {
            "status": claim.status if claim else None,
            "revision": claim.revision if claim else None,
            "review_note": claim.review_note if claim else None,
            "new_version_allowed": state.eligible,
            "reason": state.eligibility_reason,
        },
        title="chat logs",
    )
    if state.eligible and state.chat_logs_required:
        app_ctx.render.note(
            "A reviewer asked for more. Upload a new version within 14 days: "
            f"`conjectures logs upload <package.zip> --submission {submission}`."
        )
