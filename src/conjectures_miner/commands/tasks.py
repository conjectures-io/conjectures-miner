"""`conjectures tasks` -- the public task allowlist, and the local cache of it.

Unauthenticated: the pool and its digests are published. This is where a miner starts,
because `task_id` and `task_bundle_sha256` have to be committed to in the bundle.

`sync` is what lets every later command name a task by a short prefix instead of a
71-character digest, and it is what shell completion reads. See `cache` for why the cached
digest is never what gets committed to.
"""

from __future__ import annotations

from typing import Annotated

import typer

app = typer.Typer(help="Browse and cache the allowlisted tasks.", no_args_is_help=True)


@app.command("sync")
def sync(ctx: typer.Context) -> None:
    """Fetch `GET /v1/tasks` and replace the local cache.

    Reports how many tasks were stored and the `repository_commit` they belong to. Run it
    after a pin rotation, or whenever `conjectures status` says the cache is behind.
    """


@app.command("list")
def list_tasks(
    ctx: typer.Context,
    refresh: Annotated[
        bool, typer.Option("--refresh", help="Sync first, then list.")
    ] = False,
    filter_: Annotated[
        str | None,
        typer.Option("--filter", help="Show only task ids containing this substring."),
    ] = None,
    limit: Annotated[
        int | None, typer.Option(help="Show only the first N tasks.")
    ] = None,
) -> None:
    """List tasks from the cache, and say how old it is.

    Reads the cache rather than the network, so it is fast and works offline. An empty cache
    is reported as "run `conjectures tasks sync`", not as an error.
    """


@app.command("show")
def show_task(
    ctx: typer.Context,
    task: Annotated[str, typer.Argument(help="Task id, or a unique prefix of one.")],
    refresh: Annotated[
        bool,
        typer.Option(
            "--refresh", help="Read `GET /v1/tasks/{id}` instead of the cache."
        ),
    ] = False,
) -> None:
    """Show one task's published commitment.

    The digest is labelled with where it came from and how old it is, because a cached one
    is for reading and a fresh one is for committing to. Ambiguous input prints the
    candidates.
    """
