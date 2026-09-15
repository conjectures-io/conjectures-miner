"""`pay` -- send the submission price to the treasury, and record what it bought.

The step that used to sit outside this tool. It is its own command and not a flag on `submit`,
because only one of the two moves money irreversibly: a `submit` that could also pay would make
every submission failure look like a lost transfer, and would invite a retry that pays twice.

    conjectures pay                      quote -> check -> confirm -> send -> record
    conjectures pay reference --extrinsic 8769916-13    resolve a transfer already made

Everything checkable is checked *before* the extrinsic is signed, because afterwards nothing can
be undone:

1. the treasury address and the price come from the validator itself, never from local config;
2. the wallet's coldkey must match the submission archive before a transfer is sent.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from conjectures_miner import chain as chain_module
from conjectures_miner import plan as plan_module
from conjectures_miner.commands import context
from conjectures_miner.context import AppContext
from conjectures_miner.errors import CliError
from conjectures_miner.plan import DEFAULT_PLAN

app = typer.Typer(no_args_is_help=True, help="Pay the submission price, and resolve a payment.")

RAO_PER_TAO = 1_000_000_000


def _quote(app_ctx: AppContext) -> tuple[str, int]:
    """Where to pay and how much, as the validator publishes it.

    Read live rather than from the task cache: a cached treasury address is money sent somewhere
    with no way back, and this is the one command where a stale value cannot be corrected later.
    """
    tasks = app_ctx.client.list_tasks()
    if not tasks.payment_recipient or tasks.submission_price_rao <= 0:
        raise CliError(
            "the validator publishes no payment address or price",
            hint="It may not be configured for paid submissions. Check `conjectures status`.",
        )
    return tasks.payment_recipient, tasks.submission_price_rao


def pay(
    ctx: typer.Context,
    plan: Annotated[
        Path, typer.Option(help="The plan to record the payment against.")
    ] = DEFAULT_PLAN,
    yes: Annotated[bool, typer.Option("--yes", help="Skip the confirmation prompt.")] = False,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Run every check and print what would be sent.")
    ] = False,
) -> None:
    """Send the submission price to the validator's treasury. This spends real TAO."""
    # `pay` is a group with a callback, so this runs for `pay reference` too. Paying is what the
    # bare command means; a named sub-command means something else entirely.
    if ctx.invoked_subcommand is not None:
        return

    app_ctx = context(ctx)

    # The plan first, before the network and before anything is signed. Paying for a submission
    # that cannot be sent is a mistake this catches for free, and a missing plan should not cost
    # a round trip to find out about. The plan is also where the reference has to land after.
    existing = plan_module.read(plan)
    if existing.payment.reference is not None:
        raise CliError(
            f"{plan} already cites payment {existing.payment.reference}",
            hint="Send it with `conjectures submit`, or pay against a different plan with --plan.",
        )
    loaded = plan_module.load(plan, None)
    expected_coldkey = loaded.archive.miner_coldkey
    if expected_coldkey is None:
        raise CliError("this archive is for a browser session; rebuild with --coldkey-ss58")

    recipient, price_rao = _quote(app_ctx)

    with chain_module.Chain(app_ctx.settings.bittensor_network) as chain:
        wallet = chain_module.load_coldkey_wallet(app_ctx.settings)
        coldkey = str(wallet.coldkeypub.ss58_address)
        _assert_sender(coldkey, expected_coldkey)

        app_ctx.render.preview(
            {
                "from_coldkey": coldkey,
                "submission_coldkey": expected_coldkey,
                "to_treasury": recipient,
                "amount_rao": price_rao,
                "amount_tao": price_rao / RAO_PER_TAO,
                "network": app_ctx.settings.bittensor_network,
                "plan": str(plan),
            },
            title="about to transfer",
        )
        if dry_run:
            app_ctx.render.note("[yellow]--dry-run: nothing was sent.[/]")
            return
        if not yes and not app_ctx.render.confirm("Send this transfer? It cannot be undone."):
            raise typer.Abort

        extrinsic_id = chain.transfer(wallet=wallet, recipient=recipient, amount_rao=price_rao)
        app_ctx.render.note(f"sent as {extrinsic_id}; waiting for finality")

        position = chain_module.parse_position(extrinsic_id)
        if position is None:  # pragma: no cover - the SDK returns `block-index`
            raise chain_module.ChainError(f"unusable extrinsic id: {extrinsic_id!r}")
        transfer = chain.await_finality(position)

    _assert_matches(transfer, recipient=recipient, price_rao=price_rao)
    _assert_sender(transfer.sender, expected_coldkey)
    plan_module.record_payment(
        plan, reference=transfer.reference, price_rao=price_rao, recipient=recipient
    )

    app_ctx.render.data(
        {
            "payment_reference": transfer.reference,
            "block": transfer.block,
            "sender": transfer.sender,
            "recipient": transfer.recipient,
            "amount_rao": transfer.amount_rao,
            "plan": str(plan),
        },
        title="paid",
    )
    app_ctx.render.note("Recorded on the plan. Next: `conjectures submit`.")


