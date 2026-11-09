"""VOX-6 — AssemblyAI in the Console's transcribe door.

Spec: ``project-docs/specs/projects_ai_chat.md`` §31 VOX-6 · ``customer_console.md``
§6A.10a (the door) and §6A.10b (the handler seam).

🔴 **What this suite proves, in one line each.**

* ``native_assemblyai`` is in both invocation sets, serves ``transcribe`` only,
  and an ``assemblyai/`` transcribe model takes it only.
* The model id carries the model and the languages. A wrong code, a repeat and
  an unknown model are refused at declare time.
* The handler sends ``POST /v1/transcribe`` with ``Authorization``,
  ``X-AAI-Model``, an ``audio`` part of type ``audio/wav``, and a ``config``
  part only when the id names languages.
* The door bills the seconds it measures from the WAV bytes. The vendor's
  ``audio_duration_ms`` never sets a quantity.
* A clip that is not WAV gets a 415 before any call.
* A 5xx, a 504, a 429 and a dropped connection fall to the next step. A 400
  stops the walk. A 401 strikes the vendor and falls to the next step.
* The key never reaches a log line, an exception or a repr.
* The R8 half drives the real route on a real Postgres and reads the
  ``usage_event`` row.

⚠️ **The vendor is ``httpx.MockTransport``. No test here reaches the
network.** The body is the documented shape, read on 2026-11-08 from
https://www.assemblyai.com/docs/api-reference/sync-api/transcribe.md.
"""
# ruff: noqa: F811
# The R8 fixtures come from test_customer_console_tasks.py by import, so a test
# argument redefines the imported name. That is how pytest finds a fixture here.
from __future__ import annotations

import asyncio
import io
import json
import logging
import os
import uuid
import wave
from decimal import Decimal
from email.parser import BytesParser
from email.policy import HTTP
from typing import ClassVar

import pytest

pytest.importorskip("fastapi")
httpx = pytest.importorskip("httpx")

from customer_console import catalog, handlers, stt_assemblyai  # noqa: E402
from customer_console import router as router_mod  # noqa: E402
from customer_console.handlers import (  # noqa: E402
    NativeProviderError,
    TranscribePayload,
    TranscriptResult,
)
from customer_console.router import ResolvedTier, UpstreamFailed  # noqa: E402
from customer_console.wav import wav_seconds  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402

from tests.unit._customer_console_ladder import apply_ladder  # noqa: E402
from tests.unit.test_customer_console_tasks import (  # noqa: E402, F401
    CREDITS_PER_MINUTE,
    TRANSCRIPT,
    client,
    db,
    org_id,
    org_key,
    provider,
)
from tests.unit.test_customer_console_tasks import _price as _price_tier  # noqa: E402
from tests.unit.test_customer_console_tasks import _rows as _usage_rows  # noqa: E402
from tests.unit.test_customer_console_tasks import _transcribe as _post_clip  # noqa: E402

AAI_MODEL = "assemblyai/universal-3-5-pro/hi,en"
AAI_BARE = "assemblyai/universal-3-5-pro"
WHISPER = "groq/whisper-large-v3-turbo"
KEY = "aai-test-key-0123456789abcdef"
SAID = "mera phone number hai"

#: The first bytes of a WebM file (the EBML magic). Not a WAV.
WEBM = b"\x1a\x45\xdf\xa3" + b"\x00" * 64


def make_wav(seconds: float, *, rate: int = 16_000) -> bytes:
    """A mono 16-bit PCM WAV of silence, built with the standard library."""
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(rate)
        writer.writeframes(b"\x00\x00" * int(seconds * rate))
    return buffer.getvalue()


def _ok_body(*, duration_ms: int = 3000, text: str = SAID) -> dict:
    """The documented 200 body of the Sync API."""
    return {
        "text": text,
        "words": [{"text": "mera", "start": 0, "end": 400, "confidence": 0.9}],
        "confidence": 0.93,
        "audio_duration_ms": duration_ms,
        "session_id": "sess-1",
        "request_time_ms": 410,
    }


