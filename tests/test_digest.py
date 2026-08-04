"""The two signing schemes, against vectors generated from the validator."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from conjectures_miner import digest

VECTORS = json.loads((Path(__file__).parent / "vectors" / "digests.json").read_text())


def test_request_digest_matches_the_validator():
    vector = VECTORS["request_digest"]
    assert digest.request_digest(**vector["input"]) == vector["expected"]


def test_read_message_matches_the_validator():
    vector = VECTORS["read_message"]
    assert digest.sha256_prefixed(b"") != vector["expected"]
    assert digest.sha256_prefixed(_read_preimage(vector["input"])) == vector["expected"]


def _read_preimage(inputs: dict[str, str]) -> bytes:
    return f"{digest.READ_DOMAIN}:{inputs['hotkey_ss58']}:{inputs['submission_id']}".encode()


def test_read_message_returns_the_raw_bytes_that_get_signed():
    vector = VECTORS["read_message"]
    assert digest.read_message(**vector["input"]) == digest.to_bytes(vector["expected"])


def test_canonical_json_sorts_keys_and_ends_with_one_newline():
    assert digest.canonical_json({"b": 1, "a": 2}) == b'{"a":2,"b":1}\n'


@pytest.mark.parametrize(
    ("value", "valid"),
    [
        ("sha256:" + "a" * 64, True),
        ("sha256:" + "A" * 64, False),  # the validator requires lowercase
        ("a" * 64, False),
        ("sha256:" + "a" * 63, False),
    ],
)
def test_is_sha256(value: str, valid: bool):
    assert digest.is_sha256(value) is valid
