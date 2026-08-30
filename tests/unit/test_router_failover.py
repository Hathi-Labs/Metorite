"""WS-31 slice 10 — the Router walks the chain when a step fails.

Spec: ``project-docs/specs/ai_metering_and_analytics.md`` §3.5 · D-AI-5.

⚠️ **NO DATABASE, deliberately, and that is the point of `call_chain` living in
`router.py`.** Failover ORDER is a property of a list and a policy. Testing it
through the route would need FastAPI, a real Postgres and a provider stub — and
on 2026-08-30 we measured that 843 R8-gated tests skip silently on a developer
machine, so a test that needs a database is a test that usually does not run.
This file runs everywhere, always.

⚠️ **The subject is a chain that fails over WRONGLY**, not one that works:

  1. Retrying a request the provider was right to reject. Every step fails the
     same way, and each attempt costs money.
  2. Retrying a second model from a vendor whose key just answered 401. It
     presents the same key of ours and fails identically.
  3. Billing the step we INTENDED rather than the one that answered.
"""
from __future__ import annotations

import asyncio
from typing import Any

import pytest

from customer_console.router import (
    ResolvedTier,
    UpstreamFailed,
    call_chain,
    is_retryable,
    open_stream_chain,
    set_provider_call,
)


class Upstream(Exception):
    """A provider error, shaped like the ones litellm raises."""

    def __init__(self, status: int | None) -> None:
        super().__init__(f"upstream {status}")
        if status is not None:
            self.status_code = status


def step(model: str, tier: str = "balanced", task: str = "chat") -> ResolvedTier:
    return ResolvedTier(tier=tier, model=model, task=task)


@pytest.fixture
def provider():
    """A stub that answers, or raises, per model — and records the order."""
    plan: dict[str, Any] = {}
    seen: list[str] = []

    async def _call(**kwargs: Any) -> Any:
        model = kwargs["model"]
        seen.append(model)
        outcome = plan.get(model, {"ok": True})
        if not outcome.get("ok"):
            raise Upstream(outcome.get("status"))
        return {"model": model, "answered": True}

    set_provider_call(_call)
    yield plan, seen
    # ⚠️ Restored, because `_PROVIDER_CALL` is process-global. A stub left
    # installed makes the NEXT suite's provider calls silently answer here.
    async def _refuse(**kwargs: Any) -> Any:  # pragma: no cover
        raise AssertionError("provider call escaped its test")

    set_provider_call(_refuse)


def walk(attempts, notes=None):
    def kwargs_for(s: ResolvedTier) -> dict[str, Any]:
        return {"model": s.model}

    return asyncio.run(call_chain(attempts, kwargs_for, notes))


# ── The policy ──────────────────────────────────────────────────────────────


class TestWhatIsWorthRetrying:
    def test_a_rate_limit_and_a_server_error_are(self):
        assert is_retryable(429)
        assert is_retryable(500)
        assert is_retryable(503)
        assert is_retryable(529)

    def test_a_bad_request_is_NOT(self):
        # Every step fails a malformed request identically. Walking the chain
        # spends money to learn nothing, three times.
        assert not is_retryable(400)
        assert not is_retryable(404)
        assert not is_retryable(422)

    def test_no_status_IS_retryable(self):
        # ⚠️ A timeout, a DNS failure and a dropped connection all arrive with
        # no status — and those are exactly the provider-down shapes a chain
        # exists for. Treating "no status" as terminal would make failover fire
        # only for the errors that least need it.
        assert is_retryable(None)

    def test_an_oversized_payload_is_not_retried(self):
        # A bigger-window model might accept it, and each attempt re-uploads
        # the whole thing. Paying three times to maybe fit is the wrong default.
        assert not is_retryable(413)


# ── The walk ────────────────────────────────────────────────────────────────


