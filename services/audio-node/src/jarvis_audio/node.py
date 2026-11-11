"""The audio node: one mic loop feeding wake word or capture, one socket to the brain.

Latency-critical decisions are made *here*, not in the brain:
- on the wake word, capture starts immediately (no round trip) and any playback stops at once;
- endpointing (end of speech) runs locally on Silero VAD.
The brain is told what happened and stays authoritative for the conversation state.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections import deque
from typing import Protocol

import websockets
from jarvis_protocol import (
    PROTOCOL_VERSION,
    BargeIn,
    Hello,
    ListenEnd,
    ListenLevel,
    ListenStart,
    NodeCommand,
    PlaybackDone,
    StatusUpdate,
    TtsEnd,
    TtsStart,
    WakeDetected,
    dump_event,
    pack_audio,
    parse_event,
    unpack_audio,
)
from jarvis_protocol.events import Event
from pydantic import ValidationError

from . import __version__
from .audio_io import CHUNK_SAMPLES, SAMPLE_RATE, Mic, Player, make_chime, rms_level
from .detect import Endpointer
from .settings import NodeSettings

log = logging.getLogger(__name__)

PREROLL_CHUNKS = 4  # 320 ms of audio before speech start / wake, so first syllables aren't lost
LEVEL_EVERY = 1  # send mic level every chunk (80 ms) — the UI smooths it


class WakeDetector(Protocol):
    name: str

    def process(self, pcm: bytes) -> float | None: ...
    def reset(self) -> None: ...


class SpeechProb(Protocol):
    def prob(self, pcm: bytes) -> float: ...
    def reset(self) -> None: ...


class Capture:
    def __init__(self, stream_id: int, endpointer: Endpointer, follow_up: bool) -> None:
        self.stream_id = stream_id
        self.ep = endpointer
        self.follow_up = follow_up
        self.started_at = time.monotonic()
        self.announced = False  # listen.start sent (follow-up waits for actual speech)


class AudioNode:
    def __init__(
        self,
        settings: NodeSettings,
        mic: Mic,
        wake: WakeDetector,
        vad: SpeechProb,
        player: Player | None = None,
    ) -> None:
        self.s = settings
        self.mic = mic
        self.wake = wake
        self.vad = vad
        self.player = player or Player(
            settings.output_device, lead_ms=settings.playback_lead_ms, on_level=self._on_tts_level
        )
        if self.player.on_level is None:
            self.player.on_level = self._on_tts_level
        self.chime_pcm = make_chime(24_000)
        self.chime_player = Player(settings.output_device, lead_ms=400, sink=self.player.sink)
        self.ws: websockets.ClientConnection | None = None
        self.capture: Capture | None = None
        self.muted = False
        self.chime_enabled = settings.chime
        self._next_stream = 1
        self._preroll: deque[bytes] = deque(maxlen=PREROLL_CHUNKS)
        self._send_q: asyncio.Queue[str | bytes] = asyncio.Queue(maxsize=400)
        self._playing_stream: int | None = None
        self.connected = asyncio.Event()
        self._stopping = False
        self._report_task: asyncio.Task[None] | None = None

    # ------------------------------------------------------------------ outbound

    def send(self, payload: Event | bytes) -> None:
        if self.ws is None:
            return
        data = payload if isinstance(payload, bytes) else dump_event(payload)
        try:
            self._send_q.put_nowait(data)
        except asyncio.QueueFull:
            log.warning(
                "send queue full; dropping %s", "audio" if isinstance(data, bytes) else data[:40]
            )

    def _on_tts_level(self, level: float) -> None:
        from jarvis_protocol import TtsAudioLevel

        self.send(TtsAudioLevel(level=round(level, 3)))

    # ------------------------------------------------------------------ capture

    def _begin_capture(self, *, follow_up: bool, timeout_s: float | None = None) -> Capture:
        self.vad.reset()
        sid = self._next_stream
        self._next_stream += 1
        ep = Endpointer(
            threshold=self.s.vad_threshold,
            end_silence_ms=self.s.end_silence_ms,
            max_utterance_s=self.s.max_utterance_s,
            no_speech_timeout_s=timeout_s or self.s.no_speech_timeout_s,
        )
        self.capture = Capture(sid, ep, follow_up)
        if not follow_up:
            self._announce(self.capture)
        return self.capture

    def _announce(self, cap: Capture) -> None:
        cap.announced = True
        self.send(ListenStart(follow_up=cap.follow_up, turn_id=str(cap.stream_id)))
        # Follow-up speech is detected a few chunks late: send the pre-roll so the STT hears the
        # first syllable. (After a wake word the pre-roll *is* the wake word — skip it.)
        if cap.follow_up:
            for chunk in self._preroll:
                self.send(pack_audio(cap.stream_id, chunk))
        self._preroll.clear()

    def _end_capture(self, reason: str) -> None:
        cap = self.capture
        if cap is None:
            return
        self.capture = None
        self.wake.reset()
        self.send(
            ListenEnd(
                reason=reason,  # type: ignore[arg-type]
                duration_s=round(time.monotonic() - cap.started_at, 2),
                turn_id=str(cap.stream_id),
            )
        )

    def on_wake(self, score: float, source: str = "voice") -> None:
        barge = self.player.active
        if barge:
            self.player.stop()
            self.send(BargeIn(source="voice"))
        self.send(WakeDetected(source=source, model=self.wake.name, score=round(score, 3)))  # type: ignore[arg-type]
        if self.chime_enabled and not barge:
            self._play_chime()
        self._begin_capture(follow_up=False)

    def _play_chime(self) -> None:
        self.chime_player.start(0, 24_000)
        self.chime_player.feed(0, self.chime_pcm)
        self.chime_player.end(0)

    async def process_chunk(self, pcm: bytes) -> None:
        cap = self.capture
        if cap is None:
            self._preroll.append(pcm)
            if not self.muted:
                score = self.wake.process(pcm)
                if score is not None:
                    log.info("wake word (%.2f)", score)
                    self.on_wake(score)
            return
        reason = cap.ep.push(self.vad.prob(pcm))
        if cap.follow_up and not cap.announced:
            self._preroll.append(pcm)
            if cap.ep.speech_started:
                self._announce(cap)
            elif reason == "timeout":
                self.capture = None
                self.send(ListenEnd(reason="timeout", turn_id=str(cap.stream_id)))
            return
        self.send(pack_audio(cap.stream_id, pcm))
        self.send(ListenLevel(level=round(rms_level(pcm), 3)))
        if reason:
            log.info("end of utterance: %s", reason)
            self._end_capture(reason)

    # ------------------------------------------------------------------ inbound

    async def handle_event(self, ev: Event) -> None:
        if isinstance(ev, NodeCommand):
            await self._command(ev)
        elif isinstance(ev, TtsStart):
            self._playing_stream = ev.stream_id
            self.player.start(ev.stream_id, ev.sample_rate)
            self._report_task = asyncio.create_task(self._report_playback(ev.stream_id))
        elif isinstance(ev, TtsEnd):
            if ev.interrupted:
                if self._playing_stream == ev.stream_id:
                    self.player.stop()
            else:
                self.player.end(ev.stream_id)

    async def _report_playback(self, stream_id: int) -> None:
        finished = await self.player.wait()
        if self._playing_stream == stream_id:
            self._playing_stream = None
            self.send(PlaybackDone(stream_id=stream_id, interrupted=not finished))

    async def _command(self, cmd: NodeCommand) -> None:
        match cmd.command:
            case "listen.open":
                if self.capture is None:
                    self._begin_capture(follow_up=cmd.follow_up, timeout_s=cmd.timeout_s)
            case "listen.cancel":
                if self.capture is not None:
                    self._end_capture("cancelled")
            case "playback.stop":
                self.player.stop()
            case "chime":
                self._play_chime()
            case "mute":
                self.muted = True
                if self.capture:
                    self._end_capture("cancelled")
                self.send(StatusUpdate(mic=True, muted=True))
            case "unmute":
                self.muted = False
                self.send(StatusUpdate(mic=True, muted=False))

    def handle_audio(self, frame: bytes) -> None:
        try:
            sid, pcm = unpack_audio(frame)
        except ValueError:
            return
        self.player.feed(sid, pcm)

    # ------------------------------------------------------------------ loops

    async def mic_loop(self) -> None:
        """Feed mic audio forever; if the device disappears (unplugged, PipeWire restarting at
        boot), report it and retry with backoff instead of crashing."""
        delay = 1.0
        while not self._stopping:
            try:
                async for chunk in self.mic.chunks():
                    delay = 1.0
                    try:
                        await self.process_chunk(chunk)
                    except Exception:
                        log.exception("audio chunk processing failed")
                    if self._stopping:
                        return
            except (RuntimeError, OSError) as e:
                log.warning("microphone unavailable: %s — retrying in %.0fs", e, delay)
                self.send(StatusUpdate(mic=False, muted=self.muted))
            await asyncio.sleep(delay)
            delay = min(delay * 2, 15)

    async def socket_loop(self) -> None:
        delay = 0.5
        headers = {}
        if tok := self.s.node_token.get_secret_value():
            headers["Authorization"] = f"Bearer {tok}"
        while not self._stopping:
            try:
                async with websockets.connect(
                    self.s.ws_url, additional_headers=headers, max_size=4 * 1024 * 1024,
                    ping_interval=10, ping_timeout=10, open_timeout=5,
                ) as ws:  # fmt: skip
                    self.ws = ws
                    while not self._send_q.empty():  # nothing stale from a previous session
                        self._send_q.get_nowait()
                    await ws.send(
                        dump_event(
                            Hello(
                                role="audio-node",
                                protocol=PROTOCOL_VERSION,
                                client_version=__version__,
                            )
                        )
                    )
                    await ws.send(dump_event(StatusUpdate(mic=True, muted=self.muted)))
                    log.info("connected to brain at %s", self.s.ws_url)
                    self.connected.set()
                    delay = 0.5
                    sender = asyncio.create_task(self._sender(ws))
                    try:
                        async for msg in ws:
                            if isinstance(msg, bytes):
                                self.handle_audio(msg)
                                continue
                            try:
                                ev = parse_event(msg)
                            except ValidationError:
                                log.warning("bad event from brain: %.120s", msg)
                                continue
                            await self.handle_event(ev)
                    finally:
                        sender.cancel()
                        with contextlib.suppress(asyncio.CancelledError):
                            await sender
            except (OSError, websockets.WebSocketException) as e:
                log.warning("brain connection: %s — retrying in %.1fs", e, delay)
            finally:
                self.ws = None
                self.connected.clear()
                # Without a brain there is nobody to talk to: stop speaking and listening.
                self.player.stop()
                self.capture = None
            if self._stopping:
                break
            await asyncio.sleep(delay)
            delay = min(delay * 2, 10)

    async def _sender(self, ws: websockets.ClientConnection) -> None:
        while True:
            data = await self._send_q.get()
            await ws.send(data)

    async def run(self) -> None:
        await asyncio.gather(self.socket_loop(), self.mic_loop())

    def stop(self) -> None:
        self._stopping = True
        self.player.stop()


__all__ = ["CHUNK_SAMPLES", "SAMPLE_RATE", "AudioNode"]