class _AssemblyAI:
    """A recorded AssemblyAI behind ``httpx.MockTransport``. No network.

    ``answers`` is a list of ``(status, body)`` pairs, one per request in
    order. The last pair repeats.
    """

    def __init__(self, answers: list[tuple[int, object]] | None = None) -> None:
        self.answers = answers or [(200, _ok_body())]
        self.requests: list[httpx.Request] = []

    def transport(self) -> httpx.MockTransport:
        def _answer(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            status, body = self.answers[min(len(self.requests) - 1, len(self.answers) - 1)]
            if isinstance(body, bytes):
                return httpx.Response(status, content=body)
            if status >= 400:
                return httpx.Response(
                    status, json=body, headers={"content-type": "application/problem+json"}
                )
            return httpx.Response(status, json=body)

        return httpx.MockTransport(_answer)

    def handler(self) -> stt_assemblyai.AssemblyAIHandler:
        return stt_assemblyai.AssemblyAIHandler(transport=self.transport())


def _problem(status: int, detail: str = "quotes the request") -> dict:
    """An RFC 9457 problem body, as the vendor sends one."""
    return {"type": "about:blank", "title": "Error", "status": status, "detail": detail}


def _form(request: httpx.Request) -> dict[str, tuple[str | None, str, bytes]]:
    """The multipart parts of one request: name → (file name, type, bytes)."""
    head = f"Content-Type: {request.headers['content-type']}\r\n\r\n".encode()
    message = BytesParser(policy=HTTP).parsebytes(head + request.read())
    parts: dict[str, tuple[str | None, str, bytes]] = {}
    for part in message.iter_parts():
        name = part.get_param("name", header="content-disposition")
        parts[str(name)] = (
            part.get_filename(),
            part.get_content_type(),
            part.get_payload(decode=True) or b"",
        )
    return parts


def _payload(audio: bytes, *, model: str = AAI_MODEL, filename: str = "clip.wav",
             api_base: str | None = None) -> TranscribePayload:
    return TranscribePayload(model=model, audio=audio, filename=filename, api_key=KEY,
                             api_base=api_base, organization_id="org-1", request_id="rtr-1")


def _run(handler: stt_assemblyai.AssemblyAIHandler, payload: TranscribePayload) -> TranscriptResult:
    return asyncio.run(handler.call("transcribe", payload))


# ── Do 1: the verb, the task and the prefix ──────────────────────────────────

class TestTheNativeVerbForAssemblyAI:
    def test_native_assemblyai_is_in_both_sets(self):
        assert "native_assemblyai" in catalog.KNOWN_INVOCATIONS
        assert "native_assemblyai" in router_mod.SERVING_INVOCATIONS
        assert router_mod.SERVING_INVOCATIONS <= catalog.KNOWN_INVOCATIONS
        assert isinstance(
            handlers.NATIVE_HANDLERS["native_assemblyai"], stt_assemblyai.AssemblyAIHandler
        )

    def test_the_handler_table_names_vendor_and_task(self):
        assert handlers.native_provider_of("native_assemblyai") == "assemblyai"
        assert handlers.native_task_of("native_assemblyai") == "transcribe"
        assert handlers.native_verb_for("assemblyai", "transcribe") == "native_assemblyai"
        assert handlers.native_verb_for("assemblyai", "chat") is None
        assert handlers.native_requires_wav("native_assemblyai") is True

    def test_transcribe_takes_native_assemblyai(self):
        assert catalog.check_invocation_for_task(
            "native_assemblyai", "transcribe") == "native_assemblyai"

    @pytest.mark.parametrize("task", ["decide", "chat", "embed", "speak"])
    def test_it_serves_transcribe_only(self, task):
        with pytest.raises(catalog.CatalogRefused) as exc:
            catalog.check_invocation_for_task("native_assemblyai", task)
        assert "serves only transcribe" in str(exc.value)

    def test_an_assemblyai_transcribe_model_takes_the_native_verb_only(self):
        """The prefix `assemblyai/` ties to `native_assemblyai`. litellm cannot
        call the Sync API, so the litellm verb would 502 on the first clip."""
        with pytest.raises(catalog.CatalogRefused) as exc:
            catalog.check_model_for_invocation(AAI_MODEL, "atranscription", task="transcribe")
        assert "'native_assemblyai'" in str(exc.value)
        assert catalog.check_model_for_invocation(
            AAI_MODEL, "native_assemblyai", task="transcribe") is None

    def test_the_credential_is_assemblyai(self):
        with pytest.raises(catalog.CatalogRefused) as exc:
            catalog.check_model_for_invocation(WHISPER, "native_assemblyai", task="transcribe")
        assert "'groq'" in str(exc.value)
        assert "'assemblyai'" in str(exc.value)
        with pytest.raises(catalog.CatalogRefused):
            catalog.check_model_for_invocation(
                "sarvam/saaras:v3", "native_assemblyai", task="transcribe")

    def test_other_pairs_are_unchanged(self):
        assert catalog.check_model_for_invocation(
            WHISPER, "atranscription", task="transcribe") is None
        assert catalog.check_model_for_invocation(
            "sarvam/saaras:v3/translit", "native_sarvam", task="transcribe") is None
        # A chat model under the same vendor prefix is not tied.
        assert catalog.check_model_for_invocation(
            "assemblyai/lemur", "acompletion", task="chat") is None


# ── Do 2: the model and the languages are in the model id ───────────────────

class TestTheIdSplit:
    @pytest.mark.parametrize("model, expected", [
        ("assemblyai/universal-3-5-pro", ("universal-3-5-pro", ())),
        ("assemblyai/universal-3-5-pro/hi,en", ("universal-3-5-pro", ("hi", "en"))),
        ("assemblyai/universal-3-5-pro/en", ("universal-3-5-pro", ("en",))),
        ("assemblyai/universal-3-5-pro/yue,zh,nn", ("universal-3-5-pro", ("yue", "zh", "nn"))),
    ])
    def test_the_split(self, model, expected):
        assert stt_assemblyai.split_model(model) == expected

    @pytest.mark.parametrize("model", [
        "assemblyai/universal-3-5-pro/xx",
        "assemblyai/universal-3-5-pro/hi,xx",
        "assemblyai/universal-3-5-pro/hi,,en",
        "assemblyai/universal-3-5-pro/hi,hi",
        "assemblyai/universal-3-5-pro/HI",
        "assemblyai/universal-3-5-pro/",
        "assemblyai/universal-3-5-pro/hi/en",
        "assemblyai/universal-2",
        "assemblyai/",
        "assemblyai",
        "groq/universal-3-5-pro/hi",
    ])
    def test_an_id_the_handler_cannot_call(self, model):
        assert stt_assemblyai.split_model(model) is None
        assert stt_assemblyai.model_refusal(model) is not None

    def test_the_language_list_is_the_32_the_vendor_takes(self):
        assert len(stt_assemblyai.LANGUAGES) == 32
        assert {"hi", "en", "yue", "nn"} <= stt_assemblyai.LANGUAGES

    def test_an_unknown_language_is_refused_at_declare_time(self):
        with pytest.raises(catalog.CatalogRefused) as exc:
            catalog.check_model_for_invocation(
                "assemblyai/universal-3-5-pro/hi,xx", "native_assemblyai", task="transcribe")
        assert "hi" in str(exc.value)
        assert "universal-3-5-pro" in str(exc.value)

    def test_the_capability_write_answers_400_for_an_unknown_language(self, monkeypatch):
        """The operator's capability write is the declare door. It checks
        before it opens a connection, so this needs no database."""
        from fastapi.testclient import TestClient

        monkeypatch.setenv("CUSTOMER_CONSOLE_OPERATOR_TOKEN", "vox6-operator-token")
        from customer_console.main import app

        answer = TestClient(app).post(
            "/catalog/capabilities",
            headers={"Authorization": "Bearer vox6-operator-token"},
            json={"model": "assemblyai/universal-3-5-pro/hi,xx", "task": "transcribe",
                  "invocation": "native_assemblyai", "streams": False},
        )
        assert answer.status_code == 400, answer.text
        assert "universal-3-5-pro" in answer.text


# ── Do 3: the request shape ──────────────────────────────────────────────────

class TestTheRequestShape:
    def test_the_handler_sends_the_documented_request(self):
        vendor = _AssemblyAI()
        audio = make_wav(3)
        result = _run(vendor.handler(), _payload(audio, filename="meeting.wav"))

        [request] = vendor.requests
        assert request.method == "POST"
        assert str(request.url) == "https://sync.assemblyai.com/v1/transcribe"
        assert request.headers["authorization"] == KEY
        assert request.headers["x-aai-model"] == "universal-3-5-pro"
        form = _form(request)
        assert set(form) == {"audio", "config"}
        filename, ctype, sent = form["audio"]
        assert (filename, ctype) == ("meeting.wav", "audio/wav")
        assert sent == audio  # the real bytes, not a re-encode
        assert result == TranscriptResult(text=SAID, duration=Decimal(3))

    def test_the_config_part_holds_the_codes_as_json(self):
        vendor = _AssemblyAI()
        _run(vendor.handler(), _payload(make_wav(1)))
        filename, ctype, body = _form(vendor.requests[-1])["config"]
        assert filename is None
        assert ctype == "application/json"
        assert json.loads(body) == {"language_codes": ["hi", "en"]}

    def test_an_id_with_no_languages_sends_no_config_part(self):
        vendor = _AssemblyAI()
        _run(vendor.handler(), _payload(make_wav(1), model=AAI_BARE))
        assert set(_form(vendor.requests[-1])) == {"audio"}

    def test_the_credential_api_base_wins(self):
        vendor = _AssemblyAI()
        _run(vendor.handler(), _payload(make_wav(1), api_base="https://proxy.example/"))
        assert str(vendor.requests[-1].url) == "https://proxy.example/v1/transcribe"

    def test_one_request_per_clip_even_when_long(self):
        """The API takes 20 minutes and the door caps a WAV at 600 s."""
        vendor = _AssemblyAI()
        result = _run(vendor.handler(), _payload(make_wav(65, rate=8_000)))
        assert len(vendor.requests) == 1
        assert result.duration == Decimal(65)

    def test_the_door_reads_the_result(self):
        from customer_console.main import _transcript_of

        result = _run(_AssemblyAI().handler(), _payload(make_wav(3)))
        assert _transcript_of(result) == SAID
        assert router_mod.duration_seconds(result) == Decimal(3)

    def test_the_result_duration_is_the_measure_and_not_the_vendors_figure(self):
        vendor = _AssemblyAI([(200, _ok_body(duration_ms=90_000))])
        result = _run(vendor.handler(), _payload(make_wav(3)))
        assert result.duration == Decimal(3)

    def test_an_empty_clip_calls_no_vendor(self):
        vendor = _AssemblyAI()
        result = _run(vendor.handler(), _payload(make_wav(0)))
        assert vendor.requests == []
        assert result == TranscriptResult(text="", duration=Decimal(0))

    @pytest.mark.parametrize("audio, model", [
        (WEBM, AAI_MODEL),
        (b"RIFF....WAVEfake", AAI_MODEL),
        (make_wav(1), "assemblyai/universal-3-5-pro/xx"),
    ], ids=["webm", "fake-riff", "bad-language"])
    def test_a_call_it_cannot_make_reaches_no_vendor(self, audio, model):
        vendor = _AssemblyAI()
        with pytest.raises(NativeProviderError) as exc:
            _run(vendor.handler(), _payload(audio, model=model))
        assert vendor.requests == []
        assert exc.value.status_code is None
        assert exc.value.terminal is False

    def test_the_payload_never_prints_the_key_or_the_audio(self):
        text = repr(_payload(b"secret-audio-bytes"))
        assert KEY not in text
        assert "secret-audio-bytes" not in text


# ── Do 3: failures and failover ──────────────────────────────────────────────

class TestTheFailures:
    @pytest.mark.parametrize("status", [500, 503, 504, 429, 401, 400, 413])
    def test_a_vendor_status_travels_and_the_body_does_not(self, status):
        vendor = _AssemblyAI([(status, _problem(status))])
        with pytest.raises(NativeProviderError) as exc:
            _run(vendor.handler(), _payload(make_wav(1)))
        assert exc.value.status_code == status
        assert exc.value.terminal is False
        assert "quotes the request" not in str(exc.value)

    @pytest.mark.parametrize("body", [{"request_time_ms": 4}, {"text": 7}, b"not json"])
    def test_a_200_with_no_text_is_terminal(self, body):
        """The vendor has charged for it, so no second vendor pays again."""
        vendor = _AssemblyAI([(200, body)])
        with pytest.raises(NativeProviderError) as exc:
            _run(vendor.handler(), _payload(make_wav(1)))
        assert exc.value.terminal is True

    @pytest.mark.parametrize("make", [
        lambda r: httpx.ConnectError("refused", request=r),
        lambda r: httpx.ReadTimeout("slow", request=r),
    ], ids=["connect", "timeout"])
    def test_no_answer_is_retryable(self, make):
        def _drop(request: httpx.Request) -> httpx.Response:
            raise make(request)

        handler = stt_assemblyai.AssemblyAIHandler(transport=httpx.MockTransport(_drop))
        with pytest.raises(NativeProviderError) as exc:
            _run(handler, _payload(make_wav(1)))
        assert exc.value.status_code is None
        assert router_mod.is_retryable(exc.value.status_code)

    def _walk(self, vendor: _AssemblyAI) -> tuple[object, ResolvedTier, list[str]]:
        """Walk a two-step chain: AssemblyAI first, a stubbed Whisper second."""
        aai = ResolvedTier(tier="tier-stt", model=AAI_MODEL, task="transcribe", rank=1)
        whisper = ResolvedTier(tier="tier-stt", model=WHISPER, task="transcribe", rank=2)
        tried: list[str] = []
        handler = vendor.handler()

        async def _attempt(step: ResolvedTier) -> object:
            tried.append(step.model)
            if step is aai:
                return await handler.call("transcribe", _payload(make_wav(1)))
            return {"text": "from whisper"}

        answer, resolved = asyncio.run(router_mod.walk_chain([aai, whisper], _attempt))
        return answer, resolved, tried

    @pytest.mark.parametrize("status", [500, 502, 503, 504, 429])
    def test_a_5xx_or_a_504_falls_to_the_next_step(self, status):
        answer, resolved, tried = self._walk(_AssemblyAI([(status, _problem(status))]))
        assert tried == [AAI_MODEL, WHISPER]
        assert resolved.model == WHISPER
        assert answer == {"text": "from whisper"}

    def test_a_dropped_connection_falls_to_the_next_step(self):
        def _drop(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("refused", request=request)

        vendor = _AssemblyAI()
        vendor.transport = lambda: httpx.MockTransport(_drop)  # type: ignore[method-assign]
        _answer, resolved, tried = self._walk(vendor)
        assert tried == [AAI_MODEL, WHISPER]
        assert resolved.model == WHISPER

    @pytest.mark.parametrize("status", [400, 413])
    def test_a_client_error_does_not_fail_over(self, status):
        """A 400 says the REQUEST is wrong. Another vendor would pay for it too."""
        vendor = _AssemblyAI([(status, _problem(status))])
        with pytest.raises(UpstreamFailed):
            self._walk(vendor)
        assert len(vendor.requests) == 1

    def test_a_401_strikes_the_vendor_and_falls_to_the_next_step(self):
        """401 says OUR key is bad (`router.CREDENTIAL_STATUSES`). The walk
        strikes the vendor and takes the next one, as it does for a Sarvam 403.
        The handler passes the status through and adds no rule of its own."""
        vendor = _AssemblyAI([(401, _problem(401))])
        _answer, resolved, tried = self._walk(vendor)
        assert tried == [AAI_MODEL, WHISPER]
        assert resolved.model == WHISPER
        assert 401 in router_mod.CREDENTIAL_STATUSES
        assert 400 in router_mod.TERMINAL_STATUSES

    def test_a_terminal_failure_stops_the_walk(self):
        with pytest.raises(UpstreamFailed):
            self._walk(_AssemblyAI([(200, {"no": "text"})]))


class TestTheKeyStaysOutOfEveryRecord:
    @pytest.mark.parametrize("status", [400, 401, 500, 504])
    def test_no_log_line_and_no_error_carries_the_key(self, status, caplog):
        # The vendor body echoes the key, as a careless error page might.
        vendor = _AssemblyAI([(status, _problem(status, detail=f"bad key {KEY}"))])
        payload = _payload(make_wav(1))
        with caplog.at_level(logging.DEBUG), pytest.raises(NativeProviderError) as exc:
            _run(vendor.handler(), payload)
        seen = [exc.value.__str__(), repr(exc.value), repr(payload), caplog.text]
        for record in caplog.records:
            seen.append(record.getMessage())
            seen.append(repr(record.__dict__))
        for line in seen:
            assert KEY not in line
        assert not any(b"\x00\x00\x00\x00" in repr(r.__dict__).encode() for r in caplog.records)

    def test_an_unreadable_200_logs_no_body_and_no_key(self, caplog):
        vendor = _AssemblyAI([(200, f"oops {KEY}".encode())])
        with caplog.at_level(logging.DEBUG), pytest.raises(NativeProviderError) as exc:
            _run(vendor.handler(), _payload(make_wav(1)))
        assert exc.value.terminal is True
        assert KEY not in caplog.text
        assert KEY not in str(exc.value)

    def test_a_refused_model_logs_no_key(self, caplog):
        with caplog.at_level(logging.DEBUG), pytest.raises(NativeProviderError):
            _run(_AssemblyAI().handler(), _payload(make_wav(1), model="assemblyai/nope"))
        assert KEY not in caplog.text


# ── The door, with no database ──────────────────────────────────────────────

def _cred() -> router_mod.Credential:
    return router_mod.Credential(secret=KEY, api_base=None, byok=False)


class TestTheDoorHelpers:
    """The pure halves of `audio_transcriptions`. The R8 half drives the route."""

    aai = ResolvedTier(tier="tier-stt", model=AAI_MODEL, task="transcribe", rank=1)
    whisper = ResolvedTier(tier="tier-stt", model=WHISPER, task="transcribe", rank=2)
    verbs: ClassVar[dict[str, str]] = {AAI_MODEL: "native_assemblyai", WHISPER: "atranscription"}
    creds: ClassVar[dict[str, router_mod.Credential | None]] = {
        "assemblyai": _cred(), "groq": _cred()}

    def test_a_wav_clip_keeps_every_step(self):
        from customer_console.main import _transcribe_attempts

        steps = _transcribe_attempts([self.aai, self.whisper], self.creds, self.verbs,
                                     is_wav=True)
        assert steps == [self.aai, self.whisper]

    def test_a_clip_that_is_not_wav_skips_the_assemblyai_step(self):
        from customer_console.main import _transcribe_attempts

        steps = _transcribe_attempts([self.aai, self.whisper], self.creds, self.verbs,
                                     is_wav=False)
        assert steps == [self.whisper]

    def test_no_step_left_is_a_415(self):
        from customer_console.main import _transcribe_attempts
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc:
            _transcribe_attempts([self.aai], self.creds, self.verbs, is_wav=False)
        assert exc.value.status_code == 415

    def test_a_native_step_gets_a_typed_payload(self):
        from customer_console.main import _transcribe_kwargs

        audio = make_wav(1)
        kwargs = _transcribe_kwargs(self.aai, cred=_cred(), invocation="native_assemblyai",
                                    audio=audio, filename="clip.wav", org_id="org-1",
                                    request_id="rtr-1")
        assert set(kwargs) == {"model", "task", "invocation", "payload"}
        payload = kwargs["payload"]
        assert isinstance(payload, TranscribePayload)
        assert (payload.model, payload.audio, payload.api_key) == (AAI_MODEL, audio, KEY)

    def test_the_vendors_duration_never_sets_the_bill_of_a_wav_clip(self):
        """The vendor says 90 s. The WAV holds 3 s, and 3 s bills."""
        from customer_console.main import _transcribe_minutes

        result = _run(_AssemblyAI([(200, _ok_body(duration_ms=90_000))]).handler(),
                      _payload(make_wav(3)))
        measured = wav_seconds(make_wav(3))
        minutes = _transcribe_minutes(measured, result, self.aai, org_id="o", request_id="r")
        assert minutes == Decimal("0.0500")

    def test_the_default_provider_call_sends_the_verb_to_its_handler(self, monkeypatch):
        """The dispatch lives INSIDE `_default_provider_call` (§6A.10b)."""
        vendor = _AssemblyAI()
        monkeypatch.setitem(handlers.NATIVE_HANDLERS, "native_assemblyai", vendor.handler())
        result = asyncio.run(router_mod._default_provider_call(
            model=AAI_MODEL, task="transcribe", invocation="native_assemblyai",
            payload=_payload(make_wav(2))))
        assert isinstance(result, TranscriptResult)
        assert len(vendor.requests) == 1


# ══ R8: the route, on a real Postgres ═══════════════════════════════════════
#
# 🔴 **These fences DRIVE `POST /v1/audio/transcriptions` with the REAL handler**
# behind an `httpx.MockTransport`, and read the `usage_event` row it wrote. A
# litellm step answers from the `provider` stub, so a failover lands on a step
# that costs nothing. The fixtures come from `test_customer_console_tasks.py`,
# which owns the ladder and the org setup.

_URL = os.environ.get("CUSTOMER_CONSOLE_DATABASE_URL", "").strip()

_DB_GATE = pytest.mark.skipif(
    not _URL,
    reason=(
        "CUSTOMER_CONSOLE_DATABASE_URL unset — R8 requires a REAL Postgres. "
        "A skip here is not a pass; CI must set it."
    ),
)

#: The label of every row the fixtures write, so the teardown removes its OWN
#: rows and never a sibling suite's.
_AAI_LABEL = "vox6-fence"

WAV_3S = make_wav(3)
WAV_3S_MINUTES = Decimal("0.0500")

@pytest.fixture(scope="module")
def _schema():
    eng = create_engine(_URL, future=True)
    with eng.begin() as conn:
        apply_ladder(conn)

@pytest.fixture
def aai_vendor(monkeypatch, provider):
    """The REAL `AssemblyAIHandler`, and the `provider` stub for litellm steps.

    ⚠️ **The stub is restored by hand, never by `monkeypatch`.**
    `provider` restores the original call at its own teardown, and a
    `monkeypatch` undo that ran after it would put this wrapper back for
    the next suite.
    """
    vendor = _AssemblyAI([(200, _ok_body(duration_ms=90_000))])
    monkeypatch.setitem(handlers.NATIVE_HANDLERS, "native_assemblyai",
                        stt_assemblyai.AssemblyAIHandler(transport=vendor.transport()))
    stub = router_mod._PROVIDER_CALL[0]

    async def _route(**kwargs):
        if str(kwargs.get("invocation", "")).startswith(router_mod.NATIVE_PREFIX):
            return await router_mod._default_provider_call(**kwargs)
        return await stub(**kwargs)

    router_mod.set_provider_call(_route)
    yield vendor
    router_mod.set_provider_call(stub)

@pytest.fixture
def stt_tier(db, org_key):
    """Make a fresh `transcribe` tier with the models given, ranked in order.

    It binds the AssemblyAI capability row and a platform `assemblyai` key.
    ⚠️ **Removed afterwards**, rows and key alike, because `tier_binding`
    and `model_capability` are global rows.
    """
    made: list[str] = []

    def _make(*models: str) -> str:
        tier = f"tier-stt-vox6-{uuid.uuid4().hex[:8]}"
        made.append(tier)  # noted FIRST, so a half-made tier is removed too
        with db.begin() as c:
            c.execute(text(
                "INSERT INTO tier_catalog (slug, label, task, customer_visible) "
                "VALUES (:t, 'VOX-6 fence', 'transcribe', FALSE)"), {"t": tier})
            for rank, model in enumerate(models, start=1):
                c.execute(text(
                    "INSERT INTO tier_binding (tier, model, task, rank, effective_from) "
                    "VALUES (:t, :m, 'transcribe', :r, '2026-01-01T00:00:00Z')"),
                    {"t": tier, "m": model, "r": rank})
            # An UPSERT: migration 010 replays at the top of each suite.
            c.execute(text(
                "INSERT INTO model_capability (model, task, invocation, streams) "
                "VALUES (:m, 'transcribe', 'native_assemblyai', FALSE) "
                "ON CONFLICT (model, task) DO UPDATE "
                "SET invocation = EXCLUDED.invocation"), {"m": AAI_MODEL})
            c.execute(text(
                "INSERT INTO provider_credential (provider, secret_enc, label) "
                "VALUES ('assemblyai', :s, :l) ON CONFLICT DO NOTHING"),
                {"s": router_mod.encrypt_secret(KEY), "l": _AAI_LABEL})
        _price_tier(db, tier=tier, task="transcribe", unit="minutes",
                    per_unit=CREDITS_PER_MINUTE)
        return tier

    yield _make
    with db.begin() as c:
        for tier in made:
            c.execute(text("DELETE FROM tier_binding WHERE tier = :t"), {"t": tier})
            c.execute(text("DELETE FROM tier_rate_card WHERE tier = :t"), {"t": tier})
            c.execute(text("DELETE FROM tier_catalog WHERE slug = :t"), {"t": tier})
        c.execute(text(
            "DELETE FROM model_capability WHERE model = :m AND task = 'transcribe'"),
            {"m": AAI_MODEL})
        c.execute(text(
            "DELETE FROM provider_credential "
            "WHERE provider = 'assemblyai' AND label = :l"), {"l": _AAI_LABEL})

@_DB_GATE
@pytest.mark.usefixtures("_schema")
class TestAssemblyAIOnTheTranscribeDoor:
    """VOX-6, read off the row a real POST wrote."""

    def test_a_wav_clip_bills_the_measured_minutes_and_not_the_vendors(
            self, client, db, org_key, org_id, provider, aai_vendor, stt_tier, caplog):
        tier = stt_tier(AAI_MODEL, WHISPER)
        _, key = org_key
        with caplog.at_level(logging.DEBUG):
            answer = _post_clip(client, key, model=tier, audio=WAV_3S,
                                filename="clip.wav")
        assert answer.status_code == 200, answer.text
        assert answer.json() == {"text": SAID}

        [request] = aai_vendor.requests
        assert str(request.url) == "https://sync.assemblyai.com/v1/transcribe"
        assert request.headers["authorization"] == KEY
        assert request.headers["x-aai-model"] == "universal-3-5-pro"
        form = _form(request)
        assert json.loads(form["config"][2]) == {"language_codes": ["hi", "en"]}
        assert provider["calls"] == []
        assert KEY not in caplog.text

        # 🔴 ONE `transcribe` row. The vendor said 90 s. The WAV holds 3 s.
        [row] = _usage_rows(db, org_id)
        assert row.task == "transcribe"
        assert row.tier == tier
        assert row.model == AAI_MODEL
        assert row.quantity == WAV_3S_MINUTES
        assert row.unit == "minutes"
        assert row.billed_credits == WAV_3S_MINUTES * CREDITS_PER_MINUTE
        assert row.refusal_reason is None

    def test_a_5xx_fails_over_and_the_next_step_bills_the_clip(
            self, client, db, org_key, org_id, provider, aai_vendor, stt_tier):
        aai_vendor.answers = [(504, _problem(504))]
        tier = stt_tier(AAI_MODEL, WHISPER)
        _, key = org_key
        answer = _post_clip(client, key, model=tier, audio=WAV_3S)
        assert answer.status_code == 200, answer.text
        assert answer.json() == {"text": TRANSCRIPT}
        assert len(aai_vendor.requests) == 1
        assert provider["calls"][-1]["model"] == WHISPER
        [row] = _usage_rows(db, org_id)
        assert row.model == WHISPER
        assert row.quantity == WAV_3S_MINUTES

    def test_a_400_does_not_fail_over_and_writes_no_served_row(
            self, client, db, org_key, org_id, provider, aai_vendor, stt_tier):
        aai_vendor.answers = [(400, _problem(400))]
        tier = stt_tier(AAI_MODEL, WHISPER)
        _, key = org_key
        answer = _post_clip(client, key, model=tier, audio=WAV_3S)
        assert answer.status_code >= 400
        assert len(aai_vendor.requests) == 1
        assert provider["calls"] == []
        assert all(r.refusal_reason is not None for r in _usage_rows(db, org_id))

    def test_a_webm_clip_answers_415_and_writes_no_row(
            self, client, db, org_key, org_id, provider, aai_vendor, stt_tier):
        tier = stt_tier(AAI_MODEL)
        _, key = org_key
        answer = _post_clip(client, key, model=tier, audio=WEBM, filename="clip.webm")
        assert answer.status_code == 415, answer.text
        assert "WAV" in answer.json()["detail"]
        assert aai_vendor.requests == []
        assert provider["calls"] == []
        assert _usage_rows(db, org_id) == []

    def test_one_row_per_clip(
            self, client, db, org_key, org_id, aai_vendor, stt_tier):
        tier = stt_tier(AAI_MODEL)
        _, key = org_key
        for _ in range(2):
            assert _post_clip(client, key, model=tier, audio=WAV_3S).status_code == 200
        rows = _usage_rows(db, org_id)
        assert len(rows) == 2
        assert {r.task for r in rows} == {"transcribe"}
        assert {r.quantity for r in rows} == {WAV_3S_MINUTES}
