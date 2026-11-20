"""``GET /chat/active-sessions`` lists only the caller's org's runs that the caller can see.

🔴 **Security fix: a cross-tenant leak of live thread ids.** The route used to
SCAN ``cc:active:*``, a key with no tenant, so it read the live runs of EVERY
organization. It then read ``chat_session`` under the caller's tenant. FORCE RLS
hides another org's row, so that row looked like one that did not exist yet,
and the "no session row yet" fallback appended the other org's thread id. The
Postgres-error branch returned every id it had scanned.

The fix (``stream_relay`` live-run index):

* a run start writes ``cc:<org>:liveruns`` through the tenant-prefix wrapper,
  with the server-side org and actor;
* the route reads ONE hash for the caller's org, and never scans;
* a row that exists must pass ``SESSION_VISIBLE_SQL`` for the caller;
* a run with no row yet is listed only for the member who started it;
* on a Postgres error, only the caller's own runs are listed.

Two halves:

* **Hermetic** (always runs): the index, the no-scan fence, the DB-error case
  with two orgs, and the run-start wiring.
* **R8** (``TENANT_LADDER_DATABASE_URL``): the real handler against a real
  FORCE-RLS catalog, as the non-privileged role, with two orgs.

Mutations this suite catches (R7):

* the old route restored (``cc:active:*`` SCAN plus the unconditional
  fallback): ``test_no_scan`` and the cross-org cases go red;
* the fallback without the actor check: a same-org member's no-row run leaks
  to Alice, and ``test_a_colleague_s_no_row_run_stays_hidden`` goes red;
* the DB-error branch returning every id: ``test_db_error_lists_only_own``
  goes red.

RESUME-3d (task 3785): the live row of a continuation run names its reply
row (``messageId``). The mutations:

* ``start_detached`` names the reply row of a plain run too:
  ``test_start_detached_names_the_reply_row_only_for_a_continuation`` goes
  red;
* the route sends ``messageId`` with a flag off:
  ``test_a_flag_off_sends_the_rows_of_before_byte_for_byte`` goes red;
* a re-register drops the reply row:
  ``test_a_re_register_of_the_same_run_keeps_its_reply_row`` goes red;
* the route reads the reply row of a row it does not list:
  ``test_a_reply_row_reaches_only_a_reader_who_may_see_the_chat`` (R8)
  goes red.

Run::

    TENANT_LADDER_DATABASE_URL=postgresql+psycopg://acb:acb@127.0.0.1:5434/<private_db> \\
        uv run pytest tests/unit/test_active_sessions_tenant.py -v -rs
"""
from __future__ import annotations

import asyncio
import fnmatch
import json
import uuid
from typing import Any

import pytest

stream_relay = pytest.importorskip(
    "orchestrator.stream_relay", reason="orchestrator not installed",
)

_ALICE = "alice@as-a.test"
_BOB = "bob@as-a.test"
_CAROL = "carol@as-b.test"


# ---------------------------------------------------------------------------
# A fake raw Redis. It answers the relay's keys, and it RECORDS every scan.
# ---------------------------------------------------------------------------

class _FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, Any] = {}
        self.scans: list[str] = []

    # strings
    async def set(self, key, value, ex=None, xx=False, **_kw):
        if xx and key not in self.store:
            return None
        self.store[key] = value
        return True

    async def get(self, key):
        v = self.store.get(key)
        return v if isinstance(v, str) else None

    async def delete(self, *keys):
        for k in keys:
            self.store.pop(k, None)
        return True

    async def expire(self, *_a, **_kw):
        return True

    async def exists(self, *keys):
        return sum(1 for k in keys if k in self.store)

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
        for f in fields:
            h.pop(f, None)
        return True

    # the keyspace walk this fix removes — recorded, and answered for real so
    # a restored SCAN behaves as it did in production
    async def scan(self, cursor=0, match=None, count=None):
        self.scans.append(str(match))
        return 0, [k for k in self.store if fnmatch.fnmatchcase(k, match or "*")]

    async def scan_iter(self, match=None, count=None):
        self.scans.append(str(match))
        for k in list(self.store):
            if fnmatch.fnmatchcase(k, match or "*"):
                yield k

    async def keys(self, pattern="*"):
        self.scans.append(str(pattern))
        return [k for k in self.store if fnmatch.fnmatchcase(k, pattern)]

    async def aclose(self):
        return None


