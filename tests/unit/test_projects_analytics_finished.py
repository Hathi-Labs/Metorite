"""WS-27bk §9.12.7(d) — what did we finish, against a real Postgres.

Spec: ``project-docs/specs/project_management_app.md`` §9.12.7.

(c) and (d) are one question asked along two axes. (c) groups completions by
WEEK, (d) groups the same completions by PROJECT. So the claim that matters
most here is not about either one alone:

⚠️ **the two must never disagree.** A dashboard that prints "132 finished" in
the throughput panel and a finished-by-project list adding to 130 is a
dashboard nobody trusts again, and both numbers look right on their own. They
share `cycle_cte_sql` for exactly this reason, and
:class:`TestTheTwoAxesAgree` is the fence that keeps them sharing it.

The rest, each a place where an obvious query is wrong and still plausible:

* **`cancelled` has its own column.** A report that folded it into
  `completed` would congratulate a team for abandoning work.
* **the project is the task's OWN.** Rolling a subproject's work up to its
  parent hides which team did it, which is what the list is for.
* **the window is a CLOSED period when the report asks for one.** A rolling
  window with days left in it re-reports the same tasks on the next send,
  with a different number each time.
* **`period_start` and `period_end` come from the SERVER.** A client that
  re-derives them from its own clock disagrees across a timezone, and two
  copies of one report then name different weeks.
"""
from __future__ import annotations

import os
import uuid

import pytest

pytest.importorskip("sqlalchemy")

from sqlalchemy import create_engine, text

from tests.unit._tenant_ladder import apply_ladder

_TENANT_URL = os.environ.get("TENANT_LADDER_DATABASE_URL", "").strip()

pytestmark = pytest.mark.skipif(
    not _TENANT_URL,
    reason=(
        "TENANT_LADDER_DATABASE_URL unset — R8 requires a REAL Postgres. A "
        "skip here is not a pass; CI must set it."
    ),
)

_MONDAY = "(date_trunc('week', now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC')"


def _at(*, weeks_ago: int = 0, hours: int = 1) -> str:
    return (
        f"({_MONDAY} - make_interval(weeks => {weeks_ago})"
        f" + make_interval(hours => {hours}))"
    )


@pytest.fixture(scope="module")
def db():
    eng = create_engine(_TENANT_URL, future=True)
    with eng.begin() as conn:
        apply_ladder(conn)
    yield eng
    eng.dispose()


@pytest.fixture
def tree(db):
    """A parent project and a subproject, each with its own lanes.

    Two nodes rather than one, because the ruling under test is which project
    a completion is filed under. A single-project fixture cannot tell the
    task's own project from its root.
    """
    made: dict[str, str] = {}
    with db.begin() as c:
        org = str(c.execute(
            text("SELECT id FROM organization ORDER BY created_at LIMIT 1")
        ).scalar_one())
        made["org"] = org
        for key, parent in (("parent", None), ("child", "parent")):
            pid = str(c.execute(
                text(
                    "INSERT INTO pm_projects (name, status, source, created_by,"
                    " organization_id, timezone, parent_project_id,"
                    " owns_statuses)"
                    " VALUES (:n,'active','manual','fin@example.test',"
                    " CAST(:o AS uuid),'Asia/Kolkata',"
                    " CAST(:par AS uuid), true) RETURNING id"
                ),
                {
                    "n": f"fin-{key}-{uuid.uuid4().hex[:6]}",
                    "o": org,
                    "par": made[parent] if parent else None,
                },
            ).scalar_one())
            made[key] = pid
            made[f"{key}_name"] = str(c.execute(
                text("SELECT name FROM pm_projects WHERE id = CAST(:p AS uuid)"),
                {"p": pid},
            ).scalar_one())
            for name, cat, pos in (
                ("To do", "todo", 0),
                ("In progress", "in_progress", 1),
                ("Done", "done", 2),
                ("Cancelled", "cancelled", 3),
            ):
                made[f"{key}_{cat}"] = str(c.execute(
                    text(
                        "INSERT INTO pm_task_statuses (project_id,name,color,"
                        " position,category) VALUES (CAST(:p AS uuid),:n,"
                        " 'gray',:pos,:cat) RETURNING id"
                    ),
                    {"p": pid, "n": name, "pos": pos, "cat": cat},
                ).scalar_one())
    yield made
    ids = [made["parent"], made["child"]]
    with db.begin() as c:
        c.execute(
            text(
                "DELETE FROM pm_activities WHERE task_id IN (SELECT id FROM"
                " pm_tasks WHERE project_id = ANY(CAST(:p AS uuid[])))"
            ),
            {"p": ids},
        )
        c.execute(
            text("DELETE FROM pm_tasks WHERE project_id = ANY(CAST(:p AS uuid[]))"),
            {"p": ids},
        )
        c.execute(
            text(
                "DELETE FROM pm_task_statuses"
                " WHERE project_id = ANY(CAST(:p AS uuid[]))"
            ),
            {"p": ids},
        )
        # Child first — the parent is its foreign key.
        for pid in (made["child"], made["parent"]):
            c.execute(
                text("DELETE FROM pm_projects WHERE id = CAST(:p AS uuid)"),
                {"p": pid},
            )


