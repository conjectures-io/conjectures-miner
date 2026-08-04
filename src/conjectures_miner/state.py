"""Local state. Small, but the part whose loss costs real money.

The idempotency key is the only safe way to retry a submission: reusing it returns the
original outcome, while a fresh key on a retry risks `DUPLICATE_PROOF` or
`DUPLICATE_PAYMENT` with the payment already consumed. The reference client
(`scripts/submit_proof.py` in the validator repo) prints the key and forgets it. This one
writes it down before the request goes out.

Keyed by proof digest, so a retry of the same proof finds the same key even if the miner
re-runs the command from another directory.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class SubmissionRecord:
    """One attempt, as remembered locally. Not authoritative -- the validator is."""

    idempotency_key: str
    proof_sha256: str
    task_id: str
    task_bundle_sha256: str
    payment_reference: str
    hotkey: str
    submitted_at: str
    submission_id: str | None


class StateStore:
    """A directory of records under the platform state directory.

    Plain JSON files rather than a database: a miner must be able to read, back up, and
    hand-edit this after something goes wrong.
    """

    def __init__(self, root: Path) -> None: ...

    def key_for(self, *, proof_sha256: str, payment_reference: str) -> str:
        """Return the existing idempotency key for this attempt, or mint and persist one.

        Persisted *before* the submission is sent. A key that only exists after a successful
        response is useless for the case it exists to serve.
        """
        raise NotImplementedError

    def record(self, entry: SubmissionRecord) -> None: ...

    def find(self, *, proof_sha256: str) -> SubmissionRecord | None: ...

    def all(self) -> list[SubmissionRecord]:
        """Every remembered attempt, newest first. Backs a future `conjectures history`."""
        raise NotImplementedError
