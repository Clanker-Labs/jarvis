"""Anthropic Messages API provider (official async SDK, streaming, native tool use).

Notes on the current API (see docs/DECISIONS.md#anthropic-provider):
- Thinking is adaptive by default on current models; depth is set with ``output_config.effort``.
  No sampling parameters, no assistant prefill.
- Within a turn, assistant content is replayed verbatim (``raw``) so thinking blocks stay valid;
  across turns history is text-only (removing *all* thinking blocks is allowed).
- The persona ``system`` prompt and the ``tools`` set are frozen; per-turn context (time, weather)
  goes in an appended ``role: "system"`` message on models that support it.
- Server-side refusal fallbacks (``fallbacks="default"``) are enabled on models that support them.
"""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator, Callable
from typing import Any

import anthropic

from .base import (
    AgentEvent,
    Completed,
    Message,
    Pricing,
    ProviderError,
    TextDelta,
    ToolCall,
    ToolCallRequested,
    ToolSpec,
)

log = logging.getLogger(__name__)

# Models that accept mid-conversation `role: "system"` messages.
_MID_SYSTEM = (
    "claude-opus-5",
    "claude-opus-4-8",
    "claude-fable",
    "claude-mythos",
    "claude-sonnet-5-5",
    "claude-haiku-5-5",
)
# Models where `fallbacks: "default"` (server-side refusal fallback) is available.
_FALLBACKS = ("claude-opus-5", "claude-fable-5-1", "claude-fable-5-2", "claude-sonnet-5-5")
FALLBACK_BETA = "server-side-fallback-2026-07-01"

PRICING: dict[str, Pricing] = {
    "claude-haiku-5-5": Pricing(0.10, 0.50),
    "claude-sonnet-5-5": Pricing(2.0, 10.0),
    "claude-opus-5-5": Pricing(4.0, 20.0),
    "claude-fable-5-2": Pricing(8.0, 40.0),
}

REFUSAL_TEXT = "I'm afraid that's not something I can help with."


def _supports(model: str, prefixes: tuple[str, ...]) -> bool:
    return any(model.startswith(p) for p in prefixes)