def reference(
    ctx: typer.Context,
    extrinsic: Annotated[str, typer.Option(help="block-extrinsic, as a block explorer shows it.")],
    plan: Annotated[
        Path | None, typer.Option(help="Record the resolved reference against this plan.")
    ] = None,
) -> None:
    """Resolve a transfer already made to the reference a submission needs. Reads only."""
    app_ctx = context(ctx)
    position = chain_module.parse_position(extrinsic)
    if position is None:
        raise CliError(
            f"{extrinsic!r} is not a chain position",
            hint="Expected `block-extrinsic` or `block-extrinsic-event`, e.g. 8769916-13.",
        )

    with chain_module.Chain(app_ctx.settings.bittensor_network) as chain:
        transfer = chain.finalized_transfer(position)
    if transfer is None:
        raise CliError(
            f"no finalized transfer at {position}",
            hint="It may not have finalized yet, or that extrinsic moved no TAO.",
        )

    if plan is not None:
        loaded = plan_module.load(plan, None)
        _assert_sender(transfer.sender, loaded.archive.miner_coldkey)
        recipient, price_rao = _quote(app_ctx)
        _assert_matches(transfer, recipient=recipient, price_rao=price_rao)
        plan_module.record_payment(
            plan, reference=transfer.reference, price_rao=price_rao, recipient=recipient
        )

    app_ctx.render.data(
        {
            "payment_reference": transfer.reference,
            "block": transfer.block,
            "sender": transfer.sender,
            "recipient": transfer.recipient,
            "amount_rao": transfer.amount_rao,
            "plan": str(plan) if plan else None,
        },
        title="resolved",
    )


def _assert_sender(sender: str, expected_coldkey: str | None) -> None:
    """The payment sender must be the coldkey that will sign the submission."""
    if sender != expected_coldkey:
        raise CliError(
            f"payment sender {sender} does not match the archive's coldkey {expected_coldkey}",
            hint="Use the wallet that built the archive, or rebuild with its coldkey address.",
        )


def _assert_matches(transfer: chain_module.Transfer, *, recipient: str, price_rao: int) -> None:
    """Check what the chain observed, not what was requested.

    Both of these would otherwise surface at submit time as `PAYMENT_NOT_FINALIZED`, which
    covers four different causes and names none of them.
    """
    if transfer.recipient != recipient:
        raise CliError(
            f"the transfer at {transfer.reference} went to {transfer.recipient}, not to the "
            f"validator's treasury {recipient}"
        )
    if transfer.amount_rao != price_rao:
        raise CliError(
            f"the transfer at {transfer.reference} moved {transfer.amount_rao} rao; the "
            f"submission price is exactly {price_rao} rao",
            hint="The validator compares the amount exactly.",
        )


app.command("reference")(reference)
