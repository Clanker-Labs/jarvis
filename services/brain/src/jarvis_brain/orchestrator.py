"""Conversation orchestrator: owns the state machine and the lifecycle of each turn.

Inputs: audio-node events/audio and UI commands. Outputs: state changes, transcripts, speech.
The thinking itself is delegated to a :class:`Responder` (echo in phase 2, the agent from phase 3).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
import uuid
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Protocol

from jarvis_protocol import (
    AgentDone,
    AgentThinking,
    AgentToken,
    BargeIn,
    ErrorEvent,
    Event,
    ListenEnd,
    ListenFinal,
    ListenLevel,
    ListenPartial,
    ListenStart,
    NodeCommand,
    PlaybackDone,
    State,
    StateChange,
    StateMachine,
    StatusUpdate,
    TtsAudioLevel,
    UiCommand,
    UiConfig,
    UiSnapshot,
    WakeDetected,
)

from .bus import EventBus
from .config import ConfigStore
from .hub import Hub
from .status import StatusTracker
from .voice.speaker import Speaker, one
from .voice.stt import ChainSession, STTChain
from .voice.text import SentenceSplitter

log = logging.getLogger(__name__)
S = State


@dataclass
class Turn:
    id: str
    user_text: str = ""
    follow_up: bool = False
    timings: dict[str, float] = field(default_factory=dict)
    reply: str = ""
    provider: str = ""
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    tool_calls: list[dict] = field(default_factory=list)
    error: str = ""

    def mark(self, name: str) -> None:
        self.timings.setdefault(name, time.time())


class TurnContext(Protocol):
    def set_state(self, state: State, reason: str = "") -> None: ...
    def publish(self, event: Event) -> None: ...


class Responder(Protocol):
    def respond(self, turn: Turn, ctx: TurnContext) -> AsyncIterator[str]:
        """Yield reply text deltas. Publish tool/thinking events via ``ctx``."""
        ...


class EchoResponder:
    """Phase 2 placeholder: repeat what was heard."""

    async def respond(self, turn: Turn, ctx: TurnContext) -> AsyncIterator[str]:
        turn.provider = "echo"
        yield f"You said: {turn.user_text}"


class Orchestrator:
    def __init__(
        self,
        bus: EventBus,
        hub: Hub,
        config: ConfigStore,
        stt: STTChain,
        speaker: Speaker,
        responder: Responder,
        status: StatusTracker,
    ) -> None:
        self.bus = bus
        self.hub = hub
        self.config = config
        self.stt = stt
        self.speaker = speaker
        self.responder = responder
        self.status = status
        self.sm = StateMachine()
        self.sm.subscribe(self._on_transition)
        self.capture: ChainSession | None = None
        self.capture_id: str | None = None
        self.turn: Turn | None = None
        self.turn_task: asyncio.Task[None] | None = None
        self._playback_done: asyncio.Event = asyncio.Event()
        self._awaiting_stream: int | None = None
        self._error_timer: asyncio.TimerHandle | None = None
        self.on_turn_done: list[Callable[[Turn], None]] = []

    # ------------------------------------------------------------------ state

    def _on_transition(self, previous: State, state: State, reason: str) -> None:
        log.info("state %s → %s%s", previous, state, f" ({reason})" if reason else "")
        self.bus.publish(StateChange(state=state, previous=previous, reason=reason))

    def go(self, target: State, reason: str = "") -> None:
        """Transition, routing through intermediate states when needed; never raises."""
        if target == self.sm.state:
            return
        if self.sm.can(target):
            self.sm.to(target, reason)
        elif target in (S.LISTENING, S.WAKE, S.IDLE):
            self.sm.force(target, reason)
        else:
            log.warning("unexpected transition %s → %s (%s)", self.sm.state, target, reason)
            self.sm.force(target, reason)

    def snapshot(self) -> UiSnapshot:
        a = self.config.appearance
        return UiSnapshot(
            state=self.sm.state,
            config=UiConfig(
                colors=a.colors,
                quality=a.quality,
                night_start=a.night_start,
                night_end=a.night_end,
                night_dim=a.night_dim,
                screen_sleep_min=a.screen_sleep_min,
                show_cards=a.show_cards,
            ),
            recent=self.status.recent,
            now=self.status.now,
        )

    # ------------------------------------------------------------------ node input

    async def on_node_event(self, ev: Event) -> None:
        match ev:
            case WakeDetected():
                self.bus.publish(ev)
                if self.sm.busy:
                    await self._interrupt("wake word")
                self.go(S.WAKE, "wake word")
            case ListenStart():
                await self._open_capture(ev)
            case ListenLevel() | TtsAudioLevel():
                self.bus.publish(ev)
            case ListenEnd():
                await self._close_capture(ev)
            case BargeIn():
                self.bus.publish(ev)
                await self._interrupt("barge-in")
            case PlaybackDone():
                if ev.stream_id == self._awaiting_stream:
                    self._playback_done.set()
            case StatusUpdate():
                self.status.update(mic=ev.mic, muted=ev.muted)
            case _:
                log.debug("ignored node event %s", ev.type)

    async def on_node_audio(self, stream_id: int, pcm: bytes) -> None:
        if self.capture is not None and str(stream_id) == self.capture_id:
            await self.capture.feed(pcm)

    async def _open_capture(self, ev: ListenStart) -> None:
        if self.sm.busy:
            await self._interrupt("new utterance")
        if self.capture:
            await self.capture.aclose()
        self.turn = Turn(id=uuid.uuid4().hex[:12], follow_up=ev.follow_up)
        self.turn.mark("listen_start")
        self.capture_id = ev.turn_id
        turn_id = self.turn.id

        async def partial(text: str) -> None:
            if self.turn and self.turn.id == turn_id:
                self.turn.mark("first_partial")
                self.bus.publish(ListenPartial(text=text, turn_id=turn_id))

        self.capture = self.stt.open(partial)
        self.bus.publish(ListenStart(follow_up=ev.follow_up, turn_id=turn_id))
        self.go(S.LISTENING, "follow-up" if ev.follow_up else "listening")

    async def _close_capture(self, ev: ListenEnd) -> None:
        if ev.reason == "timeout" and self.capture is None:
            # Follow-up window (or post-wake wait) expired without speech.
            if self.sm.state in (S.FOLLOW_UP, S.WAKE, S.LISTENING):
                self.go(S.IDLE, "no speech")
            return
        session, self.capture = self.capture, None
        turn = self.turn
        if session is None or turn is None:
            return
        if ev.reason in ("cancelled", "timeout"):
            await session.aclose()
            self.go(S.IDLE, ev.reason)
            return
        turn.mark("listen_end")
        self.turn_task = asyncio.create_task(self._run_turn(turn, session))

    # ------------------------------------------------------------------ UI input

    async def on_ui_command(self, cmd: UiCommand) -> None:
        match cmd.command:
            case "push_to_talk":
                if self.sm.busy:
                    await self._interrupt("tap")
                if not await self.hub.to_node(NodeCommand(command="listen.open")):
                    self._error("no_audio_node", "The microphone isn't connected.")
            case "barge_in":
                self.bus.publish(BargeIn(source="touch"))
                await self._interrupt("tap")
                await self.hub.to_node(NodeCommand(command="listen.open"))
            case "mute" | "unmute":
                await self.hub.to_node(NodeCommand(command=cmd.command))
                self.status.update(muted=cmd.command == "mute")
            case "screen_wake":
                pass

    # ------------------------------------------------------------------ turns

    async def _interrupt(self, reason: str) -> None:
        """Stop speaking/thinking now. The node has already silenced itself for voice barge-in."""
        await self.hub.to_node(NodeCommand(command="playback.stop"))
        task, self.turn_task = self.turn_task, None
        if task and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        log.info("interrupted (%s)", reason)

    async def _run_turn(self, turn: Turn, session: ChainSession) -> None:
        try:
            try:
                result = await session.finish()
            finally:
                await session.aclose()
            turn.mark("stt_final")
            self.status.voice_used(stt_fallback=result.fallback)
            text = result.text.strip()
            self.bus.publish(ListenFinal(text=text, stt_provider=result.engine, turn_id=turn.id))
            if not text:
                self.go(S.IDLE, "nothing heard")
                return
            turn.user_text = text
            self.go(S.THINKING, "thinking")
            self.bus.publish(AgentThinking(turn_id=turn.id))
            await self._respond_and_speak(turn)
            await self._follow_up(turn)
        except asyncio.CancelledError:
            self._finish(turn, interrupted=True)
            raise
        except Exception as e:
            log.exception("turn failed")
            self._finish(turn, error=str(e))
            self._error("turn_failed", str(e))

    async def _respond_and_speak(self, turn: Turn) -> None:
        splitter = SentenceSplitter()
        parts: list[str] = []
        ctx = _Ctx(self, turn)

        async def sentences() -> AsyncIterator[str]:
            async for delta in self.responder.respond(turn, ctx):
                if not delta:
                    continue
                turn.mark("first_token")
                parts.append(delta)
                self.bus.publish(AgentToken(text=delta, turn_id=turn.id))
                for s in splitter.push(delta):
                    yield s
            for s in splitter.flush():
                yield s

        def first_audio() -> None:
            turn.mark("first_audio")
            self.go(S.SPEAKING, "speaking")

        result = await self.speaker.speak(sentences(), turn_id=turn.id, on_first_audio=first_audio)
        turn.reply = "".join(parts).strip()
        self.status.voice_used(tts_fallback=result.fallback)
        self._finish(turn)
        if result.stream_id is not None:
            # Wait until the node has actually played it (plus slack), then open follow-up.
            self._awaiting_stream = result.stream_id
            self._playback_done.clear()
            with contextlib.suppress(TimeoutError):
                remaining = result.audio_s - (time.time() - (result.first_audio_at or time.time()))
                await asyncio.wait_for(self._playback_done.wait(), timeout=max(0, remaining) + 5)
            self._awaiting_stream = None

    async def _follow_up(self, turn: Turn) -> None:
        window = self.config.voice.follow_up_s
        if window <= 0 or not self.hub.node_connected:
            self.go(S.IDLE, "done")
            return
        self.go(S.FOLLOW_UP, "follow-up window")
        await self.hub.to_node(NodeCommand(command="listen.open", timeout_s=window, follow_up=True))

    def _finish(self, turn: Turn, *, interrupted: bool = False, error: str | None = None) -> None:
        if "done" in turn.timings:
            return
        turn.mark("done")
        if error:
            turn.error = error
        self.bus.publish(
            AgentDone(
                text=turn.reply,
                provider=turn.provider,
                model=turn.model,
                input_tokens=turn.input_tokens,
                output_tokens=turn.output_tokens,
                cost_usd=round(turn.cost_usd, 6),
                interrupted=interrupted,
                turn_id=turn.id,
            )
        )
        if turn.user_text and turn.reply and not interrupted:
            self.status.add_recent(turn.user_text, turn.reply)
        for cb in self.on_turn_done:
            try:
                cb(turn)
            except Exception:
                log.exception("turn-done hook failed")

    def _error(self, code: str, message: str) -> None:
        self.bus.publish(ErrorEvent(code=code, message=message))
        self.go(S.ERROR, code)
        loop = asyncio.get_running_loop()
        if self._error_timer:
            self._error_timer.cancel()
        self._error_timer = loop.call_later(
            2.0, lambda: self.sm.state is S.ERROR and self.go(S.IDLE, "recovered")
        )

    async def say(self, text: str) -> None:
        """Speak an out-of-turn announcement (reminders, errors)."""
        await self.speaker.speak(one(text), turn_id=None)

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        await self._interrupt("shutdown")
        if self.capture:
            await self.capture.aclose()


class _Ctx:
    def __init__(self, orch: Orchestrator, turn: Turn) -> None:
        self.orch = orch
        self.turn = turn

    def set_state(self, state: State, reason: str = "") -> None:
        if self.orch.sm.state is not S.SPEAKING or state is not S.THINKING:
            self.orch.go(state, reason)

    def publish(self, event: Event) -> None:
        self.orch.bus.publish(event)
