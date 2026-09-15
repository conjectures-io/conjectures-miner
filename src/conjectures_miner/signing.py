"""Coldkey authentication for submissions, reads, account registration and CLI sessions."""

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
    """The marker a non-production validator accepts, for a coldkey on its allowlist.

    Holds an address and no key, because the marker is a constant -- so this mode never opens a
    private key, and what it produces is worthless against a production validator.
    """

    ss58_address: str

    def sign(self, message: bytes) -> bytes:
        del message
        return DEVELOPMENT_MARKER


def load_signer(settings: Settings, *, uri: str | None = None) -> Signer:
    """Open the submitting coldkey, or the explicitly requested development marker."""
    if settings.dev_signature:
        return DevelopmentSigner(coldkey_address(settings, uri=uri))
    return load_coldkey(settings, uri=uri)


def coldkey_address(
    settings: Settings, *, uri: str | None = None, explicit: str | None = None
) -> str:
    """The coldkey's SS58 address, read from `coldkeypub` so nothing is unlocked.

    `auth register` needs this to *request* a challenge, and a challenge the validator refuses --
    rate limited, malformed address -- must cost no passphrase prompt. The private half is opened
    only once there is a validated message to sign.
    """
    if explicit:
        return explicit
    if uri:
        return _development_keypair(settings, uri).ss58_address
    try:
        return str(_wallet(settings).coldkeypub.ss58_address)
    except Exception as exc:
        raise ConfigError(
            f"could not read the coldkey of wallet {settings.wallet_name}: {exc}",
            hint="Check --wallet and --wallet-path.",
        ) from exc


def load_coldkey(settings: Settings, *, uri: str | None = None) -> Signer:
    """Open the coldkey for a real signature, prompting for its passphrase."""
    if settings.dev_signature:
        raise ConfigError(
            "--dev-signature cannot register: it sends a fixed marker, not a signature",
            hint="Drop --dev-signature. `--coldkey-uri //Alice` against a local validator does "
            "work -- that is a real keypair.",
        )
    if uri:
        return _development_keypair(settings, uri)
    try:
        return _wallet(settings).coldkey
    except Exception as exc:
        raise ConfigError(
            f"could not open the coldkey of wallet {settings.wallet_name}: {exc}",
            hint="Check --wallet and --wallet-path. Signing requires the coldkey.",
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
        signer_coldkey=signer.ss58_address,
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
    message = digest.read_message(coldkey_ss58=signer.ss58_address, submission_id=submission_id)
    return _signed(signer, message)


SESSION_PREFIX = "conjectures-cli-session-v1"
LOGIN_PREFIX = "conjectures-login-v1"

# What each prefix means, for the refusal message. A miner who mistyped `--api` should be told
# what they were about to sign, not just that a string did not match.
_DESCRIPTIONS = {
    SESSION_PREFIX: "a CLI session challenge",
    LOGIN_PREFIX: "a coldkey login challenge",
}


def assert_session_challenge(message: str, *, address: str, api_root: str) -> None:
    """Guard the message a coldkey signs to mint a CLI session token."""
    _assert_challenge(message, prefix=SESSION_PREFIX, address=address, api_root=api_root)


def assert_login_challenge(message: str, *, address: str, api_root: str) -> None:
    """Guard the message a coldkey signs to open a browser session.

    The same key also signs
    `conjectures-deposit-claim-v1`, so an unchecked signature could claim a transfer rather than
    open a session.
    """
    _assert_challenge(message, prefix=LOGIN_PREFIX, address=address, api_root=api_root)


def _assert_challenge(message: str, *, prefix: str, address: str, api_root: str) -> None:
    """Refuse anything that is not the named challenge, for us, from where we are talking to.

    Runs before the key is unlocked, so a message that fails this costs no passphrase prompt and
    produces no signature at all. See the module docstring for why signing unchecked bytes from
    the network is the thing being prevented.

    Three checks, and each rules out a distinct substitution:

    * **The prefix, on its own first line.** This is what stops the other four messages -- above
      all `conjectures-coldkey-link-v1`, a signature over which would attach this coldkey to
      someone else's account. Matched as the whole first line, not with `startswith`, so a
      longer prefix that merely begins the same way cannot pass.
    * **The address is ours.** A challenge minted for another key is not ours to answer, and
      signing one would produce a signature bound to an address we do not control -- useless at
      best, and not something to hand out.
    * **The domain is the deployment we are talking to.** Without it a hostile server could relay
      a challenge it obtained from the real validator, collect the signature, and use it to open
      a session there as us. The domain is the only field in the message that names *which*
      validator the signature is good for.

    The domain check is relaxed for a local API host, and only for one: a development validator
    keeps the default `LOGIN_DOMAIN=conjectures.io` while serving on localhost, so enforcing it
    would refuse every local sign-in. Nothing reachable over the network is exempt, and the relay
    attack needs a remote host to relay to.
    """
    described = _DESCRIPTIONS[prefix]
    lines = message.splitlines()
    if not lines or lines[0] != prefix:
        found = lines[0] if lines else "(empty)"
        raise ConfigError(
            f"refusing to sign: this is not {described} (first line: {found!r})",
            hint=f"The validator should have sent a message beginning {prefix!r}. "
            "Check --api points at the validator you meant.",
        )

    fields = _challenge_fields(lines[1:])

    if fields.get("address") != address:
        raise ConfigError(
            f"refusing to sign: the challenge names {fields.get('address')!r}, not {address!r}",
            hint="The validator minted this for a different key.",
        )

    domain = fields.get("domain")
    host = urlparse(api_root).hostname or ""
    if domain != host and host not in LOCAL_HOSTS:
        raise ConfigError(
            f"refusing to sign: the challenge is for domain {domain!r}, but --api points at "
            f"{host!r}",
            hint="A signature naming another deployment could be replayed there. Check --api.",
        )


def _challenge_fields(lines: list[str]) -> dict[str, str]:
    """The `key: value` lines of a challenge. Later duplicates do not overwrite earlier ones.

    First-wins because the validator emits each key once: a message carrying two `address:`
    lines is malformed however it got that way, and taking the last would let an appended line
    override the one that was checked.
    """
    fields: dict[str, str] = {}
    for line in lines:
        key, separator, value = line.partition(":")
        if separator and key.strip() not in fields:
            fields[key.strip()] = value.strip()
    return fields


def challenge_signature(signer: Signer, message: str) -> str:
    """Sign a validated challenge, as hex. The message is the validator's, byte for byte.

    The caller must have run the matching `assert_*_challenge` first -- this signs what it is
    given. That split is deliberate: validation happens before a key is unlocked, and by the time
    a keypair exists there is nothing left to decide.

    Refuses the development marker outright. `DevelopmentSigner` returns a constant, which can
    never verify against a freshly minted nonce -- sending it would spend a challenge to earn a
    `SIGNATURE_INVALID` that says nothing about the real problem.
    """
    if isinstance(signer, DevelopmentSigner):
        raise ConfigError(
            "--dev-signature cannot sign in: it sends a fixed marker, not a signature",
            hint="Signing in needs a real signature over the validator's challenge.",
        )
    return signer.sign(message.encode("utf-8")).hex()


def bearer_headers(token: str) -> dict[str, str]:
    """The only header a session-authenticated read needs."""
    return {"Authorization": f"Bearer {token}"}


def _signed(signer: Signer, message: bytes) -> dict[str, str]:
    return {
        "X-Conjectures-Coldkey": signer.ss58_address,
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