def _finish(conn, tree, node, *, title, to="done", at, frm="in_progress",
            started=None):
    """A task in `node`, moved to `to` at `at`. Optionally started first."""
    tid = str(conn.execute(
        text(
            "INSERT INTO pm_tasks (title, project_id, root_project_id,"
            " status_id, created_by, organization_id, task_number)"
            " SELECT :t, CAST(:p AS uuid), CAST(:root AS uuid),"
            " CAST(:s AS uuid), 'fin@example.test', CAST(:o AS uuid),"
            " COALESCE(MAX(task_number),0)+1 FROM pm_tasks"
            " WHERE root_project_id = CAST(:root AS uuid) RETURNING id"
        ),
        {
            "t": title, "p": tree[node], "root": tree["parent"],
            "s": tree[f"{node}_{to}"], "o": tree["org"],
        },
    ).scalar_one())

    def act(f, t, when):
        conn.execute(
            text(
                "INSERT INTO pm_activities (task_id, organization_id, type,"
                " created_by, body, meta, created_at) VALUES"
                f" (CAST(:i AS uuid), CAST(:o AS uuid), 'status_change',"
                f" 'fin@example.test', :b, CAST(:m AS jsonb), {when})"
            ),
            {
                "i": tid, "o": tree["org"], "b": f"{f} -> {t}",
                "m": f'{{"from_category": "{f}", "to_category": "{t}"}}',
            },
        )

    if started:
        act("todo", "in_progress", started)
    act(frm, to, at)
    return tid


def _run(db, tree, *, weeks=12, skip_current_week=False, node=None):
    """Execute the ROUTE MODULE'S OWN queries. Imported, never transcribed."""
    from gateway.routes.projects.analytics import (
        cycle_summary_sql,
        finished_period_sql,
        finished_sql,
        weekly_sql,
    )

    where = (
        "t.project_id = CAST(:pid AS uuid)" if node
        else (
            "t.project_id IN (WITH RECURSIVE sub AS ("
            " SELECT id FROM pm_projects WHERE id = CAST(:pid AS uuid)"
            " UNION ALL SELECT p.id FROM pm_projects p JOIN sub s"
            " ON p.parent_project_id = s.id) SELECT id FROM sub)"
        )
    )
    params = {
        "pid": tree[node] if node else tree["parent"],
        "weeks": weeks,
        "closing": ["cancelled", "done"],
        "done_cat": "done",
        "started_cat": "in_progress",
    }
    kw = {"skip_current_week": skip_current_week}
    with db.connect() as c:
        rows = c.execute(text(finished_sql(where, **kw)), params).fetchall()
        window = c.execute(
            text(finished_period_sql(**kw)), {"weeks": weeks}
        ).one()
        totals = c.execute(text(cycle_summary_sql(where, **kw)), params).one()
        series = c.execute(text(weekly_sql(where)), params).fetchall()
    return {r.name: r for r in rows}, window, totals, series


