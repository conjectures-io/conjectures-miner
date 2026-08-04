"""What every command receives, assembled once by the root callback.

Its own module rather than `cli`: commands import this, and `cli` imports commands. Putting
it in `cli` would make that a cycle.

Reached as `ctx.obj` from any typer command.
"""

from __future__ import annotations

from dataclasses import dataclass

from conjectures_miner.api.client import ApiClient
from conjectures_miner.cache import TaskCache
from conjectures_miner.output import Renderer
from conjectures_miner.settings import Settings
from conjectures_miner.state import StateStore


@dataclass(frozen=True, slots=True)
class AppContext:
    settings: Settings
    client: ApiClient
    cache: TaskCache
    state: StateStore
    render: Renderer
