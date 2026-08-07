"""Writing and reading a `conjectures-submission/v1` bundle. Stdlib only, like `digest`.

The validator admits an archive only if it holds exactly two entries, in order, with no extra
fields, no comments and no directory entries -- and it parses the ZIP structure itself rather
than trusting `zipfile`, so "it opens locally" proves nothing. The writer below mirrors
`scripts/build_submission_bundle.py` in the validator repo, which is the reference for the format.
"""

from __future__ import annotations

import json
import re
import zipfile
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any

from conjectures_miner.digest import is_sha256, sha256_prefixed
from conjectures_miner.errors import CliError

BUNDLE_FORMAT = "conjectures-submission/v1"
BUNDLE_SCHEMA_VERSION = 1
BUNDLE_MEDIA_TYPE = "application/zip"

MANIFEST_NAME = "submission.json"
PROOF_NAME = "Main.lean"
ENTRY_NAMES = (MANIFEST_NAME, PROOF_NAME)

MAX_BUNDLE_BYTES = 2 * 1024 * 1024
MAX_PROOF_BYTES = 1_000_000

TASK_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,254}$")
# Exactly 48, matching the validator: a 47-character address is accepted by the API and then
# refused at INSERT, so it is worth catching before anything is written.
SS58_ADDRESS = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{48}$")
SOLVER_TOKEN = re.compile(r"^[A-Za-z0-9._-]{1,64}$")

# Fixed so a rebuild of the same inputs produces the same archive.
ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)
UNIX_CREATE_SYSTEM = 3
REGULAR_FILE_MODE = 0o644


class BundleError(CliError):
    exit_code = 2


@dataclass(frozen=True, slots=True)
class Bundle:
    """An archive's bytes and everything the submit path reads out of it."""

    raw: bytes
    manifest: dict[str, Any]

    @property
    def sha256(self) -> str:
        return sha256_prefixed(self.raw)

    @property
    def task_id(self) -> str:
        return str(self.manifest["task_id"])

    @property
    def task_bundle_sha256(self) -> str:
        return str(self.manifest["task_bundle_sha256"])

    @property
    def proof_sha256(self) -> str:
        return str(self.manifest["proof_sha256"])

    @property
    def miner_hotkey(self) -> str:
        return str(self.manifest["miner_hotkey"])

    @property
    def proof(self) -> bytes:
        """The proof as it will be sent, read back out of the archive rather than off disk."""
        with zipfile.ZipFile(BytesIO(self.raw)) as archive:
            return archive.read(PROOF_NAME)


def check_proof(proof: bytes) -> None:
    """Apply the validator's byte-level proof policy before anything is written."""
    if not proof:
        raise BundleError("the proof file is empty")
    if len(proof) > MAX_PROOF_BYTES:
        raise BundleError(f"the proof is {len(proof)} bytes; the limit is {MAX_PROOF_BYTES}")
    if b"\x00" in proof:
        raise BundleError("the proof contains a NUL byte")
    try:
        proof.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise BundleError(f"the proof is not valid UTF-8: {exc}") from exc


def build_manifest(
    *,
    task_id: str,
    task_bundle_sha256: str,
    hotkey_ss58: str,
    proof: bytes,
    solver_name: str | None = None,
    solver_version: str | None = None,
) -> dict[str, Any]:
    if TASK_ID.fullmatch(task_id) is None:
        raise BundleError(f"{task_id!r} is not a valid task id")
    if not is_sha256(task_bundle_sha256):
        raise BundleError(f"{task_bundle_sha256!r} is not a lowercase sha256: digest")
    if SS58_ADDRESS.fullmatch(hotkey_ss58) is None:
        raise BundleError(f"{hotkey_ss58!r} is not a valid SS58 address")

    manifest: dict[str, Any] = {
        "schema_version": BUNDLE_SCHEMA_VERSION,
        "format": BUNDLE_FORMAT,
        "task_id": task_id,
        "task_bundle_sha256": task_bundle_sha256,
        "proof_path": PROOF_NAME,
        "proof_sha256": sha256_prefixed(proof),
        "proof_bytes": len(proof),
        "miner_hotkey": hotkey_ss58,
    }
    if solver_name is not None or solver_version is not None:
        if not (solver_name and solver_version):
            raise BundleError("a solver needs both a name and a version")
        for value in (solver_name, solver_version):
            if SOLVER_TOKEN.fullmatch(value) is None:
                raise BundleError(f"{value!r} must match [A-Za-z0-9._-]{{1,64}}")
        manifest["solver"] = {"name": solver_name, "version": solver_version}
    return manifest


def render_manifest(manifest: dict[str, Any]) -> bytes:
    return json.dumps(manifest, indent=2, sort_keys=True).encode("utf-8")


def write(output: Path, *, manifest: dict[str, Any], proof: bytes) -> Bundle:
    """Write the archive, atomically, and return the bytes that were written."""
    temporary = output.with_name(output.name + ".partial")
    try:
        with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED) as archive:
            for name, data in ((MANIFEST_NAME, render_manifest(manifest)), (PROOF_NAME, proof)):
                entry = zipfile.ZipInfo(name, date_time=ZIP_TIMESTAMP)
                entry.compress_type = zipfile.ZIP_DEFLATED
                entry.create_system = UNIX_CREATE_SYSTEM
                entry.external_attr = REGULAR_FILE_MODE << 16
                archive.writestr(entry, data)
        temporary.replace(output)
    except OSError as exc:
        temporary.unlink(missing_ok=True)
        raise BundleError(f"could not write {output}: {exc}") from exc

    raw = output.read_bytes()
    if len(raw) > MAX_BUNDLE_BYTES:
        raise BundleError(f"the archive is {len(raw)} bytes; the limit is {MAX_BUNDLE_BYTES}")
    return Bundle(raw=raw, manifest=manifest)


def load_bytes(path: Path) -> bytes:
    try:
        return path.read_bytes()
    except OSError as exc:
        raise BundleError(f"could not read {path}: {exc}") from exc


def read(path: Path) -> Bundle:
    """Read an archive and its manifest. Structural admission is the validator's call."""
    return parse(load_bytes(path), source=path)


def parse(raw: bytes, *, source: Path | str = "<bytes>") -> Bundle:
    try:
        with zipfile.ZipFile(BytesIO(raw)) as archive:
            names = archive.namelist()
            if tuple(names) != ENTRY_NAMES:
                raise BundleError(
                    f"{source} must hold exactly {', '.join(ENTRY_NAMES)} in that order, "
                    f"found {', '.join(names) or 'nothing'}"
                )
            manifest = json.loads(archive.read(MANIFEST_NAME))
    except (zipfile.BadZipFile, OSError) as exc:
        raise BundleError(f"{source} is not a readable archive: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise BundleError(f"{source} has an unreadable {MANIFEST_NAME}: {exc}") from exc

    if not isinstance(manifest, dict):
        raise BundleError(f"{source}: {MANIFEST_NAME} must be a JSON object")
    missing = {"task_id", "task_bundle_sha256", "proof_sha256", "miner_hotkey"} - set(manifest)
    if missing:
        raise BundleError(f"{source}: {MANIFEST_NAME} is missing {', '.join(sorted(missing))}")
    return Bundle(raw=raw, manifest=manifest)
