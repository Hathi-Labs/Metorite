"""AGA-5 (task 2108): org B sees nothing of org A in each observability reader.

``project-docs/specs/agents_view.md`` §4 and §7 own this proof. It covers the
six readers of ``apps/services/gateway/gateway/routes/observability.py`` and
the alias read:

* ``GET /observability/activity/recent``
* ``GET /observability/activity/stream`` (SSE)
* ``GET /observability/active``
* ``GET /observability/roster``, with its alias overlay
  (``agent._load_agent_aliases``), its avatars and its app names
* ``GET /observability/cost``
* ``GET /observability/runs``
* ``GET /observability/avatars``, which the spec names "the alias read"

The setup, for each test:

* Postgres is REAL. The H3 rehearsal clones a phase-4 promoted catalog with
  FORCE RLS, and every reader connects as its NOSUPERUSER NOBYPASSRLS role.
  The superuser seeds one row for EACH org in each table that a reader
  touches: ``agent_run``, ``agent_avatars``, ``apps``, ``dynamic_agents`` and
  the ``agent_aliases`` blob of ``model_config``.
* Redis is the in-repo fake of ``test_activity_feed_tenant.py``, patched at
  ``aioredis.from_url``, with the hash arithmetic of
  ``test_cost_rollup_tenant.py``. Each write and each read goes through the
  real ``acb_common.tenant_redis`` prefix wrapper and the real
  ``publish_activity``. CI has no Redis service, and the R8 gate is the
  database gate, so the Redis transport is the one fake.
* Each route runs with the tenant of the viewer bound, as the request
  middleware does.

Each reader test asserts three things, so it cannot pass on an empty answer:

* org A sees its own markers;
* org B sees its own markers;
* the whole answer of org B holds no marker of org A, and the reverse.

The one exception is ``dynamic_agents``. The roster reads it through the
unbound ``get_session()``, so under FORCE RLS no org sees its own row, and the
roster falls back to the one deployment-wide ``agents.json``. That fallback
can show the agent of one org to another (task 2956). The test for it is an
``xfail(strict=True)`` probe. It does NOT prove isolation.

``model_config`` has no row policy. Its ``WHERE organization_id = %s`` is the
only fence of the alias overlay, so this suite is the proof for it.

Mutations this suite catches (R7). Each was run, and each turned it red:

* M1: ``model_config.load_blob`` loses ``organization_id = %s``. Org B reads
  the alias blob of org A: ``test_roster`` and
  ``test_the_agent_alias_blob_reads_only_the_bound_org`` go red.
* M2: ``observability.runs`` opens the unbound ``get_session()`` and not
  ``tenant_session(org)``. Org A loses its own run: ``test_runs`` goes red.

Run::

    TENANT_LADDER_DATABASE_URL=postgresql+psycopg://acb:acb@127.0.0.1:5434/<your_db> \\
        uv run pytest tests/unit/test_observability_two_org_r8.py -v -rs
"""
from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from sqlalchemy import text

from tests.unit import test_activity_feed_tenant as _feed
from tests.unit import test_cost_rollup_tenant as _cost

# Resolved by name as fixtures, so ruff sees them as unused (F401) and the
# test signatures as redefinitions (F811). The imports are load-bearing.
from tests.unit.test_chat_write_under_rls import (  # noqa: F401
    _ALICE,
    _CAROL,
    _user,
    graph_as_app,
    members,
)
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

_ROOT = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# The Redis transport: one store with streams, strings, SCAN and hashes.
# ---------------------------------------------------------------------------

class _Redis(_feed._FakeRedis):
    """The feed fake, plus the real hash arithmetic of the cost fake."""

    def __init__(self) -> None:
        super().__init__()
        self.ttls: dict[str, int] = {}
        self.reads: list[str] = []

    hincrbyfloat = _cost._FakeRedis.hincrbyfloat
    hincrby = _cost._FakeRedis.hincrby
    expire = _cost._FakeRedis.expire
    hgetall = _cost._FakeRedis.hgetall

    def pipeline(self, transaction=False):
        return _cost._FakePipe(self)


@pytest.fixture
def redis_store(monkeypatch):
    import redis.asyncio as aioredis
    from acb_common import activity, tenant_redis

    r = _Redis()
    monkeypatch.setattr(aioredis, "from_url", lambda *_a, **_kw: r)
    tenant_redis.reset_pool_for_tests()
    monkeypatch.setattr(activity, "_client", None)
    yield r
    tenant_redis.reset_pool_for_tests()


# ---------------------------------------------------------------------------
# The two orgs and their markers.
# ---------------------------------------------------------------------------

