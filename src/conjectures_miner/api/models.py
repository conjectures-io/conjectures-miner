"""Response models, narrowed to the fields a miner uses.

`extra="ignore"`: the validator may add fields, and a new one must never break an installed CLI.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

TERMINAL_VERIFICATION = frozenset({"VERIFIED", "REJECTED"})


class Model(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)


class TaskSummary(Model):
    task_id: str
    task_bundle_sha256: str
    target_type_sha256s: tuple[str, ...] = ()


class TaskList(Model):
    repository_commit: str
    bundle_format: str
    max_bundle_bytes: int
    submission_price_rao: int
    payment_recipient: str
    tasks: tuple[TaskSummary, ...]


class MachineContract(Model):
    task_id: str
    task_bundle_sha256: str
    target_theorem: str


class ConjectureTask(Model):
    """One attack direction on a conjecture: what a bundle names, and the Lean it is checked by.

    `challenge_lean` is the exact `Challenge.lean` whose bytes are hashed into the published
    `task_bundle_sha256`, so it can be checked against the commitment rather than trusted.
    """

    task_id: str
    task_mode: str
    task_bundle_sha256: str
    challenge_lean: str
    machine_contract: MachineContract


class Conjecture(Model):
    """A conjecture and every task issued against it, one per attack direction.

    The statement belongs to the conjecture; the Lean belongs to a task. A caller that started
    from a task id therefore has to pick its own back out of `tasks`.
    """

    slug: str
    title: str
    statement: str
    tasks: tuple[ConjectureTask, ...]
    repository_commit: str

    def task(self, task_id: str) -> ConjectureTask | None:
        return next((task for task in self.tasks if task.task_id == task_id), None)


class QueueDepths(Model):
    awaiting_verification: int
    awaiting_review: int
    awaiting_reward: int


class PinRotationWindow(Model):
    weekday: int
    starts_at: datetime
    ends_at: datetime
    in_progress: bool
    drained: bool


class SystemStatus(Model):
    status: str
    submissions_open: bool
    repository_commit: str
    queue_depths: QueueDepths
    pin_rotation: PinRotationWindow
    banner: str | None = None


class PreflightResult(Model):
    """`ok`, and on refusal a reason code with a line and column when the failure has a location."""

    ok: bool
    reason_code: str | None = None
    detail: str | None = None
    line: int | None = None
    column: int | None = None
    proof_sha256: str | None = None
    proof_bytes: int | None = None


class PaymentRecord(Model):
    reference: str
    sender: str
    amount_rao: int
    block: int


class BountyQuote(Model):
    amount_rao: int
    policy_version: str


class VerificationStatus(Model):
    status: str
    attempt: int | None = None
    accepted: bool | None = None
    reason_code: str | None = None
    stage: str | None = None
    sandbox_mode: str | None = None
    report_available: bool = False
    started_at: datetime | None = None
    finished_at: datetime | None = None


class SubmissionStatus(Model):
    """Four independent axes, exactly as the validator stores them. None of them implies another."""

    submission_id: uuid.UUID
    hotkey: str
    task_id: str
    task_bundle_sha256: str
    proof_sha256: str
    request_digest: str
    verification_status: str
    manual_review_status: str
    reward_status: str
    failure_reason: str | None = None
    manual_review_required: bool
    review_policy_version: str
    payment: PaymentRecord
    bounty: BountyQuote
    verification: VerificationStatus | None = None
    created_at: datetime
    updated_at: datetime

    @property
    def settled(self) -> bool:
        return self.verification_status in TERMINAL_VERIFICATION


class Report(Model):
    submission_id: uuid.UUID
    report_sha256: str
    report: dict[str, Any]


# --- The website account, reached with a session token rather than a per-request signature -------


class CliChallenge(Model):
    """A nonce and the exact message to sign.

    `message` is what gets signed, verbatim. `nonce` is carried for display and diagnosis only --
    rebuilding the message from it is precisely the mistake that makes a signature meaningless.
    """

    nonce: str
    message: str
    expires_at: datetime


class LinkedHotkey(Model):
    hotkey: str
    linked_at: datetime


class LinkedWallet(Model):
    coldkey: str
    linked_at: datetime


class PayoutDestination(Model):
    coldkey: str | None = None
    hotkey: str | None = None


class Account(Model):
    """Who the session belongs to. An account may hold several hotkeys and several coldkeys."""

    id: uuid.UUID
    email: str | None = None
    email_verified: bool = False
    display_name: str | None = None
    roles: tuple[str, ...] = ()
    # Null until set on the website. A reward cannot be paid without it.
    payout: PayoutDestination | None = None
    hotkeys: tuple[LinkedHotkey, ...] = ()
    wallets: tuple[LinkedWallet, ...] = ()
    created_at: datetime


class SessionEnvelope(Model):
    """What a *browser* sign-in returns: the account, and nothing else.

    The credential is not in the body -- it arrives as two `Set-Cookie` headers, one of them
    HttpOnly. That asymmetry with `CliSession` is the point of the two session kinds, and it is
    why `auth register` keeps its cookie in memory and revokes it on the way out.
    """

    account: Account


class CliSession(Model):
    """What a successful sign-in returns.

    Holds the token, so it is never handed to the renderer whole: `auth login` builds its own dict
    of the fields that are safe to show, and adds the token to it only under `--show-token`.
    Dumping the model would print the account's email and payout keys alongside the credential,
    which is three disclosures for the price of one. See `session.py` for where the token goes.
    """

    access_token: str
    token_type: str
    expires_at: datetime
    # The one hotkey this token may act as. An account may own several; a token speaks for the key
    # that minted it, and the validator refuses it for any of the others.
    hotkey_scope: str
    account: Account


class SubmissionSummary(Model):
    """One of the account's own submissions, as it appears in a list.

    Flatter than `SubmissionStatus` and keyed `id` rather than `submission_id`: a different endpoint
    with a different shape, not the same object abbreviated.
    """

    id: uuid.UUID
    hotkey: str
    task_id: str
    proof_sha256: str
    verification_status: str
    manual_review_status: str
    reward_status: str
    failure_reason: str | None = None
    # Nullable for the same reason the nested quote is: another proof may already have solved the
    # target, which leaves the estimate undefined rather than zero.
    bounty_amount_rao: int | None = None
    bounty_policy_version: str | None = None
    bounty_locked: bool = False
    created_at: datetime
    updated_at: datetime


class SubmissionPage(Model):
    """One page of a keyset-paginated feed. `next_cursor` is null at the end."""

    items: tuple[SubmissionSummary, ...] = ()
    next_cursor: str | None = None
