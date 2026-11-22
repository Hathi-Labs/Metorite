"""WS-MCP SW-107 — who dispatched a run (R8).

Spec: ``project-docs/specs/agent_coordination.md`` §3.12.3 ("Who dispatched
whom"), §3.12.4 rule 2, the slice SW-107 and Q20.

``claim_task`` and ``start_agent_run`` take an optional ``dispatched_by``: the
run id of the session that handed the task out. The nullable column
``pm_agent_runs.dispatched_by_run_id`` holds it, with a foreign key to
``pm_agent_runs (id)`` and ``ON DELETE SET NULL``. The gateway reads the named
run in the caller's tenant session, so RLS hides a run of another
organization. It drops the value when that read finds no run, when the run is
the run itself, and when the value makes a loop within 5 steps. A dropped
value never fails the call: the result carries a ``warning``.

This file runs on a REAL Postgres, through the real ``/projects`` router,
under the non-privileged role of the phase-4 rehearsal (R8). The structural
fence that the value grants nothing is ``test_agent_label_fence.py``.

Run it on a private database::

    eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_agent_run_dispatch_r8.py -v -rs

Mutations (R7). ``G`` is ``gateway/routes/projects/agent_runs.py``.

* D1. ``G`` ``set_run_dispatcher``: return at once.
  ``test_a_run_of_the_same_organization_links``.
* D2. ``G`` ``_DISPATCH_CHAIN_SQL``: read as the admin, with no tenant.
  ``test_a_run_of_another_organization_drops_with_a_warning``.
* D3. ``G`` ``set_run_dispatcher``: skip the self check.
  ``test_the_run_itself_drops_with_a_warning``.
* D4. ``G`` ``DISPATCH_LOOP_STEPS``: 1.
  ``test_a_loop_within_5_steps_drops_with_a_warning``.
* D5. ``G`` ``dispatched_by_of``: skip the person check.
  ``test_a_person_body_with_dispatched_by_is_refused``.
* D6. ``G`` ``_SET_DISPATCHER_SQL``: COALESCE the old value first.
  ``test_a_later_value_replaces_it_and_none_keeps_it``.
* D7. ``G`` ``set_run_dispatcher``: drop the savepoint.
  ``test_a_dispatcher_gone_after_the_chain_read_drops_and_the_claim_passes``.
"""

from __future__ import annotations

import secrets
import uuid
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("gateway.routes.projects", reason="gateway not installed")

