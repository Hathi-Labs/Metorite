"""The chat's voice input route (``projects_ai_chat.md`` §31, VOX-1).

``/chat/transcribe`` takes one spoken clip and answers its text. These cases
run the real route, the real flag reader, the real ``routing_is_on`` and the
real ``transcribe_on_console``, against a stubbed Console (an
``httpx.MockTransport``) and a stubbed tenant Redis.

What must hold (VOX-1 Fence):

* The flag: ``CHAT_VOICE_ORGS`` off answers 404, so the composer draws no mic.
* Routing off refuses, and calls nothing, because a call off the Console
  would not bill.
* The caps: 10 MB, an audio type whose extension agrees, 120 s of WAV, and
  30 clips for each member in each hour.
* The status map: each Console answer gives ONE sentence for the member.
* No file is written: the clip stays in memory from the body to the Console.
"""
from __future__ import annotations

import io
import re
import wave
from pathlib import Path
from typing import Any

import httpx
import pytest

pytest.importorskip("sqlalchemy")

from acb_auth import UserContext, UserRole, build_access, console_resolve, get_current_user
from acb_common import tenant_redis
from acb_common._log import bind_run_context
from acb_common.settings import Settings, get_settings
from fastapi import FastAPI
from fastapi.testclient import TestClient
from gateway.routes import chat_transcribe as ct

_ROOT = Path(__file__).resolve().parents[2]

ORG = "11111111-1111-1111-1111-111111111111"
OTHER_ORG = "22222222-2222-2222-2222-222222222222"
ME = "ana@acme.test"
CONSOLE_URL = "https://console.metorite.test"
ORG_KEY = "cc_live_abcd_secretsecretsecret"


