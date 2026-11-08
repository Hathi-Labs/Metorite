"""AssemblyAI speech to text — the native ``transcribe`` handler (VOX-6).

Spec: ``project-docs/specs/projects_ai_chat.md`` §31 VOX-6 · ``customer_console.md``
§6A.10a (the transcribe door) and §6A.10b (the handler seam).

🔴 **Why a native handler.** litellm 1.103.0 lists ``assemblyai`` as a provider
id, but it cannot call the Sync API. Three routes were tried on 2026-11-08.
``assemblyai/best`` and ``openrouter/assemblyai/universal-3-5-pro`` answer
"Unmapped provider passed in", and the generic OpenAI route answers 404. So
``native_assemblyai`` is the second native verb for ``transcribe``, built on
the VOX-2 pattern (``stt_sarvam.py``). ``handlers.NATIVE_HANDLERS`` holds the
instance, and ``router._default_provider_call`` sends the verb there.

**The vendor contract, read on 2026-11-08** from the Sync API reference
(https://www.assemblyai.com/docs/api-reference/sync-api/transcribe.md) and the
language page (https://www.assemblyai.com/docs/sync-stt/language-selection.md):

* ``POST https://sync.assemblyai.com/v1/transcribe`` with a multipart body.
* The header ``Authorization`` holds the key (a ``Bearer`` prefix is optional,
  so this handler sends the bare key). The header ``X-AAI-Model`` is required.
* The part ``audio`` holds the clip. The optional part ``config`` is JSON, for
  example ``{"language_codes": ["hi", "en"]}``.
* The limits are 20 minutes, 256 MB, and a 30 second processing deadline that
  answers 504. The door caps a WAV clip at 600 s, so this handler sends ONE
  request per clip and never cuts it.
* ``language_codes`` takes 32 codes (:data:`LANGUAGES`). Another code is a 400.
* A 200 body holds ``text``, ``words``, ``confidence``, ``audio_duration_ms``,
  ``session_id`` and ``request_time_ms``. An error is
  ``application/problem+json`` with ``status``, ``title`` and ``detail``.
* Data residency hosts exist. This handler uses the global host only.

🔴 **The model and the languages are in the model id.** No column holds a
per-model setting, so the operator declares ``assemblyai/universal-3-5-pro/hi,en``.
The text before the first slash names the credential. The vendor sees the model
in ``X-AAI-Model`` and the codes in ``config``. An id with no languages sends
no ``config`` part, and the vendor applies no steering.

🔴 **The bill is the door's measure of the WAV bytes.** ``audio_duration_ms``
never sets a quantity (the VOX-2 measured-only rule). The result carries the
measured length as its ``duration`` fallback, and the door bills its own.

⚠️ **It calls ``httpx`` directly, never the vendor SDK** (§6A.14 clause 5).
It imports no tenant package (§6A.10b clause 1). It never logs the key, the
audio, the transcript or an error body.

⚠️ **This module does not import ``handlers`` at load time.** ``handlers``
imports this module to fill its table, so the seam types are imported inside
:meth:`AssemblyAIHandler.call`, as ``stt_sarvam`` does.
"""
from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

import httpx

from customer_console.wav import read_wav

if TYPE_CHECKING:
    from customer_console.handlers import TranscribePayload, TranscriptResult

__all__ = [
    "ASSEMBLYAI_API_BASE",
    "ASSEMBLYAI_PATH",
    "ASSEMBLYAI_PROVIDER",
    "LANGUAGES",
    "MODELS",
    "TRANSCRIBE_TASK",
    "AssemblyAIHandler",
    "model_refusal",
    "split_model",
]

_log = logging.getLogger(__name__)

#: The ONE task this handler serves.
TRANSCRIBE_TASK = "transcribe"

#: The credential prefix of a bound model id, and the provider id of the key.
ASSEMBLYAI_PROVIDER = "assemblyai"

#: The vendor's global host, when the credential row names no ``api_base``.
ASSEMBLYAI_API_BASE = "https://sync.assemblyai.com"

