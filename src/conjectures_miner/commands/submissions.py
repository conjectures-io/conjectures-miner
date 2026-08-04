"""`conjectures submissions` -- what happened to a submission.

Both reads are authenticated with the **read scheme**, not the submit signature: a hotkey
may only read its own submissions.
"""

from __future__ import annotations

from typing import Annotated

import typer

app = typer.Typer(help="Track submitted proofs.", no_args_is_help=True)


@app.command("show")
def show(
    ctx: typer.Context,
    submission_id: Annotated[str, typer.Argument()],
    watch: Annotated[
        bool, typer.Option("--watch", help="Poll until verification settles.")
    ] = False,
) -> None:
    """`GET /v1/submissions/{id}` -- payment, verification, review, and reward state.

    `--watch` polls on a backoff and stops once the state is terminal.
    """


@app.command("report")
def report(
    ctx: typer.Context,
    submission_id: Annotated[str, typer.Argument()],
) -> None:
    """`GET /v1/submissions/{id}/report` -- the verifier's report.

    Absent until verification finishes, which is a normal answer rather than an error.
    """
