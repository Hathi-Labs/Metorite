"""WS-27bn R2b — the figures a report panel needs, passed through.

Spec: ``project-docs/specs/projects_reports.md`` §8 R2b.

Three Analytics panels need figures that the report body dropped. The SQL
that ``render_body`` calls already selects each one, so R2b copies them into
the body and adds no query and no arithmetic:

* ``load``: ``due_next_7d`` and ``later`` for each person, from ``load_sql``.
* ``throughput``: ``p90_hours``, ``no_start`` and ``cancelled``, from
  ``_CYCLE_MEASURES``.
* ``finished``: ``median_hours`` for each project, from ``finished_sql``.

**The claim.** Each copied value equals the analytics route's value for the
same scope and the same period. A report panel and an Analytics panel then
draw one number, not two.

⚠️ The R8 half SKIPS without ``TENANT_LADDER_DATABASE_URL``, and a skip is not
a pass. ``bash scripts/dev_db.sh`` brings the database up.
"""
from __future__ import annotations

import asyncio
import inspect
import os
import uuid
from contextlib import asynccontextmanager
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from gateway.routes.projects import analytics as ana
from gateway.routes.projects import reports as rep
from sqlalchemy import text

_TENANT_URL = os.environ.get("TENANT_LADDER_DATABASE_URL", "").strip()
_needs_db = pytest.mark.skipif(
    not _TENANT_URL,
    reason=(
        "TENANT_LADDER_DATABASE_URL unset — R8 requires a REAL Postgres. A "
        "skip here is not a pass; CI must set it."
    ),
)


# ── Hermetic: the render copies, and does not compute ──────────────────────


def test_the_render_copies_the_three_figures_from_the_analytics_sql() -> None:
    """The keys are in the render, and each reads a column the SQL selects."""
    body = inspect.getsource(rep.render_body)
    for key, column in (
        ('"due_next_7d"', "p.due_next_7d"),
        ('"later"', "p.later"),
        ('"p90_hours"', "totals.p90_hours"),
        ('"no_start"', "totals.no_start"),
        ('"cancelled"', "totals.cancelled"),
        ('"median_hours"', "r.median_hours"),
    ):
        assert key in body, key
        assert column in body, column
    # The SQL the render calls selects each column already.
    assert "AS due_next_7d" in ana.load_sql("TRUE")
    assert "AS later" in ana.load_sql("TRUE")
    for column in ("p90_hours", "no_start", "cancelled"):
        assert f"AS {column}" in ana._CYCLE_MEASURES
    assert "AS median_hours" in ana.finished_sql("TRUE")


# ── R8: a real Postgres, through asyncpg ─────────────────────────────────────


def _async_url() -> str:
    url = _TENANT_URL
    if "postgresql+psycopg" in url:
        return url.replace("postgresql+psycopg", "postgresql+asyncpg")
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return url


@pytest.fixture(scope="module")
def _ladder():
    """Apply the tenant ladder ONCE for this module, never once per test."""
    if not _TENANT_URL:
        pytest.skip("TENANT_LADDER_DATABASE_URL unset")
    from sqlalchemy import create_engine

    from tests.unit._tenant_ladder import apply_ladder

    eng = create_engine(_TENANT_URL, future=True)
    with eng.begin() as conn:
        apply_ladder(conn)
    eng.dispose()


#: A moment in LAST week, in UTC. The report's `skip_current_week` window
#: holds it, and so does the dashboard's window.
_LAST_WEEK = (
    "((date_trunc('week', now() AT TIME ZONE 'UTC') - interval '2 days')"
    " AT TIME ZONE 'UTC')"
)


