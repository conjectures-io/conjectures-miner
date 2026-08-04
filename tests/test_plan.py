"""The gate `check` and `submit` share: an archive is usable only if its plan still vouches."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from tests.conftest import HOTKEY, PROOF, TASK_DIGEST, TASK_ID

from conjectures_miner import bundle, plan


def test_load_returns_the_archive_a_plan_points_at(built: tuple[Path, Path]):
    archive_path, plan_path = built
    loaded = plan.load(plan_path, None)
    assert loaded.archive.raw == archive_path.read_bytes()
    assert loaded.archive.task_id == TASK_ID
    assert loaded.plan is not None
    assert loaded.payment_reference is None


def test_a_replaced_archive_is_refused(built: tuple[Path, Path]):
    archive_path, plan_path = built
    other = bundle.build_manifest(
        task_id=TASK_ID,
        task_bundle_sha256=TASK_DIGEST,
        hotkey_ss58=HOTKEY,
        proof=PROOF + b"-- changed\n",
    )
    bundle.write(archive_path, manifest=other, proof=PROOF + b"-- changed\n")
    with pytest.raises(plan.PlanError, match="is not the archive"):
        plan.load(plan_path, None)


def test_a_truncated_archive_is_refused_as_the_wrong_archive_not_as_a_zip_error(
    built: tuple[Path, Path],
):
    archive_path, plan_path = built
    archive_path.write_bytes(archive_path.read_bytes()[:-10])
    with pytest.raises(plan.PlanError, match="is not the archive"):
        plan.load(plan_path, None)


def test_a_missing_archive_names_the_path_it_resolved(built: tuple[Path, Path]):
    archive_path, plan_path = built
    archive_path.unlink()
    with pytest.raises(plan.PlanError, match=str(archive_path)):
        plan.load(plan_path, None)


def test_the_plan_and_the_manifest_must_agree(built: tuple[Path, Path]):
    _, plan_path = built
    payload = json.loads(plan_path.read_text())
    payload["manifest"]["task_id"] = "some-other-task"
    plan_path.write_text(json.dumps(payload))
    with pytest.raises(plan.PlanError, match="disagree on task_id"):
        plan.load(plan_path, None)


def test_the_bundle_path_is_resolved_against_the_plan_not_the_working_directory(
    built: tuple[Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    _, plan_path = built
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    assert plan.load(plan_path, None).archive.task_id == TASK_ID


def test_a_bare_archive_needs_no_plan(built: tuple[Path, Path]):
    archive_path, _ = built
    loaded = plan.load(None, archive_path)
    assert loaded.plan is None
    assert loaded.archive.miner_hotkey == HOTKEY


def test_a_missing_plan_says_what_to_run(tmp_path: Path):
    with pytest.raises(plan.PlanError) as raised:
        plan.load(tmp_path / "nope.json", None)
    assert "conjectures build" in (raised.value.hint or "")


def test_something_that_is_not_a_plan_is_refused(tmp_path: Path):
    # An unzipped manifest, which is what makes the two file names worth keeping apart.
    path = tmp_path / "submission.json"
    path.write_text(json.dumps({"schema_version": 1, "format": bundle.BUNDLE_FORMAT}))
    with pytest.raises(plan.PlanError, match="is not a"):
        plan.load(path, None)


def test_missing_for_submit_reports_the_payment(built: tuple[Path, Path]):
    _, plan_path = built
    assert plan.missing_for_submit(plan.read(plan_path)) == ["payment reference (--payment-ref)"]
