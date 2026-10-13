"""The chat-log terms this tool shows before an upload, and what it reads from a package.

Every open problem requires the AI chat logs that produced the proof, uploaded before the proof is
submitted. The validator refuses an upload whose consent names terms other than its own, so these
are pinned to its digests: a mismatch means the terms changed since this release, and the answer
is to upgrade -- never to agree to text this tool has not shown.
"""

from __future__ import annotations

import io
import json
import zipfile
from dataclasses import dataclass

from conjectures_miner.errors import CliError

TERMS_VERSION = "chat-log-terms-v2"
TERMS_SHA256 = "sha256:a12a14d07a61fb8e1d430618e50f3a1f812295183a21c215b172b555e6ee9809"
TERMS_URL = "https://conjectures.io/trajectory/chat-log-terms-v2.md"
INSTRUCTIONS_URL = "https://conjectures.io/trajectory/agent-instructions.md"

# The validator's `assent-statements-v2.json`, byte for byte: its digest is what consent cites.
ASSENT_STATEMENTS = (
    b'{\n  "terms": "I agree to the chat-log terms. These are my real chat logs for this proof, '
    b"I am allowed to share them, and once they are accepted Conjectures may sell and license "
    b'them to buyers."\n}\n'
)
ASSENT_STATEMENTS_SHA256 = "sha256:e95110b7fd55ef1639b66ff1049723c610ab7c5c5c92b9d429c4ddd80721a62d"
ASSENT = json.loads(ASSENT_STATEMENTS)["terms"]


@dataclass(frozen=True, slots=True)
class PackageSummary:
    """What a package says it is for. The validator checks all of it; this is for the preview."""

    task_id: str | None
    task_bundle_sha256: str | None
    final_proof_sha256: str | None


def summarise(raw: bytes) -> PackageSummary:
    """Read the manifest of a chat-log package, at its root or inside one top-level folder."""
    try:
        archive = zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile as exc:
        raise CliError(
            "that file is not a chat-log package: it is not a ZIP archive",
            hint=f"Build the package as {INSTRUCTIONS_URL} describes.",
        ) from exc
    with archive:
        names = [
            name
            for name in archive.namelist()
            if name == "manifest.json" or (name.count("/") == 1 and name.endswith("/manifest.json"))
        ]
        if len(names) != 1:
            raise CliError(
                "that file is not a chat-log package: it has no single manifest.json",
                hint=f"Build the package as {INSTRUCTIONS_URL} describes.",
            )
        try:
            manifest = json.loads(archive.read(names[0]))
        except (KeyError, ValueError, zipfile.BadZipFile) as exc:
            raise CliError(f"could not read the package's manifest.json: {exc}") from exc
    if not isinstance(manifest, dict):
        raise CliError("the package's manifest.json is not a JSON object")

    def text(key: str) -> str | None:
        value = manifest.get(key)
        return value if isinstance(value, str) else None

    return PackageSummary(
        task_id=text("task_id"),
        task_bundle_sha256=text("task_bundle_sha256"),
        final_proof_sha256=text("final_proof_sha256"),
    )
