from jarvis_audio.audio_io import make_chime, rms_level
from jarvis_audio.detect import CHUNK_MS, Endpointer


def run(ep: Endpointer, probs: list[float]):
    for i, p in enumerate(probs):
        r = ep.push(p)
        if r:
            return r, i
    return None, len(probs)


def test_ends_after_silence_window():
    ep = Endpointer(end_silence_ms=700)
    speech, silence = [0.9] * 10, [0.05] * 20
    reason, idx = run(ep, speech + silence)
    assert reason == "silence"
    # 700 ms of silence = 9 chunks of 80 ms (ceil) after the last speech chunk
    assert idx == 10 + (700 + CHUNK_MS - 1) // CHUNK_MS - 1


def test_short_pause_does_not_end():
    ep = Endpointer(end_silence_ms=700)
    reason, _ = run(ep, [0.9] * 5 + [0.1] * 5 + [0.9] * 5)
    assert reason is None


def test_hysteresis_keeps_quiet_speech():
    ep = Endpointer(threshold=0.5, end_silence_ms=300)
    reason, _ = run(ep, [0.9] * 3 + [0.4] * 10)  # 0.4 ≥ 0.35 → still speech
    assert reason is None


def test_no_speech_timeout():
    ep = Endpointer(no_speech_timeout_s=1.0)
    reason, idx = run(ep, [0.0] * 50)
    assert reason == "timeout" and idx == -(-1000 // CHUNK_MS) - 1  # first chunk at/after 1 s


def test_single_blip_is_not_speech():
    ep = Endpointer(start_chunks=2, no_speech_timeout_s=1.0)
    reason, _ = run(ep, [0.9, 0.0] * 10)
    assert reason == "timeout" and not ep.speech_started


def test_max_duration():
    ep = Endpointer(max_utterance_s=2.0)
    reason, _ = run(ep, [0.9] * 100)
    assert reason == "max_duration"


def test_levels_and_chime():
    assert rms_level(b"\x00\x00" * 100) == 0.0
    chime = make_chime()
    assert 0.3 < rms_level(chime) < 1.0
    assert len(chime) % 2 == 0
