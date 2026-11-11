"""The agent: route → provider chain → tool-use loop → streamed reply.

A plain loop over the :class:`Provider` interface — no framework in between. Providers are tried in
route order; we only fail over to the next one if the current one fails before saying anything
in this step (never mid-sentence).
"""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from jarvis_protocol import AgentThinking, AgentToolCall, AgentToolResult, State

from ..providers.base import (
    Completed,
    Message,
    ProviderError,
    TextDelta,
    ToolCall,
    ToolCallRequested,
)
from ..providers.router import CallStat, Router, classify
from ..tools.registry import ToolContext, ToolOutcome, ToolRegistry
from .memory import ShortTermMemory

if TYPE_CHECKING:
    from ..config import ConfigStore
    from ..orchestrator import Turn, TurnContext
    from ..status import StatusTracker

log = logging.getLogger(__name__)

PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "jarvis.md"

NO_LLM = (
    "My thinking circuits aren't connected to a language model yet. Set one up in the admin panel."
)
ALL_FAILED = "I can't reach any of my language models at the moment. Do try again shortly."
TOO_MANY_STEPS = "That's taking more steps than I'd like. Shall I try a different way?"
OFFLINE_NOTE = "I'm running on the local model for now, so bear with me. "

SUMMARY_PROMPT = (
    "Summarise this earlier part of a spoken conversation between a user and their assistant, "
    "Jarvis, in at most five short sentences. Keep facts, names, decisions, open questions and "
    "anything the user asked to remember. Plain text only."
)

# Hook for phase 5: decides whether a tool call may run now (confirmation flow).
ToolGuard = Callable[[ToolCall, "Turn"], Awaitable[ToolOutcome | None]]


