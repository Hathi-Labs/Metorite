"""WS-MCP SW-59a — a run declares its role (R8).

Spec: ``project-docs/specs/agent_coordination.md`` SW-59 (amended
2026-11-28), §3.10.10 and §3.12.10. Metorite task 4135.

``claim_task`` and ``start_agent_run`` take an optional ``role``: one of
``orchestrator``, ``builder``, ``verifier``, ``reviewer`` and ``merger``. Any
other value is 422 at the schema of the body. A person's body that names it is
422. The nullable column ``pm_agent_runs.role`` holds it, and a named CHECK
holds the five values. A later claim replaces it, and a call with no role
keeps it. The runs wire and the session node of the map give it back.

The role is display data, and it grants nothing (R7). The structural fence is
``test_agent_label_fence.py`` (``TestTheRoleGrantsNothing``).

This file runs on a REAL Postgres, through the real ``/projects`` router,
under the non-privileged role of the phase-4 rehearsal (R8). Run it on a
private database::

    eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_agent_run_role_r8.py -v -rs

Mutations (R7). ``G`` is ``gateway/routes/projects/agent_runs.py`` and ``S``
is ``agent_summary.py`` beside it.

* R1. ``G`` ``_place_binds``: bind NULL for ``:role``.
  ``test_a_claim_with_a_role_stores_it_and_the_wire_gives_it``.
* R2. ``G`` ``_SET_PLACE_SQL``: drop the ``role`` arm.
  ``test_a_later_claim_replaces_it_and_a_claim_with_none_keeps_it``.
* R3. ``G`` ``StartIn.role``: take any string.
  ``test_a_bad_role_is_422_at_the_schema``.
* R4. ``G`` ``agent_place_of``: skip the person check for a role.
  ``test_a_person_body_with_a_role_is_refused``.
* R5. ``S`` ``_MAP_SESSIONS_SQL``: take the role of the OLDEST run.
  ``test_the_map_node_gives_the_role_of_its_newest_run``.
* R6. ``G`` ``_CLEAR_LABEL_SQL``: keep ``role``.
  ``test_a_reopen_clears_the_role_with_the_label``.
"""

from __future__ import annotations

import secrets
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("gateway.routes.projects", reason="gateway not installed")

from acb_common import get_settings
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from tests.unit.test_agent_run_exclusive import (
    _agent,
    _connection,
    _in_session,
    _member_row,
    _person,
    _seed_task,
)
from tests.unit.test_agent_runs_r8 import CLIENT_ID, _rows, _session
from tests.unit.test_agent_runs_r8 import VIA as R8_VIA
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

ALICE = "sw59-alice@role.test"
BOB_B = "sw59-bob@role-b.test"
ROLES = ("orchestrator", "builder", "verifier", "reviewer", "merger")

_MIGRATIONS = Path(__file__).resolve().parents[2] / "infra" / "postgres"
_MARK = "ALTER TABLE pm_agent_runs ADD COLUMN IF NOT EXISTS role TEXT NULL"

MAP = "/projects/agents/overview?shape=map"


def _migration() -> Path:
    """The migration of SW-59a, found by CONTENT, never by number (R1)."""
    found = [p for p in sorted(_MIGRATIONS.glob("[0-9]*_*.sql"))
             if _MARK in p.read_text(encoding="utf-8")]
    assert len(found) == 1, found
    return found[0]


@pytest.fixture(scope="module")
def world(promoted):  # noqa: F811
    org, org_b = promoted.org_a, promoted.org_b
    ns = SimpleNamespace(p=promoted, org=org, org_b=org_b)
    with promoted.admin_engine.begin() as c:
        _member_row(c, org, ALICE, "Alice Role")
        _member_row(c, org_b, BOB_B, "Bob Role")
    ns.conn_alice = _connection(promoted, org, ALICE, CLIENT_ID, "Claude Code")
    ns.alice = _agent(ALICE, org, ns.conn_alice, R8_VIA)
    ns.alice_person = _person(ALICE, org)
    ns.bob_person = _person(BOB_B, org_b)
    return ns


@pytest.fixture
def w(world, monkeypatch):
    monkeypatch.setattr(get_settings(), "agent_runs_enabled", True)
    with world.p.admin_engine.begin() as c:
        c.execute(text("DELETE FROM pm_agent_runs"))
    return world


_numbers = iter(range(140_000, 160_000))


def _fresh(w: Any) -> str:
    return _seed_task(w.p, w.org, ALICE, n=next(_numbers))


def _key() -> str:
    """A session key in the shape of SW-7: 22 base64url characters."""
    return secrets.token_urlsafe(16)


