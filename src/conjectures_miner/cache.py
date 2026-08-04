"""The local task cache. What makes short task names and shell completion possible.

`GET /v1/tasks` is small, so a sync stores the whole response verbatim together with the
moment it was fetched and the `repository_commit` it came from. That commit is what makes
staleness detectable: `GET /v1/system/status` returns the same field, so one cheap call says
whether the entire cache has rotated out from under you.

Three rules this module exists to enforce.

**A cached task digest is safe to build against.** The validator resolves `task_id` plus
`task_bundle_sha256` and refuses with `TASK_NOT_ALLOWED` before it confirms the payment and
before any row reaches `submissions`, and the unique constraint on `payment_reference` lives
on that table alone. So a rotated pool costs a rebuild with the same payment reference, not
the payment. `check` surfaces it earlier and for free. This is why `build` is offline.

**A cached `payment_recipient` is not safe to pay.** Nothing on the validator side protects
a transfer sent to a rotated address, and no refusal gives it back. `submission_price_rao`
and `payment_recipient` are stored here only so a change can be *detected*; the values a
miner pays against must come from a fresh call. That asymmetry -- digests forgiving,
recipients not -- is the reason both are recorded rather than either being trusted.

**The cache is disposable; state is not.** This lives under the platform cache directory,
never beside the idempotency keys in `state`. Deleting it must cost nothing but a sync.

Keyed by API base URL, so pointing at a local validator and then at production cannot poison
one with the other's tasks.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

CACHE_SCHEMA_VERSION = 1
CACHE_FILE_SUFFIX = ".tasks.json"


class TaskResolutionError(Exception):
    """Base for the two ways a short task name can fail."""


class UnknownTask(TaskResolutionError):
    """Nothing in the cache matched. Usually means the cache needs a sync."""


class AmbiguousTask(TaskResolutionError):
    """More than one task matched. Carries the candidates so the caller can print them."""

    candidates: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CachedTask:
    """One allowlisted task, as `GET /v1/tasks` reported it."""

    task_id: str
    task_bundle_sha256: str
    target_type_sha256s: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class TaskCacheFile:
    """Everything one sync produced, plus when and against what.

    `submission_price_rao` and `payment_recipient` are recorded for comparison only. Nothing
    should ever render them as "pay this" without a fresh call.
    """

    schema_version: int
    api_base_url: str
    repository_commit: str
    fetched_at: str
    bundle_format: str
    max_bundle_bytes: int
    submission_price_rao: int
    payment_recipient: str
    tasks: tuple[CachedTask, ...]


class TaskCache:
    """Read and write one API's cached task list, and resolve short names against it."""

    def __init__(self, cache_dir: Path, api_base_url: str) -> None:
        """The file name derives from `api_base_url`, so two validators never collide."""

    @property
    def path(self) -> Path:
        """Where this API's cache file lives. Safe to delete at any time."""
        raise NotImplementedError

    def load(self) -> TaskCacheFile | None:
        """Return the cached list, or `None` when absent, unreadable, or a stale schema.

        Never raises. A corrupt cache is a cache miss, not a failed command -- the fix is
        always the same sync.
        """
        raise NotImplementedError

    def save(self, payload: TaskCacheFile) -> None:
        """Replace the cache atomically: write a temporary file, then rename."""

    def resolve(self, needle: str) -> str:
        """Turn a task id, unique prefix, or unique substring into a full task id.

        The point is that a miner types `erdos89` rather than a 71-character digest, and
        that it works in scripts and over ssh where completion does not. Raises
        `UnknownTask` or `AmbiguousTask`; an exact `task_id` match always wins over any
        partial one.
        """
        raise NotImplementedError

    def candidates(self, needle: str) -> list[str]:
        """Every task id `needle` could mean.

        Backs both completion and the ambiguity error.
        """
        raise NotImplementedError

    def age_seconds(self) -> float | None:
        """How long ago this cache was synced, or `None` if there is no cache."""
        raise NotImplementedError

    def is_current_for(self, repository_commit: str) -> bool:
        """Whether the cache was built against the commit the validator is serving now.

        False means the pool rotated and every digest in here is suspect.
        """
        raise NotImplementedError


def complete_task_id(incomplete: str) -> list[str]:
    """typer completion callback for a `--task` value.

    Runs on every Tab, so it reads the cache file and nothing else: no network, no settings
    validation, no exceptions. An absent or unreadable cache returns an empty list, which
    shows up as "no completions" rather than as a broken shell.
    """
    raise NotImplementedError
