"""`verify` end to end, against a validator repository stubbed down to its interface.

The stub is what the CLI actually calls -- `check_prerequisites.py --miner --json`,
`bootstrap.sh --miner`, and a `python -m verifier` that answers `doctor` and `verify` -- plus a
`pins.lock.json`, a task pool and the `.gitignore` that keeps a built checkout looking clean.

Everything the real build and the real Lean run do in half an hour is out of scope. What is in
scope is what surrounds them: that the right things are cloned at the right commits, that a second
run resumes, that the right task directory and digest reach the verifier, and that an exit code of
1 means the proof was rejected and nothing else.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from conjectures_miner import verifier
from conjectures_miner.cache import TaskCache, TaskCacheFile
from conjectures_miner.cli import app
from tests.conftest import API, PROOF, TASK_DIGEST, TASK_ID, task_list_response

runner = CliRunner()
GIT = shutil.which("git") or "git"

PREREQUISITES = """\
import json
import os
import sys

ready = os.environ.get("STUB_PREREQUISITES_READY", "1") == "1"
print(json.dumps({"ready": ready, "checks": [
    {"name": "python", "ok": True, "detail": "3.12.3", "remedy": "", "advisory": False},
    {"name": "zstd", "ok": ready, "detail": "not on PATH", "remedy": "sudo apt install zstd",
     "advisory": False},
]}))
sys.exit(0 if ready else 1)
"""

# The verifier the build installs, standing in for `python -m verifier`. It answers `doctor` and
# `verify` differently, records the argv and the submission it was handed, and takes its exit code
# from the environment -- the things the CLI's behaviour turns on. The submission is copied out
# because it is staged in a temporary directory the CLI deletes on the way back.
BOOTSTRAP = """\
#!/usr/bin/env bash
set -euo pipefail
echo "pinning dependencies"
echo "[3/7922] Building Mathlib.Tactic.Ring"
echo "tasks root is ${CONJECTURES_TASKS_ROOT:?not set}"
mkdir -p .venv/bin
cat > .venv/bin/python <<'STUB'
#!/usr/bin/env bash
root="$(cd "$(dirname "$0")/../.." && pwd)"
mode=doctor
submission=""
previous=""
for argument in "$@"; do
  if [ "$argument" = "verify" ]; then mode=verify; fi
  if [ "$previous" = "--submission" ]; then submission="$argument"; fi
  previous="$argument"
done
if [ "$mode" = "verify" ]; then
  if [ -n "${STUB_VERIFY_ARGV:-}" ]; then
    printf '%s\\n' "$*" > "$STUB_VERIFY_ARGV"
    cp "$submission" "$STUB_VERIFY_ARGV.submission"
  fi
  code="${STUB_VERIFY_EXIT:-0}"
  if [ "$code" = "0" ]; then verdict=accepted; else verdict=rejected; fi
  cat "$root/verify-$verdict.json"
  exit "$code"
fi
if [ -n "${STUB_DOCTOR_ENV:-}" ]; then
  printf 'PATH=%s\\nELAN_HOME=%s\\n' "$PATH" "${ELAN_HOME:-}" > "$STUB_DOCTOR_ENV"
