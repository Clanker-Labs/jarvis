"""Download the wake word, feature and VAD models into JARVIS_MODELS_DIR (idempotent).

uv run --package jarvis-audio-node python -m jarvis_audio.models
"""

from __future__ import annotations

import logging

from .settings import NodeSettings

log = logging.getLogger(__name__)


def ensure_models(settings: NodeSettings) -> None:
    d = settings.models_dir
    needed = ["melspectrogram.onnx", "embedding_model.onnx", "silero_vad.onnx"]
    have_wake = any(d.glob(f"{settings.wake_model}*.onnx"))
    if all((d / n).exists() for n in needed) and have_wake:
        return
    from openwakeword.utils import download_models

    d.mkdir(parents=True, exist_ok=True)
    log.info("downloading openWakeWord models to %s", d)
    download_models(model_names=[settings.wake_model], target_directory=str(d))
    # download_models fetches both .tflite and .onnx; only ONNX is used.
    for f in d.glob("*.tflite"):
        f.unlink()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    s = NodeSettings()  # type: ignore[call-arg]
    ensure_models(s)
    print(sorted(p.name for p in s.models_dir.iterdir()))