class _Org:
    """The markers of one org. Each one is unique to this test run."""

    def __init__(self, letter: str, org: str, email: str) -> None:
        tag = uuid.uuid4().hex[:8]
        self.letter = letter
        self.org = org
        self.email = email
        self.agent = f"aga5-{letter}-agent-{tag}"
        self.dyn_agent = f"aga5-{letter}-dyn-{tag}"
        self.run_live = f"aga5-{letter}-live-{tag}"
        self.run_app = f"aga5-{letter}-applive-{tag}"
        self.run_db = f"aga5-{letter}-run-{tag}"
        self.app_slug = f"aga5-{letter}-app-{tag}"
        self.app_name = f"AGA5 App {letter.upper()} {tag}"
        self.alias = f"AGA5 Alias {letter.upper()} {tag}"
        self.model = f"aga5-{letter}-model-{tag}"
        self.colour = f"aga5-{letter}-colour-{tag}"
        self.instance = f"u:{email}"

    def markers(self) -> list[str]:
        """Each string that must never reach the other org."""
        return [self.agent, self.dyn_agent, self.run_live, self.run_app,
                self.run_db, self.app_slug, self.app_name, self.alias,
                self.model, self.colour, self.org]

    def viewer(self):
        return _user(self.email, self.org)


@contextmanager
def _bound(org: str) -> Iterator[None]:
    """The tenant of the request, as the middleware binds it."""
    from acb_common.db import bind_tenant, release_tenant

    token = bind_tenant(org)
    try:
        yield
    finally:
        release_tenant(token)


def _seed_db(promoted, o: _Org) -> None:  # noqa: F811
    """One row of *o* in each table a reader touches, as the superuser."""
    with promoted.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO agent_run (run_id, thread_id, agent_name, user_id, "
            "model, status, organization_id) VALUES (:r, :t, :a, :u, :m, "
            "'completed', CAST(:o AS uuid))"),
            {"r": o.run_db, "t": f"t-{o.run_db}", "a": o.agent, "u": o.email,
             "m": o.model, "o": o.org})
        c.execute(text(
            "INSERT INTO agent_avatars (agent_name, config, organization_id) "
            "VALUES (:n, CAST(:c AS jsonb), CAST(:o AS uuid))"),
            {"n": o.agent, "c": json.dumps({"colour": o.colour}), "o": o.org})
        c.execute(text(
            "INSERT INTO apps (slug, name, icon, owner_email, workspace_path, "
            "organization_id) VALUES (:s, :n, :i, :e, :w, CAST(:o AS uuid))"),
            {"s": o.app_slug, "n": o.app_name, "i": o.letter, "e": o.email,
             "w": f"/tmp/{o.app_slug}", "o": o.org})
        c.execute(text(
            "INSERT INTO dynamic_agents (name, description, organization_id) "
            "VALUES (:n, :d, CAST(:o AS uuid))"),
            {"n": o.dyn_agent, "d": f"{o.dyn_agent} description", "o": o.org})
        c.execute(text(
            "INSERT INTO model_config (organization_id, key, value) "
            "VALUES (CAST(:o AS uuid), 'agent_aliases', CAST(:v AS jsonb)) "
            "ON CONFLICT (organization_id, key) DO UPDATE SET value = EXCLUDED.value"),
            {"o": o.org, "v": json.dumps({"orchestrator": o.alias, o.agent: o.alias})})


def _unseed_db(promoted, orgs: list[_Org]) -> None:  # noqa: F811
    with promoted.admin_engine.begin() as c:
        for o in orgs:
            c.execute(text("DELETE FROM agent_run WHERE run_id = :r"), {"r": o.run_db})
            c.execute(text("DELETE FROM agent_avatars WHERE agent_name = :n"),
                      {"n": o.agent})
            c.execute(text("DELETE FROM apps WHERE slug = :s"), {"s": o.app_slug})
            c.execute(text("DELETE FROM dynamic_agents WHERE name = :n"),
                      {"n": o.dyn_agent})
            c.execute(text(
                "DELETE FROM model_config WHERE organization_id = CAST(:o AS uuid) "
                "AND key = 'agent_aliases'"), {"o": o.org})


async def _seed_feed(o: _Org) -> None:
    """What a live run of *o* writes: an agent start, an app start and a
    priced model call, each under the bound tenant of *o*."""
    from acb_common import publish_activity

    with _bound(o.org):
        publish_activity(kind="agent", phase="start", agent=o.agent,
                         run_id=o.run_live, source="chat", user=o.email)
        publish_activity(kind="app", phase="start", agent=f"app:{o.app_slug}",
                         run_id=o.run_app, source="apps", user=o.email)
        publish_activity(kind="model", model=o.model, agent=o.agent,
                         run_id=o.run_live, source="chat", instance=o.instance,
                         cost_usd=0.25, tokens=100)
    await _feed._drain()


