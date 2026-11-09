"""The media a single-model Try sends, built in code (MS-2, §6A.18).

Spec: ``project-docs/specs/customer_console.md`` §6A.18 "API shapes", the
adopt steps and "The Try helpers". Fence:
``tests/unit/test_catalog_connect_adopt_r8.py``.

⚠️ **A built clip, not recorded speech.** The spec asks for a bundled WAV
of 3 seconds of Hindi-English speech. The build had no way to record or
synthesise speech, so the clip is 3 seconds of soft tones in the rhythm of
speech. A vendor that answers it with a readable body has proved the key,
the model id, the verb and the handler. The transcript can be empty.

**Pure functions, no network, no file.** The bytes come from arithmetic, so
the same build always sends the same clip, and the repo holds no binary.
"""
from __future__ import annotations

import io
import math
import struct
import wave
import zlib
from functools import lru_cache

__all__ = [
    "CHAT_PROMPT",
    "CLIP_RATE",
    "CLIP_SECONDS",
    "IMAGE_QUESTION",
    "try_clip",
    "try_image",
]

#: The length of the clip. Short, so a Try costs a fraction of a rupee.
CLIP_SECONDS = 3
#: 16 kHz mono PCM: the format every speech vendor in the registry takes.
CLIP_RATE = 16_000

#: What the chat Try asks. One line, so the answer costs a few tokens.
CHAT_PROMPT = "Reply with one short sentence that says this test works."

#: What the image Try asks about the bundled picture.
IMAGE_QUESTION = "Describe this image in one sentence."

#: The side of the bundled picture, in pixels.
_IMAGE_SIDE = 128


@lru_cache(maxsize=1)
def try_clip() -> bytes:
    """A 3 second PCM WAV clip: soft tones that rise and fall 3 times a second."""
    frames = bytearray()
    for i in range(CLIP_SECONDS * CLIP_RATE):
        t = i / CLIP_RATE
        swell = 0.5 * (1.0 - math.cos(2.0 * math.pi * 3.0 * t))
        sample = swell * (
            0.30 * math.sin(2.0 * math.pi * 220.0 * t)
            + 0.15 * math.sin(2.0 * math.pi * 440.0 * t)
            + 0.08 * math.sin(2.0 * math.pi * 660.0 * t)
        )
        frames += struct.pack("<h", int(sample * 16_000))
    buf = io.BytesIO()
    with wave.open(buf, "wb") as clip:
        clip.setnchannels(1)
        clip.setsampwidth(2)
        clip.setframerate(CLIP_RATE)
        clip.writeframes(bytes(frames))
    return buf.getvalue()


def _png_chunk(tag: bytes, data: bytes) -> bytes:
    crc = zlib.crc32(tag + data) & 0xFFFFFFFF
    return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)


@lru_cache(maxsize=1)
def try_image() -> bytes:
    """A 128 pixel PNG: a red square on a white ground."""
    side = _IMAGE_SIDE
    low, high = side // 4, 3 * side // 4
    rows = bytearray()
    for y in range(side):
        rows.append(0)  # PNG filter type 0 for each row
        for x in range(side):
            inside = low <= x < high and low <= y < high
            rows += bytes((210, 30, 30)) if inside else bytes((255, 255, 255))
    header = struct.pack(">IIBBBBB", side, side, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", header)
        + _png_chunk(b"IDAT", zlib.compress(bytes(rows), 9))
        + _png_chunk(b"IEND", b"")
    )
