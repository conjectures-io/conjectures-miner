"""The write path end to end against a mocked validator: what the miner actually types."""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

import pytest
from pytest_httpx import HTTPXMock
from typer.testing import CliRunner

from conjectures_miner import digest, plan, signing
from conjectures_miner.cli import app
from tests.conftest import (
    API,
    CHALLENGE,
    HOTKEY,
    PROOF,
    TASK_DIGEST,
    TASK_ID,
    conjecture_response,
    read_manifest,
    task_list_response,
)

runner = CliRunner()
# `//Alice` resolves to HOTKEY, and the guard permits a development key against a local API only.
ALICE = ["--uri", "//Alice"]
# Explicit, because `auto` renders a table whenever rich thinks it is looking at a terminal.
JSON = ["--output", "json"]
# A real reference is a position, `block-extrinsic[-event]`, not an extrinsic hash.
PAYMENT = "4821993-2-1"


def invoke(*args: str) -> Any:
    """Run the CLI and surface a refusal as the exception it is."""
    return runner.invoke(app, [*JSON, *args])


def succeed(*args: str) -> dict:
    result = invoke(*args)
    if result.exception and not isinstance(result.exception, SystemExit):
        raise result.exception
    return json.loads(result.stdout)


def refusal(*args: str) -> BaseException:
    result = invoke(*args)
    assert result.exception is not None, result.stdout
    return result.exception


def submission_response(submission_id: str) -> dict:
    return {
        "submission_id": submission_id,
        "hotkey": HOTKEY,
        "task_id": TASK_ID,
        "task_bundle_sha256": TASK_DIGEST,
        "proof_sha256": digest.sha256_prefixed(PROOF),
        "request_digest": "sha256:" + "c" * 64,
        "verification_status": "UNVERIFIED",
        "manual_review_status": "UNREVIEWED",
        "reward_status": "INELIGIBLE",
        "manual_review_required": True,
        "review_policy_version": "v1",
        "payment": {
            "reference": PAYMENT,
            "sender": HOTKEY,
            "amount_rao": 500_000_000,
            "block": 42,
        },
        "bounty": {"amount_rao": 1_000, "policy_version": "v1"},
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
    }


def test_the_challenge_is_saved_byte_for_byte_in_a_directory_of_its_own(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, httpx_mock: HTTPXMock
):
    httpx_mock.add_response(url=f"{API}/v1/tasks", json=task_list_response())
    httpx_mock.add_response(
        url=f"{API}/v1/catalog/conjectures/{TASK_ID}", json=conjecture_response()
    )
    succeed("tasks", "sync")
    monkeypatch.chdir(tmp_path)

    saved = succeed("tasks", "challenge", "erdos89")

    assert saved["challenge"] == str(Path("challenges") / TASK_ID / "Challenge.lean")
    assert (tmp_path / saved["challenge"]).read_bytes() == CHALLENGE.encode("utf-8")
    # What you are proving, and which way round: `counterexample` wants the negation.
    assert saved["task_mode"] == "formalized"
    assert saved["target_theorem"] == "Bounty.target"


def test_the_challenge_says_when_the_catalog_and_the_cache_disagree(
    tmp_path: Path, httpx_mock: HTTPXMock
):
    httpx_mock.add_response(url=f"{API}/v1/tasks", json=task_list_response())
    httpx_mock.add_response(
        url=f"{API}/v1/catalog/conjectures/{TASK_ID}",
        json=conjecture_response(task_bundle_sha256="sha256:" + "b" * 64),
    )
    succeed("tasks", "sync")

    result = invoke("tasks", "challenge", TASK_ID, "--dir", str(tmp_path / "elsewhere"))

    assert result.exit_code == 0
    assert (tmp_path / "elsewhere" / TASK_ID / "Challenge.lean").is_file()
    assert "tasks sync" in result.stderr


def test_build_is_offline_and_names_the_task_by_prefix(
    tmp_path: Path, proof_file: Path, httpx_mock: HTTPXMock
):
    httpx_mock.add_response(url=f"{API}/v1/tasks", json=task_list_response())
    succeed("tasks", "sync")

    built = succeed(
        "build",
        "--proof",
        str(proof_file),
        "--task",
        "erdos89",
        "--hotkey-ss58",
        HOTKEY,
        "--output",
        str(tmp_path / "submission.zip"),
        "--plan",
        str(tmp_path / "submission.plan.json"),
    )
    assert built["task_id"] == TASK_ID
    assert built["task_bundle_sha256"] == TASK_DIGEST
    assert built["proof_sha256"] == digest.sha256_prefixed(PROOF)
    assert read_manifest(tmp_path / "submission.zip")["miner_hotkey"] == HOTKEY
    # One request in total: the sync. Building itself touched no network.
    assert len(httpx_mock.get_requests()) == 1


