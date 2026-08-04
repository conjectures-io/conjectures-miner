"""Rendering. The only module that writes to stdout.

Two modes, chosen by `--output`: `table` for a human, `json` for a script. Keeping both
behind one object means no command has to care which is active, and no command grows its own
`print`.

Machine output goes to stdout; progress, warnings and errors go to stderr. `conjectures
tasks --output json | jq` has to work.
"""

from __future__ import annotations

from typing import Any, Protocol

import rich.console


class Renderer(Protocol):
    """What commands are allowed to do with their results."""

    def data(self, payload: Any, *, table: str | None = None) -> None:
        """Emit a successful result. `table` names the human layout to use, if any."""
        ...

    def note(self, message: str) -> None:
        """Say something to the human on stderr. Silent in json mode."""
        ...

    def failure(self, message: str, *, reason_code: str | None = None) -> None:
        """Report a failure on stderr, in the active format."""
        ...


class JsonRenderer:
    """Exactly one JSON document on stdout, nothing else."""


class TableRenderer:
    """Rich tables and coloured status, for a terminal."""


def build(output_format: str, console: rich.console.Console | None = None) -> Renderer:
    """Pick a renderer. Defaults to `table` when stdout is a tty, `json` otherwise."""
    raise NotImplementedError
