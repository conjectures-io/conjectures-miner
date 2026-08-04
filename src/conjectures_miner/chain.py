"""Reading Subtensor, and making the one transfer that buys a submission.

The only module that spends money, and the only one that opens a coldkey. Everything else in
this tool is either free or signed by the hotkey.

Four decisions here are correctness properties rather than style, and each mirrors what the
validator does when it confirms the payment (`conjectures_subnet/transfers.py`). Getting one
wrong costs a real transfer, because the validator's check happens *after* the money has moved.

**Events, not extrinsics.** A transfer is read from the `Balances.Transfer` events in
`System.Events`. An extrinsic list says what was *attempted* -- a transfer that ran out of funds
is in there and moved nothing -- and it misses transfers made from inside another call, which is
every `utility.batch` and `proxy.proxy`. The event is emitted only when the balance actually
moved.

**Finality is the boundary.** Every read is against a block at or below the finalized head,
taken from the `blocks(finalized=True)` subscription rather than `block()`, which answers with
the best block and can be reorganised out from under a payment.

**The reference is a position, and the canonical form has three parts.** The SDK hands back
`block-extrinsic`, which is what a block explorer shows. `submissions.payment_reference` is
unique over `block-extrinsic-event`, and the validator stores what *its* reader resolved -- so
resolving it here is what makes the reference a miner is given the one the validator will agree
with. A `utility.batch` emitting two transfers under one extrinsic is exactly the case a
two-part reference cannot name.

**An unregistered hotkey has no owner.** `SubtensorModule.Owner` answers with the all-zero
account for a hotkey nobody registered, rather than with nothing. Treating that as an owner
would mean every unregistered hotkey appears to belong to an address no one holds the key to.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from conjectures_miner.errors import CliError

TRANSFER_EVENT = ("Balances", "Transfer")
SYSTEM_EVENTS = ("System", "Events")
HOTKEY_OWNER = ("SubtensorModule", "Owner")

# Bittensor uses the generic substrate SS58 format. Pinned as a literal rather than imported:
# the constant has moved between bittensor major versions, and its value has not.
SS58_FORMAT = 42

# The all-zero AccountId, which `SubtensorModule.Owner` returns for an unregistered hotkey.
ZERO_ACCOUNT = "5C4hrfjw9DjXZTzV3MwzrrAr9P1MJhSrvWGWqi1eSuyUpnhM"

REFERENCE = re.compile(r"^(\d{1,12})-(\d{1,6})(?:-(\d{1,6}))?$")

# Substrate finality on Subtensor lands a handful of blocks behind the head. Generous by roughly
# an order of magnitude: the transfer is already made by the time this waits, and giving up early
# leaves the miner holding money with no reference for it.
FINALITY_ATTEMPTS = 40
FINALITY_INTERVAL_SECONDS = 6.0


class ChainError(CliError):
    """The chain could not be read, or refused the transfer."""

    exit_code = 4


@dataclass(frozen=True, slots=True)
class Position:
    """A parsed `block-extrinsic[-event]`."""

    block: int
    extrinsic: int
    event: int | None = None

    def __str__(self) -> str:
        tail = "" if self.event is None else f"-{self.event}"
        return f"{self.block}-{self.extrinsic}{tail}"


@dataclass(frozen=True, slots=True)
class Transfer:
    """One `Balances.Transfer` event, and the canonical identity of it."""

    block: int
    extrinsic: int
    event: int
    sender: str
    recipient: str
    amount_rao: int

    @property
    def reference(self) -> str:
        """`block-extrinsic-event` -- the form the validator stores and compares."""
        return f"{self.block}-{self.extrinsic}-{self.event}"


def parse_position(raw: str) -> Position | None:
    """A payment position, or None if the string is not one."""
    matched = REFERENCE.fullmatch(raw.strip())
    if matched is None:
        return None
    event = matched.group(3)
    return Position(
        block=int(matched.group(1)),
        extrinsic=int(matched.group(2)),
        event=None if event is None else int(event),
    )


# --- decoding ------------------------------------------------------------------------------


def decode_ss58(value: Any) -> str:
    """An SS58 address from whatever the runtime decoder produced.

    bittensor 11 against Finney returns event account fields already encoded, so the string path
    is the one that runs. The tuple paths are the shapes a SCALE-decoded `AccountId32` takes,
    and which one appears depends on the node's metadata version rather than on anything here.
    """
    if isinstance(value, str):
        return value
    from bittensor.sp_core import ss58_encode

    if isinstance(value, bytes | bytearray):
        return ss58_encode(bytes(value), SS58_FORMAT)
    if isinstance(value, tuple | list):
        inner = value[0] if len(value) == 1 and isinstance(value[0], tuple | list) else value
        return ss58_encode(bytes(inner), SS58_FORMAT)
    raise ChainError(f"cannot read an SS58 address from {type(value).__name__}")


def _event_body(record: Any) -> dict[str, Any]:
    """The `(module_id, event_id, attributes)` triple, whichever envelope it arrived in.

    bittensor 11 returns each record with the fields both at the top level and nested under
    `event`; older decoders returned only the nested form. Reading through this rather than
    picking one shape means a runtime upgrade cannot make every transfer invisible.
    """
    if hasattr(record, "value_serialized"):
        record = record.value_serialized
    if not isinstance(record, dict):
        raise ChainError(f"unexpected event record: {type(record).__name__}")
    if "module_id" in record and "event_id" in record:
        return record
    nested = record.get("event")
    if isinstance(nested, dict):
        return nested
    raise ChainError("event record carries no module_id/event_id")


def _attribute(attributes: Any, key: str, index: int) -> Any:
    if isinstance(attributes, dict):
        return attributes[key]
    if isinstance(attributes, tuple | list):
        return attributes[index]
    raise ChainError(f"cannot read attribute {key!r} from {type(attributes).__name__}")


def transfers_in_block(records: list[Any], *, block: int) -> list[Transfer]:
    """Every transfer among one block's event records. Pure, so it is testable without a node."""
    found: list[Transfer] = []
    for position, record in enumerate(records):
        body = _event_body(record)
        if (body.get("module_id"), body.get("event_id")) != TRANSFER_EVENT:
            continue
        attributes = body.get("attributes")
        amount = int(_attribute(attributes, "amount", 2))
        if amount <= 0:
            # A valid extrinsic, but not a payment. Skipped rather than raised.
            continue
        outer = record if isinstance(record, dict) else {}
        index = outer.get("extrinsic_idx")
        found.append(
            Transfer(
                block=block,
                # Absent on events from a block's initialisation phase, which cannot be a user
                # transfer. The enumeration position keeps (extrinsic, event) unique regardless.
                extrinsic=int(index) if index is not None else 0,
                event=position,
                sender=decode_ss58(_attribute(attributes, "from", 0)),
                recipient=decode_ss58(_attribute(attributes, "to", 1)),
                amount_rao=amount,
            )
        )
    return found


