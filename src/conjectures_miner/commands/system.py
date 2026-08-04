"""`conjectures status` -- is the validator accepting work right now."""

from __future__ import annotations

import typer

from conjectures_miner.commands import context


def status(ctx: typer.Context) -> None:
    """Report whether submissions are open, the queues, the rotation window, and cache currency."""
    app_ctx = context(ctx)
    live = app_ctx.client.system_status()
    cached = app_ctx.cache.load()

    app_ctx.render.data(
        {
            "status": live.status,
            "submissions_open": live.submissions_open,
            "repository_commit": live.repository_commit,
            "awaiting_verification": live.queue_depths.awaiting_verification,
            "awaiting_review": live.queue_depths.awaiting_review,
            "awaiting_reward": live.queue_depths.awaiting_reward,
            "pin_rotation_starts_at": live.pin_rotation.starts_at,
            "pin_rotation_in_progress": live.pin_rotation.in_progress,
            "task_cache_current": None
            if cached is None
            else cached.repository_commit == live.repository_commit,
            "banner": live.banner,
        },
        title=app_ctx.settings.api_root,
    )
    if not live.submissions_open:
        app_ctx.render.note("[yellow]Submissions are paused. Nothing to fix; come back later.[/]")
    elif cached is not None and cached.repository_commit != live.repository_commit:
        app_ctx.render.note("[yellow]The task pool moved. Run `conjectures tasks sync`.[/]")
