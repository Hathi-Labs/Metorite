"""The run cap: at most N live runs per member, and no org cap (WS-51 D-3).

Owner decision, 2026-10-10: "Do not limit the organization because an
organization might have many people, but possibly we can limit 5 agent runs
concurrently per user." ``orchestrator/run_cap.py`` holds the rules, and
``POST /agent/run/stream`` asks it after the steer decision and before the run
is minted. ``chat_run_continuity.md`` §5 records the decision.

Each test names the rule it holds (R7):

* the 6th run is refused, and the 5th is allowed;
* a steer into the member's own live run is not a new run, so it never counts;
* a run that ended, or whose process died (#797), holds no slot;
* a caller with no member, and an entry with no actor, never count;
* a refused run reaches no memory read, no Graphiti episode, no prompt save
  and no mint, and the 429 body is ``{"error", "limit", "running"}``;
* concurrent sends cannot both pass the check, through a fake Redis that
  yields to the loop at every call.

Hermetic: a fake Redis and a faked route world. The route test of the prompt
row against a real Postgres is in ``test_chat_prompt_saved_at_start.py``
(R8), because this suite changes no SQL.

Mutations this suite catches:

* no lock around the check and the reservation: the concurrency cases admit
  six and two;
* the liveness rule dropped (every index entry counts): the dead-run case
  refuses;
* the cap check moved below the mint: the refused-run case sees a mint;
* the actor filter dropped: another member's runs refuse Alice.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

stream_relay = pytest.importorskip(
    "orchestrator.stream_relay", reason="orchestrator not installed",
)
run_cap = pytest.importorskip("orchestrator.run_cap")
run_liveness = pytest.importorskip("orchestrator.run_liveness")

_ORG = "22222222-2222-4222-8222-222222222222"
_ALICE = "alice@cap.test"
_BOB = "bob@cap.test"


# ---------------------------------------------------------------------------
# A fake raw Redis. With ``yield_io`` every call yields to the loop first, as
# a real client does, so two sends interleave at each round trip.
# ---------------------------------------------------------------------------

class _FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, Any] = {}
        self.yield_io = False
        self.broken = False

    async def _io(self) -> None:
        if self.broken:
            raise ConnectionError("redis down")
        if self.yield_io:
            await asyncio.sleep(0)

    async def set(self, key, value, ex=None, px=None, nx=False, xx=False, **_kw):
        await self._io()
        if nx and key in self.store:
            return None
        if xx and key not in self.store:
            return None
        self.store[key] = value
        return True

    async def get(self, key):
        await self._io()
        v = self.store.get(key)
        return v if isinstance(v, str) else None

    async def delete(self, *keys):
        await self._io()
        for k in keys:
            self.store.pop(k, None)
        return True

    async def exists(self, *keys):
        await self._io()
        return sum(1 for k in keys if k in self.store)

    async def expire(self, *_a, **_kw):
        await self._io()
        return True

    async def sadd(self, key, *members):
        await self._io()
        self.store.setdefault(key, set()).update(members)
        return 1

    async def hset(self, key, field, value):
        await self._io()
        self.store.setdefault(key, {})[field] = value
        return 1

    async def hget(self, key, field):
        await self._io()
        return (self.store.get(key) or {}).get(field)

    async def hgetall(self, key):
        await self._io()
        return dict(self.store.get(key) or {})

    async def hdel(self, key, *fields):
        await self._io()
        h = self.store.get(key) or {}
        for f in fields:
            h.pop(f, None)
        return True


@pytest.fixture
def fake_redis(monkeypatch):
    r = _FakeRedis()

    async def _get_client():
        return r

    monkeypatch.setattr(stream_relay, "_get_client", _get_client)
    monkeypatch.setattr(run_cap, "run_cap_limit", lambda: 5)
    return r


def _index(org: str = _ORG) -> str:
    return f"cc:{org}:liveruns"


def _slots(org: str = _ORG) -> str:
    return f"cc:{org}:runslots"


def _live(r: _FakeRedis, tid: str, actor: str, *, owner: str | None = None,
          heartbeat: bool = True, active: bool = True) -> None:
    """One entry in the org's index, as ``register_live_run`` writes it."""
    r.store.setdefault(_index(), {})[tid] = json.dumps(
        {"actor": actor, "startedAt": "2026-10-10T00:00:00+00:00", "token": tid},
    )
    if active:
        r.store[f"cc:active:{tid}"] = "1"
    if owner:
        r.store[run_liveness.run_owner_key(tid)] = owner
        if heartbeat:
            r.store[run_liveness.instance_key(owner)] = "1"


