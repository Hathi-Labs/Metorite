"""WS-31 CP-11 slice 5 — a STREAM is routed through the Console Router.

Spec: ``project-docs/specs/customer_console.md`` §6B · CP-4b · D57.7 · D32.7.
Closes **H-68**.

**What this finishes.** Slice 3 routed `/v1/chat/completions` to the Console
Router and refused to route a STREAM, because the Router answered 501 and CP-4b
was unbuilt. Every agent runtime streams, so with the flag on most traffic took
the local path and **nobody billed it**. CP-4b made the Console stream on
2026-08-27. This removes the refusal and carries the stream across.

**The shape that matters.** A streamed refusal has to become a STATUS CODE. The
status line is fixed the moment a body starts, and a 402 delivered inside an SSE
frame is one every client renders as content. So the gateway advances the
generator ONCE while a ``JSONResponse`` is still possible, and only then hands
the rest to Starlette.
"""
from __future__ import annotations

import ast
import json
import pathlib
from typing import Any

import httpx
import pytest
from acb_auth import console_resolve
from acb_auth.console_resolve import ConsoleRouterUnavailable, ConsoleRouterVerdict
from acb_common.settings import get_settings
from gateway.routes import v1_compat

CONSOLE_URL = "https://console.invalid"
ORG_KEY = "cc_live_streamfixture_notarealsecret"

PAYLOAD = {"model": "tier-balanced", "messages": [{"role": "user", "content": "hi"}]}

#: What the Console Router emits. CP-4b guarantees these are the provider's own
#: frames, so this suite's subject is whether the GATEWAY alters them.
FRAMES = [
    b'data: {"choices":[{"delta":{"content":"Six"}}]}\n\n',
    b'data: {"choices":[{"delta":{"content":"teen"}}]}\n\n',
    b'data: {"choices":[{"delta":{},"finish_reason":"stop"}],'
    b'"usage":{"prompt_tokens":12,"completion_tokens":3}}\n\n',
    b"data: [DONE]\n\n",
]


class FakeStreamingRouter:
    """A Console whose ``/v1/chat/completions`` streams, driven by MockTransport."""

    def __init__(self) -> None:
        self.status = 200
        self.frames: list[bytes] = list(FRAMES)
        self.error_body: dict[str, Any] = {}
        self.requests: list[httpx.Request] = []
        self.raise_on_connect: Exception | None = None

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.raise_on_connect is not None:
            raise self.raise_on_connect
        if self.status != 200:
            return httpx.Response(self.status, json=self.error_body)
        return httpx.Response(
            200,
            stream=httpx.ByteStream(b"".join(self.frames)),
            headers={"content-type": "text/event-stream"},
        )

    def client(self, timeout: Any = None):
        return httpx.AsyncClient(
            transport=httpx.MockTransport(self._handle), timeout=5.0
        )


@pytest.fixture
def router(monkeypatch):
    fake = FakeStreamingRouter()
    monkeypatch.setenv("CUSTOMER_CONSOLE_URL", CONSOLE_URL)
    monkeypatch.setenv("CUSTOMER_CONSOLE_ORG_KEY", ORG_KEY)
    get_settings.cache_clear()
    monkeypatch.setattr(console_resolve, "_new_http_client", fake.client)
    try:
        yield fake
    finally:
        get_settings.cache_clear()


async def _drain(agen) -> bytes:
    out = []
    async for chunk in agen:
        out.append(chunk)
    return b"".join(out)


# ── The client ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_the_bytes_reach_the_caller_unaltered(router):
    body = await _drain(console_resolve.stream_completion_on_console(PAYLOAD))
    assert body == b"".join(FRAMES)


@pytest.mark.asyncio
async def test_an_unwired_box_never_opens_a_connection(router, monkeypatch):
    monkeypatch.setenv("CUSTOMER_CONSOLE_ORG_KEY", "")
    get_settings.cache_clear()
    with pytest.raises(ConsoleRouterUnavailable):
        await _drain(console_resolve.stream_completion_on_console(PAYLOAD))
    assert router.requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [400, 402, 403, 404, 422, 501])
