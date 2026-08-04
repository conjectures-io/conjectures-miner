"""Failures, in the terms a miner can act on.

The validator answers refusals with a stable `reason_code`. That code -- not the HTTP
status -- is what should drive the message, the exit code, and whether retrying could
possibly help.

Three groups worth distinguishing, because the advice differs sharply:

- **Retry unchanged**: `SUBMISSIONS_PAUSED` (503 with `Retry-After`), transport failures.
- **Retry after waiting**: `PAYMENT_NOT_FINALIZED`.
- **Never retry**: `DUPLICATE_PROOF`, `DUPLICATE_PAYMENT`. The payment is already consumed;
  a fresh attempt only wastes another one.

Distinct exit codes so a wrapper script can branch without parsing text.
"""

from __future__ import annotations

import httpx


class CliError(Exception):
    """Anything the tool refuses to do, rendered without a traceback."""

    exit_code: int = 1


class ConfigError(CliError):
    """Bad settings, missing wallet, unusable output format."""

    exit_code = 2


class ApiError(CliError):
    """A refusal carrying the validator's own vocabulary."""

    exit_code = 3

    reason_code: str | None
    status_code: int
    detail: str | None

    @property
    def retryable(self) -> bool:
        """Whether repeating this request could ever succeed. Never true for a duplicate."""
        raise NotImplementedError


class TransportError(CliError):
    """The validator was unreachable. Says nothing about whether the work was accepted."""

    exit_code = 4


def translate(response: httpx.Response) -> ApiError:
    """Turn a non-2xx response into an `ApiError`, preserving `reason_code` and `detail`."""
    raise NotImplementedError


def advice(reason_code: str | None) -> str | None:
    """The one-line "what to do about it" for a known reason code.

    Mirrors the troubleshooting table in the validator's `docs/MINER.md`.
    """
    raise NotImplementedError
