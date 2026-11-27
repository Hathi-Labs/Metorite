"""The product's own AI goes through OUR Router, so it bills. H-171.

Spec: `launch_surface.md` §4.1 (*"AI usage is metered separately in
credits"*) · **D57.7** (a routed call that fails, FAILS) · CP-6.

🔴 **Measured 2026-09-23: `acb_llm` held ZERO references to the Console.**
`acompletion_with_fallback` imported litellm and called it directly, so the
apps runtime, the email automation, the assistants and the agents — 29 files —
spent our vendor account and billed nobody. Flipping `ROUTER_SERVING_ENABLED`
did not change it, because the Router was not on that path.

⚠️ **Observed is not billed.** `_emit_usage` already logged the vendor cost and
published an activity with D1's four-tuple. What never happened was a
`usage_event` row or a credit drawn down.

⚠️ Hermetic. The subject is which client gets called and what it is handed, so
a real Postgres adds nothing. The BILLING half is proved by the Console's own
R8 suites, which this path now reaches.
"""
from __future__ import annotations

import asyncio
import itertools
import json
from typing import Any

import pytest

from acb_common.settings import get_settings

pytest.importorskip("litellm")

TIER = "tier-fast"
MESSAGES = [{"role": "user", "content": "hello"}]
OK_BODY = {
    "id": "cmpl-1",
    "model": "deepseek/deepseek-v4-flash",
    "choices": [
        {"index": 0, "message": {"role": "assistant", "content": "hi"},
         "finish_reason": "stop"}
    ],
    "usage": {"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4},
}


class RouterSpy:
    """Records what the Router was handed, and answers what the test set."""

    def __init__(self, status: int = 200, body: Any = None) -> None:
        self.calls: list[tuple[dict, dict]] = []
        self._status = status
        self._body = OK_BODY if body is None else body

    async def __call__(self, payload, **kw):
        self.calls.append((payload, kw))
        return self._status, self._body


@pytest.fixture
def routed(monkeypatch):
    """A box that bills: the flag on, the Console reachable."""
    monkeypatch.setenv("ROUTER_SERVING_ENABLED", "1")
    monkeypatch.setenv("CUSTOMER_CONSOLE_URL", "https://console.test")
    monkeypatch.setenv("CUSTOMER_CONSOLE_ORG_KEY", "cc_live_fixture_notarealsecret")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _spy_on(monkeypatch, spy):
    from acb_auth import console_resolve

    monkeypatch.setattr(console_resolve, "chat_completion_on_console", spy)


class TestRoutingIsOn:
    def test_BOTH_the_flag_and_the_wiring_are_required(self, monkeypatch):
        """⚠️ Neither implies the other. A box with the flag on and no address
        must not silently serve unbilled."""
        from acb_llm.routed import routing_is_on

        for flag, url, expected in (
            ("1", "https://console.test", True),
            ("0", "https://console.test", False),
            ("1", "", False),
        ):
            monkeypatch.setenv("ROUTER_SERVING_ENABLED", flag)
            monkeypatch.setenv("CUSTOMER_CONSOLE_URL", url)
            monkeypatch.setenv(
                "CUSTOMER_CONSOLE_ORG_KEY", "cc_live_fixture_notarealsecret"
            )
            get_settings.cache_clear()
            assert routing_is_on() is expected, (flag, url)
        get_settings.cache_clear()


class TestTheInProductPathNowBills:
    async def test_it_reaches_the_ROUTER_and_not_litellm(self, monkeypatch, routed):
        """🔴 The whole ticket. This path used to call litellm directly."""
        from acb_llm.context import acompletion_with_fallback

        spy = RouterSpy()
        _spy_on(monkeypatch, spy)
        # If routing leaks through to litellm the test fails loudly rather
        # than quietly spending a real vendor key.
        import litellm

        async def _boom(*a, **k):
            raise AssertionError("the call reached litellm, so it billed nobody")

        monkeypatch.setattr(litellm, "acompletion", _boom)

        resp, used = await acompletion_with_fallback(
            model=TIER, messages=MESSAGES, max_tokens=64
        )
        assert spy.calls, "the Router was never called"
        assert resp.choices[0].message.content == "hi"
        assert used == "deepseek/deepseek-v4-flash"

    async def test_it_sends_the_TIER_and_never_a_resolved_model(
        self, monkeypatch, routed
    ):
        """⚠️ The Console picks from `tier_binding`, which an operator ordered
        and which fails over. Sending a concrete model would take that away."""
        from acb_llm.context import acompletion_with_fallback

        spy = RouterSpy()
        _spy_on(monkeypatch, spy)
        await acompletion_with_fallback(
            model=TIER, fallback_model="tier-powerful", messages=MESSAGES,
            max_tokens=64,
        )
        payload, _ = spy.calls[0]
        # ⚠️ The field is called `model` and it carries the TIER ALIAS. A
        # first version sent `{"tier": ...}`, which `CompletionRequest` would
        # have refused outright — it is `extra="forbid"`.
        assert payload["model"] == TIER
        assert not payload["model"].startswith("deepseek/"), (
            "a resolved model id would take away the operator's ranked chain")

    async def test_the_response_is_the_TYPE_the_29_call_sites_already_hold(
        self, monkeypatch, routed
    ):
        """⚠️ They read `resp.choices[0].message.content`. Handing them a dict
        turns a working feature into an AttributeError far from this change."""
        from litellm import ModelResponse

        from acb_llm.context import acompletion_with_fallback

        _spy_on(monkeypatch, RouterSpy())
        resp, _ = await acompletion_with_fallback(
            model=TIER, messages=MESSAGES, max_tokens=64
        )
        assert isinstance(resp, ModelResponse)
        assert resp.usage.prompt_tokens == 3

    async def test_attribution_comes_from_the_RUN_CONTEXT_for_free(
        self, monkeypatch, routed
    ):
        """⚠️ Per-member and per-app billing with no call-site change, which is
        the property `_emit_usage`'s docstring already records: *an in-run
        caller passes none of them*."""
        from acb_common._log import bind_run_context

        from acb_llm.context import acompletion_with_fallback

        spy = RouterSpy()
        _spy_on(monkeypatch, spy)
        bind_run_context(
            user="dana@example.com", agent="planner", source="email",
            run_id="run-9",
        )
        try:
            await acompletion_with_fallback(
                model=TIER, messages=MESSAGES, max_tokens=64
            )
        finally:
            import structlog

            structlog.contextvars.clear_contextvars()
        _, kw = spy.calls[0]
        assert kw["member"] == "dana@example.com"
        assert kw["agent"] == "planner"
        assert kw["module_slug"] == "email"
        assert kw["run_id"] == "run-9"

    async def test_an_explicit_source_BEATS_the_run_context(
        self, monkeypatch, routed
    ):
        """Custom Apps all run through one module, so only the caller knows
        which app it is — the reason `source` exists at all."""
        from acb_common._log import bind_run_context

        from acb_llm.context import acompletion_with_fallback

        spy = RouterSpy()
        _spy_on(monkeypatch, spy)
        bind_run_context(source="apps")
        try:
            await acompletion_with_fallback(
                model=TIER, messages=MESSAGES, max_tokens=64,
                source="app:invoices",
            )
        finally:
            import structlog

            structlog.contextvars.clear_contextvars()
        _, kw = spy.calls[0]
        assert kw["module_slug"] == "app:invoices"


class TestD57_7_ARoutedCallThatFailsFAILS:
    @pytest.mark.parametrize(
        ("status", "word"),
        [(402, "credits"), (400, "tier"), (403, "breaker")],
    )
    async def test_a_refusal_RAISES_and_never_falls_back_to_litellm(
        self, monkeypatch, routed, status, word
    ):
        """🔴 The one moment the unbilled path must not come back.

        A quiet fall back to local litellm would serve the call for free
        exactly when the Console is refusing to be paid for it.
        """
        import litellm

        from acb_llm.context import acompletion_with_fallback
        from acb_llm.routed import RoutedRefusal

        _spy_on(monkeypatch, RouterSpy(status, {"detail": f"out of {word}"}))

        async def _boom(*a, **k):
            raise AssertionError("it fell back to litellm and served unbilled")

        monkeypatch.setattr(litellm, "acompletion", _boom)

        with pytest.raises(RoutedRefusal) as caught:
            await acompletion_with_fallback(
                model=TIER, fallback_model="tier-powerful",
                messages=MESSAGES, max_tokens=64,
            )
        assert caught.value.status == status
        assert word in caught.value.detail

    async def test_an_OUTAGE_also_never_falls_back(self, monkeypatch, routed):
        """⚠️ D57.7 covers no-answer too, not only a refusal."""
        import litellm

        from acb_auth.console_resolve import ConsoleRouterUnavailable
        from acb_llm.context import acompletion_with_fallback

        async def _down(*a, **k):
            raise ConsoleRouterUnavailable("unreachable")

        _spy_on(monkeypatch, _down)

        async def _boom(*a, **k):
            raise AssertionError("it fell back to litellm during an outage")

        monkeypatch.setattr(litellm, "acompletion", _boom)

        with pytest.raises(ConsoleRouterUnavailable):
            await acompletion_with_fallback(
                model=TIER, messages=MESSAGES, max_tokens=64
            )


class TestTheUNROUTEDPathIsUntouched:
    @pytest.mark.direct_arm_guard
    async def test_with_the_flag_OFF_it_still_uses_litellm(self, monkeypatch):
        """⚠️ A dev box with the flag off behaves as before H-171.

        Task 3554: the direct arm runs on a dev box only, so this test says
        ``ACB_ENV=dev`` and runs the real guard. Outside dev the call raises
        (``TestFallbackSeamIsClosedOutsideDev``)."""
        monkeypatch.setenv("ACB_ENV", "dev")
        monkeypatch.setenv("ROUTER_SERVING_ENABLED", "0")
        get_settings.cache_clear()

        import litellm

        from acb_llm.context import acompletion_with_fallback

        seen: list[str] = []

        async def _fake(*, model, **k):
            seen.append(model)
            from litellm import ModelResponse

            return ModelResponse(**OK_BODY)

        monkeypatch.setattr(litellm, "acompletion", _fake)
        try:
            await acompletion_with_fallback(
                model=TIER, messages=MESSAGES, max_tokens=64
            )
        finally:
            get_settings.cache_clear()
        assert seen, "the direct path stopped working"


# ── The streamed arm (task 3541, then task 3542) ────────────────────────────

#: The model that a Router stream names in each frame.
STREAM_MODEL = "deepseek/deepseek-v4-flash"


def _sse(body: dict[str, Any]) -> bytes:
    return b"data: " + json.dumps(body).encode("utf-8") + b"\n\n"


def _frame(content: str | None = None, reasoning: str | None = None) -> bytes:
    """One chunk frame of a Router stream, as the Console relays it."""
    delta: dict[str, str] = {}
    if content is not None:
        delta["content"] = content
    if reasoning is not None:
        delta["reasoning_content"] = reasoning
    return _sse({"model": STREAM_MODEL, "choices": [{"index": 0, "delta": delta}]})


#: The usage frame: no choice, the counts only. It must never read as text.
USAGE_FRAME = _sse({"model": STREAM_MODEL, "choices": [], "usage": {
    "prompt_tokens": 3, "completion_tokens": 4, "total_tokens": 7}})
DONE_FRAME = b"data: [DONE]\n\n"


class StreamSpy:
    """A fake ``stream_completion_on_console``. It yields raw SSE bytes.

    Each step is bytes, or ``(seconds, bytes)`` that first spends those
    seconds of ``clock``. ``verdict`` and ``outage`` raise before the first
    byte, as the real client does. ``hang_after`` waits with no end after the
    last step. ``closed`` turns True in the ``finally``, so a test sees the
    Console stream close.
    """

    def __init__(self, steps: Any = (), *, verdict: tuple[int, dict] | None = None,
                 outage: bool = False, hang_after: bool = False,
                 clock: Any = None) -> None:
        self.steps = steps
        self.verdict = verdict
        self.outage = outage
        self.hang_after = hang_after
        self.clock = clock
        self.calls: list[tuple[dict, dict]] = []
        self.closed = False

    def __call__(self, payload, **kw):
        self.calls.append((payload, kw))
        return self._bytes()

    async def _bytes(self):
        from acb_auth.console_resolve import ConsoleRouterUnavailable, ConsoleRouterVerdict

        try:
            if self.outage:
                raise ConsoleRouterUnavailable("unreachable")
            if self.verdict is not None:
                raise ConsoleRouterVerdict(*self.verdict)
            for step in self.steps:
                delay, chunk = step if isinstance(step, tuple) else (0.0, step)
                if delay:
                    self.clock.spend(delay)
                yield chunk
            if self.hang_after:
                if self.clock is not None:
                    await self.clock.hang()
                await asyncio.Event().wait()
        finally:
            self.closed = True


class _Seen(list):
    """An ``on_delta`` that records each ``(kind, text)``."""

    async def __call__(self, kind: str, text: str) -> None:
        self.append((kind, text))


async def _draft(on_delta: Any = None, **kw: Any) -> tuple[str, str]:
    from acb_llm.context import acompletion_stream_text

    return await acompletion_stream_text(
        model="tier-balanced", messages=MESSAGES, max_tokens=64,
        on_delta=on_delta, **kw)


@pytest.fixture
def stream_spy(monkeypatch, routed):
    """Route the streamed arm to a :class:`StreamSpy`. The buffered client and
    litellm each fail the test, so the arm makes ONE Console call: a stream."""
    import litellm
    from acb_auth import console_resolve

    async def _no_buffered(*a, **k):
        raise AssertionError("the streamed arm made a buffered Router call too")

    async def _no_litellm(*a, **k):
        raise AssertionError("the streaming arm reached litellm, so it billed nobody")

    monkeypatch.setattr(console_resolve, "chat_completion_on_console", _no_buffered)
    monkeypatch.setattr(litellm, "acompletion", _no_litellm)

    def install(spy: StreamSpy) -> StreamSpy:
        monkeypatch.setattr(console_resolve, "stream_completion_on_console", spy)
        return spy

    return install


#: A hang guard, never a budget. Only a fake that never answers meets it.
_HANG_S = 30.0


class _Clock:
    """``asyncio`` as ``acb_llm.context`` reads it, on a clock only the fakes
    move. The same fake as ``test_llm_stream_bounds.py``: the timing of a
    stream is data, so a loaded runner cannot change which bound fires."""

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
            await asyncio.wait({task, hung}, timeout=_HANG_S,
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


@pytest.fixture
def clock(monkeypatch):
    from acb_llm import context as ctx

    fake = _Clock()
    monkeypatch.setattr(ctx, "asyncio", fake)
    return fake


async def _closed(spy: StreamSpy) -> None:
    """The fake clock cancels a wait and does not wait for the cancel, so the
    close of the Console stream can land a few turns of the loop later."""
    for _ in range(50):
        if spy.closed:
            return
        await asyncio.sleep(0)
    assert spy.closed, "the Console stream stayed open after the bound"


def _timeout_line(logs: list[dict[str, Any]]) -> dict[str, Any]:
    return next(e for e in logs if e["event"] == "acb_llm.stream_timeout")


class TestTheSTREAMINGArmIsRouted:
    """Task 3541 routed ``acompletion_stream_text``, and task 3542 streams it.

    🔴 Production, 2026-11-16 07:20 UTC: the email draft streamed from DeepSeek
    with the gateway's own key. That account had no balance, so each draft
    failed, while the Router served chat. Task 3541 sent the arm to the Router
    for one whole answer. So the member saw the draft at once, and only the
    total bound held. Task 3542 reads the Router's stream (CP-4b) with
    ``acb_llm.routed.stream_on_router``: each delta reaches ``on_delta`` as it
    comes, and the three bounds hold again.
    """

    async def test_a_routed_draft_STREAMS_more_than_one_content_delta(self, stream_spy):
        """The whole slice. ``aiter_bytes`` does not cut on a frame, so the
        bytes come in pieces of 7, and most pieces end inside a frame."""
        from acb_llm.routed import router_tier

        raw = (_frame(content="Dear ") + _frame(content="Ravi,")
               + _frame(content=" thanks.") + USAGE_FRAME + DONE_FRAME)
        spy = stream_spy(StreamSpy([raw[i:i + 7] for i in range(0, len(raw), 7)]))
        seen = _Seen()
        text, used = await _draft(on_delta=seen, feature="email.draft", job="writing")
        assert seen == [("content", "Dear "), ("content", "Ravi,"), ("content", " thanks.")]
        assert text == "Dear Ravi, thanks."
        assert used == STREAM_MODEL
        assert len(spy.calls) == 1, "one Router call, and no second one"
        payload, _ = spy.calls[0]
        assert payload["stream"] is True
        assert payload["model"] == router_tier("tier-balanced")
        assert spy.closed

    async def test_reasoning_goes_first_as_reasoning_and_never_into_the_text(
        self, stream_spy
    ):
        """The contract of the direct arm: a reasoning delta is ``reasoning``,
        it goes to ``on_delta``, and it is not part of the returned text."""
        stream_spy(StreamSpy([
            _frame(reasoning="Thinking"), _frame(reasoning=" more"),
            _frame(content="Hi"), _frame(content=" Ravi"), DONE_FRAME]))
        seen = _Seen()
        text, _ = await _draft(on_delta=seen)
        assert seen == [("reasoning", "Thinking"), ("reasoning", " more"),
                        ("content", "Hi"), ("content", " Ravi")]
        assert text == "Hi Ravi"

    async def test_the_usage_frame_never_reaches_the_composer(self, stream_spy):
        stream_spy(StreamSpy([_frame(content="Hi"), USAGE_FRAME, DONE_FRAME]))
        seen = _Seen()
        text, _ = await _draft(on_delta=seen)
        assert seen == [("content", "Hi")]
        assert text == "Hi"

    async def test_the_payload_VALIDATES_with_stream_on(self, stream_spy):
        """The stream payload is the buffered payload plus ``stream``. The
        Console asks the provider for the usage frame itself
        (``_stream_kwargs_for``), so the arm sends no ``stream_options``, as
        the ``/v1`` relay sends none."""
        spy = stream_spy(StreamSpy([_frame(content="Hi"), DONE_FRAME]))
        await _draft(temperature=0.3, job="writing")
        payload, _ = spy.calls[0]
        parsed = TestThePayloadSurvivesTheREALModel._model()(**payload)
        assert parsed.stream is True
        assert parsed.model == "tier-balanced"
        assert "stream_options" not in payload

    async def test_attribution_comes_from_the_RUN_CONTEXT_as_on_the_buffered_call(
        self, stream_spy
    ):
        """The same ``run_attribution`` as ``completion_on_router``, so a
        streamed draft bills the same member and app as a buffered one."""
        import structlog
        from acb_common._log import bind_run_context

        spy = stream_spy(StreamSpy([_frame(content="Hi"), DONE_FRAME]))
        bind_run_context(
            user="dana@example.com", agent="planner", source="email", run_id="run-9")
        try:
            await _draft()
        finally:
            structlog.contextvars.clear_contextvars()
        _, kw = spy.calls[0]
        assert kw["member"] == "dana@example.com"
        assert kw["agent"] == "planner"
        assert kw["module_slug"] == "email"
        assert kw["run_id"] == "run-9"

    async def test_an_empty_stream_sends_no_delta_and_logs_the_empty_answer(
        self, stream_spy
    ):
        import structlog

        finish = _sse({"model": STREAM_MODEL, "choices": [
            {"index": 0, "delta": {}, "finish_reason": "length"}]})
        stream_spy(StreamSpy([_frame(), finish, USAGE_FRAME, DONE_FRAME]))
        seen = _Seen()
        with structlog.testing.capture_logs() as logs:
            text, used = await _draft(on_delta=seen, job="writing")
        assert text == "" and seen == []
        assert used == STREAM_MODEL
        [line] = [e for e in logs if e["event"] == "acb_llm.empty_answer"]
        assert line["finish_reason"] == "length"
        assert line["completion_tokens"] == 4
        assert line["model"] == STREAM_MODEL

    @pytest.mark.parametrize("status", [402, 400, 403, 501])
    async def test_a_verdict_RAISES_a_refusal_and_never_falls_back(
        self, stream_spy, status
    ):
        """D57.7. A verdict comes before the first byte, and it is the
        buffered call's ``RoutedRefusal`` with the Console's own detail."""
        from acb_llm.routed import RoutedRefusal

        spy = stream_spy(StreamSpy(verdict=(status, {"detail": f"no ({status})"})))
        with pytest.raises(RoutedRefusal) as caught:
            await _draft()
        assert caught.value.status == status
        assert caught.value.detail == f"no ({status})"
        assert len(spy.calls) == 1

    async def test_an_OUTAGE_also_never_falls_back(self, stream_spy):
        from acb_auth.console_resolve import ConsoleRouterUnavailable

        spy = stream_spy(StreamSpy(outage=True))
        with pytest.raises(ConsoleRouterUnavailable):
            await _draft()
        assert len(spy.calls) == 1

    @pytest.mark.parametrize(("steps", "code"), [
        ([_frame(content="Hi"), _sse({"error": {
            "message": "SECRETWORD7 quoted from the prompt", "code": 500}})],
         "stream_error_frame"),
        ([_frame(content="Hi"), b"data: {not json}\n\n"], "stream_malformed_frame"),
        ([_frame(content="Hi")], "stream_truncated"),
        ([DONE_FRAME], "stream_never_opened"),
    ], ids=["error-frame", "malformed", "truncated", "lone-done"])
    async def test_a_broken_stream_is_an_OUTAGE_with_a_code_and_no_text(
        self, stream_spy, steps, code
    ):
        """A stream that is not one whole answer fails, and never as a short
        draft. ``stream_completion_on_console`` raises
        ``ConsoleRouterUnavailable`` for a break in the middle of a stream, so
        a broken frame takes the same class. The draft then says the AI
        service did not answer (``drafting.draft_failure``). A lone ``[DONE]``
        is what the Console sends when every step of its chain failed.

        The message is our code only. A provider error can quote the prompt,
        and the drafter logs ``str(exc)``."""
        import structlog
        from acb_auth.console_resolve import ConsoleRouterUnavailable
        from acb_llm.routed import is_router_outage

        spy = stream_spy(StreamSpy(steps))
        with structlog.testing.capture_logs() as logs, \
                pytest.raises(ConsoleRouterUnavailable) as caught:
            await _draft(on_delta=_Seen())
        assert str(caught.value) == code
        assert is_router_outage(caught.value)
        assert "SECRETWORD7" not in repr(caught.value)
        assert "SECRETWORD7" not in repr(logs)
        assert spy.closed

    async def test_DONE_ends_the_read_and_closes_the_Router_stream(self, stream_spy):
        """The spy waits with no end after ``[DONE]``. A reader that went on
        would meet the idle bound, and a stream left open would not read
        closed."""
        spy = stream_spy(StreamSpy([_frame(content="Hi"), DONE_FRAME], hang_after=True))
        text, _ = await _draft(idle_s=2.0)
        assert text == "Hi"
        assert spy.closed, "the Console stream stayed open after [DONE]"

    async def test_a_cancel_while_the_arm_waits_closes_the_Router_stream(
        self, stream_spy
    ):
        spy = stream_spy(StreamSpy([_frame(content="Hi")], hang_after=True))
        first = asyncio.Event()

        async def on_delta(_kind: str, _text: str) -> None:
            first.set()

        task = asyncio.create_task(_draft(on_delta=on_delta))
        await asyncio.wait_for(first.wait(), 5.0)
        await asyncio.sleep(0)  # the arm waits for the next frame by now
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert spy.closed, "the Console stream stayed open after the cancel"

    async def test_a_cancel_inside_on_delta_closes_the_Router_stream(self, stream_spy):
        """The member closed the composer while the route sent a delta. The
        Console generator sits at a ``yield`` then, so only the close in the
        ``finally`` of the arm and of the client reaches it."""
        spy = stream_spy(StreamSpy(
            [_frame(content="Hi"), _frame(content=" there"), DONE_FRAME]))
        entered = asyncio.Event()

        async def on_delta(_kind: str, _text: str) -> None:
            entered.set()
            await asyncio.Event().wait()  # a slow composer

        task = asyncio.create_task(_draft(on_delta=on_delta))
        await asyncio.wait_for(entered.wait(), 5.0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert spy.closed, "the Console stream stayed open after the cancel"

    # ── The three bounds, on a fake clock ───────────────────────────────────

    async def test_no_frame_ends_at_the_FIRST_CHUNK_bound(self, stream_spy, clock):
        import structlog
        from acb_llm.context import StreamFirstChunkTimeout

        spy = stream_spy(StreamSpy([], hang_after=True, clock=clock))
        with structlog.testing.capture_logs() as logs, \
                pytest.raises(StreamFirstChunkTimeout) as info:
            await _draft(first_chunk_s=0.15, idle_s=5.0, total_s=5.0)
        assert info.value.reason == "model_slow"
        line = _timeout_line(logs)
        assert (line["bound"], line["bound_s"], line["chunks"]) == ("first_chunk", 0.15, 0)
        assert line["elapsed_ms"] == 150
        await _closed(spy)

    async def test_a_stream_that_stops_ends_at_the_IDLE_bound(self, stream_spy, clock):
        import structlog
        from acb_llm.context import StreamIdleTimeout

        spy = stream_spy(StreamSpy(
            [_frame(content="Hi "), _frame(content="there")], hang_after=True,
            clock=clock))
        seen = _Seen()
        with structlog.testing.capture_logs() as logs, \
                pytest.raises(StreamIdleTimeout) as info:
            await _draft(on_delta=seen, first_chunk_s=5.0, idle_s=0.15, total_s=5.0)
        assert info.value.reason == "model_stalled"
        assert seen == [("content", "Hi "), ("content", "there")], (
            "the preview reached the composer before the stall")
        line = _timeout_line(logs)
        assert (line["bound"], line["bound_s"], line["chunks"]) == ("idle", 0.15, 2)
        assert line["elapsed_ms"] == 150
        await _closed(spy)

    async def test_every_stream_ends_by_the_TOTAL_bound(self, stream_spy, clock):
        import structlog
        from acb_llm.context import StreamTotalTimeout

        spy = stream_spy(StreamSpy(
            itertools.repeat((0.02, _frame(content="x"))), clock=clock))
        with structlog.testing.capture_logs() as logs, \
                pytest.raises(StreamTotalTimeout) as info:
            await _draft(first_chunk_s=5.0, idle_s=5.0, total_s=0.3)
        assert info.value.reason == "model_timeout"
        line = _timeout_line(logs)
        assert (line["bound"], line["bound_s"]) == ("total", 0.3)
        assert 300 <= line["elapsed_ms"] <= 320, line["elapsed_ms"]
        await _closed(spy)

    async def test_reasoning_frames_reset_the_idle_bound(self, stream_spy, clock):
        """A reasoning model thinks for longer than one idle bound before it
        writes. Each reasoning frame is a sign of life, as on the direct arm."""
        steps: list[Any] = [(0.06, _frame(reasoning="thinking")) for _ in range(8)]
        steps += [(0.06, _frame(content="Hi Ravi")), DONE_FRAME]
        stream_spy(StreamSpy(steps, clock=clock))
        text, _ = await _draft(first_chunk_s=0.15, idle_s=0.15, total_s=5.0)
        assert text == "Hi Ravi"

    async def test_a_keepalive_is_no_sign_of_life(self, stream_spy, clock):
        """A comment line is not a frame of the model. Three of them, 0.06 s
        apart, pass an idle bound of 0.15 s. If a comment reset the bound,
        the stream would end well with " late"."""
        from acb_llm.context import StreamIdleTimeout

        steps: list[Any] = [_frame(content="Hi")]
        steps += [(0.06, b": keepalive\n\n")] * 3
        steps += [_frame(content=" late"), DONE_FRAME]
        stream_spy(StreamSpy(steps, clock=clock))
        with pytest.raises(StreamIdleTimeout):
            await _draft(first_chunk_s=5.0, idle_s=0.15, total_s=5.0)

    async def test_a_short_module_constant_bounds_the_routed_arm_too(
        self, stream_spy, clock, monkeypatch
    ):
        """Each bound is read at call time, on this arm as on the direct one."""
        from acb_llm import context as ctx

        monkeypatch.setattr(ctx, "STREAM_IDLE_S", 0.1)
        stream_spy(StreamSpy([_frame(content="Hi")], hang_after=True, clock=clock))
        with pytest.raises(ctx.StreamIdleTimeout):
            await _draft()

    @pytest.mark.direct_arm_guard
    async def test_with_the_flag_OFF_it_still_streams_from_litellm(
        self, monkeypatch
    ):
        """⚠️ A dev box with the flag off streams as before task 3541.

        Task 3554: outside dev the call raises
        (``TestStreamSeamIsClosedOutsideDev``)."""
        monkeypatch.setenv("ACB_ENV", "dev")
        monkeypatch.setenv("ROUTER_SERVING_ENABLED", "0")
        get_settings.cache_clear()
        import litellm
        from acb_llm import client as llm_client
        from acb_llm import context as ctx

        class _Stream:
            def __init__(self):
                self._chunks = [
                    type("C", (), {"choices": [type("Ch", (), {
                        "delta": type("D", (), {"content": "hi",
                                                "reasoning_content": None})()
                    })()]})()
                ]

            def __aiter__(self):
                return self

            async def __anext__(self):
                if not self._chunks:
                    raise StopAsyncIteration
                return self._chunks.pop(0)

        streamed: list[bool] = []

        async def _fake(*, stream=False, **k):
            streamed.append(stream)
            return _Stream()

        async def _keys():
            return None

        monkeypatch.setattr(litellm, "acompletion", _fake)
        monkeypatch.setattr(litellm, "stream_chunk_builder", lambda *a, **k: None)
        monkeypatch.setattr(llm_client, "_ensure_keys_loaded", _keys)
        monkeypatch.setattr(llm_client, "ensure_model_registered", lambda _m: None)
        monkeypatch.setattr(ctx, "resolve_underlying_model", lambda m: m)
        try:
            text, _used = await ctx.acompletion_stream_text(
                model=TIER, messages=MESSAGES, max_tokens=64)
        finally:
            get_settings.cache_clear()
        assert streamed == [True], "the direct streaming path stopped working"
        assert text == "hi"


class TestTheStreamedArmThroughTheREALClient:
    """The real ``stream_completion_on_console`` over an ``httpx`` mock.

    A stub agrees with whatever it is handed (R8). Here the request is the one
    that leaves the box, and the bytes come in pieces of 5, as a socket cuts
    them, through ``aiter_bytes``.
    """

    @staticmethod
    def _wire(monkeypatch, *, status: int = 200, body: bytes = b"",
              error: dict[str, Any] | None = None, hang: bool = False,
              state: dict[str, bool] | None = None,
              source: Any = None) -> list[Any]:
        """``hang`` makes the body wait with no end after its bytes, and
        ``state["closed"]`` turns True when httpx closes the body stream.
        ``source`` is a function that gives the body as an async iterator,
        in place of ``body``."""
        import httpx
        import litellm
        from acb_auth import console_resolve

        seen: list[httpx.Request] = []

        class _Pieces(httpx.AsyncByteStream):
            async def __aiter__(self):
                if source is not None:
                    async for chunk in source():
                        yield chunk
                    return
                for i in range(0, len(body), 5):
                    yield body[i:i + 5]
                if hang:
                    await asyncio.Event().wait()

            async def aclose(self) -> None:
                if state is not None:
                    state["closed"] = True

        def answer(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            if status != 200:
                return httpx.Response(status, json=error or {})
            return httpx.Response(200, stream=_Pieces(),
                                  headers={"content-type": "text/event-stream"})

        async def _no_litellm(*a, **k):
            raise AssertionError("the streaming arm reached litellm")

        monkeypatch.setattr(litellm, "acompletion", _no_litellm)
        monkeypatch.setattr(
            console_resolve, "_new_http_client",
            lambda _timeout=None: httpx.AsyncClient(
                transport=httpx.MockTransport(answer), timeout=5.0))
        return seen

    async def test_the_draft_streams_through_the_real_client(self, monkeypatch, routed):
        raw = _frame(content="Dear ") + _frame(content="Ravi") + USAGE_FRAME + DONE_FRAME
        requests = self._wire(monkeypatch, body=raw)
        seen = _Seen()
        text, used = await _draft(on_delta=seen)
        assert seen == [("content", "Dear "), ("content", "Ravi")]
        assert (text, used) == ("Dear Ravi", STREAM_MODEL)
        [request] = requests
        assert str(request.url) == "https://console.test/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer cc_live_fixture_notarealsecret"
        sent = json.loads(request.content)
        assert sent["stream"] is True
        TestThePayloadSurvivesTheREALModel._model()(**sent)

    async def test_a_402_through_the_real_client_is_a_refusal(self, monkeypatch, routed):
        from acb_llm.routed import RoutedRefusal

        self._wire(monkeypatch, status=402, error={"detail": "out of credits"})
        with pytest.raises(RoutedRefusal) as caught:
            await _draft()
        assert (caught.value.status, caught.value.detail) == (402, "out of credits")

    async def test_a_bound_through_the_real_client_closes_the_connection(
        self, monkeypatch, routed
    ):
        """The idle bound with the REAL ``asyncio.wait_for`` and the real
        client. The cancel of the wait runs through both generators and the
        httpx stream, and ``stream_completion_on_console`` turns each
        ``Exception`` into an outage. So the arm must still raise the bound,
        and the body stream must be closed when the raise lands."""
        from acb_llm.context import StreamIdleTimeout

        state = {"closed": False}
        self._wire(monkeypatch, body=_frame(content="Hi"), hang=True, state=state)
        seen = _Seen()
        with pytest.raises(StreamIdleTimeout):
            await _draft(on_delta=seen, first_chunk_s=5.0, idle_s=0.05, total_s=5.0)
        assert seen == [("content", "Hi")]
        assert state["closed"], "the connection to the Console stayed open"

    async def test_the_FIRST_CHUNK_bound_through_the_real_client(
        self, monkeypatch, routed
    ):
        """The real clock and the real client. A keepalive comment every
        0.05 s makes no frame, so the first-chunk bound (0.25 s) fires, far
        inside the total bound (2 s). The guard of 5 s turns a bound that never
        fires into a plain ``TimeoutError``, so the test fails and does not
        hang."""
        from acb_llm import context as ctx

        async def keepalives():
            while True:
                yield b": keepalive\n\n"
                await asyncio.sleep(0.05)

        monkeypatch.setattr(ctx, "STREAM_FIRST_CHUNK_S", 0.25)
        monkeypatch.setattr(ctx, "STREAM_TOTAL_S", 2.0)
        state = {"closed": False}
        self._wire(monkeypatch, source=keepalives, state=state)
        with pytest.raises(ctx.StreamFirstChunkTimeout):
            await asyncio.wait_for(_draft(), 5.0)
        assert state["closed"], "the connection to the Console stayed open"

    async def test_the_TOTAL_bound_through_the_real_client(self, monkeypatch, routed):
        """The real clock and the real client. A frame every 0.1 s keeps the
        idle bound (30 s) away, so the total bound (1.0 s) ends the stream. The
        guard of 5 s turns a bound that never fires into a plain
        ``TimeoutError``."""
        from acb_llm import context as ctx

        async def frames():
            while True:
                yield _frame(content="x")
                await asyncio.sleep(0.1)

        monkeypatch.setattr(ctx, "STREAM_TOTAL_S", 1.0)
        state = {"closed": False}
        self._wire(monkeypatch, source=frames, state=state)
        seen = _Seen()
        with pytest.raises(ctx.StreamTotalTimeout):
            await asyncio.wait_for(_draft(on_delta=seen), 5.0)
        assert seen, "no delta reached the composer before the bound"
        assert state["closed"], "the connection to the Console stayed open"


# ── The fence the stub could not be ─────────────────────────────────────────


class TestThePayloadSurvivesTheREALModel:
    """🔴 A stub agrees with whatever it is handed, and that hid a 422.

    The first version of this routing sent `{"tier": ...}` and spread the
    caller's `**extra`. `CompletionRequest` is `extra="forbid"` and its field
    is called `model`, so EVERY routed call would have been refused — and the
    suite above stayed green, because it stubbed the client.

    R8 says verify against the real thing. The real thing here is not a
    database, it is the pydantic model the Console actually validates with.
    """

    @staticmethod
    def _model():
        pytest.importorskip("fastapi")
        from customer_console.main import CompletionRequest

        return CompletionRequest

    async def test_the_payload_VALIDATES(self, monkeypatch, routed):
        from acb_llm.context import acompletion_with_fallback

        spy = RouterSpy()
        _spy_on(monkeypatch, spy)
        await acompletion_with_fallback(
            model=TIER, messages=MESSAGES, max_tokens=64, temperature=0.3
        )
        payload, _ = spy.calls[0]
        # Raises ValidationError if a field is misnamed or unknown.
        parsed = self._model()(**payload)
        assert parsed.model == TIER
        assert parsed.max_tokens == 64

    async def test_litellm_TRANSPORT_options_never_reach_the_Console(
        self, monkeypatch, routed
    ):
        """⚠️ `timeout`, `cache` and `prompt_cache_key` are litellm's, and a
        `forbid` model refuses the whole call rather than ignoring them."""
        from acb_llm.context import acompletion_with_fallback

        spy = RouterSpy()
        _spy_on(monkeypatch, spy)
        await acompletion_with_fallback(
            model=TIER, messages=MESSAGES, max_tokens=64,
            timeout=30, cache={"no-cache": False}, prompt_cache_key="k",
        )
        payload, _ = spy.calls[0]
        for leaked in ("timeout", "cache", "prompt_cache_key"):
            assert leaked not in payload, f"{leaked} would 422 the whole call"
        self._model()(**payload)

    def test_every_FORWARDABLE_key_is_a_real_field(self):
        """⚠️ The allowlist is named, not derived. This is what keeps the two
        from drifting apart without coupling the package to the service."""
        from acb_llm.routed import _FORWARDABLE

        fields = set(self._model().model_fields)
        assert set(_FORWARDABLE) <= fields, (
            f"not fields of CompletionRequest: {set(_FORWARDABLE) - fields}")

    def test_TOOL_CALLING_is_routable_and_forwarded(self):
        """🔴 This clause was INVERTED, and the inversion is the point.

        It first asserted `tools` was absent, because I read the model with a
        line-bounded grep and concluded tool-calling could not be routed. The
        field exists. So does `tool_choice`. Had that stood, every
        tool-calling agent would have stayed unbilled on a wrong premise
        written into a test.
        """
        from acb_llm.routed import _FORWARDABLE

        fields = self._model().model_fields
        for name in ("tools", "tool_choice", "parallel_tool_calls"):
            assert name in fields, name
            assert name in _FORWARDABLE, name

    def test_STREAM_is_never_forwarded_on_the_non_streaming_path(self):
        """⚠️ Asking for frames nothing reads. The streaming client is a
        separate function with a separate contract."""
        from acb_llm.routed import _FORWARDABLE

        assert "stream" not in _FORWARDABLE


# ── The AGENT paths — the largest unbilled surface of all ───────────────────


#: The slate the Console seeds in `tier_catalog`. A routed call must name one.
_CONSOLE_CHAT_TIERS = {"tier-fast", "tier-balanced", "tier-powerful"}


class TestTheRouterIsHandedASlateName:
    """🔴 Production, 2026-09-24: the Tasks assistant sent `tier2` and the
    Router refused it with `tier_unknown`. `LLMTier.value` is the legacy id,
    and no test looked at the name the Router was handed."""

    @pytest.mark.parametrize("which", ["complete", "complete_with_tools"])
    async def test_every_LLMTier_reaches_the_Router_by_its_slate_name(
        self, monkeypatch, routed, which
    ):
        from acb_llm import client
        from acb_llm.client import LLMTier

        expected = {
            LLMTier.TIER_1: "tier-fast",
            LLMTier.TIER_2: "tier-balanced",
            LLMTier.TIER_3: "tier-powerful",
        }
        for tier, name in expected.items():
            spy = RouterSpy()
            _spy_on(monkeypatch, spy)
            if which == "complete":
                await client.complete(tier=tier, messages=MESSAGES, max_tokens=8)
            else:
                await client.complete_with_tools(
                    tier=tier, messages=MESSAGES, tools=[], max_tokens=8
                )
            sent = spy.calls[0][0]["model"]
            assert sent == name, f"{tier.value} reached the Router as {sent!r}"
            assert sent in _CONSOLE_CHAT_TIERS

    def test_a_slate_name_or_model_id_passes_through(self):
        from acb_llm.routed import router_tier

        assert router_tier("tier-balanced") == "tier-balanced"
        assert router_tier("deepseek/deepseek-v4-pro") == "deepseek/deepseek-v4-pro"
        assert router_tier("stt") == "tier-stt"

    def test_the_slate_names_are_the_ones_the_Console_seeds(self):
        """The set above is not a guess: the Console ladder seeds it."""
        import pathlib

        sql = "".join(
            p.read_text(encoding="utf-8")
            for p in (pathlib.Path(__file__).resolve().parents[2]
                      / "infra/customer_console").glob("*.sql")
        )
        for name in _CONSOLE_CHAT_TIERS | {"tier-stt"}:
            assert f"'{name}'" in sql, f"{name} is not seeded in tier_catalog"


class TestTheAgentPathsBillToo:
    """🔴 `acompletion_with_fallback`'s own comment says *"client.complete
    covers agent runs"*, so agent runs were the biggest remaining hole. An
    agent loop makes many calls, so it cost most while billing least."""

    def _tier(self):
        from acb_llm.client import LLMTier

        return next(iter(LLMTier))

    async def test_complete_reaches_the_Router_and_still_returns_TEXT(
        self, monkeypatch, routed
    ):
        """⚠️ `complete` answers a string. Returning the response object under
        the flag would break every agent at once, and only when billing is on
        — the hardest kind of break to attribute."""
        from acb_llm import client

        spy = RouterSpy()
        _spy_on(monkeypatch, spy)

        async def _boom(*a, **k):
            raise AssertionError("an agent call reached litellm, so it billed nobody")

        # The bound name, for the reason the flag-off test below records.
        monkeypatch.setattr(client, "acompletion", _boom)

        out = await client.complete(
            tier=self._tier(), messages=MESSAGES, max_tokens=64
        )
        assert out == "hi"
        assert isinstance(out, str)
        assert spy.calls, "the agent path never reached the Router"

    async def test_complete_with_tools_routes_and_FORWARDS_the_tools(
        self, monkeypatch, routed
    ):
        """🔴 The clause that corrected a wrong belief.

        I first left this on the direct path, believing `CompletionRequest`
        had no `tools` field. It has. Tool-calling turns are the longest-lived
        completions in the tree, so that belief would have left the most
        expensive calls free.
        """
        from acb_llm import client

        tool_body = dict(OK_BODY)
        tool_body["choices"] = [
            {
                "index": 0,
                "message": {
                    "role": "assistant", "content": None,
                    "tool_calls": [
                        {
                            "id": "call_1", "type": "function",
                            "function": {"name": "search", "arguments": "{}"},
                        }
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ]
        spy = RouterSpy(200, tool_body)
        _spy_on(monkeypatch, spy)

        async def _boom(*a, **k):
            raise AssertionError("a tool-calling turn reached litellm, unbilled")

        monkeypatch.setattr(client, "acompletion", _boom)

        tools = [{"type": "function", "function": {"name": "search"}}]
        out = await client.complete_with_tools(
            tier=self._tier(), messages=MESSAGES, tools=tools, max_tokens=64
        )
        payload, _ = spy.calls[0]
        assert payload["tools"] == tools
        assert payload["tool_choice"] == "auto"
        # ⚠️ The contract is a JSON-serialisable dict — the caller feeds it
        # straight back into `messages` for the next turn.
        assert isinstance(out, dict)
        assert out["tool_calls"][0]["function"]["name"] == "search"

    async def test_the_agent_payloads_VALIDATE_against_the_real_model(
        self, monkeypatch, routed
    ):
        """The same R8-shaped fence as the completion path: a stub agrees with
        whatever it is handed, so the pydantic model is the judge."""
        pytest.importorskip("fastapi")
        from customer_console.main import CompletionRequest

        from acb_llm import client

        spy = RouterSpy()
        _spy_on(monkeypatch, spy)
        await client.complete(tier=self._tier(), messages=MESSAGES, max_tokens=64)
        CompletionRequest(**spy.calls[0][0])

        spy2 = RouterSpy(200, OK_BODY)
        _spy_on(monkeypatch, spy2)
        await client.complete_with_tools(
            tier=self._tier(), messages=MESSAGES,
            tools=[{"type": "function", "function": {"name": "x"}}],
            max_tokens=64,
        )
        CompletionRequest(**spy2.calls[0][0])

    @pytest.mark.direct_arm_guard
    async def test_with_the_flag_OFF_the_agent_paths_are_untouched(
        self, monkeypatch
    ):
        """⚠️ A dev box with the flag off behaves as before H-171.

        Task 3554: outside dev the call raises
        (``TestCompleteIsClosedOutsideDev``)."""
        monkeypatch.setenv("ACB_ENV", "dev")
        monkeypatch.setenv("ROUTER_SERVING_ENABLED", "0")
        get_settings.cache_clear()

        from acb_llm import client

        seen: list[str] = []

        async def _fake(*, model, **k):
            seen.append(model)
            return {"choices": [{"message": {"content": "direct"}}]}

        # 🔴 **`acb_llm.client.acompletion`, NOT `litellm.acompletion`.**
        # `client.py` binds the name at import time (`from litellm import
        # acompletion`), so patching the module attribute leaves the bound
        # reference untouched. The first version of this test did that and
        # made a REAL DeepSeek call, which failed on authentication — a test
        # that spends money is worse than one that fails.
        monkeypatch.setattr(client, "acompletion", _fake)
        try:
            await client.complete(
                tier=self._tier(), messages=MESSAGES, max_tokens=64
            )
        finally:
            get_settings.cache_clear()
        assert seen, "the direct agent path stopped working"


class TestTheSystemOneDecideIsRouted:
    """WS-45 S1 (D90, ``ai_tier_routing.md`` §6.5): the System-1 ``decide``.

    Its request goes to the gateway's ``/v1``, named ``tier-fast`` and
    attributed to the CALLING agent, and only on a box that routes. A box
    whose flag is on but whose Router is not wired sends nothing, so the call
    can never reach a vendor directly.
    """

    @staticmethod
    def _wire(monkeypatch) -> list[Any]:
        import httpx
        import openai

        seen: list[Any] = []

        def answer(request):
            seen.append(request)
            return httpx.Response(200, json={
                "id": "s1", "object": "chat.completion", "created": 0, "model": "p",
                "choices": [{"index": 0, "finish_reason": "stop", "message": {
                    "role": "assistant",
                    "content": '{"answers": [{"id": "q", "choice": "yes", '
                               '"confidence": 0.9, "reason": "r"}]}',
                }}],
            })

        monkeypatch.setattr(
            openai, "DefaultAsyncHttpxClient",
            lambda **kw: httpx.AsyncClient(transport=httpx.MockTransport(answer), **kw),
        )
        return seen

    async def test_a_routed_box_sends_one_attributed_tier_fast_request(
        self, monkeypatch, routed
    ):
        from acb_common import bind_run_context, clear_run_context
        from acb_skills.decide_tools import system_one_decide

        seen = self._wire(monkeypatch)
        bind_run_context(run_id="run-r", agent="projects-assistant")
        try:
            out = await system_one_decide(question="Is it?", context="x")
        finally:
            clear_run_context()
        assert out.endswith("yes (confidence 0.90) — r")
        assert len(seen) == 1
        assert str(seen[0].url).endswith("/v1/chat/completions")
        assert json.loads(seen[0].content)["model"] == "tier-fast"
        assert seen[0].headers["X-CC-Agent"] == "projects-assistant"
        assert seen[0].headers["X-CC-Run"] == "run-r"

    async def test_an_unwired_box_sends_nothing(self, monkeypatch, routed):
        from acb_skills.decide_tools import UNAVAILABLE, system_one_decide

        monkeypatch.setenv("CUSTOMER_CONSOLE_URL", "")
        get_settings.cache_clear()
        seen = self._wire(monkeypatch)
        assert await system_one_decide(question="Is it?", context="x") == UNAVAILABLE
        assert seen == []


# ── Task 3554 (2449 S3): the direct arm is closed outside dev ───────────────
#
# 🔴 With routing off, each of the five seams called a vendor with the
# gateway's own key, and nobody billed the call. Outside ``ACB_ENV=dev`` each
# seam now raises ``ConsoleRouterUnavailable("unrouted")`` (the ``/v1`` door
# answers the Router's 502), before any key loads or litellm runs.
#
# ⚠️ Each class carries ``direct_arm_guard``, so the autouse fixture
# ``_allow_the_direct_ai_arm`` (``tests/conftest.py``) leaves the real guard.

CONSOLE_URL = "https://console.test"
ORG_KEY = "cc_live_fixture_notarealsecret"

#: The states of a box in which the Router does not take a call:
#: ``(ROUTER_SERVING_ENABLED, CUSTOMER_CONSOLE_URL)``.
_UNROUTED = [
    pytest.param("0", CONSOLE_URL, id="flag-off-wired"),
    pytest.param("0", "", id="flag-off-unwired"),
    pytest.param("1", "", id="flag-on-unwired"),
]
#: Production. An unset ``ACB_ENV`` reads as ``prod`` (H-90).
_PRODUCTION = [pytest.param(None, id="env-unset"), pytest.param("prod", id="env-prod")]


def _box(monkeypatch, tmp_path, *, env: str | None, flag: str, url: str) -> None:
    """Configure the box. The settings read ``.env`` from the working folder,
    so a production case runs in an empty folder: a developer's ``.env`` with
    ``ACB_ENV=dev`` must not decide the case."""
    if env is None:
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("ACB_ENV", raising=False)
    else:
        monkeypatch.setenv("ACB_ENV", env)
    monkeypatch.setenv("ROUTER_SERVING_ENABLED", flag)
    monkeypatch.setenv("CUSTOMER_CONSOLE_URL", url)
    monkeypatch.setenv("CUSTOMER_CONSOLE_ORG_KEY", ORG_KEY)
    monkeypatch.delenv("CUSTOMER_CONSOLE_DEPLOYMENT_KEY", raising=False)
    get_settings.cache_clear()


class _Spies:
    """Counts the vendor calls, the key loads and the Router calls."""

    def __init__(self) -> None:
        self.litellm_calls = 0
        self.keys_loaded = 0
        self.router = RouterSpy()


@pytest.fixture
def _fresh_settings():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


class _ClosedOutsideDev:
    """The cases of one seam. A subclass names the seam and calls it."""

    seam = ""

    async def _call(self) -> Any:
        raise NotImplementedError

    def _local_answer(self, stream: bool) -> Any:
        return {"choices": [{"message": {"role": "assistant", "content": "direct"}}]}

    def _spies(self, monkeypatch) -> _Spies:
        import litellm
        from acb_llm import client
        from acb_llm import context as ctx

        spies = _Spies()

        async def _vendor(*_a, stream=False, **_k):
            spies.litellm_calls += 1
            return self._local_answer(stream)

        async def _keys() -> None:
            spies.keys_loaded += 1

        # `client.py` binds `acompletion` at import, and `context.py` takes
        # it from litellm at call time. Both names are spied.
        monkeypatch.setattr(litellm, "acompletion", _vendor)
        monkeypatch.setattr(client, "acompletion", _vendor)
        monkeypatch.setattr(client, "_ensure_keys_loaded", _keys)
        monkeypatch.setattr(litellm, "stream_chunk_builder", lambda *a, **k: None)
        monkeypatch.setattr(client, "ensure_model_registered", lambda _m: None)
        monkeypatch.setattr(ctx, "resolve_underlying_model", lambda m: m)
        _spy_on(monkeypatch, spies.router)
        return spies

    @pytest.mark.parametrize(("flag", "url"), _UNROUTED)
    @pytest.mark.parametrize("env", _PRODUCTION)
    async def test_production_refuses_with_no_vendor_call_and_no_key(
        self, monkeypatch, tmp_path, _fresh_settings, env, flag, url
    ):
        from acb_auth.console_resolve import ConsoleRouterUnavailable

        _box(monkeypatch, tmp_path, env=env, flag=flag, url=url)
        spies = self._spies(monkeypatch)
        with pytest.raises(ConsoleRouterUnavailable, match="unrouted"):
            await self._call()
        assert spies.litellm_calls == 0, "the call reached a vendor, unbilled"
        assert spies.keys_loaded == 0, "the call loaded a vendor key"
        assert spies.router.calls == []

    async def test_one_log_line_over_two_calls_with_no_secret(
        self, monkeypatch, tmp_path, _fresh_settings
    ):
        import structlog
        from acb_auth.console_resolve import ConsoleRouterUnavailable
        from acb_llm import routed as routed_mod

        monkeypatch.setattr(routed_mod, "_REFUSAL_LOGGED", False)
        _box(monkeypatch, tmp_path, env=None, flag="0", url=CONSOLE_URL)
        self._spies(monkeypatch)
        with structlog.testing.capture_logs() as logs:
            for _ in range(2):
                with pytest.raises(ConsoleRouterUnavailable):
                    await self._call()
        lines = [e for e in logs if e.get("event") == "ai.route_refused_unwired"]
        assert len(lines) == 1, lines
        line = lines[0]
        assert line["seam"] == self.seam
        assert line["router_serving"] is False
        assert line["router_wired"] is True
        assert line["acb_env"] == "prod"
        assert ORG_KEY not in repr(line), "the log line holds a secret"

    async def test_a_wired_production_box_reaches_the_Router(
        self, monkeypatch, tmp_path, _fresh_settings
    ):
        """🔴 The guard sits AFTER the Router branch. Production with the flag
        on and a wired Console sends the call to the Router, with no refusal,
        no vendor call and no key load. Mutation: move ``refuse_direct_arm``
        above ``if routing_is_on():`` → this case raises → red."""
        _box(monkeypatch, tmp_path, env=None, flag="1", url=CONSOLE_URL)
        spies = self._spies(monkeypatch)
        await self._call()
        assert len(spies.router.calls) == 1, "the Router did not get the call"
        assert spies.litellm_calls == 0, "a routed call reached a vendor"
        assert spies.keys_loaded == 0, "a routed call loaded a vendor key"

    async def test_a_dev_box_still_takes_the_direct_arm(
        self, monkeypatch, tmp_path, _fresh_settings
    ):
        _box(monkeypatch, tmp_path, env="dev", flag="0", url="")
        spies = self._spies(monkeypatch)
        await self._call()
        assert spies.litellm_calls == 1, "the dev box lost its direct arm"
        assert spies.keys_loaded == 1
        assert spies.router.calls == []

    async def test_a_settings_read_that_fails_refuses(self, monkeypatch, _fresh_settings):
        """🔴 ``routing_is_on`` reads a failure as "off". The guard must not
        read it as "dev"."""
        from acb_auth.console_resolve import ConsoleRouterUnavailable
        from acb_common import settings as settings_mod
        from acb_llm import routed as routed_mod

        def _unreadable():
            raise RuntimeError("the settings do not read")

        monkeypatch.setattr(routed_mod, "routing_is_on", lambda: False)
        spies = self._spies(monkeypatch)
        monkeypatch.setattr(settings_mod, "get_settings", _unreadable)
        with pytest.raises(ConsoleRouterUnavailable, match="unrouted"):
            await self._call()
        assert spies.litellm_calls == 0
        assert spies.keys_loaded == 0


@pytest.mark.direct_arm_guard
class TestFallbackSeamIsClosedOutsideDev(_ClosedOutsideDev):
    seam = "acompletion_with_fallback"

    def _local_answer(self, stream: bool) -> Any:
        from litellm import ModelResponse

        return ModelResponse(**OK_BODY)

    async def _call(self) -> Any:
        from acb_llm.context import acompletion_with_fallback

        return await acompletion_with_fallback(
            model=TIER, fallback_model="tier-powerful", messages=MESSAGES,
            max_tokens=64,
        )


@pytest.mark.direct_arm_guard
class TestStreamSeamIsClosedOutsideDev(_ClosedOutsideDev):
    seam = "acompletion_stream_text"

    def _spies(self, monkeypatch) -> _Spies:
        """The routed streaming arm makes a STREAM call (task 3542), not the
        buffered one. The Router spy of this seam is a :class:`StreamSpy`, so
        ``spies.router.calls`` counts the Console streams."""
        from acb_auth import console_resolve

        spies = super()._spies(monkeypatch)
        spies.router = StreamSpy([_frame(content="routed"), DONE_FRAME])
        monkeypatch.setattr(
            console_resolve, "stream_completion_on_console", spies.router)
        return spies

    def _local_answer(self, stream: bool) -> Any:
        assert stream, "the streaming arm asked for no stream"
        chunk = type("C", (), {"choices": [type("Ch", (), {
            "delta": type("D", (), {"content": "direct",
                                    "reasoning_content": None})()
        })()]})()

        class _Stream:
            def __init__(self):
                self._chunks = [chunk]

            def __aiter__(self):
                return self

            async def __anext__(self):
                if not self._chunks:
                    raise StopAsyncIteration
                return self._chunks.pop(0)

        return _Stream()

    async def _call(self) -> Any:
        from acb_llm.context import acompletion_stream_text

        return await acompletion_stream_text(
            model=TIER, messages=MESSAGES, max_tokens=64)


@pytest.mark.direct_arm_guard
class TestCompleteIsClosedOutsideDev(_ClosedOutsideDev):
    seam = "complete"

    async def _call(self) -> Any:
        from acb_llm import client

        return await client.complete(
            tier=client.LLMTier.TIER_1, messages=MESSAGES, max_tokens=64)


@pytest.mark.direct_arm_guard
class TestCompleteWithToolsIsClosedOutsideDev(_ClosedOutsideDev):
    seam = "complete_with_tools"

    async def _call(self) -> Any:
        from acb_llm import client

        return await client.complete_with_tools(
            tier=client.LLMTier.TIER_1, messages=MESSAGES,
            tools=[{"type": "function", "function": {"name": "x"}}],
            max_tokens=64,
        )


class _V1Request:
    """The two things ``_handle_chat_completions`` reads off a request."""

    def __init__(self, body: dict[str, Any]) -> None:
        self._body = body
        self.headers: dict[str, str] = {}

    async def json(self) -> dict[str, Any]:
        return self._body


#: The body of the Router's 502 (``v1_compat._router_unavailable``).
_ROUTER_502 = {"error": {
    "message": "the AI Router is unavailable",
    "type": "ConsoleRouterUnavailable",
    "code": 502,
}}


@pytest.mark.direct_arm_guard
class TestTheV1DoorIsClosedOutsideDev:
    """The gateway ``/v1`` door answers the Router's 502, and calls nothing."""

    seam = "v1_compat"

    @staticmethod
    def _v1():
        pytest.importorskip("fastapi")
        from gateway.routes import v1_compat

        return v1_compat

    def _spies(self, monkeypatch, v1) -> _Spies:
        import litellm
        from acb_auth import console_resolve

        spies = _Spies()

        async def _vendor(*_a, **_k):
            spies.litellm_calls += 1
            return {"choices": [{"message": {"role": "assistant", "content": "direct"}}]}

        async def _keys() -> None:
            spies.keys_loaded += 1

        def _no_stream(*_a, **_k):
            raise AssertionError("the door streamed from the Router, unrouted")

        monkeypatch.setattr(litellm, "acompletion", _vendor)
        monkeypatch.setattr(v1, "_ensure_keys_loaded", _keys)
        monkeypatch.setattr(v1, "ROUTER_FAILED_AT", 0.0)
        monkeypatch.setattr(console_resolve, "stream_completion_on_console", _no_stream)
        _spy_on(monkeypatch, spies.router)
        return spies

    @pytest.mark.parametrize("stream", [False, True])
    @pytest.mark.parametrize(("flag", "url"), _UNROUTED)
    @pytest.mark.parametrize("env", _PRODUCTION)
    async def test_production_answers_the_router_502(
        self, monkeypatch, tmp_path, _fresh_settings, env, flag, url, stream
    ):
        v1 = self._v1()
        _box(monkeypatch, tmp_path, env=env, flag=flag, url=url)
        spies = self._spies(monkeypatch, v1)
        body = {"model": "tier-balanced", "messages": MESSAGES, "stream": stream}
        got = await v1._handle_chat_completions(_V1Request(body))
        assert got.status_code == 502
        assert json.loads(got.body) == _ROUTER_502
        assert spies.litellm_calls == 0, "the door reached a vendor, unbilled"
        assert spies.keys_loaded == 0, "the door loaded a vendor key"
        assert spies.router.calls == []
        assert v1.ROUTER_FAILED_AT > 0, "WAC-13 reads the refusal as an outage"

    async def test_one_log_line_over_two_calls_with_no_secret(
        self, monkeypatch, tmp_path, _fresh_settings
    ):
        import structlog
        from acb_llm import routed as routed_mod

        v1 = self._v1()
        monkeypatch.setattr(routed_mod, "_REFUSAL_LOGGED", False)
        _box(monkeypatch, tmp_path, env=None, flag="0", url=CONSOLE_URL)
        self._spies(monkeypatch, v1)
        body = {"model": "tier-balanced", "messages": MESSAGES}
        with structlog.testing.capture_logs() as logs:
            for _ in range(2):
                got = await v1._handle_chat_completions(_V1Request(body))
                assert got.status_code == 502
        lines = [e for e in logs if e.get("event") == "ai.route_refused_unwired"]
        assert len(lines) == 1, lines
        assert lines[0]["seam"] == self.seam
        assert lines[0]["router_wired"] is True
        assert ORG_KEY not in repr(lines[0]), "the log line holds a secret"

    @pytest.mark.parametrize("stream", [False, True])
    async def test_a_wired_production_box_reaches_the_Router(
        self, monkeypatch, tmp_path, _fresh_settings, stream
    ):
        """🔴 The refusal sits AFTER the Router hop. Production with the flag
        on and a wired Console serves through the Router: no 502, no vendor
        call, no key load."""
        from acb_auth import console_resolve

        v1 = self._v1()
        _box(monkeypatch, tmp_path, env=None, flag="1", url=CONSOLE_URL)
        spies = self._spies(monkeypatch, v1)
        streamed: list[dict[str, Any]] = []

        async def _frames(payload, **_kw):
            streamed.append(payload)
            yield b'data: {"choices": []}\n\n'

        monkeypatch.setattr(console_resolve, "stream_completion_on_console", _frames)
        body = {"model": "tier-balanced", "messages": MESSAGES, "stream": stream}
        got = await v1._handle_chat_completions(_V1Request(body))
        assert getattr(got, "status_code", 200) == 200, "a routed call got a refusal"
        sent = len(streamed) if stream else len(spies.router.calls)
        assert sent == 1, "the Router did not get the call"
        assert spies.litellm_calls == 0, "a routed call reached a vendor"
        assert spies.keys_loaded == 0, "a routed call loaded a vendor key"
        assert v1.ROUTER_FAILED_AT == 0.0

    async def test_a_dev_box_still_takes_the_local_arm(
        self, monkeypatch, tmp_path, _fresh_settings
    ):
        v1 = self._v1()
        _box(monkeypatch, tmp_path, env="dev", flag="0", url="")
        spies = self._spies(monkeypatch, v1)
        body = {"model": "tier-balanced", "messages": MESSAGES}
        got = await v1._handle_chat_completions(_V1Request(body))
        assert isinstance(got, dict), got
        assert spies.litellm_calls == 1, "the dev box lost its local arm"
        assert spies.keys_loaded == 1
        assert spies.router.calls == []

    async def test_a_settings_read_that_fails_refuses(self, monkeypatch, _fresh_settings):
        from acb_common import settings as settings_mod

        v1 = self._v1()

        def _unreadable():
            raise RuntimeError("the settings do not read")

        spies = self._spies(monkeypatch, v1)
        monkeypatch.setattr(v1, "_router_should_serve", lambda _body: False)
        monkeypatch.setattr(settings_mod, "get_settings", _unreadable)
        got = await v1._handle_chat_completions(
            _V1Request({"model": "tier-balanced", "messages": MESSAGES}))
        assert got.status_code == 502
        assert spies.litellm_calls == 0
        assert spies.keys_loaded == 0


@pytest.mark.direct_arm_guard
class TestTheGuardItself:
    """``refuse_direct_arm`` and ``direct_arm_allowed``, with no seam."""

    @pytest.mark.parametrize("env", ["dev", "prod", "staging", None, "development"])
    def test_only_dev_allows(self, monkeypatch, tmp_path, _fresh_settings, env):
        """An unknown value fails the settings read, and that refuses too."""
        from acb_llm.routed import direct_arm_allowed

        monkeypatch.chdir(tmp_path)
        if env is None:
            monkeypatch.delenv("ACB_ENV", raising=False)
        else:
            monkeypatch.setenv("ACB_ENV", env)
        get_settings.cache_clear()
        assert direct_arm_allowed() is (env == "dev")

    def test_the_refusal_is_the_router_outage_type(self, monkeypatch, tmp_path, _fresh_settings):
        """So a caller that reads an outage (``is_router_outage``, the email
        draft of task 3541) reads this refusal too."""
        from acb_auth.console_resolve import ConsoleRouterUnavailable
        from acb_llm.routed import is_router_outage, refuse_direct_arm

        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("ACB_ENV", raising=False)
        get_settings.cache_clear()
        with pytest.raises(ConsoleRouterUnavailable) as caught:
            refuse_direct_arm("a-test")
        assert is_router_outage(caught.value)
        assert str(caught.value) == "unrouted"