@pytest.fixture
def fake_redis(monkeypatch):
    r = _FakeRedis()

    async def _get_client():
        return r

    monkeypatch.setattr(stream_relay, "_get_client", _get_client)
    # Any module that opens its own client gets the same fake, so a restored
    # `aioredis.from_url(...)` + SCAN is SEEN rather than failing to connect.
    import redis.asyncio as aioredis

    monkeypatch.setattr(aioredis, "from_url", lambda *_a, **_kw: r)
    return r


def _user(email: str, org: str | None):
    from acb_auth import UserContext
    from acb_auth.roles import UserRole

    return UserContext(email=email, role=UserRole.EMPLOYEE, organization_id=org)


def _start(tid: str, org: str, actor: str) -> None:
    """What a run start does: the active flag, then the org index entry."""
    async def _go() -> None:
        await stream_relay.mark_active(tid, reset=True, actor=actor)
        await stream_relay.register_live_run(
            tid, organization_id=org, actor=actor, token=f"tok-{tid}",
        )
    asyncio.run(_go())


def _list(user) -> list[dict]:
    from gateway.routes.chat import list_active_sessions

    return asyncio.run(list_active_sessions(user=user))


def _ids(rows: list[dict]) -> set[str]:
    return {r["threadId"] for r in rows}


# ---------------------------------------------------------------------------
# Hermetic: the index itself
# ---------------------------------------------------------------------------

def test_the_index_is_one_hash_per_org_behind_the_tenant_prefix(fake_redis):
    org = str(uuid.uuid4())
    _start("t-1", org, "Alice@As-A.test ")
    raw = fake_redis.store[f"cc:{org}:liveruns"]
    meta = json.loads(raw["t-1"])
    assert meta["actor"] == _ALICE, "the actor is stored normalised"
    assert meta["startedAt"], "the start time is recorded for the UI"
    assert meta["token"] == "tok-t-1"


def test_a_run_with_no_org_is_never_indexed(fake_redis):
    asyncio.run(stream_relay.register_live_run(
        "t-x", organization_id=None, actor=_ALICE, token="x",
    ))
    assert not any(k.endswith(":liveruns") for k in fake_redis.store)


def test_an_ended_run_is_pruned_from_the_index(fake_redis):
    org = str(uuid.uuid4())
    _start("t-live", org, _ALICE)
    _start("t-dead", org, _ALICE)
    asyncio.run(stream_relay.mark_inactive("t-dead"))  # no index finally ran

    live = asyncio.run(stream_relay.list_live_runs(org))
    assert [r["threadId"] for r in live] == ["t-live"]
    assert "t-dead" not in fake_redis.store[f"cc:{org}:liveruns"]


def test_a_superseded_run_cannot_remove_the_new_run_s_entry(fake_redis):
    org = str(uuid.uuid4())
    _start("t-1", org, _ALICE)  # token tok-t-1
    asyncio.run(stream_relay.unregister_live_run(
        "t-1", organization_id=org, token="the-old-run",
    ))
    assert "t-1" in fake_redis.store[f"cc:{org}:liveruns"]
    asyncio.run(stream_relay.unregister_live_run(
        "t-1", organization_id=org, token="tok-t-1",
    ))
    assert "t-1" not in fake_redis.store[f"cc:{org}:liveruns"]


# ---------------------------------------------------------------------------
# Hermetic: the route, with Postgres down
# ---------------------------------------------------------------------------

@pytest.fixture
def db_down(monkeypatch):
    import acb_graph

    def _boom(*_a, **_kw):
        raise RuntimeError("postgres is down")

    monkeypatch.setattr(acb_graph, "tenant_session", _boom)


def test_no_scan(fake_redis, db_down):
    """The route never walks the keyspace: O(the org's live runs), not O(Redis)."""
    org_a, org_b = str(uuid.uuid4()), str(uuid.uuid4())
    _start("t-alice", org_a, _ALICE)
    _start("t-carol", org_b, _CAROL)

    _list(_user(_ALICE, org_a))
    assert fake_redis.scans == [], (
        f"/chat/active-sessions scanned the keyspace: {fake_redis.scans}"
    )


def test_db_error_lists_only_own(fake_redis, db_down):
    """Postgres down: the caller's own ids, never a colleague's, never org B's."""
    org_a, org_b = str(uuid.uuid4()), str(uuid.uuid4())
    _start("t-alice", org_a, _ALICE)
    _start("t-bob", org_a, _BOB)
    _start("t-carol", org_b, _CAROL)

    rows = _list(_user(_ALICE, org_a))
    assert _ids(rows) == {"t-alice"}, rows
    assert rows[0]["agentName"] == "unknown"

    assert _ids(_list(_user(_CAROL, org_b))) == {"t-carol"}


