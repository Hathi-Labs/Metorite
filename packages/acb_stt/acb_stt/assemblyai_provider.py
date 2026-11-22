"""AssemblyAI speech-to-text — a native provider (not routed through LiteLLM).

Why native: AssemblyAI's batch API is **submit-then-poll** (upload → create a
transcript job → poll until it completes), which doesn't fit LiteLLM's one-shot
``atranscription`` shape at all — LiteLLM only carries it as an opaque
pass-through. So this speaks the v2 REST API directly, and slots in behind the
same :class:`SttProvider` interface as ``LiteLLMSTT``, keeping the notes pipeline
provider-agnostic.

Why it's the default STT: it diarizes (``speaker_labels``), handles
Hindi/English code-switching, and is materially cheaper than the alternatives
per hour of audio. Because it returns real speaker labels, the local sherpa-onnx
diarization pass (``local_diarization.py``) automatically becomes a *fallback* —
``maybe_diarize`` no-ops on an already-diarized result.

Diarization, precisely (an earlier version of this note over-promised and cost a
debugging session):

- **Batch** — set ``speaker_labels`` on the job. Works on the Universal models
  including universal-2. This is what the notes pipeline uses.
- **Streaming** — also supported, but it is **opt-in per connection**
  (``speaker_labels=true`` on the WebSocket URL, exactly like Deepgram's
  ``diarize``) and needs a streaming model that offers it: Universal-3 Pro
  Streaming or a multilingual streaming model. **Universal-2 does not.** Asking
  a model that can't for speaker labels yields captions with no speakers, which
  looks identical to broken plumbing.

Note the streaming model is chosen independently of the ``tier-stt`` batch model
(see ``routes/notes/live.py::_live_model`` / ``ASSEMBLYAI_LIVE_MODEL``) — picking
universal-2 in Settings -> Models does not make streaming use universal-2.

Spec: project-docs/specs/note_taker_app.md §3.4.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from typing import Any

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

_log = logging.getLogger("acb_stt.assemblyai")

_API = "https://api.assemblyai.com/v2"
_PREFIX = "assemblyai/"
# A long meeting can take a few minutes to come back; poll patiently but never
# forever (the caller's summary_run would otherwise hang "running" indefinitely).
_POLL_INTERVAL_S = 3.0
_POLL_TIMEOUT_S = float(os.environ.get("ASSEMBLYAI_POLL_TIMEOUT_S", "1800"))
_UPLOAD_TIMEOUT_S = 300.0
# AssemblyAI caps boosted vocabulary; keep well under it and keep terms short.
_MAX_BOOST_TERMS = 100


def api_key() -> str:
    """The operator's PLATFORM key from the env, or ``""``. It never writes."""
    return os.environ.get("ASSEMBLYAI_API_KEY", "").strip()


async def resolve_api_key() -> str:
    """The key of ONE transcription, for the organization bound to the call.

    D58.2 (task 625): the key store seam answers the org's stored key, and
    else the platform key (``ProviderKeyStore.provider_key``). The key travels
    as an argument, never on the provider object, because one provider object
    serves every organization. Without the key store package, the platform key.
    """
    try:
        from acb_llm.key_store import caller_provider_key
    except Exception:  # a stand-alone acb_stt keeps the env key
        return api_key()
    return await caller_provider_key("assemblyai")


def is_assemblyai_model(model: str) -> bool:
    return bool(model) and model.strip().lower().startswith(_PREFIX)


def speech_model(model: str) -> str | None:
    """``assemblyai/universal`` → ``universal``. Empty → let AssemblyAI default."""
    m = (model or "").strip()
    if m.lower().startswith(_PREFIX):
        m = m[len(_PREFIX):]
    return m or None


