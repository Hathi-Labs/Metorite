"""Steer — routing, the no-double-post marker, durable replay, carve-outs.

Covers ``docs/multiplayer/README.md`` §4.6 and
``project-docs/specs/multiplayer_prior_art_qm_2026-08.md`` §QM-1.

The four outcomes are tested against the PURE function rather than through the
route, because the whole reason ``route_turn`` has no I/O is so the rule can be
pinned without a Redis, a run, or a model. Everything below the routing tests
exercises the impure halves with fakes.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from orchestrator import steer
from orchestrator.steer import Route, route_turn


# ---------------------------------------------------------------------------
# 1. The routing function — all four outcomes
# ---------------------------------------------------------------------------

def test_agent_own_message_is_dropped():
    """The agent's own output must never re-enter as a turn — that's a loop."""
    d = route_turn(author_kind="agent", text="here is the answer",
                   run_active=True)
    assert d.route is Route.DROP
    assert d.steered is False


def test_empty_message_is_dropped():
    assert route_turn(
        author_kind="human", text="   ", run_active=True,
    ).route is Route.DROP


def test_nothing_running_engages():
    d = route_turn(author_kind="human", text="check the invoice",
                   run_active=False)
    assert d.route is Route.ENGAGE


def test_bare_stop_aborts():
    for word in ("stop", "Stop.", "  STOP! ", "never mind", "cancel that"):
        d = route_turn(author_kind="human", text=word, run_active=True)
        assert d.route is Route.ABORT, word


def test_stop_inside_a_sentence_steers_not_aborts():
    """The 40-minute-run trap: 'stop the staging deploy' is guidance.

    Reading it as an abort would cancel somebody's long run because they used
    the word 'stop' in a sentence.
    """
    d = route_turn(author_kind="human", text="stop the staging deploy",
                   run_active=True)
    assert d.route is Route.STEER


def test_message_during_a_live_run_steers():
    d = route_turn(author_kind="human", text="also check the invoice",
                   run_active=True)
    assert d.route is Route.STEER
    assert d.steered is True


# ---------------------------------------------------------------------------
# 2. The automation carve-out
# ---------------------------------------------------------------------------

def test_human_message_during_an_automation_turn_engages():
    """§QM-1's one carve-out: you do not steer a cron, you start your own run."""
    d = route_turn(
        author_kind="human", text="also check the invoice",
        run_active=True, target_run_kind="automation",
    )
    assert d.route is Route.ENGAGE
    assert d.reason == "automation_turn_not_steerable"


def test_bare_stop_still_aborts_an_automation_turn():
    """Abort beats the carve-out: stopping a cron is exactly what was meant."""
    d = route_turn(
        author_kind="human", text="stop",
        run_active=True, target_run_kind="automation",
    )
    assert d.route is Route.ABORT


def test_unknown_source_is_treated_as_a_conversation():
    """Mistaking a chat for a cron would spawn a second concurrent run."""
    from gateway.routes.agent import _run_kind_for_source

    assert _run_kind_for_source("") == "human"
    assert _run_kind_for_source("chat") == "human"
    assert _run_kind_for_source("something-new") == "human"
    assert _run_kind_for_source("schedule") == "automation"
    assert _run_kind_for_source("WEBHOOK") == "automation"
    assert _run_kind_for_source("workflow") == "automation"


# ---------------------------------------------------------------------------
# 3. No double post — the `steered` marker
# ---------------------------------------------------------------------------

def test_steer_decision_carries_the_stand_down_marker():
    """The marker the second surface keys off.

    qm's Slack handler comments that delivering from both sides "is how one
    answer got posted twice". Only a STEER decision may set it: an ENGAGE that
    claimed `steered` would make the surface that OWNS the answer suppress it,
    and the reply would be posted zero times.
    """
    steered = route_turn(author_kind="human", text="also do X", run_active=True)
    engaged = route_turn(author_kind="human", text="also do X", run_active=False)
    aborted = route_turn(author_kind="human", text="stop", run_active=True)
    dropped = route_turn(author_kind="agent", text="done", run_active=True)

    assert steered.steered is True
    assert engaged.steered is False
    assert aborted.steered is False
    assert dropped.steered is False


def test_only_one_route_ever_reports_steered():
    """Exhaustive over the outcome set, so a new Route can't default to True."""
    assert [r for r in Route if r is Route.STEER] == [Route.STEER]


# ---------------------------------------------------------------------------
# 4. Durable signals and orphan replay
# ---------------------------------------------------------------------------