class TestTheWalk:
    def test_a_healthy_first_step_is_the_only_call(self, provider):
        plan, seen = provider
        response, served = walk([step("a/one"), step("b/two")])
        assert response["model"] == "a/one"
        assert served.model == "a/one"
        assert seen == ["a/one"], "a working chain must not call its backup"

    def test_it_moves_to_the_backup_when_the_first_is_overloaded(self, provider):
        plan, seen = provider
        plan["a/one"] = {"ok": False, "status": 529}
        response, served = walk([step("a/one"), step("b/two")])
        assert response["model"] == "b/two"
        assert seen == ["a/one", "b/two"]

    def test_it_returns_the_step_that_ANSWERED(self, provider):
        # The billing property. `_record_completion` prices from this value, so
        # an Opus request that fell over to Haiku must cost Haiku. Returning the
        # intended step would overcharge for a model the customer did not get.
        plan, seen = provider
        plan["expensive/opus"] = {"ok": False, "status": 500}
        _, served = walk([step("expensive/opus"), step("cheap/haiku")])
        assert served.model == "cheap/haiku"

    def test_a_terminal_status_stops_the_walk_dead(self, provider):
        plan, seen = provider
        plan["a/one"] = {"ok": False, "status": 400}
        with pytest.raises(UpstreamFailed) as exc:
            walk([step("a/one"), step("b/two")])
        assert exc.value.status == 400
        assert seen == ["a/one"], "a 400 must not be retried on the backup"

    def test_a_401_strikes_off_the_WHOLE_vendor(self, provider):
        # Our key for that vendor is bad. Every other model from it presents
        # the same key and fails the same way, so trying one spends a round
        # trip to learn what we already know.
        plan, seen = provider
        plan["a/one"] = {"ok": False, "status": 401}
        response, served = walk([step("a/one"), step("a/two"), step("b/three")])
        assert seen == ["a/one", "b/three"]
        assert served.model == "b/three"

    def test_a_429_does_NOT_strike_off_the_vendor(self, provider):
        # A rate limit is per model and per capacity pool. A second model from
        # the same vendor is a legitimate next try, unlike a bad key.
        plan, seen = provider
        plan["a/one"] = {"ok": False, "status": 429}
        _, served = walk([step("a/one"), step("a/two")])
        assert seen == ["a/one", "a/two"]
        assert served.model == "a/two"

    def test_every_step_failing_raises_with_the_LAST_status(self, provider):
        plan, seen = provider
        plan["a/one"] = {"ok": False, "status": 500}
        plan["b/two"] = {"ok": False, "status": 503}
        with pytest.raises(UpstreamFailed) as exc:
            walk([step("a/one"), step("b/two")])
        assert exc.value.status == 503
        assert seen == ["a/one", "b/two"]

    def test_a_timeout_with_no_status_still_falls_over(self, provider):
        plan, seen = provider
        plan["a/one"] = {"ok": False, "status": None}
        _, served = walk([step("a/one"), step("b/two")])
        assert served.model == "b/two"

    def test_a_one_step_chain_raises_rather_than_looping(self, provider):
        plan, seen = provider
        plan["a/one"] = {"ok": False, "status": 500}
        with pytest.raises(UpstreamFailed):
            walk([step("a/one")])
        assert seen == ["a/one"]

    def test_the_error_carries_a_STATUS_and_no_provider_text(self, provider):
        # ⚠️ The upstream message can quote the request, and the request can
        # carry customer content. Only the status leaves the walk.
        plan, seen = provider
        plan["a/one"] = {"ok": False, "status": 500}
        with pytest.raises(UpstreamFailed) as exc:
            walk([step("a/one")])
        assert "upstream 500" not in str(exc.value)


class TestTheRecord:
    def test_a_failover_is_announced_with_both_ends_and_the_reason(self, provider):
        # 📌 `usage_event` has no column for the step that served, so this
        # callback is the only evidence a chain ever earned its keep.
        plan, seen = provider
        plan["a/one"] = {"ok": False, "status": 429}
        notes: list[tuple[str, str, int | None]] = []
        walk(
            [step("a/one"), step("b/two")],
            lambda frm, to, st: notes.append((frm.model, to.model, st)),
        )
        assert notes == [("a/one", "b/two", 429)]

    def test_a_healthy_chain_announces_nothing(self, provider):
        notes: list[Any] = []
        walk([step("a/one")], lambda *a: notes.append(a))
        assert notes == []

    def test_the_note_names_the_step_actually_tried_next(self, provider):
        # ⚠️ Not simply "the next in the list". A struck-off vendor is skipped,
        # and a note naming a step nobody tried sends the reader to the wrong
        # model.
        plan, seen = provider
        plan["a/one"] = {"ok": False, "status": 401}
        notes: list[tuple[str, str, int | None]] = []
        walk(
            [step("a/one"), step("a/two"), step("b/three")],
            lambda frm, to, st: notes.append((frm.model, to.model, st)),
        )
        assert notes == [("a/one", "b/three", 401)]


# ── The STREAM walk (§8.6, slice 11) ────────────────────────────────────────


@pytest.fixture
def stream_provider():
    """A stub whose call returns an async iterator of frames, per model.

    Each model gets a plan: ``frames`` to emit, ``open`` to raise on the call
    itself, or ``after_open`` to raise on the first ``__anext__``. The last is
    the outage shape a plain open check cannot see.
    """
    plan: dict[str, Any] = {}
    seen: list[str] = []

    async def _call(**kwargs: Any) -> Any:
        model = kwargs["model"]
        seen.append(model)
        outcome = plan.get(model, {})
        if "open" in outcome:
            raise Upstream(outcome["open"])
        frames = outcome.get("frames", [f"{model}-1".encode()])
        after = outcome.get("after_open")

        async def _gen():
            if after is not None:
                raise Upstream(after)
            for f in frames:
                yield f

        return _gen()

    set_provider_call(_call)
    yield plan, seen

    async def _refuse(**kwargs: Any) -> Any:  # pragma: no cover
        raise AssertionError("provider call escaped its test")

    set_provider_call(_refuse)