def _admit(member: str | None = _ALICE, thread: str = "t-new", org: str | None = _ORG):
    return run_cap.admit_member_run(
        organization_id=org, member=member, thread_id=thread,
    )


# ---------------------------------------------------------------------------
# The count
# ---------------------------------------------------------------------------

def test_the_fifth_run_is_allowed_and_the_sixth_is_refused(fake_redis):
    for i in range(4):
        _live(fake_redis, f"t-{i}", _ALICE)
    fifth = asyncio.run(_admit(thread="t-5th"))
    assert fifth.admitted and fifth.limit == 5

    # The fifth registers as its stream starts, so it holds its slot in the
    # index from then on, and the reservation goes.
    asyncio.run(stream_relay.register_live_run(
        "t-5th", organization_id=_ORG, actor=_ALICE, token="tok",
    ))
    fake_redis.store["cc:active:t-5th"] = "1"
    assert "t-5th" not in fake_redis.store.get(_slots(), {})

    sixth = asyncio.run(_admit(thread="t-6th"))
    assert not sixth.admitted
    assert sixth.limit == 5
    assert sorted(sixth.running) == ["t-0", "t-1", "t-2", "t-3", "t-5th"]
    # A refused run reserves nothing.
    assert "t-6th" not in fake_redis.store.get(_slots(), {})


def test_a_reservation_holds_its_slot_until_the_run_registers(fake_redis):
    """Five admitted runs that have not started yet still hold five slots."""
    for i in range(5):
        assert asyncio.run(_admit(thread=f"r-{i}")).admitted
    refused = asyncio.run(_admit(thread="r-5"))
    assert not refused.admitted
    assert sorted(refused.running) == [f"r-{i}" for i in range(5)]


def test_a_lapsed_reservation_frees_its_slot(fake_redis):
    for i in range(5):
        assert asyncio.run(_admit(thread=f"r-{i}")).admitted
    for tid in list(fake_redis.store[_slots()]):
        meta = json.loads(fake_redis.store[_slots()][tid])
        meta["at"] -= run_cap.SLOT_TTL_SECONDS + 1
        fake_redis.store[_slots()][tid] = json.dumps(meta)
    assert asyncio.run(_admit(thread="r-5")).admitted
    # The lapsed ones are swept on the way.
    assert set(fake_redis.store[_slots()]) == {"r-5"}


def test_a_released_reservation_frees_its_slot(fake_redis):
    for i in range(5):
        assert asyncio.run(_admit(thread=f"r-{i}")).admitted
    asyncio.run(run_cap.release_member_slot(organization_id=_ORG, thread_id="r-0"))
    assert asyncio.run(_admit(thread="r-5")).admitted


def test_the_thread_about_to_start_is_never_counted(fake_redis):
    """A new run on a thread replaces the run on it, so it takes no new slot."""
    for i in range(5):
        _live(fake_redis, f"t-{i}", _ALICE)
    again = asyncio.run(_admit(thread="t-0"))
    assert again.admitted
    assert "t-0" not in again.running


