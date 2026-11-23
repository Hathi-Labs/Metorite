"""SW-27b — ``reserve_number`` on a real database, through branch 0 (R8).

Spec: ``project-docs/specs/agent_coordination.md``, the SW-27 row (SW-27b),
and ``external_agent_access.md`` §9.5. Task 3893.

**R8.** The real edge app takes a real token from the real store. The tool
signs each call, and branch 0 of the REAL gateway app admits it as the
member. The route runs as a NON-privileged role on a promoted catalog of its
own, so FORCE RLS binds every read and write. One organization holds:

* MEMBER, with three connections: ``write`` (``projects:write``, no pin),
  ``pinned`` (``projects:write``, pinned to project ONE) and ``read``
  (``projects:read`` only);
* two team projects that MEMBER can see, ONE and TWO, each with one task;
* one agent run on the task of ONE.

A second organization holds a project with one task.

The gate is ``TENANT_LADDER_DATABASE_URL``. A skip is not a pass. Run::

    eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_mcp_edge_reserve_r8.py -v -rs

**The mutations (R7).**

* Drop the agent narrowing of ``Visibility.project_clause``
  (``_narrow_for_agent`` returns *clause*), and
  ``test_r3_a_pinned_connection_reserves_only_in_its_pins`` turns red: the
  pinned connection reserves in project TWO.
* Take the reserve pair out of ``MCP_WRITE_ROUTES``, and
  ``test_r1_two_calls_get_one_and_two`` turns red: branch 0 refuses the pair.
"""
from __future__ import annotations

import asyncio
import os
import uuid
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

pytest.importorskip("sqlalchemy")

from acb_auth import access as access_module
from acb_auth.access import invalidate as invalidate_access
from acb_common import get_settings
from agent_edge import gateway_client, tools
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from tests.unit import test_mcp_edge_task_writes as tw
from tests.unit import test_mcp_edge_writes as w
from tests.unit._tenant_ladder import apply_ladder

_DB_GATE = w._DB_GATE
pytestmark = _DB_GATE

_APP_ROLE = "acb_app_mcpsw27b"
_APP_PW = "mcpsw27b_scratch_pw"
MEMBER = w.MEMBER

redis_fake = tw.redis_fake


