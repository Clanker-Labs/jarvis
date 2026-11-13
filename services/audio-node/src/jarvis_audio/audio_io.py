"""Microphone capture and speaker playback through ALSA's ``arecord`` / ``aplay``.

Subprocesses instead of PortAudio bindings: alsa-utils is present on every Pi image, needs no
compilation, works with PipeWire's ALSA plugin, and killing ``aplay`` is the fastest possible way
to stop sound on barge-in.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import shutil
import time
import wave
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Protocol

import numpy as np

log = logging.getLogger(__name__)

SAMPLE_RATE = 16_000
CHUNK_SAMPLES = 1280  # 80 ms — the frame size openWakeWord expects
CHUNK_BYTES = CHUNK_SAMPLES * 2


def rms_level(pcm: bytes) -> float:
    """Perceptual-ish 0..1 level for an s16le chunk (dBFS mapped from -60..0)."""
    if not pcm:
        return 0.0
    x = np.frombuffer(pcm, dtype=np.int16).astype(np.float32)
    rms = float(np.sqrt(np.mean(x * x))) / 32768.0
    if rms <= 1e-6:
        return 0.0
    db = 20 * np.log10(rms)
    return float(np.clip((db + 60) / 60, 0.0, 1.0))


class NoMicrophone(RuntimeError):
    """No capture hardware: the mic loop reports mic=false and polls until one is plugged in."""


def capture_devices(pcm_table: Path = Path("/proc/asound/pcm")) -> list[str]:
    """ALSA PCM devices that can capture, e.g. ``['Speakerphone: USB Audio : capture 1']``.

    The kernel's view of the hardware, so it also works inside the container. PipeWire hides the
    difference: with no input at all it hands ``default`` its *output monitor*, and arecord then
    happily records the speaker, which is why a successful arecord is not evidence of a mic."""
    try:
        lines = pcm_table.read_text().splitlines()
    except OSError:
        return []
    return [ln.strip() for ln in lines if "capture" in ln]


class Mic(Protocol):
    def chunks(self) -> AsyncIterator[bytes]: ...
    async def close(self) -> None: ...


# Device names routed through PipeWire/Pulse, where "it opened" doesn't mean a mic exists.
ROUTED_DEVICES = {"default", "pulse", "pipewire", "sysdefault"}


class AlsaMic:
    def __init__(
        self,
        device: str = "default",
        *,
        has_capture_hw: Callable[[], bool] = lambda: bool(capture_devices()),
    ) -> None:
        self.device = device
        self._has_capture_hw = has_capture_hw
        self._proc: asyncio.subprocess.Process | None = None

    async def chunks(self) -> AsyncIterator[bytes]:
        # An explicit hardware name (plughw:CARD=…) fails on its own if it's missing; the routed
        # names need the hardware check (see capture_devices).
        if self.device in ROUTED_DEVICES and not self._has_capture_hw():
            raise NoMicrophone("no microphone connected")
        exe = shutil.which("arecord")
        if not exe:
            raise RuntimeError("arecord not found — install alsa-utils")
        self._proc = await asyncio.create_subprocess_exec(
            exe,
            "-q",
            "-D",
            self.device,
            "-f",
            "S16_LE",
            "-r",
            str(SAMPLE_RATE),
            "-c",
            "1",
            "-t",
            "raw",
            "--buffer-time=200000",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        assert self._proc.stdout
        try:
            while True:
                try:
                    yield await self._proc.stdout.readexactly(CHUNK_BYTES)
                except asyncio.IncompleteReadError:
                    err = b""
                    if self._proc.stderr:
                        err = await self._proc.stderr.read()
                    raise RuntimeError(f"arecord stopped: {err.decode(errors='replace')}") from None
        finally:
            await self.close()

    async def close(self) -> None:
        if self._proc and self._proc.returncode is None:
            self._proc.kill()
            with contextlib.suppress(Exception):
                await self._proc.wait()


class FileMic:
    """Replays a WAV file (16 kHz mono s16) as if it were the microphone; used by `make demo`."""

    def __init__(self, path: Path, *, realtime: bool = True, tail_silence_s: float = 3.0) -> None:
        self.path = path
        self.realtime = realtime
        self.tail = tail_silence_s
        self.done = asyncio.Event()

    async def chunks(self) -> AsyncIterator[bytes]:
        with wave.open(str(self.path), "rb") as w:
            if w.getframerate() != SAMPLE_RATE or w.getnchannels() != 1 or w.getsampwidth() != 2:
                raise ValueError("demo audio must be 16 kHz mono 16-bit WAV")
            data = w.readframes(w.getnframes())
        data += b"\x00" * int(self.tail * SAMPLE_RATE * 2)
        t0 = time.monotonic()
        for i in range(0, len(data) - CHUNK_BYTES + 1, CHUNK_BYTES):
            if self.realtime:
                due = t0 + (i / 2) / SAMPLE_RATE
                await asyncio.sleep(max(0.0, due - time.monotonic()))
            yield data[i : i + CHUNK_BYTES]
        self.done.set()
        # Keep feeding silence so endpointing/timeouts can complete.
        while True:
            await asyncio.sleep(CHUNK_SAMPLES / SAMPLE_RATE if self.realtime else 0)
            yield b"\x00" * CHUNK_BYTES

    async def close(self) -> None:
        pass


class Player:
    """Streams PCM to ``aplay`` paced in real time, reporting levels as audio becomes audible.

    One ``aplay`` process per stream. ``stop()`` kills it: sound stops within milliseconds.
    """

    def __init__(
        self,
        device: str = "default",
        *,
        lead_ms: int = 160,
        on_level: Callable[[float], None] | None = None,
        sink: Callable[[bytes, int], None] | None = None,
    ) -> None:
        self.device = device
        self.lead = lead_ms / 1000
        self.on_level = on_level
        self.sink = sink  # test hook: receives (pcm, sample_rate) instead of a real device
        self.stream_id: int | None = None
        self._queue: asyncio.Queue[bytes | None] = asyncio.Queue()
        self._task: asyncio.Task[bool] | None = None
        self._proc: asyncio.subprocess.Process | None = None
        self._level_handles: list[asyncio.TimerHandle] = []

    @property
    def active(self) -> bool:
        # False the instant stop() is called, even before the cancelled task has unwound.
        return self.stream_id is not None and self._task is not None and not self._task.done()

    def start(self, stream_id: int, sample_rate: int) -> None:
        self.stop()
        self.stream_id = stream_id
        self._queue = asyncio.Queue()
        self._task = asyncio.create_task(self._run(sample_rate, self._queue))

    def feed(self, stream_id: int, pcm: bytes) -> None:
        if stream_id == self.stream_id and self.active:
            self._queue.put_nowait(pcm)

    def end(self, stream_id: int) -> None:
        if stream_id == self.stream_id and self.active:
            self._queue.put_nowait(None)

    async def wait(self) -> bool:
        """Wait for the current stream to finish. True if it played to the end."""
        if self._task is None:
            return False
        try:
            return await asyncio.shield(self._task)
        except asyncio.CancelledError:
            return False

    def stop(self) -> None:
        """Barge-in: silence now, drop everything queued."""
        self.stream_id = None
        if self._proc and self._proc.returncode is None:
            self._proc.kill()
        if self._task and not self._task.done():
            self._task.cancel()
        for h in self._level_handles:
            h.cancel()
        self._level_handles.clear()
        if self.on_level:
            self.on_level(0.0)

    async def _run(self, rate: int, queue: asyncio.Queue[bytes | None]) -> bool:
        loop = asyncio.get_running_loop()
        if self.sink is None:
            exe = shutil.which("aplay")
            if not exe:
                raise RuntimeError("aplay not found — install alsa-utils")
            self._proc = await asyncio.create_subprocess_exec(
                exe,
                "-q",
                "-D",
                self.device,
                "-f",
                "S16_LE",
                "-r",
                str(rate),
                "-c",
                "1",
                "-t",
                "raw",
                "--buffer-time=100000",
                stdin=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
        level_step = int(rate / 30) * 2  # ~30 Hz level updates, in bytes
        t0: float | None = None
        written_s = 0.0
        try:
            while (pcm := await queue.get()) is not None:
                if t0 is None:
                    t0 = loop.time()
                # Pace: never get more than `lead` seconds ahead of the speaker.
                ahead = (t0 + written_s) - loop.time()
                if ahead > self.lead:
                    await asyncio.sleep(ahead - self.lead)
                if self.on_level:
                    for off in range(0, len(pcm), level_step):
                        at = t0 + written_s + (off / 2) / rate
                        lvl = rms_level(pcm[off : off + level_step])
                        self._level_handles.append(
                            loop.call_at(at + 0.1, self.on_level, lvl)  # + ALSA buffer
                        )
                if self.sink:
                    self.sink(pcm, rate)
                else:
                    assert self._proc and self._proc.stdin
                    self._proc.stdin.write(pcm)
                    await self._proc.stdin.drain()
                written_s += len(pcm) / 2 / rate
            # Let the tail play out.
            if t0 is not None:
                await asyncio.sleep(max(0.0, t0 + written_s - loop.time() + 0.1))
            if self._proc and self._proc.stdin:
                self._proc.stdin.close()
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(self._proc.wait(), 2)
            return True
        except (BrokenPipeError, ConnectionResetError):
            return False
        finally:
            self._level_handles = [h for h in self._level_handles if not h.cancelled()]
            if self._proc and self._proc.returncode is None:
                self._proc.kill()
            if self.on_level:
                loop.call_later(0.12, self.on_level, 0.0)


def make_chime(rate: int = 24_000) -> bytes:
    """A soft two-note rising chime (~220 ms), synthesised so no asset is needed."""
    out = []
    for freq, dur, delay in ((880.0, 0.16, 0.0), (1318.5, 0.18, 0.06)):
        n = int(rate * (dur + delay))
        t = np.arange(n) / rate
        env = np.clip((t - delay) / 0.008, 0, 1) * np.exp(-np.clip(t - delay, 0, None) * 18)
        env[t < delay] = 0
        out.append(np.sin(2 * np.pi * freq * t) * env)
    length = max(len(x) for x in out)
    mix = sum(np.pad(x, (0, length - len(x))) for x in out) * 0.18
    return (np.clip(mix, -1, 1) * 32767).astype(np.int16).tobytes()
