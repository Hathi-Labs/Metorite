"""The Console client of the chat's voice input (``projects_ai_chat.md`` §31, VOX-1).

``transcribe_on_console`` sends one clip to the Console's metered transcribe
door (``customer_console.md`` §6A.10a). These cases drive it through a real
``httpx.MockTransport``, so the multipart body that the code builds is the
body under test.

What must hold:

* The request: ``POST /v1/audio/transcriptions``, the multipart field
  ``file`` with the extension in its name, the form field ``model=tier-stt``,
  and NO ``stream`` field.
* The auth is the chat client's: the same wiring gate, the same credential
  pick, the same local refusal of an unproven member on a deployment key, and
  the same ``X-CC-*`` headers. No ``X-CC-Run``: a gateway route is not a run.
* Every HTTP answer comes back as ``(status, body)``, a 5xx included, so the
  caller can tell a setup fault from an outage. Only "no answer" raises.
* One request per call: no retry, because the Console charges per minute.
* The timeout is longer than the chat client's 120 s.
"""
from __future__ import annotations

import inspect
from typing import Any

import httpx
import pytest
from acb_auth import console_resolve
from acb_common.settings import get_settings

CONSOLE_URL = "https://console.metorite.test"
ORG_KEY = "cc_live_abcd_secretsecretsecret"
DEPLOYMENT_KEY = "cc_depl_wxyz_anothersecretvalue"
MEMBER = "ana@acme.test"
AUDIO = b"RIFF\x24\x00\x00\x00WAVEfmt " + b"\x00" * 64


