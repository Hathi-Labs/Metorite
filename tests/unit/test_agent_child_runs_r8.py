"""WS-MCP SW-39 and SW-114 — child runs: one sub-agent under an open run (R8).

Spec: ``project-docs/specs/agent_coordination.md`` §3.11.9, §3.12.7 rule 11
and the slices SW-39 and SW-114.

Migration ``agent_run_child`` adds ``kind`` (``run`` or ``child``) and
``parent_run_id`` to ``pm_agent_runs``, with ``ON DELETE CASCADE``. A child
has no task and no app, so every read of task runs and of app runs leaves it
out. The lease index ``pm_agent_runs_one_open_per_task`` leaves it out too.
``POST /projects/agent-runs/{run_id}/children`` is the one writer, and
``GET /projects/agent-runs/{run_id}/children`` is the one read.

SW-114 adds the rest of the writer, for the edge tool ``report_subagent``:
``agent_id`` is the key of a child (``agent_runs.child_key``), so a replayed
start writes nothing. ``event: stop`` ends the open child, and a stop of an
unknown or ended child changes nothing. A run holds at most 20 open
children, and the 21st start is 429. An own run of another connection is 403.

This file runs on a REAL Postgres, through the real ``/projects`` router,
under the non-privileged role of the phase-4 rehearsal (R8). The structural
fence is ``test_agent_label_fence.py`` (``TestTheChildGrantsNothing``) and
the subject fence of ``test_app_requests_r8.py``.

Run it on a private database::

    eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_agent_child_runs_r8.py -v -rs

Mutations (R7). ``G`` is ``gateway/routes/projects/agent_runs.py``, ``M`` is
the migration.

* C1. ``M``: build the lease index without ``kind <> 'child'``.
  ``test_the_columns_the_checks_and_the_indexes``.
* C2. ``G`` ``_parent_for_child``: skip the visibility read of the task.
  ``test_a_parent_on_a_task_that_the_caller_no_longer_sees_is_refused``.
* C3. ``G`` ``_parent_for_child``: skip the status test.
  ``test_a_closed_parent_is_refused``.
* C4. ``G`` ``_parent_for_child``: skip the connection test.
  ``test_a_run_of_another_member_or_connection_is_refused``. The member test
  beside it is a second wall: a connection is of one member, so no test
  reaches it alone.
* C5. ``G`` ``_move_run``: drop the call of ``_end_children``.
  ``test_a_parent_that_ends_ends_its_open_children``.
* C6. ``G`` ``_take_over``: drop the call of ``_end_children``.
  ``test_a_takeover_ends_the_children_of_the_old_run``.
* C7. ``G`` ``_INSERT_CHILD_SQL``: write the parent's task_id.
  ``test_a_child_starts_under_an_open_parent_that_holds_the_task`` and the
  subject CHECK.
* C8. ``G`` ``list_child_runs``: skip ``_load_visible_run``.
  ``test_a_viewer_who_cannot_see_the_parent_sees_no_child``.
* C9. ``G`` ``start_child_run``: skip the person check.
  ``test_a_person_body_is_refused``.
* C10. ``G`` ``start_child_run``: skip the read of ``_CHILD_BY_KEY_SQL``.
  ``test_a_replayed_start_writes_nothing`` (the insert finds the key and
  answers 409).
* C11. ``G`` ``start_child_run``: skip the cap.
  ``test_the_21st_open_child_is_429_and_a_stop_frees_a_slot``.
* C12. ``G`` ``_STOP_CHILD_SQL``: drop ``idempotency_key = :key``.
  ``test_a_stop_ends_one_child_and_a_second_stop_is_quiet``.
* C13. ``G`` ``_parent_for_child``: give 404 for another connection.
  ``test_a_run_of_another_member_or_connection_is_refused``.
* C14. ``G`` ``start_child_run``: drop the ``_child_writes_on`` check.
  ``TestTheDarkRoute`` (each test).
* C15. ``G`` ``start_child_run``: move the flag check after the parent lock.
  ``test_the_flag_off_takes_no_lock``.
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
from sqlalchemy.exc import IntegrityError

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

ALICE = "sw39-alice@child.test"     # the parent's member, org A
BOB = "sw39-bob@child.test"         # another member of org A
LANE = "sw39-lane@child.test"       # a member of org A who sees only a granted task
OTHER = "sw39-other@child.test"     # a member of org B

_REFUSED = "No open run of yours holds a task with that id."
_OTHER_CONNECTION = "That run belongs to another connection of yours."
_CAP = "This run already has 20 open sub-agents. Stop one before you start another."
_OPEN = ("created", "in_progress", "awaiting", "stopping")


@pytest.fixture(scope="module")
def world(promoted):  # noqa: F811
    org, org_b = promoted.org_a, promoted.org_b
    ns = SimpleNamespace(p=promoted, org=org, org_b=org_b)
    with promoted.admin_engine.begin() as c:
        _member_row(c, org, ALICE, "Alice Child")
        _member_row(c, org, BOB, "Bob Child")
        _member_row(c, org, LANE, "Lane Child")
        _member_row(c, org_b, OTHER, "Other Child")
    ns.conn_alice = _connection(promoted, org, ALICE, CLIENT_ID, "Claude Code")
    ns.conn_alice_2 = _connection(promoted, org, ALICE, CLIENT_ID, "Claude Code")
    ns.alice = _agent(ALICE, org, ns.conn_alice, R8_VIA)
    ns.alice_2 = _agent(ALICE, org, ns.conn_alice_2, R8_VIA)
    ns.alice_person = _person(ALICE, org)
    ns.conn_bob = _connection(promoted, org, BOB, CLIENT_ID, "Claude Code")
    ns.bob = _agent(BOB, org, ns.conn_bob, R8_VIA)
    ns.lane_person = _person(LANE, org, sees_all=False)
    ns.conn_other = _connection(promoted, org_b, OTHER, CLIENT_ID, "Claude Code")
    ns.other = _agent(OTHER, org_b, ns.conn_other, R8_VIA)
    return ns


@pytest.fixture
def w(world, monkeypatch):
    """SW-114: the child writes are dark behind ``AGENT_MAP_ORGS``, so each
    test runs with the flag on for every org. ``TestTheDarkRoute`` turns it
    off or on for one org."""
    monkeypatch.setattr(get_settings(), "agent_runs_enabled", True)
    monkeypatch.setattr(get_settings(), "agent_map_orgs", "*")
    with world.p.admin_engine.begin() as c:
        c.execute(text("DELETE FROM pm_agent_runs"))
    return world


_numbers = iter(range(140_000, 160_000))


def _fresh(w: Any, org: str | None = None, owner: str = ALICE,
           grant: str | None = None) -> str:
    return _seed_task(w.p, org or w.org, owner, n=next(_numbers), grant=grant)


def _key() -> str:
    """A session key in the shape of SW-7: 22 base64url characters."""
    return secrets.token_urlsafe(16)


async def _claim(call: Any, user: Any, task: str) -> str:
    answer = await call(user, "POST", f"/projects/tasks/{task}/claim", None)
    assert answer.status_code == 200, answer.text
    run_id = answer.json()["agent_run_id"]
    assert run_id, answer.text
    return str(run_id)


_agent_ids = iter(range(1, 1_000_000))


async def _child(call: Any, user: Any, parent: str, label: str = "implementer",
                 **extra: Any) -> Any:
    """One start, with a new ``agent_id`` unless *extra* names one."""
    body = {"agent_id": f"sub-{next(_agent_ids)}", "agent_label": label, **extra}
    return await call(user, "POST", f"/projects/agent-runs/{parent}/children", body)


async def _stop(call: Any, user: Any, parent: str, agent_id: str) -> Any:
    return await call(user, "POST", f"/projects/agent-runs/{parent}/children",
                      {"agent_id": agent_id, "event": "stop"})


def _child_rows(w: Any, parent: str) -> list[Any]:
    return _rows(w.p, "SELECT id, organization_id, task_id, app, kind, parent_run_id,"
                      " member_email, connection_id, client_via, status, session_key,"
                      " idempotency_key, agent_label, agent_model, ended_at"
                      " FROM pm_agent_runs WHERE parent_run_id = CAST(:parent AS uuid)"
                      " ORDER BY created_at", parent=parent)


def _status(w: Any, run_id: str) -> str:
    [row] = _rows(w.p, "SELECT status FROM pm_agent_runs WHERE id = CAST(:i AS uuid)",
                  i=run_id)
    return str(row.status)


def _open_task_runs(w: Any, task: str) -> list[str]:
    return [str(r.id) for r in _rows(
        w.p, "SELECT id FROM pm_agent_runs WHERE task_id = CAST(:t AS uuid)"
             " AND status = ANY(:open)", t=task, open=list(_OPEN))]


def _age(w: Any, run_id: str, seconds: int) -> None:
    with w.p.admin_engine.begin() as c:
        c.execute(text(
            "UPDATE pm_agent_runs SET last_activity_at = now() - make_interval(secs => :s),"
            " last_agent_activity_at = now() - make_interval(secs => :s)"
            " WHERE id = CAST(:i AS uuid)"), {"s": seconds, "i": run_id})


def _admin_child(w: Any, parent: str, status: str = "in_progress") -> str:
    """A child row as the RLS-bypassing admin, for a state that the route
    cannot make: an open child under a parent that has ended."""
    with w.p.admin_engine.begin() as c:
        return str(c.execute(text(
            "INSERT INTO pm_agent_runs (organization_id, kind, parent_run_id, member_email,"
            " client_via, status, started_by, agent_label)"
            " SELECT organization_id, 'child', id, member_email, client_via, :s,"
            "        client_via, 'stray' FROM pm_agent_runs WHERE id = CAST(:p AS uuid)"
            " RETURNING id"), {"s": status, "p": parent}).scalar())


@_DB_GATE
class TestTheMigration:

    async def test_the_columns_the_checks_and_the_indexes(self, w):
        cols = {r.column_name: (r.data_type, r.is_nullable, r.column_default) for r in _rows(
            w.p, "SELECT column_name, data_type, is_nullable, column_default"
                 " FROM information_schema.columns WHERE table_name = 'pm_agent_runs'"
                 " AND column_name IN ('kind', 'parent_run_id')")}
        assert cols == {"kind": ("text", "NO", "'run'::text"),
                        "parent_run_id": ("uuid", "YES", None)}
        checks = {r.conname: (r.convalidated, r.definition) for r in _rows(
            w.p, "SELECT conname, convalidated, pg_get_constraintdef(oid) AS definition"
                 " FROM pg_constraint WHERE conrelid = to_regclass('pm_agent_runs')"
                 " AND conname IN ('pm_agent_runs_kind_check', 'pm_agent_runs_child_parent',"
                 " 'pm_agent_runs_parent_fk', 'pm_agent_runs_one_subject')")}
        assert set(checks) == {"pm_agent_runs_kind_check", "pm_agent_runs_child_parent",
                               "pm_agent_runs_parent_fk", "pm_agent_runs_one_subject"}
        assert all(valid for valid, _ in checks.values()), checks
        assert "ON DELETE CASCADE" in checks["pm_agent_runs_parent_fk"][1]
        assert "child" in checks["pm_agent_runs_one_subject"][1]
        index = {r.indexname: r.indexdef for r in _rows(
            w.p, "SELECT indexname, indexdef FROM pg_indexes WHERE tablename = 'pm_agent_runs'")}
        lease = index["pm_agent_runs_one_open_per_task"]
        assert "UNIQUE" in lease and "(organization_id, task_id)" in lease, lease
        assert "kind <> 'child'::text" in lease, lease
        for status in _OPEN:
            assert f"'{status}'" in lease, (status, lease)
        assert "pm_agent_runs_one_open_per_task_sw39" not in index
        assert "(parent_run_id)" in index["pm_agent_runs_parent"]
        assert "parent_run_id IS NOT NULL" in index["pm_agent_runs_parent"]

    async def test_the_checks_refuse_a_bad_shape(self, w):
        """A child with a task, a child with no parent, a run with a parent,
        and an unknown kind each fail at the database."""
        async with _session(w.p) as call:
            parent = await _claim(call, _in_session(w.alice, _key()), _fresh(w))
        [row] = _rows(w.p, "SELECT task_id, member_email, client_via FROM pm_agent_runs"
                           " WHERE id = CAST(:i AS uuid)", i=parent)
        bad = (
            ("child", parent, row.task_id),   # a child with a task
            ("child", None, None),            # a child with no parent
            ("run", parent, row.task_id),     # a run with a parent
            ("agent", None, row.task_id),     # an unknown kind
        )
        for kind, parent_id, task_id in bad:
            with pytest.raises(IntegrityError), w.p.admin_engine.begin() as c:
                c.execute(text(
                    "INSERT INTO pm_agent_runs (organization_id, task_id, kind, parent_run_id,"
                    " member_email, client_via, status, started_by) VALUES (CAST(:o AS uuid),"
                    " CAST(:t AS uuid), :k, CAST(:p AS uuid), :m, :v, 'in_progress', :v)"),
                    {"o": w.org, "t": task_id, "k": kind, "p": parent_id,
                     "m": row.member_email, "v": row.client_via})


@_DB_GATE
class TestTheChildRunOnARealDatabase:

    async def test_a_child_starts_under_an_open_parent_that_holds_the_task(self, w):
        """The first "Done when" of SW-39. The child keeps the name of its
        agent type and its model. It has no task, no app, no session key and
        no key of its own, and the parent stays the one open run of the task."""
        task = _fresh(w)
        session = _in_session(w.alice, _key())
        async with _session(w.p) as call:
            parent = await _claim(call, session, task)
            made = await _child(call, session, parent, "verifier", agent_id="sub-a1",
                                model="claude-sonnet-5.5", host="box-1", host_kind="server")
        assert made.status_code == 201, made.text
        assert (made.json()["created"], made.json()["ended"]) == (True, False)
        run = made.json()["run"]
        assert (run["kind"], run["parent_run_id"], run["status"]) == (
            "child", parent, "in_progress")
        assert (run["agent_label"], run["agent_model"], run["agent_host"],
                run["agent_host_kind"]) == ("verifier", "claude-sonnet-5.5", "box-1", "server")
        assert run["started_at"] and run["ended_at"] is None
        [row] = _child_rows(w, parent)
        assert str(row.id) == run["id"]
        assert (row.task_id, row.app, row.session_key) == (None, None, None)
        assert row.idempotency_key == f"child:{parent}:sub-a1"
        assert (str(row.organization_id), row.member_email, str(row.connection_id)) == (
            w.org, ALICE, w.conn_alice)
        assert row.client_via == R8_VIA
        assert _open_task_runs(w, task) == [parent]
        assert _status(w, parent) == "in_progress"

    async def test_a_second_normal_run_still_gets_the_lease_refusal(self, w):
        """The second "Done when". A child takes no lease, so the parent still
        holds the task: another member and another session of Alice meet
        ``task_held``, by a claim and by a start."""
        task = _fresh(w)
        session = _in_session(w.alice, _key())
        async with _session(w.p) as call:
            parent = await _claim(call, session, task)
            assert (await _child(call, session, parent)).status_code == 201
            assert (await _child(call, session, parent, "reviewer")).status_code == 201
            bob = await call(_in_session(w.bob, _key()), "POST",
                             f"/projects/tasks/{task}/claim", None)
            bob_start = await call(_in_session(w.bob, _key()), "POST",
                                   f"/projects/tasks/{task}/agent-runs",
                                   {"idempotency_key": _key()})
            sibling = await call(_in_session(w.alice, _key()), "POST",
                                 f"/projects/tasks/{task}/claim", None)
        for answer in (bob, bob_start, sibling):
            assert answer.status_code == 409, answer.text
            assert answer.json()["detail"]["error"] == "task_held", answer.text
        assert sibling.json()["detail"]["run_id"] == parent
        assert _open_task_runs(w, task) == [parent]
        assert len(_child_rows(w, parent)) == 2

    async def test_a_parent_of_another_organization_reads_as_an_unknown_one(self, w):
        """The third "Done when". RLS hides the run of org B, so it reads as
        unknown: the same 404 and the same text, so no oracle."""
        async with _session(w.p) as call:
            theirs = await _claim(call, _in_session(w.other, _key()),
                                  _fresh(w, w.org_b, OTHER))
            session = _in_session(w.alice, _key())
            answers = [await _child(call, session, theirs),
                       await _child(call, session, str(uuid.uuid4())),
                       await _child(call, session, "not-a-run")]
        for answer in answers:
            assert answer.status_code == 404, answer.text
            assert answer.json() == {"detail": _REFUSED}
        assert _child_rows(w, theirs) == []

    async def test_a_closed_parent_is_refused(self, w):
        session = _in_session(w.alice, _key())
        async with _session(w.p) as call:
            parent = await _claim(call, session, _fresh(w))
            fin = await call(session, "POST", f"/projects/agent-runs/{parent}/finish", {})
            assert fin.status_code == 200, fin.text
            answer = await _child(call, session, parent)
        assert answer.status_code == 404 and answer.json() == {"detail": _REFUSED}
        assert _child_rows(w, parent) == []

    async def test_a_child_is_never_a_parent(self, w):
        """No grandchildren: a child has no task, so the parent read never
        finds it."""
        session = _in_session(w.alice, _key())
        async with _session(w.p) as call:
            parent = await _claim(call, session, _fresh(w))
            child = (await _child(call, session, parent)).json()["run"]["id"]
            answer = await _child(call, session, child)
        assert answer.status_code == 404 and answer.json() == {"detail": _REFUSED}
        assert _child_rows(w, child) == []

    async def test_a_run_of_another_member_or_connection_is_refused(self, w):
        """SW-114: an own run of another connection of the same member is 403,
        for a start and for a stop. A run of another member stays the one 404,
        so the 403 tells a member only about the member's own run."""
        session = _in_session(w.alice, _key())
        async with _session(w.p) as call:
            parent = await _claim(call, session, _fresh(w))
            assert (await _child(call, session, parent, agent_id="mine")).status_code == 201
            bob = _in_session(w.bob, _key())
            alice_2 = _in_session(w.alice_2, _key())
            by_bob = [await _child(call, bob, parent), await _stop(call, bob, parent, "mine")]
            by_other_connection = [await _child(call, alice_2, parent),
                                   await _stop(call, alice_2, parent, "mine")]
        for answer in by_bob:
            assert answer.status_code == 404 and answer.json() == {"detail": _REFUSED}
        for answer in by_other_connection:
            assert answer.status_code == 403, answer.text
            assert answer.json() == {"detail": _OTHER_CONNECTION}
        assert [r.status for r in _child_rows(w, parent)] == ["in_progress"]

    async def test_a_parent_that_lost_its_task_to_a_takeover_is_refused(self, w):
        """A run whose lease ended and that another member took over is
        closed, so it is no parent. The holder read of ``_parent_for_child``
        is a second wall: under the lease index an open task run is always
        the holder of its task, so no test can reach that read alone."""
        task = _fresh(w)
        session = _in_session(w.alice, _key())
        async with _session(w.p) as call:
            parent = await _claim(call, session, task)
            _age(w, parent, 24 * 3600)
            await _claim(call, _in_session(w.bob, _key()), task)
            answer = await _child(call, session, parent)
        assert _status(w, parent) == "abandoned"
        assert answer.status_code == 404 and answer.json() == {"detail": _REFUSED}

    async def test_a_parent_on_a_task_that_the_caller_no_longer_sees_is_refused(self, w):
        """The agent of Lane sees only the task granted to it. When the grant
        and the assignee row go, its open run is no parent any more."""
        from tests.unit.test_agent_run_dispatch_r8 import _narrow_agent

        conn = _connection(w.p, w.org, LANE, CLIENT_ID, "Claude Code")
        lane = _in_session(_narrow_agent(LANE, w.org, conn), _key())
        task = _fresh(w, grant=LANE)
        async with _session(w.p) as call:
            parent = await _claim(call, lane, task)
            assert (await _child(call, lane, parent)).status_code == 201
            with w.p.admin_engine.begin() as c:
                c.execute(text(
                    "DELETE FROM pm_project_grants WHERE subject = :s AND project_id ="
                    " (SELECT project_id FROM pm_tasks WHERE id = CAST(:t AS uuid))"),
                    {"s": LANE, "t": task})
                # The claim made Lane an assignee, and an assignee sees the task.
                c.execute(text("DELETE FROM pm_task_assignees WHERE task_id = CAST(:t AS uuid)"),
                          {"t": task})
            answer = await _child(call, lane, parent)
        assert answer.status_code == 404 and answer.json() == {"detail": _REFUSED}
        assert len(_child_rows(w, parent)) == 1

    async def test_a_parent_that_ends_ends_its_open_children(self, w):
        """The fourth "Done when", for a finish and a release. Each open
        child takes the parent's status. A child that had ended keeps its
        own status."""
        async with _session(w.p) as call:
            ended = {}
            for path, status in (("finish", "completed"), ("release", "released")):
                session = _in_session(w.alice, _key())
                parent = await _claim(call, session, _fresh(w))
                for label in ("implementer", "verifier"):
                    assert (await _child(call, session, parent, label)).status_code == 201
                done = _admin_child(w, parent, status="failed")
                answer = await call(session, "POST", f"/projects/agent-runs/{parent}/{path}", {})
                assert answer.status_code == 200, answer.text
                ended[status] = (parent, done)
        for status, (parent, done) in ended.items():
            assert _status(w, parent) == status
            rows = {str(r.id): (r.status, r.ended_at is not None) for r in _child_rows(w, parent)}
            assert rows.pop(done) == ("failed", False)
            assert set(rows.values()) == {(status, True)}, rows

    async def test_a_takeover_ends_the_children_of_the_old_run(self, w):
        task = _fresh(w)
        session = _in_session(w.alice, _key())
        async with _session(w.p) as call:
            parent = await _claim(call, session, task)
            assert (await _child(call, session, parent)).status_code == 201
            _age(w, parent, 24 * 3600)
            bob_run = await _claim(call, _in_session(w.bob, _key()), task)
        assert _status(w, parent) == "abandoned"
        assert [r.status for r in _child_rows(w, parent)] == ["abandoned"]
        assert _open_task_runs(w, task) == [bob_run]

    async def test_a_child_is_never_a_holder_in_unheld_or_in_the_run_lists(self, w):
        """A child never counts as the holder of a task. With the parent
        ended and a stray open child left (a state that only the admin can
        make), the task reads unheld, it shows no ``held_by``, and a new
        claim takes it. The run lists and the ``needs_agent`` list that
        ``my_work`` reads never name the child."""
        task = _fresh(w)
        session = _in_session(w.alice, _key())
        async with _session(w.p) as call:
            parent = await _claim(call, session, task)
            child = (await _child(call, session, parent)).json()["run"]["id"]
            held = (await call(w.alice_person, "GET", f"/projects/tasks/{task}")).json()
            mine = (await call(w.alice_person, "GET", "/projects/agent-runs")).json()
            needs = (await call(session, "GET", "/projects/agent-runs?needs_agent=true")).json()
            of_task = (await call(w.alice_person, "GET",
                                  f"/projects/tasks/{task}/agent-runs")).json()
            fin = await call(session, "POST", f"/projects/agent-runs/{parent}/finish", {})
            assert fin.status_code == 200, fin.text
            stray = _admin_child(w, parent)
            free = (await call(w.alice_person, "GET", f"/projects/tasks/{task}")).json()
            unheld = (await call(w.alice_person, "GET",
                                 "/projects/tasks?unheld=true&page_size=100")).json()
            bob_run = await _claim(call, _in_session(w.bob, _key()), task)
        assert held["held_by"]["member"] == ALICE
        assert [r["id"] for r in mine["rows"]] == [parent]
        assert mine["rows"][0]["kind"] == "run" and mine["rows"][0]["parent_run_id"] is None
        assert child not in {r["id"] for r in needs["rows"]}
        assert [r["id"] for r in of_task["rows"]] == [parent]
        assert free["held_by"] is None
        assert task in {r["id"] for r in unheld["rows"]}
        assert _open_task_runs(w, task) == [bob_run]
        assert _status(w, stray) == "in_progress"

    async def test_a_viewer_who_cannot_see_the_parent_sees_no_child(self, w, monkeypatch):
        """The fifth "Done when". The parent's task is granted to Alice
        only. Lane cannot see it, so the children read is 404, and no list,
        run read or map read names the child. Alice sees the child under
        its parent only: a child id is no run of its own."""
        monkeypatch.setattr(get_settings(), "agents_view_orgs", "*")
        monkeypatch.setattr(get_settings(), "agent_map_orgs", "*")
        task = _fresh(w, grant=ALICE)
        session = _in_session(w.alice, _key())
        async with _session(w.p) as call:
            parent = await _claim(call, session, task)
            child = (await _child(call, session, parent, "secret-lane")).json()["run"]["id"]
            lane = {
                "children": await call(w.lane_person, "GET",
                                       f"/projects/agent-runs/{parent}/children"),
                "parent": await call(w.lane_person, "GET", f"/projects/agent-runs/{parent}"),
                "child": await call(w.lane_person, "GET", f"/projects/agent-runs/{child}"),
                "list": await call(w.lane_person, "GET", "/projects/agent-runs"),
                "map": await call(w.lane_person, "GET",
                                  "/projects/agents/overview?shape=map"),
            }
            alice = {
                "children": await call(w.alice_person, "GET",
                                       f"/projects/agent-runs/{parent}/children"),
                "child": await call(w.alice_person, "GET", f"/projects/agent-runs/{child}"),
                "map": await call(w.alice_person, "GET",
                                  "/projects/agents/overview?shape=map"),
            }
        assert lane["children"].status_code == 404, lane["children"].text
        assert lane["parent"].status_code == 404 and lane["child"].status_code == 404
        assert lane["list"].status_code == 200 and lane["list"].json()["rows"] == []
        assert lane["map"].status_code == 200, lane["map"].text
        for answer in (lane["list"], lane["map"], alice["map"]):
            assert child not in answer.text and "secret-lane" not in answer.text
        assert alice["children"].status_code == 200, alice["children"].text
        assert [r["id"] for r in alice["children"].json()["rows"]] == [child]
        assert alice["children"].json()["rows"][0]["parent_run_id"] == parent
        assert alice["child"].status_code == 404

    async def test_a_person_body_is_refused(self, w):
        session = _in_session(w.alice, _key())
        async with _session(w.p) as call:
            parent = await _claim(call, session, _fresh(w))
            person = await _child(call, w.alice_person, parent)
            person_stop = await _stop(call, w.alice_person, parent, "sub-x")
            no_label = await call(session, "POST", f"/projects/agent-runs/{parent}/children", {})
            only_id = await call(session, "POST", f"/projects/agent-runs/{parent}/children",
                                 {"agent_id": "sub-y"})
            blank = await _child(call, session, parent, "​\x07")
            extra = await _child(call, session, parent, task_id=str(uuid.uuid4()))
            bad_ids = [await _child(call, session, parent, agent_id=bad)
                       for bad in ("", "a b", "a/b", "x" * 65)]
            bad_event = await _child(call, session, parent, event="pause")
        for answer in (person, person_stop):
            assert answer.status_code == 422, answer.text
            assert answer.json() == {
                "detail": "Only an agent starts a child run, for one of its sub-agents."}
        for answer in (no_label, only_id, blank, extra, *bad_ids, bad_event):
            assert answer.status_code == 422, answer.text
        assert _child_rows(w, parent) == []

    async def test_a_delete_of_the_parent_deletes_its_children(
            self, w, app_engine):  # noqa: F811
        """ON DELETE CASCADE, under the non-privileged role and the bound
        tenant, as a task delete runs it. A delete of the task takes the
        parent, and the parent takes the children."""
        async with _session(w.p) as call:
            session = _in_session(w.alice, _key())
            parent = await _claim(call, session, _fresh(w))
            assert (await _child(call, session, parent)).status_code == 201
            session_2 = _in_session(w.alice, _key())
            task_2 = _fresh(w)
            parent_2 = await _claim(call, session_2, task_2)
            assert (await _child(call, session_2, parent_2)).status_code == 201
        with app_engine.begin() as c:
            c.execute(text("SELECT set_config('app.tenant_id', :o, true)"), {"o": w.org})
            gone = c.execute(text("DELETE FROM pm_agent_runs WHERE id = CAST(:i AS uuid)"),
                             {"i": parent}).rowcount
            c.execute(text("DELETE FROM pm_tasks WHERE id = CAST(:t AS uuid)"), {"t": task_2})
        assert gone == 1
        assert _child_rows(w, parent) == [] and _child_rows(w, parent_2) == []

    async def test_the_lease_index_leaves_children_out(self, w):
        """The index on its own, with the subject CHECK dropped for one
        transaction that rolls back: two open children that name the
        parent's task, each with its own client stamp, sit beside the open
        parent. Under the old index the first would fail."""
        session = _in_session(w.alice, _key())
        async with _session(w.p) as call:
            parent = await _claim(call, session, _fresh(w))
        with w.p.admin_engine.connect() as c:
            trans = c.begin()
            c.execute(text("ALTER TABLE pm_agent_runs DROP CONSTRAINT pm_agent_runs_one_subject"))
            for n in (1, 2):
                c.execute(text(
                    "INSERT INTO pm_agent_runs (organization_id, task_id, kind, parent_run_id,"
                    " member_email, client_via, status, started_by)"
                    " SELECT organization_id, task_id, 'child', id, member_email, :v,"
                    "        'in_progress', :v FROM pm_agent_runs"
                    " WHERE id = CAST(:p AS uuid)"), {"p": parent, "v": f"mcp:sw39:child-{n}"})
            assert len(_open_child_rows(c, parent)) == 2
            trans.rollback()
        [row] = _rows(w.p, "SELECT count(*) AS n FROM pg_constraint"
                           " WHERE conname = 'pm_agent_runs_one_subject'")
        assert row.n == 1
        assert _child_rows(w, parent) == []


