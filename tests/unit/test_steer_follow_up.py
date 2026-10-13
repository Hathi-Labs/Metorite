"""A message sent during a run always steers it, and always gets an answer.

Owner, 2026-10-12: "Whenever an agent is still thinking and we send a reply,
it should steer the output necessarily so that the agent keeps continuing
with the response and uses the new input as additional context."

``POST /agent/run/stream`` is driven through FastAPI with the REAL
``_route_incoming_turn`` and ``_apply_turn_decision``. Only the edges are
faked: the Redis client, the run's liveness probes, the control bus, the
executor and the run itself.

Each test names the rule it holds (R7):

* a second message to a live thread routes as STEER: 202, and no second
  run, no executor call and no mint;
* a steer that arrives after the run's last tool call still gets an answer:
  the run's terminal event gives it back to the store, and the follow-up
  starts ONE run whose message is the steer, through the ENGAGE seam, with
  the steer read once;
* a follow-up with nothing owed, or with a run still going, starts nothing.

Mutations this suite catches (the table is in the PR):

* the terminal event gives nothing back (``_give_back_steers`` a no-op): the
  follow-up finds nothing and the late steer gets no answer;
* the follow-up gate dropped: a follow-up with nothing owed starts a run;
* the ENGAGE replay no longer drops the history copy: the model reads the
  steer twice.

Hermetic: no SQL changes, so no database (R8 does not apply).
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

stream_relay = pytest.importorskip(
    "orchestrator.stream_relay", reason="orchestrator not installed",
)
steer = pytest.importorskip("orchestrator.steer")

_ORG = "33333333-3333-4333-8333-333333333333"
_ALICE = "alice@steer.test"


class _FakeRedis:
    """Lists for the steer store, and a stream for the run's events."""

    def __init__(self) -> None:
        self.lists: dict[str, list[str]] = {}
        self.kv: dict[str, str] = {}

    async def rpush(self, key: str, val: str) -> int:
        self.lists.setdefault(key, []).append(val)
        return len(self.lists[key])

    async def ltrim(self, *_a: Any) -> None:
        return None

    async def expire(self, *_a: Any, **_k: Any) -> bool:
        return True

    async def rename(self, src: str, dst: str) -> None:
        if src not in self.lists:
            raise RuntimeError("no such key")
        self.lists[dst] = self.lists.pop(src)

    async def lrange(self, key: str, start: int, end: int) -> list[str]:
        items = list(self.lists.get(key, []))
        return items if end == -1 else items[start:end + 1]

    async def lrem(self, key: str, _count: int, val: str) -> int:
        items = self.lists.get(key, [])
        if val in items:
            items.remove(val)
            return 1
        return 0

    async def delete(self, *keys: str) -> None:
        for k in keys:
            self.lists.pop(k, None)
            self.kv.pop(k, None)

    async def xadd(self, key: str, fields: dict, **_k: Any) -> str:
        self.lists.setdefault(key, []).append(json.dumps(fields))
        return f"{len(self.lists[key])}-0"

    async def set(self, key: str, val: Any, *_a: Any, xx: bool = False, **_k: Any) -> bool:
        if xx and key not in self.kv:
            return False
        self.kv[key] = str(val)
        return True

    async def get(self, key: str) -> str | None:
        return self.kv.get(key)


class _World:
    def __init__(self) -> None:
        self.active = False
        self.calls: list[str] = []
        self.payloads: list[dict[str, Any]] = []
        self.redis = _FakeRedis()


