"""Wallet loading, and the headers each authenticated call needs.

The only module that touches key material, and the only one that imports bittensor. Commands
ask for headers; they never see a keypair.

Note which key does what: the **coldkey pays** and the **hotkey signs**. The validator
checks on-chain that the paying coldkey owns the submitting hotkey, so the CLI never needs
the coldkey -- it needs the hotkey plus the extrinsic reference for a transfer that
already happened.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol


class Signer(Protocol):
    """The narrow view of a keypair that this tool actually uses."""

    @property
    def ss58_address(self) -> str: ...

    def sign(self, message: bytes) -> bytes: ...


def load_signer(
    *,
    wallet_name: str | None,
    hotkey_name: str | None,
    wallet_path: Path | None,
    uri: str | None = None,
) -> Signer:
    """Open a hotkey for signing.

    `uri` accepts a development key such as `//Alice` and must be refused against anything
    but a local endpoint -- a dev key on mainnet is a stolen submission waiting to happen.
    """
    raise NotImplementedError


def resolve_hotkey_address(
    *,
    wallet_name: str | None,
    hotkey_name: str | None,
    wallet_path: Path | None,
    explicit: str | None = None,
) -> str:
    """Get the SS58 address without unlocking anything.

    `build` and `check` need the address only -- it goes in the bundle manifest and in
    preflight's header. Neither should prompt for a password.
    """
    raise NotImplementedError


def submit_headers(
    signer: Signer,
    *,
    task_id: str,
    task_bundle_sha256: str,
    proof_sha256: str,
    payment_reference: str,
    idempotency_key: str,
    content_length: int,
) -> dict[str, str]:
    """Headers for `POST /v1/submissions`.

    Signs `digest.request_digest(...)`. The digest's hex is signed as **bytes**, not as its
    string form.
    """
    raise NotImplementedError


def read_headers(signer: Signer, *, submission_id: str) -> dict[str, str]:
    """Headers for the status and report reads. Signs `digest.read_message(...)`."""
    raise NotImplementedError
