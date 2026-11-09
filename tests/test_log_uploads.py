"""Large packages must cross the public proxy without changing the ZIP or its consent."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable

import httpx
import pytest

from conjectures_miner.api.client import ApiClient
from conjectures_miner.errors import ApiError, CliError, TransportError
from conjectures_miner.settings import Settings
from tests.conftest import API
from tests.test_logs import CONSENT, TOKEN

UPLOAD_ID = "a3a2475a-ea28-4488-b404-93bf3fc693b6"
SUBMISSION_ID = "123fa3e6-09f2-469d-8af0-1a54190370e4"
UPLOADS = "/v1/me/chat-log-uploads"
PART = f"{UPLOADS}/{UPLOAD_ID}"
PART_BYTES = 8 * 1024 * 1024
HEADERS = {"Authorization": f"Bearer {TOKEN}"}
TERMS = {key: value for key, value in CONSENT.items() if key != "accepted"}
CLAIM = {
    "id": "a41410a5-a767-42cf-b29f-9e51109d9585",
    "status": "DEPOSITED",
    "terms_version": CONSENT["terms_version"],
    "package_sha256": "sha256:" + "b" * 64,
}


def envelope(value: dict, *, error: bool = False) -> httpx.Response:
    return httpx.Response(
        200, content=b"\n\n" + json.dumps({"error" if error else "result": value}).encode()
    )


class UploadServer:
    def __init__(self, *, chunk_bytes: int = PART_BYTES) -> None:
        self.chunk_bytes = chunk_bytes
        self.sent = 0
        self.received = hashlib.sha256()
        self.requests: list[tuple[str, str, int]] = []
        self.hook: Callable[[httpx.Request], httpx.Response | None] = lambda _request: None
        self.completed = False
        self.deleted = False
        self.final_params: dict[str, str] = {}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        method, path = request.method, request.url.path
        self.requests.append((method, path, len(request.content)))
        assert request.headers["Authorization"] == HEADERS["Authorization"]
        assert len(request.content) <= PART_BYTES
        intercepted = self.hook(request)
        if intercepted is not None:
            return intercepted
        if method == "POST":
            assert not request.content
            assert all(request.url.params[key] == value for key, value in CONSENT.items())
            assert request.url.params["keepalive"] == "true"
            if path == UPLOADS:
                self.sent = int(request.url.params["byte_length"])
                return envelope({"id": UPLOAD_ID, "chunk_bytes": self.chunk_bytes})
            assert request.url.params["upload_id"] == UPLOAD_ID
            self.final_params = dict(request.url.params)
            self.completed = True
            return envelope(CLAIM)
        assert path == PART
        if method == "PUT":
            assert request.headers["Content-Type"] == "application/zip"
            offset = int(request.url.params["offset"])
            assert len(request.content) == min(self.chunk_bytes, self.sent - offset)
            self.received.update(request.content)
            return httpx.Response(200, json={"received_bytes": offset + len(request.content)})
        assert method == "DELETE"
        self.deleted = True
        return httpx.Response(204)


@pytest.mark.parametrize("revision", [False, True], ids=["deposit", "revision"])
def test_125_mb_crosses_a_proxy_with_small_requests_and_unchanged_bytes(revision: bool):
    package = (b"original chat log\n" * 8_000_000)[:125_000_000]
    server = UploadServer()
    with ApiClient(Settings(), transport=httpx.MockTransport(server)) as client:
        if revision:
            result = client.revise_chat_logs(
                SUBMISSION_ID,
                package=package,
                headers=HEADERS,
                expected_state_version=7,
                **TERMS,
            )
        else:
            result = client.deposit_chat_logs(package=package, headers=HEADERS, **TERMS)
    assert result.status == "DEPOSITED"
    assert server.sent == len(package) == 125_000_000
    assert server.received.digest() == hashlib.sha256(package).digest()
    assert len([r for r in server.requests if r[0] == "PUT"]) == 15
    assert server.completed and server.deleted
    assert server.final_params.get("expected_state_version") == ("7" if revision else None)
    final_path = (
        f"/v1/me/submissions/{SUBMISSION_ID}/trajectory" if revision else "/v1/me/chat-log-deposits"
    )
    assert server.requests[-2] == ("POST", final_path, 0)


@pytest.mark.parametrize("failure", ["disconnect", "timeout", "server", "rate_limit"])
def test_interrupted_part_retries_identical_bytes(failure: str, monkeypatch: pytest.MonkeyPatch):
    sleeps = []
    monkeypatch.setattr("conjectures_miner.api.client.time.sleep", sleeps.append)
    server = UploadServer()
    attempts: list[tuple[str, bytes]] = []

    def interrupt(request: httpx.Request) -> httpx.Response | None:
        if request.method != "PUT" or request.url.params["offset"] != "0":
            return None
        attempts.append((str(request.url), hashlib.sha256(request.content).digest()))
        if len(attempts) > 1:
            return None
        if failure == "disconnect":
            raise httpx.ReadError("response lost", request=request)
        status = {"timeout": 408, "server": 503, "rate_limit": 429}[failure]
        return httpx.Response(status, json={"detail": "try again"}, headers={"Retry-After": "2"})

    server.hook = interrupt
    package = b"x" * (PART_BYTES + 19)
    with ApiClient(Settings(), transport=httpx.MockTransport(server)) as client:
        client.deposit_chat_logs(package=package, headers=HEADERS, **TERMS)
    assert len(attempts) == 2 and attempts[0] == attempts[1]
    assert len(sleeps) == 1
    assert server.received.digest() == hashlib.sha256(package).digest()
    assert server.completed and server.deleted


def test_exhausted_part_retries_discard_the_upload(monkeypatch: pytest.MonkeyPatch):
    sleeps = []
    monkeypatch.setattr("conjectures_miner.api.client.time.sleep", sleeps.append)
    server = UploadServer()
    server.hook = lambda request: httpx.Response(503) if request.method == "PUT" else None
    with (
        ApiClient(Settings(), transport=httpx.MockTransport(server)) as client,
        pytest.raises(ApiError) as error,
    ):
        client.deposit_chat_logs(package=b"x" * (PART_BYTES + 1), headers=HEADERS, **TERMS)
    assert error.value.status_code == 503
    assert len([r for r in server.requests if r[0] == "PUT"]) == 4
    assert sleeps == [1, 2, 4]
    assert server.deleted and not server.completed


@pytest.mark.parametrize("chunk_bytes", [0, -1, PART_BYTES + 1, 125_000_000])
def test_unsafe_part_size_is_rejected_and_cleaned_up(chunk_bytes: int):
    server = UploadServer(chunk_bytes=chunk_bytes)
    with (
        ApiClient(Settings(), transport=httpx.MockTransport(server)) as client,
        pytest.raises(CliError, match=r"invalid.*part size"),
    ):
        client.deposit_chat_logs(package=b"x" * (PART_BYTES + 1), headers=HEADERS, **TERMS)
    assert server.deleted and not server.completed
    assert not any(r[0] == "PUT" for r in server.requests)


def test_incomplete_acknowledgement_never_finalizes():
    server = UploadServer()
    server.hook = lambda request: (
        httpx.Response(200, json={"received_bytes": 1}) if request.method == "PUT" else None
    )
    with (
        ApiClient(Settings(), transport=httpx.MockTransport(server)) as client,
        pytest.raises(CliError, match="acknowledge"),
    ):
        client.deposit_chat_logs(package=b"x" * (PART_BYTES + 1), headers=HEADERS, **TERMS)
    assert server.deleted and not server.completed


@pytest.mark.parametrize("phase", ["start", "finalize"])
def test_ambiguous_post_failure_is_not_replayed(phase: str):
    server = UploadServer()

    def disconnect(request: httpx.Request) -> None:
        if request.method == "POST" and (request.url.path == UPLOADS) == (phase == "start"):
            raise httpx.ReadTimeout("reply lost", request=request)

    server.hook = disconnect
    with (
        ApiClient(Settings(), transport=httpx.MockTransport(server)) as client,
        pytest.raises(TransportError),
    ):
        client.deposit_chat_logs(package=b"x" * (PART_BYTES + 1), headers=HEADERS, **TERMS)
    posts = [path for method, path, _size in server.requests if method == "POST"]
    assert posts == ([UPLOADS] if phase == "start" else [UPLOADS, "/v1/me/chat-log-deposits"])
    assert server.deleted == (phase == "finalize")


def test_error_inside_http_200_keepalive_is_not_success():
    server = UploadServer()

    def reject(request: httpx.Request) -> httpx.Response | None:
        if request.method == "POST" and request.url.path != UPLOADS:
            return envelope(
                {
                    "status": 400,
                    "detail": "Invalid chat-log package",
                    "reason_code": "TRAJECTORY_INVALID",
                },
                error=True,
            )
        return None

    server.hook = reject
    with (
        ApiClient(Settings(), transport=httpx.MockTransport(server)) as client,
        pytest.raises(ApiError) as error,
    ):
        client.deposit_chat_logs(package=b"x" * (PART_BYTES + 1), headers=HEADERS, **TERMS)
    assert error.value.status_code == 400
    assert error.value.reason_code == "TRAJECTORY_INVALID"
    assert server.deleted and not server.completed


def test_keepalive_rate_limit_honors_the_server_delay(monkeypatch: pytest.MonkeyPatch):
    sleeps = []
    monkeypatch.setattr("conjectures_miner.api.client.time.sleep", sleeps.append)
    server = UploadServer()
    attempts = 0

    def limit(request: httpx.Request) -> httpx.Response | None:
        nonlocal attempts
        if request.url.path == UPLOADS:
            attempts += 1
            if attempts == 1:
                return envelope(
                    {"status": 429, "detail": "busy", "retry_after_seconds": 2}, error=True
                )
        return None

    server.hook = limit
    with ApiClient(Settings(), transport=httpx.MockTransport(server)) as client:
        client.deposit_chat_logs(package=b"x" * (PART_BYTES + 1), headers=HEADERS, **TERMS)
    assert attempts == 2 and sleeps == [2]
    assert server.completed and server.deleted


def test_failed_cleanup_does_not_hide_success():
    server = UploadServer()
    server.hook = lambda request: httpx.Response(503) if request.method == "DELETE" else None
    with ApiClient(Settings(), transport=httpx.MockTransport(server)) as client:
        result = client.deposit_chat_logs(package=b"x" * (PART_BYTES + 1), headers=HEADERS, **TERMS)
    assert result.status == "DEPOSITED" and server.completed


def test_eight_mib_package_keeps_the_existing_direct_upload():
    package = b"x" * PART_BYTES

    def direct(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/me/chat-log-deposits"
        assert request.method == "POST" and request.content == package
        assert dict(request.url.params) == CONSENT
        return httpx.Response(200, json=CLAIM)

    with ApiClient(Settings(api_base_url=API), transport=httpx.MockTransport(direct)) as client:
        assert (
            client.deposit_chat_logs(package=package, headers=HEADERS, **TERMS).status
            == "DEPOSITED"
        )
