"""Writing a `conjectures-submission/v1` bundle. Stdlib only, like `digest`.

The second byte-exact contract with the validator. Ported from
`scripts/build_submission_bundle.py`, which exists in the validator repo precisely so a
miner can copy it without taking on dependencies.

The archive is admitted only if it contains exactly the two required entries, in order,
with no extra fields, no comments, and no directory entries. The validator's scanner reads
the zip structure itself rather than trusting `zipfile`, so "it opens fine locally" proves
nothing.

The archive is written once, by `build`, and is the artifact from then on: `plan` records
its digest and later commands verify rather than regenerate it. So nothing here has to be
reproducible after the fact -- but `tests/vectors/` still pins the output, because the
format itself is a contract and a change to it must be deliberate.

Later this moves to a shared verification package imported by both the validator and this
tool.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

BUNDLE_FORMAT = "conjectures-submission/v1"
BUNDLE_SCHEMA_VERSION = 1
BUNDLE_MEDIA_TYPE = "application/zip"

MANIFEST_NAME = "manifest.json"
PROOF_NAME = "Main.lean"


@dataclass(frozen=True, slots=True)
class BuiltBundle:
    """A bundle on disk, plus the digests the submit path will need."""

    path: Path
    bundle_bytes: int
    bundle_sha256: str
    proof_sha256: str
    manifest: dict[str, object]


def build(
    *,
    proof: Path,
    task_id: str,
    task_bundle_sha256: str,
    hotkey_ss58: str,
    output: Path,
    solver_name: str | None = None,
    solver_version: str | None = None,
) -> BuiltBundle:
    """Write the archive and report what went into it.

    `proof_sha256` is taken from the archived bytes, not from the file on disk, so that what
    gets signed is what got shipped.
    """
    raise NotImplementedError


def inspect(path: Path) -> BuiltBundle:
    """Re-read an existing bundle and recompute its digests.

    Local sanity only -- structural admission is the validator's call, which `check` asks
    for over the network via preflight.
    """
    raise NotImplementedError