def boost_terms(prompt: str | None) -> list[str]:
    """Map our glossary *prompt* (a comma/newline-joined string of org
    vocabulary) onto AssemblyAI's ``word_boost`` term list.

    Defensive: the prompt is free text, so split on the separators the glossary
    uses, drop anything implausible as a term, and cap the list."""
    if not prompt:
        return []
    terms: list[str] = []
    for raw in re.split(r"[,\n;]+", prompt):
        t = raw.strip().strip(".").strip()
        # Skip sentence-y fragments and empties — word_boost wants vocabulary.
        if not t or len(t) > 40 or len(t.split()) > 4:
            continue
        if t not in terms:
            terms.append(t)
        if len(terms) >= _MAX_BOOST_TERMS:
            break
    return terms


def _speaker_label(speaker: Any) -> str | None:
    """AssemblyAI labels speakers "A", "B", … — map onto our S1/S2 space so the
    rest of the app (speaker_names, the rename UI, live reconciliation) sees one
    convention regardless of provider."""
    if speaker is None:
        return None
    s = str(speaker).strip()
    if not s:
        return None
    if len(s) == 1 and s.isalpha():
        return f"S{ord(s.upper()) - ord('A') + 1}"
    if s.isdigit():                       # some responses use ints
        return f"S{int(s) + 1}"
    return s


def _ms(v: Any) -> float:
    """AssemblyAI reports offsets in milliseconds; we store seconds."""
    try:
        return float(v) / 1000.0
    except (TypeError, ValueError):
        return 0.0


def _words(raw: Any) -> list[TranscriptWord] | None:
    if not isinstance(raw, list) or not raw:
        return None
    out = [
        TranscriptWord(
            text=str(w.get("text") or ""),
            start_s=_ms(w.get("start")),
            end_s=_ms(w.get("end")),
        )
        for w in raw
        if isinstance(w, dict)
    ]
    return out or None


def normalize(payload: dict[str, Any], model: str) -> TranscriptResult:
    """Map a completed AssemblyAI transcript into our provider-agnostic result.

    Prefers ``utterances`` (already grouped per speaker turn — exactly our
    segment unit); falls back to ``words``, then to the flat ``text``."""
    segments: list[TranscriptSegmentData] = []
    diarized = False

    utterances = payload.get("utterances")
    if isinstance(utterances, list) and utterances:
        for u in utterances:
            if not isinstance(u, dict):
                continue
            text = str(u.get("text") or "").strip()
            if not text:
                continue
            label = _speaker_label(u.get("speaker"))
            if label:
                diarized = True
            segments.append(
                TranscriptSegmentData(
                    idx=len(segments),
                    start_s=_ms(u.get("start")),
                    end_s=_ms(u.get("end")),
                    text=text,
                    speaker_label=label,
                    confidence=u.get("confidence"),
                    words=_words(u.get("words")),
                )
            )
    elif isinstance(payload.get("words"), list) and payload["words"]:
        words = payload["words"]
        segments.append(
            TranscriptSegmentData(
                idx=0,
                start_s=_ms(words[0].get("start")),
                end_s=_ms(words[-1].get("end")),
                text=str(payload.get("text") or "").strip(),
                words=_words(words),
            )
        )
    elif str(payload.get("text") or "").strip():
        segments.append(
            TranscriptSegmentData(
                idx=0, start_s=0.0, end_s=0.0,
                text=str(payload["text"]).strip(),
            )
        )

    duration = payload.get("audio_duration")
    return TranscriptResult(
        text=str(payload.get("text") or "").strip() or flatten_text(segments),
        segments=segments,
        provider="assemblyai",
        model=model,
        language=payload.get("language_code"),
        duration_s=float(duration) if isinstance(duration, (int, float)) else None,
        diarized=diarized,
    )


