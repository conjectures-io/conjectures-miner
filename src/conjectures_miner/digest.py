"""The byte-exact digests the validator expects. Stdlib only, and a contract: see tests/vectors."""

from __future__ import annotations

import hashlib
import json
from typing import Any

READ_DOMAIN = "conjectures-read-v1"
PREFIX = "sha256:"


def sha256_prefixed(data: bytes) -> str:
    return PREFIX + hashlib.sha256(data).hexdigest()


def to_bytes(prefixed: str) -> bytes:
    """The 32 raw bytes behind a `sha256:<hex>` digest -- what actually gets signed."""
    return bytes.fromhex(prefixed.removeprefix(PREFIX))


def is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 71
        and value.startswith(PREFIX)
        and all(char in "0123456789abcdef" for char in value[len(PREFIX) :])
    )


def canonical_json(value: Any) -> bytes:
    """Sorted keys, no whitespace, one trailing newline. Every one of those is load-bearing."""
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return (encoded + "\n").encode("utf-8")


def request_digest(
    *,
    hotkey: str,
    task_id: str,
    task_bundle_sha256: str,
    proof_sha256: str,
    payment_reference: str,
    idempotency_key: str,
) -> str:
    """The message a submission's hotkey signs."""
    return sha256_prefixed(
        canonical_json(
            {
                "hotkey": hotkey,
                "idempotency_key": idempotency_key,
                "payment_reference": payment_reference,
                "proof_sha256": proof_sha256,
                "task_bundle_sha256": task_bundle_sha256,
                "task_id": task_id,
            }
        )
    )


def read_message(*, hotkey_ss58: str, submission_id: str) -> bytes:
    """The message a status or report read signs. Not interchangeable with `request_digest`."""
    return hashlib.sha256(f"{READ_DOMAIN}:{hotkey_ss58}:{submission_id}".encode()).digest()
