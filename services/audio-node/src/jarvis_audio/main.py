"""``jarvis-audio-node``: run the Pi-side audio service."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal

from .audio_io import AlsaMic
from .detect import Vad, WakeWord
from .models import ensure_models
from .node import AudioNode
from .settings import NodeSettings

log = logging.getLogger("jarvis_audio")


async def amain() -> None:
    s = NodeSettings()  # type: ignore[call-arg]
    logging.basicConfig(
        level=s.log_level.upper(),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    logging.getLogger("websockets").setLevel(logging.WARNING)
    ensure_models(s)
    node = AudioNode(
        s,
        mic=AlsaMic(s.input_device),
        wake=WakeWord(s.models_dir, s.wake_model, s.wake_threshold, s.wake_refractory_s),
        vad=Vad(s.models_dir),
    )
    loop = asyncio.get_running_loop()
    task = asyncio.current_task()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda: (node.stop(), task and task.cancel()))
    log.info(
        "audio node up: mic=%s speaker=%s wake=%s", s.input_device, s.output_device, s.wake_model
    )
    with contextlib.suppress(asyncio.CancelledError):
        await node.run()


def main() -> None:
    asyncio.run(amain())


if __name__ == "__main__":
    main()
