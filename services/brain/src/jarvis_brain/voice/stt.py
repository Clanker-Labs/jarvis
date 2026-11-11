"""Speech-to-text adapters and the fallback chain.

* ``deepgram``       — cloud, streaming (partials while you talk)
* ``openai``         — cloud, batch on end of utterance
* ``whisper_remote`` — any OpenAI-compatible transcription server on the tailnet (faster-whisper)
* ``whisper_local``  — faster-whisper in-process (offline fallback)

The chain always buffers the whole utterance, so if the streaming engine dies mid-utterance the
next engine transcribes the buffer — the user never has to repeat themselves.
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import json
import logging
import time
import wave
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol
from urllib.parse import urlencode

import httpx
import numpy as np
import websockets

from ..config import VoiceConfig
from ..secrets import SecretStore

log = logging.getLogger(__name__)

RATE = 16_000
PartialCb = Callable[[str], Awaitable[None] | None]


class STTError(RuntimeError):
    pass


class StreamSession(Protocol):
    async def feed(self, pcm: bytes) -> None: ...
    async def finish(self) -> str: ...
    async def aclose(self) -> None: ...


class STTEngine(Protocol):
    id: str
    streaming: bool
    local: bool

    def available(self) -> bool: ...
    async def transcribe(self, pcm: bytes) -> str: ...
    async def open_stream(self, on_partial: PartialCb) -> StreamSession: ...


def to_wav(pcm: bytes, rate: int = RATE) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return buf.getvalue()


async def _call(cb: PartialCb, text: str) -> None:
    res = cb(text)
    if asyncio.iscoroutine(res):
        await res


# --------------------------------------------------------------------------- Deepgram (streaming)


class _DeepgramSession:
    def __init__(self, ws: websockets.ClientConnection, on_partial: PartialCb) -> None:
        self.ws = ws
        self.on_partial = on_partial
        self.finals: list[str] = []
        self.interim = ""
        self._reader = asyncio.create_task(self._read())

    async def _read(self) -> None:
        async for raw in self.ws:
            if isinstance(raw, bytes):
                continue
            msg = json.loads(raw)
            if msg.get("type") != "Results":
                continue
            alt = (msg.get("channel", {}).get("alternatives") or [{}])[0]
            text = (alt.get("transcript") or "").strip()
            if msg.get("is_final"):
                if text:
                    self.finals.append(text)
                self.interim = ""
            else:
                self.interim = text
            await _call(self.on_partial, " ".join([*self.finals, self.interim]).strip())

    async def feed(self, pcm: bytes) -> None:
        if self._reader.done():
            raise STTError(f"deepgram stream ended: {self._reader.exception()!r}")
        await self.ws.send(pcm)

    async def finish(self) -> str:
        await self.ws.send(json.dumps({"type": "CloseStream"}))
        try:
            await asyncio.wait_for(self._reader, timeout=4)
        except TimeoutError as e:
            raise STTError("deepgram finalisation timed out") from e
        return " ".join(self.finals).strip()

    async def aclose(self) -> None:
        self._reader.cancel()
        with contextlib.suppress(Exception):
            await self.ws.close()


@dataclass
class DeepgramSTT:
    secrets: SecretStore
    cfg: Callable[[], VoiceConfig]
    id: str = "deepgram"
    streaming: bool = True
    local: bool = False

    def available(self) -> bool:
        return self.secrets.has("DEEPGRAM_API_KEY")

    async def open_stream(self, on_partial: PartialCb) -> StreamSession:
        c = self.cfg()
        q = urlencode(
            {
                "model": c.deepgram_model,
                "language": c.language,
                "encoding": "linear16",
                "sample_rate": RATE,
                "channels": 1,
                "interim_results": "true",
                "punctuate": "true",
                "smart_format": "true",
                "endpointing": "false",
            }
        )
        ws = await websockets.connect(
            f"wss://api.deepgram.com/v1/listen?{q}",
            additional_headers={"Authorization": f"Token {self.secrets.get('DEEPGRAM_API_KEY')}"},
            open_timeout=3,
        )
        return _DeepgramSession(ws, on_partial)

    async def transcribe(self, pcm: bytes) -> str:
        c = self.cfg()
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(
                "https://api.deepgram.com/v1/listen",
                params={"model": c.deepgram_model, "language": c.language, "smart_format": "true"},
                headers={
                    "Authorization": f"Token {self.secrets.get('DEEPGRAM_API_KEY')}",
                    "Content-Type": "audio/wav",
                },
                content=to_wav(pcm),
            )
            r.raise_for_status()
            alts = r.json()["results"]["channels"][0]["alternatives"]
            return (alts[0]["transcript"] if alts else "").strip()


# --------------------------------------------------------------------------- OpenAI-compatible


@dataclass
class OpenAICompatSTT:
    """OpenAI's /v1/audio/transcriptions — also served by faster-whisper servers (speaches)."""

    id: str
    base_url: Callable[[], str]
    model: Callable[[], str]
    api_key: Callable[[], str]
    local: bool = False
    streaming: bool = False
    timeout: float = 20

    def available(self) -> bool:
        return bool(self.base_url()) and (self.local or bool(self.api_key()))

    async def transcribe(self, pcm: bytes) -> str:
        headers = {"Authorization": f"Bearer {self.api_key()}"} if self.api_key() else {}
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            r = await client.post(
                self.base_url().rstrip("/") + "/audio/transcriptions",
                headers=headers,
                data={"model": self.model(), "response_format": "json"},
                files={"file": ("speech.wav", to_wav(pcm), "audio/wav")},
            )
            r.raise_for_status()
            return str(r.json().get("text", "")).strip()

    async def open_stream(self, on_partial: PartialCb) -> StreamSession:
        raise NotImplementedError


