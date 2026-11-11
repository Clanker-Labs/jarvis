"""Tool registry: built-in tools, MCP tools and skills are all exposed to the agent the same way.

Every tool declares a side-effect level. Inputs are validated against the tool's pydantic model
before the handler runs (model output is untrusted, and eagerly streamed inputs can be truncated).
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal

import jsonschema
from jarvis_protocol import SideEffect
from pydantic import BaseModel, ValidationError

from ..providers.base import ToolCall, ToolSpec

log = logging.getLogger(__name__)

Source = Literal["builtin", "mcp", "skill", "connector"]
Handler = Callable[[Any, "ToolContext"], Awaitable[Any]]


@dataclass
class ToolContext:
    turn_id: str | None = None
    user_text: str = ""
    extras: dict[str, Any] = field(default_factory=dict)


@dataclass
class Tool:
    name: str
    description: str
    handler: Handler
    params: type[BaseModel] | None = None  # pydantic model; or raw `schema` for MCP tools
    schema: dict[str, Any] | None = None
    side_effect: SideEffect = SideEffect.NONE
    source: Source = "builtin"
    label: str = ""
    icon: str = "tool"
    enabled: bool = True
    timeout_s: float = 30.0
    # Spoken read-back for confirmations: "send an email to Sam saying …"
    describe: Callable[[dict[str, Any]], str] | None = None

    def json_schema(self) -> dict[str, Any]:
        if self.params is not None:
            s = self.params.model_json_schema()
            s.pop("title", None)
            return s
        return self.schema or {"type": "object", "properties": {}}

    def spec(self) -> ToolSpec:
        return ToolSpec(self.name, self.description, self.json_schema())

    def readback(self, args: dict[str, Any]) -> str:
        if self.describe:
            try:
                return self.describe(args)
            except Exception:
                log.debug("describe() failed for %s", self.name, exc_info=True)
        pretty = ", ".join(f"{k} {v}" for k, v in args.items() if v not in (None, "", []))
        return f"use {self.label or self.name}" + (f" with {pretty}" if pretty else "")


@dataclass
class ToolOutcome:
    ok: bool
    content: str  # what the model sees
    summary: str  # short, for UI/traces
    duration_s: float = 0.0


def _to_text(result: Any, limit: int = 12_000) -> str:
    if isinstance(result, str):
        text = result
    elif isinstance(result, BaseModel):
        text = result.model_dump_json()
    else:
        text = json.dumps(result, default=str, ensure_ascii=False)
    if len(text) > limit:
        text = text[:limit] + f"\n…[truncated {len(text) - limit} characters]"
    return text


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}
        self.overrides: dict[str, dict[str, Any]] = {}  # name → {"enabled": bool, "side_effect": …}
        self.listeners: list[Callable[[], None]] = []

    def register(self, tool: Tool, *, replace: bool = True) -> None:
        if tool.name in self._tools and not replace:
            raise ValueError(f"tool {tool.name} already registered")
        self._tools[tool.name] = tool
        self._changed()

    def unregister_prefix(self, prefix: str) -> None:
        for name in [n for n in self._tools if n.startswith(prefix)]:
            del self._tools[name]
        self._changed()

    def _changed(self) -> None:
        for cb in self.listeners:
            cb()

    def get(self, name: str) -> Tool | None:
        tool = self._tools.get(name)
        if tool is None:
            return None
        ov = self.overrides.get(name, {})
        if ov:
            tool = Tool(**{**tool.__dict__, **{k: v for k, v in ov.items() if k in ("enabled",)}})
            if "side_effect" in ov:
                tool.side_effect = max(
                    SideEffect(ov["side_effect"]), tool.side_effect, key=_SEVERITY.__getitem__
                )
        return tool

    def all(self) -> list[Tool]:
        return [t for n in sorted(self._tools) if (t := self.get(n))]

    def active(self) -> list[Tool]:
        return [t for t in self.all() if t.enabled]

    def specs(self) -> list[ToolSpec]:
        return [t.spec() for t in self.active()]

    def validate(self, call: ToolCall) -> tuple[Tool | None, dict[str, Any] | str]:
        tool = self.get(call.name)
        if tool is None or not tool.enabled:
            return None, f"Unknown or disabled tool: {call.name}"
        if "__invalid_json__" in call.args:
            return tool, "Tool arguments were not valid JSON."
        if tool.params is not None:
            try:
                return tool, tool.params.model_validate(call.args).model_dump()
            except ValidationError as e:
                return tool, f"Invalid arguments: {e.errors(include_url=False)}"
        if not isinstance(call.args, dict):
            return tool, "Arguments must be an object."
        try:
            jsonschema.validate(call.args, tool.json_schema())
        except jsonschema.ValidationError as e:
            return tool, f"Invalid arguments: {e.message}"
        except jsonschema.SchemaError:
            log.warning("tool %s has an invalid schema; skipping validation", tool.name)
        return tool, call.args

    async def execute(self, call: ToolCall, ctx: ToolContext) -> ToolOutcome:
        t0 = time.monotonic()
        tool, args = self.validate(call)
        if tool is None or isinstance(args, str):
            return ToolOutcome(False, str(args), str(args)[:120], time.monotonic() - t0)
        try:
            parsed: Any = tool.params.model_validate(args) if tool.params else args
            result = await asyncio.wait_for(tool.handler(parsed, ctx), tool.timeout_s)
            text = _to_text(result)
            return ToolOutcome(True, text, text[:160], time.monotonic() - t0)
        except TimeoutError:
            msg = f"{tool.name} timed out after {tool.timeout_s:.0f}s"
        except Exception as e:
            log.exception("tool %s failed", tool.name)
            msg = f"{tool.name} failed: {e}"
        return ToolOutcome(False, msg, msg[:160], time.monotonic() - t0)


_SEVERITY = {SideEffect.NONE: 0, SideEffect.REVERSIBLE: 1, SideEffect.IRREVERSIBLE: 2}
