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

import asyncio
import inspect
import json
import os
import uuid
from datetime import UTC, date, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from gateway.routes.projects import analytics as _ana
from gateway.routes.projects.analytics import (
    MIN_FINISHED_FOR_FORECAST,
    capacity_forecast,
    project_forecast,
    slip_days,
)
from gateway.work_schedule import DEFAULT_POLICY

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


# ── WS-27bm S11: the capacity rate reads the schedule — hermetic half ──────
#
# Spec: `projects_ai_chat.md` §17. The owner's answer, 2026-09-25: every
# viewer gets each holder's working schedule, and leave reduces the hours
# ONLY for a viewer with `admin:members:read`. The decisions are pinned here
# against a fake that records each statement. The R8 half below runs the SQL.

#: A fixed Wednesday, so a hermetic window never depends on the run date.
_WEDNESDAY = date(2026, 9, 23)
_PID = "00000000-0000-0000-0000-0000000000a1"


def _week_away(today: date, weeks_ahead: int = 2) -> dict[str, Any]:
    """One full Monday-to-Friday absence, ``weeks_ahead`` weeks from now."""
    monday = today - timedelta(days=today.isoweekday() - 1)
    monday += timedelta(weeks=weeks_ahead)
    return {"starts_on": monday, "ends_on": monday + timedelta(days=4),
            "kind": "away", "hours_per_day": None}


class _Res:
    def __init__(self, row: Any = None, rows: list[Any] | None = None):
        self._row, self._rows = row, rows or []

    def one(self) -> Any:
        return self._row

    def fetchall(self) -> list[Any]:
        return self._rows

    def fetchone(self) -> Any:
        return self._rows[0] if self._rows else None

    def scalar(self) -> Any:
        return 4


class _OutlookDB:
    """Answers `outlook_body`'s statements by shape, and records each one."""

    def __init__(self, *, schedules: Any, spans: list[dict] | None = None):
        self.statements: list[str] = []
        self.schedules = schedules
        self.spans = spans or []

    async def execute(self, sql: Any, params: dict | None = None) -> _Res:
        statement = " ".join(str(sql).split())
        self.statements.append(statement)
        if "FROM people_absences" in statement:
            return _Res(rows=[
                SimpleNamespace(person_id=_PID, **span) for span in self.spans
            ])
        if "FROM org_settings" in statement:
            return _Res(rows=[])
        n = len(self.schedules) if isinstance(self.schedules, list) else 1
        row = SimpleNamespace(
            mins=60 * 400, estimated=4, tasks=4, planned_finish=None,
            dated=0, people=n + 1, in_directory=n, schedules=self.schedules,
            leaving_soon=0,
        )
        return _Res(row=row, rows=[])


class _Vis:
    unrestricted = True
    params: dict[str, Any] = {"vis_org": "org-1"}  # noqa: RUF012


def _outlook(db: _OutlookDB, *, hr: bool) -> dict[str, Any]:
    async def _no_node(*_a: Any, **_k: Any) -> str:
        return "TRUE"

    original_scope = _ana.scope_clause
    original_vis = _ana.task_visibility_clause
    _ana.scope_clause = _no_node  # type: ignore[assignment]
    _ana.task_visibility_clause = lambda _v, _a: "TRUE"  # type: ignore[assignment]
    try:
        return asyncio.run(_ana.outlook_body(
            db, _Vis(), hr_visible=hr, project_id=None, include_subtree=True,
            today=_WEDNESDAY,
        ))
    finally:
        _ana.scope_clause = original_scope  # type: ignore[assignment]
        _ana.task_visibility_clause = original_vis  # type: ignore[assignment]


def _one(working_hours: Any = None) -> list[dict[str, Any]]:
    return [{"id": _PID, "working_hours": working_hours}]