def test_a_caller_with_no_org_sees_nothing(fake_redis, db_down):
    _start("t-alice", str(uuid.uuid4()), _ALICE)
    assert _list(_user(_ALICE, None)) == []


def test_redis_down_returns_an_empty_list(monkeypatch):
    async def _broken():
        raise ConnectionError("redis is down")

    monkeypatch.setattr(stream_relay, "_get_client", _broken)
    assert _list(_user(_ALICE, str(uuid.uuid4()))) == []


# ---------------------------------------------------------------------------
# WS-51 S5: a degraded list says that it is partial
# ---------------------------------------------------------------------------
#
# The browser marks a chat unread when its run leaves this list. A list that
# lacks runs because Redis, Postgres or the question read failed must say so,
# or one outage reads as "every run ended" (review of #818). Each degraded
# branch sets `X-Runs-Partial: 1`, and a healthy list does not.


def _list_with_response(user):
    from fastapi import Response
    from gateway.routes.chat import list_active_sessions

    resp = Response()
    rows = asyncio.run(list_active_sessions(user=user, steps=False, response=resp))
    return rows, resp.headers.get("X-Runs-Partial")


def test_the_route_takes_the_response_from_fastapi():
    import inspect
    import typing

    from fastapi import Response
    from gateway.routes import chat

    hints = typing.get_type_hints(chat.list_active_sessions)
    assert hints["response"] is Response
    assert "response" in inspect.signature(chat.list_active_sessions).parameters


def test_redis_down_marks_the_list_partial(monkeypatch):
    async def _broken():
        raise ConnectionError("redis is down")

    monkeypatch.setattr(stream_relay, "_get_client", _broken)
    rows, partial = _list_with_response(_user(_ALICE, str(uuid.uuid4())))
    assert rows == []
    assert partial == "1"


def test_db_down_marks_the_list_partial(fake_redis, db_down):
    org = str(uuid.uuid4())
    _start("t-alice", org, _ALICE)
    _start("t-bob", org, _BOB)
    rows, partial = _list_with_response(_user(_ALICE, org))
    assert _ids(rows) == {"t-alice"}
    assert partial == "1"


@pytest.fixture
def db_empty(monkeypatch):
    """A healthy Postgres that holds no chat row, so no branch is degraded."""
    import acb_graph
    from contextlib import contextmanager

    class _Rows:
        def fetchall(self):
            return []

    class _Session:
        def execute(self, *_a, **_kw):
            return _Rows()

    @contextmanager
    def _ok(_org):
        yield _Session()

    monkeypatch.setattr(acb_graph, "tenant_session", _ok)


def test_a_failed_question_read_marks_the_list_partial(fake_redis, db_empty, monkeypatch):
    from orchestrator import pending_ask

    def _boom(*_a, **_kw):
        raise RuntimeError("pending asks unreadable")

    monkeypatch.setattr(pending_ask, "durable_asks_enabled", lambda: True)
    monkeypatch.setattr(pending_ask, "waiting_asks", _boom)
    org = str(uuid.uuid4())
    _start("t-alice", org, _ALICE)
    rows, partial = _list_with_response(_user(_ALICE, org))
    assert partial == "1"
    assert all(r["state"] == "running" for r in rows)


def test_a_complete_list_is_not_partial(fake_redis, db_empty):
    """No error anywhere: no header. Postgres answers with no rows."""
    org = str(uuid.uuid4())
    _start("t-alice", org, _ALICE)
    rows, partial = _list_with_response(_user(_ALICE, org))
    assert _ids(rows) == {"t-alice"}
    assert partial is None


# ---------------------------------------------------------------------------
# Hermetic: a run start writes the index, and its end removes it
# ---------------------------------------------------------------------------

