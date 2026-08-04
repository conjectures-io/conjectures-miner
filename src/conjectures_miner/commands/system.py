"""`conjectures status` -- is the validator accepting work right now.

Worth calling before paying. Submissions can be paused for a pin rotation, in which case
both intake paths refuse with 503 `SUBMISSIONS_PAUSED` -- the miner did nothing wrong and
should come back.

It is also the cheapest way to learn that the local task cache is stale, because the
response carries the same `repository_commit` the cache recorded at sync time.
"""

from __future__ import annotations

import typer


def status(ctx: typer.Context) -> None:
    """`GET /v1/system/status`: `submissions_open`, queue depths, rotation, banner.

    Also reports the live price and payment recipient -- this is a fresh call, so these are
    the values it is safe to pay against -- and whether the cached task list still matches
    the commit being served.
    """
