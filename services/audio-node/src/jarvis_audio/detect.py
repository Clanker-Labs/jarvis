"""Wake word (openWakeWord) + voice activity (Silero, via openWakeWord's bundled ONNX model)."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import numpy as np

from .audio_io import CHUNK_SAMPLES, SAMPLE_RATE

log = logging.getLogger(__name__)

CHUNK_MS = CHUNK_SAMPLES * 1000 // SAMPLE_RATE  # 80


class WakeWord:
    def __init__(self, models_dir: Path, model: str, threshold: float, refractory_s: float):
        from openwakeword.model import Model  # heavy import, keep lazy

        path = Path(model)
        if not path.exists():
            candidates = sorted(models_dir.glob(f"{model}*.onnx"))
            if not candidates:
                raise FileNotFoundError(
                    f"wake model {model!r} not found in {models_dir} — run "
                    "`python -m jarvis_audio.models`"
                )
            path = candidates[0]
        self.name = path.stem
        self.threshold = threshold
        self.refractory_s = refractory_s
        self._last = 0.0
        self._model = Model(
            wakeword_models=[str(path)],
            inference_framework="onnx",
            melspec_model_path=str(models_dir / "melspectrogram.onnx"),
            embedding_model_path=str(models_dir / "embedding_model.onnx"),
        )

    def process(self, pcm: bytes) -> float | None:
        """Feed one 80 ms chunk. Returns the score when the wake word fires, else None."""
        scores = self._model.predict(np.frombuffer(pcm, dtype=np.int16))
        score = max(scores.values()) if scores else 0.0
        now = time.monotonic()
        if score >= self.threshold and now - self._last > self.refractory_s:
            self._last = now
            self._model.reset()
            return float(score)
        return None

    def reset(self) -> None:
        self._model.reset()


class Vad:
    """Silero VAD probability for an 80 ms chunk."""

    def __init__(self, models_dir: Path):
        from openwakeword.vad import VAD

        self._vad = VAD(model_path=str(models_dir / "silero_vad.onnx"))

    def prob(self, pcm: bytes) -> float:
        x = np.frombuffer(pcm, dtype=np.int16)
        return float(self._vad.predict(x, frame_size=640))

    def reset(self) -> None:
        self._vad.reset_states()


EndReason = Literal["silence", "max_duration", "timeout"]


@dataclass
class Endpointer:
    """Decides when an utterance starts and ends from per-chunk speech probabilities.

    Pure (no audio, no clock) so it is unit-testable: time advances by one chunk per ``push``.
    """

    threshold: float = 0.5
    end_silence_ms: int = 700
    max_utterance_s: float = 30.0
    no_speech_timeout_s: float = 6.0
    start_chunks: int = 2  # consecutive speech chunks needed to count as "speech started"

    elapsed_ms: int = 0
    speech_started: bool = False
    _run: int = 0
    _silence_ms: int = 0
    _speech_ms: int = field(default=0)

    def push(self, prob: float) -> EndReason | None:
        self.elapsed_ms += CHUNK_MS
        speech = prob >= self.threshold
        # Hysteresis: once talking, a slightly lower probability still counts as speech.
        if self.speech_started and prob >= self.threshold * 0.7:
            speech = True
        if not self.speech_started:
            self._run = self._run + 1 if speech else 0
            if self._run >= self.start_chunks:
                self.speech_started = True
                self._speech_ms = self._run * CHUNK_MS
            elif self.elapsed_ms >= self.no_speech_timeout_s * 1000:
                return "timeout"
            return None
        if speech:
            self._silence_ms = 0
            self._speech_ms += CHUNK_MS
        else:
            self._silence_ms += CHUNK_MS
            if self._silence_ms >= self.end_silence_ms:
                return "silence"
        if self.elapsed_ms >= self.max_utterance_s * 1000:
            return "max_duration"
        return None
