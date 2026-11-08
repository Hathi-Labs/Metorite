"""VOX-2 — Sarvam in the Console's transcribe door, with no database.

Spec: ``project-docs/specs/projects_ai_chat.md`` §31 VOX-2 · ``customer_console.md``
§6A.10a (the door) and §6A.10b (the handler seam).

🔴 **What this suite proves, in one line each.**

* ``native_sarvam`` is in both invocation sets, serves ``transcribe`` only,
  and a ``sarvam/`` transcribe model takes it only.
* The handler sends ``POST /speech-to-text`` with ``api-subscription-key``,
  the vendor model and the mode it splits off the bound id.
* A clip above 29 s goes in parts, and the transcripts join in order.
* A 5xx is retryable, a 403 strikes the vendor, and a 200 with no transcript
  is TERMINAL. The chain walk fails over on the first two.
* The door bills a WAV clip by its own length, for a Sarvam step and for a
  litellm step. A clip that is not WAV skips a Sarvam step, and with no step
  left the door answers 415.

⚠️ **The vendor is ``httpx.MockTransport``. No test here reaches the
network.** The Sarvam body is the documented shape, read on 2026-11-08 from
https://docs.sarvam.ai/api-reference-docs/speech-to-text/transcribe.

The R8 half — the ``usage_event`` row a real ``POST /v1/audio/transcriptions``
writes — is in ``test_customer_console_tasks.py``, beside the door's other
fences.
"""
from __future__ import annotations

import asyncio
import io
import struct
import wave
from decimal import Decimal
from email.parser import BytesParser
from email.policy import HTTP
from typing import ClassVar

import pytest

pytest.importorskip("fastapi")
httpx = pytest.importorskip("httpx")

from customer_console import catalog, handlers, stt_sarvam  # noqa: E402
from customer_console import router as router_mod  # noqa: E402
from customer_console.handlers import (  # noqa: E402
    NativeProviderError,
    TranscribePayload,
    TranscriptResult,
)
from customer_console.router import ResolvedTier, UpstreamFailed  # noqa: E402
from customer_console.wav import read_wav, split_wav, wav_seconds  # noqa: E402

SARVAM_MODEL = "sarvam/saaras:v3/translit"
WHISPER = "groq/whisper-large-v3-turbo"
KEY = "sk-sarvam-test-key"


def make_wav(seconds: float, *, rate: int = 16_000, channels: int = 1) -> bytes:
    """A PCM WAV of silence, built with the standard library."""
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as writer:
        writer.setnchannels(channels)
        writer.setsampwidth(2)
        writer.setframerate(rate)
        writer.writeframes(b"\x00\x00" * channels * int(seconds * rate))
    return buffer.getvalue()


#: The first bytes of a WebM file (the EBML magic). Not a WAV.
WEBM = b"\x1a\x45\xdf\xa3" + b"\x00" * 64