def resolve(transfers: list[Transfer], position: Position) -> Transfer | None:
    """The transfer a position names, or None. Raises when a two-part position is ambiguous."""
    in_extrinsic = [item for item in transfers if item.extrinsic == position.extrinsic]
    if not in_extrinsic:
        return None
    if position.event is not None:
        return next((item for item in in_extrinsic if item.event == position.event), None)
    if len(in_extrinsic) > 1:
        # Picking either would be deciding which payment the miner meant. Fail closed on money.
        raise ChainError(
            f"extrinsic {position} moved TAO {len(in_extrinsic)} times, so it does not name one "
            "payment",
            hint="Name the event index too: " + ", ".join(item.reference for item in in_extrinsic),
        )
    return in_extrinsic[0]


# --- the node ------------------------------------------------------------------------------


class Chain:
    """A Subtensor connection, held open for the life of one command.

    Synchronous on the outside because the CLI is: every method runs its own event loop through
    `_run`. One connection per call would mean a websocket handshake per read, and the public
    Finney endpoints answer HTTP 429 to that.
    """

    def __init__(self, network: str) -> None:
        self.network = network
        self._client: Any = None
        self._loop: asyncio.AbstractEventLoop | None = None

    # -- lifecycle

    def close(self) -> None:
        if self._client is None:
            return
        try:
            self._run(self._client.close())
        except Exception:  # pragma: no cover - closing a broken socket is best effort
            pass
        finally:
            self._client = None
            if self._loop is not None:
                self._loop.close()
                self._loop = None

    def __enter__(self) -> Chain:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    # -- reads

    def finalized_head(self) -> int:
        """The highest block that can no longer be reorganised away."""

        async def read(client: Any) -> int:
            stream = client.blocks(finalized=True)
            try:
                header = await anext(stream)
            finally:
                # Closing the generator cancels the subscription deterministically rather than
                # at collection time, which matters on a connection being kept open.
                await stream.aclose()
            return int(header.number)

        return self._read(read)

    def transfers_at(self, block: int) -> list[Transfer]:
        async def read(client: Any) -> Any:
            return await client.query(SYSTEM_EVENTS, block=block)

        records = self._read(read)
        if records is None:
            raise ChainError(f"block {block} returned no event list")
        return transfers_in_block(list(records), block=block)

    def coldkey_of(self, hotkey: str) -> str | None:
        """The coldkey that owns a hotkey, or None if the chain knows no owner for it."""

        async def read(client: Any) -> Any:
            return await client.query(HOTKEY_OWNER, [hotkey])

        value = self._read(read)
        if value is None:
            return None
        owner = decode_ss58(value)
        return None if owner == ZERO_ACCOUNT else owner

    def finalized_transfer(self, position: Position) -> Transfer | None:
        """Resolve a position against finalized state. None if it is not finalized, or not one."""
        if position.block > self.finalized_head():
            return None
        return resolve(self.transfers_at(position.block), position)

    # -- the write

    def transfer(self, *, wallet: Any, recipient: str, amount_rao: int) -> str:
        """Sign and submit the transfer. Returns the SDK's `block-extrinsic` id.

        `keep_alive` stays on: emptying the coldkey is never what buying a submission means. The
        amount goes as integer rao, never as a float of TAO -- the validator compares it with
        `==`, and a float that rounds one rao off buys nothing while still spending the money.
        """
        import bittensor as bt

        intent = bt.Transfer(dest_ss58=recipient, amount_tao=bt.rao(amount_rao), keep_alive=True)

        async def send(client: Any) -> Any:
            return await client.execute(intent, wallet)

        try:
            result = self._read(send)
        except ChainError:
            raise
        except Exception as exc:
            raise ChainError(f"the transfer was not accepted by the chain: {exc}") from exc
        if not result.success:
            raise ChainError(
                f"the transfer failed on chain: {result.error or result.message or 'unknown'}"
            )
        if not result.extrinsic_id:
            raise ChainError(
                "the transfer succeeded but the chain returned no extrinsic id",
                hint=(
                    f"Find the transfer to {recipient} on an explorer and resolve it with "
                    "`conjectures pay reference --extrinsic <block>-<index>`."
                ),
            )
        return str(result.extrinsic_id)

    def await_finality(
        self, position: Position, *, sleep: Callable[[float], None] | None = None
    ) -> Transfer:
        """Poll until the including block is finalized, then resolve the canonical reference."""
        pause = sleep or _sleep
        for _ in range(FINALITY_ATTEMPTS):
            if self.finalized_head() >= position.block:
                found = resolve(self.transfers_at(position.block), position)
                if found is not None:
                    return found
                raise ChainError(
                    f"block {position.block} is finalized but carries no transfer at {position}",
                    hint="It may have been reorganised out. Check the coldkey balance before "
                    "paying again.",
                )
            pause(FINALITY_INTERVAL_SECONDS)
        raise ChainError(
            f"the transfer at {position} has not finalized after "
            f"{int(FINALITY_ATTEMPTS * FINALITY_INTERVAL_SECONDS)}s",
            hint=f"The money has moved. Recover the reference with "
            f"`conjectures pay reference --extrinsic {position}`.",
        )

    # -- plumbing

    def _read(self, operation: Callable[[Any], Awaitable[Any]]) -> Any:
        return self._run(self._with_client(operation))

    async def _with_client(self, operation: Callable[[Any], Awaitable[Any]]) -> Any:
        import bittensor as bt

        if self._client is None:
            # Awaiting the instance connects it on this loop and returns the async client, which
            # is the bittensor 11 contract -- `async with` would close it on the way out.
            self._client = await bt.Subtensor(self.network)
        return await operation(self._client)

    def _run(self, coroutine: Awaitable[Any]) -> Any:
        """One loop for the life of this object: the held connection is bound to it."""
        if self._loop is None:
            self._loop = asyncio.new_event_loop()
        try:
            return self._loop.run_until_complete(coroutine)
        except ChainError:
            raise
        except Exception as exc:
            raise ChainError(f"chain read failed on {self.network}: {exc}") from exc


def _sleep(seconds: float) -> None:
    import time

    time.sleep(seconds)


def load_coldkey_wallet(settings: Any) -> Any:
    """Open the wallet whose coldkey pays.

    The only place this tool touches a coldkey, and it never leaves the process: what goes on
    chain is a signed extrinsic. `signing.py` handles the hotkey, which is all every other
    command needs.
    """
    from bittensor.wallet import Wallet

    extra = {"path": str(settings.wallet_path)} if settings.wallet_path else {}
    try:
        wallet = Wallet(name=settings.wallet_name, hotkey=settings.wallet_hotkey, **extra)
        _ = wallet.coldkey.ss58_address
    except Exception as exc:
        raise CliError(
            f"could not open the coldkey of wallet {settings.wallet_name}: {exc}",
            hint="Check --wallet and --wallet-path. Paying needs the coldkey, unlike every "
            "other command.",
        ) from exc
    return wallet