def _role(w: Any, run_id: str) -> Any:
    [row] = _rows(w.p, "SELECT role FROM pm_agent_runs WHERE id = CAST(:i AS uuid)",
                  i=run_id)
    return row.role


@_DB_GATE
class TestTheRoleOnARealDatabase:

    def test_the_migration_applies_twice_with_no_change(self, w):
        """Found by content. A second and a third run of the whole file
        change nothing: the column, its one CHECK and its comment stay."""
        sql = _migration().read_text(encoding="utf-8")

        def shape() -> tuple[Any, ...]:
            [col] = _rows(w.p, "SELECT data_type, is_nullable, column_default"
                               " FROM information_schema.columns"
                               " WHERE table_name = 'pm_agent_runs' AND column_name = 'role'")
            checks = _rows(w.p, "SELECT conname, convalidated FROM pg_constraint"
                                " WHERE conrelid = 'pm_agent_runs'::regclass"
                                " AND pg_get_constraintdef(oid) LIKE '%role%'")
            [note] = _rows(w.p, "SELECT col_description('pm_agent_runs'::regclass,"
                                " (SELECT attnum FROM pg_attribute"
                                "   WHERE attrelid = 'pm_agent_runs'::regclass"
                                "     AND attname = 'role')) AS d")
            return ((col.data_type, col.is_nullable, col.column_default),
                    sorted((c.conname, c.convalidated) for c in checks), note.d)

        before = shape()
        assert before[0] == ("text", "YES", None)
        assert before[1] == [("pm_agent_runs_role_check", True)]
        assert before[2].startswith("WS-MCP SW-59a.")
        for _ in range(2):
            with w.p.admin_engine.begin() as c:
                c.exec_driver_sql(sql)
        assert shape() == before

    async def test_the_check_holds_the_five_roles(self, w):
        task = _fresh(w)
        async with _session(w.p) as call:
            claimed = await call(w.alice, "POST", f"/projects/tasks/{task}/claim", None)
        run_id = claimed.json()["agent_run_id"]
        for bad in ("implementer", "Verifier", ""):
            with pytest.raises(DBAPIError), w.p.admin_engine.begin() as c:
                c.execute(text("UPDATE pm_agent_runs SET role = :v WHERE id = CAST(:i AS uuid)"),
                          {"v": bad, "i": run_id})
        for good in ROLES:
            with w.p.admin_engine.begin() as c:
                c.execute(text("UPDATE pm_agent_runs SET role = :v WHERE id = CAST(:i AS uuid)"),
                          {"v": good, "i": run_id})
        assert _role(w, run_id) == "merger"

    async def test_a_claim_with_a_role_stores_it_and_the_wire_gives_it(self, w):
        """The first "Done when" of SW-59: a claim with ``role: verifier``."""
        task = _fresh(w)
        session = _in_session(w.alice, _key())
        async with _session(w.p) as call:
            claimed = await call(session, "POST", f"/projects/tasks/{task}/claim",
                                 {"role": "verifier"})
            assert claimed.status_code == 200, claimed.text
            run_id = claimed.json()["agent_run_id"]
            one = await call(w.alice_person, "GET", f"/projects/agent-runs/{run_id}", None)
            listed = await call(w.alice_person, "GET", "/projects/agent-runs", None)
        assert _role(w, run_id) == "verifier"
        assert one.status_code == 200, one.text
        assert one.json()["run"]["role"] == "verifier"
        [row] = [r for r in listed.json()["rows"] if r["id"] == run_id]
        assert row["role"] == "verifier"

    async def test_a_later_claim_replaces_it_and_a_claim_with_none_keeps_it(self, w):
        task = _fresh(w)
        session = _in_session(w.alice, _key())
        async with _session(w.p) as call:
            first = await call(session, "POST", f"/projects/tasks/{task}/claim",
                               {"role": "verifier"})
            run_id = first.json()["agent_run_id"]
            later = await call(session, "POST", f"/projects/tasks/{task}/claim",
                               {"role": "builder"})
            assert later.status_code == 200, later.text
            assert later.json()["agent_run_id"] == run_id
            assert _role(w, run_id) == "builder"
            again = await call(session, "POST", f"/projects/tasks/{task}/claim", None)
            assert again.json()["agent_run_id"] == run_id
            assert _role(w, run_id) == "builder"
            # A start with no role keeps it, and one with a role replaces it.
            kept = await call(session, "POST", f"/projects/tasks/{task}/agent-runs",
                              {"idempotency_key": _key(), "model": "Haiku 5"})
            assert kept.status_code == 200, kept.text
            assert kept.json()["run"]["role"] == "builder"
            swapped = await call(session, "POST", f"/projects/tasks/{task}/agent-runs",
                                 {"idempotency_key": _key(), "role": "reviewer"})
            assert swapped.status_code == 200, swapped.text
            assert swapped.json()["run"]["role"] == "reviewer"
        assert _role(w, run_id) == "reviewer"

    async def test_a_new_run_of_a_start_stores_the_role(self, w):
        task = _fresh(w)
        async with _session(w.p) as call:
            started = await call(_in_session(w.alice, _key()), "POST",
                                 f"/projects/tasks/{task}/agent-runs",
                                 {"idempotency_key": _key(), "role": "orchestrator"})
        assert started.status_code == 201, started.text
        run = started.json()["run"]
        assert run["role"] == "orchestrator" and _role(w, run["id"]) == "orchestrator"

    async def test_a_run_with_no_role_reads_null(self, w):
        """The second "Done when": a claim with no role works as today."""
        task = _fresh(w)
        async with _session(w.p) as call:
            claimed = await call(_in_session(w.alice, _key()), "POST",
                                 f"/projects/tasks/{task}/claim", {"agent_label": "lane-1"})
            assert claimed.status_code == 200, claimed.text
            run_id = claimed.json()["agent_run_id"]
            one = await call(w.alice_person, "GET", f"/projects/agent-runs/{run_id}", None)
        assert _role(w, run_id) is None
        assert one.json()["run"]["role"] is None

    async def test_a_bad_role_is_422_at_the_schema(self, w):
        task = _fresh(w)
        session = _in_session(w.alice, _key())
        async with _session(w.p) as call:
            answers = [
                await call(session, "POST", f"/projects/tasks/{task}/claim", {"role": bad})
                for bad in ("implementer", "Verifier", "", 1, "admin")
            ]
            answers.append(await call(session, "POST", f"/projects/tasks/{task}/agent-runs",
                                      {"idempotency_key": _key(), "role": "boss"}))
        assert [a.status_code for a in answers] == [422] * 6, [a.text for a in answers]
        assert _rows(w.p, "SELECT id FROM pm_agent_runs WHERE task_id = CAST(:t AS uuid)",
                     t=task) == []

    async def test_a_person_body_with_a_role_is_refused(self, w):
        task = _fresh(w)
        async with _session(w.p) as call:
            claim = await call(w.alice_person, "POST", f"/projects/tasks/{task}/claim",
                               {"role": "verifier"})
            start = await call(w.alice_person, "POST", f"/projects/tasks/{task}/agent-runs", {
                "connection_id": w.conn_alice, "body": "Please take it", "role": "builder"})
        assert claim.status_code == 422, claim.text
        assert start.status_code == 422, start.text
        assert _rows(w.p, "SELECT id FROM pm_agent_runs WHERE task_id = CAST(:t AS uuid)",
                     t=task) == []

    async def test_a_reopen_clears_the_role_with_the_label(self, w):
        """A finished run keeps the role of the session that ran it. A
        member's prompt hands the run to the next session, and the role goes
        with the label, as the place does (SW-104)."""
        task = _fresh(w)
        first = _in_session(w.alice, _key())
        async with _session(w.p) as call:
            claimed = await call(first, "POST", f"/projects/tasks/{task}/claim",
                                 {"role": "verifier", "agent_label": "lane-3"})
            run_id = claimed.json()["agent_run_id"]
            fin = await call(first, "POST", f"/projects/agent-runs/{run_id}/finish", {})
            assert fin.status_code == 200, fin.text
            assert _role(w, run_id) == "verifier"
            prompt = await call(w.alice_person, "POST",
                                f"/projects/agent-runs/{run_id}/activities",
                                {"type": "prompt", "body": "One more thing",
                                 "idempotency_key": _key()})
            assert prompt.status_code == 201, prompt.text
        assert _role(w, run_id) is None

    async def test_the_role_never_opens_the_run_of_another_session(self, w):
        """Session B claims as an orchestrator. The gate still finds the run of
        session A as a holder: 409, and the run keeps its role."""
        task = _fresh(w)
        first, second = _in_session(w.alice, _key()), _in_session(w.alice, _key())
        async with _session(w.p) as call:
            a = await call(first, "POST", f"/projects/tasks/{task}/claim", {"role": "builder"})
            b = await call(second, "POST", f"/projects/tasks/{task}/claim",
                           {"role": "orchestrator"})
        assert a.status_code == 200, a.text
        assert b.status_code == 409 and b.json()["detail"]["error"] == "task_held", b.text
        assert _role(w, a.json()["agent_run_id"]) == "builder"

    async def test_org_b_cannot_see_the_role_of_org_a(self, w, app_engine):  # noqa: F811
        """RLS keeps the run, and so its role, in org A: the wire of an org B
        member, the map of org B and a bound read of org B find nothing."""
        task = _fresh(w)
        async with _session(w.p) as call:
            claimed = await call(_in_session(w.alice, _key()), "POST",
                                 f"/projects/tasks/{task}/claim", {"role": "verifier"})
            run_id = claimed.json()["agent_run_id"]
            one = await call(w.bob_person, "GET", f"/projects/agent-runs/{run_id}", None)
            listed = await call(w.bob_person, "GET", "/projects/agent-runs", None)
        assert one.status_code == 404, one.text
        assert run_id not in {r["id"] for r in listed.json().get("rows", [])}
        with app_engine.connect() as c, c.begin():
            c.execute(text("SELECT set_config('app.tenant_id', :o, true)"), {"o": w.org_b})
            seen = c.execute(text("SELECT role FROM pm_agent_runs"
                                  " WHERE id = CAST(:i AS uuid)"), {"i": run_id}).fetchall()
        assert seen == []
        with app_engine.connect() as c, c.begin():
            c.execute(text("SELECT set_config('app.tenant_id', :o, true)"), {"o": w.org})
            [mine] = c.execute(text("SELECT role FROM pm_agent_runs"
                                    " WHERE id = CAST(:i AS uuid)"), {"i": run_id}).fetchall()
        assert mine.role == "verifier"


