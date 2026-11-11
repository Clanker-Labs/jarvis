"""Connector framework. A connector is a folder in ``connectors/`` with:

- ``manifest.yaml`` — id, name, auth, required secrets, OAuth scopes, tools + side effects
- ``connector.py``  — ``create(ctx) -> Connector``

The loader refuses a connector whose code disagrees with its manifest (a tool missing from the
manifest, or a different side-effect level), so the manifest is an honest summary of what it can do.
See docs/ADDING_A_CONNECTOR.md.
"""

from __future__ import annotations

import asyncio
import contextlib
import importlib
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

import httpx
from jarvis_protocol import NowCard
from pydantic import Field

from ..config import ConfigStore, Section
from ..manifest import Manifest
from ..secrets import SecretStore
from ..tools.registry import Tool, ToolRegistry
from .oauth import OAuthManager

log = logging.getLogger(__name__)
ROOT = Path(__file__).parent


class ConnectorsConfig(Section):
    enabled: dict[str, bool] = Field(default_factory=dict)  # overrides manifest.enabled
    # OAuth redirect URI. Empty = "<admin origin>/api/connectors/oauth/callback".
    # Google only accepts https or loopback; with `tailscale serve` use your https *.ts.net URL.
    redirect_uri: str = ""
    now_refresh_s: int = Field(300, ge=60, le=3600)


ConfigStore.register("connectors", ConnectorsConfig)


@dataclass
class ConnectorContext:
    id: str
    manifest: Manifest
    secrets: SecretStore
    oauth: OAuthManager
    config: ConfigStore
    http: Callable[[], httpx.AsyncClient] = field(default=lambda: httpx.AsyncClient(timeout=15))

    async def bearer(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {await self.oauth.access_token(self.id)}"}


class Connector(Protocol):
    def tools(self) -> list[Tool]: ...

    async def now(self) -> dict[str, Any]:
        """Optional: fields for the UI's Now card (NowCard field names)."""
        ...


class ManifestMismatch(ValueError):
    pass


def check_against_manifest(manifest: Manifest, tools: list[Tool]) -> None:
    declared = {t.name: t for t in manifest.tools}
    for t in tools:
        d = declared.get(t.name)
        if d is None:
            raise ManifestMismatch(f"tool {t.name} is not declared in manifest.yaml")
        if d.side_effect != t.side_effect:
            raise ManifestMismatch(
                f"tool {t.name}: code says {t.side_effect}, manifest says {d.side_effect}"
            )


@dataclass
class LoadedConnector:
    manifest: Manifest
    impl: Any = None
    error: str = ""
    tools: list[Tool] = field(default_factory=list)


class ConnectorRegistry:
    def __init__(
        self,
        config: ConfigStore,
        secrets: SecretStore,
        oauth: OAuthManager,
        registry: ToolRegistry,
        *,
        root: Path = ROOT,
        http: Callable[[], httpx.AsyncClient] | None = None,
    ) -> None:
        self.config = config
        self.secrets = secrets
        self.oauth = oauth
        self.registry = registry
        self.root = root
        self.http = http
        self.connectors: dict[str, LoadedConnector] = {}
        self.on_now: list[Callable[[NowCard], None]] = []
        self._task: asyncio.Task[None] | None = None
        config.on_change(lambda name, _v: name == "connectors" and self.sync())

    @property
    def cfg(self) -> ConnectorsConfig:
        return self.config.get("connectors")

    def discover(self) -> None:
        for folder in sorted(self.root.iterdir()):
            mf = folder / "manifest.yaml"
            if folder.name.startswith("_"):
                continue  # _template and friends
            if not (folder.is_dir() and mf.is_file() and (folder / "connector.py").is_file()):
                continue
            try:
                manifest = Manifest.load(mf)
            except Exception as e:
                self.connectors[folder.name] = LoadedConnector(
                    Manifest(
                        id=folder.name.replace("_", "-")[:40], name=folder.name, type="connector"
                    ),
                    error=f"bad manifest: {e}",
                )
                continue
            lc = LoadedConnector(manifest)
            try:
                module = importlib.import_module(f"{__package__}.{folder.name}.connector")
                ctx = ConnectorContext(
                    manifest.id,
                    manifest,
                    self.secrets,
                    self.oauth,
                    self.config,
                    **({"http": self.http} if self.http else {}),
                )
                lc.impl = module.create(ctx)
                lc.tools = lc.impl.tools()
                check_against_manifest(manifest, lc.tools)
            except Exception as e:
                log.exception("connector %s failed to load", manifest.id)
                lc.error = str(e)
            self.connectors[manifest.id] = lc

    # ------------------------------------------------------------------ state

    def enabled(self, cid: str) -> bool:
        lc = self.connectors[cid]
        return self.cfg.enabled.get(cid, lc.manifest.enabled)

    def missing_secrets(self, cid: str) -> list[str]:
        return [
            s for s in self.connectors[cid].manifest.required_secrets if not self.secrets.has(s)
        ]

    def authorized(self, cid: str) -> bool:
        m = self.connectors[cid].manifest
        if self.missing_secrets(cid):
            return False
        return self.oauth.connected(cid) if m.auth == "oauth2" else True

    def active(self, cid: str) -> bool:
        lc = self.connectors[cid]
        return not lc.error and self.enabled(cid) and self.authorized(cid)

    def sync(self) -> None:
        """(Un)register each connector's tools according to enabled/auth state."""
        for cid, lc in self.connectors.items():
            for t in lc.tools:
                if self.active(cid):
                    self.registry.register(t)
                else:
                    self.registry.unregister(t.name)

    def describe(self) -> list[dict[str, Any]]:
        out = []
        for cid, lc in self.connectors.items():
            m = lc.manifest
            out.append(
                {
                    "id": cid,
                    "name": m.name,
                    "description": m.description,
                    "auth": m.auth,
                    "enabled": self.enabled(cid) if not lc.error else False,
                    "error": lc.error,
                    "required_secrets": m.required_secrets,
                    "missing_secrets": self.missing_secrets(cid),
                    "connected": self.oauth.connected(cid) if m.auth == "oauth2" else None,
                    "active": self.active(cid) if not lc.error else False,
                    "tools": [t.model_dump(mode="json") for t in m.tools],
                }
            )
        return out

    # ------------------------------------------------------------------ Now card

    async def refresh_now(self) -> NowCard:
        merged: dict[str, Any] = {}
        for cid, lc in self.connectors.items():
            if not self.active(cid) or not hasattr(lc.impl, "now"):
                continue
            try:
                merged.update({k: v for k, v in (await lc.impl.now()).items() if v is not None})
            except Exception as e:
                log.info("now-card refresh failed for %s: %s", cid, e)
        card = NowCard(**{k: merged.get(k) for k in NowCard.model_fields})
        for cb in self.on_now:
            cb(card)
        return card

    async def _loop(self) -> None:
        while True:
            await self.refresh_now()
            await asyncio.sleep(self.cfg.now_refresh_s)

    async def start(self) -> None:
        self.discover()
        self.sync()
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task


def rfc3339(ts: float) -> str:
    return datetime.fromtimestamp(ts).astimezone().isoformat(timespec="seconds")
