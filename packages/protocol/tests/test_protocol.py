import itertools
import json

import pytest
from jarvis_protocol import (
    EVENT_TYPES,
    TRANSITIONS,
    AgentToolCall,
    InvalidTransition,
    SideEffect,
    State,
    StateMachine,
    TtsAudioLevel,
    WakeDetected,
    dump_event,
    pack_audio,
    parse_event,
    unpack_audio,
)
from pydantic import ValidationError

REQUIRED_EVENTS = {
    "wake.detected",
    "listen.start",
    "listen.partial",
    "listen.final",
    "agent.thinking",
    "agent.tool_call",
    "agent.tool_result",
    "agent.token",
    "agent.done",
    "tts.start",
    "tts.audio_level",
    "tts.end",
    "barge_in",
    "error",
    "state.change",
}


def test_spec_minimum_events_exist():
    assert REQUIRED_EVENTS <= EVENT_TYPES


def test_roundtrip_every_event_type_discriminates():
    ev = AgentToolCall(
        call_id="c1",
        tool="weather",
        args={"city": "Paris"},
        side_effect=SideEffect.NONE,
        turn_id="t1",
    )
    back = parse_event(dump_event(ev))
    assert isinstance(back, AgentToolCall)
    assert back == ev


def test_unknown_type_rejected():
    with pytest.raises(ValidationError):
        parse_event(json.dumps({"type": "nope"}))


def test_extra_fields_rejected():
    with pytest.raises(ValidationError):
        parse_event(json.dumps({"type": "wake.detected", "bogus": 1}))


def test_level_bounds():
    with pytest.raises(ValidationError):
        TtsAudioLevel(level=1.5)


def test_defaults_populated():
    ev = WakeDetected()
    assert ev.id and ev.ts > 0 and ev.source == "voice"


def test_audio_framing_roundtrip():
    pcm = b"\x01\x02" * 160
    sid, out = unpack_audio(pack_audio(42, pcm))
    assert sid == 42 and out == pcm
    with pytest.raises(ValueError):
        unpack_audio(b"\x00")


# ---- state machine


def test_happy_path():
    sm = StateMachine()
    seen = []
    sm.subscribe(lambda a, b, r: seen.append((a, b)))
    for s in [
        State.WAKE,
        State.LISTENING,
        State.THINKING,
        State.TOOL,
        State.THINKING,
        State.SPEAKING,
        State.FOLLOW_UP,
        State.IDLE,
    ]:
        sm.to(s)
    assert seen[0] == (State.IDLE, State.WAKE)
    assert sm.state is State.IDLE


def test_every_state_can_error_and_error_recovers_only_to_idle():
    for s in State:
        if s is not State.ERROR:
            assert State.ERROR in TRANSITIONS[s]
    assert TRANSITIONS[State.ERROR] == {State.IDLE}


def test_barge_in_edge():
    sm = StateMachine(State.SPEAKING)
    sm.to(State.LISTENING, "barge_in")
    assert sm.state is State.LISTENING


def test_invalid_transitions_raise():
    for a, b in itertools.product(State, State):
        if a == b or b in TRANSITIONS[a]:
            continue
        with pytest.raises(InvalidTransition):
            StateMachine(a).to(b)


def test_same_state_is_noop():
    sm = StateMachine()
    assert sm.to(State.IDLE) is False