class _Sarvam:
    """A recorded Sarvam behind ``httpx.MockTransport``. No network.

    ``answers`` is a list of ``(status, body)`` pairs, one per request in
    order. The last pair repeats.
    """

    def __init__(self, answers: list[tuple[int, object]] | None = None) -> None:
        self.answers = answers or [(200, {"request_id": "r1", "transcript": "mera phone",
                                          "language_code": "hi-IN"})]
        self.requests: list[httpx.Request] = []

    def transport(self) -> httpx.MockTransport:
        def _answer(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            status, body = self.answers[min(len(self.requests) - 1, len(self.answers) - 1)]
            if isinstance(body, bytes):
                return httpx.Response(status, content=body)
            return httpx.Response(status, json=body)
        return httpx.MockTransport(_answer)

    def handler(self) -> stt_sarvam.SarvamHandler:
        return stt_sarvam.SarvamHandler(transport=self.transport())


def _form(request: httpx.Request) -> dict[str, tuple[str | None, bytes]]:
    """The multipart fields of one request: name → (file name, bytes)."""
    head = f"Content-Type: {request.headers['content-type']}\r\n\r\n".encode()
    message = BytesParser(policy=HTTP).parsebytes(head + request.read())
    fields: dict[str, tuple[str | None, bytes]] = {}
    for part in message.iter_parts():
        name = part.get_param("name", header="content-disposition")
        fields[str(name)] = (part.get_filename(), part.get_payload(decode=True) or b"")
    return fields


def _payload(audio: bytes, *, model: str = SARVAM_MODEL, filename: str = "clip.wav",
             api_base: str | None = None) -> TranscribePayload:
    return TranscribePayload(model=model, audio=audio, filename=filename, api_key=KEY,
                             api_base=api_base, organization_id="org-1", request_id="rtr-1")


def _run(handler: stt_sarvam.SarvamHandler, payload: TranscribePayload) -> TranscriptResult:
    return asyncio.run(handler.call("transcribe", payload))


# ── Do 1: the verb, the task and the prefix ──────────────────────────────────

class TestTheNativeVerbForSpeech:
    def test_native_sarvam_is_in_both_sets(self):
        assert "native_sarvam" in catalog.KNOWN_INVOCATIONS
        assert "native_sarvam" in router_mod.SERVING_INVOCATIONS
        assert router_mod.SERVING_INVOCATIONS <= catalog.KNOWN_INVOCATIONS
        assert isinstance(handlers.NATIVE_HANDLERS["native_sarvam"], stt_sarvam.SarvamHandler)

    def test_transcribe_is_a_native_task_and_decide_stays_native_only(self):
        assert "transcribe" in catalog.NATIVE_TASKS
        assert set(catalog.NATIVE_ONLY_TASKS) == {"decide"}

    def test_the_handler_table_names_vendor_and_task(self):
        assert handlers.native_provider_of("native_sarvam") == "sarvam"
        assert handlers.native_task_of("native_sarvam") == "transcribe"
        assert handlers.native_task_of("native_typesafe") == "decide"
        assert handlers.native_verb_for("sarvam", "transcribe") == "native_sarvam"
        assert handlers.native_verb_for("sarvam", "chat") is None
        assert handlers.native_requires_wav("native_sarvam") is True
        assert handlers.native_requires_wav("native_typesafe") is False
        assert handlers.native_requires_wav("atranscription") is False

    @pytest.mark.parametrize("verb", ["native_sarvam", "atranscription"])
    def test_transcribe_takes_a_litellm_verb_or_native_sarvam(self, verb):
        assert catalog.check_invocation_for_task(verb, "transcribe") == verb

    @pytest.mark.parametrize("verb, task, served", [
        ("native_sarvam", "decide", "transcribe"),
        ("native_sarvam", "chat", "transcribe"),
        ("native_typesafe", "transcribe", "decide"),
        ("native_aimlapi", "transcribe", "decide"),
    ])
    def test_a_native_verb_serves_only_its_handlers_task(self, verb, task, served):
        with pytest.raises(catalog.CatalogRefused) as exc:
            catalog.check_invocation_for_task(verb, task)
        assert f"serves only {served}" in str(exc.value)

    def test_decide_still_takes_a_native_verb_only(self):
        with pytest.raises(catalog.CatalogRefused):
            catalog.check_invocation_for_task("acompletion", "decide")

    def test_a_sarvam_transcribe_model_takes_native_sarvam_only(self):
        """Do 1: the prefix `sarvam/` ties to `native_sarvam`. litellm has no
        Sarvam transcription, so the litellm verb would 502 on the first clip."""
        with pytest.raises(catalog.CatalogRefused) as exc:
            catalog.check_model_for_invocation(SARVAM_MODEL, "atranscription", task="transcribe")
        assert "'native_sarvam'" in str(exc.value)
        assert catalog.check_model_for_invocation(
            SARVAM_MODEL, "native_sarvam", task="transcribe") is None

    @pytest.mark.parametrize("model, verb, task", [
        (WHISPER, "atranscription", "transcribe"),
        ("sarvam/sarvam-m", "acompletion", "chat"),
        ("typesafe/jev-1.13.0", "native_typesafe", "decide"),
    ])
    def test_other_pairs_are_unchanged(self, model, verb, task):
        assert catalog.check_model_for_invocation(model, verb, task=task) is None

    def test_native_sarvam_needs_the_sarvam_credential(self):
        with pytest.raises(catalog.CatalogRefused) as exc:
            catalog.check_model_for_invocation(WHISPER, "native_sarvam", task="transcribe")
        assert "'groq'" in str(exc.value)
        assert "'sarvam'" in str(exc.value)


# ── Do 3: the mode is in the model id ────────────────────────────────────────

class TestTheModeIsInTheModelId:
    @pytest.mark.parametrize("model, expected", [
        ("sarvam/saaras:v3/translit", ("saaras:v3", "translit")),
        ("sarvam/saaras:v3", ("saaras:v3", "translit")),
        ("sarvam/saaras:v3/codemix", ("saaras:v3", "codemix")),
        ("sarvam/saaras:v3/transcribe", ("saaras:v3", "transcribe")),
        ("sarvam/saaras:v4/translate", ("saaras:v4", "translate")),
    ])
    def test_the_split(self, model, expected):
        assert stt_sarvam.split_model(model) == expected

    @pytest.mark.parametrize("model", [
        "sarvam/saaras:v3/romanize",
        "sarvam/saaras:v3/verbatim",
        "sarvam/",
        "sarvam",
        "groq/saaras:v3/translit",
        "sarvam//translit",
    ])
    def test_an_id_the_handler_cannot_call(self, model):
        assert stt_sarvam.split_model(model) is None
        assert stt_sarvam.model_refusal(model) is not None

    def test_a_wrong_mode_is_refused_at_declare_time(self):
        with pytest.raises(catalog.CatalogRefused) as exc:
            catalog.check_model_for_invocation(
                "sarvam/saaras:v3/romanize", "native_sarvam", task="transcribe")
        assert "translit" in str(exc.value)

    def test_the_default_is_as_spoken_in_roman_script(self):
        """§31.6 Q1: Hinglish comes out as spoken, in Roman script."""
        assert stt_sarvam.DEFAULT_MODE == "translit"


# ── Do 4: the WAV clip, measured with the standard library ───────────────────

class TestTheWavClip:
    def test_a_wav_clip_is_measured(self):
        assert wav_seconds(make_wav(3)) == Decimal(3)
        assert wav_seconds(make_wav(1.5, rate=8_000, channels=2)) == Decimal("1.5")

    @pytest.mark.parametrize("audio", [
        WEBM, b"", b"RIFF....WAVEfake-audio-bytes", b"ID3\x04fake-mp3", b"RIFF",
    ], ids=["webm", "empty", "fake-riff", "mp3", "riff-only"])
    def test_a_clip_that_is_not_wav_reads_as_none(self, audio):
        assert read_wav(audio) is None
        assert wav_seconds(audio) is None

    def test_a_streaming_header_bills_the_frames_present(self):
        """A recorder that streams writes a data size of 0xFFFFFFFF. Read as
        the header states it, two seconds would bill 37 hours."""
        audio = bytearray(make_wav(2))
        data_at = audio.index(b"data")
        audio[data_at + 4: data_at + 8] = struct.pack("<I", 0xFFFFFFFF)
        assert wav_seconds(bytes(audio)) == Decimal(2)

    def test_the_parts_cover_the_clip_in_order(self):
        clip = read_wav(make_wav(65, rate=8_000))
        assert clip is not None
        parts = split_wav(clip, 29)
        lengths = [wav_seconds(part) for part in parts]
        assert lengths == [Decimal(29), Decimal(29), Decimal(7)]

    def test_an_empty_clip_has_no_parts(self):
        clip = read_wav(make_wav(0))
        assert clip is not None
        assert clip.seconds == 0
        assert split_wav(clip, 29) == []


def _with_data_size(audio: bytes, size: int) -> bytes:
    out = bytearray(audio)
    at = out.index(b"data")
    out[at + 4: at + 8] = struct.pack("<I", size)
    return bytes(out)


class TestTheHeaderCannotLieAboutTheBill:
    """P1. The provider decodes the bytes present, so the bill counts them."""

    @pytest.mark.parametrize("declared", [0, 1, 100, 0xFFFFFFFF, 10**9],
                             ids=["zero", "one", "undersized", "max", "oversized"])
    def test_the_declared_data_size_is_ignored(self, declared):
        audio = _with_data_size(make_wav(60), declared)
        assert wav_seconds(audio) == Decimal(60)
        clip = read_wav(audio)
        assert clip is not None and clip.seconds == Decimal(60)

    def test_the_parts_cover_a_clip_whose_header_says_zero(self):
        clip = read_wav(_with_data_size(make_wav(65, rate=8_000), 0))
        assert clip is not None
        lengths = [wav_seconds(part) for part in split_wav(clip, 29)]
        assert lengths == [Decimal(29), Decimal(29), Decimal(7)]

    def test_a_trailing_list_chunk_counts_as_audio(self):
        """We bill the bytes we send. 20 bytes of LIST after `data` are 10
        extra frames at 16-bit mono, and that errs toward the platform."""
        audio = make_wav(1) + b"LIST" + struct.pack("<I", 12) + b"INFOabcdefgh"
        assert wav_seconds(audio) == Decimal(16_000 + 10) / Decimal(16_000)

    def test_a_chunk_before_data_is_stepped_over(self):
        base = make_wav(1)
        at = base.index(b"data")
        audio = base[:at] + b"LIST" + struct.pack("<I", 5) + b"abcde\x00" + base[at:]
        assert wav_seconds(audio) == Decimal(1)

    @pytest.mark.parametrize("value", [0, 500, 4_000_000],
                             ids=["rate0", "rate-low", "rate-high"])
    def test_an_absurd_sample_rate_is_not_a_measurable_wav(self, value):
        audio = bytearray(make_wav(1))
        at = audio.index(b"fmt ")
        audio[at + 12: at + 16] = struct.pack("<I", value)
        assert wav_seconds(bytes(audio)) is None
        assert read_wav(bytes(audio)) is None

    def test_a_zero_block_align_is_not_a_measurable_wav(self):
        audio = bytearray(make_wav(1))
        at = audio.index(b"fmt ")
        audio[at + 20: at + 22] = struct.pack("<H", 0)
        assert wav_seconds(bytes(audio)) is None

    def test_data_before_fmt_is_not_a_measurable_wav(self):
        base = make_wav(1)
        fmt_at, data_at = base.index(b"fmt "), base.index(b"data")
        audio = base[:12] + base[data_at:] + base[fmt_at:data_at]
        assert wav_seconds(audio) is None


# ── Do 2: the request shape ──────────────────────────────────────────────────

class TestTheRequestShape:
    def test_the_handler_sends_the_documented_request(self):
        vendor = _Sarvam()
        audio = make_wav(3)
        result = _run(vendor.handler(), _payload(audio, filename="meeting.wav"))

        [request] = vendor.requests
        assert request.method == "POST"
        assert str(request.url) == "https://api.sarvam.ai/speech-to-text"
        assert request.headers["api-subscription-key"] == KEY
        assert "authorization" not in request.headers
        form = _form(request)
        assert form["model"] == (None, b"saaras:v3")
        assert form["mode"] == (None, b"translit")
        filename, sent = form["file"]
        assert filename == "meeting.wav"
        # The vendor gets a WAV of the same length as the clip.
        assert wav_seconds(sent) == Decimal(3)
        assert result == TranscriptResult(text="mera phone", duration=Decimal(3))

    def test_the_mode_suffix_reaches_the_vendor(self):
        vendor = _Sarvam()
        _run(vendor.handler(), _payload(make_wav(1), model="sarvam/saaras:v3/codemix"))
        assert _form(vendor.requests[-1])["mode"] == (None, b"codemix")

    def test_the_credential_api_base_wins(self):
        vendor = _Sarvam()
        _run(vendor.handler(), _payload(make_wav(1), api_base="https://proxy.example/"))
        assert str(vendor.requests[-1].url) == "https://proxy.example/speech-to-text"

    def test_the_door_reads_the_result(self):
        """Do 2: the result is a shape `_transcript_of` and `duration_seconds` read."""
        from customer_console.main import _transcript_of

        result = _run(_Sarvam().handler(), _payload(make_wav(3)))
        assert _transcript_of(result) == "mera phone"
        assert router_mod.duration_seconds(result) == Decimal(3)

    def test_a_long_clip_goes_in_parts_and_joins_in_order(self):
        vendor = _Sarvam([
            (200, {"transcript": " first ", "request_id": "a", "language_code": "hi-IN"}),
            (200, {"transcript": "second", "request_id": "b", "language_code": "hi-IN"}),
            (200, {"transcript": "third", "request_id": "c", "language_code": "hi-IN"}),
        ])
        result = _run(vendor.handler(), _payload(make_wav(65, rate=8_000)))
        assert len(vendor.requests) == 3
        assert [wav_seconds(_form(r)["file"][1]) for r in vendor.requests] == [
            Decimal(29), Decimal(29), Decimal(7)]
        assert result.text == "first second third"
        assert result.duration == Decimal(65)

    def test_an_empty_clip_calls_no_vendor(self):
        vendor = _Sarvam()
        result = _run(vendor.handler(), _payload(make_wav(0)))
        assert vendor.requests == []
        assert result == TranscriptResult(text="", duration=Decimal(0))

    @pytest.mark.parametrize("audio, model", [
        (WEBM, SARVAM_MODEL),
        (b"RIFF....WAVEfake", SARVAM_MODEL),
        (make_wav(1), "sarvam/saaras:v3/romanize"),
    ], ids=["webm", "fake-riff", "bad-mode"])
    def test_a_call_it_cannot_make_reaches_no_vendor(self, audio, model):
        vendor = _Sarvam()
        with pytest.raises(NativeProviderError) as exc:
            _run(vendor.handler(), _payload(audio, model=model))
        assert vendor.requests == []
        assert exc.value.status_code is None
        assert exc.value.terminal is False

    def test_the_payload_never_prints_the_key_or_the_audio(self):
        text = repr(_payload(b"secret-audio-bytes"))
        assert KEY not in text
        assert "secret-audio-bytes" not in text


# ── Do 5: failures and failover ──────────────────────────────────────────────

class TestTheFailures:
    @pytest.mark.parametrize("status", [500, 503, 429, 403, 422])
    def test_a_vendor_status_travels_and_the_body_does_not(self, status):
        vendor = _Sarvam([(status, {"error": {"message": "quotes the request"}})])
        with pytest.raises(NativeProviderError) as exc:
            _run(vendor.handler(), _payload(make_wav(1)))
        assert exc.value.status_code == status
        assert exc.value.terminal is False
        assert "quotes the request" not in str(exc.value)

    @pytest.mark.parametrize("body", [{"request_id": "x"}, {"transcript": 7}, b"not json"])
    def test_a_200_with_no_transcript_is_terminal(self, body):
        """The vendor has charged for it, so no second vendor pays again."""
        vendor = _Sarvam([(200, body)])
        with pytest.raises(NativeProviderError) as exc:
            _run(vendor.handler(), _payload(make_wav(1)))
        assert exc.value.terminal is True

    def test_no_answer_is_retryable(self):
        def _drop(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("refused", request=request)

        handler = stt_sarvam.SarvamHandler(transport=httpx.MockTransport(_drop))
        with pytest.raises(NativeProviderError) as exc:
            _run(handler, _payload(make_wav(1)))
        assert exc.value.status_code is None
        assert router_mod.is_retryable(exc.value.status_code)

    def test_a_part_that_fails_after_one_served_is_logged(self, caplog):
        vendor = _Sarvam([(200, {"transcript": "one"}), (503, {"error": {}})])
        with caplog.at_level("WARNING", logger="customer_console.stt_sarvam"), \
                pytest.raises(NativeProviderError):
            _run(vendor.handler(), _payload(make_wav(40, rate=8_000)))
        [line] = [r for r in caplog.records if r.getMessage() == "stt_sarvam.partial_failure"]
        assert line.parts_served == 1
        assert line.parts_total == 2

    def _walk(self, vendor: _Sarvam) -> tuple[object, ResolvedTier, list[str]]:
        """Walk a two-step chain: Sarvam first, a stubbed Whisper second."""
        sarvam = ResolvedTier(tier="tier-stt", model=SARVAM_MODEL, task="transcribe", rank=1)
        whisper = ResolvedTier(tier="tier-stt", model=WHISPER, task="transcribe", rank=2)
        tried: list[str] = []
        handler = vendor.handler()

        async def _attempt(step: ResolvedTier) -> object:
            tried.append(step.model)
            if step is sarvam:
                return await handler.call("transcribe", _payload(make_wav(1)))
            return {"text": "from whisper"}

        answer, resolved = asyncio.run(router_mod.walk_chain([sarvam, whisper], _attempt))
        return answer, resolved, tried

    @pytest.mark.parametrize("status", [500, 503, 403])
    def test_a_sarvam_failure_falls_to_the_next_step(self, status):
        answer, resolved, tried = self._walk(_Sarvam([(status, {"error": {}})]))
        assert tried == [SARVAM_MODEL, WHISPER]
        assert resolved.model == WHISPER
        assert answer == {"text": "from whisper"}

    def test_a_terminal_failure_stops_the_walk(self):
        with pytest.raises(UpstreamFailed):
            self._walk(_Sarvam([(200, {"no": "transcript"})]))


# ── The door, with no database ──────────────────────────────────────────────

def _cred() -> router_mod.Credential:
    return router_mod.Credential(secret=KEY, api_base=None, byok=False)


class TestTheDoorHelpers:
    """The pure halves of `audio_transcriptions`. The R8 half drives the route."""

    sarvam = ResolvedTier(tier="tier-stt", model=SARVAM_MODEL, task="transcribe", rank=1)
    whisper = ResolvedTier(tier="tier-stt", model=WHISPER, task="transcribe", rank=2)
    verbs: ClassVar[dict[str, str]] = {SARVAM_MODEL: "native_sarvam", WHISPER: "atranscription"}
    creds: ClassVar[dict[str, router_mod.Credential | None]] = {
        "sarvam": _cred(), "groq": _cred()}

    def test_a_wav_clip_keeps_every_step(self):
        from customer_console.main import _transcribe_attempts

        steps = _transcribe_attempts([self.sarvam, self.whisper], self.creds, self.verbs,
                                     is_wav=True)
        assert steps == [self.sarvam, self.whisper]

    def test_a_clip_that_is_not_wav_skips_the_sarvam_step(self):
        from customer_console.main import _transcribe_attempts

        steps = _transcribe_attempts([self.sarvam, self.whisper], self.creds, self.verbs,
                                     is_wav=False)
        assert steps == [self.whisper]

    def test_no_step_left_is_a_415(self):
        from customer_console.main import _transcribe_attempts
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc:
            _transcribe_attempts([self.sarvam], self.creds, self.verbs, is_wav=False)
        assert exc.value.status_code == 415

    def test_no_key_is_still_the_503_first(self):
        from customer_console.main import _transcribe_attempts
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc:
            _transcribe_attempts([self.sarvam], {"sarvam": None}, self.verbs, is_wav=False)
        assert exc.value.status_code == 503

    def test_a_native_step_gets_a_typed_payload(self):
        from customer_console.main import _transcribe_kwargs

        audio = make_wav(1)
        kwargs = _transcribe_kwargs(self.sarvam, cred=_cred(), invocation="native_sarvam",
                                    audio=audio, filename="clip.wav", org_id="org-1",
                                    request_id="rtr-1")
        assert set(kwargs) == {"model", "task", "invocation", "payload"}
        assert kwargs["task"] == "transcribe"
        payload = kwargs["payload"]
        assert isinstance(payload, TranscribePayload)
        assert (payload.model, payload.audio, payload.filename, payload.api_key) == (
            SARVAM_MODEL, audio, "clip.wav", KEY)
        assert payload.request_id == "rtr-1"

    def test_a_litellm_step_keeps_its_kwargs(self):
        from customer_console.main import _transcribe_kwargs

        kwargs = _transcribe_kwargs(self.whisper, cred=_cred(), invocation="atranscription",
                                    audio=b"x", filename="clip.wav", org_id="org-1",
                                    request_id="rtr-1")
        assert kwargs["response_format"] == "verbose_json"
        assert kwargs["invocation"] == "atranscription"
        assert "payload" not in kwargs

    def test_the_larger_of_the_clip_and_the_vendors_figure_bills(self):
        """The vendor says 90 s. The WAV holds 3 s. The vendor decoded and
        charged for 90 s, so 90 s bills. The clip wins when it is larger."""
        from customer_console.main import _transcribe_minutes

        reply = {"text": "x", "usage": {"type": "duration", "seconds": 90}}
        minutes = _transcribe_minutes(Decimal(3), reply, self.whisper, org_id="o",
                                      request_id="r")
        assert minutes == Decimal("1.5000")
        assert _transcribe_minutes(Decimal(120), reply, self.whisper, org_id="o",
                                   request_id="r") == Decimal("2.0000")
        assert _transcribe_minutes(Decimal(3), {"text": "x"}, self.whisper, org_id="o",
                                   request_id="r") == Decimal("0.0500")

    def test_another_format_keeps_the_vendors_figure(self):
        from customer_console.main import _transcribe_minutes

        reply = {"text": "x", "usage": {"type": "duration", "seconds": 90}}
        assert _transcribe_minutes(None, reply, self.whisper, org_id="o",
                                   request_id="r") == Decimal("1.5000")
        assert _transcribe_minutes(None, {"text": "x"}, self.whisper, org_id="o",
                                   request_id="r") == Decimal(0)

    def test_the_default_provider_call_sends_native_sarvam_to_its_handler(self, monkeypatch):
        """The dispatch lives INSIDE `_default_provider_call` (§6A.10b)."""
        vendor = _Sarvam()
        monkeypatch.setitem(handlers.NATIVE_HANDLERS, "native_sarvam", vendor.handler())
        result = asyncio.run(router_mod._default_provider_call(
            model=SARVAM_MODEL, task="transcribe", invocation="native_sarvam",
            payload=_payload(make_wav(2))))
        assert isinstance(result, TranscriptResult)
        assert len(vendor.requests) == 1
