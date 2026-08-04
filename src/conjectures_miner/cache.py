"""The local copy of `GET /v1/tasks`. What makes short task names and shell completion possible.

A cached task digest is safe to build against: the validator resolves the task and refuses with
`TASK_NOT_ALLOWED` before it confirms the payment and before any row reaches `submissions`, so a
pool that rotated costs a rebuild rather than the payment. A cached `payment_recipient` is not
safe to pay -- nothing gives back TAO sent to a rotated address -- so price and recipient are
stored here only so a change can be detected.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, ValidationError

from conjectures_miner.errors import CliError

CACHE_SCHEMA_VERSION = 1
CACHE_FILE_SUFFIX = ".tasks.json"


class UnknownTaskError(CliError):
    exit_code = 2


class AmbiguousTaskError(CliError):
    exit_code = 2

    def __init__(self, needle: str, candidates: list[str]) -> None:
        shown = "\n  ".join(candidates[:10])
        more = "" if len(candidates) <= 10 else f"\n  ... and {len(candidates) - 10} more"
        super().__init__(f"{needle!r} matches {len(candidates)} tasks:\n  {shown}{more}")
        self.candidates = candidates


class CachedTask(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    task_id: str
    task_bundle_sha256: str
    target_type_sha256s: tuple[str, ...] = ()


class TaskCacheFile(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    schema_version: int = CACHE_SCHEMA_VERSION
    api_base_url: str
    repository_commit: str
    fetched_at: datetime
    bundle_format: str
    max_bundle_bytes: int
    submission_price_rao: int
    payment_recipient: str
    tasks: tuple[CachedTask, ...]

    def get(self, task_id: str) -> CachedTask | None:
        return next((task for task in self.tasks if task.task_id == task_id), None)


class TaskCache:
    """One API's cached task list, and short-name resolution against it."""

    def __init__(self, cache_dir: Path, api_base_url: str) -> None:
        self._dir = cache_dir
        self._api_base_url = api_base_url

    @property
    def path(self) -> Path:
        # Keyed by API, so pointing at a local validator and then at production cannot poison
        # one with the other's tasks. The slug is only there to keep the directory readable.
        slug = re.sub(r"[^a-z0-9]+", "-", self._api_base_url.lower()).strip("-")[:40]
        fingerprint = hashlib.sha256(self._api_base_url.encode()).hexdigest()[:12]
        return self._dir / f"{slug}-{fingerprint}{CACHE_FILE_SUFFIX}"

    def load(self) -> TaskCacheFile | None:
        """The cached list, or None when absent, unreadable, or written by an older schema."""
        try:
            payload = json.loads(self.path.read_text("utf-8"))
            cached = TaskCacheFile.model_validate(payload)
        except (OSError, json.JSONDecodeError, ValidationError):
            return None
        return cached if cached.schema_version == CACHE_SCHEMA_VERSION else None

    def require(self) -> TaskCacheFile:
        cached = self.load()
        if cached is None:
            raise UnknownTaskError(
                f"no task cache for {self._api_base_url}",
                hint="Run `conjectures tasks sync` first.",
            )
        return cached

    def save(self, payload: TaskCacheFile) -> Path:
        self._dir.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".partial")
        temporary.write_text(payload.model_dump_json(indent=2), encoding="utf-8")
        temporary.replace(self.path)
        return self.path

    def resolve(self, needle: str) -> CachedTask:
        """Turn a task id, or a unique prefix or substring of one, into the cached task."""
        cached = self.require()
        exact = cached.get(needle)
        if exact is not None:
            return exact
        matches = self._matches(cached, needle)
        if not matches:
            raise UnknownTaskError(
                f"no task matches {needle!r}",
                hint="Run `conjectures tasks sync`, or `conjectures tasks list` to see the pool.",
            )
        if len(matches) > 1:
            raise AmbiguousTaskError(needle, [task.task_id for task in matches])
        return matches[0]

    def candidates(self, needle: str) -> list[str]:
        cached = self.load()
        if cached is None:
            return []
        return [task.task_id for task in self._matches(cached, needle)]

    def age_seconds(self) -> float | None:
        cached = self.load()
        if cached is None:
            return None
        return (datetime.now(UTC) - cached.fetched_at).total_seconds()

    @staticmethod
    def _matches(cached: TaskCacheFile, needle: str) -> list[CachedTask]:
        prefixed = [task for task in cached.tasks if task.task_id.startswith(needle)]
        return prefixed or [task for task in cached.tasks if needle in task.task_id]


def complete_task_id(incomplete: str) -> list[str]:
    """Shell completion for a task option. Runs on every Tab, so it must never raise."""
    try:
        from conjectures_miner.settings import load

        settings = load()
        return TaskCache(settings.cache_dir, settings.api_root).candidates(incomplete)
    except Exception:  # a broken cache or a bad env var must not break the shell
        return []