class Agent:
    def __init__(
        self,
        router: Router,
        tools: ToolRegistry,
        config: ConfigStore,
        status: StatusTracker,
    ) -> None:
        self.router = router
        self.tools = tools
        self.config = config
        self.status = status
        a = config.agent
        self.memory = ShortTermMemory(
            window=a.history_turns, session_timeout_s=a.session_timeout_min * 60
        )
        self.guard: ToolGuard | None = None
        self._persona_cache: tuple[float, str, str] | None = None
        self._degraded_announced = False

    # ------------------------------------------------------------------ prompt

    def persona(self) -> str:
        """The frozen system prompt (re-read only if the file or the user's name changes)."""
        mtime = PROMPT_PATH.stat().st_mtime
        name = self.config.agent.user_name
        if self._persona_cache and self._persona_cache[:2] == (mtime, name):
            return self._persona_cache[2]
        text = PROMPT_PATH.read_text().replace("{user_name_or_owner}", name or "its owner")
        self._persona_cache = (mtime, name, text)
        return text

    def context_note(self) -> str:
        now = datetime.now().astimezone()
        parts = [f"Now: {now.strftime('%A %-d %B %Y, %H:%M')} ({now.tzname()})."]
        loc = self.config.location
        if loc.name:
            parts.append(f"Location: {loc.name}.")
        if self.status.weather:
            w = self.status.weather
            parts.append(f"Weather now: {round(w.temperature_c)}°, {w.condition.lower()}.")
        if self.config.agent.user_name:
            parts.append(f"You are talking to {self.config.agent.user_name}.")
        if self.memory.summary:
            parts.append(f"Earlier in this conversation: {self.memory.summary}")
        return " ".join(parts)

    # ------------------------------------------------------------------ main loop

    async def respond(self, turn: Turn, ctx: TurnContext) -> AsyncIterator[str]:
        route = classify(turn.user_text, self.router.cfg)
        chain = self.router.chain(route)
        if not chain:
            turn.provider = "none"
            yield NO_LLM
            return
        preferred = chain[0].id
        msgs: list[Message] = [
            Message("system", self.persona()),
            *self.memory.messages(),
            Message("user", turn.user_text),
            Message("system", self.context_note()),
        ]
        specs = self.tools.specs()
        tool_ctx = ToolContext(turn_id=turn.id, user_text=turn.user_text)
        reply: list[str] = []
        max_steps = self.config.agent.max_steps

        for _step in range(max_steps):
            completed: Completed | None = None
            calls: list[ToolCall] = []
            for provider in chain:
                t0 = time.monotonic()
                first: float | None = None
                emitted = False
                ctx.publish(
                    AgentThinking(
                        provider=provider.id, model=getattr(provider, "model", ""), turn_id=turn.id
                    )
                )
                try:
                    async for ev in provider.run(msgs, specs, stream=True):
                        if isinstance(ev, TextDelta):
                            if first is None:
                                first = time.monotonic() - t0
                                if self._should_announce_offline(provider, preferred, reply):
                                    reply.append(OFFLINE_NOTE)
                                    yield OFFLINE_NOTE
                            emitted = True
                            reply.append(ev.text)
                            yield ev.text
                        elif isinstance(ev, ToolCallRequested):
                            calls.append(ev.call)
                        elif isinstance(ev, Completed):
                            completed = ev
                except ProviderError as e:
                    self.router.record(
                        CallStat(
                            time.time(),
                            provider.id,
                            getattr(provider, "model", ""),
                            route,
                            False,
                            first,
                            time.monotonic() - t0,
                            error=str(e),
                        )
                    )
                    log.warning("provider %s failed: %s", provider.id, e)
                    if emitted:
                        break  # can't take back what was already said
                    continue
                assert completed is not None
                self.router.health[provider.id] = True
                self.router.record(
                    CallStat(
                        time.time(),
                        provider.id,
                        completed.model,
                        route,
                        True,
                        first,
                        time.monotonic() - t0,
                        completed.input_tokens,
                        completed.output_tokens,
                        completed.cost_usd,
                    )
                )
                turn.provider, turn.model = provider.id, completed.model
                turn.input_tokens += completed.input_tokens
                turn.output_tokens += completed.output_tokens
                turn.cost_usd += completed.cost_usd
                chain = [provider]  # stick with the provider that worked for this turn
                break
            if completed is None:
                if not reply:
                    turn.provider = "none"
                    yield ALL_FAILED
                break

            msgs.append(completed.message)
            if not calls:
                break
            ctx.set_state(State.TOOL, "tool call")
            for call in calls:
                outcome = await self._run_tool(call, turn, tool_ctx, ctx)
                msgs.append(
                    Message(
                        "tool",
                        outcome.content,
                        tool_call_id=call.id,
                        tool_name=call.name,
                        is_error=not outcome.ok,
                    )
                )
            ctx.set_state(State.THINKING, "after tool")
            if reply and not reply[-1].endswith((" ", "\n")):
                reply.append(" ")
                yield " "
        else:
            reply.append(TOO_MANY_STEPS)
            yield TOO_MANY_STEPS

        text = "".join(reply).strip()
        if text:
            self.memory.add(turn.user_text, text, summarizer=self._summarize)

    def _should_announce_offline(self, provider, preferred: str, reply: list[str]) -> bool:  # type: ignore[no-untyped-def]
        degraded = provider.id != preferred and getattr(provider, "local", False)
        if not degraded:
            self._degraded_announced = False
            return False
        if self._degraded_announced or reply:
            return False
        self._degraded_announced = True
        return True

    async def _run_tool(
        self, call: ToolCall, turn: Turn, tool_ctx: ToolContext, ctx: TurnContext
    ) -> ToolOutcome:
        tool = self.tools.get(call.name)
        ctx.publish(
            AgentToolCall(
                call_id=call.id,
                tool=call.name,
                args=call.args,
                side_effect=tool.side_effect if tool else "none",
                label=(tool.label if tool else "") or call.name,
                icon=tool.icon if tool else "tool",
                turn_id=turn.id,
            )
        )
        outcome: ToolOutcome | None = None
        if self.guard:
            outcome = await self.guard(call, turn)
        if outcome is None:
            outcome = await self.tools.execute(call, tool_ctx)
        turn.tool_calls.append(
            {
                "tool": call.name,
                "args": call.args,
                "ok": outcome.ok,
                "summary": outcome.summary,
                "duration_s": round(outcome.duration_s, 3),
            }
        )
        ctx.publish(
            AgentToolResult(
                call_id=call.id,
                tool=call.name,
                ok=outcome.ok,
                summary=outcome.summary,
                turn_id=turn.id,
            )
        )
        return outcome

    async def _summarize(self, previous: str, turns: list[tuple[str, str]]) -> str:
        transcript = "\n".join(f"User: {u}\nJarvis: {a}" for u, a in turns)
        if previous:
            transcript = f"(Summary so far: {previous})\n{transcript}"
        msgs = [Message("system", SUMMARY_PROMPT), Message("user", transcript)]
        for provider in self.router.chain("chat"):
            try:
                out = []
                async for ev in provider.run(msgs, [], stream=True):
                    if isinstance(ev, TextDelta):
                        out.append(ev.text)
                return "".join(out)
            except ProviderError:
                continue
        raise RuntimeError("no provider available for summarising")