# --------------------------------------------------------------------------- faster-whisper local


@dataclass
class LocalWhisperSTT:
    cfg: Callable[[], VoiceConfig]
    models_dir: Path
    id: str = "whisper_local"
    streaming: bool = False
    local: bool = True
    _model: object | None = field(default=None, repr=False)
    _model_name: str = ""
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)

    def available(self) -> bool:
        try:
            import faster_whisper  # noqa: F401
        except ImportError:
            return False
        return True

    def _load(self) -> object:
        name = self.cfg().whisper_local_model
        if self._model is None or self._model_name != name:
            from faster_whisper import WhisperModel

            t = time.monotonic()
            self._model = WhisperModel(
                name,
                device="cpu",
                compute_type="int8",
                cpu_threads=4,
                download_root=str(self.models_dir / "whisper"),
            )
            self._model_name = name
            log.info("loaded faster-whisper %s in %.1fs", name, time.monotonic() - t)
        return self._model

    def warm(self) -> None:
        self._load()

    async def transcribe(self, pcm: bytes) -> str:
        audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0

        def run() -> str:
            model = self._load()
            segments, _ = model.transcribe(  # type: ignore[attr-defined]
                audio, language=self.cfg().language or None, beam_size=1, vad_filter=False
            )
            return " ".join(s.text.strip() for s in segments).strip()

        async with self._lock:
            return await asyncio.to_thread(run)

    async def open_stream(self, on_partial: PartialCb) -> StreamSession:
        raise NotImplementedError


# --------------------------------------------------------------------------- chain


@dataclass
class Transcript:
    text: str
    engine: str
    fallback: bool  # True if a lower-priority engine produced it


class ChainSession:
    """One utterance: stream to the first engine that can, buffer everything, fall back on error."""

    def __init__(self, engines: list[STTEngine], on_partial: PartialCb) -> None:
        self.engines = engines
        self.on_partial = on_partial
        self.buf = bytearray()
        self.stream: StreamSession | None = None
        self.stream_engine: STTEngine | None = None
        self._opening: asyncio.Task[None] | None = None
        self._pending: list[bytes] = []
        if engines and engines[0].streaming:
            self._opening = asyncio.create_task(self._open(engines[0]))

    async def _open(self, engine: STTEngine) -> None:
        try:
            self.stream = await engine.open_stream(self.on_partial)
            self.stream_engine = engine
            for chunk in self._pending:
                await self.stream.feed(chunk)
        except Exception as e:
            log.warning("STT %s stream failed to open: %s", engine.id, e)
            self.stream = None
        self._pending.clear()

    async def feed(self, pcm: bytes) -> None:
        self.buf += pcm
        if self._opening and not self._opening.done():
            self._pending.append(pcm)
            return
        if self.stream:
            try:
                await self.stream.feed(pcm)
            except Exception as e:
                log.warning("STT stream feed failed: %s", e)
                await self._drop_stream()

    async def _drop_stream(self) -> None:
        if self.stream:
            with contextlib.suppress(Exception):
                await self.stream.aclose()
        self.stream = None

    async def finish(self) -> Transcript:
        if self._opening:
            with contextlib.suppress(Exception):
                await self._opening
        if len(self.buf) < RATE * 2 * 0.25:  # < 250 ms: nothing worth sending
            await self._drop_stream()
            return Transcript("", self.engines[0].id if self.engines else "none", False)
        errors = []
        if self.stream and self.stream_engine:
            try:
                text = await self.stream.finish()
                return Transcript(text, self.stream_engine.id, False)
            except Exception as e:
                errors.append(f"{self.stream_engine.id}: {e}")
                log.warning("STT %s finish failed: %s", self.stream_engine.id, e)
            finally:
                await self._drop_stream()
        for i, engine in enumerate(self.engines):
            if engine is self.stream_engine:
                continue
            try:
                text = await engine.transcribe(bytes(self.buf))
                return Transcript(text, engine.id, i > 0)
            except Exception as e:
                errors.append(f"{engine.id}: {e!r}")
                log.warning("STT %s failed: %r", engine.id, e)
        raise STTError("; ".join(errors) or "no STT engine available")

    async def aclose(self) -> None:
        if self._opening:
            self._opening.cancel()
        await self._drop_stream()


class STTChain:
    def __init__(self, engines: dict[str, STTEngine], cfg: Callable[[], VoiceConfig]) -> None:
        self.engines = engines
        self.cfg = cfg

    def active(self) -> list[STTEngine]:
        return [
            self.engines[e]
            for e in self.cfg().stt_chain
            if e in self.engines and self.engines[e].available()
        ]

    def open(self, on_partial: PartialCb) -> ChainSession:
        return ChainSession(self.active(), on_partial)
