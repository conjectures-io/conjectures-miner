"""The typer application: global options, context assembly, sub-command wiring.

Wiring only. Every command body lives under `commands/`, so this file stays readable as the
map of what the tool can do.

`Settings` is built here, in the callback -- not at import time. A bad environment variable
should not stop `--help` or `--version` from working.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from conjectures_miner.commands import config, submissions, submit, system, tasks

app = typer.Typer(
    name="conjectures",
    help="Submit Lean proofs to the conjectures.io Subnet 66 validator.",
    no_args_is_help=True,
    add_completion=True,
)

app.add_typer(tasks.app, name="tasks")
app.add_typer(submissions.app, name="submissions")
app.add_typer(config.app, name="config")
app.command("build")(submit.build)
app.command("check")(submit.check)
app.command("submit")(submit.submit)
app.command("status")(system.status)


@app.callback()
def main(
    ctx: typer.Context,
    api: Annotated[str | None, typer.Option(help="Validator base URL.")] = None,
    wallet: Annotated[str | None, typer.Option(help="Bittensor wallet name.")] = None,
    hotkey: Annotated[
        str | None, typer.Option(help="Hotkey name within that wallet.")
    ] = None,
    wallet_path: Annotated[
        Path | None, typer.Option(help="Override the wallet directory.")
    ] = None,
    output: Annotated[str | None, typer.Option(help="json | table")] = None,
    timeout: Annotated[
        float | None, typer.Option(help="Request timeout, seconds.")
    ] = None,
) -> None:
    """Resolve settings, then hang the shared context off `ctx.obj`.

    Every option defaults to `None` so that not passing it leaves the environment and the
    config file in charge -- `settings.load()` drops the `None`s before resolving.
    """


def version_callback(value: bool) -> None:
    """`--version`, answered without touching settings, the network, or a wallet."""