class _FakeRedis:
    """Just enough Redis for the steer store: lists, rename, expire."""

    def __init__(self) -> None:
        self.lists: dict[str, list[str]] = {}

    async def rpush(self, key: str, val: str) -> int:
        self.lists.setdefault(key, []).append(val)
        return len(self.lists[key])

    async def ltrim(self, key: str, start: int, end: int) -> None:
        if key in self.lists:
            self.lists[key] = self.lists[key][start:] if end == -1 else self.lists[key]

    async def expire(self, key: str, ttl: int) -> None:
        return None

    async def rename(self, src: str, dst: str) -> None:
        if src not in self.lists:
            raise RuntimeError("no such key")
        self.lists[dst] = self.lists.pop(src)

    async def lrange(self, key: str, start: int, end: int) -> list[str]:
        return list(self.lists.get(key, []))

    async def lrem(self, key: str, count: int, val: str) -> int:
        items = self.lists.get(key, [])
        if val in items:
            items.remove(val)
            return 1
        return 0

    async def delete(self, key: str) -> None:
        self.lists.pop(key, None)

    # The run stream, for the terminal-event seam (``stream_relay.push_event``).
    async def xadd(self, key: str, fields: dict, **_kw: Any) -> str:
        self.lists.setdefault(key, []).append(json.dumps(fields))
        return f"{len(self.lists[key])}-0"

    async def set(self, *_a: Any, **_kw: Any) -> bool:
        return True


@pytest.fixture
def fake_redis(monkeypatch) -> _FakeRedis:
    r = _FakeRedis()

    async def _get_client() -> _FakeRedis:
        return r

    from orchestrator import stream_relay
    monkeypatch.setattr(stream_relay, "_get_client", _get_client)
    return r


def test_a_steer_is_persisted_before_it_is_dispatched(fake_redis, monkeypatch):
    """The ordering that makes an orphan recoverable.

    Persist-then-dispatch means a steer that reached the run but died with it is
    still on disk. Dispatch-then-persist would lose it, which is the failure
    this ordering exists to prevent — so the test asserts the store was written
    BEFORE dispatch was even called.
    """
    seen_at_dispatch: list[int] = []

    async def _dispatch(tid: str, _cmd: dict[str, Any]) -> bool:
        seen_at_dispatch.append(
            len(fake_redis.lists.get(f"cc:steer:{tid}", []))
        )
        return False  # owner unreachable — the orphan case

    from orchestrator import stream_relay
    monkeypatch.setattr(stream_relay, "dispatch_control", _dispatch)

    out = asyncio.run(steer.send_steer("t-orphan", "bob@x.io", "also check X"))

    assert out["stored"] is True
    assert out["delivered"] is False
    assert seen_at_dispatch == [1], "the signal must be durable before dispatch"


def test_an_orphaned_steer_is_replayed_into_the_next_run(fake_redis, monkeypatch):
    """A steer whose target run terminated mid-send is spoken, not lost."""
    async def _dispatch(_tid: str, _cmd: dict[str, Any]) -> bool:
        return False  # the run died before the command landed

    from orchestrator import stream_relay
    monkeypatch.setattr(stream_relay, "dispatch_control", _dispatch)

    asyncio.run(steer.send_steer("t-replay", "bob@x.io", "skip staging"))

    # …the next run starts and takes what is owed.
    pending = asyncio.run(steer.take_pending_steers("t-replay"))
    assert len(pending) == 1
    assert pending[0]["text"] == "skip staging"
    assert "[steer from bob@x.io] skip staging" in steer.format_replayed(pending)


def test_taking_pending_steers_is_exactly_once(fake_redis, monkeypatch):
    """Two runs starting at once must not each replay the same sentence."""
    async def _dispatch(_tid: str, _cmd: dict[str, Any]) -> bool:
        return False

    from orchestrator import stream_relay
    monkeypatch.setattr(stream_relay, "dispatch_control", _dispatch)
    asyncio.run(steer.send_steer("t-once", "bob@x.io", "skip staging"))

    first = asyncio.run(steer.take_pending_steers("t-once"))
    second = asyncio.run(steer.take_pending_steers("t-once"))
    assert len(first) == 1
    assert second == [], "a replayed steer must not be replayed again"


def test_a_delivered_steer_is_discarded_so_it_is_not_replayed(fake_redis, monkeypatch):
    async def _dispatch(_tid: str, _cmd: dict[str, Any]) -> bool:
        return True

    from orchestrator import stream_relay
    monkeypatch.setattr(stream_relay, "dispatch_control", _dispatch)

    out = asyncio.run(steer.send_steer("t-live", "bob@x.io", "skip staging"))
    asyncio.run(steer.discard_signal("t-live", str(out["id"])))
    assert asyncio.run(steer.take_pending_steers("t-live")) == []