def test_run_detached_indexes_the_run_under_its_server_side_org(monkeypatch):
    calls: list[tuple[str, dict]] = []

    async def _reg(tid, **kw):
        calls.append(("register", {"tid": tid, **kw}))

    async def _unreg(tid, **kw):
        calls.append(("unregister", {"tid": tid, **kw}))

    async def _noop(*_a, **_kw):
        return None

    async def _sub(_tid, since_id="0"):
        yield {"type": "RUN_FINISHED"}

    async def _gen():
        if False:  # pragma: no cover - a generator that yields nothing
            yield ""

    monkeypatch.setattr(stream_relay, "register_live_run", _reg)
    monkeypatch.setattr(stream_relay, "unregister_live_run", _unreg)
    monkeypatch.setattr(stream_relay, "mark_active", _noop)
    monkeypatch.setattr(stream_relay, "mark_inactive", _noop)
    monkeypatch.setattr(stream_relay, "subscribe_events", _sub)
    monkeypatch.setattr(stream_relay, "_start_control_listener", lambda _t: None)
    stream_relay._DETACHED_TASKS.clear()

    async def _go() -> None:
        async for _evt in stream_relay.run_detached(
            "t-run", _gen(), actor=_ALICE, organization_id="org-a",
        ):
            pass
        task = stream_relay._DETACHED_TASKS.get("t-run")
        if task is not None:
            await task

    try:
        asyncio.run(_go())
    finally:
        stream_relay._DETACHED_TASKS.clear()
        stream_relay._LOCAL_CONTROL_HANDLERS.clear()

    kinds = [k for k, _ in calls]
    assert kinds == ["register", "unregister"], calls
    reg, unreg = calls[0][1], calls[1][1]
    assert reg["organization_id"] == "org-a" and reg["actor"] == _ALICE
    assert unreg["organization_id"] == "org-a"
    assert unreg["token"] == reg["token"], "the run removes only its OWN entry"


# ---------------------------------------------------------------------------
# R8: the real handler, a real FORCE-RLS catalog, two orgs
# ---------------------------------------------------------------------------

pytest.importorskip("sqlalchemy")

import acb_graph.db as graph_db  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import NullPool  # noqa: E402

# Resolved by name as fixtures. The imports are load-bearing.
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: E402, F401
    _DB_GATE,
    app_engine,
    promoted,
)


@pytest.fixture(scope="module")
def world(promoted):  # noqa: F811
    """Alice and Bob in org A, Carol in org B, and their chat rows.

    Seeded as the superuser, so org B's rows really are in the table. Only
    RLS stands between them and Alice.
    """
    ns = promoted
    ns.t = {k: f"as-{k}-{uuid.uuid4().hex[:8]}" for k in (
        "alice_row", "alice_norow", "bob_row", "bob_norow",
        "carol_row", "carol_norow",
    )}
    with promoted.admin_engine.begin() as c:
        for email, org in ((_ALICE, ns.org_a), (_BOB, ns.org_a),
                           (_CAROL, ns.org_b)):
            c.execute(text(
                "INSERT INTO app_user (email, display_name, role, status, "
                "organization_id) VALUES (:e, :e, 'employee', 'active', :o) "
                "ON CONFLICT DO NOTHING"), {"e": email, "o": org})
        for key, email, org in (("alice_row", _ALICE, ns.org_a),
                                ("bob_row", _BOB, ns.org_a),
                                ("carol_row", _CAROL, ns.org_b)):
            c.execute(text(
                "INSERT INTO chat_session (id, user_id, agent_name, title, "
                "organization_id) VALUES (:id, :u, 'orchestrator', :t, :o)"),
                {"id": ns.t[key], "u": email, "t": f"title {key}", "o": org})
    return ns


@pytest.fixture
def graph_as_app(world, monkeypatch):
    """``acb_graph`` sessions open as the NON-privileged role, one backend each."""
    eng = create_engine(world.app_url, poolclass=NullPool, future=True)
    factory = sessionmaker(bind=eng, expire_on_commit=False, future=True)
    monkeypatch.setattr(graph_db, "_session_factory", lambda: factory)
    try:
        yield world
    finally:
        eng.dispose()


@pytest.fixture
def live(graph_as_app, fake_redis):
    """Every seeded thread has a live run, indexed under its own org."""
    w = graph_as_app
    for key, email, org in (
        ("alice_row", _ALICE, w.org_a), ("alice_norow", _ALICE, w.org_a),
        ("bob_row", _BOB, w.org_a), ("bob_norow", _BOB, w.org_a),
        ("carol_row", _CAROL, w.org_b), ("carol_norow", _CAROL, w.org_b),
    ):
        _start(w.t[key], org, email)
    return w


