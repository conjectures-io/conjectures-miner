"""Building and driving a local verifier: the validator's own code, on the miner's host.

No pin is negotiated here, because the pins travel with the code. Cloning the validator brings
`pins.lock.json` with it, `pin_dependencies.sh` clones Formal Conjectures, Mathlib and the
comparator at those commits, and `assert_dependency_pins` re-checks every one of them on every
proof. So this module chooses a *ref* and nothing else: the ref chooses the recipe, and the recipe
chooses everything that decides a verdict. The worst a wrong ref can do is build a verifier that
refuses to run -- it cannot build one that quietly answers differently.

The tasks checkout is the exception, and the reason it is cloned at a commit rather than at latest.
It supplies the audited Formal Conjectures patch, which `pin_dependencies.sh` accepts only at
`pins.lock.json -> tasks.commit` and only if its sha256 matches. That commit is therefore read out
of the validator clone rather than tracked here.

What this is not is an attestation. A local verifier runs the development sandbox, not the
isolation a validator applies to a proof it did not write.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError

from conjectures_miner import __version__
from conjectures_miner.errors import CliError
from conjectures_miner.settings import Settings

GIGABYTE = 1024**3
# Mathlib's build cache and the Lean toolchain dominate this. `scripts/check_prerequisites.py` in
# the validator repository holds the authoritative figure and re-checks it right after the clone;
# this copy exists only because that script cannot run before the clone.
REQUIRED_DISK_BYTES = 20 * GIGABYTE
# `--filter=blob:none`, which every checkout here and in pin_dependencies.sh relies on.
MINIMUM_GIT_VERSION = (2, 19)
# Reaching the clone is all this gate has to prove. The rest of the egress list -- PyPI, the
# Mathlib cache buckets -- belongs to the validator's own gate, which runs a minute later and is
# the one place worth maintaining it.
CLONE_HOST = "https://github.com"

VALIDATOR_DIRECTORY = "conjectures-validator"
# A sibling of the validator checkout, which is where pin_dependencies.sh looks by default.
TASKS_DIRECTORY = "conjectures-tasks"
SETUP_RECORD_NAME = "setup.json"
SETUP_SCHEMA_VERSION = 1

# The checked-in task bundles, one directory per task, under `pool/<tier>/<slug>/`.
POOL_DIRECTORY = "pool"
ALLOWLIST_NAME = "allowlist.json"

RESUMES = (
    "Fix what it reported and run `conjectures verify --setup` again -- it resumes rather than "
    "rebuilding."
)


class VerifierError(CliError):
    """The local verifier could not be built, or is not fit to answer."""

    exit_code = 5


class TaskNotVerifiableError(CliError):
    """This task cannot be run here at all -- retired, absent from the pool, or named wrong.

    Deliberately not exit 1. That code has to mean "the verifier rejected this proof" and nothing
    else, or a script reads a retired task as a wrong proof and the miner rewrites a correct one.
    """

    exit_code = 2


@dataclass(frozen=True, slots=True)
class Check:
    """One prerequisite.

    The field names are `check_prerequisites.py --json`'s, which is parsed straight into this, so
    the small pre-clone gate below and the validator's full gate render as one kind of thing.
    """

    name: str
    ok: bool
    detail: str
    remedy: str = ""
    # A failed advisory check is reported and does not stop the build.
    advisory: bool = False


@dataclass(frozen=True, slots=True)
class Paths:
    home: Path
    validator: Path
    tasks: Path

    @property
    def record(self) -> Path:
        return self.home / SETUP_RECORD_NAME

    @property
    def python(self) -> Path:
        """The interpreter `bootstrap.sh --miner` installs the verifier into."""
        return self.validator / ".venv" / "bin" / "python"


@dataclass(frozen=True, slots=True)
class Checkouts:
    validator_commit: str
    tasks_commit: str


@dataclass(frozen=True, slots=True)
class TaskBundle:
    """A task directory in the pinned pool, and what its manifest says about running it."""

    path: Path
    task_id: str
    task_mode: str
    timeout_seconds: int


class SetupRecord(BaseModel):
    """What the last completed setup built, written beside the checkouts it describes.

    Beside them rather than in `state_dir`: it describes that tree and is worthless without it, so
    losing the two together is correct. It is also the answer to "which validator did you build?",
    which is the first question any disagreement with a real verdict raises.
    """

    model_config = ConfigDict(extra="ignore", frozen=True)

    schema_version: int = SETUP_SCHEMA_VERSION
    repository: str
    ref: str
    validator_commit: str
    tasks_commit: str
    completed_at: datetime
    tool_version: str


def paths(settings: Settings) -> Paths:
    home = settings.verifier_home
    return Paths(home=home, validator=home / VALIDATOR_DIRECTORY, tasks=home / TASKS_DIRECTORY)


def read_record(where: Paths) -> SetupRecord | None:
    """The last completed setup, or None when there has not been one this code can read."""
    try:
        record = SetupRecord.model_validate_json(where.record.read_text("utf-8"))
    except (OSError, ValueError, ValidationError):
        return None
    return record if record.schema_version == SETUP_SCHEMA_VERSION else None


def write_record(where: Paths, *, repository: str, ref: str, checkouts: Checkouts) -> SetupRecord:
    record = SetupRecord(
        repository=repository,
        ref=ref,
        validator_commit=checkouts.validator_commit,
        tasks_commit=checkouts.tasks_commit,
        completed_at=datetime.now(UTC),
        tool_version=__version__,
    )
    where.home.mkdir(parents=True, exist_ok=True)
    where.record.write_text(record.model_dump_json(indent=2), encoding="utf-8")
    return record


# --- the pre-clone gate ----------------------------------------------------------------------


def preflight(home: Path, *, offline: bool = False) -> list[Check]:
    """The little that has to hold before a clone can happen.

    Deliberately a subset. The authoritative list is `scripts/check_prerequisites.py` in the
    validator repository, and it cannot run until that repository has been cloned -- so what is
    duplicated here is only what the clone itself needs, plus the disk figure, which is worth
    knowing before five gigabytes are downloaded.
    """
    checks = [_check_git(), _check_command("curl", "--version", remedy="sudo apt install curl")]
    if not offline:
        checks.append(_check_reachable(CLONE_HOST))
    checks.append(_check_disk(home))
    return checks


def _check_git() -> Check:
    remedy = "sudo apt install git"
    found = shutil.which("git")
    if found is None:
        return Check("git", False, "not on PATH", remedy)
    result = _capture(found, "--version")
    if result.returncode != 0:
        return Check("git", False, f"{found}: {_last_line(result)}", remedy)
    reported = result.stdout.split()
    version = reported[2] if len(reported) > 2 else ""
    try:
        parsed = tuple(int(part) for part in version.split(".")[:2])
    except ValueError:
        parsed = ()
    if parsed < MINIMUM_GIT_VERSION:
        wanted = ".".join(str(part) for part in MINIMUM_GIT_VERSION)
        return Check(
            "git",
            False,
            f"{found} is {version or 'an unreadable version'}",
            f"partial clones need Git >= {wanted}",
        )
    return Check("git", True, f"{found} {version}")


def _check_command(name: str, *version_args: str, remedy: str) -> Check:
    found = shutil.which(name)
    if found is None:
        return Check(name, False, "not on PATH", remedy)
    result = _capture(found, *version_args)
    if result.returncode != 0:
        return Check(name, False, f"{found}: {_last_line(result)}", remedy)
    return Check(name, True, found)


def _check_reachable(url: str) -> Check:
    """Reachability, not status: any answer at all proves egress and TLS work."""
    curl = shutil.which("curl")
    if curl is None:
        return Check("network", False, "curl is not on PATH", "sudo apt install curl")
    probe = _capture(curl, "-sS", "-I", "-o", os.devnull, "--max-time", "20", url)
    if probe.returncode != 0:
        return Check(
            "network",
            False,
            f"cannot reach {url}: {_last_line(probe)}",
            "setup clones from here; a missing ca-certificates looks the same as a blocked host",
        )
    return Check("network", True, f"{url} is reachable")


def _check_disk(home: Path) -> Check:
    # The target does not exist on a first run, so measure the nearest ancestor that does.
    where = next((parent for parent in [home, *home.parents] if parent.exists()), Path("/"))
    free = shutil.disk_usage(where).free
    detail = f"{free / GIGABYTE:.1f} GB free on {where}"
    if free >= REQUIRED_DISK_BYTES:
        return Check("disk", True, detail)
    return Check(
        "disk", False, detail, f"the build needs about {REQUIRED_DISK_BYTES // GIGABYTE} GB"
    )


# --- the checkouts ---------------------------------------------------------------------------


def sync_checkouts(where: Paths, *, repository: str, ref: str) -> Checkouts:
    """Clone or update both repositories, laid out as siblings, and report what they landed on."""
    validator_commit = _sync(repository=repository, ref=ref, destination=where.validator)
    pins = read_pins(where)
    tasks_commit = _sync(
        repository=str(pins["tasks"]["repository"]),
        ref=str(pins["tasks"]["commit"]),
        destination=where.tasks,
    )
    return Checkouts(validator_commit=validator_commit, tasks_commit=tasks_commit)


def read_pins(where: Paths) -> dict[str, Any]:
    path = where.validator / "pins.lock.json"
    try:
        pins = json.loads(path.read_text("utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise VerifierError(f"could not read {path}: {exc}") from exc
    if not isinstance(pins, dict) or "tasks" not in pins:
        raise VerifierError(f"{path} is not a pin file this version understands")
    return pins


def _sync(*, repository: str, ref: str, destination: Path) -> str:
    """Put `destination` on `ref`, detached, and return the commit it resolved to.

    Detached on purpose, as `pin_dependencies.sh` does for the vendored checkouts: a miner who
    commits on top of a branch here would be verifying against code no validator runs.
    """
    if (destination / ".git").is_dir():
        # A run interrupted between the clone and the checkout leaves a repository whose index is
        # empty, so `status` reports every tracked file as deleted. That is a setup to resume, not
        # a modification, and the only way to tell them apart is that the tree holds nothing yet.
        checked_out = any(entry.name != ".git" for entry in destination.iterdir())
        if checked_out and _git("status", "--porcelain", cwd=destination):
            raise VerifierError(
                f"{destination} has local modifications",
                hint="A modified verifier does not answer for the validator's. Inspect it with "
                f"`git -C {destination} status`, then re-run.",
            )
    else:
        if destination.exists() and any(destination.iterdir()):
            raise VerifierError(
                f"{destination} exists and is not a Git checkout",
                hint="Move it aside, or set CONJECTURES_VERIFIER_ROOT to another directory.",
            )
        destination.parent.mkdir(parents=True, exist_ok=True)
        _git("clone", "--filter=blob:none", "--no-checkout", repository, str(destination))
    _git("remote", "set-url", "origin", repository, cwd=destination)
    # Fetching the ref by name covers a branch, a tag and a commit alike, and leaves the answer in
    # FETCH_HEAD -- so re-running after the branch has moved lands on the new tip.
    _git("fetch", "--no-tags", "origin", ref, cwd=destination)
    _git("checkout", "--detach", "FETCH_HEAD", cwd=destination)
    return _git("rev-parse", "HEAD", cwd=destination)


# --- the build -------------------------------------------------------------------------------


def prerequisites(where: Paths, *, offline: bool = False) -> tuple[bool, list[Check]]:
    """The validator's own gate, run before the expensive part.

    Not ready is a report rather than a crash: the script exits 1 to say so, and every failure it
    found is worth showing at once.
    """
    script = where.validator / "scripts" / "check_prerequisites.py"
    if not script.is_file():
        raise VerifierError(
            f"{script} is missing from the validator checkout",
            hint="This ref predates the miner build path. Set --ref, or CONJECTURES_VERIFIER_REF.",
        )
    command = [_python3(), str(script), "--miner", "--json"]
    if offline:
        command.append("--offline")
    result = _capture(*command, cwd=where.validator, env=_environment(where))
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise VerifierError(f"{script.name} produced no report: {_last_line(result)}") from exc
    # Read leniently: this crosses a repository boundary, and a field added on the validator side
    # must not turn a passing gate into a crash here.
    checks = [
        Check(
            name=str(entry.get("name", "?")),
            ok=bool(entry.get("ok")),
            detail=str(entry.get("detail", "")),
            remedy=str(entry.get("remedy", "")),
            advisory=bool(entry.get("advisory", False)),
        )
        for entry in payload.get("checks", [])
    ]
    return bool(payload.get("ready")), checks


def bootstrap(where: Paths, *, on_line: Callable[[str], None]) -> None:
    """The 30-plus minute part: pins, Elan, the venv, and the Lean build. Streamed, not buffered."""
    script = where.validator / "scripts" / "bootstrap.sh"
    if not script.is_file():
        raise VerifierError(f"{script} is missing from the validator checkout")
    _stream([str(script), "--miner"], cwd=where.validator, env=_environment(where), on_line=on_line)


def doctor(where: Paths) -> dict[str, Any]:
    """The verifier's own readiness report, judged against the sandbox a miner actually runs."""
    if not where.python.is_file():
        raise VerifierError(
            f"no verifier virtualenv at {where.python}",
            hint="Run `conjectures verify --setup` first.",
        )
    result = _capture(
        str(where.python),
        "-m",
        "verifier",
        "doctor",
        "--allow-insecure-development",
        cwd=where.validator,
        env=_environment(where),
    )
    try:
        # Exit 2 means "not ready", which is a verdict about the host and not a failure to report.
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise VerifierError(f"the verifier produced no report: {_last_line(result)}") from exc