#: The one vendor endpoint. A transcription is a POST here.
ASSEMBLYAI_PATH = "/v1/transcribe"

#: The header that names the model. The vendor requires it on every request.
MODEL_HEADER = "X-AAI-Model"

#: The models the Sync API accepts in :data:`MODEL_HEADER` (OpenAPI enum,
#: read 2026-11-08). The legacy aliases ``u3-sync-pro`` and ``u3-pro`` are not
#: offered, so one model has one spelling.
MODELS = frozenset({"universal-3-5-pro"})

#: The 32 codes ``config.language_codes`` takes (language page, 2026-11-08).
#: The vendor answers 400 for any other code, so the declare form refuses it
#: first.
LANGUAGES = frozenset({
    "en", "es", "de", "fr", "it", "pt", "tr", "nl", "sv", "no", "da", "fi",
    "hi", "vi", "he", "ur", "ko", "ca", "gl", "ru", "ro", "et", "fa", "yue",
    "af", "mr", "zu", "xh", "nn", "ar", "ja", "zh",
})

#: Where the vendor model ends and the language list begins.
_LANGUAGE_SEPARATOR = ","


def split_model(model: str) -> tuple[str, tuple[str, ...]] | None:
    """``assemblyai/universal-3-5-pro/hi,en`` → ``("universal-3-5-pro", ("hi", "en"))``.

    None means the id is not one this handler can call. The prefix must be
    ``assemblyai``, the vendor model must be in :data:`MODELS`, and every code
    must be in :data:`LANGUAGES`. An id with no languages gives an empty tuple.
    """
    prefix, sep, rest = model.strip().partition("/")
    if prefix != ASSEMBLYAI_PROVIDER or not sep:
        return None
    vendor_model, lang_sep, raw = rest.partition("/")
    if vendor_model not in MODELS:
        return None
    if not lang_sep:
        return vendor_model, ()
    codes = tuple(raw.split(_LANGUAGE_SEPARATOR))
    # A trailing slash, an empty element, a repeat or an unknown code.
    if not raw or len(set(codes)) != len(codes) or any(c not in LANGUAGES for c in codes):
        return None
    return vendor_model, codes


def model_refusal(model: str) -> str | None:
    """Why the declare form must refuse this id, or None when it is fine.

    ``catalog.check_model_for_invocation`` reads it, so an operator who types
    a wrong language gets a 400 at declare time and not a 502 at the first clip.
    """
    if split_model(model) is not None:
        return None
    return (
        f"model {model!r} is not an AssemblyAI id this Console can call. Declare "
        f"assemblyai/<model> or assemblyai/<model>/<codes>, for example "
        f"assemblyai/universal-3-5-pro/hi,en. The model is one of "
        f"{', '.join(sorted(MODELS))}. The codes are a comma list from "
        f"{', '.join(sorted(LANGUAGES))}, with no repeat, and no codes means "
        "no language steering"
    )


def _text(data: Any) -> str | None:
    """The vendor's ``text``, or None when the body is not that shape."""
    if not isinstance(data, dict):
        return None
    text = data.get("text")
    return text if isinstance(text, str) else None