@pytest.fixture
def seeded(_ladder):
    """One project with open work in each due bucket and finished history.

    The finished tasks carry real cycle times, so the median and the p90 are
    numbers and not nulls. One task has no recorded start and one task is
    cancelled, so `no_start` and `cancelled` are not zero.
    """
    from sqlalchemy import create_engine

    eng = create_engine(_TENANT_URL, future=True)
    tag = uuid.uuid4().hex[:8]
    ana_ = f"ana-{tag}@example.test"
    bo = f"bo-{tag}@example.test"
    made: dict[str, Any] = {"ana": ana_, "bo": bo, "tag": tag}
    with eng.begin() as c:
        org = str(c.execute(
            text("SELECT id FROM organization ORDER BY created_at LIMIT 1")
        ).scalar_one())
        made["org"] = org
        pid = str(c.execute(
            text(
                "INSERT INTO pm_projects (name, status, source, created_by,"
                " organization_id, timezone, parent_project_id, owns_statuses)"
                " VALUES (:n,'active','manual','rv@example.test',"
                " CAST(:o AS uuid),'UTC',NULL,true) RETURNING id"
            ),
            {"n": f"rv-{tag}", "o": org},
        ).scalar_one())
        made["project"] = pid
        status: dict[str, str] = {}
        for name, cat, pos in (("To do", "todo", 0), ("Doing", "in_progress", 1),
                               ("Done", "done", 2), ("Dropped", "cancelled", 3)):
            status[cat] = str(c.execute(
                text(
                    "INSERT INTO pm_task_statuses (project_id,name,color,"
                    " position,category) VALUES (CAST(:p AS uuid),:n,'gray',"
                    " :pos,:cat) RETURNING id"
                ),
                {"p": pid, "n": name, "pos": pos, "cat": cat},
            ).scalar_one())

        def task(title: str, cat: str, who: str | None, due_days: int | None) -> str:
            tid = str(c.execute(
                text(
                    "INSERT INTO pm_tasks (title, project_id, root_project_id,"
                    " status_id, created_by, organization_id, task_number, due_at)"
                    " SELECT :t, CAST(:p AS uuid), CAST(:p AS uuid),"
                    " CAST(:s AS uuid), 'rv@example.test', CAST(:o AS uuid),"
                    " COALESCE(MAX(task_number),0)+1,"
                    " CASE WHEN CAST(:d AS int) IS NULL THEN NULL"
                    "      ELSE now() + make_interval(days => CAST(:d AS int)) END"
                    " FROM pm_tasks WHERE root_project_id = CAST(:p AS uuid)"
                    " RETURNING id"
                ),
                {"t": title, "p": pid, "s": status[cat], "o": org, "d": due_days},
            ).scalar_one())
            if who:
                c.execute(
                    text(
                        "INSERT INTO pm_task_assignees (task_id, assignee,"
                        " assigned_by) VALUES (CAST(:t AS uuid), :a,"
                        " 'rv@example.test')"
                    ),
                    {"t": tid, "a": who},
                )
            return tid

        def act(tid: str, frm: str, to: str, when: str) -> None:
            c.execute(
                text(
                    "INSERT INTO pm_activities (task_id, organization_id, type,"
                    " created_by, body, meta, created_at) VALUES"
                    f" (CAST(:i AS uuid), CAST(:o AS uuid), 'status_change',"
                    f" 'rv@example.test', :b, CAST(:m AS jsonb), {when})"
                ),
                {
                    "i": tid, "o": org, "b": f"{frm} -> {to}",
                    "m": f'{{"from_category": "{frm}", "to_category": "{to}"}}',
                },
            )

        # Open work in each of load's three buckets.
        task("late", "todo", ana_, -3)
        task("soon", "in_progress", ana_, 3)
        task("far", "todo", ana_, 20)
        task("undated", "todo", ana_, None)
        task("bo soon", "todo", bo, 2)
        task("nobody's", "todo", None, None)

        # Finished work this week, with three different cycle times.
        for title, hours in (("quick", 5), ("medium", 30), ("slow", 100)):
            tid = task(title, "done", ana_, None)
            act(tid, "todo", "in_progress",
                f"now() - make_interval(hours => {hours + 1})")
            act(tid, "in_progress", "done", "now() - interval '1 hour'")
        # Finished last week, so the report's closed period is not empty.
        for title, hours in (("last a", 8), ("last b", 60)):
            tid = task(title, "done", bo, None)
            act(tid, "todo", "in_progress",
                f"{_LAST_WEEK} - make_interval(hours => {hours})")
            act(tid, "in_progress", "done", _LAST_WEEK)
        # Done with no recorded start: `no_start`, and no cycle time.
        tid = task("no start", "done", bo, None)
        act(tid, "todo", "done", "now() - interval '1 hour'")
        # Cancelled: `cancelled`, and never throughput.
        tid = task("dropped", "cancelled", bo, None)
        act(tid, "todo", "cancelled", "now() - interval '1 hour'")
    yield made
    with eng.begin() as c:
        c.execute(
            text(
                "DELETE FROM pm_activities WHERE task_id IN"
                " (SELECT id FROM pm_tasks WHERE project_id = CAST(:p AS uuid))"
            ),
            {"p": made["project"]},
        )
        c.execute(text("DELETE FROM pm_tasks WHERE project_id = CAST(:p AS uuid)"),
                  {"p": made["project"]})
        c.execute(text("DELETE FROM pm_task_statuses WHERE project_id = CAST(:p AS uuid)"),
                  {"p": made["project"]})
        c.execute(text("DELETE FROM pm_projects WHERE id = CAST(:p AS uuid)"),
                  {"p": made["project"]})
    eng.dispose()


