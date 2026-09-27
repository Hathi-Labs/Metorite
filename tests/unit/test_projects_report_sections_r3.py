"""WS-27bn R3a — the outlook section, and the ageing bands in ``stuck``.

Spec: ``project-docs/specs/projects_reports.md`` §8 R3a.

**The claim is one computation.** A report section and the Analytics panel
beside it read the SAME function, so they cannot disagree:

* ``outlook``: ``render_body`` awaits ``outlook_body``, the function the
  ``GET /projects/analytics/outlook`` route also awaits. For one scope and one
  caller the two bodies are EQUAL.
* ``outlook`` reads ``FORECAST_WEEKS`` of history, never the report period. A
  report with ``weeks`` 1 samples as many weeks as the route does. The route
  clamps a ``weeks`` of 1 to 2, so a render that passed the period would
  change the forecast.
* ``stuck``: the render's open-work predicate is the ``stuck`` route's, and
  the bands come from ``stale_bands_sql``. Each band equals the route's.

⚠️ The R8 half SKIPS without ``TENANT_LADDER_DATABASE_URL``, and a skip is not
a pass. ``bash scripts/dev_db.sh`` brings the database up.
"""
from __future__ import annotations

import asyncio
import inspect
import os
import uuid
from contextlib import asynccontextmanager
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from gateway.routes.projects import analytics as ana
from gateway.routes.projects import reports as rep
from sqlalchemy import text

REPO = Path(__file__).resolve().parents[2]
_TENANT_URL = os.environ.get("TENANT_LADDER_DATABASE_URL", "").strip()
_needs_db = pytest.mark.skipif(
    not _TENANT_URL,
    reason=(
        "TENANT_LADDER_DATABASE_URL unset — R8 requires a REAL Postgres. A "
        "skip here is not a pass; CI must set it."
    ),
)


# ── Hermetic: the source says one computation ───────────────────────────────


def _reports_source() -> str:
    return (
        REPO / "apps/services/gateway/gateway/routes/projects/reports.py"
    ).read_text(encoding="utf-8")


def test_the_render_awaits_the_routes_own_outlook_body() -> None:
    assert "await outlook_body(" in _reports_source()
    assert "await outlook_body(" in inspect.getsource(ana.outlook)


def test_the_render_never_passes_the_report_period_to_the_forecast() -> None:
    """`config["weeks"]` of 1 would clamp to 2 and change the forecast."""
    body = inspect.getsource(rep.render_body)
    start = body.index("await outlook_body(") + len("await outlook_body(")
    depth, end = 1, start
    while depth:
        depth += {"(": 1, ")": -1}.get(body[end], 0)
        end += 1
    call = body[start:end]
    assert "include_subtree" in call, call
    assert "weeks" not in call, call


def test_the_stuck_section_reads_the_routes_band_builder() -> None:
    body = inspect.getsource(rep.render_body)
    assert "stale_bands_sql(open_where)" in body
    assert "stale_bands_sql(open_where)" in inspect.getsource(ana.stuck)


def test_outlook_is_an_opt_in_section_in_the_declared_order() -> None:
    assert "outlook" in rep.SECTIONS
    assert "outlook" not in rep.DEFAULT_SECTIONS
    assert "outlook" not in rep.normalise_report_config({})["sections"]
    # §8 R3 declares the whole order. Each slice adds its name in its place.
    declared = (
        "finished", "throughput", "outlook", "load", "capacity", "pulse",
        "stuck", "hygiene", "conflicts", "rebalance",
    )
    assert list(rep.SECTIONS) == [s for s in declared if s in rep.SECTIONS]
    got = rep.normalise_report_config({"sections": ["stuck", "outlook", "finished"]})
    assert got["sections"] == ["finished", "outlook", "stuck"]


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


def _weeks_ago(n: int) -> str:
    """A moment inside the week `n` weeks before this one, in UTC."""
    return (
        f"((date_trunc('week', now() AT TIME ZONE 'UTC')"
        f" - make_interval(weeks => {int(n)}) + interval '2 days')"
        f" AT TIME ZONE 'UTC')"
    )


