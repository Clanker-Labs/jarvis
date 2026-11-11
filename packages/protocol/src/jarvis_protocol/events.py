"""Typed event protocol for JARVIS.

Every message on every WebSocket is one JSON object matching :data:`Event`, discriminated by
its ``type`` field. Binary WebSocket frames carry audio only (see :mod:`jarvis_protocol.audio`).

This module is the single source of truth: the TypeScript types in ``packages/protocol/ts`` are
generated from it (``make protocol``) and CI fails if they drift.
"""

from __future__ import annotations

import time
import uuid
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

PROTOCOL_VERSION = 1


class State(StrEnum):
    IDLE = "IDLE"
    WAKE = "WAKE"
    LISTENING = "LISTENING"
    THINKING = "THINKING"
    TOOL = "TOOL"
    SPEAKING = "SPEAKING"
    FOLLOW_UP = "FOLLOW_UP"
    ERROR = "ERROR"


class SideEffect(StrEnum):
    NONE = "none"
    REVERSIBLE = "reversible"
    IRREVERSIBLE = "irreversible"


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


class _Event(BaseModel):
    # Every field is always present on the wire, so mark defaulted fields required in the
    # serialization schema (keeps the generated TS discriminated union sound).
    model_config = ConfigDict(
        extra="forbid", frozen=True, json_schema_serialization_defaults_required=True
    )

    id: str = Field(default_factory=_new_id, description="Unique event id")
    ts: float = Field(default_factory=time.time, description="Unix time (seconds) at creation")
    turn_id: str | None = Field(default=None, description="Conversation turn this event belongs to")


# --------------------------------------------------------------------------- handshake / status


class Hello(_Event):
    """First message a client sends after connecting."""

    type: Literal["hello"] = "hello"
    role: Literal["ui", "audio-node", "admin"]
    protocol: int = PROTOCOL_VERSION
    client_version: str = "0.0.0"


class Weather(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, json_schema_serialization_defaults_required=True
    )

    temperature_c: float
    condition: str
    icon: str = Field(description="Short icon key, e.g. 'clear', 'cloud', 'rain', 'snow', 'storm'")
    location: str = ""


class StatusUpdate(_Event):
    """Health of the moving parts, shown as connection dots on the UI."""

    type: Literal["status.update"] = "status.update"
    brain: bool = True
    llm: bool = False
    internet: bool = False
    mic: bool = False
    offline_voice: bool = Field(default=False, description="STT/TTS fell back to local engines")
    muted: bool = False
    weather: Weather | None = None


class StateChange(_Event):
    type: Literal["state.change"] = "state.change"
    state: State
    previous: State
    reason: str = ""


class ErrorEvent(_Event):
    type: Literal["error"] = "error"
    code: str
    message: str
    recoverable: bool = True


# --------------------------------------------------------------------------- wake / listening


class WakeDetected(_Event):
    type: Literal["wake.detected"] = "wake.detected"
    source: Literal["voice", "touch"] = "voice"
    model: str = ""
    score: float = 0.0


class ListenStart(_Event):
    type: Literal["listen.start"] = "listen.start"
    follow_up: bool = False


class ListenLevel(_Event):
    """Microphone RMS level, 0..1, ~30 Hz while listening."""

    type: Literal["listen.level"] = "listen.level"
    level: float = Field(ge=0.0, le=1.0)


class ListenEnd(_Event):
    """Audio node finished capturing an utterance (endpointing decided)."""

    type: Literal["listen.end"] = "listen.end"
    reason: Literal["silence", "max_duration", "timeout", "cancelled"]
    duration_s: float = 0.0


class ListenPartial(_Event):
    type: Literal["listen.partial"] = "listen.partial"
    text: str


class ListenFinal(_Event):
    type: Literal["listen.final"] = "listen.final"
    text: str
    stt_provider: str = ""


# --------------------------------------------------------------------------- agent


class AgentThinking(_Event):
    type: Literal["agent.thinking"] = "agent.thinking"
    provider: str = ""
    model: str = ""


class AgentToolCall(_Event):
    type: Literal["agent.tool_call"] = "agent.tool_call"
    call_id: str
    tool: str
    args: dict[str, Any] = Field(default_factory=dict)
    side_effect: SideEffect = SideEffect.NONE
    label: str = Field(default="", description="Short human label for the UI chip")
    icon: str = Field(default="tool", description="Icon key for the UI chip")


class AgentToolResult(_Event):
    type: Literal["agent.tool_result"] = "agent.tool_result"
    call_id: str
    tool: str
    ok: bool
    summary: str = ""


class AgentConfirmRequest(_Event):
    """An irreversible tool call is waiting for a spoken yes/no."""

    type: Literal["agent.confirm_request"] = "agent.confirm_request"
    call_id: str
    tool: str
    prompt: str


class AgentProgress(_Event):
    """Long-running work (agent-CLI providers) reporting progress."""

    type: Literal["agent.progress"] = "agent.progress"
    provider: str
    message: str


class AgentToken(_Event):
    type: Literal["agent.token"] = "agent.token"
    text: str


class AgentDone(_Event):
    type: Literal["agent.done"] = "agent.done"
    text: str
    provider: str = ""
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    interrupted: bool = False


# --------------------------------------------------------------------------- speech output


class TtsStart(_Event):
    type: Literal["tts.start"] = "tts.start"
    stream_id: int
    sample_rate: int = 24000
    provider: str = ""


class TtsSentence(_Event):
    """Subtitle for the sentence that is about to be spoken."""

    type: Literal["tts.sentence"] = "tts.sentence"
    stream_id: int
    text: str