@_DB_GATE
class TestTheSubagentReports:
    """SW-114 (``agent_coordination.md`` §3.12.7 rule 11). The start and the
    stop that the edge tool ``report_subagent`` sends."""

    async def test_a_replayed_start_writes_nothing(self, w):
        """The first and second "Done when" of SW-114. A start writes one
        child. The same ``agent_id`` again writes none and answers the same
        child, 200, also after a stop. Another ``agent_id`` is another child."""
        session = _in_session(w.alice, _key())
        async with _session(w.p) as call:
            parent = await _claim(call, session, _fresh(w))
            first = await _child(call, session, parent, "verifier", agent_id="sub-r1")
            again = await _child(call, session, parent, "renamed", agent_id="sub-r1",
                                 model="other-model")
            other = await _child(call, session, parent, "verifier", agent_id="sub-r2")
            stopped = await _stop(call, session, parent, "sub-r1")
            after_stop = await _child(call, session, parent, "verifier", agent_id="sub-r1")
        assert first.status_code == 201, first.text
        assert again.status_code == 200, again.text
        assert again.json()["run"] == first.json()["run"]
        assert (again.json()["created"], again.json()["ended"]) == (False, False)
        assert again.json()["run"]["agent_label"] == "verifier"
        assert other.status_code == 201 and other.json()["run"]["id"] != first.json()["run"]["id"]
        assert stopped.status_code == 200, stopped.text
        assert after_stop.status_code == 200, after_stop.text
        assert after_stop.json()["run"]["id"] == first.json()["run"]["id"]
        assert after_stop.json()["run"]["status"] == "completed"
        assert after_stop.json()["created"] is False
        rows = _child_rows(w, parent)
        assert len(rows) == 2
        assert sorted(r.idempotency_key for r in rows) == [
            f"child:{parent}:sub-r1", f"child:{parent}:sub-r2"]

    async def test_a_stop_ends_one_child_and_a_second_stop_is_quiet(self, w):
        """The third "Done when" of SW-114. A stop ends the open child of its
        ``agent_id``: the status ``completed`` and ``ended_at``. A second stop
        and a stop of an unknown id change nothing and answer 200. A stop
        never ends the parent or another child."""
        session = _in_session(w.alice, _key())
        async with _session(w.p) as call:
            parent = await _claim(call, session, _fresh(w))
            one = (await _child(call, session, parent, agent_id="sub-s1")).json()["run"]["id"]
            two = (await _child(call, session, parent, agent_id="sub-s2")).json()["run"]["id"]
            stop = await _stop(call, session, parent, "sub-s1")
            ended_at = _rows(w.p, "SELECT ended_at FROM pm_agent_runs"
                                  " WHERE id = CAST(:i AS uuid)", i=one)[0].ended_at
            second = await _stop(call, session, parent, "sub-s1")
            unknown = await _stop(call, session, parent, "sub-none")
        assert stop.status_code == 200, stop.text
        assert (stop.json()["created"], stop.json()["ended"]) == (False, True)
        assert stop.json()["run"]["id"] == one
        assert stop.json()["run"]["status"] == "completed"
        assert stop.json()["run"]["ended_at"]
        assert second.status_code == 200, second.text
        assert (second.json()["ended"], second.json()["run"]["id"]) == (False, one)
        assert unknown.status_code == 200, unknown.text
        assert unknown.json() == {"run": None, "created": False, "ended": False}
        assert _status(w, one) == "completed" and _status(w, two) == "in_progress"
        assert _rows(w.p, "SELECT ended_at FROM pm_agent_runs WHERE id = CAST(:i AS uuid)",
                     i=one)[0].ended_at == ended_at
        assert _status(w, parent) == "in_progress"

    async def test_the_21st_open_child_is_429_and_a_stop_frees_a_slot(self, w):
        """The fifth "Done when" of SW-114. 20 open children fit. The 21st
        start is 429 with the fixed text, and it writes nothing. A replay of
        an open child still answers 200. A stop frees a slot."""
        session = _in_session(w.alice, _key())
        async with _session(w.p) as call:
            parent = await _claim(call, session, _fresh(w))
            for n in range(20):
                made = await _child(call, session, parent, agent_id=f"cap-{n}")
                assert made.status_code == 201, (n, made.text)
            over = await _child(call, session, parent, agent_id="cap-20")
            replay = await _child(call, session, parent, agent_id="cap-3")
            assert (await _stop(call, session, parent, "cap-0")).status_code == 200
            freed = await _child(call, session, parent, agent_id="cap-20")
            over_again = await _child(call, session, parent, agent_id="cap-21")
        assert over.status_code == 429, over.text
        assert over.json() == {"detail": _CAP}
        assert replay.status_code == 200, replay.text
        assert freed.status_code == 201, freed.text
        assert over_again.status_code == 429 and over_again.json() == {"detail": _CAP}
        rows = _child_rows(w, parent)
        assert len(rows) == 21
        assert sum(1 for r in rows if r.status in _OPEN) == 20

    async def test_another_org_and_an_unknown_id_get_the_same_fixed_404(self, w):
        """A start and a stop under a run of org B, an unknown id and a word
        all read the same, so the answer names no other organization."""
        async with _session(w.p) as call:
            theirs_session = _in_session(w.other, _key())
            theirs = await _claim(call, theirs_session, _fresh(w, w.org_b, OTHER))
            assert (await _child(call, theirs_session, theirs,
                                 agent_id="theirs")).status_code == 201
            session = _in_session(w.alice, _key())
            answers = []
            for target in (theirs, str(uuid.uuid4()), "not-a-run"):
                answers.append(await _child(call, session, target))
                answers.append(await _stop(call, session, target, "theirs"))
        for answer in answers:
            assert answer.status_code == 404, answer.text
            assert answer.json() == {"detail": _REFUSED}
        assert [r.status for r in _child_rows(w, theirs)] == ["in_progress"]

    async def test_a_closed_parent_takes_no_start_and_a_stop_is_quiet(self, w):
        """A start under a closed parent is the 404. Its children ended with
        it, so a stop finds no open child and changes nothing."""
        session = _in_session(w.alice, _key())
        async with _session(w.p) as call:
            parent = await _claim(call, session, _fresh(w))
            assert (await _child(call, session, parent, agent_id="sub-c1")).status_code == 201
            fin = await call(session, "POST", f"/projects/agent-runs/{parent}/finish", {})
            assert fin.status_code == 200, fin.text
            start = await _child(call, session, parent, agent_id="sub-c2")
            stop = await _stop(call, session, parent, "sub-c1")
        assert start.status_code == 404 and start.json() == {"detail": _REFUSED}
        assert stop.status_code == 200, stop.text
        assert stop.json()["ended"] is False
        assert stop.json()["run"]["status"] == "completed"
        assert [r.status for r in _child_rows(w, parent)] == ["completed"]

    async def test_a_key_that_another_run_holds_is_409(self, w):
        """The unique key of migration 267 is on organization, member and
        key. When another run of the member holds the key of a child, the
        start writes nothing and answers 409, never a 500."""
        session = _in_session(w.alice, _key())
        async with _session(w.p) as call:
            parent = await _claim(call, session, _fresh(w))
            other = await _claim(call, _in_session(w.alice, _key()), _fresh(w))
            with w.p.admin_engine.begin() as c:
                c.execute(text("UPDATE pm_agent_runs SET idempotency_key = :k"
                               " WHERE id = CAST(:i AS uuid)"),
                          {"k": f"child:{parent}:sub-k1", "i": other})
            answer = await _child(call, session, parent, agent_id="sub-k1")
        assert answer.status_code == 409, answer.text
        assert answer.json() == {"detail": "That agent_id is the key of another run of yours."}
        assert _child_rows(w, parent) == []