def _user() -> Any:
    from acb_auth import UserContext, UserRole, build_access

    return UserContext(email="rv@example.test", role=UserRole.EMPLOYEE,
                       access=build_access(["feature:projects"]))


@pytest.fixture
def wired(seeded, monkeypatch):
    """Bind the report routes AND the analytics routes to one engine.

    Both modules read the same visibility, so the two answers describe one
    reader and one scope.
    """
    from gateway.routes.projects.core import Visibility
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool

    eng = create_async_engine(_async_url(), future=True, poolclass=NullPool)

    @asynccontextmanager
    async def _session(*_a: Any, **_k: Any):
        async with eng.begin() as conn:
            yield conn

    async def _resolve(_db: Any, _user: Any) -> Any:
        return Visibility(unrestricted=True, email="", groups=(),
                          organization_id=seeded["org"])

    for module in (rep, ana):
        monkeypatch.setattr(module, "_tenant_session", _session)
        monkeypatch.setattr(module, "resolve_visibility", _resolve)
    yield
    asyncio.run(eng.dispose())


def _report(project: str, **config: Any) -> dict[str, Any]:
    return asyncio.run(
        rep.preview_report(
            {"project_id": project, "config": config}, user=_user(),
        )
    )["sections"]


@_needs_db
def test_load_buckets_equal_the_load_route(seeded, wired) -> None:
    pid = seeded["project"]
    sections = _report(pid, sections=["load"])
    route = asyncio.run(ana.load(project_id=pid, include_subtree=True, user=_user()))

    by_who = {p["assignee"]: p for p in route["people"]}
    got = sections["load"]["people"]
    assert got, "the seed has open work, so the comparison is not empty"
    for person in got:
        want = by_who[person["assignee"]]
        assert person["due_next_7d"] == want["due_next_7d"], person
        assert person["later"] == want["later"], person
    # The seed puts real figures in each bucket.
    ana_row = next(p for p in got if p["assignee"] == seeded["ana"])
    assert (ana_row["overdue"], ana_row["due_next_7d"], ana_row["later"]) == (1, 1, 2)


@_needs_db
def test_throughput_figures_equal_the_throughput_route(seeded, wired) -> None:
    """The route has no `skip_current_week`, so the report asks for none."""
    pid = seeded["project"]
    sections = _report(pid, sections=["throughput"], weeks=4,
                       skip_current_week=False)
    route = asyncio.run(
        ana.throughput(project_id=pid, include_subtree=True, weeks=4, user=_user())
    )
    got = sections["throughput"]
    want = route["summary"]
    for key in ("p90_hours", "no_start", "cancelled", "median_hours", "measured"):
        assert got[key] == want[key], key
    # Real figures, not nulls and zeros that agree by accident.
    assert got["p90_hours"] is not None
    assert got["no_start"] == 1
    assert got["cancelled"] == 1


@_needs_db
@pytest.mark.parametrize("skip", [False, True])
def test_finished_medians_equal_the_finished_route(seeded, wired, skip) -> None:
    pid = seeded["project"]
    sections = _report(pid, sections=["finished"], weeks=2,
                       skip_current_week=skip)
    route = asyncio.run(
        ana.finished(project_id=pid, include_subtree=True, weeks=2,
                     skip_current_week=skip, user=_user())
    )
    want = {p["project_id"]: p["median_hours"] for p in route["projects"]}
    got = sections["finished"]["projects"]
    assert got, "the seed finished work in both windows"
    for project in got:
        assert project["median_hours"] == want[project["project_id"]], project
        assert project["median_hours"] is not None
