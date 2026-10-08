"""The Router ride-through: retry a REFUSED connection, never anything else.

Owner report, 2026-10-08: ``acb-gateway`` restarted eight times in 36
minutes, and a chat turn failed with ``APIConnectionError`` because nothing
listened on the Router's port. ``acb_llm.ride_through`` holds the rule.

Each test names the mutation it was run against.
"""
from __future__ import annotations

import asyncio

import httpx
import pytest
from acb_llm.ride_through import RIDE_THROUGH_DELAYS, RideThroughTransport

URL = "http://127.0.0.1:8080/v1/chat/completions"


class _Script(httpx.AsyncBaseTransport):
    """Answers from a script: an exception to raise, or a response."""

    def __init__(self, steps):
        self.steps = list(steps)
        self.calls = 0

    async def handle_async_request(self, request):
        self.calls += 1
        step = self.steps.pop(0)
        if isinstance(step, BaseException):
            raise step
        return step


class _Clock:
    def __init__(self):
        self.now = 0.0
        self.slept: list[float] = []

    def __call__(self):
        return self.now

    async def sleep(self, s):
        self.slept.append(s)
        self.now += s


def _ok(body: bytes = b"data: {}\n\n") -> httpx.Response:
    return httpx.Response(200, stream=httpx.ByteStream(body))


def _client(inner, clock):
    return httpx.AsyncClient(
        transport=RideThroughTransport(inner, sleep=clock.sleep, clock=clock),
    )


def test_refused_twice_then_ok_completes():
    """Mutation: catch nothing (re-raise at once), and this fails."""
    clock = _Clock()
    inner = _Script([httpx.ConnectError("refused"), httpx.ConnectError("refused"), _ok()])

    async def go():
        async with _client(inner, clock) as c:
            return await c.post(URL, json={"model": "tier-balanced"})

    res = asyncio.run(go())
    assert res.status_code == 200
    assert inner.calls == 3
    assert clock.slept == list(RIDE_THROUGH_DELAYS[:2])


def test_it_gives_up_inside_the_deadline():
    """Four tries, three waits, about 14 s. Mutation: loop forever, and the
    script runs out (IndexError) instead of raising ConnectError."""
    clock = _Clock()
    inner = _Script([httpx.ConnectError("refused")] * 4)

    async def go():
        async with _client(inner, clock) as c:
            await c.post(URL, json={})

    with pytest.raises(httpx.ConnectError):
        asyncio.run(go())
    assert inner.calls == 4
    assert sum(clock.slept) <= 15.0


def test_a_failure_after_the_first_token_is_never_retried():
    """The response started, a token streamed, then the connection dropped.
    The transport has returned by then, so no catch in it sees the body's
    error. This test holds that property: the failure reaches the caller
    once, and nothing sends the request again. Mutation: make the transport
    read the whole body before it returns and retry a ``ReadError``, and this
    sees two calls. The ``TransportError`` mutation is fenced by
    ``test_only_a_refused_connection_is_retried``."""
    clock = _Clock()

    class _Drops(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'data: {"choices":[{"delta":{"content":"Hel"}}]}\n\n'
            raise httpx.ReadError("connection reset")

    inner = _Script([httpx.Response(200, stream=_Drops()), _ok()])

    async def go():
        async with _client(inner, clock) as c, c.stream("POST", URL, json={}) as res:
            async for _ in res.aiter_bytes():
                pass

    with pytest.raises(httpx.ReadError):
        asyncio.run(go())
    assert inner.calls == 1
    assert clock.slept == []


@pytest.mark.parametrize(
    "exc",
    [httpx.ConnectTimeout("slow"), httpx.RemoteProtocolError("Server disconnected")],
)
def test_only_a_refused_connection_is_retried(exc):
    """A timeout or a dropped request may have reached the Router, so it
    is never sent again here. Mutation: catch ``httpx.TransportError``."""
    clock = _Clock()
    inner = _Script([exc, _ok()])

    async def go():
        async with _client(inner, clock) as c:
            await c.post(URL, json={})

    with pytest.raises(type(exc)):
        asyncio.run(go())
    assert inner.calls == 1


def test_a_stopping_process_never_rides_through(monkeypatch):
    """Review round 1: on the box the agents call their OWN process, so a
    wait during its restart only holds the old process up. Mutation: drop
    ``stopping()`` from the give-up test, and this sleeps."""
    from acb_llm import ride_through

    monkeypatch.setattr(ride_through, "_STOPPING", ride_through.threading.Event())
    ride_through.mark_stopping()
    clock = _Clock()
    inner = _Script([httpx.ConnectError("refused"), _ok()])

    async def go():
        async with _client(inner, clock) as c:
            await c.post(URL, json={})

    with pytest.raises(httpx.ConnectError):
        asyncio.run(go())
    assert inner.calls == 1
    assert clock.slept == []


def test_the_stop_hook_chains_the_servers_handler(monkeypatch):
    """The server's own handler still runs, after the flag is set.
    Mutation: drop the ``_prev(signum, frame)`` call, and this fails."""
    import signal

    from acb_llm import ride_through

    monkeypatch.setattr(ride_through, "_STOPPING", ride_through.threading.Event())
    seen: list[int] = []
    prev = signal.getsignal(signal.SIGTERM)
    try:
        signal.signal(signal.SIGTERM, lambda s, f: seen.append(s))
        ride_through.install_stop_hook()
        signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)
    finally:
        signal.signal(signal.SIGTERM, prev)
    assert ride_through.stopping()
    assert seen == [signal.SIGTERM]


