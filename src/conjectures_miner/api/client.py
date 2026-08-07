"""The validator client. One method per endpoint, no rendering, and it never sees a keypair.

Idempotent reads may be retried freely. `POST /v1/submissions` may be retried only with the same
idempotency key, which is why the key is resolved and persisted before the first attempt.
"""

from __future__ import annotations

from types import TracebackType
from typing import Any, Self

import httpx
from pydantic import ValidationError

from conjectures_miner.api import models
from conjectures_miner.bundle import BUNDLE_MEDIA_TYPE
from conjectures_miner.errors import CliError, TransportError, api_error
from conjectures_miner.settings import Settings


class ApiClient:
    def __init__(self, settings: Settings, transport: httpx.BaseTransport | None = None) -> None:
        self._upload_timeout = settings.upload_timeout_seconds
        self._http = httpx.Client(
            base_url=settings.api_root,
            timeout=settings.request_timeout_seconds,
            transport=transport,
            headers={"User-Agent": _user_agent()},
            follow_redirects=True,
        )

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    # --- public reads, no auth ----------------------------------------------------------

    def list_tasks(self) -> models.TaskList:
        return self._call("GET", "/v1/tasks", model=models.TaskList)

    def read_task(self, task_id: str) -> models.TaskSummary:
        return self._call("GET", f"/v1/tasks/{task_id}", model=models.TaskSummary)

    def read_conjecture(self, task_id: str) -> models.Conjecture:
        """The catalog entry. Keyed by task id: the website and a bundle name the same object."""
        return self._call("GET", f"/v1/catalog/conjectures/{task_id}", model=models.Conjecture)

    def system_status(self) -> models.SystemStatus:
        return self._call("GET", "/v1/system/status", model=models.SystemStatus)

    # --- preflight: free, unauthenticated, charges nothing ------------------------------

    def preflight(
        self, *, archive: bytes, task_id: str, task_bundle_sha256: str, hotkey_ss58: str
    ) -> models.PreflightResult:
        return self._call(
            "POST",
            "/v1/submissions/preflight",
            model=models.PreflightResult,
            content=archive,
            timeout=self._upload_timeout,
            headers={
                "Content-Type": BUNDLE_MEDIA_TYPE,
                "X-Conjectures-Task-Id": task_id,
                "X-Conjectures-Task-Sha256": task_bundle_sha256,
                "X-Conjectures-Hotkey": hotkey_ss58,
            },
        )

    # --- the paid path -------------------------------------------------------------------

    def submit(self, *, archive: bytes, headers: dict[str, str]) -> models.SubmissionStatus:
        return self._call(
            "POST",
            "/v1/submissions",
            model=models.SubmissionStatus,
            content=archive,
            headers={"Content-Type": BUNDLE_MEDIA_TYPE, **headers},
            timeout=self._upload_timeout,
        )

    def read_submission(
        self, submission_id: str, *, headers: dict[str, str]
    ) -> models.SubmissionStatus:
        return self._call(
            "GET",
            f"/v1/submissions/{submission_id}",
            model=models.SubmissionStatus,
            headers=headers,
        )

    def read_report(self, submission_id: str, *, headers: dict[str, str]) -> models.Report:
        return self._call(
            "GET",
            f"/v1/submissions/{submission_id}/report",
            model=models.Report,
            headers=headers,
        )

    # --- transport ------------------------------------------------------------------------

    def _call[T: models.Model](
        self,
        method: str,
        path: str,
        *,
        model: type[T],
        content: bytes | None = None,
        headers: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> T:
        try:
            response = self._http.request(
                method,
                path,
                content=content,
                headers=headers,
                timeout=timeout if timeout is not None else httpx.USE_CLIENT_DEFAULT,
            )
        except httpx.HTTPError as exc:
            raise TransportError(f"could not reach {self._http.base_url}: {exc}") from exc

        if response.status_code >= 400:
            raise api_error(
                response.status_code, _body(response), response.headers.get("retry-after")
            )
        try:
            return model.model_validate(_body(response))
        except ValidationError as exc:
            raise CliError(
                f"the validator answered {path} in a shape this CLI does not understand",
                hint=f"Upgrade conjectures-miner. It could not read {_unreadable(exc)}.",
            ) from exc


def _unreadable(exc: ValidationError) -> str:
    """Which fields, not how many.

    `extra="ignore"` means no error here is a field the CLI did not expect: every one is a field
    it needed and the answer did not have, or had in another shape.
    """
    named = [".".join(str(part) for part in error["loc"]) or "the body" for error in exc.errors()]
    shown = ", ".join(named[:4])
    return shown if len(named) <= 4 else f"{shown} and {len(named) - 4} more"


def _body(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return None


def _user_agent() -> str:
    from conjectures_miner import __version__

    return f"conjectures-miner/{__version__}"
