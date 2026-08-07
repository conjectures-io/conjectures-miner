"""Resolved configuration: CLI flag > environment > user config file > default.

Never add a field holding key material. Wallet names and paths belong here; seeds, mnemonics and
passwords do not, because env vars leak into shells, process listings and CI logs.
"""

from __future__ import annotations

import os
import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Any, ClassVar, Literal

import platformdirs
from pydantic import Field, ValidationError
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    TomlConfigSettingsSource,
)

from conjectures_miner.errors import ConfigError

# Placeholder until the production host is confirmed -- the validator docs name it only as
# $CONJECTURES_API.
DEFAULT_API_BASE_URL = "https://conjectures.io"

# The validator repository a local verifier is built from, and the ref to build. The ref decides
# only which revision of the verifier source runs: `pins.lock.json` travels with it and is what
# fixes every dependency. `--ref` and CONJECTURES_VERIFIER_REF override it.
DEFAULT_VERIFIER_REPOSITORY = "https://github.com/conjectures-io/conjectures-validator.git"
DEFAULT_VERIFIER_REF = "main"

APP_NAME = "conjectures"
ENV_PREFIX = "CONJECTURES_"
CONFIG_FILE_ENV = f"{ENV_PREFIX}CONFIG_FILE"
CONFIG_FILE_NAME = "config.toml"

OutputFormat = Literal["auto", "table", "json"]


def config_file_path() -> Path:
    """A user-level file, so which directory the tool runs from cannot change where it submits."""
    override = os.environ.get(CONFIG_FILE_ENV)
    if override:
        return Path(override).expanduser()
    return Path(platformdirs.user_config_dir(APP_NAME)) / CONFIG_FILE_NAME


def read_config_file() -> dict[str, Any]:
    path = config_file_path()
    if not path.is_file():
        return {}
    try:
        return tomllib.loads(path.read_text("utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"could not read {path}: {exc}") from exc


class Settings(BaseSettings):
    """Everything a command may need that the user can configure."""

    model_config: ClassVar[SettingsConfigDict] = SettingsConfigDict(
        env_prefix=ENV_PREFIX, extra="forbid"
    )

    # One base URL rather than host + port: production is TLS on 443 and may carry a path prefix.
    api_base_url: str = DEFAULT_API_BASE_URL
    request_timeout_seconds: float = 30.0
    # Submitting streams a zip body; polling is cheap. Separate because one deserves patience.
    upload_timeout_seconds: float = 120.0

    # Wallet: names and locations only, never material.
    wallet_name: str = "default"
    wallet_hotkey: str = "default"
    wallet_path: Path | None = None
    # A validator outside PROD defaults to the static-key authenticator, which wants a fixed
    # marker where a signature goes. Off by default: an installed CLI meets production, and
    # production refuses the marker.
    dev_signature: bool = False

    # Which Subtensor `conjectures pay` transfers on and reads back from. Only that command and
    # `pay reference` open a chain connection at all; everything else talks to the validator over
    # HTTP. A network the SDK knows (`finney`, `test`, `local`) or a websocket endpoint.
    bittensor_network: str = "finney"

    # `cache_dir` is disposable; `state_dir` holds idempotency keys whose loss can cost a
    # payment. Kept apart so one command that clears "the data" cannot take both.
    cache_dir: Path = Field(default_factory=lambda: Path(platformdirs.user_cache_dir(APP_NAME)))
    state_dir: Path = Field(default_factory=lambda: Path(platformdirs.user_state_dir(APP_NAME)))
    cache_max_age_seconds: float = 24 * 60 * 60

    # `conjectures verify --setup` builds the validator's own verifier from source. Only the ref
    # is configured: the pins that decide a verdict travel with the code, so choosing the ref is
    # choosing all of them. Under `cache_dir` because it is 20 GB that a re-run rebuilds.
    verifier_repository: str = DEFAULT_VERIFIER_REPOSITORY
    verifier_ref: str = DEFAULT_VERIFIER_REF
    verifier_root: Path | None = None

    output_format: OutputFormat = "auto"

    @property
    def api_root(self) -> str:
        return self.api_base_url.rstrip("/")

    @property
    def verifier_home(self) -> Path:
        """Where the validator and tasks checkouts live, as siblings."""
        return self.verifier_root or self.cache_dir / "verifier"

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (
            init_settings,
            env_settings,
            TomlConfigSettingsSource(settings_cls, toml_file=config_file_path()),
        )


def load(**overrides: Any) -> Settings:
    """Build `Settings`; `None` overrides are dropped so an unset flag cannot outrank the env."""
    supplied = {name: value for name, value in overrides.items() if value is not None}
    try:
        return Settings(**supplied)
    except ValidationError as exc:
        raise ConfigError(_explain(exc)) from exc


def describe(settings: Settings, overrides: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Every effective setting with the layer it came from -- the "why is it submitting there"."""
    from_file = read_config_file()
    rows = []
    for name in type(settings).model_fields:
        if name in overrides and overrides[name] is not None:
            source = "flag"
        elif os.environ.get(f"{ENV_PREFIX}{name.upper()}") is not None:
            source = "environment"
        elif name in from_file:
            source = "config file"
        else:
            source = "default"
        rows.append({"setting": name, "value": getattr(settings, name), "source": source})
    return rows


def _explain(exc: ValidationError) -> str:
    lines = []
    for error in exc.errors():
        field = ".".join(str(part) for part in error["loc"]) or "settings"
        lines.append(f"{field}: {error['msg']}")
    return "invalid configuration -- " + "; ".join(lines)
