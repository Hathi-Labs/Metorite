"""LiteLLM-backed speech-to-text.

Routes transcription through the SAME plumbing chat uses (acb_llm): the model is
resolved from the tier config (``tier-stt`` by default → e.g.
``groq/whisper-large-v3-turbo``, editable in Settings → Models), the key of the
bound organization comes per call from ``call_credentials`` (D58.2), provider routing is
litellm's job (``litellm.atranscription``), and usage is emitted for
observability via ``_emit_usage``. No bespoke HTTP client, no separate key
resolution — STT is a configured model like every other model on the platform.
Spec: note_taker_app.md §3.4 (D3).
"""

from __future__ import annotations

import contextlib
import io
import os
from typing import Any

from acb_common import get_logger

from acb_stt.base import SttProvider
from acb_stt.types import (
    AudioInput,
    SttCaps,
    SttError,
    SttOptions,
    TranscriptResult,
    TranscriptSegmentData,
    TranscriptWord,
    flatten_text,
)

_log = get_logger("acb_stt.litellm")

# The STT model alias resolved through the tier system. Overridable per-call
# (opts.model) or globally via NOTES_STT_MODEL (a tier alias or concrete
# provider/model). Default routes to the configured tier-stt.
DEFAULT_STT_ALIAS = "tier-stt"


def _get(obj: Any, key: str, default: Any = None) -> Any:
    """Attribute-or-dict access — litellm responses are pydantic-ish objects."""
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _word(w: Any) -> TranscriptWord:
    return TranscriptWord(
        text=str(_get(w, "word") or _get(w, "text") or _get(w, "punctuated_word") or ""),
        start_s=float(_get(w, "start") or 0.0),
        end_s=float(_get(w, "end") or 0.0),
    )


def _segments_from_verbose_json(resp: Any) -> list[TranscriptSegmentData]:
    """OpenAI/whisper ``verbose_json`` shape: a flat ``segments`` list."""
    segments: list[TranscriptSegmentData] = []
    for i, s in enumerate(_get(resp, "segments") or []):
        wraw = _get(s, "words")
        # Deepgram-via-litellm may surface a speaker int; whisper never does.
        speaker = _get(s, "speaker")
        segments.append(
            TranscriptSegmentData(
                idx=i,
                start_s=float(_get(s, "start") or 0.0),
                end_s=float(_get(s, "end") or 0.0),
                text=str(_get(s, "text") or "").strip(),
                speaker_label=f"S{int(speaker) + 1}" if speaker is not None else None,
                words=[_word(w) for w in wraw] if wraw else None,
            )
        )
    return segments


def _deepgram_words(resp: Any) -> list[dict[str, Any]]:
    """The raw diarized word list Deepgram returns.

    litellm's Deepgram transcription handler normalises the response down to
    ``text`` + speaker-less ``words`` and stashes the full Deepgram JSON on
    ``resp._hidden_params`` — so the per-word ``speaker`` (the whole point of
    named speakers) only survives there. Pull it back out.
    """
    hp = getattr(resp, "_hidden_params", None)
    if not isinstance(hp, dict):
        return []
    try:
        alt = hp["results"]["channels"][0]["alternatives"][0]
    except (KeyError, IndexError, TypeError):
        return []
    words = alt.get("words")
    return words if isinstance(words, list) else []


def _segments_from_deepgram_words(words: list[dict[str, Any]]) -> list[TranscriptSegmentData]:
    """Group consecutive same-speaker words into speaker-attributed segments."""
    segments: list[TranscriptSegmentData] = []
    run: list[dict[str, Any]] = []
    run_speaker: Any = None

    def flush() -> None:
        if not run:
            return
        text = " ".join(
            str(w.get("punctuated_word") or w.get("word") or "") for w in run
        ).strip()
        segments.append(
            TranscriptSegmentData(
                idx=len(segments),
                start_s=float(run[0].get("start") or 0.0),
                end_s=float(run[-1].get("end") or 0.0),
                text=text,
                speaker_label=(
                    f"S{int(run_speaker) + 1}" if run_speaker is not None else None
                ),
                words=[_word(w) for w in run],
            )
        )

    for w in words:
        speaker = w.get("speaker")
        if run and speaker != run_speaker:
            flush()
            run = []
        run_speaker = speaker
        run.append(w)
    flush()
    return segments


