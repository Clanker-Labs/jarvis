"""Provider configuration, construction, health checks and routing."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import time
from collections import deque
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field

from ..config import ConfigStore, Section
from ..secrets import SecretStore
from ..settings import Settings
from .anthropic_provider import PRICING, AnthropicProvider
from .base import Pricing, Provider
from .openai_compat import OpenAICompatProvider

log = logging.getLogger(__name__)

Route = Literal["chat", "tools", "task"]


class ProviderEntry(BaseModel):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,40}$")
    kind: Literal["anthropic", "openai", "local", "cli"]
    model: str = ""
    base_url: str = ""
    api_key_secret: str = ""  # name of the secret holding the key
    enabled: bool = True
    tool_mode: Literal["native", "json", "auto"] = "native"
    effort: Literal["low", "medium", "high", "xhigh", "max"] | None = None
    input_price: float | None = None  # USD per 1M tokens (None = built-in table / free)
    output_price: float | None = None
    # agent-CLI providers (phase 8)
    cli: Literal["claude", "codex", "cursor-agent"] | None = None


DEFAULT_TOOL_WORDS = [
    "weather",
    "forecast",
    "rain",
    "email",
    "mail",
    "inbox",
    "calendar",
    "schedule",
    "meeting",
    "appointment",
    "remind",
    "reminder",
    "timer",
    "alarm",
    "note",
    "search",
    "look up",
    "google",
    "news",
    "latest",
    "price",
    "who won",
    "remember",
    "recall",
    "my notes",
    "document",
    "file",
    "according to",
    "compare",
    "plan",
    "summarise",
    "summarize",
    "calculate",
    "convert",
]
DEFAULT_TASK_WORDS = [
    "build",
    "code",
    "fix the",
    "implement",
    "refactor",
    "deploy",
    "write a script",
    "debug",
    "repo",
    "repository",
    "pull request",
    "commit",
    "run the tests",
    "open a pr",
]


class ProvidersConfig(Section):
    providers: list[ProviderEntry] = [
        ProviderEntry(id="local", kind="local", tool_mode="auto"),
        ProviderEntry(
            id="claude-fast",
            kind="anthropic",
            model="claude-haiku-5-5",
            api_key_secret="ANTHROPIC_API_KEY",
            effort="low",
        ),
        ProviderEntry(
            id="claude-strong",
            kind="anthropic",
            model="claude-opus-5-5",
            api_key_secret="ANTHROPIC_API_KEY",
            effort="medium",
        ),
        ProviderEntry(
            id="openai",
            kind="openai",
            base_url="https://api.openai.com/v1",
            api_key_secret="OPENAI_API_KEY",
            enabled=False,
        ),
        ProviderEntry(
            id="openrouter",
            kind="openai",
            base_url="https://openrouter.ai/api/v1",
            api_key_secret="OPENROUTER_API_KEY",
            enabled=False,
        ),
    ]
    routes: dict[str, list[str]] = {
        "chat": ["local", "claude-fast", "claude-strong"],
        "tools": ["claude-strong", "claude-fast", "local"],
        "task": ["claude-strong"],  # agent CLIs are prepended here in phase 8
    }
    tool_keywords: list[str] = DEFAULT_TOOL_WORDS
    task_keywords: list[str] = DEFAULT_TASK_WORDS
    health_interval_s: int = Field(30, ge=5, le=3600)


ConfigStore.register("providers", ProvidersConfig)


def classify(text: str, cfg: ProvidersConfig) -> Route:
    t = f" {text.lower()} "

    def hit(words: list[str]) -> bool:
        return any(re.search(rf"\b{re.escape(w.lower())}\b", t) for w in words if w.strip())

    if hit(cfg.task_keywords):
        return "task"
    if hit(cfg.tool_keywords):
        return "tools"
    return "chat"


@dataclass
class CallStat:
    ts: float
    provider: str
    model: str
    route: str
    ok: bool
    first_token_s: float | None
    total_s: float
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    error: str = ""


class Router:
    def __init__(self, config: ConfigStore, secrets: SecretStore, settings: Settings) -> None:
        self.config = config
        self.secrets = secrets
        self.settings = settings
        self.providers: dict[str, Provider] = {}
        self.health: dict[str, bool] = {}
        self.health_checked: dict[str, float] = {}
        self.stats: deque[CallStat] = deque(maxlen=500)
        self.extra_factories: dict[str, object] = {}  # kind → factory (agent CLIs, phase 8)
        self._task: asyncio.Task[None] | None = None
        self.on_health: list = []
        self.rebuild()
        config.on_change(lambda name, _v: name == "providers" and self.rebuild())

    @property
    def cfg(self) -> ProvidersConfig:
        return self.config.get("providers")

    def _build(self, e: ProviderEntry) -> Provider | None:
        key = (lambda n=e.api_key_secret: self.secrets.get(n)) if e.api_key_secret else (lambda: "")
        price = (
            Pricing(e.input_price or 0, e.output_price or 0)
            if e.input_price is not None or e.output_price is not None
            else None
        )
        if e.kind == "anthropic":
            return AnthropicProvider(
                e.id,
                e.model or "claude-opus-5-5",
                key,
                effort=e.effort,
                pricing=price or PRICING.get(e.model),
                base_url=e.base_url or None,
            )
        if e.kind in ("openai", "local"):
            s = self.settings
            local = e.kind == "local"
            return OpenAICompatProvider(
                e.id,
                base_url=lambda e=e: e.base_url or (s.local_llm_base_url if local else ""),
                model=lambda e=e: e.model or (s.local_llm_model if local else ""),
                api_key=(lambda: s.local_llm_api_key.get_secret_value())
                if local and not e.api_key_secret
                else key,
                tool_mode=e.tool_mode,
                local=local,
                pricing=price,
            )
        factory = self.extra_factories.get(e.kind)
        return factory(e) if callable(factory) else None  # type: ignore[operator]

    def rebuild(self) -> None:
        built: dict[str, Provider] = {}
        for e in self.cfg.providers:
            if not e.enabled:
                continue
            try:
                p = self._build(e)
            except Exception:
                log.exception("could not build provider %s", e.id)
                continue
            if p is not None:
                built[e.id] = p
        self.providers = built
        self.health = {k: v for k, v in self.health.items() if k in built}
        log.info("providers: %s", ", ".join(built) or "none")

    def chain(self, route: Route) -> list[Provider]:
        """Providers for a route in priority order: known-healthy first, then unknown, then
        known-unhealthy (still worth a try as a last resort)."""
        ids = self.cfg.routes.get(route) or self.cfg.routes.get("chat", [])
        ps = [self.providers[i] for i in ids if i in self.providers]
        rank = {True: 0, None: 1, False: 2}
        return sorted(ps, key=lambda p: rank[self.health.get(p.id)])

    @property
    def any_healthy(self) -> bool:
        return any(self.health.values())

    def record(self, stat: CallStat) -> None:
        self.stats.append(stat)
        if not stat.ok:
            self.health[stat.provider] = False
        log.info(
            "llm %s/%s route=%s ok=%s ttft=%s total=%.2fs tokens=%d/%d cost=$%.5f%s",
            stat.provider,
            stat.model,
            stat.route,
            stat.ok,
            f"{stat.first_token_s:.2f}s" if stat.first_token_s is not None else "-",
            stat.total_s,
            stat.input_tokens,
            stat.output_tokens,
            stat.cost_usd,
            f" error={stat.error}" if stat.error else "",
        )
        self._notify()

    async def check_health(self) -> dict[str, bool]:
        async def one(pid: str, p: Provider) -> None:
            try:
                ok = await asyncio.wait_for(p.health(), timeout=8)
            except Exception:
                ok = False
            self.health[pid] = ok
            self.health_checked[pid] = time.time()

        await asyncio.gather(*(one(pid, p) for pid, p in list(self.providers.items())))
        self._notify()
        return dict(self.health)

    def _notify(self) -> None:
        for cb in self.on_health:
            cb(self.any_healthy)

    async def _loop(self) -> None:
        while True:
            try:
                await self.check_health()
            except Exception:
                log.exception("health check failed")
            await asyncio.sleep(self.cfg.health_interval_s)

    async def start(self) -> None:
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