from acb_common import get_settings
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from tests.unit.test_agent_run_exclusive import (
    ORIGIN,
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

ALICE = "sw107-alice@dispatch.test"   # the orchestrator and the lanes, org A
LANE = "sw107-lane@dispatch.test"     # a lane that sees only its own task, org A
OTHER = "sw107-bob@dispatch.test"     # a member of org B

_UNKNOWN = "Metorite kept no dispatcher: your organization has no run with that id."
_SELF = "Metorite kept no dispatcher: a run cannot dispatch itself."
_LOOP = "Metorite kept no dispatcher: that run would make a loop of dispatchers."
_NO_RUN = "Metorite kept no dispatcher, because the claim opened no run."

_DISPATCHER_SQL = ("SELECT dispatched_by_run_id FROM pm_agent_runs"
                   " WHERE id = CAST(:i AS uuid)")


def _narrow_agent(email: str, org: str, connection: str) -> Any:
    """An agent with no org-wide read: it sees only a task granted to it."""
    from acb_auth import AgentScope, UserContext, UserRole, build_access

    return UserContext(
        email=email, role=UserRole.EMPLOYEE,
        access=build_access(["feature:projects"]), organization_id=org,
        agent=AgentScope(
            connection_id=connection, scopes=("projects:read", "projects:write"),
            include_personal=False, client_origin=ORIGIN, actor_via=R8_VIA),
    )


@pytest.fixture(scope="module")
def world(promoted):  # noqa: F811
    org, org_b = promoted.org_a, promoted.org_b
    ns = SimpleNamespace(p=promoted, org=org, org_b=org_b)
    with promoted.admin_engine.begin() as c:
        _member_row(c, org, ALICE, "Alice Dispatch")
        _member_row(c, org, LANE, "Lane Dispatch")
        _member_row(c, org_b, OTHER, "Bob Other")
    ns.conn_alice = _connection(promoted, org, ALICE, CLIENT_ID, "Claude Code")
    ns.alice = _agent(ALICE, org, ns.conn_alice, R8_VIA)
    ns.alice_person = _person(ALICE, org)
    ns.conn_lane = _connection(promoted, org, LANE, CLIENT_ID, "Claude Code")
    ns.lane = _narrow_agent(LANE, org, ns.conn_lane)
    ns.lane_person = _person(LANE, org, sees_all=False)
    ns.conn_other = _connection(promoted, org_b, OTHER, CLIENT_ID, "Claude Code")
    ns.other = _agent(OTHER, org_b, ns.conn_other, R8_VIA)
    return ns


@pytest.fixture
def w(world, monkeypatch):
    monkeypatch.setattr(get_settings(), "agent_runs_enabled", True)
    with world.p.admin_engine.begin() as c:
        c.execute(text("DELETE FROM pm_agent_runs"))
    return world


_numbers = iter(range(120_000, 140_000))


def _fresh(w: Any, org: str | None = None, owner: str = ALICE,
           grant: str | None = None) -> str:
    return _seed_task(w.p, org or w.org, owner, n=next(_numbers), grant=grant)


def _key() -> str:
    """A session key in the shape of SW-7: 22 base64url characters."""
    return secrets.token_urlsafe(16)


def _dispatcher(w: Any, run_id: str) -> str | None:
    [row] = _rows(w.p, _DISPATCHER_SQL, i=run_id)
    return None if row.dispatched_by_run_id is None else str(row.dispatched_by_run_id)


async def _claim(call: Any, user: Any, task: str, body: dict | None = None) -> Any:
    answer = await call(user, "POST", f"/projects/tasks/{task}/claim", body)
    assert answer.status_code == 200, answer.text
    return answer.json()


async def _run_of(call: Any, w: Any, dispatched_by: str | None = None) -> str:
    """A new run of Alice on a new task, in a session of its own."""
    body = {"dispatched_by": dispatched_by} if dispatched_by else None
    out = await _claim(call, _in_session(w.alice, _key()), _fresh(w), body)
    assert out["agent_run_id"], out
    return str(out["agent_run_id"])


@_DB_GATE
class TestTheDispatcherOnARealDatabase:

    async def test_the_column_and_its_foreign_key(self, w):
        cols = {r.column_name: (r.data_type, r.is_nullable, r.column_default) for r in _rows(
            w.p, "SELECT column_name, data_type, is_nullable, column_default"
                 " FROM information_schema.columns WHERE table_name = 'pm_agent_runs'"
                 " AND column_name = 'dispatched_by_run_id'")}
        assert cols == {"dispatched_by_run_id": ("uuid", "YES", None)}
        [fk] = _rows(w.p, "SELECT confrelid::regclass::text AS target, confdeltype,"
                          " convalidated FROM pg_constraint"
                          " WHERE conname = 'pm_agent_runs_dispatched_by_fk'")
        assert (fk.target, fk.confdeltype, fk.convalidated) == ("pm_agent_runs", "n", True)

    async def test_a_run_of_the_same_organization_links(self, w):
        """The first "Done when" of SW-107. ``get_agent_run`` and the runs
        list give the id back, as an id."""
        async with _session(w.p) as call:
            boss = await _run_of(call, w)
            out = await _claim(call, _in_session(w.alice, _key()), _fresh(w),
                               {"dispatched_by": boss.upper()})
            lane = out["agent_run_id"]
            one = await call(w.alice_person, "GET", f"/projects/agent-runs/{lane}", None)
            listed = await call(w.alice_person, "GET", "/projects/agent-runs", None)
        assert "warning" not in out and "dispatched_by_dropped" not in out, out
        assert _dispatcher(w, lane) == boss
        assert one.status_code == 200, one.text
        assert one.json()["run"]["dispatched_by_run_id"] == boss
        rows = {r["id"]: r["dispatched_by_run_id"] for r in listed.json()["rows"]}
        assert rows == {boss: None, lane: boss}

    async def test_a_start_stores_it_on_a_new_run(self, w):
        async with _session(w.p) as call:
            boss = await _run_of(call, w)
            started = await call(_in_session(w.alice, _key()), "POST",
                                 f"/projects/tasks/{_fresh(w)}/agent-runs",
                                 {"idempotency_key": _key(), "dispatched_by": boss})
        assert started.status_code == 201, started.text
        body = started.json()
        assert "warning" not in body, body
        assert body["run"]["dispatched_by_run_id"] == boss
        assert _dispatcher(w, body["run"]["id"]) == boss

    async def test_a_run_of_another_organization_drops_with_a_warning(self, w):
        """RLS hides the run of org B, so it reads as unknown. The claim and
        the start still work."""
        async with _session(w.p) as call:
            theirs = (await _claim(call, _in_session(w.other, _key()),
                                   _fresh(w, w.org_b, OTHER)))["agent_run_id"]
            assert theirs
            out = await _claim(call, _in_session(w.alice, _key()), _fresh(w),
                               {"dispatched_by": theirs})
            started = await call(_in_session(w.alice, _key()), "POST",
                                 f"/projects/tasks/{_fresh(w)}/agent-runs",
                                 {"idempotency_key": _key(), "dispatched_by": theirs})
        assert out["agent_run_id"] and out["moved"] is True, out
        assert (out["warning"], out["dispatched_by_dropped"]) == (_UNKNOWN, "unknown")
        assert _dispatcher(w, out["agent_run_id"]) is None
        assert started.status_code == 201, started.text
        assert started.json()["warning"] == _UNKNOWN
        assert started.json()["run"]["dispatched_by_run_id"] is None

    async def test_an_id_with_no_run_drops_with_the_same_warning(self, w):
        async with _session(w.p) as call:
            out = await _claim(call, _in_session(w.alice, _key()), _fresh(w),
                               {"dispatched_by": str(uuid.uuid4())})
        assert (out["warning"], out["dispatched_by_dropped"]) == (_UNKNOWN, "unknown")
        assert _dispatcher(w, out["agent_run_id"]) is None

    async def test_the_run_itself_drops_with_a_warning(self, w):
        task = _fresh(w)
        session = _in_session(w.alice, _key())
        async with _session(w.p) as call:
            first = await _claim(call, session, task)
            run_id = first["agent_run_id"]
            again = await _claim(call, session, task, {"dispatched_by": run_id})
            started = await call(session, "POST", f"/projects/tasks/{task}/agent-runs",
                                 {"idempotency_key": _key(), "dispatched_by": run_id})
        assert again["agent_run_id"] == run_id
        assert (again["warning"], again["dispatched_by_dropped"]) == (_SELF, "self")
        assert started.status_code == 200, started.text
        assert started.json()["warning"] == _SELF
        assert _dispatcher(w, run_id) is None

    async def test_a_loop_within_5_steps_drops_with_a_warning(self, w):
        """A two-run loop, and a loop of five runs. The old value stays."""
        async with _session(w.p) as call:
            tasks = [_fresh(w) for _ in range(5)]
            sessions = [_in_session(w.alice, _key()) for _ in range(5)]
            runs = [(await _claim(call, sessions[0], tasks[0]))["agent_run_id"]]
            for i in range(1, 5):
                runs.append((await _claim(call, sessions[i], tasks[i],
                                          {"dispatched_by": runs[i - 1]}))["agent_run_id"])
            # Run 0 dispatched by run 1 closes a loop of two.
            two = await _claim(call, sessions[0], tasks[0], {"dispatched_by": runs[1]})
            # Run 0 dispatched by run 4 closes a loop of five: 4, 3, 2, 1, 0.
            five = await _claim(call, sessions[0], tasks[0], {"dispatched_by": runs[4]})
        for out in (two, five):
            assert out["agent_run_id"] == runs[0]
            assert (out["warning"], out["dispatched_by_dropped"]) == (_LOOP, "loop")
        assert _dispatcher(w, runs[0]) is None
        assert [_dispatcher(w, r) for r in runs[1:]] == runs[:4]

    async def test_a_chain_longer_than_5_steps_is_not_walked(self, w):
        """The rule names 5 steps, so the walk stops there. A loop of six
        runs is not seen, and the value links."""
        async with _session(w.p) as call:
            tasks = [_fresh(w) for _ in range(6)]
            sessions = [_in_session(w.alice, _key()) for _ in range(6)]
            runs = [(await _claim(call, sessions[0], tasks[0]))["agent_run_id"]]
            for i in range(1, 6):
                runs.append((await _claim(call, sessions[i], tasks[i],
                                          {"dispatched_by": runs[i - 1]}))["agent_run_id"])
            out = await _claim(call, sessions[0], tasks[0], {"dispatched_by": runs[5]})
        assert "warning" not in out, out
        assert _dispatcher(w, runs[0]) == runs[5]

    async def test_a_dispatcher_that_the_caller_cannot_see_links_and_grants_nothing(self, w):
        """The rule is the organization, not D12. The lane sees only its own
        task, and names the run of a task that it cannot see. The value
        links, and the lane still cannot read that run: the id grants
        nothing."""
        hidden = _fresh(w)            # no grant: the lane cannot see it
        mine = _fresh(w, grant=LANE)  # the lane's own task
        async with _session(w.p) as call:
            boss = await _run_of(call, w)
            out = await _claim(call, _in_session(w.alice, _key()), hidden,
                               {"dispatched_by": boss})
            secret = out["agent_run_id"]
            peek = await call(w.lane_person, "GET", f"/projects/tasks/{hidden}", None)
            assert peek.status_code == 404, peek.text
            lane = await _claim(call, _in_session(w.lane, _key()), mine,
                                {"dispatched_by": secret})
            one = await call(w.lane_person, "GET", f"/projects/agent-runs/{secret}", None)
            listed = await call(w.lane_person, "GET", "/projects/agent-runs", None)
        assert "warning" not in lane, lane
        assert _dispatcher(w, lane["agent_run_id"]) == secret
        assert one.status_code == 404, one.text
        assert [r["id"] for r in listed.json()["rows"]] == [lane["agent_run_id"]]

    async def test_a_person_body_with_dispatched_by_is_refused(self, w):
        task = _fresh(w)
        async with _session(w.p) as call:
            boss = await _run_of(call, w)
            claimed = await call(w.alice_person, "POST", f"/projects/tasks/{task}/claim",
                                 {"dispatched_by": boss})
            started = await call(w.alice_person, "POST",
                                 f"/projects/tasks/{task}/agent-runs", {
                                     "connection_id": w.conn_alice,
                                     "body": "Please take it", "dispatched_by": boss})
        assert [claimed.status_code, started.status_code] == [422, 422], (
            claimed.text, started.text)
        assert _rows(w.p, "SELECT id FROM pm_agent_runs WHERE task_id = CAST(:t AS uuid)",
                     t=task) == []

    async def test_a_value_that_is_not_an_id_is_422_at_the_schema(self, w):
        task = _fresh(w)
        session = _in_session(w.alice, _key())
        async with _session(w.p) as call:
            answers = [await call(session, "POST", f"/projects/tasks/{task}/claim", body)
                       for body in ({"dispatched_by": "run-1"}, {"dispatched_by": ""},
                                    {"dispatched_by": 7})]
            answers.append(await call(session, "POST", f"/projects/tasks/{task}/agent-runs",
                                      {"idempotency_key": _key(), "dispatched_by": "x"}))
        assert [a.status_code for a in answers] == [422] * 4, [a.text for a in answers]
        assert _rows(w.p, "SELECT id FROM pm_agent_runs WHERE task_id = CAST(:t AS uuid)",
                     t=task) == []

    async def test_a_later_value_replaces_it_and_none_keeps_it(self, w):
        task = _fresh(w)
        session = _in_session(w.alice, _key())
        async with _session(w.p) as call:
            one, two = await _run_of(call, w), await _run_of(call, w)
            run_id = (await _claim(call, session, task, {"dispatched_by": one}))["agent_run_id"]
            assert _dispatcher(w, run_id) == one
            await _claim(call, session, task, {"dispatched_by": two})
            assert _dispatcher(w, run_id) == two
            await _claim(call, session, task, {"agent_label": "lane-2"})
            assert _dispatcher(w, run_id) == two
            started = await call(session, "POST", f"/projects/tasks/{task}/agent-runs",
                                 {"idempotency_key": _key()})
            assert started.json()["run"]["dispatched_by_run_id"] == two
            # A dropped value keeps the old one too.
            bad = await _claim(call, session, task, {"dispatched_by": str(uuid.uuid4())})
            assert bad["dispatched_by_dropped"] == "unknown"
        assert _dispatcher(w, run_id) == two

    async def test_a_dispatcher_gone_after_the_chain_read_drops_and_the_claim_passes(
            self, w, monkeypatch):
        """P2. A delete between the chain read and the UPDATE makes the
        foreign key check fail (23503). The chain read is forced to find a run
        that does not exist, which is the same state, so the UPDATE meets the
        failure each time. The savepoint maps it to ``unknown``, and the
        claim, its run and its assignee still commit."""
        from gateway.routes.projects import agent_runs

        ghost = str(uuid.uuid4())
        monkeypatch.setattr(agent_runs, "_DISPATCH_CHAIN_SQL",
                            "SELECT CAST(:d AS uuid) AS id, 1 AS step"
                            " WHERE CAST(:steps AS int) > 0")
        task = _fresh(w)
        async with _session(w.p) as call:
            out = await _claim(call, _in_session(w.alice, _key()), task,
                               {"dispatched_by": ghost, "agent_label": "lane-ghost"})
        assert (out["warning"], out["dispatched_by_dropped"]) == (_UNKNOWN, "unknown")
        [row] = _rows(w.p, "SELECT dispatched_by_run_id, agent_label FROM pm_agent_runs"
                           " WHERE id = CAST(:i AS uuid)", i=out["agent_run_id"])
        assert (row.dispatched_by_run_id, row.agent_label) == (None, "lane-ghost")
        assert ALICE in out["assignees"], out

    async def test_a_claim_with_the_runs_flag_off_drops_it(self, w, monkeypatch):
        async with _session(w.p) as call:
            boss = await _run_of(call, w)
            monkeypatch.setattr(get_settings(), "agent_runs_enabled", False)
            out = await _claim(call, _in_session(w.alice, _key()), _fresh(w),
                               {"dispatched_by": boss})
        assert out["agent_run_id"] is None
        assert (out["warning"], out["dispatched_by_dropped"]) == (_NO_RUN, "no_run")

    async def test_a_delete_of_the_dispatcher_sets_null_and_keeps_the_lane(
            self, w, app_engine):  # noqa: F811
        """ON DELETE SET NULL, under the non-privileged role and the bound
        tenant, as a task delete runs it."""
        async with _session(w.p) as call:
            boss = await _run_of(call, w)
            lane = await _run_of(call, w, dispatched_by=boss)
        assert _dispatcher(w, lane) == boss
        with app_engine.begin() as c:
            c.execute(text("SELECT set_config('app.tenant_id', :o, true)"), {"o": w.org})
            gone = c.execute(text("DELETE FROM pm_agent_runs WHERE id = CAST(:i AS uuid)"),
                             {"i": boss}).rowcount
        assert gone == 1
        assert _dispatcher(w, lane) is None

    async def test_the_foreign_key_alone_does_not_hold_the_organization(
            self, w, app_engine):  # noqa: F811
        """Postgres checks a foreign key with no row security, so the key
        alone lets a raw write in the tenant of org A name a run of org B,
        which RLS hides from it. The gateway's read in the caller's session
        is the first guard. Task 3846 (migration 482, ``pm_same_org_ref``)
        added the second: the database now refuses that raw write with
        23503. A NULL still passes."""
        async with _session(w.p) as call:
            theirs = (await _claim(call, _in_session(w.other, _key()),
                                   _fresh(w, w.org_b, OTHER)))["agent_run_id"]
            mine = await _run_of(call, w)
        with app_engine.begin() as c:
            c.execute(text("SELECT set_config('app.tenant_id', :o, true)"), {"o": w.org})
            seen = c.execute(text("SELECT count(*) FROM pm_agent_runs"
                                  " WHERE id = CAST(:i AS uuid)"), {"i": theirs}).scalar()
            assert seen == 0
            sp = c.begin_nested()
            with pytest.raises(DBAPIError) as caught:
                c.execute(text("UPDATE pm_agent_runs SET dispatched_by_run_id = CAST(:d AS uuid)"
                               " WHERE id = CAST(:i AS uuid)"), {"d": theirs, "i": mine})
            sp.rollback()
            c.execute(text("UPDATE pm_agent_runs SET dispatched_by_run_id = NULL"
                           " WHERE id = CAST(:i AS uuid)"), {"i": mine})
        orig = caught.value.orig
        assert getattr(orig, "sqlstate", None) == "23503", orig
        assert ("pm_agent_runs.dispatched_by_run_id names no pm_agent_runs row of this"
                " organization") in str(orig), orig
        assert _dispatcher(w, mine) is None