@pytest.fixture
def world(monkeypatch):
    import acb_memory
    import orchestrator.executor as executor
    from gateway.routes import agent
    from orchestrator import run_cap, run_liveness

    w = _World()
    steer._PENDING_GUIDANCE.clear()
    steer._CLOSED.clear()

    async def _get_client():
        return w.redis

    async def _noop(*_a, **_k):
        return None

    async def _is_active(_tid):
        return w.active

    async def _source(_tid):
        return "chat"

    async def _alive(_tid):
        return "alive"

    async def _false(*_a, **_k):
        return False

    async def _dispatch(_tid, _cmd):
        w.calls.append("dispatch")
        return True

    class _Admitted:
        admitted = True
        limit = 5
        running: tuple = ()

    async def _admit(**_k):
        return _Admitted()

    async def _memory(**_k):
        return ""

    async def _mint(*_a, **_k):
        w.calls.append("mint")

    async def _gen():
        yield "unused"

    def _run_agent_stream(agent_name, payload, **_k):
        w.calls.append("executor")
        w.payloads.append(json.loads(json.dumps(
            {k: v for k, v in payload.items() if not k.startswith("_")}, default=str,
        )))
        return _gen()

    async def _run_detached(thread_id, gen, **_k):
        w.calls.append("run")
        yield {"type": "RUN_STARTED", "threadId": thread_id}

    monkeypatch.setattr(stream_relay, "_get_client", _get_client)
    monkeypatch.setattr(stream_relay, "is_active", _is_active)
    monkeypatch.setattr(stream_relay, "get_run_source", _source)
    monkeypatch.setattr(stream_relay, "get_run_floor", _noop)
    monkeypatch.setattr(stream_relay, "dispatch_control", _dispatch)
    monkeypatch.setattr(stream_relay, "run_detached", _run_detached)
    monkeypatch.setattr(run_liveness, "recovery_in_progress", _false)
    monkeypatch.setattr(run_liveness, "run_liveness", _alive)
    monkeypatch.setattr(run_cap, "admit_member_run", _admit)
    monkeypatch.setattr(run_cap, "release_member_slot", _noop)
    monkeypatch.setattr(agent, "_resolve_room", lambda *_a, **_k: None)
    monkeypatch.setattr(agent, "assert_can_run_agent_in_session", _noop)
    monkeypatch.setattr(agent, "_prepare_if_new_thread", _noop)
    monkeypatch.setattr(agent, "_refuse_if_another_run_is_active", _noop)
    monkeypatch.setattr(agent, "_mint_run_row_bounded", _mint)
    monkeypatch.setattr(agent, "_address_agent", lambda req, room, org=None: "orchestrator")
    monkeypatch.setattr(executor, "run_agent_stream", _run_agent_stream)
    monkeypatch.setattr(acb_memory, "get_session_memory", _memory)
    monkeypatch.setattr(acb_memory, "add_episode", _noop)
    yield w
    steer._PENDING_GUIDANCE.clear()
    steer._CLOSED.clear()


def _client():
    from acb_auth import UserContext, get_current_user
    from acb_auth.roles import UserRole
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from gateway.routes.agent import run_agent_stream_endpoint

    user = UserContext(email=_ALICE, role=UserRole.EMPLOYEE, organization_id=_ORG)
    app = FastAPI()
    app.post("/agent/run/stream")(run_agent_stream_endpoint)
    app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app)


def _body(thread: str, message: str, *, history=None, followup=False) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "mode": "chat", "message": message, "messages": history or [],
    }
    if followup:
        payload["steer_followup"] = True
    return {
        "agent": "orchestrator", "thread_id": thread,
        "assistant_message_id": f"a-{thread}", "payload": payload,
    }


def _post(body: dict[str, Any]):
    with _client().stream("POST", "/agent/run/stream", json=body) as r:
        r.read()
        return r.status_code, (r.json() if r.status_code == 202 else None)


def test_a_second_message_to_a_live_thread_steers_and_starts_no_run(world):
    world.active = True
    code, body = _post(_body("t-live", "Also add a due date"))
    assert code == 202
    assert body["steered"] is True and body["reason"] == "folded_into_live_run"
    assert world.calls == ["dispatch"], "a steer starts no run, no executor and no mint"
    # Durable before dispatch: the store holds it for the follow-up.
    stored = [json.loads(x) for x in world.redis.lists["cc:steer:t-live"]]
    assert [s["text"] for s in stored] == ["Also add a due date"]