def test_the_plan_records_what_it_takes_to_submit_and_an_empty_payment_slot(
    tmp_path: Path, proof_file: Path, httpx_mock: HTTPXMock
):
    httpx_mock.add_response(url=f"{API}/v1/tasks", json=task_list_response(commit="deadbeef"))
    succeed("tasks", "sync")
    succeed(
        "build",
        "--proof",
        str(proof_file),
        "--task",
        TASK_ID,
        "--hotkey-ss58",
        HOTKEY,
        "--output",
        str(tmp_path / "submission.zip"),
        "--plan",
        str(tmp_path / "submission.plan.json"),
    )

    written = plan.read(tmp_path / "submission.plan.json")
    assert written.bundle.path == "submission.zip"  # relative to the plan
    assert written.payment.reference is None
    assert written.payment.price_rao_seen == 500_000_000
    assert written.provenance.repository_commit == "deadbeef"
    assert written.manifest["task_id"] == TASK_ID


def test_check_sends_the_archive_and_reads_identity_out_of_its_manifest(
    built: tuple[Path, Path], httpx_mock: HTTPXMock
):
    _, plan_path = built
    httpx_mock.add_response(
        url=f"{API}/v1/submissions/preflight",
        json={"ok": True, "proof_sha256": digest.sha256_prefixed(PROOF), "proof_bytes": len(PROOF)},
    )
    assert succeed("check", "--plan", str(plan_path))["ok"] is True

    request = httpx_mock.get_request()
    assert request is not None
    assert request.headers["X-Conjectures-Task-Id"] == TASK_ID
    assert request.headers["X-Conjectures-Task-Sha256"] == TASK_DIGEST
    assert request.headers["X-Conjectures-Hotkey"] == HOTKEY
    assert request.headers["Content-Type"] == "application/zip"


def test_check_exits_non_zero_on_a_refusal_so_it_gates_submit(
    built: tuple[Path, Path], httpx_mock: HTTPXMock
):
    _, plan_path = built
    httpx_mock.add_response(
        url=f"{API}/v1/submissions/preflight",
        json={"ok": False, "reason_code": "SUBMISSION_POLICY_VIOLATION", "line": 3, "column": 1},
    )
    result = invoke("check", "--plan", str(plan_path))
    assert result.exit_code == 1
    assert json.loads(result.stdout)["reason_code"] == "SUBMISSION_POLICY_VIOLATION"


def test_submit_signs_the_canonical_request_digest(built: tuple[Path, Path], httpx_mock: HTTPXMock):
    archive_path, plan_path = built
    submission_id = str(uuid.uuid4())
    httpx_mock.add_response(
        url=f"{API}/v1/submissions", status_code=201, json=submission_response(submission_id)
    )

    sent = succeed(*ALICE, "submit", "--plan", str(plan_path), "--payment-ref", PAYMENT, "--yes")
    assert sent["submission_id"] == submission_id

    request = httpx_mock.get_request()
    assert request is not None
    assert request.content == archive_path.read_bytes()
    expected = digest.request_digest(
        hotkey=HOTKEY,
        task_id=TASK_ID,
        task_bundle_sha256=TASK_DIGEST,
        proof_sha256=digest.sha256_prefixed(PROOF),
        payment_reference=PAYMENT,
        idempotency_key=request.headers["Idempotency-Key"],
    )
    assert _verifies(request.headers["X-Conjectures-Signature"], digest.to_bytes(expected))
    assert uuid.UUID(request.headers["Idempotency-Key"])  # the validator requires a UUID


def test_the_idempotency_key_is_persisted_before_the_request_goes_out(
    built: tuple[Path, Path], httpx_mock: HTTPXMock, isolated_home: Path
):
    _, plan_path = built
    httpx_mock.add_response(
        url=f"{API}/v1/submissions",
        status_code=503,
        json={"reason_code": "SUBMISSIONS_PAUSED", "detail": "submissions are paused"},
    )
    error = refusal(*ALICE, "submit", "--plan", str(plan_path), "--payment-ref", PAYMENT, "--yes")
    assert getattr(error, "reason_code", None) == "SUBMISSIONS_PAUSED"

    records = list((isolated_home / "state").glob("*.json"))
    assert len(records) == 1
    stored = json.loads(records[0].read_text())
    assert stored["payment_reference"] == PAYMENT
    assert stored["submission_id"] is None


def test_a_retry_reuses_the_stored_key(built: tuple[Path, Path], httpx_mock: HTTPXMock):
    _, plan_path = built
    httpx_mock.add_response(url=f"{API}/v1/submissions", status_code=500, json={})
    refusal(*ALICE, "submit", "--plan", str(plan_path), "--payment-ref", PAYMENT, "--yes")

    httpx_mock.add_response(
        url=f"{API}/v1/submissions", status_code=200, json=submission_response(str(uuid.uuid4()))
    )
    succeed(*ALICE, "submit", "--plan", str(plan_path), "--payment-ref", PAYMENT, "--yes")

    keys = {request.headers["Idempotency-Key"] for request in httpx_mock.get_requests()}
    assert len(keys) == 1