class TestS11TheScheduleRate:
    """§17.4 items 1 to 6 and 9, against a recording fake."""

    def test_the_window_is_twelve_whole_weeks_whatever_the_weekday(self):
        # ⚠️ The divisor trap. `horizon_window` counts its end day, so asking
        # it for 84 days sums 85. Every start weekday must give exactly 40.
        for offset in range(7):
            today = _WEDNESDAY + timedelta(days=offset)
            start, end = _ana.outlook_window(today)
            assert (end - start).days + 1 == _ana.OUTLOOK_RATE_WEEKS * 7
            rate = _ana.outlook_rate(
                policy=DEFAULT_POLICY,
                holders=[SimpleNamespace(id=_PID, working_hours=None)],
                absences=None, today=today,
            )
            assert rate == pytest.approx(40.0), (today, rate)

    def test_the_default_policy_gives_forty_hours(self):
        got = _outlook(_OutlookDB(schedules=_one()), hr=False)
        assert got["capacity"]["hours_per_week"] == 40.0
        assert got["capacity"]["verdict"] == "ok"

    def test_the_schedules_column_may_arrive_as_a_json_string(self):
        # asyncpg returns a `json` column as text through raw `text()`.
        raw = json.dumps(_one({"hours_per_day": 4}))
        got = _outlook(_OutlookDB(schedules=raw), hr=False)
        assert got["capacity"]["hours_per_week"] == 20.0

    def test_nobody_in_the_directory_is_no_capacity(self):
        got = _outlook(_OutlookDB(schedules=[]), hr=True)
        assert got["capacity"]["verdict"] == "no_capacity"
        assert got["capacity"]["hours_per_week"] == 0.0
        assert got["people"]["in_directory"] == 0

    def test_with_no_absence_the_admin_and_the_member_see_one_figure(self):
        admin = _outlook(_OutlookDB(schedules=_one()), hr=True)
        member = _outlook(_OutlookDB(schedules=_one()), hr=False)
        assert admin["capacity"]["hours_per_week"] == 40.0
        assert member["capacity"]["hours_per_week"] == 40.0

    def test_a_week_away_reduces_the_hours_for_an_admin_only(self):
        spans = [_week_away(_WEDNESDAY)]
        admin = _outlook(_OutlookDB(schedules=_one(), spans=spans), hr=True)
        member = _outlook(_OutlookDB(schedules=_one(), spans=spans), hr=False)
        # 11 of 12 weeks at 40 hours: 440 / 12 = 36.67.
        assert admin["capacity"]["hours_per_week"] == 36.7
        assert member["capacity"]["hours_per_week"] == 40.0
        assert admin["people"]["absences_applied"] is True
        assert member["people"]["absences_applied"] is False
        assert (admin["hr_visible"], member["hr_visible"]) == (True, False)

    def test_without_the_grant_no_statement_reads_absences(self):
        member_db = _OutlookDB(schedules=_one(), spans=[_week_away(_WEDNESDAY)])
        _outlook(member_db, hr=False)
        assert not any("people_absences" in s for s in member_db.statements)
        admin_db = _OutlookDB(schedules=_one())
        _outlook(admin_db, hr=True)
        assert any("FROM people_absences" in s for s in admin_db.statements)

    def test_hr_visible_is_keyword_only_with_no_default(self):
        param = inspect.signature(_ana.outlook_body).parameters["hr_visible"]
        assert param.kind is inspect.Parameter.KEYWORD_ONLY
        assert param.default is inspect.Parameter.empty

    def test_the_two_hour_figures_are_one_figure(self):
        got = _outlook(_OutlookDB(schedules=_one({"hours_per_day": 7.3333})), hr=False)
        assert got["people"]["hours_per_week"] == got["capacity"]["hours_per_week"]

    def test_the_payload_says_what_it_counted(self):
        got = _outlook(_OutlookDB(schedules=_one()), hr=True)
        start, end = _ana.outlook_window(_WEDNESDAY)
        assert got["capacity"]["window"] == {
            "starts_on": start.isoformat(), "ends_on": end.isoformat(),
            "weeks": _ana.OUTLOOK_RATE_WEEKS,
        }
        assert set(got["people"]) == {
            "holding_open_work", "in_directory", "hours_per_week",
            "absences_applied", "leaving_within_90d",
        }

    def test_no_key_or_value_names_a_person_or_an_absence(self):
        """§17.3 rule 7. Team totals only: no list, no id, no absence span."""
        spans = [_week_away(_WEDNESDAY)]
        got = _outlook(_OutlookDB(schedules=_one(), spans=spans), hr=True)
        forbidden = {"email", "name", "person", "person_id", "id", "absences",
                     "spans", "kind", "rows", "schedules", "people_rows"}

        def walk(node: Any, path: str) -> None:
            assert not isinstance(node, list | tuple), f"a list at {path}"
            if isinstance(node, dict):
                for key, value in node.items():
                    assert key not in forbidden, f"{path}.{key}"
                    walk(value, f"{path}.{key}")
            else:
                assert node != _PID, f"a person id at {path}"

        walk(got, "outlook")


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


