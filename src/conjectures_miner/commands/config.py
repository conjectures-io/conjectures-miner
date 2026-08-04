"""`conjectures config` -- read and write the user config file.

So that the validator URL and the wallet names are said once rather than on every command.
Writes the TOML at `settings.config_file_path()`, which sits below the environment and above
the defaults in the precedence order.

Only ever writes non-secret fields. There is no `config set` for anything resembling key
material, by design.
"""

from __future__ import annotations

from typing import Annotated

import typer

app = typer.Typer(help="Read and write the user config file.", no_args_is_help=True)


@app.command("show")
def show(
    ctx: typer.Context,
    resolved: Annotated[
        bool,
        typer.Option(
            "--resolved", help="Show effective values and where each came from."
        ),
    ] = False,
) -> None:
    """Print the config file, or with `--resolved` the effective settings.

    `--resolved` is the one that answers "why is it submitting there": it names the source
    of each value -- flag, environment, config file, or default.
    """


@app.command("set")
def set_value(
    ctx: typer.Context,
    key: Annotated[str, typer.Argument(help="Setting name, e.g. api_base_url.")],
    value: Annotated[str, typer.Argument()],
) -> None:
    """Set one field in the config file, creating the file and its directory if needed.

    Validated against `Settings` before it is written, so a bad value is refused here rather
    than surfacing as a broken tool on the next command.
    """


@app.command("path")
def path(ctx: typer.Context) -> None:
    """Print the config file path, whether or not it exists yet."""