def test_dead_and_ended_runs_hold_no_slot(fake_redis):
    """#797: a run is live only while a process holds it."""
    _live(fake_redis, "t-live-a", _ALICE)
    _live(fake_redis, "t-live-b", _ALICE, owner="box:1:aaaa", heartbeat=True)
    # The process that held these died: its heartbeat key is gone.
    _live(fake_redis, "t-dead-1", _ALICE, owner="box:2:dead", heartbeat=False)
    _live(fake_redis, "t-dead-2", _ALICE, owner="box:2:dead", heartbeat=False)
    # These ended without their finally: no active flag.
    _live(fake_redis, "t-ended-1", _ALICE, active=False)
    _live(fake_redis, "t-ended-2", _ALICE, active=False)
    got = asyncio.run(_admit())
    assert got.admitted
    assert sorted(got.running) == ["t-live-a", "t-live-b"]


def test_other_members_and_automation_never_count(fake_redis):
    for i in range(5):
        _live(fake_redis, f"bob-{i}", _BOB)
    # An entry with no actor: a run that no session member started.
    for i in range(5):
        _live(fake_redis, f"auto-{i}", "")
    got = asyncio.run(_admit())
    assert got.admitted and got.running == ()


def test_a_caller_with_no_member_is_never_capped(fake_redis):
    for i in range(5):
        _live(fake_redis, f"t-{i}", "")
    for who in (None, "", "anonymous", "service-account"):
        got = asyncio.run(_admit(member=who))
        assert got.admitted, who
    assert _slots() not in fake_redis.store


def test_the_member_address_is_matched_without_case(fake_redis):
    for i in range(5):
        _live(fake_redis, f"t-{i}", _ALICE)
    assert not asyncio.run(_admit(member="Alice@Cap.Test")).admitted


def test_no_org_means_no_cap(fake_redis):
    for i in range(5):
        _live(fake_redis, f"t-{i}", _ALICE)
    assert asyncio.run(_admit(org=None)).admitted


def test_zero_turns_the_cap_off(fake_redis, monkeypatch):
    monkeypatch.setattr(run_cap, "run_cap_limit", lambda: 0)
    for i in range(9):
        _live(fake_redis, f"t-{i}", _ALICE)
    got = asyncio.run(_admit())
    assert got.admitted and got.limit == 0
    assert _slots() not in fake_redis.store


def test_a_redis_error_admits_the_run(fake_redis):
    fake_redis.broken = True
    assert asyncio.run(_admit()).admitted


def test_the_member_address_never_goes_in_a_key(fake_redis):
    assert asyncio.run(_admit()).admitted
    assert not any(_ALICE in k for k in fake_redis.store)


# ---------------------------------------------------------------------------
# The race
# ---------------------------------------------------------------------------

def test_six_concurrent_sends_admit_exactly_five(fake_redis):
    fake_redis.yield_io = True

    async def _go():
        return await asyncio.gather(*(
            _admit(thread=f"c-{i}") for i in range(6)
        ))

    results = asyncio.run(_go())
    assert sum(1 for a in results if a.admitted) == 5
    assert sum(1 for a in results if not a.admitted) == 1


def test_two_concurrent_sends_on_the_last_slot_admit_one(fake_redis):
    fake_redis.yield_io = True
    for i in range(4):
        _live(fake_redis, f"t-{i}", _ALICE)

    async def _go():
        return await asyncio.gather(_admit(thread="c-a"), _admit(thread="c-b"))

    a, b = asyncio.run(_go())
    assert [a.admitted, b.admitted].count(True) == 1
    # The lock is gone after both checks.
    assert not any(":runcap:" in k for k in fake_redis.store)


def test_a_lock_left_by_a_dead_holder_is_waited_out(fake_redis, monkeypatch):
    """The lock has a TTL. A send that cannot take it in time still runs
    the check, and logs it (the documented over-admit of one)."""
    monkeypatch.setattr(run_cap, "LOCK_WAIT_SECONDS", 0.05)
    lock = f"cc:{_ORG}:runcap:{run_cap._digest(_ALICE)}"
    fake_redis.store[lock] = "someone-else"
    assert asyncio.run(_admit()).admitted
    # The send did not delete a lock it does not hold.
    assert fake_redis.store[lock] == "someone-else"


# ---------------------------------------------------------------------------
# The setting
# ---------------------------------------------------------------------------

