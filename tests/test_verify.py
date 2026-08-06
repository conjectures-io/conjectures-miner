"""`verify --setup` end to end, against a validator repository stubbed down to its interface.

The stub is the two scripts the CLI actually calls -- `check_prerequisites.py --miner --json` and
`bootstrap.sh --miner` -- plus a `pins.lock.json` and the `.gitignore` that keeps a built checkout
looking clean. Everything the real build does in half an hour is out of scope here; what is in
scope is that the right things are cloned at the right commits, in the right order, and that a
second run resumes.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from conjectures_miner import verifier
from conjectures_miner.cli import app

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

# Prints a line rich would read as markup for a style it does not have, which is what `log` is for.
BOOTSTRAP = """\
#!/usr/bin/env bash
set -euo pipefail
echo "pinning dependencies"
echo "[3/7922] Building Mathlib.Tactic.Ring"
echo "tasks root is ${CONJECTURES_TASKS_ROOT:?not set}"
mkdir -p .venv/bin
cat > .venv/bin/python <<'STUB'
#!/usr/bin/env bash
cat "$(dirname "$0")/../../doctor.json"
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
    _write(tasks, {"tiers/tier-1/formal-conjectures-audit-fixes.patch": "the audited patch\n"})
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
def test_a_verifier_that_reports_unready_exits_non_zero(isolated_home: Path):
    _succeed("verify", "--setup", "--offline")
    checkout = isolated_home / "cache" / "verifier" / "conjectures-validator"
    (checkout / "doctor.json").write_text(json.dumps(DOCTOR | {"ready": False}))

    result = _invoke("verify")

    assert result.exit_code == 1
    assert json.loads(result.stdout)["ready"] is False


# --- the stub upstreams ------------------------------------------------------------------------


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
