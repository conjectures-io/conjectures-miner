"""Precedence, the whole point of this module: flag > environment > config file > default."""

from __future__ import annotations

from pathlib import Path

import pytest

from conjectures_miner import settings as settings_module
from conjectures_miner.errors import ConfigError


@pytest.fixture
def config_file() -> Path:
    return Path(settings_module.config_file_path())


def test_default_is_used_when_nothing_else_says_otherwise(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("CONJECTURES_API_BASE_URL")
    assert settings_module.load().api_base_url == settings_module.DEFAULT_API_BASE_URL


def test_the_config_file_beats_the_default(config_file: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("CONJECTURES_API_BASE_URL")
    config_file.write_text('api_base_url = "https://from-file"\n')
    assert settings_module.load().api_base_url == "https://from-file"


def test_the_environment_beats_the_config_file(config_file: Path, monkeypatch: pytest.MonkeyPatch):
    config_file.write_text('api_base_url = "https://from-file"\n')
    monkeypatch.setenv("CONJECTURES_API_BASE_URL", "https://from-env")
    assert settings_module.load().api_base_url == "https://from-env"


def test_a_flag_beats_the_environment(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CONJECTURES_API_BASE_URL", "https://from-env")
    assert (
        settings_module.load(api_base_url="https://from-flag").api_base_url == "https://from-flag"
    )


def test_an_unset_flag_does_not_outrank_the_environment(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CONJECTURES_API_BASE_URL", "https://from-env")
    assert settings_module.load(api_base_url=None).api_base_url == "https://from-env"


def test_api_root_drops_the_trailing_slash():
    assert settings_module.load(api_base_url="https://host/").api_root == "https://host"


def test_an_unknown_config_key_is_refused(config_file: Path):
    config_file.write_text('nonsense = "value"\n')
    with pytest.raises(ConfigError, match="nonsense"):
        settings_module.load()


def test_a_bad_value_is_refused_with_the_field_named(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CONJECTURES_REQUEST_TIMEOUT_SECONDS", "soon")
    with pytest.raises(ConfigError, match="request_timeout_seconds"):
        settings_module.load()


def test_describe_names_the_layer_each_value_came_from(
    config_file: Path, monkeypatch: pytest.MonkeyPatch
):
    config_file.write_text('wallet_name = "from-file"\n')
    monkeypatch.setenv("CONJECTURES_WALLET_HOTKEY", "from-env")
    resolved = settings_module.load(api_base_url="https://from-flag")
    sources = {
        row["setting"]: row["source"]
        for row in settings_module.describe(resolved, {"api_base_url": "https://from-flag"})
    }
    assert sources["api_base_url"] == "flag"
    assert sources["wallet_hotkey"] == "environment"
    assert sources["wallet_name"] == "config file"
    assert sources["upload_timeout_seconds"] == "default"


def test_settings_hold_no_key_material():
    forbidden = {"mnemonic", "seed", "password", "private_key", "secret", "uri"}
    assert not forbidden & set(settings_module.Settings.model_fields)