def test_a_steer_after_the_last_tool_call_still_gets_an_answer(world):
    """The whole path: steer, final answer with no tool call, run end,
    follow-up. The follow-up's ONE run reads the steer once."""
    world.active = True
    code, body = _post(_body("t-late", "Also add a due date"))
    assert code == 202 and body["steered"]
    # The owner's applier buffers it. Since round 2 of the review the
    # durable copy stays until a tool result carries the note.
    sid = body["signalId"]
    assert steer.buffer_guidance("t-late", _ALICE, "Also add a due date", signal_id=sid)

    # The model writes its final answer with no tool call, and the run ends.
    asyncio.run(stream_relay.push_event("t-late", {"type": "RUN_FINISHED"}))
    world.active = False

    # The chat asks for the follow-up, with the steer bubble in its history.
    history = [
        {"role": "user", "content": "Plan the release"},
        {"role": "assistant", "content": "Here is the plan."},
        {"role": "user", "content": "Also add a due date"},
    ]
    code, _ = _post(_body("t-late", "", history=history, followup=True))
    assert code == 200
    assert world.calls.count("run") == 1 and world.calls.count("executor") == 1
    sent = world.payloads[0]
    assert sent["message"] == f"[steer from {_ALICE}] Also add a due date"
    assert [m["content"] for m in sent["messages"]] == ["Plan the release", "Here is the plan."]
    assert "steer_followup" not in sent
    # Taken once: a second follow-up finds nothing.
    code, body = _post(_body("t-late", "", followup=True))
    assert code == 202 and body["reason"] == "nothing_pending"
    assert world.calls.count("run") == 1


def test_a_follow_up_with_nothing_owed_starts_nothing(world):
    """The run read the steer at a tool boundary, so nothing is stored."""
    code, body = _post(_body("t-read", "", followup=True))
    assert code == 202
    assert body == {
        "steered": False, "followUp": False,
        "reason": "nothing_pending", "threadId": "t-read",
    }
    assert world.calls == []


def test_a_follow_up_while_a_run_goes_on_starts_nothing(world):
    """A run still going gets its steers itself. The follow-up waits."""
    asyncio.run(steer.persist_signal("t-busy", steer.make_signal(
        thread_id="t-busy", author=_ALICE, text="and the owner",
    )))
    world.active = True
    code, body = _post(_body("t-busy", "", followup=True))
    assert code == 202 and body["reason"] == "run_in_flight"
    assert world.calls == []
    # The steer stays stored for the next ENGAGE.
    assert asyncio.run(steer.has_pending_steers("t-busy")) is True


def test_a_refusal_after_engage_gives_the_steers_back(world, monkeypatch):
    """Review of #920: the follow-up took the steers, then another member's
    run took the thread. The 409 starts no run, so the steers go back."""
    from fastapi import HTTPException
    from gateway.routes import agent

    asyncio.run(steer.persist_signal("t-race", steer.make_signal(
        thread_id="t-race", author=_ALICE, text="and the owner",
    )))

    async def _busy(*_a, **_k):
        raise HTTPException(status_code=409, detail={"error": "run_in_progress"})

    monkeypatch.setattr(agent, "_refuse_if_another_run_is_active", _busy)
    code, _ = _post(_body("t-race", "", followup=True))
    assert code == 409
    assert world.calls.count("run") == 0
    pending = asyncio.run(steer.take_pending_steers("t-race"))
    assert [p["text"] for p in pending] == ["and the owner"], "the steer must not be lost"


# ---------------------------------------------------------------------------
# Round 2 of the review of #920: a Stop asks for no follow-up, from any tab
# ---------------------------------------------------------------------------
#
# The cancelled run's own end gives its unread steer back (``end_of_run``).
# Before round 2 the gate then ENGAGEd a new run, so the agent went on after
# the member stopped it. ``cancel_run`` now records the Stop for the thread,
# and the gate answers 202 ``stopped`` to every tab and every member.

def _steer_then_buffer(world, thread: str, words: str = "Also add a due date") -> str:
    world.active = True
    code, body = _post(_body(thread, words))
    assert code == 202 and body["steered"]
    sid = body["signalId"]
    assert steer.buffer_guidance(thread, _ALICE, words, signal_id=sid)
    return sid


def _owner_ends_the_cancelled_run(world, thread: str) -> None:
    """The owner's drain finally: it gives the unread steer back."""
    asyncio.run(steer.end_of_run(thread))
    world.active = False


