"""WS-27bk §9.12.7(b) — who is overloaded, against a real Postgres.

Spec: ``project-docs/specs/project_management_app.md`` §9.12.7.

⚠️ **R8, and the spec asks for it by name.** §9.12.7's Done-when says *"Each is
covered against a real database (R8)"*. Slice (a) shipped with a STRUCTURAL
suite instead — `test_projects_analytics.py` pins the shape and its header
records that somebody ran the SQL by hand once. That is weaker than it reads:
a hand-run leaves nothing behind that fails later.

So this suite executes **the route module's own SQL**, imported rather than
copied. `load_sql` is a function for that reason. An edit to it is an edit to
what runs here, which is the property a transcribed query loses on its first
divergence.

The claims, each one a place where a plausible query is wrong and still looks
right:

* **unassigned work is a ROW.** An inner join answers a different question —
  "who is overloaded, among tasks somebody already took" — and hides the
  backlog nobody owns. It is usually the largest bar.
* **the buckets are DISJOINT and undated work is `later`.** Overlapping
  buckets make three numbers that do not add to the fourth, while each one
  is individually explicable.
* **a task with two assignees counts for BOTH**, so the per-person numbers
  sum past the total — which is why the total is counted over tasks.
* **a closed task is not load.** Counting finished work keeps somebody
  permanently overloaded by work they completed.
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


@pytest.fixture(scope="module")
def db():
    eng = create_engine(_TENANT_URL, future=True)
    with eng.begin() as conn:
        apply_ladder(conn)
    yield eng
    eng.dispose()


@pytest.fixture
def scope(db):
    """One project with a full lane set, torn down after each test.

    Function-scoped and rolled back by hand rather than shared: every test here
    counts rows, so one test's leftovers are another's wrong answer.
    """
    made: dict[str, str] = {}
    with db.begin() as c:
        org = str(c.execute(
            text("SELECT id FROM organization ORDER BY created_at LIMIT 1")
        ).scalar_one())
        made["org"] = org
        made["project"] = str(c.execute(
            text(
                "INSERT INTO pm_projects (name, status, source, created_by,"
                " organization_id, timezone, parent_project_id, owns_statuses)"
                " VALUES (:n,'active','manual','load@example.test',"
                " CAST(:o AS uuid),'Asia/Kolkata',NULL,true) RETURNING id"
            ),
            {"n": f"load-{uuid.uuid4().hex[:6]}", "o": org},
        ).scalar_one())
        for name, cat, pos in (
            ("To do", "todo", 0),
            ("In progress", "in_progress", 1),
            ("Done", "done", 2),
        ):
            made[cat] = str(c.execute(
                text(
                    "INSERT INTO pm_task_statuses (project_id,name,color,"
                    " position,category) VALUES (CAST(:p AS uuid),:n,'gray',"
                    " :pos,:cat) RETURNING id"
                ),
                {"p": made["project"], "n": name, "pos": pos, "cat": cat},
            ).scalar_one())
    yield made
    with db.begin() as c:
        c.execute(
            text("DELETE FROM pm_tasks WHERE project_id = CAST(:p AS uuid)"),
            {"p": made["project"]},
        )
        c.execute(
            text(
                "DELETE FROM pm_task_statuses WHERE project_id = CAST(:p AS uuid)"
            ),
            {"p": made["project"]},
        )
        c.execute(
            text("DELETE FROM pm_projects WHERE id = CAST(:p AS uuid)"),
            {"p": made["project"]},
        )


def _task(conn, scope, *, title, category="todo", due=None, who=()):
    tid = str(conn.execute(
        text(
            "INSERT INTO pm_tasks (title, project_id, root_project_id,"
            " status_id, created_by, organization_id, task_number, due_at)"
            " SELECT :t, CAST(:p AS uuid), CAST(:p AS uuid), CAST(:s AS uuid),"
            " 'load@example.test', CAST(:o AS uuid),"
            " COALESCE(MAX(task_number),0)+1, CAST(:due AS timestamptz)"
            " FROM pm_tasks WHERE root_project_id = CAST(:p AS uuid)"
            " RETURNING id"
        ),
        {
            "t": title, "p": scope["project"], "s": scope[category],
            "o": scope["org"], "due": due,
        },
    ).scalar_one())
    for person in who:
        conn.execute(
            text(
                "INSERT INTO pm_task_assignees (task_id, assignee, assigned_by)"
                " VALUES (CAST(:t AS uuid), :a, 'load@example.test')"
            ),
            {"t": tid, "a": person},
        )
    return tid


def _run(db, scope):
    """Execute the ROUTE MODULE'S OWN query. Imported, never transcribed."""
    from gateway.routes.projects.analytics import load_sql, total_open_sql

    # The route builds this from the caller's scope and grants. Here it is the
    # project plus the open-only rule, which is the part these claims are about.
    where = (
        "t.project_id = CAST(:pid AS uuid)"
        " AND t.archived_at IS NULL"
        " AND s.category <> ALL(CAST(:closed AS text[]))"
    )
    params = {"pid": scope["project"], "closed": ["done", "cancelled"]}
    with db.connect() as c:
        rows = c.execute(text(load_sql(where)), params).fetchall()
        total = c.execute(text(total_open_sql(where)), params).scalar()
    return {r.who: r for r in rows}, int(total or 0)