class AnthropicProvider:
    capabilities = {"tools", "streaming", "vision", "long_context"}

    def __init__(
        self,
        id: str,
        model: str,
        api_key: Callable[[], str],
        *,
        effort: str | None = "low",
        max_tokens: int = 16000,
        pricing: Pricing | None = None,
        base_url: str | None = None,
    ) -> None:
        self.id = id
        self.model = model
        self.effort = effort
        self.max_tokens = max_tokens
        self.pricing = pricing or PRICING.get(model, Pricing())
        self._api_key = api_key
        self._base_url = base_url
        self._client: anthropic.AsyncAnthropic | None = None
        self._client_key = ""

    def _get_client(self) -> anthropic.AsyncAnthropic:
        key = self._api_key()
        if not key:
            raise ProviderError(f"{self.id}: no ANTHROPIC_API_KEY")
        if self._client is None or key != self._client_key:
            self._client = anthropic.AsyncAnthropic(
                api_key=key, base_url=self._base_url, max_retries=1, timeout=60
            )
            self._client_key = key
        return self._client

    # ------------------------------------------------------------------ mapping

    def _convert(self, messages: list[Message]) -> tuple[str, list[dict[str, Any]]]:
        system = ""
        out: list[dict[str, Any]] = []
        mid_system = _supports(self.model, _MID_SYSTEM)
        pending_results: list[dict[str, Any]] = []

        def flush_results() -> None:
            if pending_results:
                out.append({"role": "user", "content": list(pending_results)})
                pending_results.clear()

        for m in messages:
            if m.role != "tool":
                flush_results()
            if m.role == "system":
                if not system and not out:
                    system = m.content  # the frozen persona prompt
                elif mid_system and out:
                    out.append({"role": "system", "content": m.content})
                else:  # older models: fold context into the next user turn
                    out.append({"role": "user", "content": f"<context>\n{m.content}\n</context>"})
            elif m.role == "user":
                out.append({"role": "user", "content": m.content})
            elif m.role == "assistant":
                if m.raw is not None and m.raw_provider == self.id:
                    out.append({"role": "assistant", "content": m.raw})
                    continue
                blocks: list[dict[str, Any]] = []
                if m.content:
                    blocks.append({"type": "text", "text": m.content})
                blocks += [
                    {"type": "tool_use", "id": c.id, "name": c.name, "input": c.args}
                    for c in m.tool_calls
                ]
                out.append({"role": "assistant", "content": blocks or m.content or "…"})
            elif m.role == "tool":
                pending_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": m.tool_call_id,
                        "content": m.content,
                        "is_error": m.is_error,
                    }
                )
        flush_results()
        return system, out

    @staticmethod
    def _tools(tools: list[ToolSpec]) -> list[dict[str, Any]]:
        # Sorted for a stable prefix (prompt cache). Inputs stream eagerly; the tool registry
        # validates every input against its schema before running anything.
        return [
            {
                "name": t.name,
                "description": t.description,
                "input_schema": t.parameters,
                "eager_input_streaming": True,
            }
            for t in sorted(tools, key=lambda t: t.name)
        ]

    # ------------------------------------------------------------------ run

    async def run(
        self, messages: list[Message], tools: list[ToolSpec], *, stream: bool = True
    ) -> AsyncIterator[AgentEvent]:
        client = self._get_client()
        system, msgs = self._convert(messages)
        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "messages": msgs,
            "cache_control": {"type": "ephemeral"},
        }
        if system:
            kwargs["system"] = system
        if tools:
            kwargs["tools"] = self._tools(tools)
        if self.effort:
            kwargs["output_config"] = {"effort": self.effort}
        if _supports(self.model, _FALLBACKS):
            kwargs["betas"] = [FALLBACK_BETA]
            kwargs["fallbacks"] = "default"
        t0 = time.monotonic()
        emitted = False
        try:
            async with client.beta.messages.stream(**kwargs) as s:
                async for event in s:
                    if event.type == "text" and event.text:
                        emitted = True
                        yield TextDelta(event.text)
                final = await s.get_final_message()
        except ValueError as e:
            # Tool input JSON the SDK could not parse at all (eager input streaming).
            raise ProviderError(f"{self.id}: malformed tool input: {e}") from e
        except anthropic.APIConnectionError as e:
            raise ProviderError(f"{self.id}: connection error: {e}") from e
        except anthropic.RateLimitError as e:
            raise ProviderError(f"{self.id}: rate limited") from e
        except anthropic.AuthenticationError as e:
            raise ProviderError(f"{self.id}: authentication failed") from e
        except anthropic.APIStatusError as e:
            raise ProviderError(f"{self.id}: HTTP {e.status_code}: {e.message}") from e

        text = "".join(b.text for b in final.content if b.type == "text")
        calls = [
            ToolCall(b.id, b.name, b.input if isinstance(b.input, dict) else {})
            for b in final.content
            if b.type == "tool_use"
        ]
        stop = final.stop_reason or "end_turn"
        if stop == "refusal":
            log.info("%s refused (%s)", self.id, getattr(final.stop_details, "category", None))
            calls = []
            if not emitted:
                yield TextDelta(REFUSAL_TEXT)
                text = REFUSAL_TEXT
        elif stop == "max_tokens" and calls:
            log.warning("%s hit max_tokens mid tool call; not running it", self.id)
            calls = []
        for c in calls:
            yield ToolCallRequested(c)
        u = final.usage
        inp = (
            (u.input_tokens or 0)
            + (getattr(u, "cache_read_input_tokens", 0) or 0)
            + (getattr(u, "cache_creation_input_tokens", 0) or 0)
        )
        cost = (
            self.pricing.cost(u.input_tokens or 0, u.output_tokens or 0)
            + (
                (getattr(u, "cache_read_input_tokens", 0) or 0) * self.pricing.input_per_mtok * 0.1
                + (getattr(u, "cache_creation_input_tokens", 0) or 0)
                * self.pricing.input_per_mtok
                * 1.25
            )
            / 1_000_000
        )
        log.debug("%s done in %.2fs (%s)", self.id, time.monotonic() - t0, stop)
        yield Completed(
            message=Message(
                role="assistant",
                content=text,
                tool_calls=calls,
                raw=final.content,
                raw_provider=self.id,
            ),
            stop_reason=stop,
            input_tokens=inp,
            output_tokens=u.output_tokens or 0,
            cost_usd=cost,
            model=final.model,
        )

    async def health(self) -> bool:
        try:
            client = self._get_client()
            await client.with_options(timeout=5, max_retries=0).models.retrieve(self.model)
            return True
        except (ProviderError, anthropic.APIError) as e:
            log.info("%s unhealthy: %s", self.id, e)
            return False
