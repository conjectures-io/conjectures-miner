"""The payment reference, which the validator resolves by position rather than by hash."""

from __future__ import annotations

import pytest

from conjectures_miner import payment
from conjectures_miner.errors import ConfigError


@pytest.mark.parametrize("reference", ["4821993-2", "4821993-2-1", "1-0-0"])
def test_a_position_is_canonical(reference: str):
    assert payment.is_canonical(payment.normalise(reference))


@pytest.mark.parametrize(
    "reference",
    [
        # What a block explorer shows for an extrinsic. Accepted by the API's header check, so it
        # cannot be refused locally, but no node can resolve it.
        "0x9c3f2ab1",
        "dev-payment-1",
    ],
)
def test_something_the_chain_cannot_resolve_is_accepted_but_not_canonical(reference: str):
    assert payment.normalise(reference) == reference
    assert not payment.is_canonical(reference)


def test_surrounding_whitespace_is_dropped_before_it_is_signed():
    assert payment.normalise("  4821993-2-1\n") == "4821993-2-1"


@pytest.mark.parametrize("reference", ["", "abc", "x" * 129, "4821993 2 1", "ref/1"])
def test_what_the_validator_would_reject_outright_is_refused_locally(reference: str):
    with pytest.raises(ConfigError) as raised:
        payment.normalise(reference)
    assert "block-extrinsic" in (raised.value.hint or "")
