"""Response models for the endpoints the MVP calls.

Mirrors the validator's `submission_api/schemas*.py`, narrowed to the fields a miner uses.
`extra="ignore"` throughout: the validator may add fields, and a new one should never break
an installed CLI.

Only the eight MVP endpoints are modelled. The public catalog and results endpoints
(`/v1/catalog/*`, `/v1/results/*`) are unauthenticated and worth adding later; they are not
on the submit path.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class Model(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)


class TaskSummary(Model):
    """One allowlisted task: what to commit to in a bundle."""


class TaskList(Model):
    """The allowlist, plus what a submission costs and who to pay.

    Carries `repository_commit`, `bundle_format`, `max_bundle_bytes`,
    `submission_price_rao`, `payment_recipient`.
    """


class SystemStatus(Model):
    """`submissions_open` is the one a client must respect before charging a miner."""


class PreflightResult(Model):
    """`ok`, and on refusal a `reason_code` with an optional `line`/`column`."""


class SubmissionStatus(Model):
    """Payment, verification, review, and reward state for one submission."""


class Report(Model):
    """The immutable verifier report."""