@_DB_GATE
def test_the_rls_catalog_really_hides_org_b_from_alice(live):
    """Guard on the fixture: org B's row exists, and Alice's tenant cannot see it."""
    from acb_graph import tenant_session

    with live.admin_engine.connect() as c:
        assert c.execute(text("SELECT 1 FROM chat_session WHERE id = :i"),
                         {"i": live.t["carol_row"]}).first() is not None
    with tenant_session(live.org_a) as s:
        assert s.execute(text("SELECT 1 FROM chat_session WHERE id = :i"),
                         {"i": live.t["carol_row"]}).first() is None


@_DB_GATE
def test_org_b_s_live_threads_never_reach_org_a(live):
    """THE test. Neither Carol's row-backed run nor her no-row run leaks to Alice."""
    got = _ids(_list(_user(_ALICE, live.org_a)))
    assert live.t["carol_row"] not in got
    assert live.t["carol_norow"] not in got


@_DB_GATE
def test_the_caller_s_own_runs_are_listed(live):
    rows = {r["threadId"]: r for r in _list(_user(_ALICE, live.org_a))}
    own_row = rows[live.t["alice_row"]]
    assert own_row["agentName"] == "orchestrator"
    assert own_row["title"] == "title alice_row"
    assert own_row["startedAt"]
    # The no-row fallback survives for the run the caller started.
    own_norow = rows[live.t["alice_norow"]]
    assert own_norow["agentName"] == "unknown" and own_norow["title"] is None


@_DB_GATE
def test_a_colleague_s_no_row_run_stays_hidden(live):
    """Same org, other member: a private row hides, and a no-row run hides too."""
    got = _ids(_list(_user(_ALICE, live.org_a)))
    assert live.t["bob_row"] not in got, "SESSION_VISIBLE_SQL must apply"
    assert live.t["bob_norow"] not in got, (
        "the no-row fallback is for the caller's OWN run only"
    )
    assert got == {live.t["alice_row"], live.t["alice_norow"]}


@_DB_GATE
def test_each_org_sees_only_its_own(live):
    assert _ids(_list(_user(_CAROL, live.org_b))) == {
        live.t["carol_row"], live.t["carol_norow"],
    }


@_DB_GATE
def test_a_shared_room_shows_the_run_to_every_participant(live):
    """A room member sees a colleague's live run: the dot is for everyone in it."""
    with live.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO chat_session_participant (session_id, subject, role, "
            "organization_id) VALUES (:s, :u, 'member', :o) ON CONFLICT DO NOTHING"),
            {"s": live.t["bob_row"], "u": _BOB, "o": live.org_a})
        c.execute(text(
            "INSERT INTO chat_session_participant (session_id, subject, role, "
            "organization_id) VALUES (:s, :u, 'member', :o) ON CONFLICT DO NOTHING"),
            {"s": live.t["bob_row"], "u": _ALICE, "o": live.org_a})
    try:
        assert live.t["bob_row"] in _ids(_list(_user(_ALICE, live.org_a)))
    finally:
        with live.admin_engine.begin() as c:
            c.execute(text(
                "DELETE FROM chat_session_participant WHERE session_id = :s"),
                {"s": live.t["bob_row"]})


@_DB_GATE
def test_no_scan_against_the_real_catalog(live):
    _list(_user(_ALICE, live.org_a))
    import redis.asyncio as aioredis

    fake = aioredis.from_url("redis://unused")
    assert fake.scans == []


# ---------------------------------------------------------------------------
# §4.10 (re-review of #1171): one identity per run
# ---------------------------------------------------------------------------

def test_a_re_register_of_the_same_run_keeps_its_start_and_its_id(fake_redis):
    """run_detached registers first, then the executor registers the SAME run
    again. The token changes (the executor's finally removes its own entry),
    but startedAt and runId stay: a moved startedAt read as a new run."""
    org = str(uuid.uuid4())
    asyncio.run(stream_relay.register_live_run(
        "t-1", organization_id=org, actor=_ALICE, token="detached-tok", run_id="run-1",
    ))
    first = json.loads(fake_redis.store[f"cc:{org}:liveruns"]["t-1"])
    assert first["runId"] == "run-1"
    asyncio.run(stream_relay.register_live_run(
        "t-1", organization_id=org, actor=_ALICE, token="run-1",
        run_id="run-1", keep_run_facts=True,
    ))
    again = json.loads(fake_redis.store[f"cc:{org}:liveruns"]["t-1"])
    assert again["startedAt"] == first["startedAt"]
    assert again["runId"] == "run-1"
    # What the re-register is for: the executor's token now holds the entry.
    assert again["token"] == "run-1"
    asyncio.run(stream_relay.unregister_live_run("t-1", organization_id=org, token="run-1"))
    assert "t-1" not in fake_redis.store[f"cc:{org}:liveruns"]