def test_a_reference_no_node_could_resolve_is_refused_before_the_key_is_unlocked(
    built: tuple[Path, Path], httpx_mock: HTTPXMock
):
    _, plan_path = built
    # Pasted from a block explorer, spaces and all.
    error = refusal(
        *ALICE, "submit", "--plan", str(plan_path), "--payment-ref", "4821993 2 1", "--yes"
    )
    assert "is not a payment reference" in str(error)
    assert getattr(error, "exit_code", None) == 2
    assert not httpx_mock.get_requests()


def test_a_development_reference_warns_but_still_goes_out(
    built: tuple[Path, Path], httpx_mock: HTTPXMock
):
    _, plan_path = built
    httpx_mock.add_response(
        url=f"{API}/v1/submissions", status_code=201, json=submission_response(str(uuid.uuid4()))
    )
    result = invoke(
        *ALICE, "submit", "--plan", str(plan_path), "--payment-ref", "dev-payment-1", "--yes"
    )
    assert result.exit_code == 0
    assert "only a development validator" in result.stderr
    request = httpx_mock.get_request()
    assert request is not None
    assert request.headers["X-Conjectures-Payment-Ref"] == "dev-payment-1"


def test_the_reference_the_validator_recorded_is_reported_back(
    built: tuple[Path, Path], httpx_mock: HTTPXMock
):
    """The validator stores the canonical three-part identity of a two-part reference."""
    _, plan_path = built
    httpx_mock.add_response(
        url=f"{API}/v1/submissions", status_code=201, json=submission_response(str(uuid.uuid4()))
    )
    sent = succeed(
        *ALICE, "submit", "--plan", str(plan_path), "--payment-ref", "4821993-2", "--yes"
    )
    assert sent["payment_reference"] == PAYMENT

    request = httpx_mock.get_request()
    assert request is not None
    # What was signed is what was typed. The canonical form is the validator's answer, not an
    # input, so it must not leak into the digest.
    assert request.headers["X-Conjectures-Payment-Ref"] == "4821993-2"


def test_watching_waits_out_a_rate_limit_instead_of_failing(
    httpx_mock: HTTPXMock, monkeypatch: pytest.MonkeyPatch
):
    submission_id = str(uuid.uuid4())
    settled = submission_response(submission_id) | {"verification_status": "VERIFIED"}
    httpx_mock.add_response(
        url=f"{API}/v1/submissions/{submission_id}", json=submission_response(submission_id)
    )
    httpx_mock.add_response(
        url=f"{API}/v1/submissions/{submission_id}",
        status_code=429,
        headers={"Retry-After": "90"},
        json={"reason_code": "RATE_LIMITED", "detail": "request rate exceeded"},
    )
    httpx_mock.add_response(url=f"{API}/v1/submissions/{submission_id}", json=settled)

    slept: list[float] = []
    monkeypatch.setattr("conjectures_miner.commands.submissions.time.sleep", slept.append)
    watched = succeed(*ALICE, "submissions", "show", submission_id, "--watch")

    assert watched["verification_status"] == "VERIFIED"
    # The interval the validator asked for, honoured over the client's own backoff.
    assert slept[-1] == 90.0


def test_dev_signature_sends_the_marker_and_opens_no_key(
    built: tuple[Path, Path], httpx_mock: HTTPXMock, monkeypatch: pytest.MonkeyPatch
):
    _, plan_path = built
    httpx_mock.add_response(
        url=f"{API}/v1/submissions", status_code=201, json=submission_response(str(uuid.uuid4()))
    )
    # Any attempt to open a private key is the failure this mode exists to avoid.
    monkeypatch.setattr(
        "conjectures_miner.signing._wallet", lambda _: pytest.fail("opened a wallet")
    )
    succeed(
        *ALICE,
        "--dev-signature",
        "submit",
        "--plan",
        str(plan_path),
        "--payment-ref",
        PAYMENT,
        "--yes",
    )

    request = httpx_mock.get_request()
    assert request is not None
    assert bytes.fromhex(request.headers["X-Conjectures-Signature"]) == signing.DEVELOPMENT_MARKER
    assert request.headers["X-Conjectures-Hotkey"] == HOTKEY