async def test_a_verdict_is_raised_before_the_first_byte(router, status):
    """400 a bare model id, 402 out of credits, 403 the breaker.

    ⚠️ **501 is here on purpose.** A Console too old to stream still answers it,
    and that is an ANSWER — "deploy CP-4b" — not an outage. Folding it into the
    unreachable branch would send somebody hunting a network fault.
    """
    router.status = status
    router.error_body = {"detail": {"reason": "insufficient_credits"}}

    with pytest.raises(ConsoleRouterVerdict) as exc:
        await _drain(console_resolve.stream_completion_on_console(PAYLOAD))

    assert exc.value.status == status
    assert exc.value.body == {"detail": {"reason": "insufficient_credits"}}


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [500, 502, 503, 401, 408, 429])
async def test_an_outage_is_not_a_verdict(router, status):
    router.status = status
    with pytest.raises(ConsoleRouterUnavailable):
        await _drain(console_resolve.stream_completion_on_console(PAYLOAD))


@pytest.mark.asyncio
async def test_a_transport_failure_becomes_unavailable(router):
    router.raise_on_connect = httpx.ConnectError("no route to host")
    with pytest.raises(ConsoleRouterUnavailable):
        await _drain(console_resolve.stream_completion_on_console(PAYLOAD))


@pytest.mark.asyncio
async def test_there_is_no_retry(router):
    """The Console meters and CHARGES on the way through.

    A retried stream that actually succeeded bills the customer twice for one
    answer. Worse on a stream than on a completion, because a stream can fail
    after most of its frames were delivered AND metered.
    """
    router.status = 500
    with pytest.raises(ConsoleRouterUnavailable):
        await _drain(console_resolve.stream_completion_on_console(PAYLOAD))
    assert len(router.requests) == 1


@pytest.mark.asyncio
async def test_the_org_key_and_attribution_headers_are_sent(router):
    await _drain(console_resolve.stream_completion_on_console(
        PAYLOAD, member="a@b.com", agent="planner",
        module_slug="projects", run_id="run-7"))

    sent = router.requests[-1].headers
    assert sent["authorization"] == f"Bearer {ORG_KEY}"
    assert sent["x-cc-member"] == "a@b.com"
    assert sent["x-cc-agent"] == "planner"
    assert sent["x-cc-module"] == "projects"
    assert sent["x-cc-run"] == "run-7"


@pytest.mark.asyncio
async def test_an_empty_attribution_header_is_omitted_not_sent_blank(router):
    # An empty member recorded as a member reads as an attribution nobody made.
    await _drain(console_resolve.stream_completion_on_console(
        PAYLOAD, member="", agent=None))
    sent = router.requests[-1].headers
    assert "x-cc-member" not in sent
    assert "x-cc-agent" not in sent


@pytest.mark.asyncio
async def test_the_model_string_is_forwarded_untouched(router):
    """The Console resolves the tier. Resolving here would 400 every call."""
    await _drain(console_resolve.stream_completion_on_console(PAYLOAD))
    body = json.loads(router.requests[-1].content)
    assert body["model"] == "tier-balanced"


# ── The gateway hop ─────────────────────────────────────────────────────────

def _configure(monkeypatch, *, flag: str = "1") -> None:
    monkeypatch.setenv("ROUTER_SERVING_ENABLED", flag)
    monkeypatch.setenv("CUSTOMER_CONSOLE_URL", CONSOLE_URL)
    monkeypatch.setenv("CUSTOMER_CONSOLE_ORG_KEY", ORG_KEY)
    get_settings.cache_clear()


class _Req:
    def __init__(self, headers: dict[str, str] | None = None) -> None:
        self.headers = headers or {}


@pytest.mark.asyncio
async def test_the_hop_streams_the_bytes_through(router, monkeypatch):
    _configure(monkeypatch)
    resp = await v1_compat._serve_via_router_stream(
        _Req(), {**PAYLOAD, "stream": True})

    assert resp.media_type == "text/event-stream"
    body = b"".join([c async for c in resp.body_iterator])
    assert body == b"".join(FRAMES)