def test_a_re_register_never_keeps_another_run_s_facts(fake_redis):
    org = str(uuid.uuid4())
    asyncio.run(stream_relay.register_live_run(
        "t-1", organization_id=org, actor=_ALICE, token="old", run_id="run-old",
    ))
    old = json.loads(fake_redis.store[f"cc:{org}:liveruns"]["t-1"])
    import time
    time.sleep(0.002)
    asyncio.run(stream_relay.register_live_run(
        "t-1", organization_id=org, actor=_ALICE, token="run-new",
        run_id="run-new", keep_run_facts=True,
    ))
    new = json.loads(fake_redis.store[f"cc:{org}:liveruns"]["t-1"])
    assert new["runId"] == "run-new"
    assert new["startedAt"] != old["startedAt"]


def test_a_first_register_writes_fresh_facts(fake_redis):
    org = str(uuid.uuid4())
    asyncio.run(stream_relay.register_live_run(
        "t-1", organization_id=org, actor=_ALICE, token="a", run_id="run-a",
    ))
    asyncio.run(stream_relay.register_live_run(
        "t-1", organization_id=org, actor=_ALICE, token="b", run_id="run-b",
    ))
    assert json.loads(fake_redis.store[f"cc:{org}:liveruns"]["t-1"])["runId"] == "run-b"


def test_the_route_sends_the_run_id(fake_redis, db_down):
    org = str(uuid.uuid4())
    asyncio.run(stream_relay.mark_active("t-r", reset=True, actor=_ALICE))
    asyncio.run(stream_relay.register_live_run(
        "t-r", organization_id=org, actor=_ALICE, token="tok", run_id="run-r",
    ))
    rows = _list(_user(_ALICE, org))
    assert [(r["threadId"], r["runId"]) for r in rows] == [("t-r", "run-r")]



# ---------------------------------------------------------------------------
# RESUME-3d (task 3785): the live row of a continuation names its reply row
# ---------------------------------------------------------------------------

def _start_continuation(tid: str, org: str, actor: str, run_id: str, mid: str) -> None:
    """What a continuation start does: the active flag, then an entry with
    the run's reply row (``start_detached(continuation=True)``)."""
    async def _go() -> None:
        await stream_relay.mark_active(tid, reset=False, actor=actor)
        await stream_relay.register_live_run(
            tid, organization_id=org, actor=actor, token=f"tok-{tid}",
            run_id=run_id, message_id=mid,
        )
    asyncio.run(_go())


def _resume_flags(monkeypatch, *, resume: str, late: str) -> None:
    from acb_common import get_settings

    monkeypatch.setattr(get_settings(), "chat_resume_orgs", resume)
    monkeypatch.setattr(get_settings(), "chat_late_approve_orgs", late)


def test_a_continuation_entry_names_its_reply_row(fake_redis):
    org = str(uuid.uuid4())
    _start_continuation("t-c", org, _ALICE, "run-c", "reply-c")
    meta = json.loads(fake_redis.store[f"cc:{org}:liveruns"]["t-c"])
    assert meta["messageId"] == "reply-c"
    live = asyncio.run(stream_relay.list_live_runs(org))
    assert live == [{
        "threadId": "t-c", "actor": _ALICE, "startedAt": meta["startedAt"],
        "runId": "run-c", "messageId": "reply-c",
    }]


def test_a_plain_entry_names_no_reply_row(fake_redis):
    """A plain run's entry and its listed row are the shape of before."""
    org = str(uuid.uuid4())
    asyncio.run(stream_relay.mark_active("t-p", reset=True, actor=_ALICE))
    for mid in (None, "", "   "):
        asyncio.run(stream_relay.register_live_run(
            "t-p", organization_id=org, actor=_ALICE, token="tok", run_id="run-p",
            message_id=mid,
        ))
        meta = json.loads(fake_redis.store[f"cc:{org}:liveruns"]["t-p"])
        assert set(meta) == {"actor", "startedAt", "token", "runId"}
        live = asyncio.run(stream_relay.list_live_runs(org))
        assert [set(r) for r in live] == [{"threadId", "actor", "startedAt", "runId"}]


