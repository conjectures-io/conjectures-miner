"""Exercise CLI output against the validator checkout, not a copy of its schema.

Set CONJECTURES_VALIDATOR_ROOT to run these locally. CI checks out validator main
and runs them on every push so a stale client contract cannot silently pass.
"""

from __future__ import annotations

import importlib
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from conjectures_miner import bundle, chat_logs, digest, signing
from conjectures_miner.api import models
from tests.conftest import HOTKEY, PROOF, TASK_DIGEST, TASK_ID
from tests.test_auth import account_response, session_response, submission_page
from tests.test_cli import submission_response


@pytest.fixture
def validator(monkeypatch: pytest.MonkeyPatch):
    root = os.environ.get("CONJECTURES_VALIDATOR_ROOT")
    if root is None:
        pytest.skip("set CONJECTURES_VALIDATOR_ROOT to run the validator contract tests")
    monkeypatch.syspath_prepend(root)
    return SimpleNamespace(
        bundle=importlib.import_module("verifier.bundle"),
        schemas=importlib.import_module("submission_api.schemas"),
        account=importlib.import_module("submission_api.schemas_account"),
        submissions=importlib.import_module("conjectures_subnet.db.submissions"),
        chat_log_terms=importlib.import_module("submission_api.trajectory_terms"),
    )


def test_bundle_policy_matches_the_current_validator(validator):
    assert bundle.MAX_PROOF_BYTES == validator.bundle.MAX_SUBMISSION_BYTES
    assert bundle.MAX_BUNDLE_BYTES == validator.bundle.MAX_BUNDLE_BYTES
    assert bundle.BUNDLE_SCHEMA_VERSION == validator.bundle.BUNDLE_SCHEMA_VERSION


def test_chat_log_consent_matches_the_current_validator(validator):
    assert chat_logs.TERMS_VERSION == validator.chat_log_terms.TERMS_VERSION
    assert chat_logs.TERMS_SHA256 == validator.chat_log_terms.TERMS_SHA256
    assert chat_logs.ASSENT_STATEMENTS_SHA256 == validator.chat_log_terms.ASSENT_STATEMENTS_SHA256


def test_validator_reads_a_maximum_sized_uncompressed_cli_proof(validator, tmp_path: Path):
    # Repetitive proof text is stored uncompressed, so this also exercises the archive cap.
    proof = PROOF.ljust(validator.bundle.MAX_SUBMISSION_BYTES, b" ")
    bundle.check_proof(proof)
    manifest = bundle.build_manifest(
        task_id=TASK_ID, task_bundle_sha256=TASK_DIGEST, coldkey_ss58=HOTKEY, proof=proof
    )
    path = tmp_path / "maximum.zip"
    archive = bundle.write(path, manifest=manifest, proof=proof)
    assert len(archive.raw) > len(proof)
    assert bundle.read(path).proof == proof
    admitted = validator.bundle.load_proof_bundle(archive.raw)
    assert admitted.proof.raw == proof
    assert admitted.manifest.proof_bytes == len(proof)


@pytest.mark.parametrize("proof", [PROOF, b"x\n" * 300_000])
def test_validator_admits_cli_archives(validator, tmp_path: Path, proof: bytes):
    manifest = bundle.build_manifest(
        task_id=TASK_ID, task_bundle_sha256=TASK_DIGEST, coldkey_ss58=HOTKEY, proof=proof
    )
    archive = bundle.write(tmp_path / "proof.zip", manifest=manifest, proof=proof)
    admitted = validator.bundle.admit_proof_bundle(
        archive.raw,
        task_manifest=SimpleNamespace(
            task_id=TASK_ID,
            max_submission_bytes=validator.bundle.MAX_SUBMISSION_BYTES,
            theorem_names=("Bounty.target",),
            forbidden_dependencies=(),
            answer_policy={},
        ),
        expected_task_sha256=TASK_DIGEST,
        expected_signer=HOTKEY,
    )
    assert admitted.proof.raw == proof
    assert admitted.manifest.schema_version == 2
    assert admitted.manifest.miner_coldkey == HOTKEY


def test_submit_signature_verifies_over_the_validators_digest(validator):
    from bittensor.sp_core import Keypair

    signer = Keypair.create_from_uri("//Alice")
    fields = {
        "task_id": TASK_ID,
        "task_bundle_sha256": TASK_DIGEST,
        "proof_sha256": digest.sha256_prefixed(PROOF),
        "payment_reference": "8769916-13-0",
        "idempotency_key": "6f1e2d3c-4b5a-6978-8796-a5b4c3d2e1f0",
    }
    headers = signing.submit_headers(signer, **fields)
    expected = validator.submissions.canonical_request_digest(
        signer_coldkey=signer.ss58_address, **fields
    )
    assert headers["X-Conjectures-Coldkey"] == signer.ss58_address
    assert "X-Conjectures-Hotkey" not in headers
    assert signer.verify(
        digest.to_bytes(expected), bytes.fromhex(headers["X-Conjectures-Signature"])
    )


def test_current_validator_response_models_round_trip_through_cli(validator):
    raw = submission_response("0f1e2d3c-4b5a-6978-8796-a5b4c3d2e1f0")
    raw["signer_coldkey"] = None  # Session-authorised submissions do not have a signer.
    raw["bounty"] |= {
        "amount_rao": None,
        "amount_usd": None,
        "available": False,
        "reason": "unavailable",
        "as_of": raw["created_at"],
        "locked": False,
    }
    current = validator.schemas.SubmissionStatus.model_validate(raw)
    parsed = models.SubmissionStatus.model_validate(current.model_dump())
    assert parsed.signer_coldkey is None
    assert parsed.bounty.amount_rao is None

    current_account = validator.account.Account.model_validate(account_response())
    parsed_account = models.Account.model_validate(current_account.model_dump())
    assert parsed_account.wallets[0].coldkey == HOTKEY

    current_session = validator.account.CliSession.model_validate(session_response())
    assert models.CliSession.model_validate(current_session.model_dump()).coldkey_scope == HOTKEY

    raw_summary = submission_page()["items"][0] | {"signer_coldkey": None}
    current_summary = validator.account.SubmissionSummary.model_validate(raw_summary)
    parsed_summary = models.SubmissionSummary.model_validate(current_summary.model_dump())
    assert parsed_summary.signer_coldkey is None
