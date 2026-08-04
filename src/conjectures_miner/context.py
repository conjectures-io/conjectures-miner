"""What every command receives, assembled once by the root callback and reached as `ctx.obj`.

Its own module rather than `cli`, because commands import this and `cli` imports commands.
Everything below the settings is lazy, so an offline command opens no socket and touches no
wallet.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import cached_property
from typing import Any

from conjectures_miner.api.client import ApiClient
from conjectures_miner.cache import TaskCache
from conjectures_miner.output import Renderer
from conjectures_miner.settings import Settings
from conjectures_miner.signing import Signer, hotkey_address, load_signer
from conjectures_miner.state import StateStore


@dataclass
class AppContext:
    settings: Settings
    render: Renderer
    # Which options the user actually passed, so `config show --resolved` can name the layer.
    overrides: dict[str, Any] = field(default_factory=dict)
    # A development key such as `//Alice`. A flag only: key material never reaches Settings.
    uri: str | None = None

    @cached_property
    def client(self) -> ApiClient:
        return ApiClient(self.settings)

    @cached_property
    def cache(self) -> TaskCache:
        return TaskCache(self.settings.cache_dir, self.settings.api_root)

    @cached_property
    def state(self) -> StateStore:
        return StateStore(self.settings.state_dir)

    @cached_property
    def signer(self) -> Signer:
        return load_signer(self.settings, uri=self.uri)

    def hotkey_address(self, explicit: str | None = None) -> str:
        return hotkey_address(self.settings, uri=self.uri, explicit=explicit)

    def close(self) -> None:
        if "client" in self.__dict__:
            self.client.close()
