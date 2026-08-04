"""`build`, `check`, `submit` -- the write path, in the order a miner walks it.

    build   write the archive and the plan       local, offline, no key unlock
    check   would the validator take this?       free, no auth, no key unlock
    pay     the submission price                 outside this tool, for now
    submit  spend the payment                    signed, irreversible

Kept in one module because the three share the plan, and because seeing them together is
what makes the ordering obvious.

`build` produces `submission.zip` plus `submission.json`. The archive is built once and
never rebuilt: `check` and `submit` take the plan, resolve the archive it points at, verify
it still hashes to what was built, and send those exact bytes. So what `check` approved is
literally what `submit` sends, and no code has to reproduce a byte-identical zip after the
fact.

After that, nothing about the task is ever typed again -- identity comes from the archive's
own `manifest.json`, the copy the validator parses and cross-checks. The only thing a plan
cannot supply is the payment reference, and once a later `conjectures pay` writes that into
the plan, `conjectures submit` will need no arguments at all.

Why a stale task cache is not a hazard on this path: the validator resolves `task_id` plus
`task_bundle_sha256` at `submissions.py:247` and refuses with `TASK_NOT_ALLOWED` long before
it confirms the payment at `:299` ("Payment last, and before any write: the schema has no
unpaid state"). No row reaches `submissions`, and the unique constraint on
`payment_reference` lives on that table alone -- a refusal only lands in
`api_rejection_log`, whose index on the column is not unique. So a digest that rotated
between sync and submit costs one rebuild with the same payment reference, not the payment.
`check` surfaces it earlier still, for free.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from conjectures_miner.cache import complete_task_id
from conjectures_miner.plan import DEFAULT_PLAN_NAME

DEFAULT_BUNDLE = Path("submission.zip")
DEFAULT_PLAN = Path(DEFAULT_PLAN_NAME)


def build(
    ctx: typer.Context,
    proof: Annotated[Path, typer.Option(help="The candidate Main.lean.")],
    task: Annotated[
        str,
        typer.Option(
            help="Task id, or a unique prefix of one.",
            autocompletion=complete_task_id,
        ),
    ],
    output: Annotated[
        Path, typer.Option(help="Where to write the archive.")
    ] = DEFAULT_BUNDLE,
    plan: Annotated[
        Path, typer.Option(help="Where to write the submission plan.")
    ] = DEFAULT_PLAN,
    refresh: Annotated[
        bool, typer.Option("--refresh", help="Sync the task cache first.")
    ] = False,
    task_sha256: Annotated[
        str | None,
        typer.Option(help="Override the task digest instead of using the cached one."),
    ] = None,
    hotkey_ss58: Annotated[
        str | None, typer.Option(help="Override the manifest hotkey address.")
    ] = None,
    solver_name: Annotated[str | None, typer.Option()] = None,
    solver_version: Annotated[str | None, typer.Option()] = None,
) -> None:
    """Write the archive and the plan that points at it.

    Offline. `--task` resolves against the local cache, so a short prefix is enough and an
    ambiguous one is refused with the candidates listed; the cached `task_bundle_sha256`
    goes into the manifest. `--refresh` syncs first for anyone who would rather not find out
    at `check` time, and `--task-sha256` overrides the digest outright.

    Two artifacts, and the split is deliberate. `submission.zip` is the bytes that will be
    sent, sealed here and never regenerated. `submission.json` records where it is, what it
    hashes to, a readable copy of its manifest, and an empty payment slot. Everything
    afterwards works from the plan.

    Validates the task id, digest and hotkey formats locally before writing anything -- a
    47-character address is accepted by the API and then refused at insert, so it is caught
    here instead.

    Needs the hotkey's address for the manifest, never its private key.
    """


def check(
    ctx: typer.Context,
    plan: Annotated[
        Path, typer.Option(help="The submission plan to test.")
    ] = DEFAULT_PLAN,
    bundle: Annotated[
        Path | None,
        typer.Option(help="Test a bare archive instead, with no plan."),
    ] = None,
) -> None:
    """Ask preflight whether this submission would be accepted. Free, no credit, no auth.

    Resolves the archive through `plan.load_verified`, so a plan pointing at a missing,
    truncated or replaced archive fails here rather than at submit time. `--bundle` skips
    the plan for an archive built by some other tool; an archive is self-describing, so
    nothing else is needed either way.

    Task references and hotkey come from the archive's manifest. There is nothing to re-type
    and nothing to get inconsistent.

    This is also where a rotated task pool shows up: preflight resolves the task, so a
    digest that moved since the last sync is reported here rather than after a payment.
    Re-run `conjectures tasks sync`, rebuild, and check again.

    A refusal is a successful call: `ok: false` plus a reason code, and a line and column
    when the failure has a location in the proof. Exits non-zero on refusal even so, so that
    `conjectures check && conjectures submit` gates correctly.

    Passing here is not a guarantee, but everything statically checkable has been checked by
    the validator itself. This is the loop to iterate in: it is the last step before money
    moves.
    """


def submit(
    ctx: typer.Context,
    plan: Annotated[
        Path, typer.Option(help="The submission plan to send.")
    ] = DEFAULT_PLAN,
    bundle: Annotated[
        Path | None,
        typer.Option(help="Send a bare archive instead; requires --payment-ref."),
    ] = None,
    payment_ref: Annotated[
        str | None,
        typer.Option(help="The payment. Required only if the plan has none."),
    ] = None,
    idempotency_key: Annotated[
        str | None,
        typer.Option(help="Reuse a previous key to retry the same submission safely."),
    ] = None,
    yes: Annotated[
        bool, typer.Option("--yes", help="Skip the confirmation prompt.")
    ] = False,
) -> None:
    """Sign and send the submission. This spends the payment.

    Nothing is re-typed. The archive, its digest, the task references, the proof digest and
    the hotkey all come from the plan and the archive it vouches for. `--payment-ref` is
    needed only until a plan carries one; when both are present the flag wins and the
    difference is reported, because overriding a recorded payment silently is how the wrong
    extrinsic gets cited.

    Order matters, and it is deliberate:

    1. `plan.load_verified` -- resolve the archive, confirm it still hashes to what `build`
       sealed, and confirm its manifest agrees with the plan's readable copy;
    2. report anything still missing, all of it at once, rather than one field per attempt;
    3. refuse if the manifest's `miner_hotkey` is not the address about to sign, which the
       validator would refuse anyway but only after a round trip;
    4. resolve or mint the idempotency key and **persist it**, before anything is sent;
    5. show what is about to be spent and confirm, unless `--yes`;
    6. sign the canonical request digest with the hotkey;
    7. stream the archive.

    No local check of whether the task digest is still current: the validator does that at
    intake, before it confirms the payment, so a stale one is a cheap refusal rather than
    something worth a pre-flight of its own.

    On a transport failure the outcome is unknown, not failed. The stored key is what makes
    the retry safe, and `conjectures submissions show` is how to find out which it was. That
    same key with a corrected archive is also the answer to a `TASK_NOT_ALLOWED`: the
    payment reference was never consumed.
    """
