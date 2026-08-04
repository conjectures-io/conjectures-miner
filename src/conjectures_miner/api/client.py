"""The validator client. One method per endpoint, no rendering, no key handling.

Signed headers arrive from `signing`; this module never sees a keypair. Every non-2xx is
translated into an `ApiError` by `errors.translate`, so no command has to inspect a status
code.

Retries: idempotent reads may be retried freely. `POST /v1/submissions` may be retried
**only with the same idempotency key**, which is why the key is resolved and persisted
before the first attempt.
"""

from __future__ import annotations

from pathlib import Path
from types import TracebackType
from typing import Self

import httpx

from conjectures_miner.api import models
from conjectures_miner.settings import Settings


class ApiClient:
    """A configured `httpx.Client` and the eight calls the MVP makes."""

    def __init__(
        self, settings: Settings, transport: httpx.BaseTransport | None = None
    ) -> None:
        """`transport` exists for tests; production passes nothing."""

    def __enter__(self) -> Self: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None: ...

    # --- public reads, no auth -----------------------------------------------------------

    def list_tasks(self) -> models.TaskList:
        """`GET /v1/tasks` -- the allowlist, plus price and payment recipient."""
        raise NotImplementedError

    def read_task(self, task_id: str) -> models.TaskSummary:
        """`GET /v1/tasks/{task_id}`."""
        raise NotImplementedError

    def system_status(self) -> models.SystemStatus:
        """`GET /v1/system/status` -- `submissions_open`, pin rotation window, banner."""
        raise NotImplementedError

    # --- preflight: free, unauthenticated, and the MVP's stand-in for local checking ------

    def preflight(
        self, *, bundle: Path, task_id: str, task_bundle_sha256: str, hotkey_ss58: str
    ) -> models.PreflightResult:
        """`POST /v1/submissions/preflight`.

        Runs the same admission the paid path runs, charges nothing, and needs only the
        hotkey's address. Always answers 200: a refusal comes back as `ok: false` with a
        reason code and, when the failure has one, a line and column.
        """
        raise NotImplementedError

    # --- the paid path -------------------------------------------------------------------

    def submit(
        self, *, bundle: Path, headers: dict[str, str]
    ) -> models.SubmissionStatus:
        """`POST /v1/submissions`, bundle streamed as the raw body.

        `headers` must already carry the signature, the idempotency key, and the task,
        proof, and payment references.
        """
        raise NotImplementedError

    def read_submission(
        self, submission_id: str, *, headers: dict[str, str]
    ) -> models.SubmissionStatus:
        """`GET /v1/submissions/{id}` -- payment, verification, review, reward state."""
        raise NotImplementedError

    def read_report(
        self, submission_id: str, *, headers: dict[str, str]
    ) -> models.Report:
        """`GET /v1/submissions/{id}/report`.

        The immutable verifier report, available once verification finishes.
        """
        raise NotImplementedError
