"""Sarvam speech to text — the native ``transcribe`` handler (VOX-2).

Spec: ``project-docs/specs/projects_ai_chat.md`` §31 VOX-2 · ``customer_console.md``
§6A.10a (the transcribe door) and §6A.10b (the handler seam).

🔴 **Why a native handler.** litellm 1.103.0 knows Sarvam as a chat provider
only, and it has no Sarvam transcription. So ``native_sarvam`` is the first
native verb for ``transcribe``. ``handlers.NATIVE_HANDLERS`` holds the
instance, and ``router._default_provider_call`` sends the verb there.

**The vendor contract, read on 2026-11-08** from Sarvam's public API
reference (https://docs.sarvam.ai/api-reference-docs/speech-to-text/transcribe)
and its REST guide (https://docs.sarvam.ai/api/api-guides-tutorials/speech-to-text/rest-api):

* ``POST https://api.sarvam.ai/speech-to-text`` with a multipart body.
* The key goes in the header ``api-subscription-key``.
* The form fields are ``file``, ``model`` (``saaras:v3`` or ``saaras:v4``) and
  ``mode`` (``transcribe``, ``translate``, ``verbatim``, ``translit`` or
  ``codemix``). With no ``language_code``, the vendor detects the language.
* A 200 body holds ``request_id``, ``transcript`` and ``language_code``. It
  holds no duration, so the door measures the WAV clip itself.
* The errors are 400, 403 (the key), 422 (bad audio, or audio above 30 s),
  429, 500 and 503.
* 🔴 **One REST call takes at most 30 seconds of audio.** The chat sends up
  to 120 s (VOX-1). So this handler cuts a longer clip into parts of
  :data:`PART_SECONDS` and joins the transcripts in order.

🔴 **The mode is in the model id** (Do 3). No column holds a per-model
setting, so the operator declares ``sarvam/saaras:v3/translit``. The text
before the first slash names the credential. The vendor sees ``saaras:v3``
and ``mode=translit``. An id with no mode gets :data:`DEFAULT_MODE`, which
keeps Hinglish as spoken, in Roman script (§31.6 Q1).

⚠️ **It calls ``httpx`` directly, never the vendor SDK** (§6A.14 clause 5).
It imports no tenant package (§6A.10b clause 1).

⚠️ **This module does not import ``handlers`` at load time.** ``handlers``
imports this module to fill its table, so the seam types are imported
inside :meth:`SarvamHandler.call`, as ``router`` imports ``handlers``.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import httpx

from customer_console.wav import read_wav, split_wav

if TYPE_CHECKING:
    from customer_console.handlers import TranscribePayload, TranscriptResult

__all__ = [
    "DEFAULT_MODE",
    "MODES",
    "PART_SECONDS",
    "SARVAM_API_BASE",
    "SARVAM_PATH",
    "SARVAM_PROVIDER",
    "TRANSCRIBE_TASK",
    "SarvamHandler",
    "model_refusal",
    "split_model",
]

_log = logging.getLogger(__name__)

#: The ONE task this handler serves.
TRANSCRIBE_TASK = "transcribe"

#: The credential prefix of a bound model id, and the provider id of the key.
SARVAM_PROVIDER = "sarvam"

#: The vendor's host, when the credential row names no ``api_base``.
SARVAM_API_BASE = "https://api.sarvam.ai"

#: The one vendor endpoint. A transcription is a POST here.
SARVAM_PATH = "/speech-to-text"

#: The header the vendor reads the key from. It is not a bearer token.
KEY_HEADER = "api-subscription-key"

#: The modes the operator may declare (spec §31 VOX-2 Do 3). The vendor has
#: a fifth, ``verbatim``. The spec names four, so the allowlist holds four.
MODES = frozenset({"translit", "codemix", "transcribe", "translate"})

#: The mode of an id that names none: as spoken, in Roman script (§31.6 Q1).
DEFAULT_MODE = "translit"

#: The longest part one REST call gets. The vendor states 30 s. One second
#: less keeps a rounded header off the limit, because a 422 does not fail
#: over (``router.TERMINAL_STATUSES``).
PART_SECONDS = 29

#: How long one part may take. It matches the litellm step (``timeout: 120``).
DEFAULT_TIMEOUT_SECONDS = 120.0


def split_model(model: str) -> tuple[str, str] | None:
    """``sarvam/saaras:v3/translit`` → ``("saaras:v3", "translit")``, or None.

    None means the id is not one this handler can call. The prefix must be
    ``sarvam``, the vendor model must not be empty, and a mode must be one of
    :data:`MODES`. An id with no mode gets :data:`DEFAULT_MODE`.
    """
    prefix, sep, rest = model.strip().partition("/")
    if prefix != SARVAM_PROVIDER or not sep:
        return None
    vendor_model, _, mode = rest.partition("/")
    mode = mode or DEFAULT_MODE
    if not vendor_model or mode not in MODES:
        return None
    return vendor_model, mode


def model_refusal(model: str) -> str | None:
    """Why the declare form must refuse this id, or None when it is fine.

    ``catalog.check_model_for_invocation`` reads it, so an operator who types
    a wrong mode gets a 400 at declare time and not a 502 at the first clip.
    """
    if split_model(model) is not None:
        return None
    return (
        f"model {model!r} is not a Sarvam id this Console can call. Declare "
        f"sarvam/<model>/<mode>, for example sarvam/saaras:v3/{DEFAULT_MODE}. "
        f"The mode is one of {', '.join(sorted(MODES))}, and no mode means "
        f"{DEFAULT_MODE}"
    )


def _transcript(data: Any) -> str | None:
    """The vendor's ``transcript``, or None when the body is not that shape."""
    if not isinstance(data, dict):
        return None
    text = data.get("transcript")
    return text if isinstance(text, str) else None