@_DB_GATE
class TestTheRoleOnTheMap:

    @pytest.fixture
    def m(self, w, monkeypatch):
        monkeypatch.setattr(get_settings(), "agents_view_orgs", "*")
        monkeypatch.setattr(get_settings(), "agent_map_orgs", "*")
        return w

    async def _map(self, m: Any, user: Any, query: str = MAP) -> dict[str, Any]:
        async with _session(m.p) as call:
            got = await call(user, "GET", query, None)
        assert got.status_code == 200, got.text
        return got.json()

    async def test_the_map_node_gives_the_role_of_its_newest_run(self, m):
        """Two sessions with two roles show two roles. A session with two runs
        shows the role of its newest run, the run that gives its machine."""
        one, two, three = _fresh(m), _fresh(m), _fresh(m)
        lane, boss = _in_session(m.alice, _key()), _in_session(m.alice, _key())
        async with _session(m.p) as call:
            for session, task, body in (
                    (lane, one, {"agent_label": "lane-1", "role": "builder"}),
                    (boss, two, {"agent_label": "boss-1", "role": "orchestrator"})):
                done = await call(session, "POST", f"/projects/tasks/{task}/claim", body)
                assert done.status_code == 200, done.text
            newer = await call(lane, "POST", f"/projects/tasks/{three}/claim",
                               {"agent_label": "lane-1", "role": "verifier"})
            assert newer.status_code == 200, newer.text
        with m.p.admin_engine.begin() as c:
            c.execute(text("UPDATE pm_agent_runs SET last_activity_at = now() - interval"
                           " '1 minute' WHERE id <> CAST(:i AS uuid)"),
                      {"i": newer.json()["agent_run_id"]})
        body = await self._map(m, m.alice_person)
        roles = {s["name"]: s["role"] for s in body["sessions"]}
        assert roles == {"lane-1": "verifier", "boss-1": "orchestrator"}, body["sessions"]

    async def test_the_summary_gives_the_same_role(self, m, monkeypatch):
        """AMR-4: the summary reads the newest run by the same rule."""
        monkeypatch.setattr(get_settings(), "agent_lenses_orgs", "*")
        task = _fresh(m)
        async with _session(m.p) as call:
            done = await call(_in_session(m.alice, _key()), "POST",
                              f"/projects/tasks/{task}/claim",
                              {"agent_label": "merge-bot", "role": "merger"})
            assert done.status_code == 200, done.text
        plain = await self._map(m, m.alice_person)
        summary = await self._map(m, m.alice_person, MAP + "&detail=summary")
        for body in (plain, summary):
            [node] = [s for s in body["sessions"] if s["name"] == "merge-bot"]
            assert node["role"] == "merger", node

    async def test_a_session_with_no_role_reads_null_and_org_b_sees_no_node(self, m):
        task = _fresh(m)
        async with _session(m.p) as call:
            done = await call(_in_session(m.alice, _key()), "POST",
                              f"/projects/tasks/{task}/claim", {"agent_label": "plain-1"})
            assert done.status_code == 200, done.text
        body = await self._map(m, m.alice_person)
        [node] = [s for s in body["sessions"] if s["name"] == "plain-1"]
        assert node["role"] is None
        other = await self._map(m, m.bob_person)
        assert other["sessions"] == [], other