@pytest.fixture
def world(graph_as_app, redis_store, monkeypatch, tmp_path):  # noqa: F811
    """Two orgs with data in every source of every reader."""
    from acb_common import get_settings
    from gateway.routes import agent as agent_routes

    db = graph_as_app
    a = _Org("a", db.org_a, _ALICE)
    b = _Org("b", db.org_b, _CAROL)

    # The alias blob reads through its own psycopg connection. Point it at
    # the app role of the promoted catalog, never at a shared database.
    monkeypatch.setattr(
        get_settings(), "database_url",
        db.app_url.render_as_string(hide_password=False), raising=False)
    # No deployment agents.json leaks into the roster from the checkout.
    monkeypatch.setattr(agent_routes, "_get_agents_file",
                        lambda: tmp_path / "agents.json")

    for o in (a, b):
        _seed_db(db, o)

    async def _go() -> None:
        await _seed_feed(a)
        await _seed_feed(b)

    asyncio.run(_go())
    try:
        yield a, b
    finally:
        _unseed_db(db, [a, b])


def _call(o: _Org, make) -> Any:
    """Run one route coroutine as the viewer *o*, with its tenant bound."""
    async def _go() -> Any:
        with _bound(o.org):
            return await make(o.viewer())

    return asyncio.run(_go())


def _assert_isolated(a: _Org, b: _Org, out_a: Any, out_b: Any,
                     own: list[str] | None = None) -> None:
    """Each org's answer holds none of the other org's markers. *own* names
    the marker attributes that each answer must hold."""
    text_a = json.dumps(out_a, default=str)
    text_b = json.dumps(out_b, default=str)
    for attr in own or []:
        assert getattr(a, attr) in text_a, f"org A lost its own {attr}"
        assert getattr(b, attr) in text_b, f"org B lost its own {attr}"
    leaked_to_b = [m for m in a.markers() if m in text_b]
    leaked_to_a = [m for m in b.markers() if m in text_a]
    assert leaked_to_b == [], f"org B sees org A: {leaked_to_b}"
    assert leaked_to_a == [], f"org A sees org B: {leaked_to_a}"


# ---------------------------------------------------------------------------
# The six readers and the alias read.
# ---------------------------------------------------------------------------

@_DB_GATE
def test_activity_recent(world):
    from gateway.routes.observability import activity_recent

    a, b = world
    out_a = _call(a, lambda v: activity_recent(limit=500, viewer=v))
    out_b = _call(b, lambda v: activity_recent(limit=500, viewer=v))

    assert out_a["count"] == 3 and out_b["count"] == 3
    assert {e["run_id"] for e in out_a["events"]} == {a.run_live, a.run_app}
    assert {e["run_id"] for e in out_b["events"]} == {b.run_live, b.run_app}
    _assert_isolated(a, b, out_a, out_b, own=["agent", "run_live", "model"])


@_DB_GATE
def test_activity_stream(world):
    a, b = world

    async def _frames(o: _Org) -> list[bytes]:
        with _bound(o.org):
            return await _feed._stream_frames(o.viewer())

    frames_a = asyncio.run(_frames(a))
    frames_b = asyncio.run(_frames(b))

    assert frames_b[0] == b": connected\n\n"
    data_a, data_b = _feed._data(frames_a), _feed._data(frames_b)
    assert {e["run_id"] for e in data_a} == {a.run_live, a.run_app}
    assert {e["run_id"] for e in data_b} == {b.run_live, b.run_app}
    _assert_isolated(a, b, data_a, data_b, own=["agent", "run_live", "model"])


@_DB_GATE
def test_active(world):
    from gateway.routes.observability import active

    a, b = world
    out_a = _call(a, lambda v: active(viewer=v))
    out_b = _call(b, lambda v: active(viewer=v))

    assert {r["run_id"] for r in out_a["runs"]} == {a.run_live, a.run_app}
    assert {r["run_id"] for r in out_b["runs"]} == {b.run_live, b.run_app}
    _assert_isolated(a, b, out_a, out_b, own=["agent", "run_live", "app_slug"])