def test_a_re_register_of_the_same_run_keeps_its_reply_row(fake_redis):
    """The executor registers the SAME run again with no reply row. The entry
    keeps the reply row that the continuation start wrote."""
    org = str(uuid.uuid4())
    _start_continuation("t-c", org, _ALICE, "run-c", "reply-c")
    asyncio.run(stream_relay.register_live_run(
        "t-c", organization_id=org, actor=_ALICE, token="run-c",
        run_id="run-c", keep_run_facts=True,
    ))
    meta = json.loads(fake_redis.store[f"cc:{org}:liveruns"]["t-c"])
    assert meta["messageId"] == "reply-c" and meta["token"] == "run-c"


def test_a_re_register_never_keeps_another_run_s_reply_row(fake_redis):
    org = str(uuid.uuid4())
    _start_continuation("t-c", org, _ALICE, "run-old", "reply-old")
    asyncio.run(stream_relay.register_live_run(
        "t-c", organization_id=org, actor=_ALICE, token="run-new",
        run_id="run-new", keep_run_facts=True,
    ))
    meta = json.loads(fake_redis.store[f"cc:{org}:liveruns"]["t-c"])
    assert meta["runId"] == "run-new" and "messageId" not in meta
    # A plain register of a new run never keeps it either.
    _start_continuation("t-d", org, _ALICE, "run-d", "reply-d")
    asyncio.run(stream_relay.register_live_run(
        "t-d", organization_id=org, actor=_ALICE, token="b", run_id="run-e",
    ))
    assert "messageId" not in json.loads(fake_redis.store[f"cc:{org}:liveruns"]["t-d"])


@pytest.mark.parametrize("continuation", [True, False])
def test_start_detached_names_the_reply_row_only_for_a_continuation(fake_redis, monkeypatch, continuation):
    """The reply row comes from the run record (``messageId``), and only a
    continuation start passes it to the index."""
    calls: list[dict] = []

    async def _reg(tid, **kw):
        calls.append(kw)

    async def _noop(*_a, **_kw):
        return None

    async def _gen():
        if False:  # pragma: no cover - a generator that yields nothing
            yield ""

    from orchestrator import run_liveness

    monkeypatch.setattr(stream_relay, "register_live_run", _reg)
    monkeypatch.setattr(stream_relay, "unregister_live_run", _noop)
    monkeypatch.setattr(stream_relay, "mark_active", _noop)
    monkeypatch.setattr(stream_relay, "mark_inactive", _noop)
    monkeypatch.setattr(stream_relay, "is_active", lambda *_a, **_kw: _false())
    monkeypatch.setattr(stream_relay, "_start_control_listener", lambda _t: None)
    monkeypatch.setattr(run_liveness, "record_instance_run", _noop)
    monkeypatch.setattr(run_liveness, "forget_instance_run", _noop)
    stream_relay._DETACHED_TASKS.clear()

    async def _go() -> None:
        task = await stream_relay.start_detached(
            "t-run", _gen(), actor=_ALICE, organization_id="org-a",
            record={"runId": "run-1", "messageId": "reply-1"},
            exclusive=continuation, continuation=continuation,
        )
        await task

    try:
        asyncio.run(_go())
    finally:
        stream_relay._DETACHED_TASKS.clear()
        stream_relay._LOCAL_CONTROL_HANDLERS.clear()

    assert calls, "the start registers the run"
    assert calls[0]["run_id"] == "run-1"
    assert calls[0]["message_id"] == ("reply-1" if continuation else None)


async def _false() -> bool:
    return False


@pytest.mark.parametrize(("resume", "late"), [("", ""), ("*", ""), ("", "*")])
def test_a_flag_off_sends_the_rows_of_before_byte_for_byte(fake_redis, db_empty, monkeypatch, resume, late):
    """With either flag off, a continuation's row is the row of a plain run."""
    _resume_flags(monkeypatch, resume=resume, late=late)
    org = str(uuid.uuid4())
    _start_continuation("t-c", org, _ALICE, "run-c", "reply-c")
    with_reply = json.dumps(_list(_user(_ALICE, org)))
    # The same entry, with no reply row: the shape of before.
    k = f"cc:{org}:liveruns"
    meta = json.loads(fake_redis.store[k]["t-c"])
    meta.pop("messageId")
    fake_redis.store[k]["t-c"] = json.dumps(meta)
    assert with_reply == json.dumps(_list(_user(_ALICE, org)))
    assert "messageId" not in with_reply


