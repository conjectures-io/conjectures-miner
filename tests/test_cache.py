"""Short task names: what a miner types instead of a 71-character digest."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from tests.conftest import API, HOTKEY, TASK_DIGEST

from conjectures_miner.cache import (
    AmbiguousTaskError,
    CachedTask,
    TaskCache,
    TaskCacheFile,
    UnknownTaskError,
)


def cache_with(tmp_path: Path, *task_ids: str, api: str = API) -> TaskCache:
    cache = TaskCache(tmp_path, api)
    cache.save(
        TaskCacheFile(
            api_base_url=api,
            repository_commit="379fc029",
            fetched_at=datetime.now(UTC),
            bundle_format="conjectures-submission/v1",
            max_bundle_bytes=2097152,
            submission_price_rao=500_000_000,
            payment_recipient=HOTKEY,
            tasks=tuple(
                CachedTask(task_id=task_id, task_bundle_sha256=TASK_DIGEST) for task_id in task_ids
            ),
        )
    )
    return cache


def test_an_exact_id_wins_over_any_partial_match(tmp_path: Path):
    cache = cache_with(tmp_path, "erdos89", "erdos89-formalized")
    assert cache.resolve("erdos89").task_id == "erdos89"


def test_a_unique_prefix_is_enough(tmp_path: Path):
    cache = cache_with(tmp_path, "fc-erdos89-v1", "fc-erdos1094-v1")
    assert cache.resolve("fc-erdos89").task_id == "fc-erdos89-v1"


def test_a_unique_substring_is_enough(tmp_path: Path):
    cache = cache_with(tmp_path, "fc-379fc029-erdos89-v1", "fc-379fc029-erdos1094-v1")
    assert cache.resolve("erdos89").task_id == "fc-379fc029-erdos89-v1"


def test_an_ambiguous_needle_lists_the_candidates(tmp_path: Path):
    cache = cache_with(tmp_path, "fc-erdos89-a", "fc-erdos89-b")
    with pytest.raises(AmbiguousTaskError) as raised:
        cache.resolve("erdos89")
    assert raised.value.candidates == ["fc-erdos89-a", "fc-erdos89-b"]


def test_no_match_says_to_sync(tmp_path: Path):
    cache = cache_with(tmp_path, "fc-erdos89-a")
    with pytest.raises(UnknownTaskError) as raised:
        cache.resolve("erdos1094")
    assert "tasks sync" in (raised.value.hint or "")


def test_an_empty_cache_says_to_sync(tmp_path: Path):
    with pytest.raises(UnknownTaskError) as raised:
        TaskCache(tmp_path, API).resolve("anything")
    assert "tasks sync" in (raised.value.hint or "")


def test_two_validators_do_not_share_a_cache_file(tmp_path: Path):
    first = cache_with(tmp_path, "local-task", api="http://localhost:8000")
    second = cache_with(tmp_path, "production-task", api="https://api.conjectures.io")
    assert first.path != second.path
    assert first.resolve("local-task").task_id == "local-task"
    with pytest.raises(UnknownTaskError):
        second.resolve("local-task")


def test_a_corrupt_cache_is_a_miss_rather_than_a_failure(tmp_path: Path):
    cache = cache_with(tmp_path, "fc-erdos89-a")
    cache.path.write_text("{ not json")
    assert cache.load() is None
    assert cache.candidates("erdos") == []


def test_candidates_never_raise_without_a_cache(tmp_path: Path):
    assert TaskCache(tmp_path, API).candidates("erdos") == []
