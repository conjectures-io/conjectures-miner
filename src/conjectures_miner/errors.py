"""Every failure the tool reports, and the exit code it reports it with."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

# Refusals grouped by what a miner should do, because the advice differs sharply.
RETRY_UNCHANGED = frozenset({"SUBMISSIONS_PAUSED"})
RETRY_AFTER_WAITING = frozenset({"PAYMENT_NOT_FINALIZED"})
NEVER_RETRY = frozenset({"DUPLICATE_PROOF", "DUPLICATE_PAYMENT", "IDEMPOTENCY_CONFLICT"})

ADVICE: Mapping[str, str] = {
    "PAYMENT_NOT_FINALIZED": (
        "Wait for the transfer to finalize, then retry with the same idempotency key. "
        "Check the recipient, the amount, and that your coldkey owns this hotkey."
    ),
    "SIGNATURE_INVALID": "Check that the signing hotkey is the one in the bundle manifest.",
    "TASK_NOT_ALLOWED": (
        "The task pool moved. Run `conjectures tasks sync`, rebuild, and submit again with the "
        "same payment reference -- it was not consumed."
    ),
    "TASK_COMMITMENT_MISMATCH": (
        "The bundle commits to a task digest the validator no longer publishes. "
        "Run `conjectures tasks sync` and rebuild."
    ),
    "DUPLICATE_PROOF": "These proof bytes were already submitted. Nothing to retry.",
    "DUPLICATE_PAYMENT": "This payment already funded a submission. Nothing to retry.",
    "IDEMPOTENCY_CONFLICT": "That key belongs to a different submission. Build a new one.",
    "SUBMISSIONS_PAUSED": "The validator is not accepting work. Try again after the pause.",
    "SUBMISSION_POLICY_VIOLATION": "The proof uses something the policy forbids; see the detail.",
}


class CliError(Exception):
    """Anything the tool refuses to do, reported without a traceback."""

    exit_code = 1

    def __init__(self, message: str, *, hint: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint


class ConfigError(CliError):
    exit_code = 2


class ApiError(CliError):
    exit_code = 3

    def __init__(
        self,
        message: str,
        *,
        status_code: int,
        reason_code: str | None = None,
        detail: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message, hint=ADVICE.get(reason_code or ""))
        self.status_code = status_code
        self.reason_code = reason_code
        self.detail = dict(detail or {})

    @property
    def retryable(self) -> bool:
        if self.reason_code in NEVER_RETRY:
            return False
        return self.reason_code in RETRY_UNCHANGED | RETRY_AFTER_WAITING or self.status_code >= 500


class TransportError(CliError):
    """The validator was unreachable. Says nothing about whether the work was accepted."""

    exit_code = 4


def api_error(status_code: int, body: object) -> ApiError:
    """Build an ApiError from an RFC 9457 problem document, or from whatever arrived instead."""
    if not isinstance(body, Mapping):
        return ApiError(f"validator returned HTTP {status_code}", status_code=status_code)
    reason_code = body.get("reason_code")
    message = body.get("detail") or body.get("title") or f"validator returned HTTP {status_code}"
    envelope = {"type", "title", "status", "detail", "reason_code"}
    return ApiError(
        str(message),
        status_code=status_code,
        reason_code=str(reason_code) if reason_code else None,
        detail={key: value for key, value in body.items() if key not in envelope},
    )