class AssemblyAISTT(SttProvider):
    """Batch transcription via AssemblyAI's v2 REST API."""

    name = "assemblyai"

    def __init__(self, model_alias: str | None = None):
        self._model = model_alias or ""

    def capabilities(self) -> SttCaps:
        return SttCaps(
            diarization=True,
            word_timestamps=True,
            streaming=True,       # via the live path, not this method
            languages="multilingual",
        )

    def _headers(self, key: str) -> dict[str, str]:
        if not key:
            raise SttError(self.name, "ASSEMBLYAI_API_KEY is not set")
        # AssemblyAI takes the raw key in `authorization` — NOT a Bearer token.
        return {"authorization": key}

    async def _upload(self, client: Any, audio: AudioInput, key: str) -> str:
        r = await client.post(
            f"{_API}/upload",
            content=audio.data,
            headers={**self._headers(key), "content-type": "application/octet-stream"},
            timeout=_UPLOAD_TIMEOUT_S,
        )
        if r.status_code >= 400:
            raise SttError(self.name, f"upload failed: {r.text[:200]}", r.status_code)
        url = r.json().get("upload_url")
        if not url:
            raise SttError(self.name, "upload returned no upload_url")
        return str(url)

    def build_request(
        self, upload_url: str, opts: SttOptions, *, plural_models: bool = True
    ) -> dict[str, Any]:
        """The transcript-job body (pure, so the option mapping is testable).

        ``plural_models`` picks how the model is named. AssemblyAI's current
        pre-recorded API takes **``speech_models``: a required non-empty list**,
        but older accounts/regions take the singular ``speech_model`` string.
        Guessing wrong fails *every* request, so :meth:`transcribe` sends the
        documented plural form and retries once with the singular one if the API
        rejects the field — self-correcting rather than a hardcoded bet."""
        body: dict[str, Any] = {"audio_url": upload_url}
        model = speech_model(opts.model or self._model)
        if model:
            body["speech_models" if plural_models else "speech_model"] = (
                [model] if plural_models else model
            )
        if opts.diarize:
            body["speaker_labels"] = True
        if opts.language:
            body["language_code"] = opts.language
        else:
            # No hint → let AssemblyAI detect (handles Hindi/English meetings).
            body["language_detection"] = True
        terms = boost_terms(opts.prompt)
        if terms:
            body["word_boost"] = terms
        return body

    async def transcribe(self, audio: AudioInput, opts: SttOptions) -> TranscriptResult:
        import httpx

        model_label = (opts.model or self._model or "assemblyai/universal")
        key = await resolve_api_key()
        headers = self._headers(key)  # raises SttError before any upload
        async with httpx.AsyncClient(timeout=60.0) as client:
            upload_url = await self._upload(client, audio, key)
            r = await client.post(
                f"{_API}/transcript",
                json=self.build_request(upload_url, opts, plural_models=True),
                headers=headers,
            )
            # The model field is named `speech_models` (list) on the current API
            # and `speech_model` (string) on older accounts. If the plural form
            # is rejected, retry once with the singular rather than failing the
            # whole transcription over a field name.
            if r.status_code == 400 and "speech_model" in r.text:
                _log.info("assemblyai: retrying with singular speech_model")
                r = await client.post(
                    f"{_API}/transcript",
                    json=self.build_request(upload_url, opts, plural_models=False),
                    headers=headers,
                )
            if r.status_code >= 400:
                raise SttError(
                    self.name, f"job create failed: {r.text[:200]}", r.status_code
                )
            job_id = r.json().get("id")
            if not job_id:
                raise SttError(self.name, "job create returned no id")

            waited = 0.0
            while waited < _POLL_TIMEOUT_S:
                await asyncio.sleep(_POLL_INTERVAL_S)
                waited += _POLL_INTERVAL_S
                p = await client.get(
                    f"{_API}/transcript/{job_id}", headers=headers
                )
                if p.status_code >= 400:
                    raise SttError(
                        self.name, f"poll failed: {p.text[:200]}", p.status_code
                    )
                payload = p.json()
                status = str(payload.get("status") or "")
                if status == "completed":
                    return normalize(payload, model_label)
                if status == "error":
                    raise SttError(
                        self.name, str(payload.get("error") or "transcription error")
                    )
            raise SttError(
                self.name, f"transcription timed out after {_POLL_TIMEOUT_S:.0f}s"
            )