class TtsAudioLevel(_Event):
    type: Literal["tts.audio_level"] = "tts.audio_level"
    level: float = Field(ge=0.0, le=1.0)


class TtsEnd(_Event):
    """Brain finished sending audio for a stream."""

    type: Literal["tts.end"] = "tts.end"
    stream_id: int
    interrupted: bool = False


class PlaybackDone(_Event):
    """Audio node finished (or aborted) playing a stream."""

    type: Literal["playback.done"] = "playback.done"
    stream_id: int
    interrupted: bool = False


class BargeIn(_Event):
    type: Literal["barge_in"] = "barge_in"
    source: Literal["voice", "touch"] = "voice"


# --------------------------------------------------------------------------- commands


class NodeCommand(_Event):
    """Brain → audio node."""

    type: Literal["node.command"] = "node.command"
    command: Literal[
        "listen.open",  # start capturing without a wake word (push-to-talk / follow-up)
        "listen.cancel",
        "playback.stop",
        "chime",
        "mute",
        "unmute",
    ]
    timeout_s: float | None = Field(
        default=None, description="listen.open: give up if no speech starts within this window"
    )
    follow_up: bool = False


class NodeConfig(_Event):
    """Brain → audio node: live-tunable voice settings (from the admin Voice page)."""

    type: Literal["node.config"] = "node.config"
    wake_threshold: float = Field(0.5, ge=0.05, le=0.99)
    chime: bool = True
    vad_end_silence_ms: int = Field(700, ge=200, le=3000)
    max_utterance_s: float = Field(30.0, ge=3, le=120)
    # Admin mic meter: stream mic levels (and wake scores) continuously, not only while listening.
    monitor: bool = False


class WakeScore(_Event):
    """Audio node → brain while monitoring: best wake-word score of the last ~0.5 s."""

    type: Literal["wake.score"] = "wake.score"
    score: float = Field(ge=0.0, le=1.0)


class UiCommand(_Event):
    """UI → brain (touch interactions)."""

    type: Literal["ui.command"] = "ui.command"
    command: Literal["push_to_talk", "mute", "unmute", "barge_in", "screen_wake"]


class ConversationEntry(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, json_schema_serialization_defaults_required=True
    )

    user: str
    assistant: str
    ts: float


class NowCard(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, json_schema_serialization_defaults_required=True
    )

    next_event_title: str | None = None
    next_event_start: float | None = Field(default=None, description="Unix seconds")
    next_event_location: str | None = None
    unread_important: int | None = None


class UiConfig(BaseModel):
    """Appearance settings pushed to the kiosk UI."""

    model_config = ConfigDict(
        extra="forbid", frozen=True, json_schema_serialization_defaults_required=True
    )

    colors: dict[str, str] = Field(default_factory=dict, description="State → #RRGGBB")
    quality: Literal["auto", "high", "medium", "low"] = "auto"
    night_start: str = "23:00"
    night_end: str = "07:00"
    night_dim: float = 0.3
    screen_sleep_min: int = 10
    show_cards: bool = True


class UiSnapshot(_Event):
    """Everything the UI needs after (re)connecting or when side data changes."""

    type: Literal["ui.snapshot"] = "ui.snapshot"
    state: State
    config: UiConfig
    recent: list[ConversationEntry] = Field(default_factory=list)
    now: NowCard = Field(default_factory=NowCard)


Event = Annotated[
    Hello
    | StatusUpdate
    | StateChange
    | ErrorEvent
    | WakeDetected
    | ListenStart
    | ListenLevel
    | ListenEnd
    | ListenPartial
    | ListenFinal
    | AgentThinking
    | AgentToolCall
    | AgentToolResult
    | AgentConfirmRequest
    | AgentProgress
    | AgentToken
    | AgentDone
    | TtsStart
    | TtsSentence
    | TtsAudioLevel
    | TtsEnd
    | PlaybackDone
    | BargeIn
    | NodeCommand
    | NodeConfig
    | WakeScore
    | UiCommand
    | UiSnapshot,
    Field(discriminator="type"),
]

EventAdapter: TypeAdapter[Event] = TypeAdapter(Event)

EVENT_TYPES: frozenset[str] = frozenset(
    m.model_fields["type"].default for m in _Event.__subclasses__()
)


def parse_event(raw: str | bytes) -> Event:
    """Parse and validate a JSON event. Raises ``pydantic.ValidationError`` on bad input."""
    return EventAdapter.validate_json(raw)


def dump_event(event: Event) -> str:
    return event.model_dump_json()


__all__ = [
    "EVENT_TYPES",
    "PROTOCOL_VERSION",
    "AgentConfirmRequest",
    "AgentDone",
    "AgentProgress",
    "AgentThinking",
    "AgentToken",
    "AgentToolCall",
    "AgentToolResult",
    "BargeIn",
    "ConversationEntry",
    "ErrorEvent",
    "Event",
    "EventAdapter",
    "Hello",
    "ListenEnd",
    "ListenFinal",
    "ListenLevel",
    "ListenPartial",
    "ListenStart",
    "NodeCommand",
    "NodeConfig",
    "NowCard",
    "PlaybackDone",
    "SideEffect",
    "State",
    "StateChange",
    "StatusUpdate",
    "TtsAudioLevel",
    "TtsEnd",
    "TtsSentence",
    "TtsStart",
    "UiCommand",
    "UiConfig",
    "UiSnapshot",
    "WakeDetected",
    "WakeScore",
    "Weather",
    "dump_event",
    "parse_event",
]