def wav_clip(seconds: float, *, rate: int = 16000, channels: int = 1) -> bytes:
    """A silent PCM WAV of the given length."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * channels * int(rate * seconds))
    return buf.getvalue()


# ── The fakes ───────────────────────────────────────────────────────────────


class FakeConsole:
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.status = 200
        self.body: Any = {"text": "kal ki meeting 3 baje hai"}
        self.error: Exception | None = None

    def _handle(self, request: httpx.Request) -> httpx.Response:
        request.read()
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        return httpx.Response(self.status, json=self.body)

    def client(self, timeout: Any = None):
        return httpx.AsyncClient(transport=httpx.MockTransport(self._handle), timeout=5.0)


class FakeRedis:
    """``incr``, ``expire`` and ``ttl`` of the tenant wrapper, by key string."""

    def __init__(self) -> None:
        self.counts: dict[str, int] = {}
        self.ttls: dict[str, int] = {}
        self.down = False

    def _k(self, k: Any) -> str:
        assert isinstance(k, tenant_redis.TenantKey), "a raw key string"
        return k.value

    async def incr(self, k: Any) -> int:
        if self.down:
            raise ConnectionError("redis is down")
        key = self._k(k)
        self.counts[key] = self.counts.get(key, 0) + 1
        return self.counts[key]

    async def expire(self, k: Any, seconds: int) -> bool:
        self.ttls[self._k(k)] = seconds
        return True

    async def ttl(self, k: Any) -> int:
        return self.ttls.get(self._k(k), -1)


@pytest.fixture
def box(monkeypatch):
    """A wired box with the flag on for ORG: the Console, Redis and settings."""
    console = FakeConsole()
    redis = FakeRedis()
    monkeypatch.setenv("CUSTOMER_CONSOLE_URL", CONSOLE_URL)
    monkeypatch.setenv("CUSTOMER_CONSOLE_ORG_KEY", ORG_KEY)
    monkeypatch.setenv("ROUTER_SERVING_ENABLED", "true")
    monkeypatch.setenv("CHAT_VOICE_ORGS", ORG)
    get_settings.cache_clear()
    monkeypatch.setattr(console_resolve, "_new_http_client", console.client)
    monkeypatch.setattr(tenant_redis, "get_tenant_redis", lambda **_: redis)
    try:
        yield console, redis
    finally:
        get_settings.cache_clear()


def _user(email: str = ME, org: str = ORG) -> UserContext:
    return UserContext(email=email, role=UserRole.EMPLOYEE, organization_id=org,
                       access=build_access(["feature:chat"], roles=["member"]))


def _client(user: UserContext | None = None, *, proven: bool = True) -> TestClient:
    """The route behind a session of ``user``.

    An ``async`` override binds the run context as ``get_current_user`` does
    for a browser request (branch 1a), so the endpoint sees it.
    """
    who = user or _user()

    async def _session() -> UserContext:
        if proven:
            bind_run_context(user=who.email, member_verified=True)
        return who

    app = FastAPI()
    app.include_router(ct.router)
    app.dependency_overrides[get_current_user] = _session
    return TestClient(app)


def _post(client: TestClient, audio: bytes | None = None, *, name: str = "clip.wav",
          mime: str = "audio/wav", headers: dict[str, str] | None = None) -> httpx.Response:
    files = {"file": (name, wav_clip(3) if audio is None else audio, mime)}
    return client.post("/chat/transcribe", files=files, headers=headers or {})


def _refusal(res: httpx.Response) -> tuple[int, str, str]:
    detail = res.json()["detail"]
    return res.status_code, detail["code"], detail["message"]


# ── The flag ────────────────────────────────────────────────────────────────


def test_the_flag_is_off_by_default_and_reloads() -> None:
    """Mutation: a default of ``*`` -> red. A name with no ``_orgs`` -> red."""
    from acb_common.env_guard import is_platform_env
    from gateway.flag_reload import reloadable

    assert Settings.model_fields["chat_voice_orgs"].default == ""
    assert reloadable("CHAT_VOICE_ORGS")
    assert is_platform_env("CHAT_VOICE_ORGS")


@pytest.mark.parametrize(("value", "org", "on"), [
    ("", ORG, False),
    ("*", ORG, True),
    ("*", "", False),
    (ORG, ORG.upper(), True),
    (f"{OTHER_ORG}, {ORG}", ORG, True),
    (OTHER_ORG, ORG, False),
])
def test_voice_allowed_reads_the_org_list(monkeypatch, value, org, on) -> None:
    from acb_common.chat_voice_flag import voice_allowed

    monkeypatch.setattr(get_settings(), "chat_voice_orgs", value)
    assert voice_allowed(org) is on


def test_the_flag_off_hides_the_mic_and_refuses_the_clip(box, monkeypatch) -> None:
    """Off for the org: the state read and the clip both answer 404.

    Mutation: drop the flag check -> the GET answers 200 -> red.
    """
    console, redis = box
    monkeypatch.setenv("CHAT_VOICE_ORGS", OTHER_ORG)
    get_settings.cache_clear()
    client = _client()
    assert client.get("/chat/transcribe").status_code == 404
    assert _post(client).status_code == 404
    assert console.requests == []
    assert redis.counts == {}


def test_the_flag_on_shows_the_mic(box) -> None:
    res = _client().get("/chat/transcribe")
    assert res.status_code == 200
    assert res.json() == {"ready": True, "message": None, "max_seconds": 120}


def test_the_router_needs_the_chat_feature() -> None:
    deps = [d.dependency.__qualname__ for d in ct.router.dependencies]
    assert any(q.startswith("require_feature_router") for q in deps)


# ── Routing off ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize("env", [
    {"ROUTER_SERVING_ENABLED": "false"},
    {"CUSTOMER_CONSOLE_URL": ""},
])
def test_routing_off_refuses_and_calls_nothing(box, monkeypatch, env) -> None:
    """A call off the Console would not bill, so the route makes none.

    Mutation: drop the ``routing_is_on`` check -> a request reaches the
    Console (or the transcriber raises) -> red.
    """
    console, redis = box
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    get_settings.cache_clear()
    client = _client()

    state = client.get("/chat/transcribe")
    assert state.status_code == 200
    assert state.json() == {"ready": False, "message": ct.NOT_HERE, "max_seconds": 120}

    res = _post(client)
    assert _refusal(res) == (503, "not_here", "Voice input is not available here.")
    assert console.requests == []
    assert redis.counts == {}


# ── The happy path ──────────────────────────────────────────────────────────


def test_a_clip_comes_back_as_text(box) -> None:
    console, redis = box
    res = _post(_client())
    assert res.status_code == 200
    assert res.json() == {"text": "kal ki meeting 3 baje hai"}

    assert len(console.requests) == 1
    req = console.requests[0]
    assert str(req.url) == f"{CONSOLE_URL}/v1/audio/transcriptions"
    raw = req.content.decode("latin-1")
    assert 'name="file"; filename="clip.wav"' in raw
    assert 'name="model"\r\n\r\ntier-stt\r\n' in raw
    assert 'name="stream"' not in raw
    assert req.headers["x-cc-member"] == ME
    assert req.headers["x-cc-member-proven"] == "1"
    # The counter is the session's org and member, in the tenant wrapper.
    assert redis.counts == {f"cc:{ORG}:chat_voice:clips:{ME}": 1}


def test_the_server_names_the_file_sent_upstream(box) -> None:
    """The member's file name never reaches the vendor, only its extension."""
    console, _ = box
    res = _post(_client(), name="../../secret plans.WAV")
    assert res.status_code == 200
    raw = console.requests[0].content.decode("latin-1")
    assert 'filename="clip.wav"' in raw
    assert "secret plans" not in raw


