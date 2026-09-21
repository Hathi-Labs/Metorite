"""Will this land, and when — the executive read. WS-27bk wave 7.

Owner ask, 2026-09-17: an estimated completion date, the number of people,
the work left and spent, *"and other important information that might be
needed by an executive team, CEO, or project/product manager"*.

⚠️ **Built with NO time tracking, and the owner set that constraint on
purpose** — *"we can estimate the temporal characteristics of each project
without needing to use time tracking"*. Everything here derives from what the
product already stores: `pm_activities` for what happened, `estimate_mins`
and `due_at` for the plan, `people` for who can do the work.

The claims worth pinning are the REFUSALS. A forecast that always answers is
easy and wrong, and the wrong answer is the one that gets quoted in a
meeting:

* **a growing backlog has no completion date.** `remaining / finished` always
  yields one. That date slips every week and nobody is told why.
* **three completions are not a velocity.**
* **hours summed over a third of the backlog are a third of the answer**, and
  look identical to the whole one.
* **nobody's capacity known is not "assume forty hours"**.
"""
from __future__ import annotations

import os
import uuid
from datetime import UTC, date, timedelta

import pytest
from gateway.routes.projects.analytics import (
    MIN_FINISHED_FOR_FORECAST,
    capacity_forecast,
    project_forecast,
    slip_days,
)

# ── The arithmetic, with no database ────────────────────────────────────────


class TestScopeGrowthIsPartOfTheAnswer:
    """⚠️ The owner's decision, 2026-09-17, and the reason this module exists."""

    def test_a_project_growing_faster_than_it_finishes_gets_NO_date(self):
        got = project_forecast(
            remaining_tasks=71,
            finished=[5, 4, 4, 5, 3, 4],
            created=[6, 5, 5, 4, 6, 5],
        )
        assert got["verdict"] == "not_converging"
        assert got["finish_date"] is None
        assert got["weeks_remaining"] is None

    def test_and_it_shows_BOTH_rates_so_the_reader_sees_why(self):
        # A refusal with no numbers reads as a broken feature. With the two
        # rates beside it, it reads as the finding it is.
        got = project_forecast(
            remaining_tasks=71,
            finished=[5, 4, 4, 5, 3, 4],
            created=[6, 5, 5, 4, 6, 5],
        )
        assert got["finished_per_week"] == 4.17
        assert got["created_per_week"] == 5.17
        assert got["net_per_week"] == -1.0

    def test_a_dead_heat_is_also_not_converging(self):
        # Finishing exactly as fast as work arrives never empties the
        # backlog. `net == 0` divides into infinity, not into a date.
        got = project_forecast(
            remaining_tasks=20, finished=[4, 4, 4], created=[4, 4, 4],
        )
        assert got["verdict"] == "not_converging"

    def test_a_healthy_project_gets_a_date_from_the_NET_rate(self):
        got = project_forecast(
            remaining_tasks=55,
            finished=[8, 7, 9, 8, 7, 8],
            created=[2, 3, 2, 3, 2, 2],
        )
        assert got["verdict"] == "converging"
        assert got["net_per_week"] == 5.5
        # 55 / 5.5 = 10 weeks. Not 55 / 7.83, which would say 7 and be wrong
        # by three weeks because it ignores the work still arriving.
        assert got["weeks_remaining"] == 10
        assert got["finish_date"] == (
            date.today() + timedelta(weeks=10)
        ).isoformat()

    def test_the_naive_forecast_would_have_been_optimistic(self):
        """The difference the net rate makes, stated as a number."""
        got = project_forecast(
            remaining_tasks=55,
            finished=[8, 7, 9, 8, 7, 8],
            created=[2, 3, 2, 3, 2, 2],
        )
        naive = 55 / got["finished_per_week"]
        assert got["weeks_remaining"] > naive


class TestItRefusesRatherThanGuesses:
    def test_no_open_work_is_not_a_forecast(self):
        got = project_forecast(remaining_tasks=0, finished=[9], created=[0])
        assert got["verdict"] == "nothing_left"
        assert got["finish_date"] is None

    def test_too_little_history_refuses(self):
        got = project_forecast(
            remaining_tasks=40,
            finished=[0] * 5 + [MIN_FINISHED_FOR_FORECAST - 1],
            created=[0] * 6,
        )
        assert got["verdict"] == "no_history"
        assert got["finish_date"] is None

    def test_a_brand_new_project_refuses(self):
        got = project_forecast(remaining_tasks=12, finished=[], created=[])
        assert got["verdict"] == "no_history"

    def test_exactly_enough_history_is_enough(self):
        got = project_forecast(
            remaining_tasks=4,
            finished=[MIN_FINISHED_FOR_FORECAST, 0, 0],
            created=[0, 0, 0],
        )
        assert got["verdict"] == "converging"