def _run_count(w: Any) -> int:
    return int(_rows(w.p, "SELECT count(*) AS n FROM pm_agent_runs")[0].n)


@_DB_GATE
class TestTheDarkRoute:
    """SW-114. The child writes are dark behind ``AGENT_MAP_ORGS``. Off for
    the caller's org, a start and a stop answer the 404 of an unknown run,
    byte for byte, before any read, lock or write."""

    async def test_the_flag_off_gives_the_unknown_run_404_and_writes_nothing(
            self, w, monkeypatch):
        session = _in_session(w.alice, _key())
        async with _session(w.p) as call:
            parent = await _claim(call, session, _fresh(w))
            assert (await _child(call, session, parent, agent_id="on-1")).status_code == 201
            unknown = await _child(call, session, str(uuid.uuid4()))
            before = _run_count(w)
            monkeypatch.setattr(get_settings(), "agent_map_orgs", "")
            off = [await _child(call, session, parent, agent_id="off-1"),
                   await _child(call, session, parent, agent_id="on-1"),
                   await _stop(call, session, parent, "on-1"),
                   await _child(call, w.alice_person, parent),
                   await _child(call, session, str(uuid.uuid4()))]
            after = _run_count(w)
        assert unknown.status_code == 404
        for answer in off:
            assert answer.status_code == 404, answer.text
            assert answer.content == unknown.content
        assert after == before
        assert [r.status for r in _child_rows(w, parent)] == ["in_progress"]

    async def test_the_flag_off_takes_no_lock(self, w, monkeypatch):
        """An admin transaction holds the parent row FOR UPDATE. With the flag
        off, the start and the stop still answer at once, so the route never
        asks for the lock. With the flag on the route would wait for it."""
        import asyncio

        session = _in_session(w.alice, _key())
        async with _session(w.p) as call:
            parent = await _claim(call, session, _fresh(w))
            monkeypatch.setattr(get_settings(), "agent_map_orgs", "")
            with w.p.admin_engine.connect() as c:
                trans = c.begin()
                c.execute(text("SELECT id FROM pm_agent_runs WHERE id = CAST(:i AS uuid)"
                               " FOR UPDATE"), {"i": parent})
                try:
                    start = await asyncio.wait_for(
                        _child(call, session, parent, agent_id="lock-1"), 10)
                    stop = await asyncio.wait_for(
                        _stop(call, session, parent, "lock-1"), 10)
                finally:
                    trans.rollback()
        assert start.status_code == 404 and stop.status_code == 404
        assert _child_rows(w, parent) == []

    async def test_the_flag_is_read_for_each_org(self, w, monkeypatch):
        """On for org A only: org A writes and org B gets the 404. Then on
        for org B only: the answers swap."""
        alice = _in_session(w.alice, _key())
        other = _in_session(w.other, _key())
        async with _session(w.p) as call:
            mine = await _claim(call, alice, _fresh(w))
            theirs = await _claim(call, other, _fresh(w, w.org_b, OTHER))
            monkeypatch.setattr(get_settings(), "agent_map_orgs", w.org)
            a_on = await _child(call, alice, mine, agent_id="a-1")
            b_off = [await _child(call, other, theirs, agent_id="b-1"),
                     await _stop(call, other, theirs, "b-1")]
            monkeypatch.setattr(get_settings(), "agent_map_orgs", w.org_b)
            a_off = [await _child(call, alice, mine, agent_id="a-2"),
                     await _stop(call, alice, mine, "a-1")]
            b_on = await _child(call, other, theirs, agent_id="b-1")
        assert a_on.status_code == 201 and b_on.status_code == 201, (a_on.text, b_on.text)
        for answer in (*b_off, *a_off):
            assert answer.status_code == 404 and answer.json() == {"detail": _REFUSED}
        assert [r.status for r in _child_rows(w, mine)] == ["in_progress"]
        assert len(_child_rows(w, theirs)) == 1