def open_stream(attempts, notes=None):
    def kwargs_for(s: ResolvedTier) -> dict[str, Any]:
        return {"model": s.model}

    return asyncio.run(open_stream_chain(attempts, kwargs_for, notes))


def open_and_drain(attempts) -> tuple[list[Any], list[Any]]:
    """Open the stream and read the REST of it, on ONE event loop.

    ⚠️ **Two `asyncio.run` calls would report a lie here.** Closing a loop runs
    `shutdown_asyncgens()`, which throws `GeneratorExit` into the provider
    stream — so a second call would find an exhausted source and the test would
    read as "no chunks after the head". The route has the same constraint, and
    answers it with `anyio.from_thread.run` onto the serving loop.
    """
    async def _go():
        head, source, _ = await open_stream_chain(
            attempts, lambda s: {"model": s.model})
        return head, [chunk async for chunk in source]

    return asyncio.run(_go())


class TestTheStreamWalk:
    """A stream fails over before its first frame, and by the SAME policy.

    🔴 **The subject is the boundary.** Every failure up to the first frame may
    fail over. The first frame is the commitment, because the 200 status line
    goes out with it and no retry after that can be spliced in honestly.
    """

    def test_a_healthy_first_step_is_the_only_call(self, stream_provider):
        _, seen = stream_provider
        head, _, served = open_stream([step("a/one"), step("b/two")])

        assert served.model == "a/one"
        assert head == [b"a/one-1"]
        assert seen == ["a/one"], "a working chain must not open its backup"

    def test_a_529_before_any_frame_serves_the_backup(self, stream_provider):
        plan, seen = stream_provider
        plan["a/one"] = {"open": 529}
        head, _, served = open_stream([step("a/one"), step("b/two")])

        assert served.model == "b/two"
        assert head == [b"b/two-1"]
        assert seen == ["a/one", "b/two"]

    def test_a_400_before_any_frame_stops_the_walk_dead(self, stream_provider):
        plan, seen = stream_provider
        plan["a/one"] = {"open": 400}
        with pytest.raises(UpstreamFailed) as exc:
            open_stream([step("a/one"), step("b/two")])

        assert exc.value.status == 400
        assert seen == ["a/one"], "a 400 must not be retried on the backup"

    def test_a_stream_that_OPENS_and_then_dies_still_fails_over(
            self, stream_provider):
        # 🔴 The reason the walk pulls a frame instead of only opening. A
        # provider that accepts the socket and then falls over is the common
        # outage, and an open-only check would call it a success.
        plan, seen = stream_provider
        plan["a/one"] = {"after_open": 503}
        _, _, served = open_stream([step("a/one"), step("b/two")])

        assert served.model == "b/two"
        assert seen == ["a/one", "b/two"]

    def test_a_401_strikes_off_the_WHOLE_vendor_here_too(self, stream_provider):
        # The SAME policy, not a second one. `CREDENTIAL_STATUSES` is read in
        # one function and both shapes walk through it.
        plan, seen = stream_provider
        plan["a/one"] = {"open": 401}
        _, _, served = open_stream(
            [step("a/one"), step("a/two"), step("b/three")])

        assert seen == ["a/one", "b/three"]
        assert served.model == "b/three"

    def test_every_step_failing_raises_with_the_LAST_status(
            self, stream_provider):
        plan, seen = stream_provider
        plan["a/one"] = {"open": 500}
        plan["b/two"] = {"after_open": 503}
        with pytest.raises(UpstreamFailed) as exc:
            open_stream([step("a/one"), step("b/two")])

        assert exc.value.status == 503
        assert seen == ["a/one", "b/two"]

    def test_an_EMPTY_stream_is_an_answer_and_not_a_failure(
            self, stream_provider):
        # ⚠️ A provider that completed with no content has served the request.
        # Walking on would bill a second vendor to repeat one empty answer.
        plan, seen = stream_provider
        plan["a/one"] = {"frames": []}
        head, _, served = open_stream([step("a/one"), step("b/two")])

        assert head == []
        assert served.model == "a/one"
        assert seen == ["a/one"]

    def test_the_head_leaves_the_source_at_the_SECOND_chunk(
            self, stream_provider):
        # ⚠️ The head is already consumed. A caller that replays it and then
        # drains the source must see each chunk once — the two failures this
        # mechanism can have are a duplicated first chunk and a lost one.
        plan, _ = stream_provider
        plan["a/one"] = {"frames": [b"one", b"two", b"three"]}
        head, rest = open_and_drain([step("a/one")])

        assert head == [b"one"]
        assert rest == [b"two", b"three"]
        assert head + rest == [b"one", b"two", b"three"]

    def test_a_stream_failover_is_announced_like_any_other(
            self, stream_provider):
        plan, _ = stream_provider
        plan["a/one"] = {"after_open": 429}
        notes: list[tuple[str, str, int | None]] = []
        open_stream(
            [step("a/one"), step("b/two")],
            lambda frm, to, st: notes.append((frm.model, to.model, st)),
        )

        assert notes == [("a/one", "b/two", 429)]