def summarise(report: Mapping[str, Any]) -> dict[str, Any]:
    """The handful of fields from a doctor report worth putting in front of a miner."""
    lean = str(report.get("toolchain_identity", {}).get("lean_version", "")).strip()
    return {
        "ready": bool(report.get("ready")),
        "sandbox_mode": report.get("sandbox", {}).get("mode"),
        "formal_conjectures_commit": report.get("formal_conjectures", {}).get("actual_commit"),
        "lean": lean.splitlines()[0] if lean else None,
        "missing_tools": report.get("comparator", {}).get("missing", []),
    }


# --- the task pool ---------------------------------------------------------------------------


def allowed_tasks(where: Paths) -> dict[str, str] | None:
    """Task id -> committed bundle digest, from the pinned pool's allowlist.

    None when it cannot be read, which is reported rather than refused: this exists to fail a
    retired target in two seconds instead of an hour, and the verifier is the authority either way.
    """
    try:
        allowlist = json.loads((where.tasks / ALLOWLIST_NAME).read_text("utf-8"))
        entries = allowlist["allowed_task_bundles"]
    except (OSError, json.JSONDecodeError, KeyError, TypeError):
        return None
    return {
        str(entry["task_id"]): str(entry["task_bundle_sha256"])
        for entry in entries
        if isinstance(entry, dict) and "task_id" in entry and "task_bundle_sha256" in entry
    }