fi
cat "$root/doctor.json"
STUB
chmod +x .venv/bin/python
"""

DOCTOR: dict[str, Any] = {
    "ready": True,
    "sandbox": {"mode": "development-fake-landrun"},
    "formal_conjectures": {"actual_commit": "379fc0298dc146df549e7061c3ede0353a5bb51f"},
    "toolchain_identity": {"lean_version": "Lean (version 4.27.0)"},
    "comparator": {"missing": []},
}

VERDICT: dict[str, Any] = {
    "task_id": TASK_ID,
    "task_mode": "formalized",
    "task_bundle_sha256": TASK_DIGEST,
    "submission_sha256": "sha256:" + "c" * 64,
    "sandbox_mode": "development-fake-landrun",
    "duration_ms": 91_000,
    "stage": "COMPARE",
    "stderr_tail": "",
}
ACCEPTED = VERDICT | {"accepted": True, "reason_code": "ACCEPTED"}
REJECTED = VERDICT | {
    "accepted": False,
    "reason_code": "TARGET_TYPE_MISMATCH",
    "stderr_tail": "the solution proves True, not the stated theorem",
}

TASK_SLUG = "erdos-89"


@dataclass(frozen=True)
class Upstream:
    validator: Path
    tasks: Path
    pinned_tasks_commit: str


@pytest.fixture
def upstream(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Upstream:
    """Two local repositories the CLI can clone, wired together the way the real ones are."""
    tasks = tmp_path / "upstream" / "conjectures-tasks"
    _init(tasks)
    _write(
        tasks,
        {
            "tiers/tier-1/formal-conjectures-audit-fixes.patch": "the audited patch\n",
            # Slug-named, as the real pool is: the manifest is the only thing tying it to a task id.
            f"pool/tier-1/{TASK_SLUG}/manifest.json": json.dumps(
                {"task_id": TASK_ID, "task_mode": "formalized", "timeout_seconds": 3600}
            ),
            "allowlist.json": json.dumps(
                {
                    "allowed_task_bundles": [
                        {"task_id": TASK_ID, "task_bundle_sha256": TASK_DIGEST, "tier": "tier-1"}
                    ]
                }
            ),
        },
    )
    pinned = _commit(tasks, "audited")
    # A later commit, so checking out the tip instead of the pin is a distinguishable mistake.
    _write(tasks, {"tiers/tier-1/formal-conjectures-audit-fixes.patch": "a newer patch\n"})
    _commit(tasks, "unaudited")

    validator = tmp_path / "upstream" / "conjectures-validator"
    _init(validator)
    _write(
        validator,
        {
            ".gitignore": ".venv/\n",
            "doctor.json": json.dumps(DOCTOR),
            "verify-accepted.json": json.dumps(ACCEPTED),
            "verify-rejected.json": json.dumps(REJECTED),
            "pins.lock.json": json.dumps(
                {"tasks": {"repository": f"file://{tasks}", "commit": pinned}}
            ),
            "scripts/check_prerequisites.py": PREREQUISITES,
            "scripts/bootstrap.sh": BOOTSTRAP,
        },
        executable=("scripts/bootstrap.sh",),
    )
    _commit(validator, "validator")

    monkeypatch.setenv("CONJECTURES_VERIFIER_REPOSITORY", f"file://{validator}")
    monkeypatch.setenv("CONJECTURES_VERIFIER_REF", "main")
    return Upstream(validator=validator, tasks=tasks, pinned_tasks_commit=pinned)


def test_setup_takes_the_tasks_repository_to_the_commit_the_validator_pins(
    upstream: Upstream, isolated_home: Path
):
    """Not to its tip: pin_dependencies.sh reads the audited patch out of that exact commit."""
    built = _succeed("verify", "--setup", "--offline")

    home = isolated_home / "cache" / "verifier"
    assert _head(home / "conjectures-tasks") == upstream.pinned_tasks_commit
    assert _head(upstream.tasks) != upstream.pinned_tasks_commit
    assert (home / "conjectures-tasks/tiers/tier-1/formal-conjectures-audit-fixes.patch").read_text(
        encoding="utf-8"
    ) == "the audited patch\n"
    assert built["tasks_commit"] == upstream.pinned_tasks_commit
    assert built["validator_commit"] == _head(upstream.validator)
    assert built["ready"] is True
    assert built["sandbox_mode"] == "development-fake-landrun"


@pytest.mark.usefixtures("upstream")
def test_the_build_is_streamed_and_told_where_the_tasks_checkout_is(isolated_home: Path):
    result = _invoke("verify", "--setup", "--offline")

    assert result.exit_code == 0, result.stderr
    # Verbatim, brackets and all: passing build output through rich as markup mangles or kills it.
    assert "[3/7922] Building Mathlib.Tactic.Ring" in result.stderr
    assert f"tasks root is {isolated_home / 'cache' / 'verifier' / 'conjectures-tasks'}" in (
        result.stderr
    )


def test_the_setup_record_survives_beside_the_checkouts_it_describes(
    upstream: Upstream, isolated_home: Path
):
    _succeed("verify", "--setup", "--offline")

    record = verifier.SetupRecord.model_validate_json(
        (isolated_home / "cache" / "verifier" / "setup.json").read_text(encoding="utf-8")
    )
    assert record.ref == "main"
    assert record.validator_commit == _head(upstream.validator)
    assert record.tasks_commit == upstream.pinned_tasks_commit


def test_a_re_run_moves_to_the_new_tip_without_rebuilding(upstream: Upstream, isolated_home: Path):
    _succeed("verify", "--setup", "--offline")
    home = isolated_home / "cache" / "verifier"
    # Anything the build left behind. Surviving the second run is what "resumes" has to mean.
    (home / "conjectures-validator/.venv/expensive").write_text("6.5 GB of Mathlib")

    _write(upstream.validator, {"NOTES.md": "moved on\n"})
    moved = _commit(upstream.validator, "a later validator")
    built = _succeed("verify", "--setup", "--offline")

    assert built["validator_commit"] == moved
    assert _head(home / "conjectures-validator") == moved
    assert (home / "conjectures-validator/.venv/expensive").is_file()


def test_a_clone_interrupted_before_its_checkout_is_resumed_not_refused(
    upstream: Upstream, isolated_home: Path
):
    """Its index is empty, so every tracked file reads as deleted. That is not a modification."""
    home = isolated_home / "cache" / "verifier"
    home.mkdir(parents=True)
    _git(
        "clone",
        "--filter=blob:none",
        "--no-checkout",
        f"file://{upstream.validator}",
        str(home / "conjectures-validator"),
        cwd=home,
    )

    built = _succeed("verify", "--setup", "--offline")

    assert built["validator_commit"] == _head(upstream.validator)


@pytest.mark.usefixtures("upstream")
def test_a_modified_verifier_checkout_is_refused(isolated_home: Path):
    """A verifier someone has edited does not answer for the validator's."""
    _succeed("verify", "--setup", "--offline")
    checkout = isolated_home / "cache" / "verifier" / "conjectures-validator"
    (checkout / "pins.lock.json").write_text('{"tasks": {"repository": "", "commit": ""}}')

    error = _refusal("verify", "--setup", "--offline")

    assert getattr(error, "exit_code", None) == verifier.VerifierError.exit_code
    assert "local modifications" in str(error)


