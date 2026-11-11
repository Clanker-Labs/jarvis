"""Speech output: sentences → TTS → audio node, with subtitles and clean cancellation."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass

import numpy as np
from jarvis_protocol import TtsEnd, TtsSentence, TtsStart

from ..bus import EventBus
from ..hub import Hub
from .text import speakable
from .tts import TTSChain

log = logging.getLogger(__name__)


def resample(pcm: bytes, src: int, dst: int) -> bytes:
    if src == dst or not pcm:
        return pcm
    x = np.frombuffer(pcm, dtype=np.int16).astype(np.float32)
    n = max(1, round(len(x) * dst / src))
    y = np.interp(np.linspace(0, len(x) - 1, n), np.arange(len(x)), x)
    return np.clip(y, -32768, 32767).astype(np.int16).tobytes()


@dataclass
class SpeechResult:
    stream_id: int | None
    spoken: list[str]
    audio_s: float
    engine: str
    fallback: bool
    first_audio_at: float | None


class Speaker:
    def __init__(self, bus: EventBus, hub: Hub, tts: TTSChain) -> None:
        self.bus = bus
        self.hub = hub
        self.tts = tts
        self._next_stream = 1
        self.current: int | None = None

    def _new_stream(self) -> int:
        sid = self._next_stream
        self._next_stream = (self._next_stream + 1) % 0xFFFFFFFF or 1
        return sid

    async def speak(
        self,
        sentences: AsyncIterator[str],
        *,
        turn_id: str | None,
        on_first_audio: Callable[[], Awaitable[None] | None] | None = None,
    ) -> SpeechResult:
        """Speak sentences as they arrive. Cancel the task to stop (barge-in)."""
        sid: int | None = None
        rate = 0
        spoken: list[str] = []
        audio_bytes = 0
        engine, fallback, prefer = "", False, None
        first_at: float | None = None
        try:
            async for raw in sentences:
                text = speakable(raw)
                if not text:
                    continue
                utt = await self.tts.speak(text, prefer=prefer)
                prefer = utt.engine  # keep one voice for the rest of the reply
                engine, fallback = utt.engine, fallback or utt.fallback
                if sid is None:
                    sid, rate = self._new_stream(), utt.sample_rate
                    self.current = sid
                    start = TtsStart(stream_id=sid, sample_rate=rate, provider=utt.engine,
                                     turn_id=turn_id)  # fmt: skip
                    await self.hub.to_node(start)
                    self.bus.publish(start)
                self.bus.publish(TtsSentence(stream_id=sid, text=text, turn_id=turn_id))
                spoken.append(text)
                async for chunk in utt.audio:
                    pcm = resample(chunk, utt.sample_rate, rate)
                    if first_at is None:
                        first_at = time.time()
                        if on_first_audio:
                            res = on_first_audio()
                            if asyncio.iscoroutine(res):
                                await res
                    audio_bytes += len(pcm)
                    await self.hub.audio_to_node(sid, pcm)
        except asyncio.CancelledError:
            if sid is not None:
                await self._end(sid, turn_id, interrupted=True)
            raise
        if sid is not None:
            await self._end(sid, turn_id, interrupted=False)
        return SpeechResult(
            sid, spoken, audio_bytes / 2 / rate if rate else 0.0, engine, fallback, first_at
        )

    async def _end(self, sid: int, turn_id: str | None, *, interrupted: bool) -> None:
        end = TtsEnd(stream_id=sid, interrupted=interrupted, turn_id=turn_id)
        await self.hub.to_node(end)
        self.bus.publish(end)
        if self.current == sid and interrupted:
            self.current = None


async def one(text: str) -> AsyncIterator[str]:
    yield text