def test_both_flags_on_send_the_reply_row_of_a_continuation_only(fake_redis, db_empty, monkeypatch):
    org = str(uuid.uuid4())
    _resume_flags(monkeypatch, resume=org, late=org)
    _start_continuation("t-c", org, _ALICE, "run-c", "reply-c")
    _start("t-p", org, _ALICE)
    rows = {r["threadId"]: r for r in _list(_user(_ALICE, org))}
    assert rows["t-c"]["messageId"] == "reply-c"
    assert rows["t-c"]["runId"] == "run-c" and rows["t-c"]["live"] is True
    assert "messageId" not in rows["t-p"]


def test_a_flag_for_another_org_sends_no_reply_row(fake_redis, db_empty, monkeypatch):
    org, other = str(uuid.uuid4()), str(uuid.uuid4())
    _resume_flags(monkeypatch, resume=other, late=other)
    _start_continuation("t-c", org, _ALICE, "run-c", "reply-c")
    assert "messageId" not in json.dumps(_list(_user(_ALICE, org)))


def test_no_reply_row_reads_no_flag(fake_redis, db_empty, monkeypatch):
    """A list with no continuation entry reads neither flag."""
    from acb_skills import late_approve, stored_call

    read: list[str] = []
    monkeypatch.setattr(late_approve, "late_approve_on", lambda *_a: read.append("late") or True)
    monkeypatch.setattr(stored_call, "chat_resume_on", lambda *_a: read.append("resume") or True)
    org = str(uuid.uuid4())
    _start("t-p", org, _ALICE)
    assert _ids(_list(_user(_ALICE, org))) == {"t-p"}
    assert read == []


@_DB_GATE
def test_a_reply_row_reaches_only_a_reader_who_may_see_the_chat(live, monkeypatch):
    """Real FORCE RLS. Both flags name org A only. Alice sees the reply row of
    her own continuation. Bob's private chat and org B's chats never reach
    her, and Carol's org has the flags off."""
    w = live
    _resume_flags(monkeypatch, resume=w.org_a, late=w.org_a)
    for key, email, org in (
        ("alice_row", _ALICE, w.org_a), ("bob_row", _BOB, w.org_a),
        ("bob_norow", _BOB, w.org_a), ("carol_row", _CAROL, w.org_b),
        ("carol_norow", _CAROL, w.org_b),
    ):
        _start_continuation(w.t[key], org, email, f"run-{key}", f"reply-{key}")

    alice = _list(_user(_ALICE, w.org_a))
    by_tid = {r["threadId"]: r for r in alice}
    assert _ids(alice) == {w.t["alice_row"], w.t["alice_norow"]}
    assert by_tid[w.t["alice_row"]]["messageId"] == "reply-alice_row"
    assert "messageId" not in by_tid[w.t["alice_norow"]], "a plain run names no reply"
    text_of = json.dumps(alice)
    for key in ("bob_row", "bob_norow", "carol_row", "carol_norow"):
        assert f"reply-{key}" not in text_of, key

    carol = _list(_user(_CAROL, w.org_b))
    assert _ids(carol) == {w.t["carol_row"], w.t["carol_norow"]}
    assert "messageId" not in json.dumps(carol), "the flags do not name org B"


@_DB_GATE
def test_a_room_member_sees_the_reply_row_of_the_room_s_run(live, monkeypatch):
    """The reader filter is unchanged: a member of the room sees the row,
    and with it the reply row of its continuation."""
    w = live
    _resume_flags(monkeypatch, resume=w.org_a, late=w.org_a)
    _start_continuation(w.t["bob_row"], w.org_a, _BOB, "run-bob", "reply-bob")
    assert w.t["bob_row"] not in _ids(_list(_user(_ALICE, w.org_a)))
    with w.admin_engine.begin() as c:
        for who in (_BOB, _ALICE):
            c.execute(text(
                "INSERT INTO chat_session_participant (session_id, subject, role, "
                "organization_id) VALUES (:s, :u, 'member', :o) ON CONFLICT DO NOTHING"),
                {"s": w.t["bob_row"], "u": who, "o": w.org_a})
    try:
        rows = {r["threadId"]: r for r in _list(_user(_ALICE, w.org_a))}
        assert rows[w.t["bob_row"]]["messageId"] == "reply-bob"
    finally:
        with w.admin_engine.begin() as c:
            c.execute(text(
                "DELETE FROM chat_session_participant WHERE session_id = :s"),
                {"s": w.t["bob_row"]})