@pytest.mark.usefixtures("upstream")
def test_the_pre_clone_gate_stops_before_anything_is_downloaded(
    isolated_home: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(verifier, "REQUIRED_DISK_BYTES", 1 << 60)

    error = _refusal("verify", "--setup", "--offline")

    assert "the build needs about" in str(error)
    assert not (isolated_home / "cache" / "verifier" / "conjectures-validator").exists()


@pytest.mark.usefixtures("upstream")
def test_the_validators_own_gate_stops_before_the_build(
    isolated_home: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("STUB_PREREQUISITES_READY", "0")

    error = _refusal("verify", "--setup", "--offline")

    assert "sudo apt install zstd" in str(error)
    # Cloned, because the gate lives in the clone -- but not built.
    home = isolated_home / "cache" / "verifier"
    assert (home / "conjectures-validator/pins.lock.json").is_file()
    assert not (home / "conjectures-validator/.venv").exists()


def test_verify_without_a_setup_says_what_to_run():
    error = _refusal("verify")

    assert getattr(error, "exit_code", None) == verifier.VerifierError.exit_code
    assert "verify --setup" in str(getattr(error, "hint", ""))


@pytest.mark.usefixtures("upstream")
def test_the_checkouts_own_toolchain_leads_the_path_the_verifier_is_run_with(
    isolated_home: Path, monkeypatch: pytest.MonkeyPatch
):
    """`doctor` resolves lean and lake off PATH, so a host with no global Elan reads as unready.

    Found by the clean-host run: 54 minutes of build, every tool present, `ready: false`. Every
    development host had a global Elan that hid it.
    """
    _succeed("verify", "--setup", "--offline")
    recorded = isolated_home / "doctor-env.txt"
    monkeypatch.setenv("STUB_DOCTOR_ENV", str(recorded))

    _succeed("verify", "--status")

    elan = isolated_home / "cache/verifier/conjectures-validator/.elan"
    lines = dict(line.split("=", 1) for line in recorded.read_text().splitlines())
    assert lines["PATH"].startswith(f"{elan / 'bin'}{os.pathsep}")
    assert lines["ELAN_HOME"] == str(elan)


@pytest.mark.usefixtures("upstream")
def test_a_verifier_that_reports_unready_exits_non_zero(isolated_home: Path):
    _succeed("verify", "--setup", "--offline")
    checkout = isolated_home / "cache" / "verifier" / "conjectures-validator"
    (checkout / "doctor.json").write_text(json.dumps(DOCTOR | {"ready": False}))

    result = _invoke("verify", "--status")

    assert result.exit_code == 1
    assert json.loads(result.stdout)["ready"] is False


# --- verifying a proof -------------------------------------------------------------------------


@pytest.fixture
def ready(
    upstream: Upstream,  # noqa: ARG001 -- requested to build the setup these tests run against
    isolated_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Path:
    """A completed setup, and the argv file the stub verifier records its call in."""
    _succeed("verify", "--setup", "--offline")
    argv = isolated_home / "verify-argv.txt"
    monkeypatch.setenv("STUB_VERIFY_ARGV", str(argv))
    return argv


def test_the_verifier_is_pointed_at_the_pool_directory_for_that_task_id(
    ready: Path, isolated_home: Path, proof_file: Path
):
    """The pool is named by slug and the allowlist records no path, so this is the whole mapping."""
    _succeed("verify", "--proof", str(proof_file), "--task", TASK_ID, "--task-sha256", TASK_DIGEST)

    called = ready.read_text(encoding="utf-8")
    pool = isolated_home / "cache/verifier/conjectures-tasks/pool/tier-1" / TASK_SLUG
    assert f"--task {pool} " in called
    # The digest is passed through rather than recomputed; the verifier refuses on a mismatch.
    assert f"--expected-task-sha256 {TASK_DIGEST}" in called
    assert "--allow-insecure-development" in called


@pytest.mark.usefixtures("ready")
def test_an_accepted_proof_exits_zero_and_names_the_sandbox_it_ran_under(proof_file: Path):
    verdict = _succeed(
        "verify", "--proof", str(proof_file), "--task", TASK_ID, "--task-sha256", TASK_DIGEST
    )

    assert verdict["accepted"] is True
    # Not an attestation, and the report says which isolation produced it.
    assert verdict["sandbox_mode"] == "development-fake-landrun"
    assert verdict["duration_seconds"] == 91.0


@pytest.mark.usefixtures("ready")
def test_a_rejected_proof_exits_one(proof_file: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("STUB_VERIFY_EXIT", "1")

    result = _invoke(
        "verify", "--proof", str(proof_file), "--task", TASK_ID, "--task-sha256", TASK_DIGEST
    )

    assert result.exit_code == 1
    assert json.loads(result.stdout)["reason_code"] == "TARGET_TYPE_MISMATCH"
    assert "proves True, not the stated theorem" in result.stderr


@pytest.mark.usefixtures("ready")
def test_a_host_that_could_not_run_the_verification_does_not_exit_one(
    proof_file: Path, monkeypatch: pytest.MonkeyPatch
):
    """Exit 1 has to mean the proof was rejected, or a broken host reads as a wrong proof."""
    monkeypatch.setenv("STUB_VERIFY_EXIT", "2")

    error = _refusal(
        "verify", "--proof", str(proof_file), "--task", TASK_ID, "--task-sha256", TASK_DIGEST
    )

    assert getattr(error, "exit_code", None) == verifier.VerifierError.exit_code
    assert "TARGET_TYPE_MISMATCH" in str(error)


@pytest.mark.usefixtures("ready")
def test_running_out_of_resources_is_reported_as_the_host_not_as_a_verdict(
    isolated_home: Path, proof_file: Path, monkeypatch: pytest.MonkeyPatch
):
    """RESOURCE_LIMIT is exit 1 from the verifier; on a workstation it is RLIMIT_NPROC, per user."""
    checkout = isolated_home / "cache/verifier/conjectures-validator"
    (checkout / "verify-rejected.json").write_text(
        json.dumps(REJECTED | {"reason_code": "RESOURCE_LIMIT"}), encoding="utf-8"
    )
    monkeypatch.setenv("STUB_VERIFY_EXIT", "1")

    error = _refusal(
        "verify", "--proof", str(proof_file), "--task", TASK_ID, "--task-sha256", TASK_DIGEST
    )

    assert getattr(error, "exit_code", None) == verifier.VerifierError.exit_code
    assert "says nothing about the proof" in str(error)


def test_a_retired_task_is_refused_before_the_verifier_is_started(ready: Path, proof_file: Path):
    """Two seconds instead of an hour, and not exit 1: nothing judged this proof."""
    error = _refusal(
        "verify", "--proof", str(proof_file), "--task", "fc-gone-v1", "--task-sha256", TASK_DIGEST
    )

    assert getattr(error, "exit_code", None) == verifier.TaskNotVerifiableError.exit_code
    assert "not in the task pool" in str(error)
    assert not ready.exists()


@pytest.mark.usefixtures("ready")
def test_a_task_the_validator_still_offers_is_not_reported_as_retired(proof_file: Path):
    """The pin can drop a task the API is still serving, and re-running `--setup` cannot fix that.

    Observed against production on 2026-08-06: 292 tasks served, 280 in the pool the branch pins.
    """
    _seed_cache("fc-gone-v1")

    error = _refusal(
        "verify", "--proof", str(proof_file), "--task", "fc-gone-v1", "--task-sha256", TASK_DIGEST
    )

    assert "still offers this task" in getattr(error, "hint", "")


@pytest.mark.usefixtures("ready")
def test_a_task_the_validator_has_dropped_too_is_reported_as_retired(proof_file: Path):
    _seed_cache(TASK_ID)

    error = _refusal(
        "verify", "--proof", str(proof_file), "--task", "fc-gone-v1", "--task-sha256", TASK_DIGEST
    )

    assert "has been retired" in getattr(error, "hint", "")


def test_a_task_named_without_a_digest_is_resolved_through_the_cache(
    ready: Path, isolated_home: Path, proof_file: Path
):
    """The path a miner actually takes: a short name, and the digest coming from `tasks sync`."""
    _seed_cache(TASK_ID)

    _succeed("verify", "--proof", str(proof_file), "--task", TASK_ID[:12])

    pool = isolated_home / "cache/verifier/conjectures-tasks/pool/tier-1" / TASK_SLUG
    called = ready.read_text(encoding="utf-8")
    assert f"--task {pool} " in called
    assert f"--expected-task-sha256 {TASK_DIGEST}" in called


def test_a_digest_the_pinned_pool_disagrees_with_is_refused_as_pin_drift(
    ready: Path, proof_file: Path
):
    error = _refusal(
        "verify",
        "--proof",
        str(proof_file),
        "--task",
        TASK_ID,
        "--task-sha256",
        "sha256:" + "e" * 64,
    )

    assert getattr(error, "exit_code", None) == verifier.VerifierError.exit_code
    assert "the pinned pool has" in str(error)
    assert not ready.exists()


# --- the sealed bundle as the default ------------------------------------------------------------


def test_with_no_arguments_it_is_the_sealed_bundle_that_is_verified(
    ready: Path, built: tuple[Path, Path], isolated_home: Path
):
    """`build` then `verify`, with nothing to retype, and no cache: the manifest names the task.

    The bytes checked come out of the archive rather than off disk, so this is a statement about
    the submission -- a proof edited since `build` is not what `submit` would send.
    """
    _, plan_path = built

    verdict = _succeed("verify", "--plan", str(plan_path))

    assert verdict["accepted"] is True
    called = ready.read_text(encoding="utf-8")
    pool = isolated_home / "cache/verifier/conjectures-tasks/pool/tier-1" / TASK_SLUG
    assert f"--task {pool} " in called
    assert f"--expected-task-sha256 {TASK_DIGEST}" in called
    assert Path(f"{ready}.submission").read_bytes() == PROOF


def test_an_explicit_proof_replaces_the_sealed_one_and_leaves_the_task_to_the_plan(
    ready: Path, built: tuple[Path, Path], isolated_home: Path
):
    _, plan_path = built
    candidate = isolated_home / "Candidate.lean"
    candidate.write_bytes(b"theorem target : True := by\n  trivial\n")

    _succeed("verify", "--plan", str(plan_path), "--proof", str(candidate))

    assert Path(f"{ready}.submission").read_bytes() == candidate.read_bytes()
    assert f"--expected-task-sha256 {TASK_DIGEST}" in ready.read_text(encoding="utf-8")


@pytest.mark.usefixtures("ready")
def test_nothing_built_and_nothing_named_is_refused_with_both_ways_out(isolated_home: Path):
    error = _refusal("verify", "--plan", str(isolated_home / "absent.plan.json"))

    assert getattr(error, "exit_code", None) == verifier.TaskNotVerifiableError.exit_code
    assert "--proof and --task" in str(error)
    assert "conjectures build" in str(getattr(error, "hint", ""))


# --- the stub upstreams ------------------------------------------------------------------------


def _seed_cache(*task_ids: str) -> None:
    """A `tasks sync` that already happened, so the CLI can tell retired from merely unpinned."""
    payload = task_list_response(tasks=list(task_ids))
    TaskCache(Path(os.environ["CONJECTURES_CACHE_DIR"]), API).save(
        TaskCacheFile.model_validate(
            payload | {"api_base_url": API, "fetched_at": datetime.now(UTC)}
        )
    )


def _invoke(*args: str) -> Any:
    return runner.invoke(app, ["--output", "json", *args])


def _succeed(*args: str) -> dict:
    result = _invoke(*args)
    if result.exception and not isinstance(result.exception, SystemExit):
        raise result.exception
    assert result.exit_code == 0, result.stderr
    return json.loads(result.stdout)


def _refusal(*args: str) -> BaseException:
    """A CliError reaches a test as the exception it is: only `cli.run` makes it an exit code."""
    result = _invoke(*args)
    assert result.exception is not None, result.stdout
    return result.exception


def _git(*args: str, cwd: Path) -> str:
    identity = {
        "GIT_AUTHOR_NAME": "stub",
        "GIT_AUTHOR_EMAIL": "stub@example.com",
        "GIT_COMMITTER_NAME": "stub",
        "GIT_COMMITTER_EMAIL": "stub@example.com",
    }
    result = subprocess.run(
        [GIT, *args], cwd=cwd, capture_output=True, text=True, check=True, env=os.environ | identity
    )
    return result.stdout.strip()


def _init(path: Path) -> None:
    path.mkdir(parents=True)
    _git("init", "-q", "-b", "main", cwd=path)
    # Both default to false on a local repository, and GitHub has both on. Without them the
    # blob-filtered clone and the fetch of an older commit by sha are refused by the server side.
    _git("config", "uploadpack.allowFilter", "true", cwd=path)
    _git("config", "uploadpack.allowAnySHA1InWant", "true", cwd=path)


def _write(root: Path, files: Mapping[str, str], *, executable: tuple[str, ...] = ()) -> None:
    for name, content in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        if name in executable:
            path.chmod(0o755)


def _commit(root: Path, message: str) -> str:
    _git("add", "--all", cwd=root)
    _git("commit", "-q", "-m", message, cwd=root)
    return _head(root)


def _head(root: Path) -> str:
    return _git("rev-parse", "HEAD", cwd=root)
