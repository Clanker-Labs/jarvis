"""Binary audio framing.

Binary WebSocket frames are ``<uint32 little-endian stream_id><PCM s16le mono>``.

* audio node → brain: microphone audio, 16 kHz. ``stream_id`` is the capture id announced by
  the matching ``listen.start`` (its ``turn_id`` hashed is not needed — ids are simple counters).
* brain → audio node: TTS audio at the ``sample_rate`` announced in ``tts.start``. The node drops
  frames whose ``stream_id`` is not the current stream, which makes barge-in race-free.
"""

from __future__ import annotations

import struct

MIC_SAMPLE_RATE = 16_000
HEADER = struct.Struct("<I")


def pack_audio(stream_id: int, pcm: bytes) -> bytes:
    return HEADER.pack(stream_id & 0xFFFFFFFF) + pcm


def unpack_audio(frame: bytes) -> tuple[int, bytes]:
    if len(frame) < HEADER.size:
        raise ValueError("audio frame shorter than header")
    (stream_id,) = HEADER.unpack_from(frame)
    return stream_id, frame[HEADER.size :]
