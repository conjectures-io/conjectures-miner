"""Local state. Small, but the part whose loss costs real money.

The idempotency key is the only safe way to retry a submission: reusing it returns the original
outcome, while a fresh key on a retry risks `DUPLICATE_PROOF` or `DUPLICATE_PAYMENT` with the
payment already consumed. So it is written down before the request goes out, not after.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, ValidationError


class SubmissionRecord(BaseModel):
    """One attempt, as remembered locally. Not authoritative -- the validator is."""

    model_config = ConfigDict(extra="ignore")

    idempotency_key: str
    hotkey: str
    task_id: str
    task_bundle_sha256: str
    proof_sha256: str
    payment_reference: str
    created_at: datetime
    submission_id: str | None = None


class StateStore:
    """A directory of JSON records, one per attempt, readable and hand-editable by a miner."""

    def __init__(self, root: Path) -> None:
        self._root = root

    def _path(self, idempotency_key: str) -> Path:
        return self._root / f"{idempotency_key}.json"

    def reserve(
        self,
        *,
        hotkey: str,
        task_id: str,
        task_bundle_sha256: str,
        proof_sha256: str,
        payment_reference: str,
        idempotency_key: str | None = None,
    ) -> SubmissionRecord:
        """The remembered attempt for this proof and payment, persisted before anything is sent."""
        existing = self.find(proof_sha256=proof_sha256, payment_reference=payment_reference)
        if existing is not None and (
            idempotency_key is None or existing.idempotency_key == idempotency_key
        ):
            return existing
        record = SubmissionRecord(
            idempotency_key=idempotency_key or str(uuid.uuid4()),
            hotkey=hotkey,
            task_id=task_id,
            task_bundle_sha256=task_bundle_sha256,
            proof_sha256=proof_sha256,
            payment_reference=payment_reference,
            created_at=datetime.now(UTC),
        )
        self.save(record)
        return record

    def save(self, record: SubmissionRecord) -> Path:
        self._root.mkdir(parents=True, exist_ok=True)
        path = self._path(record.idempotency_key)
        path.write_text(record.model_dump_json(indent=2), encoding="utf-8")
        return path

    def find(self, *, proof_sha256: str, payment_reference: str) -> SubmissionRecord | None:
        return next(
            (
                record
                for record in self.all()
                if record.proof_sha256 == proof_sha256
                and record.payment_reference == payment_reference
            ),
            None,
        )

    def all(self) -> list[SubmissionRecord]:
        """Every remembered attempt, newest first."""
        records = []
        for path in sorted(self._root.glob("*.json")) if self._root.is_dir() else []:
            try:
                records.append(SubmissionRecord.model_validate(json.loads(path.read_text("utf-8"))))
            except (OSError, json.JSONDecodeError, ValidationError):
                continue
        return sorted(records, key=lambda record: record.created_at, reverse=True)