@pytest.mark.asyncio
async def test_a_verdict_becomes_its_own_status_not_an_sse_frame(
        router, monkeypatch):
    """A refusal inside a frame is one every client renders as CONTENT."""
    _configure(monkeypatch)
    router.status = 402
    router.error_body = {"detail": {"reason": "insufficient_credits"}}

    resp = await v1_compat._serve_via_router_stream(
        _Req(), {**PAYLOAD, "stream": True})

    assert resp.status_code == 402
    assert b"insufficient_credits" in resp.body
    assert resp.media_type != "text/event-stream"


@pytest.mark.asyncio
async def test_an_outage_is_502_and_never_falls_back_locally(
        router, monkeypatch):
    """D57.7. A routed call that fails FAILS."""
    _configure(monkeypatch)
    router.status = 503

    resp = await v1_compat._serve_via_router_stream(
        _Req(), {**PAYLOAD, "stream": True})

    assert resp.status_code == 502
    assert b"ConsoleRouterUnavailable" in resp.body


@pytest.mark.asyncio
async def test_the_attribution_headers_cross_the_hop(router, monkeypatch):
    _configure(monkeypatch)
    req = _Req({"x-cc-member": "a@b.com", "x-cc-run": "run-9"})

    resp = await v1_compat._serve_via_router_stream(
        req, {**PAYLOAD, "stream": True})
    [c async for c in resp.body_iterator]

    sent = router.requests[-1].headers
    assert sent["x-cc-member"] == "a@b.com"
    assert sent["x-cc-run"] == "run-9"


@pytest.mark.asyncio
async def test_an_empty_stream_still_returns_a_stream(router, monkeypatch):
    _configure(monkeypatch)
    router.frames = []

    resp = await v1_compat._serve_via_router_stream(
        _Req(), {**PAYLOAD, "stream": True})

    assert resp.media_type == "text/event-stream"
    assert b"".join([c async for c in resp.body_iterator]) == b""


# ── D57.7, structurally ─────────────────────────────────────────────────────

def _identifiers_in(func_name: str) -> set[str]:
    """Every identifier a function actually REFERENCES.

    ⚠️ Built from the AST, not from the text, and that is the whole point. A
    grep over source matches the function's own DOCSTRING — and these
    docstrings name `_resolve_model` and `_ensure_keys_loaded` precisely while
    explaining why the hop must not call them. Three tests in this repo have
    now failed on their own prose, so this reads names and never comments.
    """
    src = pathlib.Path(v1_compat.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if (isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef)
                and node.name == func_name):
            names: set[str] = set()
            for sub in ast.walk(node):
                if isinstance(sub, ast.Name):
                    names.add(sub.id)
                elif isinstance(sub, ast.Attribute):
                    names.add(sub.attr)
                elif isinstance(sub, ast.alias):
                    names.add(sub.asname or sub.name)
            return names
    raise AssertionError(f"{func_name} not found")


def test_the_streaming_hop_has_no_local_fallback_arm():
    """The most tempting 'reliability' edit on this file, fenced.

    A fallback would serve a stream on tenant-local keys, at tenant-local
    models, UNMETERED — which is the exact hole this slice closes, reached from
    the other side.
    """
    names = _identifiers_in("_serve_via_router_stream")
    for forbidden in ("_ensure_keys_loaded", "acompletion", "_resolve_model"):
        assert forbidden not in names, (
            f"{forbidden} in the streaming hop is a local fallback"
        )


def test_neither_hop_resolves_the_tier():
    """The Console 400s a bare model id, so resolving here refuses every call."""
    for hop in ("_serve_via_router_stream", "_serve_via_router"):
        assert "_resolve_model" not in _identifiers_in(hop), hop


def test_the_streaming_hop_decides_its_status_before_it_returns():
    """It must reference BOTH verdict shapes, or a refusal becomes a frame."""
    names = _identifiers_in("_serve_via_router_stream")
    assert "ConsoleRouterVerdict" in names
    assert "ConsoleRouterUnavailable" in names
    assert "JSONResponse" in names
    assert "StreamingResponse" in names
