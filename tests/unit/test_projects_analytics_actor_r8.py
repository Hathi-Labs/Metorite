"""WS-27bn RA-1 — who did it, on a real Postgres (R8).

Spec: ``project-docs/specs/projects_reports.md`` §6.8 F and §8 RA-1.

``test_projects_analytics_actor.py`` pins the Python twin of the rule. This
file pins the SQL twin, through the real ``/projects`` router as the
non-privileged role of the phase-4 rehearsal (the ``promoted`` fixture), on a
database with the whole ladder and two organizations:

* **D1** each seeded done task carries the label of the table ``DONE``: an
  agent through MCP (``meta.via``), an in-app agent (``agent:<name>``), a
  person after an agent run, a person after a queued run, a GitHub done after
  an MCP act, a person with a run that came after the finish, a person alone,
  a workflow, a task reopened from a finish before the window, a task that a
  person reopened and finished again after the agent's counted finish, and a
  task tidied into a second done lane. The Python twin agrees on each task,
  from the rows in the database.
* **D2** ``actor_split`` of ``/analytics/finished`` and
  ``/analytics/throughput`` counts those labels, and adds up to the
  ``completed`` figure of the same response.
* **H1** each seeded open task carries the live holder of the table ``OPEN``,
  and the Python twin agrees.
* **H2** ``holder_split`` of ``/analytics/load`` counts those holders, and
  adds up to ``total_tasks``.
* **V1** §7 rule 1: a member who sees one task reads one task in each split,
  and the other organization reads none.

Run (a private ladder database, one R8 suite at a time)::

    eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_projects_analytics_actor_r8.py -v -rs

**The mutations (R7).** Each one turns the named test red. They need the
database, so this PC did not run them (no Docker on 2026-10-25). CI runs the
suite.

* R1. ``finish_actor_join`` orders ``ASC`` (the earliest finish)
  -> ``test_d1_each_done_task_carries_its_label``.
* R2. ``finish_label_sql`` drops the ``mcp:`` arm -> the same test and D2.
* R3. ``finish_actor_join`` drops ``IS DISTINCT FROM :done_cat`` -> the same
  test (the tidied task).
* R4. ``holder_label_sql`` drops the ``NOT unheld`` arm
  -> ``test_h1_each_open_task_carries_its_holder``.
* R5. ``load_body`` passes ``scope_sql`` to ``holder_split_body`` in place of
  ``open_where`` -> ``test_v1_a_member_reads_only_what_they_see`` and H2.
* R6. ``finish_actor_join`` drops ``fa.created_at <= {until}`` -> D1 (the
  ``finished_again`` task) and D2.
* R7. ``finish_actor_join`` passes ``now()`` as the time of
  ``agent_worked_sql`` -> D1 (the ``run_after`` task).

The class carries ``_DB_GATE`` through ``pytestmark``, so the suite skips
where no ladder URL is set (the deploy run has no ladder database), and its
fixtures never start there.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("gateway.routes.projects", reason="gateway not installed")

from sqlalchemy import text

from tests.unit import _agent_board_r8 as b
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

pytestmark = _DB_GATE

A_EMAIL = "ra1-a@actor-r8.test"
PERSON = "ra1-p@actor-r8.test"
M_EMAIL = "ra1-m@actor-r8.test"
B_EMAIL = "ra1-b@actor-r8.test"
WORKFLOW = "system:workflow:ra1"
IN_APP = "agent:coder"

#: Each done task and the label the rule gives it (§6.8 F).
DONE = {
    "mcp": "agent_finished",
    "in_app": "agent_finished",
    "assisted": "agent_assisted",
    "queued_run": "agent_assisted",
    "github_after_mcp": "agent_assisted",
    "run_after": "person",
    "person": "person",
    "workflow": "person",
    # The agent finished it before the window through MCP, so an agent
    # worked it before the person's counted finish.
    "reopened": "agent_assisted",
    "finished_again": "agent_finished",
    "tidied": "agent_finished",
}

#: A finish before the 12-week window of the routes, in minutes.
_BEFORE_WINDOW = 100 * 24 * 60

#: Each open task and its live holder. None is nobody.
OPEN = {
    "person": "person",
    "agent_assignee": "agent",
    "live_run": "agent",
    "lease_gone": None,
    "nobody": None,
}


@pytest.fixture(scope="module")
def world(promoted):  # noqa: F811 — a fixture
    ns = SimpleNamespace(p=promoted)
    ns.a = b._principal(A_EMAIL, promoted.org_a)
    ns.m = b._principal(M_EMAIL, promoted.org_a, unrestricted=False)
    ns.b = b._principal(B_EMAIL, promoted.org_b)
    b.member(promoted, promoted.org_a, PERSON)
    b.member(promoted, promoted.org_a, M_EMAIL)
    b.member(promoted, promoted.org_b, B_EMAIL)
    return ns


@pytest.fixture
def w(world):
    b.clear(world.p)
    world.root = b.project(world.p, world.p.org_a, A_EMAIL)
    return world


def _task(w: Any, category: str = "todo") -> str:
    return b.task(w.p, w.p.org_a, A_EMAIL, w.root, category=category)


def _assign(w: Any, tid: str, who: str) -> None:
    """An assignee row, with no `app_user` row for an `agent:` name."""
    with w.p.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO pm_task_assignees (organization_id, task_id, assignee, "
            "assigned_by) VALUES (CAST(:o AS uuid), CAST(:t AS uuid), :a, :e)"),
            {"o": w.p.org_a, "t": tid, "a": who, "e": A_EMAIL})


def _move(
    w: Any, tid: str, by: str, *, via: str | None = None, frm: str = "todo",
    to: str = "done", minutes_ago: int = 5,
) -> None:
    """One `status_change` row, as `apply_status_transition` writes it."""
    meta: dict[str, Any] = {
        "from": frm, "to": to, "from_category": frm, "to_category": to,
    }
    if via is not None:
        meta["via"] = via
    with w.p.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO pm_activities (organization_id, task_id, type, created_by, "
            "body, meta, created_at) VALUES (CAST(:o AS uuid), CAST(:t AS uuid), "
            "'status_change', :by, :body, CAST(:m AS jsonb), "
            "now() - make_interval(mins => :ago))"),
            {"o": w.p.org_a, "t": tid, "by": by, "body": f"{frm} -> {to}",
             "m": json.dumps(meta), "ago": minutes_ago})


def _mcp_act(w: Any, tid: str, *, minutes_ago: int) -> None:
    """A comment that an agent wrote through MCP, at a chosen time."""
    with w.p.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO pm_activities (organization_id, task_id, type, created_by, "
            "body, meta, created_at) VALUES (CAST(:o AS uuid), CAST(:t AS uuid), "
            "'comment', :by, 'working', CAST(:m AS jsonb), "
            "now() - make_interval(mins => :ago))"),
            {"o": w.p.org_a, "t": tid, "by": A_EMAIL,
             "m": json.dumps({"via": b.VIA}), "ago": minutes_ago})


def _seed_done(w: Any) -> dict[str, str]:
    t = {name: _task(w, "done") for name in DONE}
    hour_ago = "now() - interval '1 hour'"
    _move(w, t["mcp"], A_EMAIL, via=b.VIA)
    _move(w, t["in_app"], IN_APP)
    b.run(w.p, w.p.org_a, t["assisted"], A_EMAIL, "completed", created=hour_ago)
    _move(w, t["assisted"], PERSON)
    # A queued run that never started counts, as `AGENT_WORKED_SQL` reads it.
    b.run(w.p, w.p.org_a, t["queued_run"], A_EMAIL, "created", created=hour_ago)
    _move(w, t["queued_run"], PERSON)
    # The agent worked through MCP only, and the merge moved the task to done.
    _mcp_act(w, t["github_after_mcp"], minutes_ago=30)
    _move(w, t["github_after_mcp"], "system:github", via="github")
    # The run came after the finish, so it did not help that finish.
    _move(w, t["run_after"], PERSON, minutes_ago=10)
    b.run(w.p, w.p.org_a, t["run_after"], A_EMAIL, "completed")
    _move(w, t["person"], PERSON)
    _move(w, t["workflow"], WORKFLOW)
    # The agent finished before the window, a person reopened and finished it
    # in the window. The window counts the person's finish, and the agent's
    # MCP finish before it is agent work, so the task is agent_assisted.
    _move(w, t["reopened"], A_EMAIL, via=b.VIA, minutes_ago=_BEFORE_WINDOW)
    _move(w, t["reopened"], PERSON, frm="done", to="todo", minutes_ago=20)
    _move(w, t["reopened"], PERSON, minutes_ago=10)
    # The agent finished in the window, a person reopened and finished it
    # again after. The window counts the agent's finish, so the label stays.
    _move(w, t["finished_again"], A_EMAIL, via=b.VIA, minutes_ago=30)
    _move(w, t["finished_again"], PERSON, frm="done", to="todo", minutes_ago=20)
    _move(w, t["finished_again"], PERSON, minutes_ago=10)
    # The agent finished, and a person moved it to a second done lane.
    _move(w, t["tidied"], A_EMAIL, via=b.VIA, minutes_ago=20)
    _move(w, t["tidied"], PERSON, frm="done", to="done", minutes_ago=10)
    return t


def _seed_open(w: Any) -> dict[str, str]:
    t = {name: _task(w) for name in OPEN}
    _assign(w, t["person"], PERSON)
    _assign(w, t["agent_assignee"], IN_APP)
    _assign(w, t["live_run"], PERSON)
    b.run(w.p, w.p.org_a, t["live_run"], A_EMAIL, "in_progress")
    gone = "now() - interval '3 hours'"
    b.run(w.p, w.p.org_a, t["lease_gone"], A_EMAIL, "in_progress",
          last_activity=gone, last_agent=gone)
    # A done task with a holder is not open work, so it counts nowhere.
    done = _task(w, "done")
    _assign(w, done, PERSON)
    return t


def _params(w: Any) -> dict[str, Any]:
    from gateway.routes.projects.core import (
        CLOSING_CATEGORIES,
        COMPLETED_CATEGORY,
        STARTED_CATEGORY,
    )

    return {
        "pid": w.root["id"], "weeks": 12,
        "closing": sorted(CLOSING_CATEGORIES), "closed": sorted(CLOSING_CATEGORIES),
        "done_cat": COMPLETED_CATEGORY, "started_cat": STARTED_CATEGORY,
    }


def _by_name(tasks: dict[str, str], labels: dict[str, Any]) -> dict[str, Any]:
    return {name: labels.get(tid, "ABSENT") for name, tid in tasks.items()}


async def _get(w: Any, user: Any, path: str) -> dict[str, Any]:
    async with b._session(w.p) as call:
        got = await call(user, "GET", path)
    assert got.status_code == 200, got.text
    return got.json()


def _python_finish(w: Any, tid: str, until: Any) -> str:
    """The Python twin, from the rows in the database. ``until`` is the
    finish that the window counts (the SQL row's ``finished_at``)."""
    from gateway.routes.projects import analytics as ana

    with w.p.admin_engine.begin() as c:
        moves = [
            ana.StatusMove(r.created_at, r.created_by, r.meta.get("via"),
                           r.meta.get("from_category"), r.meta.get("to_category"),
                           str(r.id))
            for r in c.execute(text(
                "SELECT id, created_at, created_by, meta FROM pm_activities"
                " WHERE task_id = CAST(:t AS uuid) AND type = 'status_change'"
                " AND deleted_at IS NULL"), {"t": tid})
        ]
        # The rows of `agent_worked`'s two halves: each run, and each act
        # through MCP. The twin compares their times with the done move.
        work = [r.created_at for r in c.execute(text(
            "SELECT created_at FROM pm_agent_runs WHERE task_id = CAST(:t AS uuid)"
            " UNION ALL SELECT created_at FROM pm_activities"
            "  WHERE task_id = CAST(:t AS uuid) AND meta->>'via' LIKE 'mcp:%'"),
            {"t": tid})]
    return ana.classify_finish(moves, agent_work=work, until=until)


def _python_holder(w: Any, tid: str) -> str | None:
    from gateway.routes.projects import analytics as ana
    from gateway.routes.projects.filters import AGENT_RUN_OPEN_STATUSES, holder_free

    with w.p.admin_engine.begin() as c:
        assignees = [r.assignee for r in c.execute(text(
            "SELECT assignee FROM pm_task_assignees WHERE task_id = CAST(:t AS uuid)"),
            {"t": tid})]
        runs = c.execute(text(
            "SELECT status, EXTRACT(EPOCH FROM (now() - last_activity_at)) AS idle_s"
            "  FROM pm_agent_runs WHERE task_id = CAST(:t AS uuid)"
            "   AND status = ANY(CAST(:open AS text[]))"),
            {"t": tid, "open": list(AGENT_RUN_OPEN_STATUSES)}).fetchall()
    holds = any(not holder_free(r.status, float(r.idle_s), False) for r in runs)
    return ana.classify_holder(assignees, run_holds=holds)


class TestDoneLabels:
    async def test_d1_each_done_task_carries_its_label(self, w):
        from gateway.routes.projects import analytics as ana

        t = _seed_done(w)
        with w.p.admin_engine.begin() as c:
            rows = c.execute(text(ana.finish_labels_sql(
                "t.project_id = CAST(:pid AS uuid)")), _params(w)).fetchall()
        labels = {str(r.task_id): r.label for r in rows}
        counted = {str(r.task_id): r.finished_at for r in rows}
        assert _by_name(t, labels) == DONE
        assert {
            name: _python_finish(w, tid, counted[tid]) for name, tid in t.items()
        } == DONE

    async def test_d2_the_routes_count_the_labels(self, w):
        _seed_done(w)
        want = {"agent_finished": 4, "agent_assisted": 4, "person": 3}
        pid = w.root["id"]
        fin = await _get(w, w.a, f"/projects/analytics/finished?project_id={pid}")
        assert fin["actor_split"] == want
        assert sum(fin["actor_split"].values()) == fin["total_completed"] == len(DONE)
        thr = await _get(w, w.a, f"/projects/analytics/throughput?project_id={pid}")
        assert thr["actor_split"] == want
        assert sum(thr["actor_split"].values()) == thr["summary"]["completed"]


class TestOpenHolders:
    async def test_h1_each_open_task_carries_its_holder(self, w):
        from gateway.routes.projects import analytics as ana

        t = _seed_open(w)
        open_where = (
            "t.project_id = CAST(:pid AS uuid)"
            " AND s.category <> ALL(CAST(:closed AS text[]))"
        )
        with w.p.admin_engine.begin() as c:
            rows = c.execute(text(ana.holder_labels_sql(open_where)), _params(w)).fetchall()
        labels = {str(r.task_id): r.label for r in rows}
        assert len(labels) == len(OPEN), "a done task is not open work"
        assert _by_name(t, labels) == OPEN
        assert {name: _python_holder(w, tid) for name, tid in t.items()} == OPEN

    async def test_h2_the_load_route_counts_the_holders(self, w):
        _seed_open(w)
        body = await _get(w, w.a, f"/projects/analytics/load?project_id={w.root['id']}")
        assert body["holder_split"] == {"agent": 2, "person": 1, "none": 2}
        assert sum(body["holder_split"].values()) == body["total_tasks"]


class TestVisibility:
    async def test_v1_a_member_reads_only_what_they_see(self, w):
        """M is no admin and holds no grant, so M sees the task M holds and
        nothing else. B is in the other organization and sees nothing."""
        done = _task(w, "done")
        _assign(w, done, M_EMAIL)
        _move(w, done, A_EMAIL, via=b.VIA)
        _seed_done(w)
        held = _task(w)
        _assign(w, held, M_EMAIL)
        _seed_open(w)
        fin = await _get(w, w.m, "/projects/analytics/finished")
        assert fin["actor_split"] == {"agent_finished": 1, "agent_assisted": 0, "person": 0}
        load = await _get(w, w.m, "/projects/analytics/load")
        assert load["holder_split"] == {"agent": 0, "person": 1, "none": 0}
        zero_fin = {"agent_finished": 0, "agent_assisted": 0, "person": 0}
        assert (await _get(w, w.b, "/projects/analytics/finished"))["actor_split"] == zero_fin
        zero_load = {"agent": 0, "person": 0, "none": 0}
        assert (await _get(w, w.b, "/projects/analytics/load"))["holder_split"] == zero_load


def test_the_seed_tables_cover_every_label() -> None:
    """Each label of the rule has a seeded case, so a dropped arm shows."""
    from gateway.routes.projects import analytics as ana

    assert set(DONE.values()) == set(ana.FINISH_LABELS)
    assert {v or ana.HOLDER_NONE for v in OPEN.values()} == set(ana.HOLDER_KEYS)
