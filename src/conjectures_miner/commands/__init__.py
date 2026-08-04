"""Command bodies, one module per area.

Each command reads `ctx.obj`, calls the client, and hands the result to the renderer. Logic that
outgrows that belongs in `bundle`, `digest`, `plan`, `signing` or `state`.
"""

from __future__ import annotations

import typer

from conjectures_miner.context import AppContext


def context(ctx: typer.Context) -> AppContext:
    """The shared context, typed -- `ctx.obj` is `Any` as far as typer is concerned."""
    assert isinstance(ctx.obj, AppContext)
    return ctx.obj
