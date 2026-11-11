"""The agent: route → provider chain → tool-use loop → streamed reply.

A plain loop over the :class:`Provider` interface — no framework in between. Providers are tried in
route order; we only fail over to the next one if the current one fails before saying anything
in this step (never mid-sentence).
"""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from jarvis_protocol import (
    AgentConfirmRequest,
    AgentThinking,
    AgentToolCall,
    AgentToolResult,
    SideEffect,
    State,
)

from ..providers.base import (
    Completed,
    Message,
    Provider,
    ProviderError,
    TextDelta,
    ToolCall,
    ToolCallRequested,
)
from ..providers.router import CallStat, Route, Router, classify
from ..tools.registry import ToolContext, ToolOutcome, ToolRegistry
from .confirm import DECLINED, PendingConfirmation, classify_answer, readback_question
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

_PAUSED = "\x00paused"  # sentinel yielded internally when a confirmation is pending


@dataclass
class _Run:
    """Mutable state of one agent run (a turn, or the resumption of a confirmed one)."""

    turn: Turn
    ctx: TurnContext
    tool_ctx: ToolContext
    route: Route = "chat"
    chain: list[Provider] = field(default_factory=list)
    preferred: str = ""
    msgs: list[Message] = field(default_factory=list)
    reply: list[str] = field(default_factory=list)


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
        self.pending: PendingConfirmation | None = None
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
        run = _Run(
            turn=turn, ctx=ctx, tool_ctx=ToolContext(turn_id=turn.id, user_text=turn.user_text)
        )
        pending, self.pending = self.pending, None
        if pending and not pending.expired:
            answer = classify_answer(turn.user_text)
            if answer == "no":
                ctx.publish(
                    AgentToolResult(
                        call_id=pending.call.id,
                        tool=pending.call.name,
                        ok=False,
                        summary="declined by user",
                        turn_id=turn.id,
                    )
                )
                turn.provider = "confirmation"
                yield DECLINED
                self.memory.add(turn.user_text, DECLINED, summarizer=self._summarize)
                return
            if answer == "yes":
                async for d in self._resume(pending, run):
                    yield d
                self._remember(run)
                return
            # Anything else cancels the pending action and is handled as a new request.
            log.info("pending confirmation dropped: %r is neither yes nor no", turn.user_text)

        run.route = classify(turn.user_text, self.router.cfg)
        run.chain = self.router.chain(run.route)
        if not run.chain:
            turn.provider = "none"
            yield NO_LLM
            return
        run.preferred = run.chain[0].id
        run.msgs = [
            Message("system", self.persona()),
            *self.memory.messages(),
            Message("user", turn.user_text),
            Message("system", self.context_note()),
        ]
        async for d in self._loop(run):
            yield d
        self._remember(run)

    def _remember(self, run: _Run) -> None:
        text = "".join(run.reply).strip()
        if text:
            self.memory.add(run.turn.user_text, text, summarizer=self._summarize)

    async def _resume(self, pending: PendingConfirmation, run: _Run) -> AsyncIterator[str]:
        """The user said yes: run the confirmed call, then let the model continue."""
        provider = self.router.providers.get(pending.provider_id)
        rest = [p for p in self.router.chain(pending.route) if p.id != pending.provider_id]
        run.chain = ([provider] if provider else []) + rest
        run.route, run.preferred = pending.route, pending.provider_id
        run.msgs = pending.messages
        outcome = await self._run_tool(
            pending.call, run.turn, run.tool_ctx, run.ctx, confirmed=True
        )
        run.msgs.append(
            Message(
                "tool",
                outcome.content,
                tool_call_id=pending.call.id,
                tool_name=pending.call.name,
                is_error=not outcome.ok,
            )
        )
        paused = False
        async for d in self._process_calls(pending.remaining, run):
            if d is _PAUSED:
                paused = True
                continue
            yield d
        if paused or not run.chain:
            return
        run.ctx.set_state(State.THINKING, "after confirmed tool")
        async for d in self._loop(run):
            yield d

    async def _loop(self, run: _Run) -> AsyncIterator[str]:
        turn, ctx = run.turn, run.ctx
        specs = self.tools.specs()
        for _step in range(self.config.agent.max_steps):
            completed: Completed | None = None
            calls: list[ToolCall] = []
            for provider in run.chain:
                t0 = time.monotonic()
                first: float | None = None
                emitted = False
                ctx.publish(
                    AgentThinking(
                        provider=provider.id, model=getattr(provider, "model", ""), turn_id=turn.id
                    )
                )
                try:
                    async for ev in provider.run(run.msgs, specs, stream=True):
                        if isinstance(ev, TextDelta):
                            if first is None:
                                first = time.monotonic() - t0
                                if self._should_announce_offline(
                                    provider, run.preferred, run.reply
                                ):
                                    run.reply.append(OFFLINE_NOTE)
                                    yield OFFLINE_NOTE
                            emitted = True
                            run.reply.append(ev.text)
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
                            run.route,
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
                        run.route,
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
                run.chain = [provider]  # stick with the provider that worked for this turn
                break
            if completed is None:
                if not run.reply:
                    turn.provider = "none"
                    yield ALL_FAILED
                return

            run.msgs.append(completed.message)
            if not calls:
                return
            ctx.set_state(State.TOOL, "tool call")
            async for d in self._process_calls(calls, run):
                if d is _PAUSED:
                    return  # waiting for the user's yes/no
                yield d
            ctx.set_state(State.THINKING, "after tool")
            if run.reply and not run.reply[-1].endswith((" ", "\n")):
                run.reply.append(" ")
                yield " "
        run.reply.append(TOO_MANY_STEPS)
        yield TOO_MANY_STEPS

    async def _process_calls(self, calls: list[ToolCall], run: _Run) -> AsyncIterator[str]:
        """Execute calls in order; stop at the first irreversible one and ask for confirmation.

        Yields spoken text, or the ``_PAUSED`` sentinel once a confirmation is pending.
        """
        for i, call in enumerate(calls):
            tool = self.tools.get(call.name)
            if tool is not None and tool.side_effect == SideEffect.IRREVERSIBLE:
                _, args = self.tools.validate(call)
                if isinstance(args, dict):  # invalid args fall through and fail normally
                    question = readback_question(tool.readback(args))
                    self.pending = PendingConfirmation(
                        call=call,
                        readback=question,
                        messages=run.msgs,
                        remaining=calls[i + 1 :],
                        provider_id=run.chain[0].id if run.chain else "",
                        route=run.route,
                        reply_so_far="".join(run.reply),
                    )
                    run.ctx.publish(
                        AgentConfirmRequest(
                            call_id=call.id, tool=call.name, prompt=question, turn_id=run.turn.id
                        )
                    )
                    sep = " " if run.reply and not run.reply[-1].endswith(" ") else ""
                    run.reply.append(sep + question)
                    yield sep + question
                    yield _PAUSED
                    return
            outcome = await self._run_tool(call, run.turn, run.tool_ctx, run.ctx)
            run.msgs.append(
                Message(
                    "tool",
                    outcome.content,
                    tool_call_id=call.id,
                    tool_name=call.name,
                    is_error=not outcome.ok,
                )
            )

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
        self,
        call: ToolCall,
        turn: Turn,
        tool_ctx: ToolContext,
        ctx: TurnContext,
        *,
        confirmed: bool = False,
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
        if tool is not None and tool.side_effect == SideEffect.IRREVERSIBLE and not confirmed:
            # Defence in depth: _process_calls never gets here unconfirmed.
            outcome = ToolOutcome(
                False, "Not executed: needs the user's spoken confirmation.", "blocked: unconfirmed"
            )
        else:
            outcome = await self.tools.execute(call, tool_ctx)
        tool_ctx.extras["confirmed"] = confirmed
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
