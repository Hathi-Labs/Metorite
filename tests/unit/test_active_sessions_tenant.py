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