@pytest.fixture
def seeded(_ladder):
    """One project with open work in each age band, a plan and a history.

    Open tasks sit in three of the four bands and carry due dates, so the
    plan has a date. Seven tasks finished across the last three closed weeks,
    so the forecast converges and has a date too. A null on both sides would
    agree by accident.
    """
    from sqlalchemy import create_engine

    eng = create_engine(_TENANT_URL, future=True)
    tag = uuid.uuid4().hex[:8]
    who = f"r3-{tag}@example.test"
    made: dict[str, Any] = {"tag": tag}
    with eng.begin() as c:
        org = str(c.execute(
            text("SELECT id FROM organization ORDER BY created_at LIMIT 1")
        ).scalar_one())
        made["org"] = org
        pid = str(c.execute(
            text(
                "INSERT INTO pm_projects (name, status, source, created_by,"
                " organization_id, timezone, parent_project_id, owns_statuses)"
                " VALUES (:n,'active','manual','r3@example.test',"
                " CAST(:o AS uuid),'UTC',NULL,true) RETURNING id"
            ),
            {"n": f"r3-{tag}", "o": org},
        ).scalar_one())
        made["project"] = pid
        status: dict[str, str] = {}
        for name, cat, pos in (("To do", "todo", 0), ("Doing", "in_progress", 1),
                               ("Done", "done", 2)):
            status[cat] = str(c.execute(
                text(
                    "INSERT INTO pm_task_statuses (project_id,name,color,"
                    " position,category) VALUES (CAST(:p AS uuid),:n,'gray',"
                    " :pos,:cat) RETURNING id"
                ),
                {"p": pid, "n": name, "pos": pos, "cat": cat},
            ).scalar_one())

        def task(title: str, cat: str, *, due_days: int | None,
                 idle_days: int) -> str:
            tid = str(c.execute(
                text(
                    "INSERT INTO pm_tasks (title, project_id, root_project_id,"
                    " status_id, created_by, organization_id, task_number,"
                    " due_at, created_at, updated_at)"
                    " SELECT :t, CAST(:p AS uuid), CAST(:p AS uuid),"
                    " CAST(:s AS uuid), 'r3@example.test', CAST(:o AS uuid),"
                    " COALESCE(MAX(task_number),0)+1,"
                    " CASE WHEN CAST(:d AS int) IS NULL THEN NULL"
                    "      ELSE now() + make_interval(days => CAST(:d AS int)) END,"
                    # Born long ago, so no task ARRIVES inside the
                    # forecast's weeks and the forecast converges.
                    " now() - interval '200 days',"
                    " now() - make_interval(days => CAST(:i AS int))"
                    " FROM pm_tasks WHERE root_project_id = CAST(:p AS uuid)"
                    " RETURNING id"
                ),
                {"t": title, "p": pid, "s": status[cat], "o": org,
                 "d": due_days, "i": idle_days},
            ).scalar_one())
            c.execute(
                text(
                    "INSERT INTO pm_task_assignees (task_id, assignee,"
                    " assigned_by) VALUES (CAST(:t AS uuid), :a,"
                    " 'r3@example.test')"
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
                    f" 'r3@example.test', :b, CAST(:m AS jsonb), {when})"
                ),
                {
                    "i": tid, "o": org, "b": f"{frm} -> {to}",
                    "m": f'{{"from_category": "{frm}", "to_category": "{to}"}}',
                },
            )

        # Open work, in three of the four bands, each with a due date.
        task("fresh", "todo", due_days=5, idle_days=1)
        task("fresh too", "in_progress", due_days=9, idle_days=2)
        task("a week idle", "todo", due_days=12, idle_days=10)
        task("forgotten", "todo", due_days=20, idle_days=45)
        task("forgotten too", "todo", due_days=3, idle_days=60)
        # History: seven finished across the last three closed weeks.
        for n, weeks in ((3, 1), (2, 2), (2, 3)):
            for i in range(n):
                tid = task(f"done {weeks}.{i}", "done", due_days=None,
                           idle_days=weeks * 7)
                act(tid, "todo", "in_progress", f"{_weeks_ago(weeks)} - interval '1 day'")
                act(tid, "in_progress", "done", _weeks_ago(weeks))
        # WS-27bm S11. The holder is in the directory and is away for one
        # full working week inside the capacity window. So an admin and a
        # member get DIFFERENT hours, and each section must equal its route.
        made["person"] = str(c.execute(
            text(
                "INSERT INTO people (id, name, email, status, skills, source,"
                " source_key, organization_id, updated_by, updated_at)"
                " VALUES (gen_random_uuid(), :n, :e, 'active', ARRAY[]::text[],"
                " 'manual', :k, CAST(:o AS uuid), 'test', now()) RETURNING id"
            ),
            {"n": f"r3 {tag}", "e": who, "k": f"manual:r3:{tag}", "o": org},
        ).scalar_one())
        monday = date.today() - timedelta(days=date.today().isoweekday() - 1)
        monday += timedelta(weeks=2)
        c.execute(
            text(
                "INSERT INTO people_absences (person_id, organization_id,"
                " starts_on, ends_on, kind, created_by) VALUES"
                " (CAST(:p AS uuid), CAST(:o AS uuid), :s, :e, 'away',"
                " 'r3@example.test')"
            ),
            {"p": made["person"], "o": org, "s": monday,
             "e": monday + timedelta(days=4)},
        )
    yield made
    with eng.begin() as c:
        c.execute(text("DELETE FROM people WHERE id = CAST(:i AS uuid)"),
                  {"i": made["person"]})
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