@pytest.mark.parametrize(("name", "mime"), [
    ("clip.webm", "audio/webm;codecs=opus"),
    ("clip.ogg", "audio/ogg"),
    ("clip.mp4", "audio/mp4"),
    ("clip.m4a", "audio/x-m4a"),
])
def test_the_other_audio_kinds_pass(box, name, mime) -> None:
    console, _ = box
    res = _post(_client(), b"\x1a\x45\xdf\xa3 not decoded here", name=name, mime=mime)
    assert res.status_code == 200
    ext = name.rsplit(".", 1)[1]
    assert f'filename="clip.{ext}"' in console.requests[0].content.decode("latin-1")


def test_an_unproven_member_travels_without_the_proof(box) -> None:
    """Only the session's own verified binding proves the member (H-73)."""
    console, _ = box
    res = _post(_client(proven=False))
    assert res.status_code == 200
    assert console.requests[0].headers["x-cc-member"] == ME
    assert "x-cc-member-proven" not in console.requests[0].headers


def test_a_shared_box_refuses_an_unproven_member_before_any_call(box, monkeypatch) -> None:
    """On a deployment key the member picks who pays, so it must be proven."""
    console, _ = box
    monkeypatch.setenv("CUSTOMER_CONSOLE_ORG_KEY", "")
    monkeypatch.setenv("CUSTOMER_CONSOLE_DEPLOYMENT_KEY", "cc_depl_wxyz_anothersecretvalue")
    monkeypatch.setenv("CUSTOMER_CONSOLE_ROUTER_USES_DEPLOYMENT_KEY", "true")
    get_settings.cache_clear()
    res = _post(_client(proven=False))
    assert _refusal(res) == (502, "no_answer", ct.NO_ANSWER)
    assert console.requests == []

    ok = _post(_client(proven=True))
    assert ok.status_code == 200
    assert console.requests[0].headers["x-cc-member-proven"] == "1"


# ── The caps ────────────────────────────────────────────────────────────────


def test_a_body_over_the_cap_is_refused_before_the_console(box) -> None:
    """10 MB of clip. Mutation: raise MAX_CLIP_BYTES past the body -> red."""
    console, redis = box
    big = b"\x00" * (ct.MAX_CLIP_BYTES + 1)
    res = _post(_client(), big, name="clip.webm", mime="audio/webm")
    assert _refusal(res) == (413, "too_long", "The clip is too long.")
    assert console.requests == []
    assert redis.counts == {}


def test_a_declared_length_over_the_cap_is_refused_unread(box) -> None:
    console, _ = box
    res = _post(_client(), headers={"content-length": str(ct.MAX_BODY_BYTES + 1)})
    assert res.status_code == 413
    assert console.requests == []


