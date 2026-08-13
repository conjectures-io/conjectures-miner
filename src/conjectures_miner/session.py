"""The website session: the one credential this tool stores rather than derives.

Everything else it sends is a signature it makes on the spot -- `submit` over the request digest,
`submissions show` over a read message -- and a signature is worthless the moment the request it
covers is answered. A bearer token is not: it is reusable until it expires, so it is the only thing
here worth stealing, and it gets a file mode the rest of the local state does not need.

Three properties, each load-bearing:

* **Mode `0600`, never briefly wider.** Created with the mode rather than chmod'ed into it, and
  chmod'ed anyway, because `os.open`'s mode applies only when the file is new -- a token written
  over a pre-existing world-readable file would otherwise inherit it.
* **Bound to one validator.** The token names the `api_base_url` it was minted against, and
  `require` refuses to hand it to any other. A token for a local validator arriving at production
  is a credential leak, and pointing `--api` somewhere else is one flag away.
* **Not in the config file, not in the environment.** `settings.py` holds no key material by
  standing rule, and a token is key material. It lives beside `config.toml` so that
  `CONJECTURES_CONFIG_FILE` moves both together, but it is never *in* it.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, ValidationError

from conjectures_miner import settings as settings_module
from conjectures_miner.errors import ConfigError
from conjectures_miner.settings import Settings

SESSION_FILE_NAME = "session.json"
FILE_MODE = 0o600


class Session(BaseModel):
    """A bearer token and the facts needed to know whether it may still be used."""

    model_config = ConfigDict(extra="ignore")

    access_token: str
    expires_at: datetime
    # Which validator minted it. Not decoration: `matches` is what keeps it from reaching another.
    api_base_url: str
    hotkey: str
    account_id: str


def session_file_path() -> Path:
    """Beside `config.toml`, so one override moves the pair and a test can isolate both."""
    return settings_module.config_file_path().parent / SESSION_FILE_NAME


def save(session: Session) -> Path:
    """Write the token, never leaving it readable by anyone else -- not even for an instant."""
    path = session_file_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, FILE_MODE)
    try:
        handle = os.fdopen(descriptor, "w", encoding="utf-8")
    except BaseException:
        # Only this narrow window leaks the descriptor; once `fdopen` owns it, `with` closes it,
        # and closing here as well would free a number the process may already have reused.
        os.close(descriptor)
        raise
    with handle:
        handle.write(session.model_dump_json(indent=2))
    # `O_CREAT`'s mode is ignored when the file already existed, so this is the case that matters.
    path.chmod(FILE_MODE)
    return path


def load() -> Session | None:
    """The stored session, or None if there is nothing usable there.

    Tolerant of a corrupt file, like `StateStore.all`: a session that cannot be read is a session
    the miner does not have, and `auth login` is the fix for both.
    """
    path = session_file_path()
    try:
        return Session.model_validate(json.loads(path.read_text("utf-8")))
    except (OSError, json.JSONDecodeError, ValidationError):
        return None


def clear() -> bool:
    """Forget the token. Returns whether there was one."""
    path = session_file_path()
    existed = path.is_file()
    path.unlink(missing_ok=True)
    return existed


def is_expired(session: Session, *, now: datetime | None = None) -> bool:
    return session.expires_at <= (now or datetime.now(UTC))


def matches(session: Session, api_root: str) -> bool:
    """Whether this token belongs to the validator the CLI is currently pointed at."""
    return session.api_base_url.rstrip("/") == api_root.rstrip("/")


def require(settings: Settings) -> Session:
    """The session, or a refusal that says which of the three things is wrong.

    A `ConfigError`, so not being signed in exits `2`: it is local state the miner fixes locally,
    not the validator saying no.
    """
    stored = load()
    if stored is None:
        raise ConfigError(
            "not signed in",
            hint="Run `conjectures auth login`. It needs a hotkey already linked to your "
            "conjectures.io account.",
        )
    if not matches(stored, settings.api_root):
        raise ConfigError(
            f"the stored session was minted for {stored.api_base_url}, not {settings.api_root}",
            hint="A token is only valid where it was issued. Point --api back at "
            f"{stored.api_base_url}, or run `conjectures auth login` against "
            f"{settings.api_root}.",
        )
    if is_expired(stored):
        raise ConfigError(
            f"the stored session expired at {stored.expires_at.isoformat()}",
            hint="Run `conjectures auth login`.",
        )
    return stored