class TestTheTwoAxesAgree:
    """⚠️ The claim that matters. (c) by week and (d) by project, one set."""

    async def test_the_per_project_rows_add_to_the_weekly_total(self, db, tree):
        with db.begin() as c:
            _finish(c, tree, "parent", title="p1", at=_at(weeks_ago=2, hours=2))
            _finish(c, tree, "parent", title="p2", at=_at(weeks_ago=1, hours=2))
            _finish(c, tree, "child", title="c1", at=_at(weeks_ago=1, hours=3))
            _finish(c, tree, "child", title="c2", at=_at(hours=2))
        rows, _, totals, series = _run(db, tree)
        by_project = sum(r.completed for r in rows.values())
        by_week = sum(int(w.completed or 0) for w in series)
        assert by_project == by_week == int(totals.completed) == 4

    async def test_a_reopened_task_counts_ONCE_on_BOTH_axes(self, db, tree):
        """The `DISTINCT ON` dedup must reach (d), not just (c).

        A second definition of "finished" would have to re-implement it, and
        the two would drift on exactly this case.
        """
        with db.begin() as c:
            tid = _finish(c, tree, "parent", title="bounced",
                          at=_at(weeks_ago=3, hours=2))
            for f, t, when in (
                ("done", "in_progress", _at(weeks_ago=2, hours=1)),
                ("in_progress", "done", _at(weeks_ago=1, hours=1)),
            ):
                c.execute(
                    text(
                        "INSERT INTO pm_activities (task_id, organization_id,"
                        " type, created_by, body, meta, created_at) VALUES"
                        f" (CAST(:i AS uuid), CAST(:o AS uuid),"
                        f" 'status_change','fin@example.test', :b,"
                        f" CAST(:m AS jsonb), {when})"
                    ),
                    {
                        "i": tid, "o": tree["org"], "b": f"{f} -> {t}",
                        "m": f'{{"from_category": "{f}", "to_category": "{t}"}}',
                    },
                )
        rows, _, totals, series = _run(db, tree)
        assert sum(r.completed for r in rows.values()) == 1
        assert sum(int(w.completed or 0) for w in series) == 1
        assert int(totals.completed) == 1


class TestTheProjectIsTheTasksOwn:
    """Rolling a subproject up to its parent hides which team did the work."""

    async def test_a_subproject_gets_its_OWN_row(self, db, tree):
        with db.begin() as c:
            _finish(c, tree, "parent", title="up here", at=_at(hours=2))
            _finish(c, tree, "child", title="down there", at=_at(hours=3))
        rows, _, _, _ = _run(db, tree)
        assert set(rows) == {tree["parent_name"], tree["child_name"]}
        assert rows[tree["parent_name"]].completed == 1
        assert rows[tree["child_name"]].completed == 1

    async def test_a_node_scoped_read_sees_only_that_node(self, db, tree):
        with db.begin() as c:
            _finish(c, tree, "parent", title="up here", at=_at(hours=2))
            _finish(c, tree, "child", title="down there", at=_at(hours=3))
        rows, _, totals, _ = _run(db, tree, node="child")
        assert set(rows) == {tree["child_name"]}
        assert int(totals.completed) == 1


class TestCancelledKeepsItsOwnColumn:
    """A report that added them would congratulate abandoned work."""

    async def test_it_never_reaches_completed(self, db, tree):
        with db.begin() as c:
            _finish(c, tree, "parent", title="shipped", at=_at(hours=2))
            _finish(c, tree, "parent", title="dropped", to="cancelled",
                    frm="todo", at=_at(hours=3))
        rows, _, totals, _ = _run(db, tree)
        row = rows[tree["parent_name"]]
        assert row.completed == 1
        assert row.cancelled == 1
        assert int(totals.completed) == 1
        assert int(totals.cancelled) == 1

    async def test_a_project_that_ONLY_cancelled_still_appears(self, db, tree):
        """⚠️ The `HAVING` arm. Dropping it would hide a team that shipped
        nothing and cancelled nine, which is the finding."""
        with db.begin() as c:
            _finish(c, tree, "child", title="dropped", to="cancelled",
                    frm="todo", at=_at(hours=3))
        rows, _, _, _ = _run(db, tree)
        assert tree["child_name"] in rows
        assert rows[tree["child_name"]].completed == 0
        assert rows[tree["child_name"]].cancelled == 1