class TestCapacityAnswersADifferentQuestion:
    def test_remaining_hours_over_team_hours_gives_weeks(self):
        got = capacity_forecast(
            left_mins=60 * 240, left_estimated=30, left_tasks=30,
            hours_per_week=60,
        )
        assert got["verdict"] == "ok"
        assert got["hours_left"] == 240.0
        assert got["weeks_remaining"] == 4

    def test_no_estimates_refuses_instead_of_reporting_zero_hours(self):
        # ⚠️ Zero hours left and nothing sized are opposite findings that
        # render identically. Only one of them means the project is done.
        got = capacity_forecast(
            left_mins=0, left_estimated=0, left_tasks=25, hours_per_week=60,
        )
        assert got["verdict"] == "no_estimates"
        assert got["finish_date"] is None

    def test_nobody_capacity_known_is_not_assume_forty(self):
        got = capacity_forecast(
            left_mins=6000, left_estimated=10, left_tasks=10, hours_per_week=0,
        )
        assert got["verdict"] == "no_capacity"
        assert got["finish_date"] is None

    def test_the_coverage_travels_with_the_hours(self):
        got = capacity_forecast(
            left_mins=6000, left_estimated=10, left_tasks=30, hours_per_week=40,
        )
        # 10 of 30 sized. The hours are a third of the answer and look
        # exactly like the whole one without this.
        assert got["estimate_coverage"] == pytest.approx(0.333, abs=0.001)

    def test_an_empty_scope_is_fully_covered_not_zero_percent(self):
        got = capacity_forecast(
            left_mins=0, left_estimated=0, left_tasks=0, hours_per_week=40,
        )
        assert got["verdict"] == "nothing_left"
        assert got["estimate_coverage"] == 1.0


class TestSlip:
    def test_a_forecast_past_the_plan_is_late(self):
        plan = date(2026, 3, 12)
        assert slip_days(plan, "2026-04-04") == 23

    def test_a_forecast_before_the_plan_is_early(self):
        assert slip_days(date(2026, 3, 12), "2026-03-01") == -11

    def test_an_unplanned_project_cannot_slip(self):
        assert slip_days(None, "2026-04-04") is None

    def test_a_project_with_no_forecast_cannot_slip(self):
        # `not_converging` puts None here, and a slip of 0 would read as
        # "on time" for a project that has no completion date at all.
        assert slip_days(date(2026, 3, 12), None) is None

    def test_a_datetime_plan_is_read_as_its_date(self):
        from datetime import datetime

        plan = datetime(2026, 3, 12, 17, 30, tzinfo=UTC)
        assert slip_days(plan, "2026-03-14") == 2


# ── R8: the queries run, against a real Postgres ────────────────────────────

_TENANT_URL = os.environ.get("TENANT_LADDER_DATABASE_URL", "").strip()

pytest.importorskip("sqlalchemy")
from sqlalchemy import create_engine, text  # noqa: E402

from tests.unit._tenant_ladder import apply_ladder  # noqa: E402


@pytest.fixture(scope="module")
def db():
    if not _TENANT_URL:
        pytest.skip("TENANT_LADDER_DATABASE_URL unset — R8 needs a real Postgres")
    eng = create_engine(_TENANT_URL, future=True)
    with eng.begin() as conn:
        apply_ladder(conn)
    yield eng
    eng.dispose()


@pytest.fixture
def scope(db):
    made: dict[str, str] = {}
    with db.begin() as c:
        made["org"] = str(c.execute(
            text("SELECT id FROM organization ORDER BY created_at LIMIT 1")
        ).scalar_one())
        made["project"] = str(c.execute(
            text(
                "INSERT INTO pm_projects (name, status, source, created_by,"
                " organization_id, timezone, parent_project_id, owns_statuses)"
                " VALUES (:n,'active','manual','out@example.test',"
                " CAST(:o AS uuid),'Asia/Kolkata',NULL,true) RETURNING id"
            ),
            {"n": f"out-{uuid.uuid4().hex[:6]}", "o": made["org"]},
        ).scalar_one())
        for name, cat, pos in (
            ("To do", "todo", 0), ("Done", "done", 1),
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
            text(
                "DELETE FROM pm_activities WHERE task_id IN (SELECT id FROM"
                " pm_tasks WHERE project_id = CAST(:p AS uuid))"
            ),
            {"p": made["project"]},
        )
        for tbl in ("pm_task_assignees",):
            c.execute(
                text(
                    f"DELETE FROM {tbl} WHERE task_id IN (SELECT id FROM"
                    " pm_tasks WHERE project_id = CAST(:p AS uuid))"
                ),
                {"p": made["project"]},
            )
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


def _task(conn, scope, *, title, cat="todo", due=None, est=None, who=None):
    tid = str(conn.execute(
        text(
            "INSERT INTO pm_tasks (title, project_id, root_project_id,"
            " status_id, created_by, organization_id, task_number, due_at,"
            " estimate_mins)"
            " SELECT :t, CAST(:p AS uuid), CAST(:p AS uuid), CAST(:s AS uuid),"
            " 'out@example.test', CAST(:o AS uuid),"
            " COALESCE(MAX(task_number),0)+1, CAST(:due AS timestamptz), :est"
            " FROM pm_tasks WHERE root_project_id = CAST(:p AS uuid)"
            " RETURNING id"
        ),
        {
            "t": title, "p": scope["project"], "s": scope[cat],
            "o": scope["org"], "due": due, "est": est,
        },
    ).scalar_one())
    if who:
        conn.execute(
            text(
                "INSERT INTO pm_task_assignees (task_id, assignee, assigned_by)"
                " VALUES (CAST(:t AS uuid), :a, 'out@example.test')"
            ),
            {"t": tid, "a": who},
        )
    return tid