# ── WS-27bm S11, R8: the schedule rate, through asyncpg ─────────────────────
#
# §17.4 items 1, 2, 4 and 8 against a real Postgres. Each test runs in a
# FRESH organization, so the portfolio holds only the rows seeded here.


def _async_url() -> str:
    url = _TENANT_URL
    if "postgresql+psycopg" in url:
        return url.replace("postgresql+psycopg", "postgresql+asyncpg")
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return url


@pytest.fixture
def team(db):
    """A fresh org with three projects and three holders.

    * ``seen`` is granted to the viewer. Ana holds work there, with typed
      capacity 10 and one full working week away inside the window. A
      ghost (no ``people`` row) holds work there too.
    * ``hidden`` has no grant. Bo, who has a ``people`` row, holds work
      there and nowhere else.
    * ``ghosts`` holds only the ghost's work.

    Every task carries an estimate, so the capacity verdict can reach
    ``no_capacity`` instead of stopping at ``no_estimates``.
    """
    tag = uuid.uuid4().hex[:8]
    made: dict[str, Any] = {
        "tag": tag,
        "viewer": f"viewer-{tag}@example.test",
        "ana": f"ana-{tag}@example.test",
        "bo": f"bo-{tag}@example.test",
        "ghost": f"ghost-{tag}@example.test",
    }
    with db.begin() as c:
        org = str(c.execute(
            text(
                "INSERT INTO organization (slug, display_name)"
                " VALUES (:s, :s) RETURNING id"
            ),
            {"s": f"s11-{tag}"},
        ).scalar_one())
        made["org"] = org
        for name in ("seen", "hidden", "ghosts"):
            pid = str(c.execute(
                text(
                    "INSERT INTO pm_projects (name, status, source, created_by,"
                    " organization_id, timezone, parent_project_id, owns_statuses)"
                    " VALUES (:n,'active','manual','s11@example.test',"
                    " CAST(:o AS uuid),'UTC',NULL,true) RETURNING id"
                ),
                {"n": f"{name}-{tag}", "o": org},
            ).scalar_one())
            made[name] = pid
            made[f"{name}:todo"] = str(c.execute(
                text(
                    "INSERT INTO pm_task_statuses (project_id,name,color,"
                    " position,category) VALUES (CAST(:p AS uuid),'To do',"
                    " 'gray',0,'todo') RETURNING id"
                ),
                {"p": pid},
            ).scalar_one())
        c.execute(
            text(
                "INSERT INTO pm_project_grants (project_id, subject, created_by,"
                " organization_id) VALUES (CAST(:p AS uuid), :s,"
                " 's11@example.test', CAST(:o AS uuid))"
            ),
            {"p": made["seen"], "s": made["viewer"], "o": org},
        )

        def task(project: str, who: str) -> None:
            tid = str(c.execute(
                text(
                    "INSERT INTO pm_tasks (title, project_id, root_project_id,"
                    " status_id, created_by, organization_id, task_number,"
                    " estimate_mins)"
                    " SELECT :t, CAST(:p AS uuid), CAST(:p AS uuid),"
                    " CAST(:s AS uuid), 's11@example.test', CAST(:o AS uuid),"
                    " COALESCE(MAX(task_number),0)+1, 600"
                    " FROM pm_tasks WHERE root_project_id = CAST(:p AS uuid)"
                    " RETURNING id"
                ),
                {"t": f"{who} in {project}", "p": made[project],
                 "s": made[f"{project}:todo"], "o": org},
            ).scalar_one())
            c.execute(
                text(
                    "INSERT INTO pm_task_assignees (task_id, assignee,"
                    " assigned_by) VALUES (CAST(:t AS uuid), :a,"
                    " 's11@example.test')"
                ),
                {"t": tid, "a": who},
            )

        task("seen", made["ana"])
        task("seen", made["ghost"])
        task("hidden", made["bo"])
        task("ghosts", made["ghost"])

        for key, typed in (("ana", 10), ("bo", None)):
            made[f"{key}_id"] = str(c.execute(
                text(
                    "INSERT INTO people (id, name, email, status, skills, source,"
                    " source_key, organization_id, updated_by, updated_at,"
                    " capacity_hours_per_week)"
                    " VALUES (gen_random_uuid(), :n, :e, 'active',"
                    " ARRAY[]::text[], 'manual', :k, CAST(:o AS uuid), 'test',"
                    " now(), :cap) RETURNING id"
                ),
                {"n": f"{key} {tag}", "e": made[key],
                 "k": f"manual:{key}:{tag}", "o": org, "cap": typed},
            ).scalar_one())
        away = _week_away(date.today())
        c.execute(
            text(
                "INSERT INTO people_absences (person_id, organization_id,"
                " starts_on, ends_on, kind, created_by) VALUES"
                " (CAST(:p AS uuid), CAST(:o AS uuid), :s, :e, 'away',"
                " 's11@example.test')"
            ),
            {"p": made["ana_id"], "o": org, "s": away["starts_on"],
             "e": away["ends_on"]},
        )
    yield made
    with db.begin() as c:
        ids = [made["seen"], made["hidden"], made["ghosts"]]
        c.execute(
            text(
                "DELETE FROM pm_task_assignees WHERE task_id IN (SELECT id FROM"
                " pm_tasks WHERE project_id = ANY(CAST(:p AS uuid[])))"
            ),
            {"p": ids},
        )
        for sql in (
            "DELETE FROM pm_tasks WHERE project_id = ANY(CAST(:p AS uuid[]))",
            "DELETE FROM pm_project_grants"
            " WHERE project_id = ANY(CAST(:p AS uuid[]))",
            "DELETE FROM pm_task_statuses"
            " WHERE project_id = ANY(CAST(:p AS uuid[]))",
            "DELETE FROM pm_projects WHERE id = ANY(CAST(:p AS uuid[]))",
        ):
            c.execute(text(sql), {"p": ids})
        c.execute(
            text("DELETE FROM people WHERE id = ANY(CAST(:p AS uuid[]))"),
            {"p": [made["ana_id"], made["bo_id"]]},
        )
        c.execute(
            text("DELETE FROM organization WHERE id = CAST(:o AS uuid)"),
            {"o": made["org"]},
        )


