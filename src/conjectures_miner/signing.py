"""Wallet loading and the headers each authenticated call needs.

The hotkey half of this tool's key handling. Commands ask for headers; they never see a keypair.
The coldkey pays and the hotkey signs -- the validator checks on-chain that the paying coldkey
owns the submitting hotkey, so everything the validator *authenticates* is signed here, by the
hotkey alone.

The coldkey is opened in exactly one other place, `chain.load_coldkey_wallet`, and only by
`conjectures pay`: signing a transfer needs the account that holds the funds. Nothing in this
module touches it.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlparse

from conjectures_miner import digest
from conjectures_miner.errors import ConfigError
from conjectures_miner.settings import Settings

LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "0.0.0.0"})

DEVELOPMENT_MARKER = b"development".ljust(64, b"\x00")


class Signer(Protocol):
    """The narrow view of a keypair this tool uses."""

    @property
    def ss58_address(self) -> str: ...

    def sign(self, message: bytes) -> bytes: ...


@dataclass(frozen=True)
class DevelopmentSigner:
    """The marker a non-production validator accepts, for a hotkey on its allowlist.

    Holds an address and no key, because the marker is a constant -- so this mode never opens a
    private key, and what it produces is worthless against a production validator.
    """

    ss58_address: str

    def sign(self, message: bytes) -> bytes:
        del message
        return DEVELOPMENT_MARKER


def load_signer(settings: Settings, *, uri: str | None = None) -> Signer:
    """Open a hotkey for signing."""
    if settings.dev_signature:
        return DevelopmentSigner(hotkey_address(settings, uri=uri))
    if uri:
        return _development_keypair(settings, uri)
    try:
        return _wallet(settings).hotkey
    except Exception as exc:
        raise ConfigError(
            f"could not open hotkey {settings.wallet_name}/{settings.wallet_hotkey}: {exc}",
            hint="Check --wallet and --hotkey, or pass --wallet-path.",
        ) from exc


def hotkey_address(
    settings: Settings, *, uri: str | None = None, explicit: str | None = None
) -> str:
    """The SS58 address, without unlocking anything -- `build` and `check` need no private key."""
    if explicit:
        return explicit
    if uri:
        return _development_keypair(settings, uri).ss58_address
    try:
        return _wallet(settings).hotkeypub.ss58_address
    except Exception as exc:
        raise ConfigError(
            f"could not read hotkey {settings.wallet_name}/{settings.wallet_hotkey}: {exc}",
            hint="Check --wallet and --hotkey, or pass the address with --hotkey-ss58.",
        ) from exc


def submit_headers(
    signer: Signer,
    *,
    task_id: str,
    task_bundle_sha256: str,
    proof_sha256: str,
    payment_reference: str,
    idempotency_key: str,
) -> dict[str, str]:
    """Headers for `POST /v1/submissions`, signing the canonical request digest."""
    request_digest = digest.request_digest(
        hotkey=signer.ss58_address,
        task_id=task_id,
        task_bundle_sha256=task_bundle_sha256,
        proof_sha256=proof_sha256,
        payment_reference=payment_reference,
        idempotency_key=idempotency_key,
    )
    return {
        "Idempotency-Key": idempotency_key,
        "X-Conjectures-Task-Id": task_id,
        "X-Conjectures-Task-Sha256": task_bundle_sha256,
        "X-Conjectures-Proof-Sha256": proof_sha256,
        "X-Conjectures-Payment-Ref": payment_reference,
        **_signed(signer, digest.to_bytes(request_digest)),
    }


def read_headers(signer: Signer, *, submission_id: str) -> dict[str, str]:
    """Headers for the status and report reads. A different scheme from the submit path."""
    message = digest.read_message(hotkey_ss58=signer.ss58_address, submission_id=submission_id)
    return _signed(signer, message)


def _signed(signer: Signer, message: bytes) -> dict[str, str]:
    return {
        "X-Conjectures-Hotkey": signer.ss58_address,
        "X-Conjectures-Timestamp": str(int(time.time() * 1000)),
        "X-Conjectures-Signature": signer.sign(message).hex(),
    }


def _development_keypair(settings: Settings, uri: str):
    _assert_local(settings.api_root)
    from bittensor.sp_core import Keypair

    return Keypair.create_from_uri(uri)


def _wallet(settings: Settings):  # type: ignore[no-untyped-def] - bittensor ships no stubs
    from bittensor.wallet import Wallet

    extra = {"path": str(settings.wallet_path)} if settings.wallet_path else {}
    return Wallet(name=settings.wallet_name, hotkey=settings.wallet_hotkey, **extra)


def _assert_local(api_root: str) -> None:
    if (urlparse(api_root).hostname or "") not in LOCAL_HOSTS:
        raise ConfigError(
            f"refusing to use a development key against {api_root}",
            hint="--uri is for a local validator only.",
        )
