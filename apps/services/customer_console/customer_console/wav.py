"""Measure and cut a WAV clip with the standard library (VOX-2).

Spec: ``project-docs/specs/projects_ai_chat.md`` §31 VOX-2 (Do 2 and Do 4) ·
``customer_console.md`` §6A.10a.

🔴 **The transcribe door bills a WAV clip by its own length.** The door reads
the clip with this module before any provider sees it, for every provider.
So the minutes on the usage row come from the bytes we sent, and never from
a figure the metered vendor reports. Another format keeps the old rule: the
provider's ``verbose_json`` duration, else zero and a loud log line.

⚠️ **Only the standard library reads the clip.** This module walks the RIFF
chunks with ``struct`` and cuts parts with ``wave``. It decodes no compressed
audio, so it adds no dependency and no codec to a cross-tenant service. A
clip that is not PCM WAV here is "not a WAV", whatever its file name says.

🔴 **The length counts the bytes that are present, never the header's
claim.** The ``wave`` module honours the ``data`` size field. A recorder that
streams writes zero or ``0xFFFFFFFF`` there, and a hostile caller writes a
small number. The provider decodes ALL the bytes and charges us for them. So
the parser finds the start of the ``data`` chunk and counts
``(len(audio) - data_start) // block_align``. The declared size is ignored.
A chunk after ``data`` (a trailing ``LIST``) therefore counts as audio, by a
few bytes. We bill the bytes we send, and that errs toward the platform.

A ``fmt `` chunk with a zero or absurd value (see the bounds below) is not a
measurable WAV. The caller then uses the non-WAV rule.
"""
from __future__ import annotations

import io
import struct
import wave
from dataclasses import dataclass, field
from decimal import Decimal

__all__ = ["WavClip", "read_wav", "split_wav", "wav_seconds"]


@dataclass(frozen=True)
class WavClip:
    """One PCM WAV clip, as the ``wave`` module read it."""

    channels: int
    sample_width: int
    frame_rate: int
    #: The PCM frames that are present. ``repr=False``, because a log line
    #: must never print customer audio.
    frames: bytes = field(repr=False)

    @property
    def frame_size(self) -> int:
        return self.channels * self.sample_width

    @property
    def frame_count(self) -> int:
        return len(self.frames) // self.frame_size

    @property
    def seconds(self) -> Decimal:
        """The length of the clip, from the frames that are present."""
        return Decimal(self.frame_count) / Decimal(self.frame_rate)


#: Bounds on the ``fmt `` chunk. A value outside them is not a clip we measure.
_MAX_CHANNELS = 8
_MIN_RATE = 1_000
_MAX_RATE = 384_000
_PCM_TAGS = frozenset({1, 0xFFFE})


def _layout(audio: bytes) -> tuple[int, int, int, int] | None:
    """``(channels, sample_width, frame_rate, data_start)``, or None. Never raises.

    Walks the chunks and ignores every declared size except the ones that
    step over a chunk before ``data``. The ``fmt `` chunk must come first.
    """
    if len(audio) < 12 or audio[0:4] != b"RIFF" or audio[8:12] != b"WAVE":
        return None
    pos = 12
    fmt: tuple[int, int, int] | None = None
    while pos + 8 <= len(audio):
        chunk_id = audio[pos : pos + 4]
        size = struct.unpack_from("<I", audio, pos + 4)[0]
        body = pos + 8
        if chunk_id == b"fmt ":
            if size < 16 or body + 16 > len(audio):
                return None
            tag, channels, rate, _, align, bits = struct.unpack_from("<HHIIHH", audio, body)
            if tag not in _PCM_TAGS or bits <= 0 or bits % 8:
                return None
            width = bits // 8
            if not 1 <= channels <= _MAX_CHANNELS or not 1 <= width <= 4:
                return None
            if not _MIN_RATE <= rate <= _MAX_RATE or align != channels * width:
                return None
            fmt = (channels, width, rate)
        elif chunk_id == b"data":
            if fmt is None:
                return None
            return fmt[0], fmt[1], fmt[2], body
        pos = body + size + (size & 1)
    return None


def read_wav(audio: bytes) -> WavClip | None:
    """The clip, or None when it is not a measurable PCM WAV. Never raises."""
    layout = _layout(audio)
    if layout is None:
        return None
    channels, width, rate, start = layout
    size = channels * width
    usable = (len(audio) - start) // size * size
    return WavClip(
        channels=channels,
        sample_width=width,
        frame_rate=rate,
        frames=audio[start : start + usable],
    )


def wav_seconds(audio: bytes) -> Decimal | None:
    """How many seconds of audio a WAV clip holds, or None. Never raises.

    It keeps no copy of the frames. The door calls it for every clip, and
    §6A.10a clause 1 records the memory that one request may hold.
    """
    layout = _layout(audio)
    if layout is None:
        return None
    channels, width, rate, start = layout
    return Decimal((len(audio) - start) // (channels * width)) / Decimal(rate)


def split_wav(clip: WavClip, max_seconds: int) -> list[bytes]:
    """Cut a clip into WAV files of at most ``max_seconds`` each, in order.

    Each part keeps the clip's channels, sample width and rate. A clip with
    no frames gives no parts, so a caller sends nothing to a vendor for it.
    """
    if max_seconds <= 0:
        raise ValueError("max_seconds must be positive")
    step = clip.frame_rate * max_seconds * clip.frame_size
    usable = clip.frame_count * clip.frame_size
    parts: list[bytes] = []
    for start in range(0, usable, step):
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as writer:
            writer.setnchannels(clip.channels)
            writer.setsampwidth(clip.sample_width)
            writer.setframerate(clip.frame_rate)
            writer.writeframes(clip.frames[start : min(start + step, usable)])
        parts.append(buffer.getvalue())
    return parts
