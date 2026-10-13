"""`conjectures logs upload`: what is agreed to, what is sent, and what is refused unsent."""

from __future__ import annotations

import hashlib
import io
import json
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from pytest_httpx import HTTPXMock
from typer.testing import CliRunner

from conjectures_miner import chat_logs, digest
from conjectures_miner import session as session_module
from conjectures_miner.cli import app
from conjectures_miner.errors import CliError, ConfigError, api_error
from tests.conftest import API, HOTKEY, PROOF, TASK_DIGEST, TASK_ID

runner = CliRunner()
JSON = ["--output", "json"]
TOKEN = "a-session-token-that-must-never-be-printed"
DEPOSITS = f"{API}/v1/me/chat-log-deposits"
CONSENT = {
    "terms_version": chat_logs.TERMS_VERSION,
    "terms_sha256": chat_logs.TERMS_SHA256,
    "assent_statements_sha256": chat_logs.ASSENT_STATEMENTS_SHA256,
    "accepted": "true",
}


def invoke(*args: str):
    return runner.invoke(app, [*JSON, *args])


def signed_in() -> None:
    session_module.save(
        session_module.Session(
            access_token=TOKEN,
            expires_at=datetime.now(UTC) + timedelta(hours=24),
            api_base_url=API,
            coldkey=HOTKEY,
            account_id="6f1b6d4e-2b0a-4a5f-9c3e-0d8a1f2b3c4d",
        )
    )


def offer(**changes: object) -> dict:
    return {
        "enabled": True,
        "terms_version": chat_logs.TERMS_VERSION,
        "terms_sha256": chat_logs.TERMS_SHA256,
        "assent_statements_sha256": chat_logs.ASSENT_STATEMENTS_SHA256,
        "deposit_hours": 24,
        "max_package_bytes": 64 * 1024 * 1024,
    } | changes


def package(tmp_path: Path, proof: bytes = PROOF) -> Path:
    manifest = {
        "task_id": TASK_ID,
        "task_bundle_sha256": TASK_DIGEST,
        "final_proof_sha256": digest.sha256_prefixed(proof),
    }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("chat-log-package/manifest.json", json.dumps(manifest))
        archive.writestr("chat-log-package/events.jsonl", "{}\n")
    path = tmp_path / "chat-log-package.zip"
    path.write_bytes(buffer.getvalue())
    return path


def test_upload_sends_the_package_with_the_pinned_consent(
    httpx_mock: HTTPXMock, tmp_path: Path, built: tuple[Path, Path]
):
    signed_in()
    _, plan = built
    logs = package(tmp_path)
    httpx_mock.add_response(url=f"{API}/v1/trajectory-offer", json=offer())
    httpx_mock.add_response(
        method="POST",
        url=httpx.URL(DEPOSITS, params=CONSENT),
        json={
            "id": "0b6c9c1e-63a4-4c41-9d0a-6a0fb1b8f5d1",
            "status": "DEPOSITED",
            "package_sha256": "sha256:" + "b" * 64,
            "terms_version": chat_logs.TERMS_VERSION,
            "expires_at": "2026-10-15T00:00:00Z",
        },
    )

    result = invoke("logs", "upload", str(logs), "--plan", str(plan), "--yes")
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["status"] == "DEPOSITED"

    sent = httpx_mock.get_requests()[-1]
    assert sent.headers["Authorization"] == f"Bearer {TOKEN}"
    assert sent.headers["Content-Type"] == "application/zip"
    assert sent.content == logs.read_bytes()
    assert TOKEN not in result.output


def test_upload_needs_a_session_before_anything_is_read(httpx_mock: HTTPXMock, tmp_path: Path):
    result = invoke("logs", "upload", str(package(tmp_path)), "--yes")
    assert isinstance(result.exception, ConfigError)
    assert httpx_mock.get_requests() == []


def test_upload_refuses_terms_this_tool_has_not_shown(httpx_mock: HTTPXMock, tmp_path: Path):
    signed_in()
    httpx_mock.add_response(
        url=f"{API}/v1/trajectory-offer",
        json=offer(terms_version="chat-log-terms-v3", terms_sha256="sha256:" + "c" * 64),
    )
    result = invoke("logs", "upload", str(package(tmp_path)), "--yes")
    assert isinstance(result.exception, CliError)
    assert "Upgrade conjectures-miner" in (result.exception.hint or "")
    assert [request.method for request in httpx_mock.get_requests()] == ["GET"]


def test_upload_refuses_logs_for_another_proof_than_the_plan_seals(
    httpx_mock: HTTPXMock, tmp_path: Path, built: tuple[Path, Path]
):
    signed_in()
    _, plan = built
    httpx_mock.add_response(url=f"{API}/v1/trajectory-offer", json=offer())
    other = package(tmp_path, proof=b"theorem other : True := trivial\n")
    result = invoke("logs", "upload", str(other), "--plan", str(plan), "--yes")
    assert isinstance(result.exception, CliError)
    assert "final_proof_sha256" in str(result.exception)
    assert [request.method for request in httpx_mock.get_requests()] == ["GET"]


def test_upload_refuses_a_file_that_is_not_a_package(httpx_mock: HTTPXMock, tmp_path: Path):
    signed_in()
    httpx_mock.add_response(url=f"{API}/v1/trajectory-offer", json=offer())
    junk = tmp_path / "logs.zip"
    junk.write_bytes(b"not a zip")
    result = invoke("logs", "upload", str(junk), "--yes")
    assert isinstance(result.exception, CliError)
    assert "not a chat-log package" in str(result.exception)


def test_pinned_assent_text_is_the_text_its_digest_names():
    digest_ = "sha256:" + hashlib.sha256(chat_logs.ASSENT_STATEMENTS).hexdigest()
    assert digest_ == chat_logs.ASSENT_STATEMENTS_SHA256
    assert chat_logs.ASSENT.startswith("I agree to the chat-log terms.")


@pytest.mark.parametrize("reason", ["CHAT_LOG_REQUIRED", "CHAT_LOG_ACCOUNT_REQUIRED"])
def test_a_proof_refused_for_missing_logs_says_how_to_upload_and_that_nothing_was_spent(reason):
    hint = api_error(409, {"reason_code": reason, "detail": "logs"}).hint or ""
    assert "conjectures logs upload" in hint
    assert "not spent" in hint
