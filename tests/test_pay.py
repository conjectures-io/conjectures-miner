"""`conjectures pay` end to end, against a mocked validator and a fake chain.

The chain is faked at `chain.Chain`, so everything above it -- the quote, the ownership check,
the confirmation, the finality wait, and writing the reference onto the plan -- is exercised
exactly as a miner would drive it. Nothing here opens a socket or a wallet.

Every refusal below is one that, without this command, a miner would discover only *after* the
transfer was irreversibly on chain.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, ClassVar

import pytest
from pytest_httpx import HTTPXMock
from typer.testing import CliRunner

from conjectures_miner import chain as chain_module
from conjectures_miner import plan as plan_module
from conjectures_miner.cli import app
from tests.conftest import API, HOTKEY, task_list_response

runner = CliRunner()
JSON = ["--output", "json"]

COLDKEY = "5HMqFHmvUpzuAjEnse3hzMKS5LsFL428hffCfenF2smuGNhs"
STRANGER = "5EZotmLfrufXYvD6CCGsRRELEFdg9SnjaEzTmaemiBPNofBP"
PRICE = 500_000_000
# conftest's task list names HOTKEY as the payment_recipient, so that is the treasury here.
TREASURY = HOTKEY
EXTRINSIC_ID = "8769916-13"
REFERENCE = "8769916-13-0"


class FakeChain:
    """Stands in for `chain.Chain`. Records what it was asked to send."""

    instances: ClassVar[list[FakeChain]] = []

    def __init__(self, network: str) -> None:
        self.network = network
        self.owner: str | None = COLDKEY
        self.sent: dict[str, Any] | None = None
        self.transfer_result = chain_module.Transfer(
            block=8769916,
            extrinsic=13,
            event=0,
            sender=COLDKEY,
            recipient=TREASURY,
            amount_rao=PRICE,
        )
        self.finalized: chain_module.Transfer | None = self.transfer_result
        FakeChain.instances.append(self)

    def __enter__(self) -> FakeChain:
        return self

    def __exit__(self, *_exc: object) -> None:
        return None

    def coldkey_of(self, hotkey: str) -> str | None:
        del hotkey
        return self.owner

    def transfer(self, *, wallet: Any, recipient: str, amount_rao: int) -> str:
        del wallet
        self.sent = {"recipient": recipient, "amount_rao": amount_rao}
        return EXTRINSIC_ID

    def await_finality(self, position: chain_module.Position) -> chain_module.Transfer:
        del position
        return self.transfer_result

    def finalized_transfer(self, position: chain_module.Position) -> chain_module.Transfer | None:
        del position
        return self.finalized


class FakeWallet:
    coldkeypub = type("Pub", (), {"ss58_address": COLDKEY})()


@pytest.fixture(autouse=True)
def fake_chain(monkeypatch: pytest.MonkeyPatch):
    FakeChain.instances.clear()
    monkeypatch.setattr("conjectures_miner.commands.pay.chain_module.Chain", FakeChain)
    monkeypatch.setattr(
        "conjectures_miner.commands.pay.chain_module.load_coldkey_wallet",
        lambda _settings: FakeWallet(),
    )
    return FakeChain


def quote(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(url=f"{API}/v1/tasks", json=task_list_response())


def invoke(*args: str) -> Any:
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


# --- the happy path ----------------------------------------------------------------------------


def test_paying_records_the_canonical_reference_on_the_plan(
    httpx_mock: HTTPXMock, built: tuple[Path, Path]
):
    """The whole point: after `pay`, `submit` needs no --payment-ref."""
    _, plan_path = built
    quote(httpx_mock)

    body = succeed("pay", "--plan", str(plan_path), "--yes")

    assert body["payment_reference"] == REFERENCE
    assert body["amount_rao"] == PRICE
    assert plan_module.read(plan_path).payment.reference == REFERENCE


def test_the_treasury_and_price_come_from_the_validator(
    httpx_mock: HTTPXMock, built: tuple[Path, Path]
):
    """Never from local config: a stale treasury address is money with no way back."""
    _, plan_path = built
    quote(httpx_mock)

    succeed("pay", "--plan", str(plan_path), "--yes")

    assert FakeChain.instances[0].sent == {"recipient": TREASURY, "amount_rao": PRICE}


def test_the_plan_records_what_was_quoted(httpx_mock: HTTPXMock, built: tuple[Path, Path]):
    _, plan_path = built
    quote(httpx_mock)

    succeed("pay", "--plan", str(plan_path), "--yes")

    payment = plan_module.read(plan_path).payment
    assert payment.price_rao_seen == PRICE
    assert payment.recipient_seen == TREASURY


def test_a_dry_run_checks_everything_and_sends_nothing(
    httpx_mock: HTTPXMock, built: tuple[Path, Path]
):
    _, plan_path = built
    quote(httpx_mock)

    result = invoke("pay", "--plan", str(plan_path), "--dry-run")

    assert result.exit_code == 0
    assert FakeChain.instances[0].sent is None
    assert plan_module.read(plan_path).payment.reference is None


# --- refusals that would otherwise cost a transfer ------------------------------------------------


def test_paying_for_a_hotkey_the_coldkey_does_not_own_is_refused(
    httpx_mock: HTTPXMock, built: tuple[Path, Path]
):
    """The validator refuses this at submit time -- after the money has moved."""
    _, plan_path = built
    quote(httpx_mock)
    FakeChain.instances.clear()

    def owned_by_stranger(network: str) -> FakeChain:
        chain = FakeChain(network)
        chain.owner = STRANGER
        return chain

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr("conjectures_miner.commands.pay.chain_module.Chain", owned_by_stranger)
        error = refusal("pay", "--plan", str(plan_path), "--yes")

    assert "is owned by" in str(error)
    assert plan_module.read(plan_path).payment.reference is None


def test_paying_for_an_unregistered_hotkey_is_refused(
    httpx_mock: HTTPXMock, built: tuple[Path, Path]
):
    """An unregistered hotkey reads back as the zero account, which `coldkey_of` maps to None.
    A different fix from "owned by someone else", so a different message."""
    _, plan_path = built
    quote(httpx_mock)
    FakeChain.instances.clear()

    def unregistered(network: str) -> FakeChain:
        chain = FakeChain(network)
        chain.owner = None
        return chain

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr("conjectures_miner.commands.pay.chain_module.Chain", unregistered)
        error = refusal("pay", "--plan", str(plan_path), "--yes")

    assert "not registered on chain" in str(error)


def test_paying_twice_against_one_plan_is_refused(httpx_mock: HTTPXMock, built: tuple[Path, Path]):
    """The plan's reference is the only local record of money that moved. A second transfer
    against it would strand the first."""
    _, plan_path = built
    quote(httpx_mock)
    succeed("pay", "--plan", str(plan_path), "--yes")

    # No second quote is registered: the refusal happens before the network is touched, which is
    # the ordering `pay` documents and the reason a missing plan costs no round trip.
    error = refusal("pay", "--plan", str(plan_path), "--yes")

    assert "already cites payment" in str(error)
    assert plan_module.read(plan_path).payment.reference == REFERENCE


def test_paying_without_a_plan_is_refused(tmp_path: Path):
    """And without reaching the validator: no mock is registered here on purpose."""
    error = refusal("pay", "--plan", str(tmp_path / "nope.json"), "--yes")

    assert "no submission plan" in str(error)


def test_a_validator_with_no_payment_configuration_is_refused(
    httpx_mock: HTTPXMock, built: tuple[Path, Path]
):
    _, plan_path = built
    response = task_list_response()
    response["payment_recipient"] = ""
    httpx_mock.add_response(url=f"{API}/v1/tasks", json=response)

    error = refusal("pay", "--plan", str(plan_path), "--yes")

    assert "no payment address" in str(error)


# --- resolving a transfer already made ---------------------------------------------------


def test_reference_resolves_a_two_part_extrinsic_id():
    """The recovery path: the transfer finalized but the reference was never printed."""
    body = succeed("pay", "reference", "--extrinsic", EXTRINSIC_ID)

    assert body["payment_reference"] == REFERENCE
    assert body["recipient"] == TREASURY


def test_reference_can_record_onto_a_plan(httpx_mock: HTTPXMock, built: tuple[Path, Path]):
    _, plan_path = built
    quote(httpx_mock)

    succeed("pay", "reference", "--extrinsic", EXTRINSIC_ID, "--plan", str(plan_path))

    assert plan_module.read(plan_path).payment.reference == REFERENCE


def test_reference_refuses_an_extrinsic_hash():
    """A hash is an indexer's service; the validator can only resolve a position."""
    error = refusal("pay", "reference", "--extrinsic", "0x" + "ab" * 32)

    assert "is not a chain position" in str(error)


def test_reference_reports_a_transfer_that_is_not_there():
    FakeChain.instances.clear()

    def empty(network: str) -> FakeChain:
        chain = FakeChain(network)
        chain.finalized = None
        return chain

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr("conjectures_miner.commands.pay.chain_module.Chain", empty)
        error = refusal("pay", "reference", "--extrinsic", EXTRINSIC_ID)

    assert "no finalized transfer" in str(error)
