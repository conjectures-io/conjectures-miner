"""The typer application: global options, context assembly, sub-command wiring.

Settings are built in the callback rather than at import time, so a bad environment variable
cannot stop `--help` or `--version` from working.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from conjectures_miner import __version__
from conjectures_miner import settings as settings_module
from conjectures_miner.commands import config, submissions, submit, system, tasks
from conjectures_miner.context import AppContext
from conjectures_miner.errors import CliError
from conjectures_miner.output import Renderer

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


def _version(value: bool) -> None:
    if value:
        typer.echo(f"conjectures-miner {__version__}")
        raise typer.Exit


@app.callback()
def main(
    ctx: typer.Context,
    api: Annotated[str | None, typer.Option(help="Validator base URL.")] = None,
    wallet: Annotated[str | None, typer.Option(help="Bittensor wallet name.")] = None,
    hotkey: Annotated[str | None, typer.Option(help="Hotkey name within that wallet.")] = None,
    wallet_path: Annotated[Path | None, typer.Option(help="Override the wallet directory.")] = None,
    uri: Annotated[
        str | None,
        typer.Option(help="Development key such as //Alice. Local validators only."),
    ] = None,
    dev_signature: Annotated[
        bool | None,
        typer.Option(
            "--dev-signature/--no-dev-signature",
            help="Send the marker a validator outside PROD expects instead of signing.",
        ),
    ] = None,
    output: Annotated[str | None, typer.Option(help="auto | table | json")] = None,
    timeout: Annotated[float | None, typer.Option(help="Request timeout, seconds.")] = None,
    version: Annotated[bool, typer.Option("--version", callback=_version, is_eager=True)] = False,
) -> None:
    """Resolve settings, then hang the shared context off `ctx.obj`."""
    overrides = {
        "api_base_url": api,
        "wallet_name": wallet,
        "wallet_hotkey": hotkey,
        "wallet_path": wallet_path,
        "dev_signature": dev_signature,
        "output_format": output,
        "request_timeout_seconds": timeout,
    }
    resolved = settings_module.load(**overrides)
    ctx.obj = AppContext(
        settings=resolved,
        render=Renderer(resolved.output_format),
        overrides=overrides,
        uri=uri,
    )
    ctx.call_on_close(ctx.obj.close)


def run() -> int:
    """The console-script entry point: report refusals without a traceback."""
    try:
        app()
    except CliError as exc:
        Renderer().failure(exc)
        return exc.exit_code
    except SystemExit as exc:  # typer.Exit, --help, and usage errors already reported themselves
        return exc.code if isinstance(exc.code, int) else 0
    return 0