def test_review_a_steer_then_a_bare_stop_asks_for_no_follow_up(world):
    """(a) The member steers, then types "stop". The route ABORTs."""
    _steer_then_buffer(world, "t-stop-a")
    code, body = _post(_body("t-stop-a", "stop"))
    assert code == 202 and body["aborted"] is True
    _owner_ends_the_cancelled_run(world, "t-stop-a")
    code, body = _post(_body("t-stop-a", "", followup=True))
    assert code == 202 and body["reason"] == "stopped"
    assert world.calls.count("run") == 0, "the agent must not go on after a Stop"
    assert asyncio.run(steer.has_pending_steers("t-stop-a")) is False


def test_review_a_steer_then_the_stop_button_asks_for_no_follow_up(world):
    """(b) The member steers, then presses Stop at once. The steer's 202
    reaches the tab after the Stop, so the tab still asks."""
    _steer_then_buffer(world, "t-stop-b")
    assert asyncio.run(stream_relay.cancel_run("t-stop-b")) is True
    _owner_ends_the_cancelled_run(world, "t-stop-b")
    code, body = _post(_body("t-stop-b", "", followup=True))
    assert code == 202 and body["reason"] == "stopped"
    assert world.calls.count("run") == 0


def test_review_another_tabs_retry_after_a_stop_starts_nothing(world):
    """(c) A second tab or another member has a follow-up retry going. Each
    try after the Stop answers ``stopped``, and the next real message runs
    clean: no replay, and the mark is gone."""
    _steer_then_buffer(world, "t-stop-c")
    asyncio.run(stream_relay.cancel_run("t-stop-c"))
    _owner_ends_the_cancelled_run(world, "t-stop-c")
    for _ in range(3):
        code, body = _post(_body("t-stop-c", "", followup=True))
        assert code == 202 and body["reason"] == "stopped"
    assert world.calls.count("run") == 0
    # The member's next real message: one run, with no replayed steer.
    with _client().stream("POST", "/agent/run/stream", json=_body("t-stop-c", "Now the budget")) as r:
        assert r.status_code == 200
        r.read()
    assert world.calls.count("run") == 1
    assert world.payloads[-1]["message"] == "Now the budget"
    # The mark does not leak into the next run.
    assert asyncio.run(steer.was_stopped("t-stop-c")) is False


def test_a_new_run_start_clears_the_stop_mark(monkeypatch):
    """``run_detached`` clears the mark before the run, so a later follow-up
    of THAT run is answered normally."""
    fake = _FakeRedis()

    async def _get_client():
        return fake

    async def _noop(*_a, **_k):
        return None

    from orchestrator import run_liveness
    monkeypatch.setattr(stream_relay, "_get_client", _get_client)
    for name in ("mark_active", "register_live_run", "mark_inactive",
                 "unregister_live_run", "_stop_control_listener_wait"):
        monkeypatch.setattr(stream_relay, name, _noop)
    monkeypatch.setattr(stream_relay, "_start_control_listener", lambda *_a, **_k: None)
    monkeypatch.setattr(run_liveness, "record_instance_run", _noop)
    monkeypatch.setattr(run_liveness, "forget_instance_run", _noop)

    async def _no_events(*_a, **_k):
        if False:
            yield {}

    monkeypatch.setattr(stream_relay, "subscribe_events", _no_events)

    async def _gen():
        if False:
            yield ""

    async def _go():
        await steer.mark_stopped("t-new")
        assert await steer.was_stopped("t-new") is True
        async for _ in stream_relay.run_detached("t-new", _gen()):
            pass
        return await steer.was_stopped("t-new")

    assert asyncio.run(_go()) is False


def test_a_buffered_steer_survives_the_owner_dying(world):
    """Round 2, P2: a deploy restarts the owner while a steer sits in its
    buffer. ``interrupt_run`` ends the run from another process, whose buffer
    is empty. The durable copy still holds the steer, so the follow-up
    answers it."""
    _steer_then_buffer(world, "t-crash", "and the owner")
    # The owner process dies: its buffer is gone, nothing gave it back.
    steer._PENDING_GUIDANCE.clear()
    world.active = False
    asyncio.run(stream_relay.push_event("t-crash", {"type": "RUN_FINISHED", "interrupted": True}))
    code, _ = _post(_body("t-crash", "", followup=True))
    assert code == 200
    assert world.payloads[-1]["message"] == f"[steer from {_ALICE}] and the owner"
