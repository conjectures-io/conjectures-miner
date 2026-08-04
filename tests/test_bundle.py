"""The archive shape the validator admits, which it parses itself rather than via `zipfile`."""

from __future__ import annotations

import struct
import zipfile
from pathlib import Path

import pytest
from tests.conftest import HOTKEY, PROOF, TASK_DIGEST, TASK_ID

from conjectures_miner import bundle
from conjectures_miner.digest import sha256_prefixed

EOCD_SIGNATURE = b"PK\x05\x06"
EOCD_SIZE = 22
PERMITTED_FLAG_BITS = 0x0806


def build(tmp_path: Path, **overrides) -> tuple[Path, bundle.Bundle]:
    manifest = bundle.build_manifest(
        task_id=overrides.pop("task_id", TASK_ID),
        task_bundle_sha256=overrides.pop("task_bundle_sha256", TASK_DIGEST),
        hotkey_ss58=overrides.pop("hotkey_ss58", HOTKEY),
        proof=PROOF,
        **overrides,
    )
    path = tmp_path / "submission.zip"
    return path, bundle.write(path, manifest=manifest, proof=PROOF)


def test_archive_holds_exactly_the_two_entries_in_order(tmp_path: Path):
    path, _ = build(tmp_path)
    with zipfile.ZipFile(path) as archive:
        assert archive.namelist() == list(bundle.ENTRY_NAMES)


def test_archive_carries_no_comment_and_starts_at_offset_zero(tmp_path: Path):
    built = build(tmp_path)[1]
    assert built.raw.startswith(b"PK\x03\x04")
    tail = built.raw[-EOCD_SIZE:]
    assert tail[:4] == EOCD_SIGNATURE
    (*_, comment_length) = struct.unpack("<4H2LH", tail[4:])
    assert comment_length == 0


def test_entries_set_no_prohibited_flags_and_are_plain_regular_files(tmp_path: Path):
    path, _ = build(tmp_path)
    with zipfile.ZipFile(path) as archive:
        for entry in archive.infolist():
            assert entry.flag_bits & ~PERMITTED_FLAG_BITS == 0
            assert entry.external_attr >> 16 == 0o644
            assert entry.create_system == bundle.UNIX_CREATE_SYSTEM


def test_manifest_digests_the_archived_proof(tmp_path: Path):
    _, built = build(tmp_path)
    assert built.proof_sha256 == sha256_prefixed(PROOF)
    assert built.manifest["proof_bytes"] == len(PROOF)
    assert built.manifest["proof_path"] == bundle.PROOF_NAME
    assert built.manifest["format"] == bundle.BUNDLE_FORMAT


def test_a_rebuild_of_the_same_inputs_is_byte_identical(tmp_path: Path):
    outputs = []
    for name in ("a", "b"):
        directory = tmp_path / name
        directory.mkdir()
        outputs.append(build(directory)[1].raw)
    assert outputs[0] == outputs[1]


def test_solver_is_nested_and_needs_both_halves(tmp_path: Path):
    _, built = build(tmp_path, solver_name="prover", solver_version="1.0")
    assert built.manifest["solver"] == {"name": "prover", "version": "1.0"}
    with pytest.raises(bundle.BundleError, match="both a name and a version"):
        build(tmp_path, solver_name="prover")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("task_id", "Not A Task Id"),
        ("task_bundle_sha256", "sha256:" + "A" * 64),
        # 47 characters: accepted by the API, then refused at INSERT, so it is caught here.
        ("hotkey_ss58", HOTKEY[:47]),
    ],
)
def test_malformed_identity_is_refused_before_anything_is_written(
    tmp_path: Path, field: str, value: str
):
    with pytest.raises(bundle.BundleError):
        build(tmp_path, **{field: value})
    assert not (tmp_path / "submission.zip").exists()


def test_proof_policy_is_applied_locally():
    with pytest.raises(bundle.BundleError, match="empty"):
        bundle.check_proof(b"")
    with pytest.raises(bundle.BundleError, match="NUL"):
        bundle.check_proof(b"theorem\x00")
    with pytest.raises(bundle.BundleError, match="UTF-8"):
        bundle.check_proof(b"\xff\xfe")
    with pytest.raises(bundle.BundleError, match="limit"):
        bundle.check_proof(b"x" * (bundle.MAX_PROOF_BYTES + 1))


def test_read_round_trips_what_was_written(tmp_path: Path):
    path, built = build(tmp_path)
    reread = bundle.read(path)
    assert reread.raw == built.raw
    assert reread.manifest == built.manifest
    assert reread.sha256 == built.sha256


def test_reading_something_that_is_not_a_bundle_is_a_refusal(tmp_path: Path):
    path = tmp_path / "not.zip"
    path.write_bytes(b"not a zip archive at all")
    with pytest.raises(bundle.BundleError, match="readable archive"):
        bundle.read(path)
