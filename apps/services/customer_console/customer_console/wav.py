"""Measure and cut a WAV clip with the standard library (VOX-2).

Spec: ``project-docs/specs/projects_ai_chat.md`` §31 VOX-2 (Do 2 and Do 4) ·
``customer_console.md`` §6A.10a.

🔴 **The transcribe door bills a WAV clip by its own length.** The door reads
the clip with this module before any provider sees it, for every provider.
So the minutes on the usage row come from the bytes we sent, and never from
a figure the metered vendor reports. Another format keeps the old rule: the
provider's ``verbose_json`` duration, else zero and a loud log line.

⚠️ **Only the standard library ``wave`` module reads the clip.** It parses the
RIFF header and copies PCM frames. It decodes no compressed audio, so it adds
no dependency and no codec to a cross-tenant service. A clip that ``wave``
cannot read is "not a WAV" here, whatever its file name says.

⚠️ **The length counts the frames that are present, never the header's
claim.** A recorder that streams writes a data size of zero or of
``0xFFFFFFFF``. Read as the header states it, the second one bills 37 hours
for a short clip. So :func:`read_wav` reads the frames and counts them.
"""
from __future__ import annotations

import io
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


def read_wav(audio: bytes) -> WavClip | None:
    """The clip, or None when the ``wave`` module cannot read it. Never raises.

    None covers every clip that is not PCM WAV: webm, mp3, a float WAV, a
    truncated header, and bytes that only start with ``RIFF``. The caller
    then falls back to the provider's own duration.
    """
    try:
        with wave.open(io.BytesIO(audio), "rb") as reader:
            channels = reader.getnchannels()
            sample_width = reader.getsampwidth()
            frame_rate = reader.getframerate()
            if channels <= 0 or sample_width <= 0 or frame_rate <= 0:
                return None
            # `readframes` stops at the end of the bytes, so a header that
            # claims more frames than the file holds cannot inflate the bill.
            frames = reader.readframes(reader.getnframes())
    except Exception:  # a hostile header can raise more than `wave.Error`
        return None
    return WavClip(
        channels=channels,
        sample_width=sample_width,
        frame_rate=frame_rate,
        frames=frames,
    )


#: How many frames :func:`wav_seconds` reads at a time.
_COUNT_BLOCK_FRAMES = 65_536


def wav_seconds(audio: bytes) -> Decimal | None:
    """How many seconds of audio a WAV clip holds, or None. Never raises.

    ⚠️ **It counts in blocks and keeps no copy of the frames.** The door
    calls it for every clip, and §6A.10a clause 1 records the memory that one
    request may hold. A full copy here would add 25 MB to that bound.
    """
    try:
        with wave.open(io.BytesIO(audio), "rb") as reader:
            frame_size = reader.getnchannels() * reader.getsampwidth()
            frame_rate = reader.getframerate()
            if frame_size <= 0 or frame_rate <= 0:
                return None
            present = 0
            while True:
                # `readframes` stops at the end of the bytes, so a header that
                # claims more frames than the file holds cannot inflate this.
                block = reader.readframes(_COUNT_BLOCK_FRAMES)
                if not block:
                    break
                present += len(block) // frame_size
    except Exception:  # a hostile header can raise more than `wave.Error`
        return None
    return Decimal(present) / Decimal(frame_rate)


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