def test_the_sdks_own_retries_pass_straight_through():
    """The SDK stamps ``x-stainless-retry-count``. Only try 0 rides through,
    so the SDK's two tries never multiply the wait. Mutation: drop the header
    check, and this sees four calls."""
    clock = _Clock()
    inner = _Script([httpx.ConnectError("refused"), _ok()])

    async def go():
        async with _client(inner, clock) as c:
            await c.post(URL, json={}, headers={"x-stainless-retry-count": "1"})

    with pytest.raises(httpx.ConnectError):
        asyncio.run(go())
    assert inner.calls == 1


def test_each_retry_is_logged(monkeypatch):
    from acb_llm import ride_through

    lines: list[dict] = []

    class _Log:
        def warning(self, event, **kw):
            lines.append({"event": event, **kw})

    monkeypatch.setattr(ride_through, "_log", _Log())
    clock = _Clock()
    inner = _Script([httpx.ConnectError("refused"), _ok()])

    async def go():
        async with _client(inner, clock) as c:
            await c.post(URL, json={})

    asyncio.run(go())
    assert [ln["event"] for ln in lines] == ["router_connect_retry"]
    assert lines[0]["attempt"] == 1 and lines[0]["port"] == 8080


def test_every_agent_client_rides_through():
    """``attributed_openai`` is the one constructor the agents use. The wrap
    sits on httpx's private ``_transport``. Mutation: remove the wrap line in
    ``attributed_openai``, and this fails."""
    from acb_llm.attribution import attributed_openai

    client = attributed_openai(base_url="http://127.0.0.1:8080/v1", api_key="k")
    assert isinstance(client._client._transport, RideThroughTransport)


def test_the_real_sdk_completes_after_two_refusals(monkeypatch):
    """End to end through the OpenAI SDK, with only the socket replaced.
    The SDK's own retry runs too, and the count proves they compose: the
    ride-through absorbs both refusals, so the SDK sees one success."""
    import openai
    from acb_llm import ride_through
    from acb_llm.attribution import attributed_openai

    clock = _Clock()
    monkeypatch.setattr(ride_through.asyncio, "sleep", clock.sleep)
    body = {
        "id": "c1", "object": "chat.completion", "created": 0, "model": "m",
        "choices": [{"index": 0, "finish_reason": "stop",
                     "message": {"role": "assistant", "content": "ok"}}],
    }
    inner = _Script([
        httpx.ConnectError("refused"),
        httpx.ConnectError("refused"),
        httpx.Response(200, json=body),
    ])
    client = attributed_openai(base_url="http://127.0.0.1:8080/v1", api_key="k")
    rt = client._client._transport
    rt._inner = inner
    rt._sleep = clock.sleep

    async def go():
        return await client.chat.completions.create(
            model="m", messages=[{"role": "user", "content": "hi"}],
        )

    out = asyncio.run(go())
    assert out.choices[0].message.content == "ok"
    assert inner.calls == 3
    assert isinstance(client, openai.AsyncOpenAI)