def test_the_setting_defaults_to_five_and_is_a_platform_variable():
    from acb_common.env_guard import is_platform_env
    from acb_common.settings import Settings

    assert Settings.model_fields["chat_max_runs_per_member"].default == 5
    assert is_platform_env("CHAT_MAX_RUNS_PER_MEMBER")


def test_the_limit_reader_clamps_a_negative_value(monkeypatch):
    from acb_common import settings as settings_mod

    monkeypatch.setattr(
        settings_mod, "get_settings",
        lambda: type("S", (), {"chat_max_runs_per_member": -3})(),
    )
    assert run_cap.run_cap_limit() == 0


# ---------------------------------------------------------------------------
# The route: where the check sits
# ---------------------------------------------------------------------------

class _RouteWorld:
    def __init__(self) -> None:
        self.route = "ENGAGE"
        self.calls: list[str] = []


@pytest.fixture
def route_world(monkeypatch, fake_redis):
    import acb_memory
    import orchestrator.executor as executor
    from fastapi import status
    from fastapi.responses import JSONResponse
    from gateway.routes import agent
    from orchestrator.steer import Route, TurnDecision

    w = _RouteWorld()

    async def _noop(*_a, **_k):
        return None

    async def _route(thread_id, actor, text_):
        return TurnDecision(getattr(Route, w.route), "test")

    async def _apply(decision, req, agent_name, actor, room):
        if decision.route is Route.ENGAGE:
            return None
        w.calls.append("steer")
        return JSONResponse(
            status_code=status.HTTP_202_ACCEPTED,
            content={"steered": True, "threadId": req.thread_id},
        )

    async def _memory(**_k):
        w.calls.append("memory")
        return ""

    async def _episode(**_k):
        w.calls.append("episode")

    async def _mint(*_a, **_k):
        w.calls.append("mint")

    async def _gen():
        yield "unused"

    def _run_agent_stream(agent_name, payload, **_k):
        w.calls.append("executor")
        return _gen()

    async def _run_detached(thread_id, gen, **_k):
        w.calls.append("run")
        yield {"type": "RUN_STARTED", "threadId": thread_id}

    monkeypatch.setattr(agent, "_resolve_room", lambda *_a, **_k: None)
    monkeypatch.setattr(agent, "assert_can_run_agent_in_session", _noop)
    monkeypatch.setattr(agent, "_prepare_if_new_thread", _noop)
    monkeypatch.setattr(agent, "_refuse_if_another_run_is_active", _noop)
    monkeypatch.setattr(agent, "_route_incoming_turn", _route)
    monkeypatch.setattr(agent, "_apply_turn_decision", _apply)
    monkeypatch.setattr(agent, "_mint_run_row_bounded", _mint)
    monkeypatch.setattr(
        agent, "_address_agent", lambda req, room, org=None: "orchestrator",
    )
    monkeypatch.setattr(stream_relay, "run_detached", _run_detached)
    monkeypatch.setattr(executor, "run_agent_stream", _run_agent_stream)
    monkeypatch.setattr(acb_memory, "get_session_memory", _memory)
    monkeypatch.setattr(acb_memory, "add_episode", _episode)
    return w


def _client(email: str):
    from acb_auth import UserContext, get_current_user
    from acb_auth.roles import UserRole
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from gateway.routes.agent import run_agent_stream_endpoint

    user = UserContext(email=email, role=UserRole.EMPLOYEE, organization_id=_ORG)
    app = FastAPI()
    app.post("/agent/run/stream")(run_agent_stream_endpoint)
    app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app)


def _body(thread: str) -> dict[str, Any]:
    return {
        "agent": "orchestrator",
        "thread_id": thread,
        "assistant_message_id": f"a-{thread}",
        "payload": {
            "mode": "chat", "message": "Plan the release", "messages": [],
            "user_message_id": f"u-{thread}", "user_message_ts": 1,
        },
    }