class AssemblyAIHandler:
    """``native_assemblyai``: one clip to AssemblyAI's Sync API.

    ``transport`` exists so a test can answer with a recorded vendor body and
    no network. Production passes none.
    """

    #: The credential prefix this handler answers to. ``handlers`` reads it.
    provider = ASSEMBLYAI_PROVIDER
    #: The one task it serves. ``catalog.check_invocation_for_task`` reads it.
    task = TRANSCRIBE_TASK
    #: 🔴 The door bills a clip it measures, and it measures PCM WAV only
    #: (VOX-2). So the door drops this step for any other clip, and answers
    #: 415 when no step is left. The vendor takes more formats. The bill
    #: still needs the measure.
    requires_wav = True

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._transport = transport

    @staticmethod
    def model_refusal(model: str) -> str | None:
        return model_refusal(model)

    async def call(self, task: str, payload: Any) -> TranscriptResult:
        from customer_console.handlers import (
            NativeProviderError,
            TranscribePayload,
            TranscriptResult,
        )

        if task != TRANSCRIBE_TASK or not isinstance(payload, TranscribePayload):
            raise NativeProviderError(
                None, f"assemblyai serves {TRANSCRIBE_TASK!r}, not {task!r}"
            )

        # 🔴 Refused BEFORE any network I/O, and not terminal: no vendor saw
        # it, and nothing was charged. The next step of the chain may serve.
        parsed = split_model(payload.model)
        if parsed is None:
            _log.error(
                "stt_assemblyai.model_refused",
                extra={"router_model": payload.model, "router_org": payload.organization_id},
            )
            raise NativeProviderError(None, "model id is not an AssemblyAI id")
        vendor_model, languages = parsed

        clip = read_wav(payload.audio)
        if clip is None:
            # The door drops this step for a clip that is not WAV. This line
            # is the second wall, for a caller that skipped the first.
            raise NativeProviderError(None, "assemblyai takes PCM WAV only")
        if clip.frame_count == 0:
            # No audio, nothing to send. The vendor refuses under 80 ms.
            return TranscriptResult(text="", duration=clip.seconds)

        url = (payload.api_base or ASSEMBLYAI_API_BASE).rstrip("/") + ASSEMBLYAI_PATH
        files: dict[str, tuple[str | None, bytes | str, str]] = {
            "audio": (_clip_name(payload.filename), payload.audio, "audio/wav"),
        }
        if languages:
            files["config"] = (
                None,
                json.dumps({"language_codes": list(languages)}),
                "application/json",
            )
        text = await self._post(url, files, payload, vendor_model)
        return TranscriptResult(text=text, duration=clip.seconds)

    async def _post(
        self,
        url: str,
        files: dict[str, tuple[str | None, bytes | str, str]],
        payload: TranscribePayload,
        vendor_model: str,
    ) -> str:
        """Send the clip and return its transcript, stripped.

        A failure carries the vendor's status, and never its body. The status
        decides the chain: 5xx, 504, 429 and a network error fail over to the
        next step, a 400 stops the walk, and a 401 strikes this vendor
        (``router.is_retryable``, ``CREDENTIAL_STATUSES``).
        """
        from customer_console.handlers import NativeProviderError

        try:
            async with httpx.AsyncClient(
                transport=self._transport, timeout=payload.timeout
            ) as client:
                response = await client.post(
                    url,
                    headers={"Authorization": payload.api_key, MODEL_HEADER: vendor_model},
                    files=files,
                )
        except httpx.HTTPError as exc:
            # No status: a timeout or a dropped connection. Retryable.
            raise NativeProviderError(None, type(exc).__name__) from exc

        if response.status_code >= 400:
            _log.warning(
                "handlers.vendor_refused",
                extra={"vendor": ASSEMBLYAI_PROVIDER, "vendor_status": response.status_code},
            )
            raise NativeProviderError(response.status_code, "vendor refused")

        # 🔴 **A 200 we cannot read is TERMINAL**, as in the Sarvam handler.
        # The vendor has charged for it, so another step would pay a second
        # vendor for the same clip.
        try:
            text = _text(response.json())
        except ValueError:
            text = None
        if text is None:
            _log.error(
                "handlers.vendor_unreadable",
                extra={
                    "vendor": ASSEMBLYAI_PROVIDER,
                    "upstream_status": response.status_code,
                    "router_org": payload.organization_id,
                    "router_request": payload.request_id,
                },
            )
            raise NativeProviderError(None, "no transcript in the body", terminal=True)
        return text.strip()


def _clip_name(filename: str) -> str:
    """The file name the vendor reads. The clip is WAV, so the name says so."""
    stem = filename.rsplit(".", 1)[0] if "." in filename else filename
    return f"{stem or 'audio'}.wav"