def find_task(where: Paths, task_id: str) -> TaskBundle | None:
    """The pool directory whose manifest carries this task id.

    A scan rather than a lookup: the directories are named by slug, `allowlist.json` records no
    path, and the manifest is the only thing connecting the two. 280 small reads.
    """
    for path in sorted((where.tasks / POOL_DIRECTORY).glob("*/*/manifest.json")):
        try:
            manifest = json.loads(path.read_text("utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if manifest.get("task_id") == task_id:
            return TaskBundle(
                path=path.parent,
                task_id=task_id,
                task_mode=str(manifest.get("task_mode", "unknown")),
                timeout_seconds=int(manifest.get("timeout_seconds", 0)),
            )
    return None


def run_verification(
    where: Paths,
    *,
    task: Path,
    submission: Path,
    expected_task_sha256: str,
    on_line: Callable[[str], None],
) -> tuple[int, dict[str, Any]]:
    """Run the real verifier over one proof. Returns its exit code and the report it printed.

    `--expected-task-sha256` is the whole of the digest check: the verifier compares it against the
    bundle it loaded and refuses on a mismatch, so nothing here recomputes a task digest. The
    timeout is the manifest's and the verifier enforces it, so nothing here imposes a second one.

    stdout goes to a file rather than a pipe. The report is one JSON document on stdout and the
    diagnostics arrive on stderr; reading both as pipes needs threads to avoid deadlocking on a
    full buffer, and keeping stdout off the pipes is the cheaper way to stream one of them.
    """
    if not where.python.is_file():
        raise VerifierError(
            f"no verifier virtualenv at {where.python}",
            hint="Run `conjectures verify --setup` first.",
        )
    command = [
        str(where.python),
        "-m",
        "verifier",
        "verify",
        "--task",
        str(task),
        "--submission",
        str(submission),
        "--expected-task-sha256",
        expected_task_sha256,
        # A miner verifying their own proof is not defending against themselves. It changes the
        # isolation, not the verdict, and every report this produces says which one it ran.
        "--allow-insecure-development",
    ]
    with tempfile.TemporaryDirectory() as directory:
        report_path = Path(directory) / "report.json"
        with report_path.open("wb") as report_file:
            try:
                process = subprocess.Popen(
                    command,
                    cwd=where.validator,
                    env=_environment(where),
                    stdout=report_file,
                    stderr=subprocess.PIPE,
                    text=True,
                    errors="replace",
                    bufsize=1,
                )
            except OSError as exc:
                raise VerifierError(f"could not run the verifier: {exc}") from exc
            stderr = process.stderr
            assert stderr is not None  # guaranteed by stderr=PIPE, but not by the type
            with stderr:
                for line in stderr:
                    on_line(line.rstrip("\n"))
            code = process.wait()
        raw = report_path.read_text("utf-8", errors="replace")
    try:
        return code, json.loads(raw)
    except json.JSONDecodeError as exc:
        raise VerifierError(
            f"the verifier exited {code} without a report",
            hint=raw.strip()[-400:] or None,
        ) from exc


def summarise_verdict(report: Mapping[str, Any]) -> dict[str, Any]:
    """The fields of a verification report a miner reads first."""
    milliseconds = report.get("duration_ms")
    return {
        "accepted": bool(report.get("accepted")),
        "task_id": report.get("task_id"),
        "task_mode": report.get("task_mode"),
        # Where it stopped, which is the difference between a wrong proof and a broken host.
        "stage": report.get("stage"),
        "reason_code": report.get("reason_code"),
        "proof_sha256": report.get("submission_sha256"),
        "task_bundle_sha256": report.get("task_bundle_sha256"),
        "sandbox_mode": report.get("sandbox_mode"),
        "duration_seconds": None if milliseconds is None else round(int(milliseconds) / 1000, 1),
    }


def rows(checks: Sequence[Check]) -> list[dict[str, Any]]:
    """Checks as a table, in the shape `check_prerequisites.py` prints them."""
    return [
        {
            "status": "ok" if check.ok else "warn" if check.advisory else "FAIL",
            "check": check.name,
            "detail": check.detail,
            "remedy": check.remedy or None,
        }
        for check in checks
    ]


# --- running things --------------------------------------------------------------------------


def _environment(where: Paths) -> dict[str, str]:
    elan = where.validator / ".elan"
    return os.environ | {
        "ELAN_HOME": str(elan),
        "PATH": f"{elan / 'bin'}{os.pathsep}{os.environ.get('PATH', os.defpath)}",
        # pin_dependencies.sh defaults to a sibling of the validator checkout, which is this
        # layout; naming it anyway means a layout change cannot silently repoint the audit patch.
        "CONJECTURES_TASKS_ROOT": str(where.tasks),
        # Nothing here is interactive, and a 30-minute build that stopped at a credential prompt
        # nobody can see looks exactly like a hang.
        "GIT_TERMINAL_PROMPT": "0",
    }


def _python3() -> str:
    """`python3` from PATH, not `sys.executable`.

    bootstrap.sh builds the venv with that one, so a prerequisite report about a different
    interpreter than the build will use is worse than no report at all.
    """
    found = shutil.which("python3")
    if found is None:
        raise VerifierError("python3 is not on PATH", hint="sudo apt install python3")
    return found


def _git(*args: str, cwd: Path | None = None) -> str:
    found = shutil.which("git")
    if found is None:
        raise VerifierError("git is not on PATH", hint="sudo apt install git")
    result = _capture(found, *args, cwd=cwd, env=_git_environment())
    if result.returncode != 0:
        raise VerifierError(f"git {args[0]} failed: {_last_line(result)}")
    return result.stdout.strip()


def _git_environment() -> dict[str, str]:
    return os.environ | {"GIT_TERMINAL_PROMPT": "0"}


def _capture(
    *command: str, cwd: Path | None = None, env: Mapping[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            command,
            cwd=cwd,
            env=None if env is None else dict(env),
            capture_output=True,
            text=True,
            errors="replace",
            check=False,
        )
    except OSError as exc:
        raise VerifierError(f"could not run {command[0]}: {exc}") from exc


def _stream(
    command: Sequence[str],
    *,
    cwd: Path,
    env: Mapping[str, str],
    on_line: Callable[[str], None],
) -> None:
    """Run a long command, handing each line over as it arrives.

    Streamed because the alternative is half an hour of silence, which is where most abandonment
    happens. stderr is folded into stdout so the interleaving a reader sees is the real one.
    """
    try:
        process = subprocess.Popen(
            list(command),
            cwd=cwd,
            env=dict(env),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            errors="replace",
            bufsize=1,
        )
    except OSError as exc:
        raise VerifierError(f"could not run {command[0]}: {exc}") from exc
    stdout = process.stdout
    assert stdout is not None  # guaranteed by stdout=PIPE, but not by the type
    with stdout:
        for line in stdout:
            on_line(line.rstrip("\n"))
    if process.wait() != 0:
        raise VerifierError(
            f"{Path(command[0]).name} exited {process.returncode}; the output above says where",
            hint=RESUMES,
        )


def _last_line(result: subprocess.CompletedProcess[str]) -> str:
    output = (result.stderr or result.stdout or "").strip()
    return output.splitlines()[-1] if output else f"exited {result.returncode}"