def test_the_real_signature_is_what_you_get_without_the_flag(
    built: tuple[Path, Path], httpx_mock: HTTPXMock
):
    _, plan_path = built
    httpx_mock.add_response(
        url=f"{API}/v1/submissions", status_code=201, json=submission_response(str(uuid.uuid4()))
    )
    succeed(*ALICE, "submit", "--plan", str(plan_path), "--payment-ref", PAYMENT, "--yes")

    request = httpx_mock.get_request()
    assert request is not None
    assert bytes.fromhex(request.headers["X-Conjectures-Signature"]) != signing.DEVELOPMENT_MARKER


def test_submit_refuses_when_the_archive_commits_to_another_hotkey(built: tuple[Path, Path]):
    _, plan_path = built
    error = refusal(
        "--uri", "//Bob", "submit", "--plan", str(plan_path), "--payment-ref", PAYMENT, "--yes"
    )
    assert "is signing" in str(error)


def test_submit_without_a_payment_says_what_is_missing(built: tuple[Path, Path]):
    _, plan_path = built
    assert "payment reference" in str(refusal(*ALICE, "submit", "--plan", str(plan_path), "--yes"))


def test_a_development_key_is_refused_against_a_remote_validator(
    built: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
):
    _, plan_path = built
    monkeypatch.setenv("CONJECTURES_API_BASE_URL", "https://api.conjectures.io")
    error = refusal(*ALICE, "submit", "--plan", str(plan_path), "--payment-ref", PAYMENT, "--yes")
    assert "development key" in str(error)


def test_build_refuses_to_discard_a_payment_a_plan_already_carries(
    built: tuple[Path, Path], proof_file: Path, tmp_path: Path
):
    _, plan_path = built
    paid = plan.read(plan_path)
    paid.payment.reference = PAYMENT
    plan.write(plan_path, paid)

    error = refusal(
        "build",
        "--proof",
        str(proof_file),
        "--task",
        TASK_ID,
        "--task-sha256",
        TASK_DIGEST,
        "--hotkey-ss58",
        HOTKEY,
        "--output",
        str(tmp_path / "submission.zip"),
        "--plan",
        str(plan_path),
    )
    assert "already carries payment" in str(error)


def test_an_api_refusal_carries_the_reason_code_and_the_advice(
    built: tuple[Path, Path], httpx_mock: HTTPXMock
):
    _, plan_path = built
    httpx_mock.add_response(
        url=f"{API}/v1/submissions",
        status_code=404,
        json={"reason_code": "TASK_NOT_ALLOWED", "detail": "task is not allowed"},
    )
    error = refusal(*ALICE, "submit", "--plan", str(plan_path), "--payment-ref", PAYMENT, "--yes")
    assert getattr(error, "reason_code", None) == "TASK_NOT_ALLOWED"
    assert "was not consumed" in (getattr(error, "hint", "") or "")


def test_status_reports_a_stale_task_cache(httpx_mock: HTTPXMock):
    httpx_mock.add_response(url=f"{API}/v1/tasks", json=task_list_response(commit="old"))
    succeed("tasks", "sync")
    httpx_mock.add_response(
        url=f"{API}/v1/system/status",
        json={
            "status": "ok",
            "submissions_open": True,
            "repository_commit": "new",
            "queue_depths": {
                "awaiting_verification": 1,
                "awaiting_review": 0,
                "awaiting_reward": 0,
            },
            "pin_rotation": {
                "weekday": 0,
                "starts_at": "2026-01-05T00:00:00Z",
                "ends_at": "2026-01-05T01:00:00Z",
                "in_progress": False,
                "drained": True,
            },
        },
    )
    assert succeed("status")["task_cache_current"] is False


def _verifies(signature_hex: str, message: bytes) -> bool:
    from bittensor.sp_core import Keypair

    return Keypair(ss58_address=HOTKEY).verify(message, bytes.fromhex(signature_hex))


def test_reading_a_submission_signs_the_read_message_not_the_request_digest(
    httpx_mock: HTTPXMock,
):
    submission_id = str(uuid.uuid4())
    httpx_mock.add_response(
        url=f"{API}/v1/submissions/{submission_id}", json=submission_response(submission_id)
    )
    assert succeed(*ALICE, "submissions", "show", submission_id)["task_id"] == TASK_ID

    request = httpx_mock.get_request()
    assert request is not None
    assert request.headers["X-Conjectures-Hotkey"] == HOTKEY
    expected = digest.read_message(hotkey_ss58=HOTKEY, submission_id=submission_id)
    assert _verifies(request.headers["X-Conjectures-Signature"], expected)


def test_a_report_that_does_not_exist_yet_is_reported_as_normal(httpx_mock: HTTPXMock):
    submission_id = str(uuid.uuid4())
    httpx_mock.add_response(
        url=f"{API}/v1/submissions/{submission_id}/report",
        status_code=409,
        json={"reason_code": "CONFLICT", "detail": "verification has not produced a report yet"},
    )
    result = invoke(*ALICE, "submissions", "report", submission_id)
    assert result.exit_code == 1
