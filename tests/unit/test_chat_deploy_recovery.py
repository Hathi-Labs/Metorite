"""A chat recovers from a gateway restart (incident 2026-10-09, 16:18 UTC).

The owner sent a message in the Projects assistant while a deploy ran. The run
was parked on a question card, and the restart killed it. ``cc:active:{tid}``
survived the restart with its 1 h TTL. So every later message in that thread
was routed as a STEER into a run that no longer existed: ``control_undelivered``,
``steer.pending_replay stored=True``, and no answer, four times.

This file fences the fix. Each test names the rule it holds (R7):

* **The P1 from the #791 review.** The executor's own ``mark_active`` call,
  inside every detached run, deleted the actor, the source and the floor that
  ``run_detached`` had just written.
* **Liveness.** A run belongs to one process (``cc:runowner``), and that
  process proves it is alive with a heartbeat key (``cc:instance``).
* **A dead run is never steered into.** A steer that no process can deliver
  clears the dead run and starts a fresh one, in the same request, which
  replays the steers stored for it.
* **The sweep.** A process finds the runs of a dead process, marks them
  inactive, and stamps a ``run_interrupted`` marker into the saved reply.
* **A card answer across a restart** is never dropped. It comes back as a
  message the member's browser sends, with the question it answers.

Hermetic: a fake Redis that holds strings, hashes, sets, lists and streams.
No SQL changes in this PR, so no R8 half.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

stream_relay = pytest.importorskip(
    "orchestrator.stream_relay", reason="orchestrator not installed",
)

_ALICE = "alice@recover.test"
_ORG = "11111111-1111-4111-8111-111111111111"


# ---------------------------------------------------------------------------
# A fake raw Redis
# ---------------------------------------------------------------------------

class _FakePubSub:
    def __init__(self, r: _FakeRedis) -> None:
        self.r = r
        self.channels: list[str] = []

    async def subscribe(self, channel: str) -> None:
        self.channels.append(channel)
        self.r.subscribers[channel] = self.r.subscribers.get(channel, 0) + 1

    async def unsubscribe(self, channel: str) -> None:
        if self.r.subscribers.get(channel):
            self.r.subscribers[channel] -= 1

    async def listen(self):
        while True:  # a listener that never hears a command
            await asyncio.sleep(3600)
            yield {}

    async def aclose(self) -> None:
        for c in self.channels:
            await self.unsubscribe(c)
        self.channels = []


class _FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, Any] = {}
        self.subscribers: dict[str, int] = {}
        self.published: list[tuple[str, dict]] = []
        self._seq = 0

    # strings
    async def set(self, key, value, ex=None, xx=False, nx=False, **_kw):
        if xx and key not in self.store:
            return None
        if nx and key in self.store:
            return None
        self.store[key] = value
        return True

    async def get(self, key):
        v = self.store.get(key)
        return v if isinstance(v, str) else None

    async def delete(self, *keys):
        n = 0
        for k in keys:
            n += 1 if self.store.pop(k, None) is not None else 0
        return n

    async def expire(self, *_a, **_kw):
        return True

    async def exists(self, *keys):
        return sum(1 for k in keys if k in self.store)

    async def rename(self, src, dst):
        if src not in self.store:
            raise RuntimeError("no such key")
        self.store[dst] = self.store.pop(src)
        return True

    # hashes
    async def hset(self, key, field, value):
        self.store.setdefault(key, {})[field] = value
        return 1

    async def hget(self, key, field):
        return (self.store.get(key) or {}).get(field)

    async def hgetall(self, key):
        return dict(self.store.get(key) or {})

    async def hdel(self, key, *fields):
        h = self.store.get(key) or {}
        n = 0
        for f in fields:
            n += 1 if h.pop(f, None) is not None else 0
        if key in self.store and not h:
            self.store.pop(key, None)
        return n

    # sets
    async def sadd(self, key, *members):
        s = self.store.setdefault(key, set())
        before = len(s)
        s.update(members)
        return len(s) - before

    async def srem(self, key, *members):
        s = self.store.get(key) or set()
        for m in members:
            s.discard(m)
        if key in self.store and not s:
            self.store.pop(key, None)
        return True

    async def smembers(self, key):
        return set(self.store.get(key) or set())

    # lists
    async def rpush(self, key, *values):
        self.store.setdefault(key, []).extend(values)
        return len(self.store[key])

    async def ltrim(self, key, start, end):
        lst = self.store.get(key) or []
        n = len(lst)
        s = start if start >= 0 else max(n + start, 0)
        e = end if end >= 0 else n + end
        self.store[key] = lst[s:e + 1]
        return True

    async def lrange(self, key, start, end):
        lst = list(self.store.get(key) or [])
        return lst[start:] if end == -1 else lst[start:end + 1]

    async def lrem(self, key, count, value):
        lst = self.store.get(key) or []
        if value in lst:
            lst.remove(value)
        return 1

    # streams
    async def xadd(self, key, fields, maxlen=None, approximate=True):
        self._seq += 1
        eid = f"{1_700_000_000_000 + self._seq}-0"
        self.store.setdefault(key, []).append((eid, dict(fields)))
        return eid

    async def xread(self, streams, count=None, block=None):
        if block:
            # A real XREAD blocks. Yield to the loop so the run's drain task
            # gets to push, rather than a subscriber spinning in place.
            await asyncio.sleep(0.01)
        out = []
        for key, cursor in streams.items():
            entries = self.store.get(key) or []
            if cursor in ("$",):
                continue
            after = [
                (eid, f) for eid, f in entries
                if cursor in ("0", "0-0") or _eid_gt(eid, cursor)
            ]
            if count:
                after = after[:count]
            if after:
                out.append((key, after))
        return out

    # pub/sub
    async def publish(self, channel, message):
        self.published.append((channel, json.loads(message)))
        return self.subscribers.get(channel, 0)

    def pubsub(self):
        return _FakePubSub(self)

    async def aclose(self):
        return None


def _eid_gt(a: str, b: str) -> bool:
    am, _, aseq = a.partition("-")
    bm, _, bseq = b.partition("-")
    return (int(am), int(aseq or 0)) > (int(bm), int(bseq or 0))


@pytest.fixture
def fake_redis(monkeypatch):
    r = _FakeRedis()

    async def _get_client():
        return r

    monkeypatch.setattr(stream_relay, "_get_client", _get_client)
    monkeypatch.setattr(stream_relay, "_DETACHED_TASKS", {})
    monkeypatch.setattr(stream_relay, "_LOCAL_CONTROL_HANDLERS", {})
    monkeypatch.setattr(stream_relay, "_CONTROL_LISTENERS", {})
    # No real sleep in the zero-subscriber retry.
    monkeypatch.setattr(stream_relay, "CONTROL_ACK_TIMEOUT", 0.05)
    return r


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# 1. The P1 from the #791 review — the run's facts survive the executor
# ---------------------------------------------------------------------------

def test_the_executor_prologue_keeps_the_run_facts(fake_redis, monkeypatch):
    """The REAL ``run_agent_stream`` prologue, wrapped in ``run_detached``.

    ``run_detached`` writes the actor, the source and the floor. The executor
    then calls ``mark_active(thread_id)`` with none of them, before it yields
    ``RUN_STARTED``. All three must still hold at that moment, or the steer
    floor fails open, the supersede guard never answers 409, and a cron run
    reads as a person's.
    """
    from orchestrator import executor

    tid = "thread-p1-fence"
    seen: dict[str, Any] = {}

    async def _no_subscriber(*_a, **_kw):
        if False:  # pragma: no cover - an async generator that yields nothing
            yield {}

    monkeypatch.setattr(stream_relay, "subscribe_events", _no_subscriber)

    # Stop the run right after its prologue. RUN_STARTED is yielded before
    # this, so the facts are read at the exact moment the fence names.
    async def _refuse(_name):
        raise RuntimeError("stop after the prologue")

    monkeypatch.setattr(executor, "_assert_may_run_agent", _refuse)

    async def _wrapped():
        async for line in executor.run_agent_stream(
            "projects-assistant", {"message": "hi", "source": "schedule"},
            run_id="run-p1", thread_id=tid,
        ):
            if '"RUN_STARTED"' in line and "actor" not in seen:
                seen["actor"] = await stream_relay.get_run_actor(tid)
                seen["source"] = await stream_relay.get_run_source(tid)
                seen["floor"] = await stream_relay.get_run_floor(tid)
            yield line

    async def _go():
        async for _ in stream_relay.run_detached(
            tid, _wrapped(), actor=_ALICE, source="schedule",
            floor=[_ALICE, "bob@recover.test"],
        ):
            pass
        task = stream_relay._DETACHED_TASKS.get(tid)
        if task is not None:
            await asyncio.wait_for(task, timeout=10)

    _run(_go())
    assert seen, "the run never yielded RUN_STARTED"
    assert seen["actor"] == _ALICE
    assert seen["source"] == "schedule"
    assert seen["floor"] == sorted([_ALICE, "bob@recover.test"])


def test_a_fresh_run_still_drops_the_previous_runs_facts(fake_redis):
    """``reset=True`` is a run boundary: an omitted fact is cleared there."""
    tid = "thread-boundary"

    async def _go():
        await stream_relay.mark_active(
            tid, reset=True, actor=_ALICE, source="schedule", floor=[_ALICE],
        )
        await stream_relay.mark_active(tid, reset=True)
        return (
            await stream_relay.get_run_actor(tid),
            await stream_relay.get_run_source(tid),
            await stream_relay.get_run_floor(tid),
        )

    assert _run(_go()) == (None, "", None)


# ---------------------------------------------------------------------------
# 2. Liveness — the owner process and its heartbeat
# ---------------------------------------------------------------------------

run_liveness = pytest.importorskip("orchestrator.run_liveness")

_DEAD = "old-box:4242:deadbeef"
_LIVE_SIBLING = "old-box:4343:cafef00d"


@pytest.fixture
def liveness(fake_redis, monkeypatch):
    """The fake Redis, plus a clean liveness state for this process."""
    monkeypatch.setattr(run_liveness, "_LOCAL_RUNS", set())
    monkeypatch.setattr(run_liveness, "ensure_heartbeat", lambda: None)
    monkeypatch.setattr(run_liveness, "_ON_INTERRUPTED", None)
    return fake_redis


def _seed_run(r: _FakeRedis, tid: str, owner: str | None, *, record=None) -> None:
    """A run another process marked active: what survives a restart."""
    r.store[f"cc:active:{tid}"] = "1"
    if owner:
        r.store[f"cc:runowner:{tid}"] = owner
        r.store.setdefault("cc:instances", set()).add(owner)
        if record is not None:
            r.store.setdefault(f"cc:instance-runs:{owner}", {})[tid] = json.dumps(record)


def _seed_events(r: _FakeRedis, tid: str, events: list[dict]) -> None:
    for e in events:
        _run(r.xadd(f"cc:stream:{tid}", {"event": json.dumps(e)}))


def test_a_run_whose_owner_has_no_heartbeat_is_dead(liveness):
    _seed_run(liveness, "t-dead", _DEAD)
    assert _run(run_liveness.run_liveness("t-dead")) == "dead"


def test_a_run_whose_owner_beats_is_live(liveness):
    _seed_run(liveness, "t-live", _LIVE_SIBLING)
    liveness.store[f"cc:instance:{_LIVE_SIBLING}"] = "1"
    assert _run(run_liveness.run_liveness("t-live")) == "live"


def test_a_live_owner_with_no_listener_is_dead(liveness):
    """The graceful-stop window: the key still beats, the listener is gone."""
    _seed_run(liveness, "t-stopping", _LIVE_SIBLING)
    liveness.store[f"cc:instance:{_LIVE_SIBLING}"] = "1"
    assert _run(run_liveness.run_liveness("t-stopping", undelivered=True)) == "dead"


def test_this_process_answers_from_its_own_runs(liveness):
    async def _go():
        await stream_relay.mark_active("t-mine", reset=True, actor=_ALICE)
        live = await run_liveness.run_liveness("t-mine")
        run_liveness._LOCAL_RUNS.discard("t-mine")  # a flag this process leaked
        leaked = await run_liveness.run_liveness("t-mine")
        return live, leaked

    assert _run(_go()) == ("live", "dead")
    assert liveness.store["cc:runowner:t-mine"] == run_liveness.INSTANCE_ID
    assert liveness.store[f"cc:instance:{run_liveness.INSTANCE_ID}"] == "1"


def test_a_run_from_an_older_build_is_dead_only_when_nothing_hears_it(liveness):
    _seed_run(liveness, "t-legacy", None)
    assert _run(run_liveness.run_liveness("t-legacy")) == "live"
    assert _run(run_liveness.run_liveness("t-legacy", undelivered=True)) == "dead"


def test_no_active_flag_is_idle(liveness):
    assert _run(run_liveness.run_liveness("t-none")) == "idle"


def test_an_unreadable_redis_reads_live(liveness, monkeypatch):
    """A false 'dead' starts a second run on a live one, so errors read live."""
    async def _boom():
        raise RuntimeError("redis down")

    monkeypatch.setattr(stream_relay, "_get_client", _boom)
    assert _run(run_liveness.run_liveness("t-any")) == "live"


def test_a_clean_stop_deletes_the_heartbeat_key(liveness):
    async def _go():
        await run_liveness.beat()
        assert f"cc:instance:{run_liveness.INSTANCE_ID}" in liveness.store
        await run_liveness.stop_instance_heartbeat()

    _run(_go())
    assert f"cc:instance:{run_liveness.INSTANCE_ID}" not in liveness.store


# ---------------------------------------------------------------------------
# 3. The sweep — a dead process's runs are closed and saved as interrupted
# ---------------------------------------------------------------------------

def _record(tid: str) -> dict:
    return {
        "org": _ORG, "actor": _ALICE, "messageId": f"asst-{tid}",
        "agent": "projects-assistant", "runId": f"run-{tid}",
        "tokens": [f"tok-{tid}", f"run-{tid}"],
    }


def test_the_sweep_closes_only_the_dead_processes_active_runs(liveness, monkeypatch):
    from acb_common.tenant_redis import TenantKey, organization_scope

    # The dead process left two runs: one still marked active, one that ended.
    _seed_run(liveness, "t-cut", _DEAD, record=_record("t-cut"))
    _seed_events(liveness, "t-cut", [
        {"type": "RUN_STARTED"},
        {"type": "TEXT_MESSAGE_CONTENT", "delta": "Here is the first half"},
    ])
    liveness.store.setdefault(f"cc:instance-runs:{_DEAD}", {})["t-ended"] = json.dumps(
        _record("t-ended"))
    # A sibling that is alive keeps its run.
    _seed_run(liveness, "t-sibling", _LIVE_SIBLING, record=_record("t-sibling"))
    liveness.store[f"cc:instance:{_LIVE_SIBLING}"] = "1"
    # #791's org index holds the cut run, under the executor's token.
    with organization_scope(_ORG):
        index = str(TenantKey(_ORG, "liveruns"))
    liveness.store[index] = {"t-cut": json.dumps({"token": "run-t-cut"})}

    handed: list[list[dict]] = []

    async def _persist(records):
        handed.append(records)

    monkeypatch.setattr(run_liveness, "_ON_INTERRUPTED", _persist)
    closed = _run(run_liveness.sweep_dead_instances())

    assert [c["threadId"] for c in closed] == ["t-cut"]
    assert closed[0]["messageId"] == "asst-t-cut"
    assert handed == [closed]
    assert "cc:active:t-cut" not in liveness.store
    assert liveness.store["cc:active:t-sibling"] == "1"
    assert "t-cut" not in (liveness.store.get(index) or {})
    assert _DEAD not in liveness.store.get("cc:instances", set())
    assert f"cc:instance-runs:{_DEAD}" not in liveness.store
    names = [
        json.loads(f["event"]).get("name")
        for _eid, f in liveness.store["cc:stream:t-cut"]
    ]
    assert run_liveness.INTERRUPTED_EVENT in names


def test_one_sweeper_per_dead_process(liveness):
    _seed_run(liveness, "t-cut", _DEAD, record=_record("t-cut"))
    liveness.store[f"cc:instance-sweep:{_DEAD}"] = "another-live-process"
    assert _run(run_liveness.sweep_dead_instances()) == []
    assert liveness.store["cc:active:t-cut"] == "1"


def test_the_saved_reply_keeps_the_partial_answer_and_the_marker(liveness):
    """The fold the sweep hands to the gateway: text kept, marker added."""
    from gateway.chat_fold import fold_run_events

    _seed_run(liveness, "t-cut", _DEAD, record=_record("t-cut"))
    _seed_events(liveness, "t-cut", [
        {"type": "RUN_STARTED"},
        {"type": "TEXT_MESSAGE_CONTENT", "delta": "Here is the first half"},
    ])
    _run(run_liveness.sweep_dead_instances())
    events = _run(stream_relay.replay_events("t-cut", drain=True))
    folded = fold_run_events(events)
    assert folded is not None
    assert folded["content"] == "Here is the first half"
    assert [e["name"] for e in folded["custom_events"]] == ["run_interrupted"]


def test_persist_interrupted_folds_each_record_into_its_row(liveness, monkeypatch):
    from gateway import chat_fold, chat_recovery

    calls: list[tuple] = []

    async def _persist(tid, mid, **kw):
        calls.append((tid, mid, kw["organization_id"], kw["user_id"]))
        return {"content": "x"}

    monkeypatch.setattr(chat_fold, "persist_final_assistant_message", _persist)
    n = _run(chat_recovery.persist_interrupted([
        {"threadId": "t-cut", **_record("t-cut")},
        # /copilot/chat keeps its own path: no row id, nothing to fold here.
        {"threadId": "t-copilot", "org": _ORG},
    ]))
    assert n == 1
    assert calls == [("t-cut", "asst-t-cut", _ORG, _ALICE)]


# ---------------------------------------------------------------------------
# 4. A dead run is never steered into
# ---------------------------------------------------------------------------

@pytest.fixture
def no_persist(monkeypatch):
    from gateway import chat_fold

    calls: list[str] = []

    async def _persist(tid, mid, **_kw):
        calls.append(mid)
        return {"content": ""}

    monkeypatch.setattr(chat_fold, "persist_final_assistant_message", _persist)
    return calls


def _req(tid: str, text: str):
    from gateway.routes.agent import AgentRunRequest

    return AgentRunRequest(
        agent="projects-assistant", thread_id=tid, payload={"message": text},
    )


def _store_steer(r: _FakeRedis, tid: str, text: str) -> None:
    from orchestrator import steer

    sig = steer.make_signal(thread_id=tid, author=_ALICE, text=text)
    r.store.setdefault(f"cc:steer:{tid}", []).append(json.dumps(sig))


def test_a_message_to_a_dead_run_engages_and_replays_the_lost_steers(
    liveness, no_persist,
):
    """The incident, step by step. Four "Continue" messages were stored for a
    replay that never came. The next message now starts a run that says them."""
    from gateway.routes import agent as agent_routes
    from orchestrator.steer import Route

    tid = "t-incident"
    _seed_run(liveness, tid, _DEAD, record=_record(tid))
    _store_steer(liveness, tid, "Continue. I think you stopped midway.")

    async def _go():
        decision = await agent_routes._route_incoming_turn(tid, _ALICE, "Continue")
        req = _req(tid, "Continue")
        out = await agent_routes._apply_turn_decision(
            decision, req, "projects-assistant", _ALICE, None,
        )
        return decision, out, req

    decision, out, req = _run(_go())
    assert decision.route is Route.ENGAGE
    assert decision.reason == "dead_run_recovered"
    assert out is None, "None means: start the run, in this request"
    assert req.payload["message"] == (
        f"[steer from {_ALICE}] Continue. I think you stopped midway.\n\nContinue"
    )
    assert "cc:active:t-incident" not in liveness.store
    assert no_persist == [f"asst-{tid}"], "the partial reply is saved first"


def test_a_steer_no_process_hears_falls_back_to_a_fresh_run(liveness, no_persist):
    """The 16:18:48 window. The old process still beats, and its listener is
    gone. The router reads the run as live and steers, and nobody hears the
    steer. The route then closes the run and starts the next one, with the
    steers an earlier request left behind, and WITHOUT this message twice.
    """
    from gateway.routes import agent as agent_routes
    from orchestrator.steer import Route

    tid = "t-window"
    _seed_run(liveness, tid, _LIVE_SIBLING, record=_record(tid))
    liveness.store[f"cc:instance:{_LIVE_SIBLING}"] = "1"
    _store_steer(liveness, tid, "an earlier note")

    async def _go():
        decision = await agent_routes._route_incoming_turn(tid, _ALICE, "Continue")
        req = _req(tid, "Continue")
        out = await agent_routes._apply_turn_decision(
            decision, req, "projects-assistant", _ALICE, None,
        )
        return decision, out, req

    decision, out, req = _run(_go())
    assert decision.route is Route.STEER
    assert out is None, "a steer nobody heard must start a run, not answer 202"
    assert req.payload["message"] == f"[steer from {_ALICE}] an earlier note\n\nContinue"
    assert "cc:active:t-window" not in liveness.store
    assert liveness.store.get("cc:steer:t-window") in (None, [])


def test_a_steer_a_live_run_heard_still_answers_202(liveness, no_persist):
    """The fix must not break steering: a live run takes the steer."""
    from gateway.routes import agent as agent_routes

    tid = "t-alive"

    async def _go():
        await stream_relay.mark_active(tid, reset=True, actor=_ALICE)
        stream_relay.register_control_command(tid, "steer", lambda _c: True)
        decision = await agent_routes._route_incoming_turn(tid, _ALICE, "also X")
        return await agent_routes._apply_turn_decision(
            decision, _req(tid, "also X"), "projects-assistant", _ALICE, None,
        )

    out = _run(_go())
    assert out is not None and out.status_code == 202
    assert json.loads(out.body)["steered"] is True
    assert liveness.store["cc:active:t-alive"] == "1"
    assert no_persist == []


def test_a_bare_stop_to_a_dead_run_starts_nothing(liveness, no_persist):
    from gateway.routes import agent as agent_routes
    from orchestrator.steer import Route

    _seed_run(liveness, "t-stop", _DEAD, record=_record("t-stop"))
    d = _run(agent_routes._route_incoming_turn("t-stop", _ALICE, "stop"))
    assert d.route is Route.DROP
    assert "cc:active:t-stop" not in liveness.store


# ---------------------------------------------------------------------------
# 5. A card answer across a restart is never dropped
# ---------------------------------------------------------------------------

def _answer_client(monkeypatch):
    from acb_auth import UserContext, UserRole, get_current_user
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from gateway.routes import agent as agent_routes

    class _Room:
        can_send = True

    monkeypatch.setattr(agent_routes, "_resolve_room", lambda *_a, **_k: _Room())
    app = FastAPI()
    app.post("/agent/respond-input")(agent_routes.respond_user_input)
    app.dependency_overrides[get_current_user] = lambda: UserContext(
        email=_ALICE, role=UserRole.EMPLOYEE, organization_id=_ORG,
    )
    return TestClient(app)


def test_a_card_answer_to_a_dead_run_comes_back_with_its_question(
    liveness, no_persist, monkeypatch,
):
    """16:18:48. The member answered the card after the stop killed its run.
    The answer used to 409 into nothing. Now it comes back as a message the
    browser sends, with the question it answers."""
    tid = "t-card"
    _seed_run(liveness, tid, _DEAD, record=_record(tid))
    _seed_events(liveness, tid, [
        {"type": "RUN_STARTED"},
        {"type": "CUSTOM", "name": "user_input_requested", "value": {
            "request_id": "req-1", "question": "Which project should I use?",
        }},
    ])
    res = _answer_client(monkeypatch).post("/agent/respond-input", json={
        "request_id": "req-1", "answer": "Apollo", "thread_id": tid,
    })
    assert res.status_code == 409
    detail = res.json()["detail"]
    assert detail["error"] == "run_restarted"
    assert detail["resumeMessage"] == (
        'You asked me: "Which project should I use?"\n\nMy answer: Apollo'
    )
    assert "cc:active:t-card" not in liveness.store
    assert no_persist == [f"asst-{tid}"]


def test_a_stale_card_on_a_live_run_keeps_the_old_409(liveness, no_persist, monkeypatch):
    """A listener heard the answer and no question waits on that id. The run
    is alive, so nothing is closed and nothing is sent again."""
    tid = "t-stale"
    _seed_run(liveness, tid, _LIVE_SIBLING, record=_record(tid))
    liveness.store[f"cc:instance:{_LIVE_SIBLING}"] = "1"
    liveness.subscribers[f"cc:control:{tid}"] = 1
    res = _answer_client(monkeypatch).post("/agent/respond-input", json={
        "request_id": "req-old", "answer": "yes", "thread_id": tid,
    })
    assert res.status_code == 409
    assert isinstance(res.json()["detail"], str)
    assert liveness.store["cc:active:t-stale"] == "1"
    assert no_persist == []


def test_a_card_answer_after_the_sweep_still_comes_back(liveness, no_persist, monkeypatch):
    """The sweep already closed the run: idle, with the marker in its stream."""
    tid = "t-swept"
    _seed_events(liveness, tid, [
        {"type": "CUSTOM", "name": "user_input_requested", "value": {
            "request_id": "req-2", "question": "Ship it?",
        }},
        {"type": "CUSTOM", "name": "run_interrupted", "value": {"reason": "restart"}},
    ])
    res = _answer_client(monkeypatch).post("/agent/respond-input", json={
        "request_id": "req-2", "answer": "yes", "thread_id": tid,
    })
    assert res.status_code == 409
    assert res.json()["detail"]["resumeMessage"] == (
        'You asked me: "Ship it?"\n\nMy answer: yes'
    )


# ---------------------------------------------------------------------------
# 6. Continue — the server writes the note, from the saved partial reply
# ---------------------------------------------------------------------------

def test_the_resume_note_quotes_the_tail_of_the_saved_reply():
    from gateway.chat_recovery import RESUME_NOTE, compose_resume_note

    assert compose_resume_note("") == RESUME_NOTE
    note = compose_resume_note("Step 1 done. Step 2: write the")
    assert note.startswith(RESUME_NOTE)
    assert note.endswith("Step 1 done. Step 2: write the")
    long = compose_resume_note("x" * 5000)
    assert len(long) < len(RESUME_NOTE) + 1300


def test_continue_sends_the_servers_words_not_the_clients(monkeypatch):
    from gateway.routes import agent as agent_routes
    from gateway.routes import chat as chat_routes

    monkeypatch.setattr(chat_routes, "_get_messages", lambda *_a, **_k: [
        {"role": "user", "content": "Plan the launch"},
        {"role": "assistant", "content": "Step 1 done. Step 2: write the"},
    ])
    note = _run(agent_routes._resume_note_for("t-resume", _ALICE, _ORG))
    assert "cut off by an app update" in note
    assert note.endswith("Step 1 done. Step 2: write the")


# ---------------------------------------------------------------------------
# 7. A reconnect never waits on a dead run, and the gateway runs the beat
# ---------------------------------------------------------------------------

def test_a_reconnect_to_a_dead_run_ends_with_the_marker(liveness, no_persist, monkeypatch):
    """Before the fix, Phase 2 waited on the dead run's flag for up to an
    hour, and the chat showed "Reconnecting…" all that time."""
    from acb_auth import UserContext, UserRole, get_current_user
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from gateway.routes import agent as agent_routes

    tid = "t-reconnect"
    _seed_run(liveness, tid, _DEAD, record=_record(tid))
    _seed_events(liveness, tid, [
        {"type": "RUN_STARTED"},
        {"type": "TEXT_MESSAGE_CONTENT", "delta": "half an answer"},
    ])
    monkeypatch.setattr(agent_routes, "_thread_owner_ok", lambda *_a, **_k: True)
    app = FastAPI()
    app.get("/agent/run/{thread_id}/reconnect")(agent_routes.reconnect_agent_stream)
    app.dependency_overrides[get_current_user] = lambda: UserContext(
        email=_ALICE, role=UserRole.EMPLOYEE, organization_id=_ORG,
    )
    body = TestClient(app).get(f"/agent/run/{tid}/reconnect").text
    assert "half an answer" in body
    assert '"run_interrupted"' in body
    assert '"RUN_FINISHED"' in body
    assert "cc:active:t-reconnect" not in liveness.store


def test_the_gateway_starts_the_beat_and_stops_it_first():
    """The lifespan wiring. Without the start, no process beats and every
    sibling's run reads as dead. Without the stop, the next process waits a
    whole TTL before it closes this one's runs."""
    from pathlib import Path

    src = (
        Path(__file__).resolve().parents[2] / "apps/services/gateway/gateway/main.py"
    ).read_text(encoding="utf-8")
    assert "start_instance_heartbeat(on_interrupted=persist_interrupted)" in src
    after_yield = src[src.index("    yield\n"):]
    stop = after_yield.index("await stop_instance_heartbeat()")
    assert stop < after_yield.index("stop_background_sync"), "stop the beat first"