@pytest.fixture(scope="module")
def world():
    """A private promoted catalog, two tenants, the rows of the docstring."""
    if not w._URL:
        pytest.skip("TENANT_LADDER_DATABASE_URL unset")
    admin_url = make_url(w._URL)
    dedicated = f"{admin_url.database}_mcpsw27b"
    w._drop_database(admin_url, dedicated)
    with create_engine(admin_url, isolation_level="AUTOCOMMIT").connect() as c:
        c.execute(text(f'CREATE DATABASE "{dedicated}"'))
    ded_admin_url = admin_url.set(database=dedicated)
    eng = create_engine(ded_admin_url, future=True)
    with eng.begin() as conn:
        apply_ladder(conn)
    for phase in ("01_add_columns.sql", "02_backfill.sql", "03_constraints.sql",
                  "04_policies.sql"):
        with eng.begin() as conn, open(os.path.join(w._GENERATED, phase),
                                       encoding="utf-8") as fh:
            w._exec_sql_file(conn, fh.read())
    with create_engine(ded_admin_url, isolation_level="AUTOCOMMIT").connect() as c:
        c.execute(text(
            f"DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='{_APP_ROLE}')"
            f" THEN CREATE ROLE {_APP_ROLE} LOGIN; END IF; END $$;"))
        c.execute(text(f"ALTER ROLE {_APP_ROLE} WITH LOGIN PASSWORD '{_APP_PW}' "
                       "NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS INHERIT"))
        c.execute(text(f'GRANT CONNECT ON DATABASE "{dedicated}" TO {_APP_ROLE}'))
        c.execute(text(f"GRANT USAGE ON SCHEMA public TO {_APP_ROLE}"))
        c.execute(text("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES "
                       f"IN SCHEMA public TO {_APP_ROLE}"))
        c.execute(text(f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {_APP_ROLE}"))

    org, org_b = str(uuid.uuid4()), str(uuid.uuid4())
    with eng.begin() as c:
        for oid, slug in ((org, "mcpsw27b"), (org_b, "mcpsw27b-other")):
            c.execute(text("INSERT INTO organization (id, slug, display_name) "
                           "VALUES (:id, :s, :s)"), {"id": oid, "s": slug})
    a = w._Seeder(eng, org)
    a.number = 300_000
    a.member(MEMBER, ["feature:projects"])
    p = {}
    t = {}
    for key in ("one", "two"):
        p[key] = a.project(key.title(), in_space=True)
        t[key] = a.task(p[key], a.status(p[key], "To do", "todo", 0), key)
    run = str(a.one(
        "INSERT INTO pm_agent_runs (organization_id, task_id, member_email, client_via,"
        " status, started_by) VALUES (CAST(:o AS uuid), CAST(:t AS uuid), :m, 'mcp:test',"
        " 'in_progress', :m) RETURNING id", o=org, t=t["one"], m=MEMBER))

    b = w._Seeder(eng, org_b)
    b.number = 400_000
    p["theirs"] = b.project("Theirs", in_space=True)
    t["theirs"] = b.task(p["theirs"], b.status(p["theirs"], "To do", "todo", 0), "theirs")

    world_ = SimpleNamespace(admin=eng, org=org, seeder=a, c={}, tok={}, p=p, t=t, run=run)
    world_.c["write"], world_.tok["write"] = a.connection(scopes=("projects:write",))
    world_.c["pinned"], world_.tok["pinned"] = a.connection(
        scopes=("projects:write",), pins=(p["one"],))
    world_.c["read"], world_.tok["read"] = a.connection(scopes=("projects:read",))
    world_.app_dsn = admin_url.set(
        database=dedicated, username=_APP_ROLE, password=_APP_PW,
    ).render_as_string(hide_password=False)
    yield world_

    eng.dispose()
    w._drop_database(admin_url, dedicated)


@pytest.fixture
def live_app(monkeypatch, world, redis_fake):
    """The real gateway app at ``agent_edge_gateway_url`` (EA3-20)."""
    from gateway.main import app as gateway_app

    settings = get_settings()
    for name, value in (
            ("agent_access_enabled", True), ("agent_access_writes_enabled", True),
            ("agent_access_orgs", "*"), ("agent_edge_signing_key", tw.KEY),
            ("agent_edge_signing_key_previous", ""), ("agent_issuer", w.ISSUER),
            ("agent_resource", w.RESOURCE),
            ("agent_edge_gateway_url", "http://gateway.test")):
        monkeypatch.setattr(settings, name, value)
    monkeypatch.setenv("GATEWAY_INTERNAL_TOKEN", w.INTERNAL)
    monkeypatch.setenv("IDENTITY_CUTOVER", "1")
    monkeypatch.setenv("ALLOWED_EMAIL_DOMAIN", "mcpwrites.test")
    monkeypatch.delenv("GATEWAY_TRUST_UNVERIFIED_SSO_HEADERS", raising=False)
    monkeypatch.setattr(access_module, "_tables_missing", False)
    invalidate_access()
    world.redis = redis_fake
    with gateway_client.use_transport(httpx.ASGITransport(app=gateway_app)):
        try:
            yield world
        finally:
            invalidate_access()


@pytest.fixture
async def live(live_app):
    """:func:`live_app`, after one warm-up GET (see ``test_mcp_edge_task_writes``)."""
    if not getattr(live_app, "warmed", False):
        r = await tw._send(live_app, "GET", "/projects/tree")
        assert r.status_code == 200, r.text
        live_app.warmed = True
    return live_app


async def _reserve(world, conn: str, project: str, task: str, sequence: str,
                   **extra: str) -> dict[str, Any]:
    return await tw._mcp(world, conn, "reserve_number", {
        "project_id": world.p[project], "sequence": sequence,
        "task_id": world.t[task], **extra})


def _refused(result: dict[str, Any], text_: str) -> bool:
    return (result["isError"] is True
            and result["content"] == [{"type": "text", "text": text_}])


def _rows(world, sequence: str) -> list[Any]:
    with world.admin.begin() as c:
        return c.execute(text(
            "SELECT r.value, r.task_id, r.run_id, r.organization_id FROM"
            " pm_sequence_reservations r JOIN pm_project_sequences s ON s.id = r.sequence_id"
            " WHERE s.name = :n ORDER BY r.value"), {"n": sequence}).all()


class TestReserveNumberOnARealDatabase:
    """SW-27b through the edge, branch 0 and FORCE RLS."""

    async def test_r1_two_calls_get_one_and_two(self, live):
        first = await _reserve(live, "write", "one", "one", "r1")
        assert first["isError"] is False, first
        body = first["structuredContent"]
        assert set(body) == set(tools.RESERVE_KEYS)
        assert body["value"] == 1 and body["sequence"] == "r1"
        assert body["project_id"] == live.p["one"] and body["task_id"] == live.t["one"]
        second = await _reserve(live, "write", "one", "one", "r1")
        assert second["structuredContent"]["value"] == 2
        rows = _rows(live, "r1")
        assert [r.value for r in rows] == [1, 2]
        assert {str(r.organization_id) for r in rows} == {live.org}

    async def test_r2_two_agents_at_the_same_moment_get_two_numbers(self, live):
        """The rung demo: two calls at once never share a number."""
        got = await asyncio.gather(*(
            _reserve(live, conn, "one", "one", "r2") for conn in ("write", "pinned")))
        assert all(g["isError"] is False for g in got), got
        assert sorted(g["structuredContent"]["value"] for g in got) == [1, 2]

    async def test_r3_a_pinned_connection_reserves_only_in_its_pins(self, live):
        """The pin check. The connection pinned to ONE gets the not-found text
        for TWO, and no row is written. The unpinned one reserves in TWO."""
        refused = await _reserve(live, "pinned", "two", "two", "r3")
        assert _refused(refused, tools.TEXT_RESERVE_NOT_FOUND), refused
        assert _rows(live, "r3") == []
        ok = await _reserve(live, "write", "two", "two", "r3")
        assert ok["isError"] is False, ok
        assert ok["structuredContent"]["value"] == 1
        own = await _reserve(live, "pinned", "one", "one", "r3")
        assert own["structuredContent"]["value"] == 1

    async def test_r4_a_read_only_connection_cannot_reserve(self, live):
        path = f"/projects/nodes/{live.p['one']}/sequences/r4/reserve"
        r = await tw._send(live, "POST", path, body={"task_id": live.t["one"]},
                           signed="read")
        assert r.status_code in (401, 403), (r.status_code, r.text)
        # The edge refuses the tool call itself: the token lacks the scope.
        with pytest.raises(AssertionError, match="Insufficient scope"):
            await _reserve(live, "read", "one", "one", "r4")
        assert _rows(live, "r4") == []

    async def test_r5_another_organizations_project_is_not_found(self, live):
        result = await _reserve(live, "write", "theirs", "theirs", "r5")
        assert _refused(result, tools.TEXT_RESERVE_NOT_FOUND), result
        mixed = await _reserve(live, "write", "one", "theirs", "r5")
        assert _refused(mixed, tools.TEXT_RESERVE_NOT_FOUND), mixed
        assert _rows(live, "r5") == []

    async def test_r6_the_run_is_stored_and_must_be_a_run_of_the_task(self, live):
        ok = await _reserve(live, "write", "one", "one", "r6", run_id=live.run)
        assert ok["structuredContent"]["run_id"] == live.run
        wrong = await _reserve(live, "write", "two", "two", "r6", run_id=live.run)
        assert _refused(wrong, tools.TEXT_RESERVE_RUN), wrong
        assert [str(r.run_id) for r in _rows(live, "r6")] == [live.run]

    async def test_r7_a_task_of_another_project_is_refused(self, live):
        result = await _reserve(live, "write", "one", "two", "r7")
        assert _refused(result, tools.TEXT_RESERVE_NOT_IN_PROJECT), result
        assert _rows(live, "r7") == []
