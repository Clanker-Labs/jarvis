"""``make demo``: replay recorded audio through the whole pipeline, no microphone needed.

Starts an in-process brain on a free localhost port, runs the real audio node (wake word, VAD,
endpointing, paced playback) on a WAV file, prints the event timeline with stage latencies and
saves what Jarvis said to ``recordings/``.

    uv run --package jarvis-audio-node python -m jarvis_audio.demo [file.wav] [--play]
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import socket
import time
import wave
from pathlib import Path

FIXTURE = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "hey_jarvis_what_time.wav"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


async def run(wav: Path, play: bool, limit_s: float) -> int:
    import uvicorn
    from jarvis_brain.app import create_app
    from jarvis_brain.runtime import Runtime
    from jarvis_brain.settings import Settings
    from jarvis_protocol import State

    from .audio_io import FileMic, Player
    from .detect import Vad, WakeWord
    from .models import ensure_models
    from .node import AudioNode
    from .settings import NodeSettings

    port = _free_port()
    token = "demo-token"  # noqa: S105 — throwaway, localhost only
    bs = Settings(
        BRAIN_HOST="127.0.0.1", BRAIN_PORT=port, JARVIS_NODE_TOKEN=token,
        JARVIS_BACKGROUND_TASKS=False, JARVIS_WARM_LOCAL_VOICE=False,
    )  # type: ignore[call-arg]  # fmt: skip
    runtime = Runtime(bs)
    print("… loading voice engines")
    await runtime.warm_local_voice()  # realistic latencies: a running brain has these warm
    server = uvicorn.Server(uvicorn.Config(create_app(runtime), host="127.0.0.1", port=port,
                                           log_level="warning", log_config=None))  # fmt: skip
    server_task = asyncio.create_task(server.serve())
    while not server.started:  # noqa: ASYNC110 — uvicorn exposes no startup event
        await asyncio.sleep(0.05)

    t0 = time.time()
    timeline: list[tuple[float, str]] = []
    done = asyncio.Event()

    def tap(ev) -> None:  # type: ignore[no-untyped-def]
        if ev.type in ("listen.level", "tts.audio_level", "agent.token", "status.update"):
            return
        detail = ""
        if ev.type == "state.change":
            detail = f"{ev.previous} → {ev.state}"
            if ev.state == State.FOLLOW_UP or (ev.state == State.IDLE and timeline):
                done.set()
        elif ev.type in ("listen.final", "listen.partial", "tts.sentence"):
            detail = repr(ev.text)
        elif ev.type == "wake.detected":
            detail = f"score={ev.score}"
        elif ev.type == "tts.start":
            detail = f"engine={ev.provider} rate={ev.sample_rate}"
        elif ev.type == "error":
            detail = ev.message
            done.set()
        timeline.append((time.time() - t0, f"{ev.type:<18} {detail}"))

    runtime.bus.tap(tap)

    ns = NodeSettings(BRAIN_URL=f"ws://127.0.0.1:{port}", JARVIS_NODE_TOKEN=token)  # type: ignore[call-arg]
    ensure_models(ns)
    captured: list[tuple[int, bytes]] = []
    player = Player(
        ns.output_device,
        lead_ms=ns.playback_lead_ms,
        sink=None if play else (lambda pcm, rate: captured.append((rate, pcm))),
    )
    node = AudioNode(
        ns,
        mic=FileMic(wav),
        wake=WakeWord(ns.models_dir, ns.wake_model, ns.wake_threshold, ns.wake_refractory_s),
        vad=Vad(ns.models_dir),
        player=player,
    )
    if not play:
        node.chime_player.sink = lambda pcm, rate: None
    node_task = asyncio.create_task(node.run())
    print(f"▶ replaying {wav.name} through the pipeline (brain on 127.0.0.1:{port})…\n")
    ok = True
    try:
        await asyncio.wait_for(done.wait(), limit_s)
        await asyncio.sleep(0.3)
    except TimeoutError:
        ok = False
        print("✗ timed out waiting for the turn to finish")
    finally:
        node.stop()
        node_task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await node_task
        server.should_exit = True
        await server_task

    for t, line in timeline:
        print(f"  {t:6.2f}s  {line}")
    turn = runtime.orchestrator.turn
    if turn and turn.timings:
        tm = turn.timings
        print()
        if "listen_end" in tm and "stt_final" in tm:
            print(f"  STT after end of speech     {tm['stt_final'] - tm['listen_end']:.2f}s")
        if "listen_end" in tm and "first_audio" in tm:
            print(f"  end of speech → first audio {tm['first_audio'] - tm['listen_end']:.2f}s")
    if captured:
        rate = captured[0][0]
        out = Path("recordings") / f"demo-reply-{int(t0)}.wav"
        out.parent.mkdir(exist_ok=True)
        with wave.open(str(out), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(rate)
            w.writeframes(b"".join(p for _, p in captured))
        print(f"\n  reply audio saved to {out}")
    return 0 if ok else 1


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("wav", nargs="?", type=Path, default=FIXTURE)
    ap.add_argument("--play", action="store_true", help="play the reply on the real speaker")
    ap.add_argument("--timeout", type=float, default=60)
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
                        datefmt="%H:%M:%S")  # fmt: skip
    raise SystemExit(asyncio.run(run(args.wav, args.play, args.timeout)))


if __name__ == "__main__":
    main()
