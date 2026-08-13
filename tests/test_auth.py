"""`conjectures auth` end to end: what is signed, what is stored, and what never leaves the file.

The token is the only reusable credential this tool holds, so most of what is asserted here is
negative -- that it is not world-readable, not sent to the wrong validator, and not printed.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from pytest_httpx import HTTPXMock
from typer.testing import CliRunner

from conjectures_miner import session as session_module
from conjectures_miner.cli import app
from conjectures_miner.errors import ConfigError
from conjectures_miner.signing import assert_session_challenge
from tests.conftest import API, HOTKEY

runner = CliRunner()
ALICE = ["--uri", "//Alice"]
JSON = ["--output", "json"]

TOKEN = "a-session-token-that-must-never-be-printed"
ACCOUNT_ID = "6f1b6d4e-2b0a-4a5f-9c3e-0d8a1f2b3c4d"

# Non-ASCII and awkward whitespace on purpose: a message that survives a round trip through
# `.strip()`, a re-encode or a rebuild is not a message that proves anything.
CHALLENGE_MESSAGE = (
    "conjectures-cli-session-v1\n"
    "domain: conjectures.io\n"
    f"address: {HOTKEY}\n"
    "nonce: ¬nonce-with-a-negation\n"
    "expires: 2026-08-12T12:00:00Z\n"
)


def invoke(*args: str):
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


def _later(hours: int = 24) -> datetime:
    return datetime.now(UTC) + timedelta(hours=hours)


def challenge_response(
    message: str = CHALLENGE_MESSAGE, *, nonce: str = "¬nonce-with-a-negation"
) -> dict:
    return {
        "nonce": nonce,
        "message": message,
        "expires_at": _later(hours=1).isoformat(),
    }


def account_response(hotkeys: list[str] | None = None) -> dict:
    """The account. Holds this hotkey unless told otherwise; `[]` is one that has none yet."""
    attached = [HOTKEY] if hotkeys is None else hotkeys
    return {
        "id": ACCOUNT_ID,
        "email": "miner@example.com",
        "email_verified": True,
        "display_name": "a miner",
        "roles": ["MINER"],
        "payout": {"coldkey": HOTKEY, "hotkey": HOTKEY},
        "hotkeys": [
            {"hotkey": key, "linked_at": "2026-01-01T00:00:00Z"} for key in attached
        ],
        "wallets": [{"coldkey": HOTKEY, "linked_at": "2026-01-01T00:00:00Z"}],
        "created_at": "2026-01-01T00:00:00Z",
    }


def session_response(token: str = TOKEN) -> dict:
    return {
        "access_token": token,
        "token_type": "bearer",
        "expires_at": _later().isoformat(),
        "hotkey_scope": HOTKEY,
        "account": account_response(),
    }


def submission_page(next_cursor: str | None = None) -> dict:
    return {
        "items": [
            {
                "id": str(uuid.uuid4()),
                "hotkey": HOTKEY,
                "task_id": "fc-379fc029-erdos89-erdos-89-c956ed476a-formalized-v1",
                "proof_sha256": "sha256:" + "b" * 64,
                "verification_status": "VERIFIED",
                "manual_review_status": "APPROVED",
                "reward_status": "PAID",
                "bounty_amount_rao": 500_000_000,
                "bounty_policy_version": "v1",
                "bounty_locked": True,
                "created_at": "2026-08-01T00:00:00Z",
                "updated_at": "2026-08-02T00:00:00Z",
            }
        ],
        "next_cursor": next_cursor,
    }


def mock_login(httpx_mock: HTTPXMock, *, message: str = CHALLENGE_MESSAGE) -> None:
    httpx_mock.add_response(url=f"{API}/v1/auth/cli/challenge", json=challenge_response(message))
    httpx_mock.add_response(url=f"{API}/v1/auth/cli/verify", json=session_response())


COLDKEY = "5FHneW46xGXgs5mUiveU4sbTyGBzmstUspZC92UhjJM694ty"  # //Bob
BOB = ["--coldkey-uri", "//Bob"]
CSRF = "a-csrf-token-bound-to-the-session-row"

LOGIN_MESSAGE = (
    "conjectures-login-v1\n"
    "domain: conjectures.io\n"
    f"address: {COLDKEY}\n"
    "nonce: a-login-nonce\n"
    "expires: 2026-08-12T12:00:00Z\n"
)
LINK_MESSAGE = (
    "conjectures-hotkey-link-v1\n"
    "domain: conjectures.io\n"
    f"address: {HOTKEY}\n"
    "nonce: a-link-nonce\n"
    "expires: 2026-08-12T12:00:00Z\n"
)
# What `/v1/auth/wallet/verify` actually answers with: the credential is in the headers, and only
# the CSRF half is readable by the client. Two headers, not one -- the validator appends them
# separately so that neither overwrites the other.
SESSION_COOKIES = [
    ("set-cookie", "conjectures_session=a-browser-session; Path=/; HttpOnly"),
    ("set-cookie", f"conjectures_csrf={CSRF}; Path=/"),
]


def mock_register(
    httpx_mock: HTTPXMock,
    *,
    login_message: str = LOGIN_MESSAGE,
    link_message: str = LINK_MESSAGE,
    already_linked: bool = False,
    link_status: int = 201,
    link_json: dict | None = None,
    expect_link: bool = True,
) -> None:
    """The four calls a registration makes, plus the revoke it always ends with.

    Only what the run will actually request is registered: `pytest_httpx` fails a test that
    mocked a call nobody made, which is the behaviour wanted here -- a guard that was supposed
    to refuse before `/v1/me/hotkeys` would otherwise pass silently.
    """
    httpx_mock.add_response(
        url=f"{API}/v1/auth/wallet/challenge",
        json=challenge_response(login_message, nonce="a-login-nonce"),
    )
    httpx_mock.add_response(
        url=f"{API}/v1/auth/wallet/verify",
        json={"account": account_response([HOTKEY] if already_linked else [])},
        headers=SESSION_COOKIES,
    )
    if not already_linked:
        httpx_mock.add_response(
            url=f"{API}/v1/me/hotkeys/challenge",
            json=challenge_response(link_message, nonce="a-link-nonce"),
        )
        if expect_link:
            httpx_mock.add_response(
                url=f"{API}/v1/me/hotkeys",
                status_code=link_status,
                json=link_json if link_json is not None else account_response([HOTKEY]),
            )
    httpx_mock.add_response(url=f"{API}/v1/auth/logout", status_code=204)


def paths(httpx_mock: HTTPXMock) -> list[str]:
    return [request.url.path for request in httpx_mock.get_requests()]


def store(
    *,
    access_token: str = TOKEN,
    expires_at: datetime | None = None,
    api_base_url: str = API,
) -> Path:
    """A stored session, written the way the command would write it."""
    return session_module.save(
        session_module.Session(
            access_token=access_token,
            expires_at=expires_at or _later(),
            api_base_url=api_base_url,
            hotkey=HOTKEY,
            account_id=ACCOUNT_ID,
        )
    )


# --- register ----------------------------------------------------------------------------------
#
# The coldkey command. What is asserted here is mostly about the browser session it opens: that it
# is used for one write, that it is revoked on every path out, and that it never reaches disk.


def test_register_claims_an_account_and_attaches_the_hotkey(httpx_mock: HTTPXMock):
    from bittensor.sp_core import Keypair

    mock_register(httpx_mock)
    registered = succeed(*ALICE, "auth", "register", *BOB, "--yes")

    assert registered["account_id"] == ACCOUNT_ID
    assert registered["coldkey"] == COLDKEY
    assert registered["linked_hotkeys"] == [HOTKEY]
    assert paths(httpx_mock) == [
        "/v1/auth/wallet/challenge",
        "/v1/auth/wallet/verify",
        "/v1/me/hotkeys/challenge",
        "/v1/me/hotkeys",
        "/v1/auth/logout",
    ]

    # Two signatures over two different messages by two different keys. Getting these crossed is
    # the whole failure mode the prefixes exist to prevent, so both are checked against the key
    # that was supposed to make them.
    verify, link = httpx_mock.get_requests()[1], httpx_mock.get_requests()[3]
    assert Keypair(ss58_address=COLDKEY).verify(
        LOGIN_MESSAGE.encode("utf-8"), bytes.fromhex(json.loads(verify.content)["signature"])
    )
    assert Keypair(ss58_address=HOTKEY).verify(
        LINK_MESSAGE.encode("utf-8"), bytes.fromhex(json.loads(link.content)["signature"])
    )


def test_register_stores_no_credential_anywhere(httpx_mock: HTTPXMock):
    """The cookie is the account's most powerful credential, so it lives only in memory.

    A bearer token is written to `session.json` because a rig has to keep working between
    commands. A browser cookie has no such need -- it exists for the length of one write -- and
    a stored one would hand whoever reads the file the ability to repoint the payout, which is
    exactly what the validator's cookie-only writes are meant to prevent.
    """
    mock_register(httpx_mock)
    succeed(*ALICE, "auth", "register", *BOB, "--yes")

    assert not session_module.session_file_path().exists()
    written = list(session_module.session_file_path().parent.glob("*"))
    assert not any(CSRF in path.read_text("utf-8") for path in written if path.is_file())


def test_register_revokes_the_browser_session_it_opened(httpx_mock: HTTPXMock):
    mock_register(httpx_mock)
    succeed(*ALICE, "auth", "register", *BOB, "--yes")

    logout = httpx_mock.get_requests()[-1]
    assert logout.url.path == "/v1/auth/logout"
    # Revoking is a write, so it carries the same session-bound CSRF token the writes did. A
    # logout that failed the CSRF check would leave the session live and say nothing about it.
    assert logout.headers["X-Conjectures-CSRF"] == CSRF


def test_the_browser_session_is_revoked_even_when_linking_fails(httpx_mock: HTTPXMock):
    """The path that matters. A failure must not be how a live cookie session gets left behind."""
    mock_register(
        httpx_mock,
        link_status=409,
        link_json={"reason_code": "HOTKEY_ALREADY_LINKED", "detail": "already linked"},
    )
    error = refusal(*ALICE, "auth", "register", *BOB, "--yes")

    assert getattr(error, "reason_code", None) == "HOTKEY_ALREADY_LINKED"
    assert paths(httpx_mock)[-1] == "/v1/auth/logout"
    assert "one owner" in str(getattr(error, "hint", ""))


def test_registering_twice_changes_nothing(httpx_mock: HTTPXMock):
    """Safe to leave in a setup script: the second run costs a signature and links nothing."""
    mock_register(httpx_mock, already_linked=True)
    registered = succeed(*ALICE, "auth", "register", *BOB, "--yes")

    assert registered["linked_hotkeys"] == [HOTKEY]
    assert "/v1/me/hotkeys" not in paths(httpx_mock)
    assert paths(httpx_mock)[-1] == "/v1/auth/logout"


def test_the_writes_carry_the_csrf_token_the_sign_in_handed_out(httpx_mock: HTTPXMock):
    """A cookie is ambient, so the validator binds a second token to the row and demands it.

    The CLI is not a browser and has no cross-site exposure, but the check is the validator's and
    it applies to the credential rather than to the client -- so a client that cannot echo the
    token cannot make these writes at all.
    """
    mock_register(httpx_mock)
    succeed(*ALICE, "auth", "register", *BOB, "--yes")

    for request in httpx_mock.get_requests()[2:]:
        assert request.headers.get("X-Conjectures-CSRF") == CSRF, request.url.path
        assert "conjectures_session=a-browser-session" in request.headers["cookie"]


def test_a_deposit_claim_is_refused_before_the_coldkey_is_unlocked(
    httpx_mock: HTTPXMock, monkeypatch: pytest.MonkeyPatch
):
    """The coldkey's signing-oracle test, and the highest-stakes one in this file.

    The same key signs `conjectures-deposit-claim-v1`. A validator that can make this tool sign
    whatever it sends could serve a claim message under the guise of a login and collect a
    signature that claims a transfer. The refusal must land before the passphrase prompt.
    """

    def explode(*_args: object, **_kwargs: object):
        raise AssertionError("the coldkey was unlocked to sign a message of another kind")

    monkeypatch.setattr("conjectures_miner.commands.auth.load_coldkey", explode)
    httpx_mock.add_response(
        url=f"{API}/v1/auth/wallet/challenge",
        json=challenge_response(
            "conjectures-deposit-claim-v1\n"
            "domain: conjectures.io\n"
            f"address: {COLDKEY}\n"
            "extrinsic: 8769916-13\n"
        ),
    )

    error = refusal(*ALICE, "auth", "register", *BOB, "--yes")
    assert "not a coldkey login challenge" in str(error)


def test_a_session_challenge_cannot_be_served_as_a_link_challenge(httpx_mock: HTTPXMock):
    """The second guard, on the message the *hotkey* signs while the browser session is open.

    A session signature is a durable credential; a link signature is an ownership claim. Neither
    is the other, and the account this command just claimed is not a reason to sign bytes without
    reading them.
    """
    mock_register(httpx_mock, link_message=CHALLENGE_MESSAGE, expect_link=False)
    error = refusal(*ALICE, "auth", "register", *BOB, "--yes")

    assert "not a hotkey-link challenge" in str(error)
    assert "/v1/me/hotkeys" not in paths(httpx_mock)
    # Refused, and still cleaned up: the guard runs inside the block the revoke protects.
    assert paths(httpx_mock)[-1] == "/v1/auth/logout"


def test_a_login_challenge_for_another_coldkey_is_refused(
    httpx_mock: HTTPXMock, monkeypatch: pytest.MonkeyPatch
):
    def explode(*_args: object, **_kwargs: object):
        raise AssertionError("the coldkey was unlocked for a challenge naming another address")

    monkeypatch.setattr("conjectures_miner.commands.auth.load_coldkey", explode)
    httpx_mock.add_response(
        url=f"{API}/v1/auth/wallet/challenge",
        json=challenge_response(
            LOGIN_MESSAGE.replace(f"address: {COLDKEY}", f"address: {HOTKEY}")
        ),
    )

    error = refusal(*ALICE, "auth", "register", *BOB, "--yes")
    assert HOTKEY in str(error)


def test_register_refuses_when_the_two_keys_are_the_same(httpx_mock: HTTPXMock):
    """An account whose coldkey is its own hotkey is not a thing anyone means to create.

    The validator would accept it -- both signatures are over their own prefixes and both verify
    -- so this has to be refused here, and before any request is made.
    """
    error = refusal(*ALICE, "auth", "register", "--coldkey-uri", "//Alice", "--yes")
    assert getattr(error, "exit_code", None) == 2
    assert "same key" in str(error)
    assert not httpx_mock.get_requests()


def test_dev_signature_cannot_register(httpx_mock: HTTPXMock):
    """The marker is a constant and can never verify, so it is refused before anything is sent."""
    httpx_mock.add_response(
        url=f"{API}/v1/auth/wallet/challenge",
        json=challenge_response(LOGIN_MESSAGE, nonce="a-login-nonce"),
    )
    error = refusal("--dev-signature", *ALICE, "auth", "register", *BOB, "--yes")

    assert getattr(error, "exit_code", None) == 2
    assert "cannot register" in str(error)
    # The challenge was fetched -- it costs nothing and needs no key -- but nothing was signed
    # and no session was ever opened.
    assert paths(httpx_mock) == ["/v1/auth/wallet/challenge"]


def test_register_shows_the_coldkey_message_before_signing_it(httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url=f"{API}/v1/auth/wallet/challenge",
        json=challenge_response(LOGIN_MESSAGE, nonce="a-login-nonce"),
    )
    result = invoke(*ALICE, "auth", "register", *BOB)  # no --yes: the prompt declines on EOF

    assert result.exit_code != 0
    assert "conjectures-login-v1" in result.stderr
    # Declined at the prompt, so nothing beyond the challenge was ever sent.
    assert paths(httpx_mock) == ["/v1/auth/wallet/challenge"]


# --- login -------------------------------------------------------------------------------------


def test_login_stores_a_token_only_its_owner_can_read(httpx_mock: HTTPXMock):
    mock_login(httpx_mock)
    signed_in = succeed(*ALICE, "auth", "login", "--yes")

    assert signed_in["account_id"] == ACCOUNT_ID
    path = session_module.session_file_path()
    assert path.is_file()
    # The whole point of the file: a token readable by other users on the host is a leaked token.
    assert path.stat().st_mode & 0o777 == 0o600

    stored = session_module.load()
    assert stored is not None
    assert stored.access_token == TOKEN
    assert stored.api_base_url == API
    assert stored.hotkey == HOTKEY


def test_login_signs_the_servers_message_byte_for_byte(httpx_mock: HTTPXMock):
    from bittensor.sp_core import Keypair

    mock_login(httpx_mock)
    succeed(*ALICE, "auth", "login", "--yes")

    verify = httpx_mock.get_requests()[-1]
    assert verify.url.path == "/v1/auth/cli/verify"
    body = json.loads(verify.content)
    # The nonce names which challenge is being answered; the message itself is never re-sent,
    # because the validator verifies against the copy it stored and a client-supplied message
    # would invite it to verify against something the client chose.
    assert set(body) == {"address", "nonce", "signature"}
    assert body["address"] == HOTKEY
    assert body["nonce"] == challenge_response()["nonce"]
    assert "message" not in body
    assert Keypair(ss58_address=HOTKEY).verify(
        CHALLENGE_MESSAGE.encode("utf-8"), bytes.fromhex(body["signature"])
    )


def test_a_hotkey_link_challenge_is_refused_before_the_key_is_unlocked(
    httpx_mock: HTTPXMock, monkeypatch: pytest.MonkeyPatch
):
    """The signing-oracle test, and the most important one in this file.

    A validator that can make this tool sign arbitrary bytes can make it sign
    `conjectures-hotkey-link-v1` -- and that signature attaches this hotkey to whichever account
    asked for the challenge. A typo'd `--api` or a poisoned environment variable is enough. The
    refusal has to happen before the key is unlocked, so nothing is ever signed at all.
    """

    def explode(*_args: object, **_kwargs: object):
        raise AssertionError("the hotkey was unlocked to sign a message of another kind")

    monkeypatch.setattr("conjectures_miner.context.load_signer", explode)
    # Only the challenge is mocked: reaching verify at all would be the bug.
    httpx_mock.add_response(
        url=f"{API}/v1/auth/cli/challenge",
        json=challenge_response(
            "conjectures-hotkey-link-v1\n"
            "domain: conjectures.io\n"
            f"address: {HOTKEY}\n"
            "nonce: not-a-session-nonce\n"
            "expires: 2026-08-12T12:00:00Z\n"
        ),
    )

    error = refusal(*ALICE, "auth", "login", "--yes")
    assert "not a CLI session challenge" in str(error)
    assert not session_module.session_file_path().exists()


def test_a_challenge_for_another_hotkey_is_refused(
    httpx_mock: HTTPXMock, monkeypatch: pytest.MonkeyPatch
):
    def explode(*_args: object, **_kwargs: object):
        raise AssertionError("the hotkey was unlocked for a challenge naming another address")

    monkeypatch.setattr("conjectures_miner.context.load_signer", explode)
    other = "5FHneW46xGXgs5mUiveU4sbTyGBzmstUspZC92UhjJM694ty"
    httpx_mock.add_response(
        url=f"{API}/v1/auth/cli/challenge",
        json=challenge_response(
            CHALLENGE_MESSAGE.replace(f"address: {HOTKEY}", f"address: {other}")
        ),
    )

    error = refusal(*ALICE, "auth", "login", "--yes")
    assert other in str(error)
    assert not session_module.session_file_path().exists()


def test_a_challenge_naming_another_deployment_is_refused():
    """A hostile host must not relay the real validator's challenge and collect the signature.

    Asserted directly on the guard rather than through the CLI: reaching it that way needs a
    remote `--api`, and `--uri` refuses a remote validator before the challenge is ever fetched.
    """
    with pytest.raises(ConfigError) as caught:
        assert_session_challenge(
            CHALLENGE_MESSAGE,
            address=HOTKEY,
            api_root="https://not-the-validator.example",
        )
    assert "conjectures.io" in str(caught.value)


def test_a_local_validator_may_keep_the_default_login_domain():
    """A development validator serves on localhost while keeping LOGIN_DOMAIN=conjectures.io.

    Enforcing the domain there would refuse every local sign-in, so the check is relaxed for a
    local host and only for one. Asserted so the relaxation cannot be removed by accident, and
    so its boundary stays visible next to the test above.
    """
    assert_session_challenge(CHALLENGE_MESSAGE, address=HOTKEY, api_root="http://localhost:8000")


def test_the_prefix_must_be_the_whole_first_line():
    """`startswith` would admit a longer prefix that merely begins the same way."""
    with pytest.raises(ConfigError):
        assert_session_challenge(
            CHALLENGE_MESSAGE.replace(
                "conjectures-cli-session-v1", "conjectures-cli-session-v1-evil", 1
            ),
            address=HOTKEY,
            api_root=API,
        )


def test_an_appended_address_line_cannot_override_the_checked_one():
    """First-wins parsing, so a second `address:` cannot smuggle a different answer past."""
    with pytest.raises(ConfigError):
        assert_session_challenge(
            CHALLENGE_MESSAGE.replace(f"address: {HOTKEY}", "address: 5Fake") + "\n"
            f"address: {HOTKEY}\n",
            address=HOTKEY,
            api_root=API,
        )


def test_login_does_not_unlock_the_key_when_the_challenge_is_refused(
    httpx_mock: HTTPXMock, monkeypatch: pytest.MonkeyPatch
):
    """A refused challenge must cost no passphrase prompt."""

    def explode(*_args: object, **_kwargs: object):
        raise AssertionError("the hotkey was unlocked before there was anything to sign")

    monkeypatch.setattr("conjectures_miner.context.load_signer", explode)
    httpx_mock.add_response(
        url=f"{API}/v1/auth/cli/challenge",
        status_code=429,
        headers={"Retry-After": "120"},
        json={"reason_code": "TOO_MANY_CHALLENGES", "detail": "too many challenges"},
    )

    error = refusal(*ALICE, "auth", "login", "--yes")
    assert getattr(error, "reason_code", None) == "TOO_MANY_CHALLENGES"
    assert not session_module.session_file_path().exists()


def test_too_many_challenges_reports_retry_after(httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url=f"{API}/v1/auth/cli/challenge",
        status_code=429,
        headers={"Retry-After": "120"},
        json={"reason_code": "TOO_MANY_CHALLENGES", "detail": "too many challenges"},
    )
    error = refusal(*ALICE, "auth", "login", "--yes")
    assert getattr(error, "retry_after", None) == 120.0
    assert getattr(error, "exit_code", None) == 3
    assert "Retry-After" in str(getattr(error, "hint", ""))


def test_an_unlinked_hotkey_is_told_to_register(httpx_mock: HTTPXMock):
    """The common first-run error, and the only one whose fix is a different command."""
    httpx_mock.add_response(url=f"{API}/v1/auth/cli/challenge", json=challenge_response())
    httpx_mock.add_response(
        url=f"{API}/v1/auth/cli/verify",
        status_code=403,
        json={"reason_code": "HOTKEY_NOT_LINKED", "detail": "hotkey is not linked"},
    )
    error = refusal(*ALICE, "auth", "login", "--yes")
    assert getattr(error, "exit_code", None) == 3
    hint = str(getattr(error, "hint", ""))
    assert "conjectures auth register" in hint
    assert "coldkey" in hint
    assert not session_module.session_file_path().exists()


def test_dev_signature_cannot_sign_in(httpx_mock: HTTPXMock):
    error = refusal("--dev-signature", *ALICE, "auth", "login", "--yes")
    assert getattr(error, "exit_code", None) == 2
    assert "cannot sign in" in str(error)
    # Nothing was sent: the marker is refused locally, so no challenge is spent finding out.
    assert not httpx_mock.get_requests()
    assert not session_module.session_file_path().exists()


def test_the_development_marker_can_never_be_offered_as_a_signature():
    """The second layer: `challenge_signature` refuses the marker even if a caller gets there."""
    from conjectures_miner.errors import ConfigError
    from conjectures_miner.signing import DevelopmentSigner, challenge_signature

    with pytest.raises(ConfigError):
        challenge_signature(DevelopmentSigner(HOTKEY), CHALLENGE_MESSAGE)


def test_a_challenge_expiry_is_shown_before_anything_is_signed(httpx_mock: HTTPXMock):
    mock_login(httpx_mock)
    result = invoke(*ALICE, "auth", "login", "--yes")
    assert result.exit_code == 0
    # The message goes to stderr so `--output json` stays one document, and it is printed before
    # the key is touched.
    assert "conjectures-cli-session-v1" in result.stderr


def test_login_twice_replaces_the_token(httpx_mock: HTTPXMock):
    mock_login(httpx_mock)
    succeed(*ALICE, "auth", "login", "--yes")

    httpx_mock.add_response(url=f"{API}/v1/auth/cli/challenge", json=challenge_response())
    httpx_mock.add_response(
        url=f"{API}/v1/auth/cli/verify", json=session_response("a-second-token")
    )
    succeed(*ALICE, "auth", "login", "--yes")

    stored = session_module.load()
    assert stored is not None
    assert stored.access_token == "a-second-token"
    assert session_module.session_file_path().stat().st_mode & 0o777 == 0o600


# --- printing the token deliberately -----------------------------------------------------------


def test_login_prints_the_token_when_asked(httpx_mock: HTTPXMock):
    mock_login(httpx_mock)
    signed_in = succeed(*ALICE, "auth", "login", "--yes", "--show-token")

    assert signed_in["access_token"] == TOKEN
    # In the *same* document as the rest of the result, not a second one after it.
    assert signed_in["account_id"] == ACCOUNT_ID


def test_login_output_is_one_json_document(httpx_mock: HTTPXMock):
    """`--output json | jq` breaks the moment a command prints twice.

    `Renderer.data` appends to stdout, so a second call produces two concatenated objects, which
    is not JSON. Asserted for both forms of the command, because the token is the thing most
    likely to get bolted on as an afterthought.
    """
    mock_login(httpx_mock)
    plain = invoke(*ALICE, "auth", "login", "--yes")
    assert json.loads(plain.stdout)

    httpx_mock.add_response(url=f"{API}/v1/auth/cli/challenge", json=challenge_response())
    httpx_mock.add_response(url=f"{API}/v1/auth/cli/verify", json=session_response())
    with_token = invoke(*ALICE, "auth", "login", "--yes", "--show-token")
    assert json.loads(with_token.stdout)


def test_login_does_not_print_the_account_email_or_payout_with_the_token(httpx_mock: HTTPXMock):
    """The token is one disclosure; dumping the response model would be three.

    `CliSession` carries the account, and the account carries an email address and the payout
    keys. Asking to see a credential is not asking to put those on a terminal.
    """
    mock_login(httpx_mock)
    result = invoke(*ALICE, "auth", "login", "--yes", "--show-token")

    assert TOKEN in result.stdout
    assert "miner@example.com" not in result.stdout
    assert "payout" not in result.stdout


def test_auth_token_prints_the_token_bare_for_piping():
    """The output *is* the token, so `$(...)` and `| xclip` need no unquoting."""
    store()
    result = invoke("auth", "token")

    assert result.exit_code == 0
    assert result.stdout == f"{TOKEN}\n"
    # The caveat goes to stderr, so it never reaches the pipe.
    assert "like a password" in result.stderr


def test_auth_token_refuses_a_token_it_would_be_wrong_to_hand_out():
    """Expired, or minted elsewhere. Printing a credential for another deployment invites it to
    be pasted into a request to this one."""
    store(expires_at=datetime.now(UTC) - timedelta(minutes=1))
    expired = refusal("auth", "token")
    assert getattr(expired, "exit_code", None) == 2
    assert "expired" in str(expired)

    store(api_base_url="http://somewhere-else:9000")
    foreign = refusal("auth", "token")
    assert getattr(foreign, "exit_code", None) == 2
    assert "somewhere-else:9000" in str(foreign)


def test_auth_token_says_so_when_there_is_nothing_stored():
    error = refusal("auth", "token")
    assert getattr(error, "exit_code", None) == 2
    assert "not signed in" in str(error)


# --- the token's blast radius ------------------------------------------------------------------


def test_a_token_minted_elsewhere_is_never_sent(httpx_mock: HTTPXMock):
    store(api_base_url="http://somewhere-else:9000")
    error = refusal("submissions", "mine")
    assert getattr(error, "exit_code", None) == 2
    assert "somewhere-else:9000" in str(error)
    assert not httpx_mock.get_requests()


def test_an_expired_token_is_refused_locally(httpx_mock: HTTPXMock):
    store(expires_at=datetime.now(UTC) - timedelta(minutes=1))
    error = refusal("submissions", "mine")
    assert getattr(error, "exit_code", None) == 2
    assert "expired" in str(error)
    assert not httpx_mock.get_requests()


def test_not_signed_in_is_a_local_refusal(httpx_mock: HTTPXMock):
    error = refusal("submissions", "mine")
    assert getattr(error, "exit_code", None) == 2
    assert "not signed in" in str(error)
    assert not httpx_mock.get_requests()


def test_own_submissions_carry_only_a_bearer_header(httpx_mock: HTTPXMock):
    store()
    httpx_mock.add_response(url=f"{API}/v1/me/submissions?limit=25", json=submission_page())
    rows = succeed("submissions", "mine")

    assert len(rows) == 1
    assert rows[0]["verification_status"] == "VERIFIED"

    request = httpx_mock.get_request()
    assert request is not None
    assert request.headers["Authorization"] == f"Bearer {TOKEN}"
    # The submit and read schemes are untouched by this one; none of their headers belong here.
    assert not [name for name in request.headers if name.lower().startswith("x-conjectures-")]


def test_a_next_cursor_is_offered_as_the_command_to_run(httpx_mock: HTTPXMock):
    store()
    httpx_mock.add_response(
        url=f"{API}/v1/me/submissions?limit=25", json=submission_page(next_cursor="opaque.cursor")
    )
    result = invoke("submissions", "mine")
    assert result.exit_code == 0
    assert "--cursor opaque.cursor" in result.stderr


def test_a_revoked_session_says_to_log_in_again(httpx_mock: HTTPXMock):
    store()
    httpx_mock.add_response(
        url=f"{API}/v1/me/submissions?limit=25",
        status_code=401,
        json={"reason_code": "NOT_AUTHENTICATED", "detail": "session is not valid"},
    )
    error = refusal("submissions", "mine")
    assert getattr(error, "reason_code", None) == "NOT_AUTHENTICATED"
    assert "conjectures auth login" in str(getattr(error, "hint", ""))


# --- status ------------------------------------------------------------------------------------


def test_status_confirms_a_live_session_against_the_validator(httpx_mock: HTTPXMock):
    store()
    httpx_mock.add_response(url=f"{API}/v1/me", json=account_response())
    reported = succeed("auth", "status")

    assert reported["live"] is True
    assert reported["display_name"] == "a miner"
    assert reported["hotkey"] == HOTKEY
    request = httpx_mock.get_request()
    assert request is not None
    assert request.headers["Authorization"] == f"Bearer {TOKEN}"


def test_status_can_report_without_asking_the_validator(httpx_mock: HTTPXMock):
    store()
    reported = succeed("auth", "status", "--local")
    assert reported["live"] is None
    assert not httpx_mock.get_requests()


def test_status_exits_two_when_not_signed_in(httpx_mock: HTTPXMock):
    error = refusal("auth", "status")
    assert getattr(error, "exit_code", None) == 2
    assert not httpx_mock.get_requests()


def test_status_exits_two_when_the_validator_has_revoked_the_session(httpx_mock: HTTPXMock):
    """A script gating on `auth status` wants one code for every not-signed-in state."""
    store()
    httpx_mock.add_response(
        url=f"{API}/v1/me",
        status_code=401,
        json={"reason_code": "NOT_AUTHENTICATED", "detail": "session is not valid"},
    )
    error = refusal("auth", "status")
    assert getattr(error, "exit_code", None) == 2
    assert "expired or was revoked" in str(error)


# --- logout ------------------------------------------------------------------------------------


def test_logout_clears_the_token_on_204(httpx_mock: HTTPXMock):
    store()
    httpx_mock.add_response(url=f"{API}/v1/auth/logout", status_code=204)
    reported = succeed("auth", "logout")

    assert reported["revoked"] is True
    assert not session_module.session_file_path().exists()
    request = httpx_mock.get_request()
    assert request is not None
    assert request.headers["Authorization"] == f"Bearer {TOKEN}"


def test_logout_clears_the_token_on_401(httpx_mock: HTTPXMock):
    """A token the validator has already revoked is nothing worth keeping."""
    store()
    httpx_mock.add_response(
        url=f"{API}/v1/auth/logout",
        status_code=401,
        json={"reason_code": "NOT_AUTHENTICATED", "detail": "session is not valid"},
    )
    reported = succeed("auth", "logout")
    assert reported["revoked"] is False
    assert not session_module.session_file_path().exists()


def test_logout_keeps_the_token_when_the_validator_is_unreachable(httpx_mock: HTTPXMock):
    """Forgetting it locally would leave a live token nobody can revoke."""
    store()
    httpx_mock.add_exception(httpx.ConnectError("connection refused"))
    error = refusal("auth", "logout")

    assert getattr(error, "exit_code", None) == 4
    assert "the local token was kept" in str(error)
    assert session_module.session_file_path().is_file()


def test_logout_clears_a_token_it_cannot_send(httpx_mock: HTTPXMock):
    store(api_base_url="http://somewhere-else:9000")
    reported = succeed("auth", "logout")
    assert reported["revoked"] is False
    assert reported["cleared_locally"] is True
    assert not session_module.session_file_path().exists()
    assert not httpx_mock.get_requests()


def test_logout_without_a_session_is_a_local_refusal(httpx_mock: HTTPXMock):
    error = refusal("auth", "logout")
    assert getattr(error, "exit_code", None) == 2
    assert not httpx_mock.get_requests()


# --- the token never reaches stdout ------------------------------------------------------------


def test_the_token_never_reaches_the_output_unless_it_is_asked_for(httpx_mock: HTTPXMock):
    """None of these commands print the credential. `--show-token` and `auth token` do, on ask."""
    mock_login(httpx_mock)
    httpx_mock.add_response(url=f"{API}/v1/me", json=account_response())
    httpx_mock.add_response(url=f"{API}/v1/me/submissions?limit=25", json=submission_page())

    printed = [
        invoke(*ALICE, "auth", "login", "--yes"),
        invoke("auth", "status"),
        invoke("auth", "status", "--local"),
        invoke("submissions", "mine"),
        invoke("config", "show"),
        invoke("config", "show", "--resolved"),
    ]
    for result in printed:
        assert result.exit_code == 0, result.stdout
        assert TOKEN not in result.stdout
        assert TOKEN not in result.stderr

    # And it is not a setting, so it cannot arrive through the config file or the environment.
    from conjectures_miner.settings import Settings

    assert not [name for name in Settings.model_fields if "token" in name]


def test_the_config_file_never_holds_the_token(isolated_home: Path, httpx_mock: HTTPXMock):
    mock_login(httpx_mock)
    succeed(*ALICE, "auth", "login", "--yes")

    config = isolated_home / "config.toml"
    assert not config.is_file() or TOKEN not in config.read_text("utf-8")
    # It lives beside the config, in its own file, so one override moves both.
    assert session_module.session_file_path().parent == config.parent


# --- the untouched paths -----------------------------------------------------------------------


def test_a_corrupt_session_file_is_no_session_at_all(httpx_mock: HTTPXMock):
    path = session_module.session_file_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")

    assert session_module.load() is None
    error = refusal("auth", "status")
    assert getattr(error, "exit_code", None) == 2
    assert not httpx_mock.get_requests()
