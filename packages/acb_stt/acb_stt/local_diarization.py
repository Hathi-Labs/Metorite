"""Optional local (self-hosted) speaker diarization via sherpa-onnx.

A *free*, CPU-only alternative to paying a cloud provider (Deepgram) for
"who spoke when". It runs as an ADDITIVE post-process on a transcript that has
no speakers yet (i.e. Whisper output): decode the audio → sherpa-onnx pyannote
segmentation + speaker embeddings + clustering → merge each transcript segment
onto its max-overlap speaker turn.

Design (spec: note_taker_app.md §3.4, Tier-B):
- **Opt-in.** Does nothing unless ``NOTES_LOCAL_DIARIZATION`` is truthy AND the
  two ONNX models are provisioned (``SHERPA_SEG_MODEL`` / ``SHERPA_EMB_MODEL``).
- **Fail-safe.** ANY problem (package/models absent, ffmpeg missing, decode or
  inference error, OOM) is swallowed — the transcript is returned unchanged
  (speaker-less), never raising. So enabling it can't break transcription, and
  "rolling back" is just turning the flag off / selecting a Deepgram model
  (which already diarizes, so this pass is skipped entirely).
- sherpa-onnx is an OPTIONAL dependency (``acb-stt[local-diar]``); this module
  imports it lazily so the package installs and imports fine without it.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import tempfile

from acb_common.child_env import child_env

from acb_stt.types import TranscriptResult, TranscriptSegmentData

_SAMPLE_RATE = 16_000


def enabled() -> bool:
    return os.environ.get("NOTES_LOCAL_DIARIZATION", "").lower() in ("1", "true", "yes", "on")


def _seg_model() -> str:
    return os.environ.get("SHERPA_SEG_MODEL", "")


def _emb_model() -> str:
    return os.environ.get("SHERPA_EMB_MODEL", "")


def _threshold() -> float:
    """Clustering merge threshold (cosine distance). Tune per deployment via
    SHERPA_DIAR_THRESHOLD — higher merges more (fewer speakers)."""
    try:
        return float(os.environ.get("SHERPA_DIAR_THRESHOLD", "0.7"))
    except ValueError:
        return 0.7


def available() -> bool:
    """True when local diarization is switched on AND actually runnable."""
    if not enabled():
        return False
    seg, emb = _seg_model(), _emb_model()
    if not (seg and emb and os.path.exists(seg) and os.path.exists(emb)):
        return False
    try:
        import sherpa_onnx  # noqa: F401
    except Exception:
        return False
    return True


def _decode_pcm(data: bytes, mime: str):
    """Decode arbitrary audio bytes → mono 16 kHz float32 samples via ffmpeg.

    Returns a numpy float32 array, or None if ffmpeg (or numpy) is unavailable
    or the decode fails. ffmpeg reads the container from the byte stream, so the
    exact mime doesn't matter."""
    try:
        import numpy as np
    except Exception:
        return None
    with tempfile.NamedTemporaryFile(suffix=".bin", delete=True) as src:
        src.write(data)
        src.flush()
        try:
            proc = subprocess.run(
                [
                    "ffmpeg", "-nostdin", "-loglevel", "error",
                    "-i", src.name,
                    "-f", "f32le", "-ac", "1", "-ar", str(_SAMPLE_RATE), "-",
                ],
                capture_output=True,
                timeout=600,
                env=child_env(),
            )
        except (FileNotFoundError, subprocess.SubprocessError):
            return None
    if proc.returncode != 0 or not proc.stdout:
        return None
    return np.frombuffer(proc.stdout, dtype=np.float32)


def _speaker_turns(samples, num_speakers: int | None) -> list[tuple[float, float, int]]:
    """Run sherpa-onnx offline diarization → [(start_s, end_s, speaker_idx)]."""
    import sherpa_onnx

    config = sherpa_onnx.OfflineSpeakerDiarizationConfig(
        segmentation=sherpa_onnx.OfflineSpeakerSegmentationModelConfig(
            pyannote=sherpa_onnx.OfflineSpeakerSegmentationPyannoteModelConfig(
                model=_seg_model()
            ),
        ),
        embedding=sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=_emb_model()),
        clustering=sherpa_onnx.FastClusteringConfig(
            # -1 → auto speaker count via the threshold (meetings are 2–6 people);
            # a caller-supplied count pins it when known.
            num_clusters=num_speakers if (num_speakers and num_speakers > 0) else -1,
            # Cosine-distance merge threshold. 0.7 correctly recovered speaker
            # count in testing where 0.5 over-split; env-tunable per deployment.
            threshold=_threshold(),
        ),
        min_duration_on=0.3,
        min_duration_off=0.5,
    )
    sd = sherpa_onnx.OfflineSpeakerDiarization(config)
    result = sd.process(samples)
    segments = result.sort_by_start_time()
    return [(seg.start, seg.end, seg.speaker) for seg in segments]


def apply_speaker_turns(
    segments: list[TranscriptSegmentData],
    turns: list[tuple[float, float, int]],
) -> int:
    """Label each transcript segment with the speaker it overlaps most (in-place).

    Pure function over timings — unit-testable without any models. Returns the
    number of distinct speakers actually assigned. Labels are 1-based ``S{n}``
    to match the Deepgram convention (so the naming/rename UI is identical)."""
    assigned: set[int] = set()
    for seg in segments:
        overlap_by_spk: dict[int, float] = {}
        for (t_start, t_end, spk) in turns:
            ov = min(seg.end_s, t_end) - max(seg.start_s, t_start)
            if ov > 0:
                overlap_by_spk[spk] = overlap_by_spk.get(spk, 0.0) + ov
        if overlap_by_spk:
            best = max(overlap_by_spk, key=lambda k: overlap_by_spk[k])
            seg.speaker_label = f"S{best + 1}"
            assigned.add(best)
    return len(assigned)


async def maybe_diarize(
    data: bytes, mime: str, result: TranscriptResult, num_speakers: int | None = None
) -> TranscriptResult:
    """If enabled and the transcript isn't already diarized, add speaker labels
    locally with sherpa-onnx. Never raises — returns ``result`` unchanged on any
    problem, so callers can wrap the whole transcription flow around it safely."""
    if result.diarized or not result.segments or not available():
        return result
    try:
        samples = await asyncio.to_thread(_decode_pcm, data, mime)
        if samples is None or len(samples) == 0:
            return result
        turns = await asyncio.to_thread(_speaker_turns, samples, num_speakers)
        if not turns:
            return result
        n = apply_speaker_turns(result.segments, turns)
        if n > 0:
            result.diarized = True
            result.model = f"{result.model}+sherpa-diar"
    except Exception:
        # Fail-safe: keep the speaker-less transcript rather than fail the run.
        return result
    return result
