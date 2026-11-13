import asyncio

from jarvis_audio.audio_io import CHUNK_BYTES, Player
from jarvis_audio.node import AudioNode
from jarvis_audio.settings import NodeSettings
from jarvis_protocol import (
    BargeIn,
    ListenEnd,
    ListenStart,
    NodeCommand,
    TtsEnd,
    TtsStart,
    WakeDetected,
    parse_event,
    unpack_audio,
)

SPEECH = b"\x10\x27" * (CHUNK_BYTES // 2)
SILENCE = b"\x00" * CHUNK_BYTES


class FakeWake:
    name = "fake"

    def __init__(self):
        self.fire = False

    def process(self, pcm):
        if self.fire:
            self.fire = False
            return 0.9
        return None

    def reset(self):
        pass


class FakeVad:
    def prob(self, pcm):
        return 0.9 if pcm == SPEECH else 0.0

    def reset(self):
        pass


def make():
    s = NodeSettings(VAD_END_SILENCE_MS=240, WAKE_CHIME=False)
    played = []
    node = AudioNode(
        s,
        mic=None,
        wake=FakeWake(),
        vad=FakeVad(),
        player=Player(lead_ms=50, sink=lambda pcm, r: played.append(pcm)),
    )
    node.ws = object()  # pretend connected; outgoing messages land in node._send_q
    return node, played


def drain(node):
    out = []
    while not node._send_q.empty():
        item = node._send_q.get_nowait()
        out.append(unpack_audio(item) if isinstance(item, bytes) else parse_event(item))
    return out


async def test_wake_then_capture_then_endpoint():
    node, _ = make()
    node.wake.fire = True
    await node.process_chunk(SPEECH)  # wake fires on this chunk
    for chunk in [SPEECH] * 3 + [SILENCE] * 4:
        await node.process_chunk(chunk)
    msgs = drain(node)
    kinds = [m.type if hasattr(m, "type") else "audio" for m in msgs]
    assert kinds[:2] == ["wake.detected", "listen.start"]
    assert "audio" in kinds
    end = msgs[-1]
    assert isinstance(end, ListenEnd) and end.reason == "silence"
    assert node.capture is None


async def test_wake_during_playback_is_barge_in():
    node, played = make()
    await node.handle_event(TtsStart(stream_id=7, sample_rate=16000))
    node.handle_audio(b"\x07\x00\x00\x00" + b"\x01\x00" * 16000)
    await asyncio.sleep(0.05)
    assert node.player.active
    node.wake.fire = True
    await node.process_chunk(SPEECH)
    assert not node.player.active
    msgs = drain(node)
    assert any(isinstance(m, BargeIn) for m in msgs)
    assert any(isinstance(m, WakeDetected) for m in msgs)
    # Late frames for the old stream are dropped.
    node.handle_audio(b"\x07\x00\x00\x00" + b"\x01\x00" * 100)
    assert node.player._queue.empty()


async def test_follow_up_waits_for_speech_before_announcing():
    node, _ = make()
    await node.handle_event(NodeCommand(command="listen.open", timeout_s=5, follow_up=True))
    await node.process_chunk(SILENCE)
    assert not any(isinstance(m, ListenStart) for m in drain(node))
    await node.process_chunk(SPEECH)
    await node.process_chunk(SPEECH)
    msgs = drain(node)
    start = next(m for m in msgs if isinstance(m, ListenStart))
    assert start.follow_up
    assert any(isinstance(m, tuple) for m in msgs)  # pre-roll audio sent


async def test_follow_up_timeout():
    node, _ = make()
    await node.handle_event(NodeCommand(command="listen.open", timeout_s=0.3, follow_up=True))
    for _ in range(5):
        await node.process_chunk(SILENCE)
    end = [m for m in drain(node) if isinstance(m, ListenEnd)]
    assert end and end[0].reason == "timeout" and node.capture is None


async def test_playback_reports_done():
    node, played = make()
    await node.handle_event(TtsStart(stream_id=3, sample_rate=16000))
    node.handle_audio(b"\x03\x00\x00\x00" + b"\x01\x00" * 1600)
    await node.handle_event(TtsEnd(stream_id=3))
    async with asyncio.timeout(2):
        while not any(
            isinstance(m, str) and parse_event(m).type == "playback.done"
            for m in list(node._send_q._queue)
        ):
            await asyncio.sleep(0.02)
    assert played


async def test_mute_disables_wake():
    node, _ = make()
    await node.handle_event(NodeCommand(command="mute"))
    node.wake.fire = True
    await node.process_chunk(SPEECH)
    assert node.capture is None


async def test_node_config_and_monitor():
    from jarvis_protocol import NodeConfig, WakeScore

    node, _ = make()
    node.wake.threshold = 0.5
    node.wake.last_score = 0.42
    await node.handle_event(
        NodeConfig(wake_threshold=0.7, chime=False, vad_end_silence_ms=500, monitor=True)
    )
    assert node.wake.threshold == 0.7 and node.s.end_silence_ms == 500 and node.monitor
    for _ in range(6):
        await node.process_chunk(SILENCE)
    msgs = drain(node)
    assert sum(1 for m in msgs if getattr(m, "type", "") == "listen.level") == 6
    assert [m.score for m in msgs if isinstance(m, WakeScore)] == [0.42]


# ---------------------------------------------------------------------- microphone presence


def test_capture_devices_reads_only_capture_pcms(tmp_path):
    from jarvis_audio.audio_io import capture_devices

    table = tmp_path / "pcm"
    table.write_text(
        "00-00: bcm2835 Headphones : bcm2835 Headphones : playback 8\n"
        "01-00: MAI PCM i2s-hifi-0 : MAI PCM i2s-hifi-0 : playback 1\n"
    )
    assert capture_devices(table) == []  # a Pi 4 with nothing plugged in
    table.write_text(table.read_text() + "03-00: USB Audio : USB Audio : playback 1 : capture 1\n")
    assert capture_devices(table) == ["03-00: USB Audio : USB Audio : playback 1 : capture 1"]
    assert capture_devices(tmp_path / "missing") == []
    from jarvis_audio.audio_io import capture_hw_present

    assert capture_hw_present(tmp_path / "missing") is True  # unknown: let arecord decide
    assert capture_hw_present(table) is True


async def test_routed_default_without_hardware_is_no_microphone():
    import pytest
    from jarvis_audio.audio_io import AlsaMic, NoMicrophone

    with pytest.raises(NoMicrophone):
        async for _ in AlsaMic("default", has_capture_hw=lambda: False).chunks():
            pass


class ScriptedMic:
    """Raises NoMicrophone `missing` times, then streams `chunks` chunks."""

    def __init__(self, missing, chunks):
        self.missing, self.left = missing, chunks

    async def chunks(self):
        from jarvis_audio.audio_io import NoMicrophone

        if self.missing:
            self.missing -= 1
            raise NoMicrophone("no microphone connected")
        for _ in range(self.left):
            yield SILENCE
        raise RuntimeError("unplugged")

    async def close(self):
        pass


async def test_mic_status_follows_real_audio(monkeypatch):
    from jarvis_protocol import StatusUpdate

    node, _ = make()
    node.mic = ScriptedMic(missing=1, chunks=3)
    sleeps = []

    async def fast_sleep(s):
        sleeps.append(s)
        if len(sleeps) >= 2:
            node._stopping = True

    monkeypatch.setattr("jarvis_audio.node.asyncio.sleep", fast_sleep)
    assert node.mic_ok is False
    await node.handle_event(NodeCommand(command="unmute"))
    await node.mic_loop()
    statuses = [e.mic for e in drain(node) if isinstance(e, StatusUpdate)]
    # unmute reports the real state (no mic yet); then audio flows -> True; unplugged -> False
    assert statuses == [False, True, False]
    assert sleeps[0] == 3.0  # missing hardware is polled quickly


def test_mic_name_from_card_table(tmp_path):
    from jarvis_audio.audio_io import mic_name

    pcm, cards = tmp_path / "pcm", tmp_path / "cards"
    pcm.write_text("00-00: bcm2835 Headphones : bcm2835 Headphones : playback 8\n")
    cards.write_text(" 0 [Headphones     ]: bcm2835_headpho - bcm2835 Headphones\n")
    assert mic_name(pcm, cards) == ""
    pcm.write_text(pcm.read_text() + "03-00: USB Audio : USB Audio : playback 1 : capture 1\n")
    cards.write_text(
        cards.read_text()
        + "                      bcm2835 Headphones\n"
        + " 3 [Speakerphone   ]: USB-Audio - Jabra SPEAK 510 USB\n"
    )
    assert mic_name(pcm, cards) == "Jabra SPEAK 510 USB"
    assert mic_name(pcm, tmp_path / "missing") == "USB Audio"


async def test_mic_test_records_then_plays_back():
    from jarvis_audio.node import MIC_TEST_CHUNKS
    from jarvis_protocol import MicTest

    node, played = make()
    node.start_mic_test()
    assert [m.detail for m in drain(node) if isinstance(m, MicTest)] == ["no microphone connected"]

    node.mic_ok = True
    node.start_mic_test()
    node.wake.fire = True  # saying "hey Jarvis" during the test must not wake it
    for _ in range(MIC_TEST_CHUNKS):
        assert node._mic_test_chunk(SPEECH)
    assert node.capture is None and node.testing
    await asyncio.wait_for(node._test_task, 5)
    phases = [m.phase for m in drain(node) if isinstance(m, MicTest)]
    assert phases == ["recording", "playing", "done"]
    assert b"".join(played) == SPEECH * MIC_TEST_CHUNKS
    assert not node._mic_test_chunk(SILENCE)  # back to normal listening