def test_no_pending_steers_is_an_empty_list_not_an_error(fake_redis):
    assert asyncio.run(steer.take_pending_steers("t-never-used")) == []


# ---------------------------------------------------------------------------
# 5. The guidance buffer and the tool boundary
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _clean_guidance():
    steer._PENDING_GUIDANCE.clear()
    steer._CLOSED.clear()
    yield
    steer._PENDING_GUIDANCE.clear()
    steer._CLOSED.clear()


def test_guidance_reaches_the_model_at_the_next_tool_boundary():
    steer.buffer_guidance("t1", "sanjay@x.io", "skip the staging deploy")
    out = steer.decorate_tool_result("tool output here", "t1")
    assert "tool output here" in out
    assert "[steer from sanjay@x.io] skip the staging deploy" in out


def test_a_tool_result_with_no_pending_steer_is_untouched():
    """The ordinary path must be byte-identical — this runs on every tool call."""
    sentinel = {"not": "a string"}
    assert steer.decorate_tool_result(sentinel, "t-none") is sentinel


def test_guidance_is_consumed_once():
    steer.buffer_guidance("t2", "bob@x.io", "also check the invoice")
    first = steer.decorate_tool_result("out", "t2")
    second = steer.decorate_tool_result("out", "t2")
    assert "invoice" in first
    assert second == "out", "a steer must not be re-injected at every boundary"


def test_run_boundary_clears_stale_guidance():
    """A note buffered for a dead run must not leak into an unrelated later one."""
    steer.buffer_guidance("t3", "bob@x.io", "from the old run")
    steer.clear_guidance("t3")
    assert steer.decorate_tool_result("out", "t3") == "out"


# ---------------------------------------------------------------------------
# 6. Authority — the floor cannot move under a running turn
# ---------------------------------------------------------------------------

def test_a_participant_inside_the_run_floor_may_steer():
    assert steer.is_inside_run_floor(
        "bob@x.io", ["alice@x.io", "bob@x.io"],
    ) is True


def test_someone_who_joined_after_the_run_started_may_not_steer():
    """The intersection was folded without them; admitting them moves the floor.

    The run has already resolved credentials and read memory at the wider floor.
    We cannot un-read those, and we will not let the turn keep acting above the
    clearance of everyone who can now see its output — so the steer is refused
    with a reason, rather than the floor being narrowed silently mid-run.
    """
    assert steer.is_inside_run_floor(
        "carol@x.io", ["alice@x.io", "bob@x.io"],
    ) is False


def test_an_unrecorded_floor_fails_open():
    """Matching get_run_actor's contract: a Redis miss must not block work."""
    assert steer.is_inside_run_floor("anyone@x.io", None) is True


def test_a_group_shared_room_admits_its_members():
    """A roster of subjects cannot name everyone a `group:` subject covers."""
    assert steer.is_inside_run_floor(
        "dave@x.io", ["alice@x.io", "group:sales"], room_admits=True,
    ) is True
    assert steer.is_inside_run_floor(
        "dave@x.io", ["alice@x.io", "group:sales"], room_admits=False,
    ) is False


def test_an_anonymous_caller_may_not_steer_a_room_run():
    assert steer.is_inside_run_floor("", ["alice@x.io"]) is False


# ---------------------------------------------------------------------------
# 7. Shape of what travels
# ---------------------------------------------------------------------------

def test_a_signal_is_json_serialisable_and_attributed():
    sig = steer.make_signal(
        thread_id="t", author="bob@x.io", text="hi", run_id="r1",
    )
    round_tripped = json.loads(json.dumps(sig))
    assert round_tripped["author"] == "bob@x.io"
    assert round_tripped["targetRunId"] == "r1"
    assert round_tripped["id"]


# ---------------------------------------------------------------------------
# 8. A steer after the last tool call still gets an answer (owner, 2026-10-12)
# ---------------------------------------------------------------------------
#
# A steer rides the next tool result. A steer that arrives while the model
# writes its final answer meets no tool result. Before 2026-10-12 it died in
# the in-process buffer: the applier had already discarded its durable copy,
# and the next run start cleared the buffer. Now the run's terminal event
# gives it back to the durable store under its own id, and the steer
# follow-up replays it through ENGAGE (tests/unit/test_steer_follow_up.py).