def test_a_refused_run_saves_nothing_and_mints_nothing(route_world, fake_redis):
    for i in range(5):
        _live(fake_redis, f"t-{i}", _ALICE)
    r = _client(_ALICE).post("/agent/run/stream", json=_body("t-6th"))
    assert r.status_code == 429, r.text
    assert r.json() == {
        "error": "too_many_runs", "limit": 5,
        "running": ["t-0", "t-1", "t-2", "t-3", "t-4"],
    }
    # No memory read, no Graphiti episode, no prompt save or mint, no run.
    assert route_world.calls == []


def test_an_admitted_run_starts_and_mints(route_world, fake_redis):
    for i in range(4):
        _live(fake_redis, f"t-{i}", _ALICE)
    with _client(_ALICE).stream("POST", "/agent/run/stream", json=_body("t-5th")) as r:
        assert r.status_code == 200
        r.read()
    assert "mint" in route_world.calls and "run" in route_world.calls
    assert route_world.calls.index("mint") < route_world.calls.index("run")


def test_a_steer_into_your_own_live_run_is_not_counted(route_world, fake_redis):
    """Five runs live, and the sixth message steers into one of them."""
    for i in range(5):
        _live(fake_redis, f"t-{i}", _ALICE)
    route_world.route = "STEER"
    r = _client(_ALICE).post("/agent/run/stream", json=_body("t-0"))
    assert r.status_code == 202, r.text
    assert route_world.calls == ["steer"]
    # The steer reserved nothing either.
    assert _slots() not in fake_redis.store


def test_another_members_runs_do_not_refuse_you(route_world, fake_redis):
    for i in range(5):
        _live(fake_redis, f"bob-{i}", _BOB)
    with _client(_ALICE).stream("POST", "/agent/run/stream", json=_body("t-a")) as r:
        assert r.status_code == 200
        r.read()


def test_a_service_caller_is_not_capped(route_world, fake_redis):
    for i in range(5):
        _live(fake_redis, f"t-{i}", "")
    with _client("svc-scheduler").stream(
        "POST", "/agent/run/stream", json=_body("t-svc"),
    ) as r:
        assert r.status_code == 200
        r.read()


def test_a_refusal_after_the_cap_frees_the_reservation(route_world, fake_redis, monkeypatch):
    """A run the cap admitted and a later check refused never starts, so it
    must not hold a slot for the next minute."""
    from fastapi import HTTPException
    from gateway.routes import agent

    async def _busy(thread_id, actor):
        raise HTTPException(status_code=409, detail={"error": "run_in_progress"})

    monkeypatch.setattr(agent, "_refuse_if_another_run_is_active", _busy)
    r = _client(_ALICE).post("/agent/run/stream", json=_body("t-busy"))
    assert r.status_code == 409
    assert "t-busy" not in fake_redis.store.get(_slots(), {})


def test_a_refused_send_shows_the_room_nothing(route_world, fake_redis, monkeypatch):
    """Review of #821: the room's USER_MESSAGE goes out after the cap, so a
    refused send never shows the others a turn that did not run."""
    from types import SimpleNamespace

    from gateway.routes import agent

    published: list[dict] = []

    async def _publish(thread_id, event):
        published.append(event)

    room = SimpleNamespace(
        can_send=True, is_shared=True, members=[_ALICE, _BOB],
        unknown_session=False, resolve_failed=False,
    )
    monkeypatch.setattr(agent, "_resolve_room", lambda *_a, **_k: room)
    monkeypatch.setattr(agent, "publish_room_event", _publish)
    monkeypatch.setattr(agent, "_room_preview", lambda req: "Plan the release")
    for i in range(5):
        _live(fake_redis, f"t-{i}", _ALICE)
    r = _client(_ALICE).post("/agent/run/stream", json=_body("t-room"))
    assert r.status_code == 429, r.text
    assert published == []

    # A steer in the same room still tells the room.
    route_world.route = "STEER"
    r = _client(_ALICE).post("/agent/run/stream", json=_body("t-0"))
    assert r.status_code == 202, r.text
    assert [e["type"] for e in published] == ["USER_MESSAGE"]
