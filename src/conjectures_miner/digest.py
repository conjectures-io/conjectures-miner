"""The byte-exact digests the validator expects. Stdlib only, deliberately.

This module is a contract, not a convenience. `request_digest` must stay byte-identical to
`conjectures_subnet.db.submissions.canonical_request_digest` in the validator: sorted keys,
no whitespace, one trailing newline. A discrepancy is not a crash -- it is a
`SIGNATURE_INVALID` returned after the miner has already paid.

Two consequences for how this file is written:

- **No third-party imports.** It must be auditable and testable without bittensor, httpx, or
  pydantic in the environment.
- **No behaviour beyond the bytes.** No settings, no I/O, no logging. Everything it needs
  arrives as an argument.

Covered by golden vectors in `tests/vectors/`, generated from the validator. Regenerate them
against a known validator commit rather than editing an expectation by hand.
"""

from __future__ import annotations

READ_DOMAIN = "conjectures-read-v1"


def sha256_prefixed(data: bytes) -> str:
    """`sha256:<64 lowercase hex>` -- the digest form used everywhere in the API."""
    raise NotImplementedError


def request_digest(
    *,
    hotkey: str,
    task_id: str,
    task_bundle_sha256: str,
    proof_sha256: str,
    payment_reference: str,
    idempotency_key: str,
) -> str:
    """The message a submission's hotkey signs.

    Canonical JSON -- `sort_keys=True`, `separators=(",", ":")`, `ensure_ascii=False` --
    plus exactly one trailing newline, then sha256. Every one of those is load-bearing.
    """
    raise NotImplementedError


def read_message(*, hotkey_ss58: str, submission_id: str) -> bytes:
    """The message a status or report read signs.

    A different scheme from `request_digest`: `sha256(f"{READ_DOMAIN}:{ss58}:{id}")`, signed
    raw. Easy to conflate with the submit path; they are not interchangeable.
    """
    raise NotImplementedError
