"""MCP client: connects to configured MCP servers (stdio or streamable HTTP) and exposes their
tools through the tool registry as ``mcp__<server>__<tool>`` (shown as ``mcp.<server>.<tool>``).

Each server lives in its own task (the SDK's transports use cancel scopes that must be entered and
exited in the same task). Config changes are diffed and only the affected servers restart — no
brain restart needed.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Literal

import httpx2
from jarvis_protocol import SideEffect
from mcp import Client, StdioServerParameters
from mcp.client.streamable_http import streamable_http_client
from pydantic import BaseModel, Field

from ..config import ConfigStore, Section
from ..secrets import SecretStore
from ..tools.registry import Tool, ToolContext, ToolRegistry

log = logging.getLogger(__name__)

PREFIX = "mcp__"
_SAFE = re.compile(r"[^a-zA-Z0-9_-]")
_SECRET_REF = re.compile(r"\$\{([A-Z][A-Z0-9_]*)\}")


class MCPServerConfig(BaseModel):
    name: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,30}$")
    transport: Literal["stdio", "http"]
    command: str = ""
    args: list[str] = []
    env: dict[str, str] = {}  # values may reference secrets: "${GITHUB_TOKEN}"
    url: str = ""
    headers: dict[str, str] = {}  # same ${SECRET} substitution
    enabled: bool = True
    allowed_tools: list[str] | None = None  # None = all discovered tools
    side_effects: dict[str, SideEffect] = {}  # per-tool override; default from annotations
    timeout_s: float = Field(60, ge=1, le=900)


class MCPConfig(Section):
    servers: list[MCPServerConfig] = []


ConfigStore.register("mcp", MCPConfig)


def wire_name(server: str, tool: str) -> str:
    """Provider-safe tool name (^[a-zA-Z0-9_-]{1,64}$)."""
    return f"{PREFIX}{server}__{_SAFE.sub('_', tool)}"[:64]


def display_name(server: str, tool: str) -> str:
    return f"mcp.{server}.{tool}"


def default_side_effect(annotations: Any) -> SideEffect:
    """Unknown = irreversible (confirmation required). Only explicit hints lower it."""
    if annotations is None:
        return SideEffect.IRREVERSIBLE
    if getattr(annotations, "read_only_hint", None) is True:
        return SideEffect.NONE
    if getattr(annotations, "destructive_hint", None) is False:
        return SideEffect.REVERSIBLE
    return SideEffect.IRREVERSIBLE


def result_to_text(result: Any) -> tuple[bool, str]:
    parts: list[str] = []
    for block in getattr(result, "content", []) or []:
        t = getattr(block, "type", "")
        if t == "text":
            parts.append(block.text)
        elif t == "resource" and getattr(block.resource, "text", None):
            parts.append(block.resource.text)
        elif t in ("image", "audio"):
            parts.append(f"[{t} omitted]")
        elif t == "resource_link":
            parts.append(f"[resource {getattr(block, 'uri', '')}]")
    if not parts and getattr(result, "structured_content", None) is not None:
        import json

        parts.append(json.dumps(result.structured_content, default=str))
    return (not getattr(result, "is_error", False)), "\n".join(parts) or "(no content)"


@dataclass
class ServerState:
    cfg: MCPServerConfig
    status: Literal["starting", "connected", "error", "stopped"] = "starting"
    error: str = ""
    tools: list[dict[str, Any]] = field(default_factory=list)  # discovered, for the admin UI
    client: Client | None = None
    task: asyncio.Task[None] | None = None
    stop: asyncio.Event = field(default_factory=asyncio.Event)
    connected_at: float | None = None


class MCPManager:
    def __init__(self, config: ConfigStore, secrets: SecretStore, registry: ToolRegistry) -> None:
        self.config = config
        self.secrets = secrets
        self.registry = registry
        self.servers: dict[str, ServerState] = {}
        config.on_change(lambda name, _v: name == "mcp" and self._schedule_sync())
        self._sync_lock = asyncio.Lock()

    # ------------------------------------------------------------------ lifecycle

    def _schedule_sync(self) -> None:
        with contextlib.suppress(RuntimeError):
            asyncio.get_running_loop().create_task(self.sync())

    async def start(self) -> None:
        await self.sync()

    async def stop(self) -> None:
        for name in list(self.servers):
            await self._stop_server(name)

    async def sync(self) -> None:
        """Start/stop/restart servers so they match the config."""
        async with self._sync_lock:
            wanted = {s.name: s for s in self.config.get("mcp").servers if s.enabled}
            for name in list(self.servers):
                if name not in wanted or self.servers[name].cfg != wanted[name]:
                    await self._stop_server(name)
            for name, cfg in wanted.items():
                if name not in self.servers:
                    self._start_server(cfg)

    def _start_server(self, cfg: MCPServerConfig) -> None:
        st = ServerState(cfg)
        self.servers[cfg.name] = st
        st.task = asyncio.create_task(self._run(st), name=f"mcp:{cfg.name}")

    async def _stop_server(self, name: str) -> None:
        st = self.servers.pop(name, None)
        self.registry.unregister_prefix(f"{PREFIX}{name}__")
        if st and st.task:
            st.stop.set()
            try:
                await asyncio.wait_for(st.task, 10)
            except (TimeoutError, asyncio.CancelledError, Exception):
                st.task.cancel()

    def _subst(self, value: str) -> str:
        return _SECRET_REF.sub(lambda m: self.secrets.get(m.group(1)), value)

    def _target(self, cfg: MCPServerConfig) -> Any:
        if cfg.transport == "stdio":
            if not cfg.command:
                raise ValueError("stdio server needs a command")
            env = {k: self._subst(v) for k, v in cfg.env.items()}
            # Minimal inherited env (PATH/HOME…) plus the configured variables — no brain secrets.
            base = {k: os.environ[k] for k in ("PATH", "HOME", "LANG", "TZ") if k in os.environ}
            return StdioServerParameters(command=cfg.command, args=cfg.args, env={**base, **env})
        if not cfg.url.startswith(("http://", "https://")):
            raise ValueError("http server needs an http(s) URL")
        headers = {k: self._subst(v) for k, v in cfg.headers.items()}
        http = httpx2.AsyncClient(headers=headers, timeout=httpx2.Timeout(30, read=cfg.timeout_s))
        return streamable_http_client(cfg.url, http_client=http)

    async def _run(self, st: ServerState) -> None:
        delay = 2.0
        while not st.stop.is_set():
            try:
                async with Client(
                    self._target(st.cfg), read_timeout_seconds=st.cfg.timeout_s
                ) as client:
                    st.client = client
                    await self._register_tools(st, client)
                    st.status, st.error, st.connected_at = "connected", "", time.time()
                    log.info("MCP %s connected (%d tools)", st.cfg.name, len(st.tools))
                    delay = 2.0
                    await st.stop.wait()
            except asyncio.CancelledError:
                raise
            except BaseException as e:
                st.status, st.error = "error", _describe(e)
                log.warning("MCP %s failed: %s", st.cfg.name, st.error)
            finally:
                st.client = None
                self.registry.unregister_prefix(f"{PREFIX}{st.cfg.name}__")
            if st.stop.is_set():
                break
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(st.stop.wait(), delay)
            delay = min(delay * 2, 120)
        st.status = "stopped"

    async def _register_tools(self, st: ServerState, client: Client) -> None:
        tools: list[Any] = []
        cursor = None
        while True:
            page = await client.list_tools(cursor=cursor)
            tools += page.tools
            cursor = getattr(page, "next_cursor", None)
            if not cursor:
                break
        st.tools = []
        allowed = set(st.cfg.allowed_tools) if st.cfg.allowed_tools is not None else None
        for t in tools:
            level = st.cfg.side_effects.get(t.name) or default_side_effect(t.annotations)
            enabled = allowed is None or t.name in allowed
            st.tools.append(
                {
                    "name": t.name,
                    "display": display_name(st.cfg.name, t.name),
                    "wire": wire_name(st.cfg.name, t.name),
                    "description": t.description or "",
                    "side_effect": level,
                    "enabled": enabled,
                    "annotations": t.annotations.model_dump() if t.annotations else None,
                }
            )
            if enabled:
                self.registry.register(self._make_tool(st, t, level))

    def _make_tool(self, st: ServerState, t: Any, level: SideEffect) -> Tool:
        server, remote = st.cfg.name, t.name

        async def call(args: dict[str, Any], _ctx: ToolContext) -> str:
            client = st.client
            if client is None:
                raise RuntimeError(f"MCP server {server} is not connected")
            ok, text = result_to_text(await client.call_tool(remote, args))
            if not ok:
                raise RuntimeError(text[:500])
            return text

        schema = dict(t.input_schema or {"type": "object", "properties": {}})
        schema.setdefault("type", "object")
        return Tool(
            name=wire_name(server, remote),
            description=f"[{server}] {(t.description or remote).strip()}"[:1000],
            handler=call,
            schema=schema,
            side_effect=level,
            source="mcp",
            label=(t.annotations.title if t.annotations and t.annotations.title else remote),
            icon="plug",
            timeout_s=st.cfg.timeout_s,
        )

    # ------------------------------------------------------------------ admin helpers

    def describe(self) -> list[dict[str, Any]]:
        out = []
        for s in self.config.get("mcp").servers:
            st = self.servers.get(s.name)
            out.append(
                {
                    **s.model_dump(mode="json", exclude={"env", "headers"}),
                    "env_keys": sorted(s.env),
                    "header_keys": sorted(s.headers),
                    "status": st.status if st else ("disabled" if not s.enabled else "stopped"),
                    "error": st.error if st else "",
                    "tools": st.tools if st else [],
                }
            )
        return out

    async def test(self, cfg: MCPServerConfig) -> dict[str, Any]:
        """Connect once, list tools, disconnect — for the admin 'Test' button."""
        try:
            async with asyncio.timeout(30), Client(self._target(cfg)) as client:
                page = await client.list_tools()
                return {
                    "ok": True,
                    "tools": [
                        {
                            "name": t.name,
                            "description": t.description or "",
                            "side_effect": default_side_effect(t.annotations),
                        }
                        for t in page.tools
                    ],
                }
        except BaseException as e:
            if isinstance(e, asyncio.CancelledError):
                raise
            return {"ok": False, "error": _describe(e)}


def _describe(e: BaseException) -> str:
    if isinstance(e, BaseExceptionGroup) and e.exceptions:
        return _describe(e.exceptions[0])
    return f"{type(e).__name__}: {e}"[:400]