def _user(*, hr: bool = False) -> Any:
    from acb_auth import UserContext, UserRole, build_access

    grants = ["feature:projects"] + (["admin:members:read"] if hr else [])
    return UserContext(email="r3@example.test", role=UserRole.EMPLOYEE,
                       access=build_access(grants))


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


def _report(project: str | None, *, hr: bool = False, **config: Any) -> dict[str, Any]:
    return asyncio.run(
        rep.preview_report(
            {"project_id": project, "config": config}, user=_user(hr=hr),
        )
    )["sections"]


@_needs_db
@pytest.mark.parametrize("hr", [False, True], ids=["member", "admin"])
@pytest.mark.parametrize("scope", ["project", "portfolio"])
def test_the_outlook_section_equals_the_outlook_route(seeded, wired, scope, hr) -> None:
    """WS-27bn R3a, and WS-27bm S11 §17.4 item 7: for each READER's grant."""
    pid = seeded["project"] if scope == "project" else None
    got = _report(pid, hr=hr, sections=["outlook"], weeks=1)["outlook"]
    want = asyncio.run(
        ana.outlook(project_id=pid, include_subtree=True,
                    weeks=ana.FORECAST_WEEKS, user=_user(hr=hr))
    )
    assert got == want
    assert got["hr_visible"] is hr
    assert got["people"]["absences_applied"] is hr
    if scope == "project":
        # S11. One holder on the default week, away for one week in twelve.
        # The reader's grant decides whether that week counts.
        assert got["capacity"]["hours_per_week"] == (36.7 if hr else 40.0)
        # Real figures, not two nulls that agree by accident.
        assert got["velocity"]["verdict"] == "converging", got["velocity"]
        assert got["velocity"]["finish_date"] is not None
        assert got["plan"]["planned_finish"] is not None
        assert isinstance(got["plan"]["slip_days"], int)
        assert got["velocity"]["remaining_tasks"] == 5


@_needs_db
@pytest.mark.parametrize("weeks", [1, 4])
def test_a_report_period_does_not_change_the_forecast_window(seeded, wired, weeks) -> None:
    pid = seeded["project"]
    got = _report(pid, sections=["outlook"], weeks=weeks)["outlook"]
    route = asyncio.run(
        ana.outlook(project_id=pid, include_subtree=True,
                    weeks=ana.FORECAST_WEEKS, user=_user())
    )
    assert got["velocity"]["weeks_sampled"] == route["velocity"]["weeks_sampled"]
    assert got["velocity"]["weeks_sampled"] == ana.FORECAST_WEEKS
    assert got["weeks"] == ana.FORECAST_WEEKS


@_needs_db
@pytest.mark.parametrize("scope", ["project", "portfolio"])
def test_each_stuck_band_equals_the_stuck_route(seeded, wired, scope) -> None:
    pid = seeded["project"] if scope == "project" else None
    got = _report(pid, sections=["stuck"])["stuck"]
    route = asyncio.run(ana.stuck(project_id=pid, include_subtree=True, user=_user()))
    assert got["stale"] == route["stale"]
    assert [b["band"] for b in got["stale"]] == [b for b, _, _ in ana.STALE_BANDS]
    if scope == "project":
        by_band = {b["band"]: b["n"] for b in got["stale"]}
        assert by_band == {
            "under_7d": 2, "days_7_to_14": 1, "days_14_to_30": 0, "over_30d": 2,
        }
