"""`conjectures submissions` -- what happened to a submission.

`show` and `report` use the read signature scheme, not the submit one: a hotkey may read only its
own. `mine` is scoped differently and deliberately -- it authenticates as the *account*, with the
session token `conjectures auth login` stores, which is why it can list submissions made by hotkeys
this CLI is not currently pointed at. No per-request signature can ask that question, because a
signature names a key and the answer is keyed by account.
"""

from __future__ import annotations

import time
from typing import Annotated

import typer

from conjectures_miner.api import models
from conjectures_miner.commands import auth, context
from conjectures_miner.context import AppContext
from conjectures_miner.errors import ApiError
from conjectures_miner.signing import read_headers

app = typer.Typer(help="Track submitted proofs.", no_args_is_help=True)

POLL_FIRST_SECONDS = 5.0
POLL_MAX_SECONDS = 60.0
POLL_BACKOFF = 1.5

# The validator's own ceiling. Bounded here so an over-large page is a usage error the shell
# reports, rather than a round trip that comes back 422.
MAX_PAGE_SIZE = 100
DEFAULT_PAGE_SIZE = 25


@app.command("show")
def show(
    ctx: typer.Context,
    submission_id: Annotated[str, typer.Argument()],
    watch: Annotated[
        bool, typer.Option("--watch", help="Poll until verification settles.")
    ] = False,
    timeout: Annotated[
        float, typer.Option(help="Give up watching after this many seconds.")
    ] = 900.0,
) -> None:
    """Read payment, verification, review, and reward state."""
    app_ctx = context(ctx)
    status = _read(app_ctx, submission_id)
    if watch:
        status = _watch(app_ctx, submission_id, status, timeout)
    app_ctx.render.data(_summary(status), title=f"submission {submission_id}")


@app.command("report")
def report(ctx: typer.Context, submission_id: Annotated[str, typer.Argument()]) -> None:
    """Read the verifier's immutable report. Absent until verification finishes."""
    app_ctx = context(ctx)
    headers = read_headers(app_ctx.signer, submission_id=submission_id)
    try:
        answer = app_ctx.client.read_report(submission_id, headers=headers)
    except ApiError as exc:
        if exc.status_code != 409:
            raise
        app_ctx.render.note("Verification has not produced a report yet.")
        raise typer.Exit(1) from exc
    app_ctx.render.data(
        {"report_sha256": answer.report_sha256, **answer.report}, title="verifier report"
    )


@app.command("mine")
def mine(
    ctx: typer.Context,
    limit: Annotated[
        int, typer.Option(min=1, max=MAX_PAGE_SIZE, help="How many to fetch.")
    ] = DEFAULT_PAGE_SIZE,
    cursor: Annotated[
        str | None, typer.Option(help="Continue from a previous page's cursor.")
    ] = None,
) -> None:
    """Every submission on your account, across every hotkey linked to it. Needs `auth login`."""
    app_ctx = context(ctx)
    page = app_ctx.client.list_own_submissions(
        headers=auth.resolve(app_ctx), limit=limit, cursor=cursor
    )
    app_ctx.render.data([_row(item) for item in page.items], title="your submissions")
    if page.next_cursor:
        app_ctx.render.note(f"More: `conjectures submissions mine --cursor {page.next_cursor}`")


def _row(summary: models.SubmissionSummary) -> dict[str, object]:
    return {
        "submission_id": str(summary.id),
        "hotkey": summary.hotkey,
        "task_id": summary.task_id,
        "verification_status": summary.verification_status,
        "manual_review_status": summary.manual_review_status,
        "reward_status": summary.reward_status,
        "bounty_amount_rao": summary.bounty_amount_rao,
        "created_at": summary.created_at,
    }


def _read(app_ctx: AppContext, submission_id: str) -> models.SubmissionStatus:
    return app_ctx.client.read_submission(
        submission_id, headers=read_headers(app_ctx.signer, submission_id=submission_id)
    )


def _watch(
    app_ctx: AppContext,
    submission_id: str,
    status: models.SubmissionStatus,
    timeout: float,
) -> models.SubmissionStatus:
    deadline = time.monotonic() + timeout
    delay = POLL_FIRST_SECONDS
    while not status.settled:
        if time.monotonic() + delay > deadline:
            app_ctx.render.note(f"Still {status.verification_status} after {timeout:.0f}s.")
            break
        app_ctx.render.note(f"{status.verification_status}; checking again in {delay:.0f}s")
        time.sleep(delay)
        delay = min(delay * POLL_BACKOFF, POLL_MAX_SECONDS)
        try:
            status = _read(app_ctx, submission_id)
        except ApiError as exc:
            if exc.retry_after is None:
                raise
            # A rate limit or a transient refusal. It says nothing about the submission, so wait
            # as long as it asked and keep watching rather than reporting a failure.
            delay = max(delay, exc.retry_after)
            app_ctx.render.note(exc.message)
    return status


def _summary(status: models.SubmissionStatus) -> dict[str, object]:
    verification = status.verification
    return {
        "submission_id": str(status.submission_id),
        "task_id": status.task_id,
        "verification_status": status.verification_status,
        "manual_review_status": status.manual_review_status,
        "reward_status": status.reward_status,
        "failure_reason": status.failure_reason,
        "reason_code": verification.reason_code if verification else None,
        "stage": verification.stage if verification else None,
        "report_available": verification.report_available if verification else False,
        "bounty_amount_rao": status.bounty.amount_rao,
        "payment_reference": status.payment.reference,
        "payment_block": status.payment.block,
        "created_at": status.created_at,
        "updated_at": status.updated_at,
    }
