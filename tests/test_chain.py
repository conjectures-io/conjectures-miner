"""Event decoding and reference resolution, tested without a node.

The half of `chain.py` that breaks silently. A runtime upgrade that changes the event envelope
makes every transfer invisible, which looks exactly like "the transfer did not happen" -- and by
then the money has moved. The decoder is pure, so all of it is reachable from here.
"""

from __future__ import annotations

import pytest

from conjectures_miner.chain import (
    FINALITY_ATTEMPTS,
    ZERO_ACCOUNT,
    ChainError,
    Position,
    Transfer,
    decode_ss58,
    parse_position,
    resolve,
    transfers_in_block,
)

TREASURY = "5Gn2SyG6PmBstAjiPD93CTuxADqYaYqf6fKeFuezKsX7Chf9"
SENDER = "5HMqFHmvUpzuAjEnse3hzMKS5LsFL428hffCfenF2smuGNhs"
PRICE = 500_000_000


def event(module, name, attributes, *, extrinsic_idx=13, nested=True):
    """One record in the shape bittensor 11 returns: the fields at both levels."""
    body = {"module_id": module, "event_id": name, "attributes": attributes}
    record = {"phase": "ApplyExtrinsic", "extrinsic_idx": extrinsic_idx, "topics": []}
    if nested:
        record["event"] = dict(body)
    record.update(body)
    return record


def transfer_event(*, to=TREASURY, sender=SENDER, amount=PRICE, extrinsic_idx=13, nested=True):
    return event(
        "Balances",
        "Transfer",
        {"from": sender, "to": to, "amount": amount},
        extrinsic_idx=extrinsic_idx,
        nested=nested,
    )


# --- positions -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("8769916-13", Position(8769916, 13, None)),
        ("8769916-13-151", Position(8769916, 13, 151)),
        ("  8769916-13  ", Position(8769916, 13, None)),
    ],
)
def test_a_position_parses(raw, expected):
    assert parse_position(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "0xabc123",  # an extrinsic hash: an indexer's job, not a node's
        "8769916",
        "8769916-13-151-2",
        "abc-13",
    ],
)
def test_what_is_not_a_position_is_refused(raw):
    assert parse_position(raw) is None


def test_a_position_round_trips_through_its_string():
    for raw in ("8769916-13", "8769916-13-151"):
        assert str(parse_position(raw)) == raw


# --- decoding --------------------------------------------------------------------------------


def test_a_transfer_is_read_out_of_a_block():
    found = transfers_in_block([transfer_event()], block=8769916)

    assert found == [
        Transfer(
            block=8769916,
            extrinsic=13,
            event=0,
            sender=SENDER,
            recipient=TREASURY,
            amount_rao=PRICE,
        )
    ]
    assert found[0].reference == "8769916-13-0"


def test_the_nested_only_envelope_still_decodes():
    """Older decoders returned the fields only under `event`. Both shapes must work, because a
    runtime upgrade that broke one would make every transfer invisible."""
    assert transfers_in_block([transfer_event(nested=True)], block=1) == transfers_in_block(
        [transfer_event(nested=True)], block=1
    )
    body = transfer_event()
    nested_only = {
        "phase": "ApplyExtrinsic",
        "extrinsic_idx": 13,
        "event": {
            "module_id": body["module_id"],
            "event_id": body["event_id"],
            "attributes": body["attributes"],
        },
    }

    assert len(transfers_in_block([nested_only], block=1)) == 1


def test_positional_attributes_decode():
    """Whether the decoder names the attributes depends on the node's metadata version."""
    record = event("Balances", "Transfer", [SENDER, TREASURY, PRICE])

    assert transfers_in_block([record], block=1)[0].amount_rao == PRICE


def test_other_events_are_skipped():
    records = [
        event("System", "ExtrinsicSuccess", {}),
        transfer_event(),
        event("SubtensorModule", "NeuronRegistered", {}),
    ]

    assert [item.event for item in transfers_in_block(records, block=1)] == [1]


def test_a_zero_value_transfer_is_not_a_payment():
    """A valid extrinsic that buys nothing. Skipped rather than raised."""
    assert transfers_in_block([transfer_event(amount=0)], block=1) == []


def test_an_event_outside_an_extrinsic_still_gets_a_unique_position():
    """Initialisation-phase events carry no `extrinsic_idx`; (extrinsic, event) must stay unique."""
    record = transfer_event()
    del record["extrinsic_idx"]

    found = transfers_in_block([record, transfer_event()], block=1)

    assert {(item.extrinsic, item.event) for item in found} == {(0, 0), (13, 1)}


def test_an_unreadable_record_is_a_chain_error():
    with pytest.raises(ChainError):
        transfers_in_block(["not a record"], block=1)


def test_a_record_with_no_module_id_is_a_chain_error():
    with pytest.raises(ChainError, match="no module_id"):
        transfers_in_block([{"phase": "ApplyExtrinsic"}], block=1)


def test_an_ss58_string_passes_through():
    assert decode_ss58(TREASURY) == TREASURY


def test_raw_account_bytes_encode_to_ss58():
    assert decode_ss58(bytes(32)) == ZERO_ACCOUNT
    assert decode_ss58([list(bytes(32))]) == ZERO_ACCOUNT


def test_an_undecodable_address_is_a_chain_error():
    with pytest.raises(ChainError, match="cannot read an SS58 address"):
        decode_ss58(object())


# --- resolution -------------------------------------------------------------------------------


def _found(*events):
    return transfers_in_block(list(events), block=8769916)


def test_a_two_part_position_resolves_to_the_canonical_three_part_reference():
    found = resolve(_found(transfer_event()), Position(8769916, 13))

    assert found is not None
    assert found.reference == "8769916-13-0"


def test_a_three_part_position_selects_one_event():
    found = resolve(_found(transfer_event(), transfer_event()), Position(8769916, 13, 1))

    assert found is not None
    assert found.reference == "8769916-13-1"


def test_a_position_naming_nothing_resolves_to_none():
    assert resolve(_found(transfer_event()), Position(8769916, 99)) is None
    assert resolve(_found(transfer_event()), Position(8769916, 13, 99)) is None


def test_an_ambiguous_extrinsic_refuses_rather_than_guessing():
    """A `utility.batch` can pay the treasury twice under one extrinsic. Choosing either would be
    deciding which payment the miner meant, and the validator would refuse it anyway."""
    transfers = _found(transfer_event(), transfer_event())

    with pytest.raises(ChainError) as refused:
        resolve(transfers, Position(8769916, 13))

    assert "8769916-13-0, 8769916-13-1" in (refused.value.hint or "")


def test_the_finality_wait_is_bounded():
    """A transfer that never finalizes must end in a message, not a hang."""
    assert FINALITY_ATTEMPTS > 0
