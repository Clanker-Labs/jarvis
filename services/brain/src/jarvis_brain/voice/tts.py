"""Text-to-speech adapters (streaming PCM s16le mono) and the fallback chain.

* ``openai``     — gpt-4o-mini-tts, voice "fable" with calm-British-butler instructions (24 kHz)
* ``elevenlabs`` — streaming, "George" (24 kHz)
* ``piper``      — local, en_GB-alan-medium (22.05 kHz), the offline fallback
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

import httpx

from ..config import VoiceConfig
from ..secrets import SecretStore

log = logging.getLogger(__name__)


class TTSError(RuntimeError):
    pass


class TTSEngine(Protocol):
    id: str
    local: bool

    def available(self) -> bool: ...
    def sample_rate(self) -> int: ...
    def synthesize(self, text: str) -> AsyncIterator[bytes]: ...


def _even(buf: bytearray) -> bytes:
    """Return the largest even-length prefix (whole samples), keeping the odd byte buffered."""
    n = len(buf) - (len(buf) % 2)
    out = bytes(buf[:n])
    del buf[:n]
    return out


@dataclass
class OpenAITTS:
    secrets: SecretStore
    cfg: Callable[[], VoiceConfig]
    base_url: str = "https://api.openai.com/v1"
    id: str = "openai"
    local: bool = False

    def available(self) -> bool:
        return self.secrets.has("OPENAI_API_KEY")

    def sample_rate(self) -> int:
        return 24_000

    async def synthesize(self, text: str) -> AsyncIterator[bytes]:
        c = self.cfg()
        body = {
            "model": c.openai_tts_model,
            "voice": c.openai_voice,
            "input": text,
            "response_format": "pcm",
        }
        if c.openai_tts_instructions and "tts" in c.openai_tts_model:
            body["instructions"] = c.openai_tts_instructions
        async with (
            httpx.AsyncClient(timeout=httpx.Timeout(20, connect=4)) as client,
            client.stream(
                "POST",
                f"{self.base_url}/audio/speech",
                headers={"Authorization": f"Bearer {self.secrets.get('OPENAI_API_KEY')}"},
                json=body,
            ) as r,
        ):
            if r.status_code >= 400:
                raise TTSError(f"openai tts {r.status_code}: {(await r.aread())[:200]!r}")
            buf = bytearray()
            async for chunk in r.aiter_bytes(4096):
                buf += chunk
                if len(buf) >= 2:
                    yield _even(buf)


@dataclass
class ElevenLabsTTS:
    secrets: SecretStore
    cfg: Callable[[], VoiceConfig]
    id: str = "elevenlabs"
    local: bool = False

    def available(self) -> bool:
        return self.secrets.has("ELEVENLABS_API_KEY")

    def sample_rate(self) -> int:
        return 24_000

    async def synthesize(self, text: str) -> AsyncIterator[bytes]:
        c = self.cfg()
        async with (
            httpx.AsyncClient(timeout=httpx.Timeout(20, connect=4)) as client,
            client.stream(
                "POST",
                f"https://api.elevenlabs.io/v1/text-to-speech/{c.elevenlabs_voice_id}/stream",
                params={"output_format": "pcm_24000", "optimize_streaming_latency": 2},
                headers={"xi-api-key": self.secrets.get("ELEVENLABS_API_KEY")},
                json={"text": text, "model_id": c.elevenlabs_model},
            ) as r,
        ):
            if r.status_code >= 400:
                raise TTSError(f"elevenlabs {r.status_code}: {(await r.aread())[:200]!r}")
            buf = bytearray()
            async for chunk in r.aiter_bytes(4096):
                buf += chunk
                if len(buf) >= 2:
                    yield _even(buf)


PIPER_URL = (
    "https://huggingface.co/rhasspy/piper-voices/resolve/main/{family}/{code}/{name}/{quality}/"
    "{code}-{name}-{quality}{ext}?download=true"
)


@dataclass
class PiperTTS:
    cfg: Callable[[], VoiceConfig]
    models_dir: Path
    id: str = "piper"
    local: bool = True
    _voice: object | None = field(default=None, repr=False)
    _voice_name: str = ""
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)

    def available(self) -> bool:
        try:
            import piper  # noqa: F401
        except ImportError:
            return False
        return True

    def _paths(self, name: str) -> tuple[Path, Path]:
        d = self.models_dir / "piper"
        return d / f"{name}.onnx", d / f"{name}.onnx.json"

    def download(self, name: str | None = None) -> None:
        name = name or self.cfg().piper_voice
        model, conf = self._paths(name)
        if model.exists() and conf.exists():
            return
        code, voice, quality = name.split("-", 2)
        model.parent.mkdir(parents=True, exist_ok=True)
        for ext, dest in ((".onnx", model), (".onnx.json", conf)):
            url = PIPER_URL.format(
                family=code.split("_")[0], code=code, name=voice, quality=quality, ext=ext
            )
            log.info("downloading piper voice %s%s", name, ext)
            with httpx.stream("GET", url, follow_redirects=True, timeout=60) as r:
                r.raise_for_status()
                tmp = dest.with_suffix(dest.suffix + ".part")
                with tmp.open("wb") as f:
                    for chunk in r.iter_bytes(1 << 16):
                        f.write(chunk)
                tmp.rename(dest)

    def _load(self) -> object:
        name = self.cfg().piper_voice
        if self._voice is None or self._voice_name != name:
            from piper import PiperVoice

            self.download(name)
            model, conf = self._paths(name)
            self._voice = PiperVoice.load(str(model), config_path=str(conf))
            self._voice_name = name
        return self._voice

    def warm(self) -> None:
        self._load()

    def sample_rate(self) -> int:
        voice = self._load()
        return int(voice.config.sample_rate)  # type: ignore[attr-defined]

    async def synthesize(self, text: str) -> AsyncIterator[bytes]:
        q: asyncio.Queue[bytes | BaseException | None] = asyncio.Queue()
        loop = asyncio.get_running_loop()

        def run() -> None:
            try:
                voice = self._load()
                for chunk in voice.synthesize(text):  # type: ignore[attr-defined]
                    loop.call_soon_threadsafe(q.put_nowait, chunk.audio_int16_bytes)
                loop.call_soon_threadsafe(q.put_nowait, None)
            except BaseException as e:  # forwarded to the consumer
                loop.call_soon_threadsafe(q.put_nowait, e)

        async with self._lock:
            fut = loop.run_in_executor(None, run)
            while (item := await q.get()) is not None:
                if isinstance(item, BaseException):
                    raise TTSError(f"piper: {item}") from item
                yield item
            await fut


@dataclass
class Utterance:
    engine: str
    sample_rate: int
    fallback: bool
    audio: AsyncIterator[bytes]


class TTSChain:
    def __init__(self, engines: dict[str, TTSEngine], cfg: Callable[[], VoiceConfig]) -> None:
        self.engines = engines
        self.cfg = cfg
        self.sticky: str | None = None  # once we fall back mid-reply, keep that voice

    def active(self) -> list[TTSEngine]:
        return [
            self.engines[e]
            for e in self.cfg().tts_chain
            if e in self.engines and self.engines[e].available()
        ]

    async def speak(self, text: str, *, prefer: str | None = None) -> Utterance:
        """Start synthesising ``text``; falls through the chain until one produces audio.

        Engines are only swapped *before* the first byte — never mid-sentence.
        """
        engines = self.active()
        if prefer:
            engines.sort(key=lambda e: e.id != prefer)
        errors: list[str] = []
        primary = engines[0].id if engines else None
        for engine in engines:
            gen = engine.synthesize(text).__aiter__()
            try:
                # Cloud engines must answer fast or we move on; local ones may be loading a model.
                first = await asyncio.wait_for(gen.__anext__(), timeout=30 if engine.local else 6)
            except StopAsyncIteration:
                return Utterance(engine.id, engine.sample_rate(), engine.id != primary, _empty())
            except Exception as e:
                errors.append(f"{engine.id}: {e!r}")
                log.warning("TTS %s failed: %r", engine.id, e)
                await _aclose(gen)
                continue
            return Utterance(
                engine.id, engine.sample_rate(), engine.id != primary, _prepend(first, gen)
            )
        raise TTSError("; ".join(errors) or "no TTS engine available")


async def _empty() -> AsyncIterator[bytes]:
    return
    yield b""


async def _prepend(first: bytes, rest: AsyncIterator[bytes]) -> AsyncIterator[bytes]:
    yield first
    async for chunk in rest:
        yield chunk


async def _aclose(gen: AsyncIterator[bytes]) -> None:
    aclose = getattr(gen, "aclose", None)
    if aclose:
        try:
            await aclose()
        except Exception:
            log.debug("closing TTS generator failed", exc_info=True)