def test_a_wav_over_two_minutes_is_refused(box) -> None:
    """Mutation: drop the WAV length check -> red."""
    console, _ = box
    res = _post(_client(), wav_clip(ct.MAX_SECONDS + 6))
    assert _refusal(res) == (413, "too_long", ct.TOO_LONG)
    assert console.requests == []


def test_a_wav_of_two_minutes_passes(box) -> None:
    res = _post(_client(), wav_clip(ct.MAX_SECONDS))
    assert res.status_code == 200


@pytest.mark.parametrize(("audio", "name", "mime"), [
    (None, "clip.wav", "text/plain"),        # not audio
    (None, "clip.txt", "audio/wav"),         # the extension is not audio
    (None, "clip", "audio/wav"),             # no extension
    (None, "clip.wav", "audio/webm"),        # the two disagree
    (b"not a riff header at all", "clip.wav", "audio/wav"),  # not a WAV
    (None, "clip.mp3", "audio/mpeg"),        # a kind the route does not take
])
def test_a_clip_of_the_wrong_kind_is_refused(box, audio, name, mime) -> None:
    console, redis = box
    res = _post(_client(), audio, name=name, mime=mime)
    assert _refusal(res) == (415, "wrong_kind", ct.WRONG_KIND)
    assert console.requests == []
    assert redis.counts == {}


def test_a_body_with_no_clip_is_refused(box) -> None:
    console, _ = box
    client = _client()
    assert _refusal(client.post("/chat/transcribe", data={"model": "x"})) == (
        400, "no_clip", ct.NO_CLIP)
    assert _refusal(client.post("/chat/transcribe", content=b"raw", headers={
        "content-type": "audio/wav"})) == (400, "no_clip", ct.NO_CLIP)
    assert _refusal(_post(client, b"")) == (400, "no_clip", ct.NO_CLIP)
    assert console.requests == []


def test_thirty_clips_an_hour_then_a_refusal(box) -> None:
    """The 31st clip in the window is refused, and the Console sees 30.

    Mutation: a cap of 31 -> red. A counter with no expiry -> red.
    """
    console, redis = box
    client = _client()
    for _ in range(ct.CLIPS_PER_HOUR):
        assert _post(client).status_code == 200
    key = f"cc:{ORG}:chat_voice:clips:{ME}"
    assert redis.ttls[key] == 3600

    res = _post(client)
    status, code, message = _refusal(res)
    assert (status, code) == (429, "cap")
    assert message == (
        "You recorded 30 clips in the last hour, which is the limit. "
        "Try again in 60 minutes."
    )
    assert len(console.requests) == ct.CLIPS_PER_HOUR


def test_the_cap_is_per_member(box) -> None:
    _, redis = box
    redis.counts[f"cc:{ORG}:chat_voice:clips:{ME}"] = ct.CLIPS_PER_HOUR
    redis.ttls[f"cc:{ORG}:chat_voice:clips:{ME}"] = 90
    assert _refusal(_post(_client()))[0] == 429
    assert _post(_client(_user("ben@acme.test"))).status_code == 200


def test_a_counter_that_lost_its_expiry_gets_one_again(box) -> None:
    _, redis = box
    key = f"cc:{ORG}:chat_voice:clips:{ME}"
    redis.counts[key] = ct.CLIPS_PER_HOUR
    res = _post(_client())
    assert res.status_code == 429
    assert redis.ttls[key] == ct.CAP_WINDOW_S


def test_a_store_that_does_not_answer_refuses_the_clip(box) -> None:
    """The cap guards spend, so it fails closed."""
    console, redis = box
    redis.down = True
    assert _refusal(_post(_client())) == (502, "no_answer", ct.NO_ANSWER)
    assert console.requests == []


# ── The status map (Do 8) ───────────────────────────────────────────────────


