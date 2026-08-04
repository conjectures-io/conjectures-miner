"""Resolved configuration, and the precedence rules that produce it.

Precedence, highest first: **CLI flag -> environment -> user config file -> default.**

That is *not* pydantic-settings' default order, so `settings_customise_sources` reorders it.
The flag layer only works if the caller strips options the user did not actually supply --
a typer option defaulting to `None` must not outrank a real environment variable. See
`load()`.

Never add a field holding key material. Wallet *names* and paths belong here; seeds,
mnemonics and passwords do not. This tool spends TAO, and env vars leak into shells,
process listings, and CI logs.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from pydantic import HttpUrl
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

# Placeholder until the production host is confirmed -- see docs/MINER.md, which refers
# to it only as $CONJECTURES_API.
DEFAULT_API_BASE_URL = "https://api.conjectures.io"

CONFIG_FILE_NAME = "config.toml"


class Settings(BaseSettings):
    """Everything a command may need that the user can configure.

    One `api_base_url` rather than host + port: production is TLS on 443 and may carry a
    path prefix, both of which a separate port field breaks.
    """

    model_config: ClassVar[SettingsConfigDict] = SettingsConfigDict(
        env_prefix="CONJECTURES_",
        extra="forbid",
    )

    # --- validator endpoint ---
    api_base_url: HttpUrl
    request_timeout_seconds: float
    # Submitting streams a zip body; polling is cheap. Separate because one deserves
    # patience.
    upload_timeout_seconds: float

    # --- wallet: names and locations only, never material ---
    wallet_name: str | None
    wallet_hotkey: str | None
    wallet_path: Path | None

    # --- chain: declared, unread by the MVP ---
    # The MVP takes `--payment-ref` for an already-finalized transfer, so nothing here is
    # required. Absent values must not fail validation.
    network: str | None
    chain_endpoint: HttpUrl | None

    # --- local directories: kept apart on purpose ---
    # `cache_dir` holds the synced task list and is safe to delete at any time.
    # `state_dir` holds the idempotency keys, whose loss can cost a payment. One command
    # that clears "the data" must not be able to take both.
    cache_dir: Path
    state_dir: Path

    # How old a task cache may be before `build` says so. Never a correctness boundary --
    # `build` refetches the digest it commits to regardless.
    cache_max_age_seconds: float

    # --- presentation ---
    output_format: str

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Order the sources: init (flags), env, user config TOML, defaults.

        The TOML source is a `TomlConfigSettingsSource` over `config_file_path()`. A missing
        file is normal, not an error -- most miners will never write one.
        """
        raise NotImplementedError


def config_file_path() -> Path:
    """`config.toml` under the platform user-config directory.

    A user-level file rather than a `.env` in the working directory: this is a globally
    installed tool, and which directory it happens to run from should not change where it
    submits.
    """
    raise NotImplementedError


def cache_dir_for(api_base_url: str) -> Path:
    """The cache directory, resolved the way `Settings` resolves it.

    Exists so the completion callback can find the cache without constructing `Settings`:
    completion runs before any command body, must not validate configuration, and must not
    raise.
    """
    raise NotImplementedError


def load(**overrides: Any) -> Settings:
    """Build `Settings`, letting explicitly-supplied flags win.

    `overrides` comes from the root callback's typer options. Entries whose value is `None`
    are dropped before construction -- that is what keeps an unspecified flag from
    outranking the environment.
    """
    raise NotImplementedError
