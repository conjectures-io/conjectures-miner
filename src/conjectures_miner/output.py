"""Rendering. The only module that writes to stdout.

Exactly one JSON document reaches stdout per command; everything else -- progress, previews,
warnings, errors -- goes to stderr, so `... --output json | jq` works.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import BaseModel
from rich.console import Console
from rich.table import Table

from conjectures_miner.errors import ApiError, CliError


class Renderer:
    def __init__(self, output_format: str = "auto", *, console: Console | None = None) -> None:
        self._out = console or Console()
        self._err = Console(stderr=True)
        resolved = output_format
        if resolved == "auto":
            resolved = "table" if self._out.is_terminal else "json"
        self._json = resolved == "json"

    def data(self, payload: Any, *, title: str | None = None) -> None:
        """The command's result. Call this once."""
        if self._json:
            # Written past rich: `--output json` is for a machine, and rich would colour it.
            print(_dumps(jsonable(payload)), file=sys.stdout)
        else:
            self._table(payload, title, self._out)

    def preview(self, payload: Any, *, title: str | None = None) -> None:
        """What is about to happen, for a human to read before confirming."""
        if not self._json:
            self._table(payload, title, self._err)

    def note(self, message: str) -> None:
        if not self._json:
            self._err.print(message)

    def failure(self, error: BaseException) -> None:
        if self._json:
            print(_dumps(_problem(error)), file=sys.stderr)
            return
        self._err.print(f"[bold red]error[/] {error}")
        reason_code = getattr(error, "reason_code", None)
        if reason_code:
            self._err.print(f"[dim]reason_code[/] {reason_code}")
        hint = getattr(error, "hint", None)
        if hint:
            self._err.print(f"[yellow]hint[/] {hint}")

    def confirm(self, question: str) -> bool:
        """Ask before something irreversible. Refuses rather than blocks when not a terminal."""
        if not sys.stdin.isatty():
            raise CliError(f"{question} -- pass --yes to confirm without a terminal")
        return self._err.input(f"{question} [y/N] ").strip().lower() in {"y", "yes"}

    @staticmethod
    def _table(payload: Any, title: str | None, console: Console) -> None:
        value = jsonable(payload)
        if isinstance(value, Sequence) and not isinstance(value, str):
            console.print(_rows_table(value, title))
        elif isinstance(value, Mapping):
            console.print(_pairs_table(value, title))
        else:
            console.print(value)


def jsonable(payload: Any) -> Any:
    if isinstance(payload, BaseModel):
        return payload.model_dump(mode="json")
    if isinstance(payload, Mapping):
        return {key: jsonable(value) for key, value in payload.items()}
    if isinstance(payload, Sequence) and not isinstance(payload, str | bytes):
        return [jsonable(item) for item in payload]
    return payload


def _dumps(value: Any) -> str:
    return json.dumps(value, indent=2, sort_keys=True, default=str)


def _problem(error: BaseException) -> dict[str, Any]:
    problem: dict[str, Any] = {"error": str(error)}
    if isinstance(error, ApiError):
        problem |= {"status_code": error.status_code, "reason_code": error.reason_code}
        problem |= error.detail
    return problem


def _rows_table(rows: Sequence[Any], title: str | None) -> Table:
    table = Table(title=title, title_justify="left", header_style="bold")
    if not rows:
        table.add_column("(nothing)")
        return table
    columns = list(rows[0]) if isinstance(rows[0], Mapping) else ["value"]
    for column in columns:
        table.add_column(column.replace("_", " "))
    for row in rows:
        values = row if isinstance(row, Mapping) else {"value": row}
        table.add_row(*(_cell(values.get(column)) for column in columns))
    return table


def _pairs_table(pairs: Mapping[str, Any], title: str | None) -> Table:
    table = Table(title=title, title_justify="left", show_header=False, box=None)
    table.add_column(style="dim")
    table.add_column()
    for key, value in pairs.items():
        table.add_row(str(key).replace("_", " "), _cell(value))
    return table


def _cell(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, bool):
        return "[green]yes[/]" if value else "[red]no[/]"
    if isinstance(value, list | tuple):
        return ", ".join(str(item) for item in value) or "-"
    if isinstance(value, dict):
        return json.dumps(value, sort_keys=True)
    return str(value)
