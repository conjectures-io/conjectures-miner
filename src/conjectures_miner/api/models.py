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