@pytest.mark.parametrize(("status", "body", "want"), [
    (400, {"detail": "no binding for tier 'tier-stt' on task 'transcribe'; name a tier, not a model"},
     (503, "no_model", "No speech model is set up yet.")),
    (503, {"detail": "no provider credential configured for 'elevenlabs'"},
     (503, "no_model", "No speech model is set up yet.")),
    (503, {"detail": "no capability declares how to serve 'groq/whisper' for task 'transcribe'"},
     (503, "no_model", "No speech model is set up yet.")),
    (402, {"detail": {"reason": "insufficient_credits"}},
     (402, "no_credits", "Your organization is out of AI credits. An admin can add credits in "
                         "Settings, Billing.")),
    (413, {"detail": "audio above 26214400 bytes is refused"},
     (413, "too_long", "The clip is too long.")),
    (502, {"detail": "upstream provider error"},
     (502, "no_answer", "The speech service did not answer. Try again.")),
    (503, {}, (502, "no_answer", ct.NO_ANSWER)),
    (500, {}, (502, "no_answer", ct.NO_ANSWER)),
    (400, {"detail": "upstream provider error"}, (502, "no_answer", ct.NO_ANSWER)),
    (403, {"detail": {"reason": "run_ceiling_exceeded"}}, (502, "no_answer", ct.NO_ANSWER)),
    (429, {"detail": "upstream provider error"}, (502, "no_answer", ct.NO_ANSWER)),
])
def test_each_console_answer_maps_to_one_sentence(box, status, body, want) -> None:
    """Mutation: route a 503 through the chat client's outage rule -> the
    "no provider credential" setup fault reads as "did not answer" -> red."""
    console, _ = box
    console.status, console.body = status, body
    assert _refusal(_post(_client())) == want
    assert len(console.requests) == 1


@pytest.mark.parametrize("error", [
    httpx.ReadTimeout("slow"), httpx.ConnectError("refused"),
])
def test_no_answer_from_the_console_reads_as_try_again(box, error) -> None:
    console, _ = box
    console.error = error
    assert _refusal(_post(_client())) == (502, "no_answer", ct.NO_ANSWER)
    assert len(console.requests) == 1


def test_an_empty_transcript_is_an_empty_text(box) -> None:
    console, _ = box
    console.body = {"text": None}
    res = _post(_client())
    assert res.status_code == 200
    assert res.json() == {"text": ""}


# ── No file written ─────────────────────────────────────────────────────────


def test_a_large_clip_never_rolls_over_to_disk(box, monkeypatch) -> None:
    """Starlette spools a part above 1 MB to a temporary file. This route must
    not. A 9 MB clip passes with ``rollover`` made to fail.

    Mutation: take the clip as an ``UploadFile`` parameter, or drop the spool
    size of ``_InMemoryParser`` -> the rollover raises -> red.
    """
    import tempfile

    def _no_disk(self) -> None:
        raise AssertionError("a spooled file rolled over to disk")

    monkeypatch.setattr(tempfile.SpooledTemporaryFile, "rollover", _no_disk)
    clip = wav_clip(48, rate=48000, channels=2)  # about 9.2 MB, under 120 s
    assert 1024 * 1024 < len(clip) < ct.MAX_CLIP_BYTES
    console, _ = box
    res = _post(_client(), clip)
    assert res.status_code == 200, res.text
    assert clip.decode("latin-1") in console.requests[0].content.decode("latin-1")


def test_the_route_source_writes_no_file() -> None:
    """No ``open``, no temporary file and no ``UploadFile`` parameter."""
    src = (_ROOT / "apps/services/gateway/gateway/routes/chat_transcribe.py").read_text(
        encoding="utf-8")
    code = re.sub(r'"""[\s\S]*?"""', "", src)
    code = re.sub(r"#.*", "", code)
    # ``wave.open`` reads a header from memory. Only the builtin opens a file.
    assert not re.search(r"(?<![\w.])open\(", code)
    for banned in ("NamedTemporaryFile", "mkstemp", "write_bytes", "write_text",
                   "File(", "= File", ": UploadFile"):
        assert banned not in code, banned
    assert "spool_max_size = MAX_BODY_BYTES + 1" in code
