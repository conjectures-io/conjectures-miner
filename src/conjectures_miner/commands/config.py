"""`conjectures config` -- read and write the user config file.

Only non-secret fields, by design: there is no `config set` for anything resembling key material.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any

import tomli_w
import typer

from conjectures_miner import settings as settings_module
from conjectures_miner.commands import context
from conjectures_miner.errors import ConfigError
from conjectures_miner.settings import Settings

app = typer.Typer(help="Read and write the user config file.", no_args_is_help=True)


@app.command("show")
def show(
    ctx: typer.Context,
    resolved: Annotated[
        bool, typer.Option("--resolved", help="Show effective values and where each came from.")
    ] = False,
) -> None:
    """Print the config file, or with --resolved the effective settings and their source."""
    app_ctx = context(ctx)
    if resolved:
        app_ctx.render.data(
            settings_module.describe(app_ctx.settings, app_ctx.overrides),
            title="effective settings",
        )
        return
    app_ctx.render.data(
        settings_module.read_config_file(), title=str(settings_module.config_file_path())
    )


@app.command("set")
def set_value(
    ctx: typer.Context,
    key: Annotated[str, typer.Argument(help="Setting name, e.g. api_base_url.")],
    value: Annotated[str, typer.Argument()],
) -> None:
    """Set one field, validating it before it is written."""
    app_ctx = context(ctx)
    if key not in Settings.model_fields:
        raise ConfigError(
            f"{key!r} is not a setting",
            hint="Known: " + ", ".join(sorted(Settings.model_fields)),
        )
    parsed = _parse(value)
    settings_module.load(**{key: parsed})  # refuse a bad value here, not on the next command

    path = settings_module.config_file_path()
    contents = settings_module.read_config_file() | {key: parsed}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(tomli_w.dumps(_tomlable(contents)).encode("utf-8"))
    app_ctx.render.data({key: parsed, "file": str(path)}, title="set")


@app.command("path")
def path(ctx: typer.Context) -> None:
    """Print the config file path, whether or not it exists yet."""
    context(ctx).render.data({"path": str(settings_module.config_file_path())})


def _parse(value: str) -> Any:
    """Take JSON scalars where unambiguous, so numbers and booleans do not arrive as text."""
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def _tomlable(contents: dict[str, Any]) -> dict[str, Any]:
    return {
        key: str(value) if isinstance(value, Path) else value for key, value in contents.items()
    }