class TestUnassignedWorkIsARow:
    """⚠️ The finding, not the gap."""

    async def test_work_nobody_owns_appears_under_the_empty_key(self, db, scope):
        with db.begin() as c:
            _task(c, scope, title="nobody wants this")
        people, total = _run(db, scope)
        assert "" in people, "unassigned work vanished — an inner join would"
        assert people[""].open_tasks == 1
        assert total == 1

    async def test_it_sits_BESIDE_the_named_people(self, db, scope):
        with db.begin() as c:
            _task(c, scope, title="orphan")
            _task(c, scope, title="owned", who=("ana@example.test",))
        people, _ = _run(db, scope)
        assert set(people) == {"", "ana@example.test"}


class TestTheBucketsAreDisjoint:
    """Three numbers that must add to the fourth."""

    async def test_overdue_this_week_and_later_sum_to_the_row_total(
        self, db, scope,
    ):
        with db.begin() as c:
            _task(c, scope, title="late", due="2020-01-01T00:00:00Z",
                  who=("ana@example.test",))
            # Relative to `now()`, so the test does not rot: a literal date
            # two days out is "soon" today and overdue next week.
            _task(c, scope, title="soon", who=("ana@example.test",))
            _task(c, scope, title="far", who=("ana@example.test",))
            c.execute(text(
                "UPDATE pm_tasks SET due_at = now() + interval '2 days'"
                " WHERE title = 'soon'"
            ))
            c.execute(text(
                "UPDATE pm_tasks SET due_at = now() + interval '40 days'"
                " WHERE title = 'far'"
            ))
            _task(c, scope, title="undated", who=("ana@example.test",))
        people, _ = _run(db, scope)
        row = people["ana@example.test"]
        assert row.overdue == 1
        assert row.due_next_7d == 1
        # `far` and `undated` both land here — see the next test for why.
        assert row.later == 2
        assert row.overdue + row.due_next_7d + row.later == row.open_tasks

    async def test_undated_work_lands_in_LATER_rather_than_nowhere(
        self, db, scope,
    ):
        """⚠️ Undated work is real work.

        A `due_at IS NOT NULL` filter on every bucket would drop it from all
        three, and the three would then not add to `open_tasks` — a chart that
        silently under-reports the largest pile on most boards.
        """
        with db.begin() as c:
            _task(c, scope, title="someday", who=("ana@example.test",))
        people, _ = _run(db, scope)
        row = people["ana@example.test"]
        assert row.later == 1
        assert row.open_tasks == 1


class TestTwoAssigneesCountTwice:
    """It is genuinely on both plates, so the totals cannot be added."""

    async def test_each_person_carries_the_whole_task(self, db, scope):
        with db.begin() as c:
            _task(c, scope, title="shared",
                  who=("ana@example.test", "bo@example.test"))
        people, total = _run(db, scope)
        assert people["ana@example.test"].open_tasks == 1
        assert people["bo@example.test"].open_tasks == 1
        # ⚠️ And the total is ONE. Summing the rows would say two.
        assert total == 1
        assert sum(r.open_tasks for r in people.values()) == 2


class TestAClosedTaskIsNotLoad:
    """Otherwise finishing work never reduces anybody's bar."""

    async def test_done_work_leaves_the_count(self, db, scope):
        with db.begin() as c:
            _task(c, scope, title="finished", category="done",
                  who=("ana@example.test",))
            _task(c, scope, title="open", who=("ana@example.test",))
        people, total = _run(db, scope)
        assert people["ana@example.test"].open_tasks == 1
        assert total == 1

    async def test_an_overdue_DONE_task_is_not_overdue(self, db, scope):
        """The lesson WS-27k's `overdue` already learned."""
        with db.begin() as c:
            _task(c, scope, title="late but finished", category="done",
                  due="2020-01-01T00:00:00Z", who=("ana@example.test",))
        people, total = _run(db, scope)
        assert people == {} or people.get("ana@example.test") is None
        assert total == 0