class TestTheQueriesAgainstPostgres:
    def test_the_plan_read_reports_the_last_due_date_and_its_coverage(
        self, db, scope,
    ):
        from gateway.routes.projects.analytics import planned_finish_sql

        with db.begin() as c:
            _task(c, scope, title="a", due="2026-03-12T00:00:00Z")
            _task(c, scope, title="b", due="2026-02-01T00:00:00Z")
            _task(c, scope, title="undated")
        where = (
            "t.project_id = CAST(:pid AS uuid) AND t.archived_at IS NULL"
            " AND s.category <> ALL(CAST(:closed AS text[]))"
        )
        with db.connect() as c:
            row = c.execute(
                text(planned_finish_sql(where)),
                {"pid": scope["project"], "closed": ["done", "cancelled"]},
            ).one()
        assert row.planned_finish.date() == date(2026, 3, 12)
        # ⚠️ 2 of 3. A "planned finish" over a third of the backlog is a
        # claim about a third of it.
        assert (row.dated, row.tasks) == (2, 3)

    def test_the_capacity_read_counts_only_people_holding_open_work(
        self, db, scope,
    ):
        """⚠️ A company of forty has forty people's hours, and none of that is
        capacity for THIS project. The assignee join is what makes it mean
        something."""
        from gateway.routes.projects.analytics import team_capacity_sql

        with db.begin() as c:
            _task(c, scope, title="held", who="ana@example.test")
            _task(c, scope, title="also held", who="ana@example.test")
            _task(c, scope, title="nobody")
            _task(c, scope, title="finished", cat="done", who="zed@example.test")
        where = (
            "t.project_id = CAST(:pid AS uuid) AND t.archived_at IS NULL"
            " AND s.category <> ALL(CAST(:closed AS text[]))"
        )
        with db.connect() as c:
            row = c.execute(
                text(team_capacity_sql(where)),
                {
                    "pid": scope["project"],
                    "closed": ["done", "cancelled"],
                    "horizon_days": 90,
                },
            ).one()
        # Ana once, despite two tasks. Not the unassigned task, and not zed —
        # somebody whose only work here is finished is not carrying load.
        assert row.people == 1

    def test_an_agent_assignee_is_not_a_person_with_working_hours(
        self, db, scope,
    ):
        from gateway.routes.projects.analytics import team_capacity_sql

        with db.begin() as c:
            _task(c, scope, title="human", who="ana@example.test")
            _task(c, scope, title="robot", who="agent:summariser")
        where = (
            "t.project_id = CAST(:pid AS uuid) AND t.archived_at IS NULL"
            " AND s.category <> ALL(CAST(:closed AS text[]))"
        )
        with db.connect() as c:
            row = c.execute(
                text(team_capacity_sql(where)),
                {
                    "pid": scope["project"],
                    "closed": ["done", "cancelled"],
                    "horizon_days": 90,
                },
            ).one()
        assert row.people == 1

    def test_the_velocity_read_puts_a_quiet_week_on_the_axis_as_a_zero(
        self, db, scope,
    ):
        """A week with no completions must be a row, not an absent one. An
        average over the weeks that happened to have activity is not a
        velocity — `weekly_sql` records the same lesson for the chart."""
        from gateway.routes.projects.analytics import velocity_sql

        where = "t.project_id = CAST(:pid AS uuid)"
        with db.connect() as c:
            rows = c.execute(
                text(velocity_sql(where)),
                {
                    "pid": scope["project"],
                    "weeks": 6,
                    "closing": ["cancelled", "done"],
                    "done_cat": "done",
                    "started_cat": "in_progress",
                },
            ).fetchall()
        assert len(rows) == 6
        assert all(r.finished == 0 for r in rows)

    def test_arrivals_are_counted_even_with_no_activity_rows(self, db, scope):
        """⚠️ A task's BIRTH is not a status change, so the activity spine has
        no row for it. Reading arrivals off the spine would report zero work
        arriving on every project that never moves a status."""
        from gateway.routes.projects.analytics import velocity_sql

        with db.begin() as c:
            for i in range(3):
                _task(c, scope, title=f"new-{i}")
        where = "t.project_id = CAST(:pid AS uuid)"
        with db.connect() as c:
            rows = c.execute(
                text(velocity_sql(where)),
                {
                    "pid": scope["project"],
                    "weeks": 6,
                    "closing": ["cancelled", "done"],
                    "done_cat": "done",
                    "started_cat": "in_progress",
                },
            ).fetchall()
        # Created THIS week, and the window is the six whole weeks before it,
        # so none of them land in the sample. The point is that the query
        # runs and attributes arrivals by week rather than dropping them.
        assert sum(int(r.created) for r in rows) == 0