def _open_child_rows(c: Any, parent: str) -> list[Any]:
    return list(c.execute(text(
        "SELECT id FROM pm_agent_runs WHERE parent_run_id = CAST(:p AS uuid)"
        " AND status = ANY(:open)"), {"p": parent, "open": list(_OPEN)}))


class TestTheEndOfTheChildren:
    """Hermetic: :func:`agent_runs._end_children` runs for each move to a
    terminal status, and for no other move."""

    @pytest.mark.parametrize("status", ["completed", "stopped", "failed", "abandoned",
                                        "released"])
    async def test_each_terminal_status_ends_the_children(self, status) -> None:
        from gateway.routes.projects import agent_runs

        seen: list[tuple[str, dict]] = []

        class _Db:
            async def execute(self, statement: Any, params: dict) -> Any:
                seen.append((str(statement), params))
                return SimpleNamespace(fetchall=lambda: [], fetchone=lambda: None)

        await agent_runs._end_children(_Db(), "r1", status, "ts")
        assert [s for s, _ in seen] == [agent_runs._END_CHILDREN_SQL]
        assert seen[0][1]["status"] == status and seen[0][1]["rid"] == "r1"

    @pytest.mark.parametrize("status", ["created", "in_progress", "awaiting", "stopping"])
    async def test_an_open_status_ends_nothing(self, status) -> None:
        from gateway.routes.projects import agent_runs

        class _Db:
            async def execute(self, statement: Any, params: dict) -> Any:
                raise AssertionError(str(statement))

        await agent_runs._end_children(_Db(), "r1", status, "ts")

    def test_the_end_names_the_child_subject_and_the_open_statuses(self) -> None:
        from gateway.routes.projects import agent_runs

        sql = agent_runs._END_CHILDREN_SQL
        assert "parent_run_id = CAST(:rid AS uuid)" in sql and "kind = 'child'" in sql
        assert "status = ANY(:open)" in sql