class FakeConsole:
    """Records each request and answers with what the test set."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.timeouts: list[Any] = []
        self._status = 200
        self._body: Any = {"text": "hello there"}
        self._raise: Exception | None = None

    def answers(self, status: int, body: Any = None) -> None:
        self._status = status
        self._body = {} if body is None else body

    def explodes(self, exc: Exception) -> None:
        self._raise = exc

    def _handle(self, request: httpx.Request) -> httpx.Response:
        request.read()
        self.requests.append(request)
        if self._raise is not None:
            raise self._raise
        if isinstance(self._body, (dict, list)):
            return httpx.Response(self._status, json=self._body)
        return httpx.Response(self._status, text=str(self._body))

    def client(self, timeout: Any = None):
        self.timeouts.append(timeout)
        return httpx.AsyncClient(transport=httpx.MockTransport(self._handle), timeout=5.0)


def _wire(monkeypatch, *, org_key: str = ORG_KEY, deployment: bool = False) -> FakeConsole:
    fake = FakeConsole()
    monkeypatch.setenv("CUSTOMER_CONSOLE_URL", CONSOLE_URL)
    monkeypatch.setenv("CUSTOMER_CONSOLE_ORG_KEY", org_key)
    monkeypatch.setenv("CUSTOMER_CONSOLE_DEPLOYMENT_KEY", DEPLOYMENT_KEY)
    monkeypatch.setenv(
        "CUSTOMER_CONSOLE_ROUTER_USES_DEPLOYMENT_KEY", "true" if deployment else "false"
    )
    get_settings.cache_clear()
    monkeypatch.setattr(console_resolve, "_new_http_client", fake.client)
    return fake


@pytest.fixture
def console(monkeypatch):
    """A box wired for the Router with the ORG key."""
    fake = _wire(monkeypatch)
    try:
        yield fake
    finally:
        get_settings.cache_clear()


@pytest.fixture
def shared_box(monkeypatch):
    """A box wired with the per-box DEPLOYMENT key only (H-152)."""
    fake = _wire(monkeypatch, org_key="", deployment=True)
    try:
        yield fake
    finally:
        get_settings.cache_clear()


async def _call(**kw: Any) -> tuple[int, dict[str, Any]]:
    args: dict[str, Any] = {
        "filename": "clip.wav",
        "content_type": "audio/wav",
        "member": MEMBER,
        "member_proven": True,
        "agent": "chat.voice",
        "module_slug": "chat",
    }
    args.update(kw)
    return await console_resolve.transcribe_on_console(AUDIO, **args)


def _parts(request: httpx.Request) -> str:
    return request.content.decode("latin-1")


# ── The request ─────────────────────────────────────────────────────────────


async def test_the_request_is_the_door_contract(console):
    """``file`` with its extension, ``model=tier-stt``, and no ``stream``.

    Mutation: drop the extension from the name -> red. Send ``stream`` -> red.
    Name a model id in place of the tier -> red.
    """
    status, body = await _call()
    assert (status, body) == (200, {"text": "hello there"})

    assert len(console.requests) == 1
    req = console.requests[0]
    assert req.method == "POST"
    assert str(req.url) == f"{CONSOLE_URL}/v1/audio/transcriptions"
    assert req.headers["content-type"].startswith("multipart/form-data; boundary=")

    raw = _parts(req)
    assert 'name="file"; filename="clip.wav"' in raw
    assert "Content-Type: audio/wav" in raw
    assert AUDIO.decode("latin-1") in raw
    assert 'name="model"\r\n\r\ntier-stt\r\n' in raw
    assert 'name="stream"' not in raw
    assert console_resolve.TRANSCRIBE_TIER == "tier-stt"


async def test_the_member_travels_as_the_chat_client_sends_it(console):
    """The org key, the member, the proof, and no run id.

    Mutation: send ``X-CC-Run`` -> red. Send the proof for an unproven member
    -> red (the next case).
    """
    await _call()
    headers = console.requests[0].headers
    assert headers["authorization"] == f"Bearer {ORG_KEY}"
    assert headers["x-cc-member"] == MEMBER
    assert headers["x-cc-member-proven"] == "1"
    assert headers["x-cc-agent"] == "chat.voice"
    assert headers["x-cc-module"] == "chat"
    assert "x-cc-run" not in headers


async def test_an_unproven_member_travels_without_the_proof(console):
    await _call(member_proven=False)
    headers = console.requests[0].headers
    assert headers["x-cc-member"] == MEMBER
    assert "x-cc-member-proven" not in headers


async def test_a_shared_box_presents_the_deployment_key_for_a_proven_member(shared_box):
    await _call()
    headers = shared_box.requests[0].headers
    assert headers["authorization"] == f"Bearer {DEPLOYMENT_KEY}"
    assert headers["x-cc-member-proven"] == "1"


@pytest.mark.parametrize("member", [None, ""])
async def test_a_shared_box_refuses_a_call_with_no_member_before_it_sends(shared_box, member):
    with pytest.raises(console_resolve.ConsoleRouterUnavailable):
        await _call(member=member)
    assert shared_box.requests == []


async def test_a_shared_box_refuses_an_unproven_member_before_it_sends(shared_box):
    """An unsigned member cannot choose which organization pays (2026-09-28)."""
    with pytest.raises(console_resolve.ConsoleRouterUnavailable):
        await _call(member_proven=False)
    assert shared_box.requests == []


async def test_an_unwired_box_sends_nothing(monkeypatch):
    fake = _wire(monkeypatch)
    monkeypatch.setenv("CUSTOMER_CONSOLE_URL", "")
    get_settings.cache_clear()
    try:
        with pytest.raises(console_resolve.ConsoleRouterUnavailable):
            await _call()
        assert fake.requests == []
    finally:
        get_settings.cache_clear()


# ── The answers ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(("status", "body"), [
    (400, {"detail": "no binding for tier 'tier-stt' on task 'transcribe'; name a tier, not a model"}),
    (402, {"detail": {"reason": "insufficient_credits"}}),
    (403, {"detail": {"reason": "run_ceiling_exceeded"}}),
    (413, {"detail": "audio above 26214400 bytes is refused"}),
    (502, {"detail": "upstream provider error"}),
    (503, {"detail": "no provider credential configured for 'groq'"}),
    (500, {}),
])
async def test_every_answer_comes_back_as_a_verdict(console, status, body):
    """A 5xx is an ANSWER here. The chat client would raise for it.

    Mutation: copy the chat client's ``status >= 500`` outage rule -> red.
    """
    console.answers(status, body)
    got = await _call()
    assert got == (status, body)
    assert len(console.requests) == 1


async def test_a_body_that_is_not_json_reads_as_empty(console):
    console.answers(502, "<html>bad gateway</html>")
    assert await _call() == (502, {})


async def test_no_answer_raises_and_is_not_retried(console):
    """A timeout or a refused connection raises, after ONE attempt.

    Mutation: a retry loop -> two requests -> red.
    """
    console.explodes(httpx.ReadTimeout("slow"))
    with pytest.raises(console_resolve.ConsoleRouterUnavailable):
        await _call()
    assert len(console.requests) == 1


async def test_a_failed_answer_is_not_retried(console):
    console.answers(502, {"detail": "upstream provider error"})
    await _call()
    assert len(console.requests) == 1


async def test_the_timeout_is_longer_than_the_chat_client(console):
    """A 120 s clip and a failover can pass 120 s (Do 7)."""
    await _call()
    assert console.timeouts == [console_resolve._TRANSCRIBE_TIMEOUT_SECONDS]
    assert console_resolve._TRANSCRIBE_TIMEOUT_SECONDS > console_resolve._ROUTER_TIMEOUT_SECONDS


def test_the_client_reuses_the_one_credential_path():
    """No second credential picker and no second client factory (Do 7).

    The source of the function names each shared helper, and names neither
    settings key of a credential itself.
    """
    src = inspect.getsource(console_resolve.transcribe_on_console)
    for name in (
        "router_is_wired()",
        "router_credential()",
        "_require_proven_member_on_deployment(",
        "_attribution_headers(",
        "_new_http_client(",
    ):
        assert name in src, name
    assert "customer_console_org_key" not in src
    assert "customer_console_deployment_key" not in src
    assert "for attempt" not in src and "while " not in src
