"""WS-17 EM-T17 PR-A, acceptance A1: the three bounds of a model stream.

``acb_llm.context._stream_text`` streams a model answer for the email
composer. Before PR-A it had no timeout at all, so a provider that sent no
chunk, or stopped half-way, held the composer open with no end
(``email_app_master_plan.md`` §17.1.3, cause S1).

The bounds (§17.3.7):

* ``STREAM_FIRST_CHUNK_S``: no chunk arrives. ``StreamFirstChunkTimeout``,
  reason ``model_slow``.
* ``STREAM_IDLE_S``: a gap between two chunks. ``StreamIdleTimeout``, reason
  ``model_stalled``. ANY chunk resets it, a ``reasoning_content`` chunk too,
  because ``tier-powerful`` is a reasoning model.
* ``STREAM_TOTAL_S``: the whole stream. ``StreamTotalTimeout``, reason
  ``model_timeout``.

Each one is a ``TimeoutError`` and logs ``acb_llm.stream_timeout`` with the
bound. The provider is a fake that hangs, so no test touches a network.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest
import structlog
from acb_llm import client as llm_client
from acb_llm import context as ctx

#: A hang guard, never a budget (#947). Only a fake that never answers meets it.
HANG_S = 30.0


class _Clock:
    """``asyncio`` as ``acb_llm.context`` reads it, on a clock only the fakes move.

    The bounds read ``loop.time()`` and wait with ``asyncio.wait_for``. Here
    the loop time is :attr:`now`. A fake spends time with :meth:`spend`, and
    a fake that never answers calls :meth:`hang`. ``wait_for`` then says
    TimeoutError when the answer came past its deadline, or never came.

    These fences slept real seconds before: a chunk every 0.06 s against an
    idle bound of 0.15 s, and an upper bound of 1.0 s on a bound of 0.15 s.
    On a stalled runner a sleep of 0.06 s can pass 0.15 s. Now the stream's
    timing is data, so a loaded runner cannot change which bound fires."""

    def __init__(self) -> None:
        self.now = 0.0
        self._hung = asyncio.Event()

    def __getattr__(self, name: str) -> Any:
        return getattr(asyncio, name)

    def get_running_loop(self) -> _Clock:
        return self

    def time(self) -> float:
        return self.now

    def spend(self, seconds: float) -> None:
        self.now += seconds

    async def hang(self) -> None:
        self._hung.set()
        await asyncio.Event().wait()

    async def wait_for(self, aw: Any, timeout: float | None) -> Any:
        deadline = None if timeout is None else self.now + timeout
        task = asyncio.ensure_future(aw)
        self._hung.clear()
        hung = asyncio.ensure_future(self._hung.wait())
        try:
            await asyncio.wait({task, hung}, timeout=HANG_S,
                               return_when=asyncio.FIRST_COMPLETED)
        finally:
            hung.cancel()
        if not task.done():
            task.cancel()
            assert self._hung.is_set(), "a fake neither answered nor hung"
            if deadline is None:
                raise AssertionError("a wait with no bound met a fake that hangs")
            self.now = max(self.now, deadline)
            raise TimeoutError
        if deadline is not None and self.now > deadline:
            raise TimeoutError
        return task.result()


def _chunk(content: str | None = None, reasoning: str | None = None) -> Any:
    return SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(
        content=content, reasoning_content=reasoning))])


#: The clock of the running test. The ``provider`` fixture installs it.
_CLOCK: list[_Clock] = []


class _Stream:
    """A fake litellm stream. Each step is ``(delay_s, chunk)``. After the last
    step it ends, or it hangs when ``hang_after`` is set."""

    def __init__(self, steps: list[tuple[float, Any]], *, hang_after: bool = False) -> None:
        self.steps = list(steps)
        self.hang_after = hang_after

    def __aiter__(self) -> _Stream:
        return self

    async def __anext__(self) -> Any:
        if not self.steps:
            if self.hang_after:
                await _CLOCK[0].hang()
            raise StopAsyncIteration
        delay, chunk = self.steps.pop(0)
        _CLOCK[0].spend(delay)
        return chunk


class _Forever:
    """A stream that sends a content chunk every ``gap`` seconds, forever."""

    def __init__(self, gap: float) -> None:
        self.gap = gap

    def __aiter__(self) -> _Forever:
        return self

    async def __anext__(self) -> Any:
        _CLOCK[0].spend(self.gap)
        return _chunk(content="x")


@pytest.fixture()
def provider(monkeypatch):
    """Patch the litellm entry and the key/usage plumbing. ``provider.set``
    gives the stream that the next call returns. ``open_delay`` delays the
    ``acompletion`` call itself, and ``None`` makes it hang."""
    import litellm

    state: dict[str, Any] = {"stream": _Stream([]), "open_delay": 0.0}
    clock = _Clock()
    _CLOCK[:] = [clock]
    monkeypatch.setattr(ctx, "asyncio", clock)
    state["clock"] = clock

    async def fake_acompletion(**_kw: Any) -> Any:
        if state["open_delay"] is None:
            await clock.hang()
        clock.spend(state["open_delay"])
        return state["stream"]

    async def _keys() -> None:
        return None

    monkeypatch.setattr(litellm, "acompletion", fake_acompletion)
    monkeypatch.setattr(litellm, "stream_chunk_builder", lambda *a, **k: None)
    monkeypatch.setattr(llm_client, "_ensure_keys_loaded", _keys)
    monkeypatch.setattr(llm_client, "ensure_model_registered", lambda _m: None)
    monkeypatch.setattr(llm_client, "_emit_usage", lambda *a, **k: None)
    monkeypatch.setattr(ctx, "resolve_underlying_model", lambda m: m)
    monkeypatch.setattr(ctx, "fit_messages_to_context",
                        lambda msgs, _m, **_k: (msgs, False))
    return state


async def _run(**bounds: float) -> tuple[str, str]:
    return await ctx.acompletion_stream_text(
        model="tier-fast", messages=[{"role": "user", "content": "hi"}],
        **bounds)


# ── The constants ───────────────────────────────────────────────────────────


def test_the_bounds_have_the_numbers_of_the_spec() -> None:
    assert (ctx.STREAM_FIRST_CHUNK_S, ctx.STREAM_IDLE_S, ctx.STREAM_TOTAL_S) == (
        45.0, 30.0, 150.0)


def test_each_timeout_is_a_typed_timeout_error_with_its_reason() -> None:
    for cls, reason, bound in (
        (ctx.StreamFirstChunkTimeout, "model_slow", "first_chunk"),
        (ctx.StreamIdleTimeout, "model_stalled", "idle"),
        (ctx.StreamTotalTimeout, "model_timeout", "total"),
    ):
        assert issubclass(cls, ctx.StreamTimeout)
        assert issubclass(cls, TimeoutError)
        assert (cls.reason, cls.bound) == (reason, bound)


# ── A1: each bound ends the stream ──────────────────────────────────────────


async def test_a_stream_that_sends_no_chunk_ends_at_the_first_chunk_bound(provider) -> None:
    provider["stream"] = _Stream([], hang_after=True)
    with structlog.testing.capture_logs() as logs, \
            pytest.raises(ctx.StreamFirstChunkTimeout) as info:
        await _run(first_chunk_s=0.15, idle_s=5.0, total_s=5.0)
    assert info.value.reason == "model_slow"
    line = next(e for e in logs if e["event"] == "acb_llm.stream_timeout")
    assert line["bound"] == "first_chunk" and line["bound_s"] == 0.15
    assert line["elapsed_ms"] == 150, "the stream ended at its first-chunk bound"
    assert line["reason"] == "model_slow"


async def test_a_provider_that_never_opens_the_stream_ends_at_the_first_chunk_bound(
    provider,
) -> None:
    provider["open_delay"] = None
    with pytest.raises(ctx.StreamFirstChunkTimeout):
        await _run(first_chunk_s=0.1, idle_s=5.0, total_s=5.0)


async def test_a_stream_that_stops_ends_at_the_idle_bound(provider) -> None:
    provider["stream"] = _Stream(
        [(0.0, _chunk(content="Hi ")), (0.0, _chunk(content="there"))],
        hang_after=True)
    seen: list[str] = []

    async def on_delta(_kind: str, text: str) -> None:
        seen.append(text)

    with structlog.testing.capture_logs() as logs, \
            pytest.raises(ctx.StreamIdleTimeout) as info:
        await ctx.acompletion_stream_text(
            model="tier-fast", messages=[{"role": "user", "content": "hi"}],
            on_delta=on_delta, first_chunk_s=5.0, idle_s=0.15, total_s=5.0)
    assert info.value.reason == "model_stalled"
    assert seen == ["Hi ", "there"], "the preview reached the caller before the stall"
    line = next(e for e in logs if e["event"] == "acb_llm.stream_timeout")
    assert line["bound"] == "idle" and line["bound_s"] == 0.15
    assert line["chunks"] == 2
    assert line["elapsed_ms"] == 150, "the stream ended one idle bound after its last chunk"


async def test_every_stream_ends_by_the_total_bound(provider) -> None:
    provider["stream"] = _Forever(gap=0.02)
    with structlog.testing.capture_logs() as logs, \
            pytest.raises(ctx.StreamTotalTimeout) as info:
        await _run(first_chunk_s=5.0, idle_s=5.0, total_s=0.3)
    assert info.value.reason == "model_timeout"
    line = next(e for e in logs if e["event"] == "acb_llm.stream_timeout")
    assert line["bound"] == "total" and line["bound_s"] == 0.3
    # The chunk that came past 0.3 s ended it: 15 gaps of 20 ms, plus one.
    assert 300 <= line["elapsed_ms"] <= 320, line["elapsed_ms"]
    assert line["chunks"] in (14, 15), line["chunks"]


async def test_a_slow_open_past_the_total_bound_is_a_total_timeout(provider) -> None:
    provider["open_delay"] = None
    with pytest.raises(ctx.StreamTotalTimeout):
        await _run(first_chunk_s=5.0, idle_s=5.0, total_s=0.1)


# ── Any chunk resets the idle timer ─────────────────────────────────────────


async def test_reasoning_chunks_reset_the_idle_timer(provider) -> None:
    """A reasoning model thinks for longer than one idle bound before it
    writes. Each reasoning chunk is a sign of life, so the stream lives.

    The first-chunk bound (0.15 s) is shorter than the reasoning (8 chunks of
    0.06 s, about 0.48 s). So the stream lives only if the FIRST reasoning
    chunk ends the first-chunk phase, and each later one resets the gap. A
    drafter that counts content chunks only fails here (mutation M4a)."""
    steps = [(0.06, _chunk(reasoning="thinking")) for _ in range(8)]
    steps.append((0.06, _chunk(content="Hi Ravi")))
    provider["stream"] = _Stream(steps)
    text, _model = await _run(first_chunk_s=0.15, idle_s=0.15, total_s=5.0)
    assert text == "Hi Ravi"


async def test_an_empty_chunk_resets_the_idle_timer(provider) -> None:
    """The same rule for a chunk with no choices: 6 of them take about
    0.36 s, past the first-chunk bound of 0.15 s."""
    steps = [(0.06, SimpleNamespace(choices=[])) for _ in range(6)]
    steps.append((0.06, _chunk(content="done")))
    provider["stream"] = _Stream(steps)
    text, _model = await _run(first_chunk_s=0.15, idle_s=0.15, total_s=5.0)
    assert text == "done"


async def test_a_stream_inside_its_bounds_is_unchanged(provider) -> None:
    provider["stream"] = _Stream([(0.0, _chunk(content="Hi ")), (0.01, _chunk(content="Ravi"))])
    assert await _run() == ("Hi Ravi", "tier-fast")


# ── The defaults are read at call time ──────────────────────────────────────


async def test_a_short_module_constant_bounds_a_call_with_no_arguments(
    provider, monkeypatch,
) -> None:
    """§17.3.7: each bound is a module constant that a test can make short.
    A default frozen at definition time would ignore it."""
    monkeypatch.setattr(ctx, "STREAM_IDLE_S", 0.1)
    provider["stream"] = _Stream([(0.0, _chunk(content="Hi"))], hang_after=True)
    with pytest.raises(ctx.StreamIdleTimeout):
        await _run()


async def test_the_log_line_holds_no_text_of_the_stream(provider) -> None:
    provider["stream"] = _Stream([(0.0, _chunk(content="SECRETWORD7"))], hang_after=True)
    with structlog.testing.capture_logs() as logs, pytest.raises(ctx.StreamTimeout):
        await ctx.acompletion_stream_text(
            model="tier-fast",
            messages=[{"role": "user", "content": "PROMPTWORD9"}],
            first_chunk_s=5.0, idle_s=0.1, total_s=5.0)
    line = next(e for e in logs if e["event"] == "acb_llm.stream_timeout")
    assert "SECRETWORD7" not in repr(line) and "PROMPTWORD9" not in repr(line)