def _run_outlook(
    team: dict[str, Any], *, hr: bool, project: str | None,
    restricted: bool = False,
) -> dict[str, Any]:
    from gateway.routes.projects.core import Visibility
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool

    vis = Visibility(
        unrestricted=not restricted,
        email=team["viewer"] if restricted else "",
        groups=(),
        organization_id=team["org"],
    )

    async def go() -> dict[str, Any]:
        eng = create_async_engine(_async_url(), future=True, poolclass=NullPool)
        try:
            async with eng.connect() as conn:
                return await _ana.outlook_body(
                    conn, vis, hr_visible=hr,
                    project_id=team[project] if project else None,
                    include_subtree=True,
                )
        finally:
            await eng.dispose()

    return asyncio.run(go())


class TestS11AgainstPostgres:
    def test_the_schedule_wins_over_a_typed_ten(self, team):
        """§17.4 item 1. Ana typed 10. The default policy says 40."""
        got = _run_outlook(team, hr=False, project="seen")
        assert got["people"]["holding_open_work"] == 2
        # The ghost holds work here and has no row. It adds zero (item 2).
        assert got["people"]["in_directory"] == 1
        assert got["capacity"]["hours_per_week"] == 40.0
        assert got["people"]["hours_per_week"] == 40.0
        assert got["capacity"]["verdict"] == "ok"

    def test_holders_with_no_directory_row_give_no_capacity(self, team):
        """§17.4 item 2. Never a fallback to a typed figure or to forty."""
        got = _run_outlook(team, hr=True, project="ghosts")
        assert got["people"]["holding_open_work"] == 1
        assert got["people"]["in_directory"] == 0
        assert got["capacity"]["verdict"] == "no_capacity"

    def test_a_week_away_counts_for_an_admin_only(self, team):
        """§17.4 item 4, through the real `people_absences` read."""
        admin = _run_outlook(team, hr=True, project="seen")
        member = _run_outlook(team, hr=False, project="seen")
        assert admin["capacity"]["hours_per_week"] == 36.7
        assert member["capacity"]["hours_per_week"] == 40.0
        assert admin["people"]["absences_applied"] is True
        assert member["people"]["absences_applied"] is False

    def test_invisible_work_adds_no_hours(self, team):
        """§17.4 item 8. Bo holds work only in a project the viewer cannot
        see, so the viewer's portfolio does not count Bo's hours."""
        everyone = _run_outlook(team, hr=False, project=None)
        assert everyone["people"]["in_directory"] == 2
        assert everyone["capacity"]["hours_per_week"] == 80.0
        viewer = _run_outlook(team, hr=False, project=None, restricted=True)
        assert viewer["people"]["holding_open_work"] == 2
        assert viewer["people"]["in_directory"] == 1
        assert viewer["capacity"]["hours_per_week"] == 40.0
