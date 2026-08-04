"""The payment reference: a position on chain, not an extrinsic hash."""

from __future__ import annotations

import re

from conjectures_miner.errors import ConfigError

# What the validator's header validation accepts. Refused here so a typo costs no round trip.
ACCEPTED = re.compile(r"^[A-Za-z0-9:_.#-]{4,128}$")

# The canonical identity of one transfer, `block-extrinsic[-event]`. Positional because a substrate
# node can resolve a position and cannot resolve an extrinsic hash -- that is an indexer's service,
# so a hash is not something the validator can confirm a payment from.
CANONICAL = re.compile(r"^\d{1,12}-\d{1,6}(?:-\d{1,6})?$")

EXAMPLE = "4821993-2-1"


def normalise(raw: str) -> str:
    """The reference exactly as it will be signed and sent, or a refusal if it cannot be one."""
    reference = raw.strip()
    if ACCEPTED.fullmatch(reference) is None:
        raise ConfigError(
            f"{raw!r} is not a payment reference",
            hint=f"Expected `block-extrinsic` or `block-extrinsic-event`, e.g. {EXAMPLE}.",
        )
    return reference


def is_canonical(reference: str) -> bool:
    """Whether a chain-backed validator could resolve this reference at all."""
    return CANONICAL.fullmatch(reference) is not None
