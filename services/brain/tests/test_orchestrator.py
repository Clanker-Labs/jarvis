import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

import pytest
from jarvis_brain.bus import EventBus
from jarvis_brain.config import ConfigStore
from jarvis_brain.db import Database
from jarvis_brain.orchestrator import EchoResponder, Orchestrator
from jarvis_brain.status import StatusTracker
from jarvis_brain.voice.speaker import Speaker
from jarvis_brain.voice.stt import STTChain
from jarvis_brain.voice.tts import TTSChain
from jarvis_protocol import (
    BargeIn,
    ListenEnd,
    ListenStart,
    NodeCommand,
    PlaybackDone,
    State,
    TtsEnd,
    TtsStart,
    UiCommand,
)


@dataclass
class FakeHub:
    sent: list = field(default_factory=list)
    audio: list = field(default_factory=list)
    node_connected: bool = True

    async def to_node(self, ev):
        self.sent.append(ev)
        return True

    async def audio_to_node(self, sid, pcm):
        self.audio.append((sid, pcm))
        return True

    def commands(self):
        return [e for e in self.sent if isinstance(e, NodeCommand)]


@dataclass
class FakeSTT:
    text: str = "hello there"
    id: str = "fake"
    streaming: bool = False
    local: bool = False

    def available(self):
        return True

    async def transcribe(self, pcm):
        return self.text


@dataclass
class FakeTTS:
    delay: float = 0.0
    id: str = "fake"
    local: bool = False

    def available(self):
        return True

    def sample_rate(self):
        return 16000

    async def synthesize(self, text) -> AsyncIterator[bytes]:
        for _ in range(5):
            await asyncio.sleep(self.delay)
            yield b"\x01\x00" * 1600


def make(stt_text="hello there", tts_delay=0.0):
    db = Database(":memory:")
    cfg = ConfigStore(db)
    cfg.update("voice", {"stt_chain": ["fake"], "tts_chain": ["fake"], "follow_up_s": 8})
    bus = EventBus()
    hub = FakeHub()
    stt = STTChain({"fake": FakeSTT(stt_text)}, lambda: cfg.voice)
    tts = TTSChain({"fake": FakeTTS(tts_delay)}, lambda: cfg.voice)
    status = StatusTracker(bus, cfg)
    orch = Orchestrator(bus, hub, cfg, stt, Speaker(bus, hub, tts), EchoResponder(), status)
    states = []
    orch.sm.subscribe(lambda a, b, r: states.append(b))
    return orch, hub, states


async def utterance(orch, sid="1", seconds=1.0):
    await orch.on_node_event(ListenStart(turn_id=sid))
    await orch.on_node_audio(int(sid), b"\x00\x01" * int(16000 * seconds))
    await orch.on_node_event(ListenEnd(reason="silence", turn_id=sid))


async def wait_for(pred, timeout=3.0):
    async with asyncio.timeout(timeout):
        while not pred():
            await asyncio.sleep(0.01)


async def test_full_turn_then_follow_up_then_idle():
    orch, hub, states = make()
    await utterance(orch)
    await wait_for(lambda: any(isinstance(e, TtsEnd) for e in hub.sent))
    start = next(e for e in hub.sent if isinstance(e, TtsStart))
    assert hub.audio and all(sid == start.stream_id for sid, _ in hub.audio)
    await orch.on_node_event(PlaybackDone(stream_id=start.stream_id))
    await wait_for(lambda: orch.sm.state is State.FOLLOW_UP)
    assert states[:4] == [State.LISTENING, State.THINKING, State.SPEAKING, State.FOLLOW_UP]
    cmd = hub.commands()[-1]
    assert cmd.command == "listen.open" and cmd.follow_up and cmd.timeout_s == 8
    # No speech in the window → idle.
    await orch.on_node_event(ListenEnd(reason="timeout"))
    assert orch.sm.state is State.IDLE
    assert orch.status.recent[0].user == "hello there"


async def test_barge_in_stops_speech_and_cancels_turn():
    orch, hub, _ = make(tts_delay=0.2)
    await utterance(orch)
    await wait_for(lambda: orch.sm.state is State.SPEAKING)
    await orch.on_node_event(BargeIn(source="voice"))
    end = next(e for e in hub.sent if isinstance(e, TtsEnd))
    assert end.interrupted
    assert any(c.command == "playback.stop" for c in hub.commands())
    assert orch.turn_task is None
    # The node then starts a new capture.
    await orch.on_node_event(ListenStart(turn_id="2"))
    assert orch.sm.state is State.LISTENING


async def test_touch_barge_in_reopens_listening():
    orch, hub, _ = make(tts_delay=0.2)
    await utterance(orch)
    await wait_for(lambda: orch.sm.state is State.SPEAKING)
    await orch.on_ui_command(UiCommand(command="barge_in"))
    cmds = [c.command for c in hub.commands()]
    assert "playback.stop" in cmds and cmds[-1] == "listen.open"


async def test_empty_transcript_goes_idle_quietly():
    orch, hub, _ = make(stt_text="")
    await utterance(orch)
    await wait_for(lambda: orch.sm.state is State.IDLE)
    assert not any(isinstance(e, TtsStart) for e in hub.sent)


async def test_too_short_audio_is_ignored():
    orch, hub, _ = make()
    await utterance(orch, seconds=0.1)
    await wait_for(lambda: orch.sm.state is State.IDLE)
    assert not hub.audio


@pytest.mark.parametrize("cmd", ["mute", "unmute"])
async def test_mute_is_forwarded(cmd):
    orch, hub, _ = make()
    await orch.on_ui_command(UiCommand(command=cmd))
    assert hub.commands()[-1].command == cmd
    assert orch.status.flags["muted"] is (cmd == "mute")


async def test_settings_panel_commands():
    from jarvis_protocol import MicTest

    orch, hub, _ = make()
    seen = []
    orch.on_monitor = lambda on: _record(seen, on)
    await orch.on_ui_command(UiCommand(command="mic_test"))
    await orch.on_ui_command(UiCommand(command="speaker_test"))
    await orch.on_ui_command(UiCommand(command="monitor_on"))
    await orch.on_ui_command(UiCommand(command="monitor_off"))
    assert [c.command for c in hub.commands()] == ["mic.test", "chime"]
    assert seen == [True, False]

    # Without a node the test fails visibly instead of hanging on "recording…".
    async def offline(ev):
        return False

    hub.to_node = offline
    with orch.bus.subscription() as sub:
        await orch.on_ui_command(UiCommand(command="mic_test"))
        ev = sub.queue.get_nowait()
    assert isinstance(ev, MicTest) and ev.phase == "failed"


async def _record(seen, on):
    seen.append(on)


async def test_node_status_carries_mic_device():
    from jarvis_protocol import StatusUpdate

    orch, _, _ = make()
    await orch.on_node_event(StatusUpdate(mic=True, mic_device="Jabra SPEAK 510 USB"))
    snap = orch.status.snapshot()
    assert snap.mic and snap.mic_device == "Jabra SPEAK 510 USB"