@_DB_GATE
def test_roster(world):
    """The presence set, the avatars, the app names and the alias overlay."""
    from gateway.routes.observability import roster

    a, b = world
    out_a = _call(a, lambda v: roster(viewer=v))
    out_b = _call(b, lambda v: roster(viewer=v))

    for o, out in ((a, out_a), (b, out_b)):
        by_name = {e["name"]: e for e in out["agents"]}
        live = by_name[o.agent]
        assert live["status"] == "working" and live["active_runs"] == 1
        assert live["avatar"]["config"] == {"colour": o.colour}
        assert live["display_name"] == o.alias
        assert by_name["orchestrator"]["display_name"] == o.alias
        app = by_name[f"app:{o.app_slug}"]
        assert app["kind"] == "app" and app["display_name"] == o.app_name
    _assert_isolated(a, b, out_a, out_b,
                     own=["agent", "colour", "alias", "app_slug", "app_name"])


@_DB_GATE
@pytest.mark.xfail(
    strict=True,
    reason="task 2956: with no tenant bound on get_session(), the agent that "
           "one org saves falls back to the shared agents.json and the roster "
           "of another org reads it. Remove this mark when 2956 is fixed.",
)
def test_roster_reads_no_dynamic_agent_of_another_org(world):
    """``dynamic_agents``: an agent that org A saves must not reach org B.

    This drives the REAL save path. ``_save_dynamic_agents`` inserts through
    the unbound ``get_session()``. Under the app role and FORCE RLS the
    insert fails, and the ``except`` writes the whole list to the shared
    ``agents.json``. ``_load_dynamic_agents`` reads that file when no tenant
    is bound. The roster of org B then lists the agent of org A.

    It is a strict expected failure until task 2956 is fixed. A fix turns it
    into an XPASS, which fails the run and tells the fixer to drop the mark.
    The tests above pin the other sources. This one does NOT pin isolation.
    """
    from gateway.routes import agent as agent_routes
    from gateway.routes.observability import roster

    a, b = world
    agent_routes._save_dynamic_agents(
        [{"name": a.dyn_agent, "description": "agent saved by org A"}])

    names_b = {e["name"] for e in _call(b, lambda v: roster(viewer=v))["agents"]}

    assert a.dyn_agent not in names_b


@_DB_GATE
def test_cost(world):
    from gateway.routes.observability import cost

    a, b = world
    out_a = _call(a, lambda v: cost(days=7, viewer=v))
    out_b = _call(b, lambda v: cost(days=7, viewer=v))

    for o, out in ((a, out_a), (b, out_b)):
        assert o.model in out["by_model"], out
        assert o.instance in out["by_instance"], out
    assert a.instance not in json.dumps(out_b) and b.instance not in json.dumps(out_a)
    _assert_isolated(a, b, out_a, out_b, own=["model", "agent"])


@_DB_GATE
def test_runs(world):
    from gateway.routes.observability import runs

    a, b = world

    def _runs(v):
        return runs(agent=None, status=None, since_hours=None, limit=200, viewer=v)

    out_a = _call(a, _runs)
    out_b = _call(b, _runs)

    assert a.run_db in {r["run_id"] for r in out_a["runs"]}
    assert b.run_db in {r["run_id"] for r in out_b["runs"]}
    _assert_isolated(a, b, out_a, out_b, own=["run_db", "agent", "model"])


@_DB_GATE
def test_avatars_the_alias_read(world):
    from gateway.routes.observability import avatars

    a, b = world
    out_a = _call(a, lambda v: avatars(viewer=v))
    out_b = _call(b, lambda v: avatars(viewer=v))

    assert out_a["avatars"][a.agent]["config"] == {"colour": a.colour}
    assert out_b["avatars"][b.agent]["config"] == {"colour": b.colour}
    _assert_isolated(a, b, out_a, out_b, own=["agent", "colour"])


@_DB_GATE
def test_the_agent_alias_blob_reads_only_the_bound_org(world):
    """The alias store of ``/roster`` and ``/agents``, read directly."""
    from gateway.routes.agent import _load_agent_aliases

    a, b = world
    with _bound(a.org):
        aliases_a = _load_agent_aliases()
    with _bound(b.org):
        aliases_b = _load_agent_aliases()

    assert aliases_a == {"orchestrator": a.alias, a.agent: a.alias}
    assert aliases_b == {"orchestrator": b.alias, b.agent: b.alias}


# ---------------------------------------------------------------------------
# The suite is armed: CI names it, so a missing DSN cannot hide a skip.
# ---------------------------------------------------------------------------

class TestThisSuiteIsArmed:
    def test_this_suite_is_named_in_the_ci_skip_guard(self):
        workflow = (_ROOT / ".github/workflows/pr-check.yml").read_text(
            encoding="utf-8")
        assert "tests/unit/test_observability_two_org_r8.py" in workflow
