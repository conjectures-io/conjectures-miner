"""Shared fixtures. Every test runs against a throwaway config, cache and state directory."""

from __future__ import annotations

import json
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import pytest

from conjectures_miner import bundle, plan, settings

API = "http://localhost:8000"
# Alice, so `--uri //Alice` produces a signer whose address matches the manifest.
HOTKEY = "5GrwvaEF5zXb26Fz9rcQpDWS57CtERHpNehXCPcNoHGKutQY"
TASK_ID = "fc-379fc029-erdos89-erdos-89-c956ed476a-formalized-v1"
TASK_DIGEST = "sha256:" + "a" * 64
PROOF = b"theorem target : True := trivial\n"


@pytest.fixture(autouse=True)
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv(settings.CONFIG_FILE_ENV, str(tmp_path / "config.toml"))
    monkeypatch.setenv("CONJECTURES_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("CONJECTURES_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("CONJECTURES_API_BASE_URL", API)
    return tmp_path


@pytest.fixture
def proof_file(tmp_path: Path) -> Path:
    path = tmp_path / "Main.lean"
    path.write_bytes(PROOF)
    return path


@pytest.fixture
def built(tmp_path: Path) -> tuple[Path, Path]:
    """An archive and the plan that vouches for it, without going through the CLI."""
    manifest = bundle.build_manifest(
        task_id=TASK_ID, task_bundle_sha256=TASK_DIGEST, hotkey_ss58=HOTKEY, proof=PROOF
    )
    archive_path = tmp_path / "submission.zip"
    archive = bundle.write(archive_path, manifest=manifest, proof=PROOF)
    plan_path = plan.write(
        tmp_path / plan.DEFAULT_PLAN_NAME,
        plan.SubmissionPlan(
            bundle=plan.BundleRef(
                path=archive_path.name, sha256=archive.sha256, bytes=len(archive.raw)
            ),
            manifest=manifest,
            provenance=plan.Provenance(
                built_at=datetime(2026, 1, 1, tzinfo=UTC),
                tool_version="test",
                task_requested=TASK_ID,
                api_base_url=API,
                proof_source="Main.lean",
            ),
        ),
    )
    return archive_path, plan_path


def task_list_response(commit: str = "379fc029", tasks: list[str] | None = None) -> dict:
    return {
        "repository_commit": commit,
        "bundle_format": bundle.BUNDLE_FORMAT,
        "max_bundle_bytes": bundle.MAX_BUNDLE_BYTES,
        "submission_price_rao": 500_000_000,
        "payment_recipient": HOTKEY,
        "tasks": [
            {"task_id": task_id, "task_bundle_sha256": TASK_DIGEST, "target_type_sha256s": []}
            for task_id in (tasks or [TASK_ID])
        ],
    }


def read_manifest(archive_path: Path) -> dict:
    with zipfile.ZipFile(archive_path) as archive:
        return json.loads(archive.read(bundle.MANIFEST_NAME))