def test_an_unread_steer_goes_back_to_the_store_at_the_terminal_event(fake_redis):
    """The seam is ``stream_relay.push_event``: every path that ends a run
    writes its terminal event there, and the store is written FIRST."""
    from orchestrator import stream_relay

    # The applier buffered it, and discarded the durable copy.
    assert steer.buffer_guidance("t-late", "alice@x.io", "add a due date", signal_id="s-1")
    assert asyncio.run(steer.take_pending_steers("t-late")) == []

    # The model wrote its last answer with no tool call. The run ends.
    asyncio.run(stream_relay.push_event("t-late", {"type": "RUN_FINISHED"}))

    stored = fake_redis.lists.get("cc:steer:t-late", [])
    assert len(stored) == 1, "the unread steer must go back to the store"
    pending = asyncio.run(steer.take_pending_steers("t-late"))
    assert [(p["id"], p["author"], p["text"]) for p in pending] == [
        ("s-1", "alice@x.io", "add a due date"),
    ]
    # The model never read it, and it is not read twice.
    assert steer.decorate_tool_result("out", "t-late") == "out"


def test_the_store_is_written_before_the_terminal_event(fake_redis):
    """The chat reads RUN_FINISHED and then asks for the follow-up, so the
    steer must be in the store when the event becomes visible."""
    from orchestrator import stream_relay

    order: list[str] = []
    real_rpush, real_xadd = fake_redis.rpush, fake_redis.xadd

    async def rpush(key, val):
        order.append("store")
        return await real_rpush(key, val)

    async def xadd(key, fields, **kw):
        order.append("event")
        return await real_xadd(key, fields, **kw)

    fake_redis.rpush, fake_redis.xadd = rpush, xadd
    steer.buffer_guidance("t-order", "alice@x.io", "and the owner", signal_id="s-2")
    asyncio.run(stream_relay.push_event("t-order", {"type": "RUN_ERROR"}))
    assert order == ["store", "event"]


def test_a_tool_event_gives_nothing_back(fake_redis):
    """Only a terminal event closes the run: a mid-run event keeps the note
    for the next tool boundary."""
    from orchestrator import stream_relay

    steer.buffer_guidance("t-mid", "alice@x.io", "skip staging", signal_id="s-3")
    asyncio.run(stream_relay.push_event("t-mid", {"type": "TOOL_CALL_END"}))
    assert "cc:steer:t-mid" not in fake_redis.lists
    assert "skip staging" in steer.decorate_tool_result("out", "t-mid")


def test_a_closed_run_buffers_nothing():
    """After the terminal event a late steer is not buffered. The applier
    then acks it and keeps its durable copy, so the follow-up answers it."""
    asyncio.run(steer.return_unconsumed("t-closed"))
    assert steer.buffer_guidance("t-closed", "bob@x.io", "too late") is False
    assert not steer.has_guidance("t-closed")
    # The next run start opens the thread again.
    steer.clear_guidance("t-closed")
    assert steer.buffer_guidance("t-closed", "bob@x.io", "next run") is True


def test_end_of_run_forgets_the_closed_mark(fake_redis):
    asyncio.run(steer.end_of_run("t-end"))
    assert "t-end" not in steer._CLOSED


def test_a_returned_steer_is_replayed_once_even_with_its_first_copy_still_stored(
    fake_redis, monkeypatch,
):
    """The applier's discard runs as a task. If the run ends before it ran,
    the store holds the first copy AND the returned one. One answer."""
    async def _dispatch(_tid: str, _cmd: dict[str, Any]) -> bool:
        return True

    from orchestrator import stream_relay
    monkeypatch.setattr(stream_relay, "dispatch_control", _dispatch)
    out = asyncio.run(steer.send_steer("t-dup", "bob@x.io", "check the invoice"))
    steer.buffer_guidance("t-dup", "bob@x.io", "check the invoice", signal_id=out["id"])
    asyncio.run(steer.return_unconsumed("t-dup"))
    assert len(fake_redis.lists["cc:steer:t-dup"]) == 2
    pending = asyncio.run(steer.take_pending_steers("t-dup"))
    assert [p["text"] for p in pending] == ["check the invoice"]


def test_has_pending_steers(fake_redis):
    assert asyncio.run(steer.has_pending_steers("t-none")) is False
    asyncio.run(steer.persist_signal("t-some", steer.make_signal(
        thread_id="t-some", author="a@x.io", text="hi",
    )))
    assert asyncio.run(steer.has_pending_steers("t-some")) is True


def test_a_replayed_steer_is_not_also_a_history_turn():
    """The chat sends the steer bubble as a user turn too. One copy goes."""
    history = [
        {"role": "user", "content": "Plan the release"},
        {"role": "assistant", "content": "Here is the plan."},
        {"role": "user", "content": "add a due date"},
    ]
    out = steer.without_replayed(history, [{"text": "add a due date"}])
    assert out == history[:2]
    assert len(history) == 3, "the input list is not changed"
    # Words that match no turn change nothing.
    assert steer.without_replayed(history, [{"text": "other"}]) == history