class TestTheReportGetsAClosedPeriod:
    """⚠️ §9.12.7(d) says this slice decides §9.12.8's shape. This is it."""

    async def test_skip_current_week_EXCLUDES_this_weeks_work(self, db, tree):
        """A weekly report describes a week that ended.

        Without this the Monday send covers a window with days still left in
        it, and the next send re-reports the same tasks with a different
        number beside them.
        """
        with db.begin() as c:
            _finish(c, tree, "parent", title="this week", at=_at(hours=2))
            _finish(c, tree, "parent", title="last week",
                    at=_at(weeks_ago=1, hours=2))
        live, _, live_totals, _ = _run(db, tree)
        report, _, report_totals, _ = _run(db, tree, skip_current_week=True)
        assert int(live_totals.completed) == 2
        assert int(report_totals.completed) == 1
        assert live[tree["parent_name"]].completed == 2
        assert report[tree["parent_name"]].completed == 1

    async def test_one_whole_week_is_exactly_last_week(self, db, tree):
        """`weeks=1` + the flag is the weekly report's own window."""
        with db.begin() as c:
            _finish(c, tree, "parent", title="this week", at=_at(hours=2))
            _finish(c, tree, "parent", title="last week",
                    at=_at(weeks_ago=1, hours=2))
            _finish(c, tree, "parent", title="two weeks ago",
                    at=_at(weeks_ago=2, hours=2))
        _, window, totals, _ = _run(
            db, tree, weeks=1, skip_current_week=True,
        )
        assert int(totals.completed) == 1
        # Seven days, and both ends land on the days a person would name.
        assert (window.period_end - window.period_start).days == 6

    async def test_the_window_comes_from_the_SERVER(self, db, tree):
        """A client that derives it from its own clock names a different week.

        The dates are reported, not implied, so two copies of one report
        cannot disagree about which days they covered.
        """
        _, window, _, _ = _run(db, tree, weeks=4)
        assert (window.period_end - window.period_start).days == 27
        # ISO Monday. `weekday()` is 0 for Monday.
        assert window.period_start.weekday() == 0
        assert window.period_end.weekday() == 6


class TestOverdueIsByProject:
    """⚠️ The query that was silently dead until 2026-09-17.

    `stuck` answered `overdue` as a bare integer while the panel consuming it
    declared a LIST. `number.length` is undefined and `undefined > 0` is
    false, so the Overdue section rendered nothing and threw nothing — the
    quietest possible failure. §9.12.7(a) asks for "Overdue by project" in
    those words, so the server was the side that had drifted.

    It runs with DATA here for that reason. The old read was exercised by no
    test that put a late task in front of it.
    """

    async def test_it_names_the_project_the_late_work_is_in(self, db, tree):
        from gateway.routes.projects.analytics import overdue_by_project_sql

        with db.begin() as c:
            for n in range(3):
                tid = _finish(c, tree, "child", title=f"late-{n}", to="todo",
                              at=_at(hours=1), frm="todo")
                c.execute(
                    text(
                        "UPDATE pm_tasks SET due_at = now() - interval '5 days'"
                        " WHERE id = CAST(:i AS uuid)"
                    ),
                    {"i": tid},
                )
            tid = _finish(c, tree, "parent", title="late-parent", to="todo",
                          at=_at(hours=1), frm="todo")
            c.execute(
                text(
                    "UPDATE pm_tasks SET due_at = now() - interval '1 day'"
                    " WHERE id = CAST(:i AS uuid)"
                ),
                {"i": tid},
            )
        where = (
            "t.project_id IN (WITH RECURSIVE sub AS ("
            " SELECT id FROM pm_projects WHERE id = CAST(:pid AS uuid)"
            " UNION ALL SELECT p.id FROM pm_projects p JOIN sub s"
            " ON p.parent_project_id = s.id) SELECT id FROM sub)"
            " AND t.archived_at IS NULL"
            " AND s.category <> ALL(CAST(:closed AS text[]))"
        )
        with db.connect() as c:
            rows = c.execute(
                text(overdue_by_project_sql(where)),
                {"pid": tree["parent"], "closed": ["cancelled", "done"]},
            ).fetchall()
        by_name = {r.name: int(r.overdue) for r in rows}
        assert by_name[tree["child_name"]] == 3
        assert by_name[tree["parent_name"]] == 1
        # ⚠️ Worst first. The row a reader acts on is the top one.
        assert rows[0].name == tree["child_name"]

    async def test_a_FINISHED_overdue_task_is_not_late(self, db, tree):
        """The lesson WS-27k's `overdue` already learned, re-fenced here."""
        from gateway.routes.projects.analytics import overdue_by_project_sql

        with db.begin() as c:
            tid = _finish(c, tree, "parent", title="late but done",
                          at=_at(hours=2))
            c.execute(
                text(
                    "UPDATE pm_tasks SET due_at = now() - interval '9 days'"
                    " WHERE id = CAST(:i AS uuid)"
                ),
                {"i": tid},
            )
        where = (
            "t.project_id = CAST(:pid AS uuid)"
            " AND t.archived_at IS NULL"
            " AND s.category <> ALL(CAST(:closed AS text[]))"
        )
        with db.connect() as c:
            rows = c.execute(
                text(overdue_by_project_sql(where)),
                {"pid": tree["parent"], "closed": ["cancelled", "done"]},
            ).fetchall()
        assert rows == []