def normalize_transcription(resp: Any, model: str, diarize: bool) -> TranscriptResult:
    """Map a litellm ``TranscriptionResponse`` into our provider-agnostic
    ``TranscriptResult``.

    Handles two shapes: OpenAI/whisper ``verbose_json`` (a flat ``segments``
    list) and Deepgram (no segments — speaker-attributed words live on
    ``_hidden_params``), so named speakers survive from whichever model the
    STT tier is pointed at."""
    segments = _segments_from_verbose_json(resp)
    used_deepgram = False
    if not any(s.speaker_label for s in segments):
        # No speakers from the segment path — try Deepgram's diarized words.
        dg = _segments_from_deepgram_words(_deepgram_words(resp))
        if dg:
            segments = dg
            used_deepgram = True
    # Deepgram's ``text`` is a "Speaker 0: …" reconstruction; the clean flatten
    # of our segments is the better transcript. Whisper's ``text`` is already
    # clean, so prefer it there.
    text = (
        flatten_text(segments)
        if used_deepgram
        else (str(_get(resp, "text") or "").strip() or flatten_text(segments))
    )
    if not segments and text:
        # Providers that don't return segment timings still yield a renderable
        # transcript; timings are unknown.
        segments = [TranscriptSegmentData(idx=0, start_s=0.0, end_s=0.0, text=text)]
    dur = _get(resp, "duration")
    lang = _get(resp, "language")
    return TranscriptResult(
        text=text,
        segments=segments,
        provider="litellm",
        model=model,
        language=str(lang) if lang else None,
        duration_s=float(dur) if dur else None,
        diarized=diarize and any(s.speaker_label for s in segments),
    )


class LiteLLMSTT(SttProvider):
    name = "litellm"

    def __init__(self, model_alias: str | None = None):
        self._alias = model_alias or os.environ.get("NOTES_STT_MODEL") or DEFAULT_STT_ALIAS

    def capabilities(self) -> SttCaps:
        # Diarization (named speakers) depends on the resolved model: Deepgram's
        # nova models return per-word speakers; whisper via litellm never does
        # (the pipeline falls back to the capture-channel prior). Word
        # timestamps come through where the provider supplies them.
        from acb_llm.context import resolve_underlying_model

        model = resolve_underlying_model(self._alias) or ""
        return SttCaps(
            diarization=model.startswith("deepgram/"),
            word_timestamps=True,
            streaming=False,
        )

    async def transcribe(self, audio: AudioInput, opts: SttOptions) -> TranscriptResult:
        import litellm
        from acb_llm.client import (
            _emit_usage,
            _ensure_keys_loaded,
            call_credentials,
            ensure_model_registered,
        )
        from acb_llm.context import resolve_underlying_model

        alias = opts.model or self._alias
        model = resolve_underlying_model(alias)
        if not model:
            raise SttError("litellm", f"no STT model configured for alias '{alias}'")

        await _ensure_keys_loaded()
        ensure_model_registered(model)

        buf = io.BytesIO(audio.data)
        buf.name = audio.filename  # litellm/OpenAI infer the format from the name

        extra = self._provider_params(model, opts)
        # D58.2: the key of the bound organization, per call (task 625).
        extra.update(await call_credentials(model, extra))
        litellm.drop_params = True  # ignore params a given provider doesn't take

        try:
            resp = await litellm.atranscription(model=model, file=buf, **extra)
        except Exception as exc:
            raise SttError("litellm", str(exc)[:500]) from exc

        with contextlib.suppress(Exception):  # observability is best-effort
            _emit_usage(model, "", resp, source="gateway.routes.notes")

        _log.info(
            "acb_stt.transcribed", model=model, alias=alias, diarize=opts.diarize
        )
        return normalize_transcription(resp, model, diarize=opts.diarize)

    @staticmethod
    def _provider_params(model: str, opts: SttOptions) -> dict[str, Any]:
        """Build the per-model kwargs for ``litellm.atranscription``.

        Deepgram and whisper take different options: Deepgram names speakers via
        native ``diarize`` + word-level output (``punctuate``/``smart_format``),
        while OpenAI-compatible whisper models yield segments via
        ``verbose_json`` and accept a glossary ``prompt`` bias. Sending the wrong
        family's params would be dropped at best; branching keeps the request
        clean per provider."""
        extra: dict[str, Any] = {}
        if opts.language:
            extra["language"] = opts.language
        if model.startswith("deepgram/"):
            # Native diarization → per-word speaker labels ("named speakers").
            if opts.diarize:
                extra["diarize"] = True
            extra["punctuate"] = True
            extra["smart_format"] = True
        else:
            # OpenAI-compatible (whisper): segments + word timings; glossary bias.
            extra["response_format"] = "verbose_json"
            if opts.prompt:
                extra["prompt"] = opts.prompt
        return extra
