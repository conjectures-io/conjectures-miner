"""Every failure the tool reports, and the exit code it reports it with."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

# What to do about a refusal, keyed by the validator's reason code. Each entry says whether the
# payment survived, because that is the only question that costs money to get wrong.
ADVICE: Mapping[str, str] = {
    "PAYMENT_NOT_FINALIZED": (
        "Wait for the transfer to finalize, then retry with the same idempotency key. "
        "Check the recipient, the amount, and that your coldkey owns this hotkey."
    ),
    "PAYMENT_REFERENCE_AMBIGUOUS": (
        "That extrinsic moved TAO more than once, so it does not name one payment. Retry with the "
        "three-part `block-extrinsic-event` reference from the detail; the payment is untouched."
    ),
    "PAYMENT_VERIFIER_UNAVAILABLE": (
        "The validator could not read the chain. Nothing was spent -- retry with the same "
        "idempotency key."
    ),
    "TRANSFER_ALREADY_CREDITED": (
        "That transfer was credited to an account as credits, so it can never fund a submission. "
        "Spend the credit instead."
    ),
    "SIGNATURE_INVALID": (
        "Check that the signing hotkey is the one in the bundle manifest. When submitting to a "
        "validator outside PROD, it may want the static marker instead: pass --dev-signature. "
        "That flag cannot sign you in, though -- `auth login` needs a real signature."
    ),
    "HOTKEY_NOT_LINKED": (
        "This hotkey is not attached to a conjectures.io account. Run `conjectures auth register` "
        "on the machine that holds your coldkey, then `conjectures auth login` again. Linking is "
        "a coldkey action: a hotkey cannot attach itself to an account."
    ),
    "HOTKEY_ALREADY_LINKED": (
        "Another account already holds this hotkey, and it is not re-parented silently -- a "
        "submission has one owner and a reward one destination. Register the hotkey you meant "
        "with --hotkey, or detach it from the other account first."
    ),
    "BROWSER_SESSION_REQUIRED": (
        "That change is refused to a CLI token and accepted only from a browser session, because "
        "it decides who the account is or where its money goes. `conjectures auth register` opens "
        "one with your coldkey for the length of the command."
    ),
    "CHALLENGE_INVALID": (
        "The challenge expired or was already used. Run the command again; each one is "
        "single-use and short-lived."
    ),
    "NOT_AUTHENTICATED": (
        "The session expired or was revoked. Run `conjectures auth login`. Nothing else is "
        "affected: `submit` and `submissions show` sign each request and need no session."
    ),
    "TOO_MANY_CHALLENGES": (
        "Too many sign-in attempts for that hotkey. Wait out `Retry-After`, then try again. No "
        "key was unlocked and nothing was signed."
    ),
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
    "SUBMISSIONS_PAUSED": (
        "The validator is not accepting work; `conjectures status` says why. Nothing was spent -- "
        "retry with the same idempotency key."
    ),
    "RATE_LIMITED": "Too many requests. Wait out `Retry-After`, then retry with the same key.",
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
        retry_after: float | None = None,
    ) -> None:
        super().__init__(message, hint=ADVICE.get(reason_code or ""))
        self.status_code = status_code
        self.reason_code = reason_code
        self.detail = dict(detail or {})
        # Set only when the validator asked for a pause -- a rate limit, or something transient.
        # Its presence is what tells a poller to wait rather than to give up.
        self.retry_after = retry_after


class TransportError(CliError):
    """The validator was unreachable. Says nothing about whether the work was accepted."""

    exit_code = 4


def api_error(status_code: int, body: object, retry_after: str | None = None) -> ApiError:
    """Build an ApiError from an RFC 9457 problem document, or from whatever arrived instead."""
    delay = _seconds(retry_after)
    if not isinstance(body, Mapping):
        return ApiError(
            f"validator returned HTTP {status_code}", status_code=status_code, retry_after=delay
        )
    reason_code = body.get("reason_code")
    message = body.get("detail") or body.get("title") or f"validator returned HTTP {status_code}"
    envelope = {"type", "title", "status", "detail", "reason_code"}
    return ApiError(
        str(message),
        status_code=status_code,
        reason_code=str(reason_code) if reason_code else None,
        detail={key: value for key, value in body.items() if key not in envelope},
        retry_after=delay,
    )


def _seconds(retry_after: str | None) -> float | None:
    """`Retry-After` as a delay. The HTTP-date form is ignored: this API sends delta-seconds."""
    try:
        return max(0.0, float(retry_after or ""))
    except ValueError:
        return None