class SarvamHandler:
    """``native_sarvam``: one clip to Sarvam's REST speech-to-text endpoint.

    ``transport`` exists so a test can answer with a recorded vendor body and
    no network. Production passes none.
    """

    #: The credential prefix this handler answers to. ``handlers`` reads it.
    provider = SARVAM_PROVIDER
    #: The one task it serves. ``catalog.check_invocation_for_task`` reads it.
    task = TRANSCRIBE_TASK
    #: 🔴 It cuts the clip with the ``wave`` module, so it takes PCM WAV only.
    #: The door drops this step for any other clip, and answers 415 when no
    #: step is left.
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
            raise NativeProviderError(None, f"sarvam serves {TRANSCRIBE_TASK!r}, not {task!r}")

        # 🔴 Refused BEFORE any network I/O, and not terminal: no vendor saw
        # it, and nothing was charged. The next step of the chain may serve.
        parsed = split_model(payload.model)
        if parsed is None:
            _log.error(
                "stt_sarvam.model_refused",
                extra={"router_model": payload.model, "router_org": payload.organization_id},
            )
            raise NativeProviderError(None, "model id is not a Sarvam id")
        vendor_model, mode = parsed

        clip = read_wav(payload.audio)
        if clip is None:
            # The door drops a Sarvam step for a clip that is not WAV. This
            # line is the second wall, for a caller that skipped the first.
            raise NativeProviderError(None, "sarvam takes PCM WAV only")

        parts = split_wav(clip, PART_SECONDS)
        texts: list[str] = []
        url = (payload.api_base or SARVAM_API_BASE).rstrip("/") + SARVAM_PATH
        async with httpx.AsyncClient(transport=self._transport, timeout=payload.timeout) as client:
            for index, part in enumerate(parts):
                text = await self._post_part(
                    client, url, part, payload, vendor_model, mode, index, len(parts)
                )
                if text:
                    texts.append(text)
        return TranscriptResult(text=" ".join(texts), duration=clip.seconds)

    async def _post_part(
        self,
        client: httpx.AsyncClient,
        url: str,
        part: bytes,
        payload: TranscribePayload,
        vendor_model: str,
        mode: str,
        index: int,
        total: int,
    ) -> str:
        """Send one part and return its transcript, stripped.

        A failure carries the vendor's status, and never its body. A part
        that fails after an earlier part served is logged with both counts,
        because the vendor has charged for the parts that served.
        """
        from customer_console.handlers import NativeProviderError

        try:
            response = await client.post(
                url,
                headers={KEY_HEADER: payload.api_key},
                data={"model": vendor_model, "mode": mode},
                files={"file": (_part_name(payload.filename), part, "audio/wav")},
            )
        except httpx.HTTPError as exc:
            # No status: a timeout or a dropped connection. Retryable.
            self._note_partial(payload, index, total, None)
            raise NativeProviderError(None, type(exc).__name__) from exc

        if response.status_code >= 400:
            _log.warning(
                "handlers.vendor_refused",
                extra={"vendor": SARVAM_PROVIDER, "vendor_status": response.status_code},
            )
            self._note_partial(payload, index, total, response.status_code)
            raise NativeProviderError(response.status_code, "vendor refused")

        # 🔴 **A 200 we cannot read is TERMINAL**, as in the System One
        # handler. The vendor has charged for it, so another step would pay
        # a second vendor for the same clip.
        try:
            text = _transcript(response.json())
        except ValueError:
            text = None
        if text is None:
            _log.error(
                "handlers.vendor_unreadable",
                extra={
                    "vendor": SARVAM_PROVIDER,
                    "upstream_status": response.status_code,
                    "router_org": payload.organization_id,
                    "router_request": payload.request_id,
                },
            )
            raise NativeProviderError(None, "no transcript in the body", terminal=True)
        return text.strip()

    @staticmethod
    def _note_partial(
        payload: TranscribePayload, index: int, total: int, status: int | None
    ) -> None:
        """Log a part that failed after an earlier part served. No-op for part 1."""
        if index == 0:
            return
        _log.warning(
            "stt_sarvam.partial_failure",
            extra={
                "parts_served": index,
                "parts_total": total,
                "vendor_status": status,
                "router_org": payload.organization_id,
                "router_request": payload.request_id,
            },
        )


def _part_name(filename: str) -> str:
    """The file name the vendor reads. Every part is WAV, so it says so."""
    stem = filename.rsplit(".", 1)[0] if "." in filename else filename
    return f"{stem or 'audio'}.wav"
