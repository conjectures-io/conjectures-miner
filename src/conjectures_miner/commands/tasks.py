"""`conjectures tasks` -- the public allowlist, and the local cache that gives it short names."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import typer

from conjectures_miner.cache import CachedTask, TaskCacheFile, complete_task_id
from conjectures_miner.commands import context
from conjectures_miner.context import AppContext
from conjectures_miner.errors import CliError

app = typer.Typer(help="Browse and cache the allowlisted tasks.", no_args_is_help=True)

# Relative, so the statement lands beside the proof being written rather than in a cache
# directory. One sub-directory per task: a miner works on several, and they must not collide.
DEFAULT_CHALLENGE_DIR = Path("challenges")
CHALLENGE_NAME = "Challenge.lean"


def refresh_cache(app_ctx: AppContext) -> TaskCacheFile:
    """Fetch `GET /v1/tasks` and replace the cache. Shared with `build --refresh`."""
    published = app_ctx.client.list_tasks()
    cached = TaskCacheFile(
        api_base_url=app_ctx.settings.api_root,
        repository_commit=published.repository_commit,
        fetched_at=datetime.now(UTC),
        bundle_format=published.bundle_format,
        max_bundle_bytes=published.max_bundle_bytes,
        submission_price_rao=published.submission_price_rao,
        payment_recipient=published.payment_recipient,
        tasks=tuple(
            CachedTask(
                task_id=task.task_id,
                task_bundle_sha256=task.task_bundle_sha256,
                target_type_sha256s=task.target_type_sha256s,
            )
            for task in published.tasks
        ),
    )
    app_ctx.cache.save(cached)
    return cached


@app.command("sync")
def sync(ctx: typer.Context) -> None:
    """Fetch the allowlist and replace the local cache."""
    app_ctx = context(ctx)
    cached = refresh_cache(app_ctx)
    app_ctx.render.data(
        {
            "tasks": len(cached.tasks),
            "repository_commit": cached.repository_commit,
            "submission_price_rao": cached.submission_price_rao,
            "cache": str(app_ctx.cache.path),
        },
        title="synced",
    )


@app.command("list")
def list_tasks(
    ctx: typer.Context,
    refresh: Annotated[bool, typer.Option("--refresh", help="Sync first, then list.")] = False,
    filter_: Annotated[
        str | None, typer.Option("--filter", help="Show only task ids containing this.")
    ] = None,
    limit: Annotated[int | None, typer.Option(help="Show only the first N tasks.")] = None,
) -> None:
    """List tasks from the cache. Reads no network, so it is fast and works offline."""
    app_ctx = context(ctx)
    cached = refresh_cache(app_ctx) if refresh else app_ctx.cache.require()

    tasks = [task for task in cached.tasks if not filter_ or filter_ in task.task_id]
    app_ctx.render.note(
        f"{len(tasks)} of {len(cached.tasks)} tasks, cached {_age(app_ctx)} at commit "
        f"{cached.repository_commit[:12]}"
    )
    app_ctx.render.data(
        [
            {"task_id": task.task_id, "task_bundle_sha256": task.task_bundle_sha256}
            for task in tasks[:limit]
        ],
        title="tasks",
    )


@app.command("show")
def show_task(
    ctx: typer.Context,
    task: Annotated[str, typer.Argument(help="Task id, or a unique prefix of one.")],
    refresh: Annotated[
        bool, typer.Option("--refresh", help="Read the task from the validator, not the cache.")
    ] = False,
) -> None:
    """Show one task's published commitment, and say where the digest came from."""
    app_ctx = context(ctx)
    resolved = app_ctx.cache.resolve(task)
    if refresh:
        published = app_ctx.client.read_task(resolved.task_id)
        app_ctx.render.data(published, title="task (live)")
        return
    app_ctx.render.note(f"from the cache, synced {_age(app_ctx)}")
    app_ctx.render.data(resolved, title="task")


@app.command("challenge")
def challenge(
    ctx: typer.Context,
    task: Annotated[
        str,
        typer.Argument(help="Task id, or a unique prefix of one.", autocompletion=complete_task_id),
    ],
    directory: Annotated[
        Path, typer.Option("--dir", help="Where to save it. A sub-directory per task.")
    ] = DEFAULT_CHALLENGE_DIR,
) -> None:
    """Save a task's `Challenge.lean` -- the statement a proof has to close."""
    app_ctx = context(ctx)
    resolved = app_ctx.cache.resolve(task)
    conjecture = app_ctx.client.read_conjecture(resolved.task_id)
    issued = conjecture.task(resolved.task_id)
    if issued is None:
        raise CliError(
            f"the catalog no longer issues {resolved.task_id}",
            hint="Every task id changes on a pin rotation. Run `conjectures tasks sync`; this "
            f"conjecture is now issued as {', '.join(task.task_id for task in conjecture.tasks)}.",
        )

    destination = directory / resolved.task_id / CHALLENGE_NAME
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(issued.challenge_lean.encode("utf-8"))
    except OSError as exc:
        raise CliError(f"could not write {destination}: {exc}") from exc

    app_ctx.render.data(
        {
            "task_id": issued.task_id,
            "conjecture": conjecture.slug,
            # `counterexample` means the target is the negation of the statement below.
            "task_mode": issued.task_mode,
            "target_theorem": issued.machine_contract.target_theorem,
            "statement": conjecture.statement,
            "challenge": str(destination),
        },
        title=conjecture.title,
    )
    if issued.machine_contract.task_bundle_sha256 != resolved.task_bundle_sha256:
        app_ctx.render.note(
            "[yellow]The catalog and the cache disagree on this task's digest. "
            "Run `conjectures tasks sync` before building.[/]"
        )
    app_ctx.render.note(
        "Prove it in a Main.lean of your own: declarations only, no imports and no namespace."
    )


def _age(app_ctx: AppContext) -> str:
    seconds = app_ctx.cache.age_seconds()
    if seconds is None:
        return "never"
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60)):
        if seconds >= size:
            return f"{seconds / size:.0f}{unit} ago"
    return "just now"
