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

# Named by the validator's `submission_api/sessions.py`. The session cookie itself is HttpOnly and
# never read here -- httpx sends it back on its own.
CSRF_COOKIE = "conjectures_csrf"
CSRF_HEADER = "X-Conjectures-CSRF"


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

    # --- the account, reached with a session token ----------------------------------------
    #
    # Still no keypair here, and no reading of the session file: the bearer header arrives from the
    # caller exactly as the submit signature does.

    def cli_challenge(self, *, address: str) -> models.CliChallenge:
        return self._call(
            "POST",
            "/v1/auth/cli/challenge",
            model=models.CliChallenge,
            json={"address": address},
        )

    def cli_verify(self, *, address: str, nonce: str, signature: str) -> models.CliSession:
        """Redeem the signature for a token.

        The nonce goes back with it. It is not the proof -- the signature is, checked against the
        message the validator stored -- but it names *which* challenge is being answered. Without
        it the validator would have to resolve "the newest open challenge for this address", and
        hotkeys are public on chain: anyone could then request a challenge for someone else's
        hotkey on a loop and that miner's own signature would never be over the newest message.
        """
        return self._call(
            "POST",
            "/v1/auth/cli/verify",
            model=models.CliSession,
            json={"address": address, "nonce": nonce, "signature": signature},
        )

    def logout(self, *, headers: dict[str, str]) -> None:
        """Revoke this session. `204`, so there is no body to validate.

        Serves both credentials: `headers` is the bearer header for a CLI token, or the CSRF
        header for the cookie session `auth register` opens.
        """
        self._send("POST", "/v1/auth/logout", headers=headers)

    # --- the browser flow, used only by `auth register` -----------------------------------
    #
    # A cookie session, not a token, and this client is the browser for as long as one command
    # runs. `httpx.Client` keeps the jar; nothing here writes it anywhere. The validator restricts
    # linking a hotkey, repointing the payout and editing the profile to a *cookie* session
    # precisely because those compose into account takeover, so this is the only way to link --
    # and the reason the credential is revoked rather than merely dropped when the command ends.

    def wallet_challenge(self, *, address: str) -> models.CliChallenge:
        return self._call(
            "POST",
            "/v1/auth/wallet/challenge",
            model=models.CliChallenge,
            json={"address": address},
        )

    def wallet_verify(self, *, address: str, signature: str) -> models.SessionEnvelope:
        """Redeem a coldkey signature for a browser session, creating the account if it is new.

        No nonce goes back, unlike the CLI flow: the validator resolves the latest open challenge
        for the address. A coldkey is not published the way a hotkey is, so the supersession
        denial-of-service that forced a nonce onto `/v1/auth/cli/verify` does not apply.
        """
        return self._call(
            "POST",
            "/v1/auth/wallet/verify",
            model=models.SessionEnvelope,
            json={"address": address, "signature": signature},
        )

    def link_challenge(
        self, *, hotkey: str, headers: dict[str, str]
    ) -> models.CliChallenge:
        return self._call(
            "POST",
            "/v1/me/hotkeys/challenge",
            model=models.CliChallenge,
            headers=headers,
            json={"hotkey": hotkey},
        )

    def link_hotkey(
        self, *, hotkey: str, signature: str, headers: dict[str, str]
    ) -> models.Account:
        return self._call(
            "POST",
            "/v1/me/hotkeys",
            model=models.Account,
            headers=headers,
            json={"hotkey": hotkey, "signature": signature},
        )

    def csrf_headers(self) -> dict[str, str]:
        """The double-submit header every cookie-authenticated write needs.

        The validator binds the CSRF token to the session row rather than merely comparing a
        cookie to a header, so this value is not something a caller may invent -- it has to be
        the one the sign-in response set. Absent before sign-in, which is a programming error
        here rather than a condition to handle at runtime.
        """
        token = self._http.cookies.get(CSRF_COOKIE)
        if token is None:
            raise CliError(
                "the validator opened a session without a CSRF token",
                hint="It is not the API this CLI expects. Check --api.",
            )
        return {CSRF_HEADER: token}

    def clear_cookies(self) -> None:
        """Drop the browser session from the jar. Revoking it server-side is the caller's job."""
        self._http.cookies.clear()

    def read_account(self, *, headers: dict[str, str]) -> models.Account:
        return self._call("GET", "/v1/me", model=models.Account, headers=headers)

    def list_own_submissions(
        self, *, headers: dict[str, str], limit: int, cursor: str | None = None
    ) -> models.SubmissionPage:
        params: dict[str, str] = {"limit": str(limit)}
        if cursor:
            params["cursor"] = cursor
        return self._call(
            "GET",
            "/v1/me/submissions",
            model=models.SubmissionPage,
            headers=headers,
            params=params,
        )

    # --- transport ------------------------------------------------------------------------

    def _send(
        self,
        method: str,
        path: str,
        *,
        content: bytes | None = None,
        json: Any | None = None,
        params: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> httpx.Response:
        """Make the request and turn a refusal into an `ApiError`. No body validation."""
        try:
            response = self._http.request(
                method,
                path,
                content=content,
                json=json,
                params=params,
                headers=headers,
                timeout=timeout if timeout is not None else httpx.USE_CLIENT_DEFAULT,
            )
        except httpx.HTTPError as exc:
            raise TransportError(f"could not reach {self._http.base_url}: {exc}") from exc

        if response.status_code >= 400:
            raise api_error(
                response.status_code, _body(response), response.headers.get("retry-after")
            )
        return response

    def _call[T: models.Model](
        self,
        method: str,
        path: str,
        *,
        model: type[T],
        content: bytes | None = None,
        json: Any | None = None,
        params: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> T:
        response = self._send(
            method,
            path,
            content=content,
            json=json,
            params=params,
            headers=headers,
            timeout=timeout,
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
