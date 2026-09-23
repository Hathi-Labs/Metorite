"""WS-27bm S7a — capacity: who holds the open work, and who has the hours.

Spec: ``project-docs/specs/projects_ai_chat.md`` §10.3, §13.2, §13.3.

Two halves, and the split is the point.

* **Hermetic** — the rules that are decisions rather than SQL: the horizon
  bounds, the two windows, the HR tier ABSENT (never null) without the grant,
  no hours without an estimate, and the leaf module agreeing with
  ``workload.at_risk_tasks`` / ``workload.classify`` for the same inputs. These
  never skip.
* **R8, on a real Postgres through asyncpg** — the claims only a database can
  prove. A row's ``open_tasks`` equals the Load route's count for the same
  scope (§10.3 item 1). The hours read every open task the caller can see,
  not only this scope. The unassigned row is always there. A shared fake would
  agree with whatever SQL it is handed, which is how five live bugs once
  shipped green.

⚠️ The R8 half SKIPS without ``TENANT_LADDER_DATABASE_URL``, and a skip is not
a pass. ``bash scripts/dev_db.sh`` brings the database up.
"""
from __future__ import annotations

import asyncio
import os
import uuid
from datetime import date, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from fastapi import HTTPException
from gateway import capacity as cap
from gateway import workload
from gateway.routes.projects import analytics_capacity as route
from sqlalchemy import text

from tests.unit._sql_match import hits

_TENANT_URL = os.environ.get("TENANT_LADDER_DATABASE_URL", "").strip()
_needs_db = pytest.mark.skipif(
    not _TENANT_URL,
    reason=(
        "TENANT_LADDER_DATABASE_URL unset — R8 requires a REAL Postgres. A "
        "skip here is not a pass; CI must set it."
    ),
)

#: The keys the spec names as HR tier (§10.3 item 2), spelled as the route
#: spells them. `HR_KEYS` must hold every one.
_SPEC_HR_KEYS = (
    "working_hours_horizon",
    "spare_hours_horizon",
    "committed_hours_horizon",
    "contracted_hours_per_week",
    "absences",
    "end_date",
    "max_concurrent_tasks",
    "at_risk",
)

MONDAY = date(2026, 9, 21)
WEDNESDAY = date(2026, 9, 23)


# ── Hermetic: the horizon ────────────────────────────────────────────────────


@pytest.mark.parametrize("bad", [0, -1, 91, 365, "x", None])
def test_the_route_refuses_a_horizon_outside_1_to_90(bad) -> None:
    with pytest.raises(HTTPException) as err:
        route.check_horizon(bad)
    assert err.value.status_code == 422


@pytest.mark.parametrize("good", [1, 14, 90, "30"])
def test_the_route_takes_a_horizon_inside_1_to_90(good) -> None:
    assert route.check_horizon(good) == int(good)


def test_the_default_horizon_is_the_dashboards_fourteen_days() -> None:
    import inspect

    default = inspect.signature(route.capacity).parameters["horizon_days"].default
    assert default == 14 == workload.HORIZON_DAYS


def test_a_bad_horizon_is_refused_before_any_session_opens(monkeypatch) -> None:
    opened: list[bool] = []

    def _session(*_a, **_k):
        opened.append(True)
        raise AssertionError("a session opened for a refused horizon")

    monkeypatch.setattr(route, "_tenant_session", _session)
    with pytest.raises(HTTPException):
        asyncio.run(route.capacity(horizon_days=0, user=SimpleNamespace()))
    assert opened == []


# ── Hermetic: the two windows ────────────────────────────────────────────────


def test_the_pill_window_is_monday_to_sunday() -> None:
    assert cap.week_window(WEDNESDAY) == (MONDAY, MONDAY + timedelta(days=6))
    assert cap.week_window(MONDAY) == (MONDAY, MONDAY + timedelta(days=6))


def test_the_horizon_window_starts_today() -> None:
    assert cap.horizon_window(WEDNESDAY, 10) == (
        WEDNESDAY, WEDNESDAY + timedelta(days=10),
    )


# ── Hermetic: the leaf module is workload's arithmetic, not a copy ──────────


def _schedule() -> dict[str, Any]:
    from gateway.work_schedule import DEFAULT_POLICY, effective_schedule

    return effective_schedule(DEFAULT_POLICY, None)


def _dated(*tasks: tuple[str, date, int | None]) -> list[dict[str, Any]]:
    return [
        {"id": str(i), "title": title, "due_at": due, "estimate_mins": est,
         "_due": due}
        for i, (title, due, est) in enumerate(tasks)
    ]


def test_at_risk_and_the_pill_equal_workload_for_the_same_inputs() -> None:
    """§10.3 item 5. Three four-hour tasks due Thursday cannot fit in the
    working time before it, and the leaf must say so exactly as workload
    does — not a re-derivation that agrees today."""
    schedule = _schedule()
    dated = _dated(
        ("a", WEDNESDAY + timedelta(days=1), 480),
        ("b", WEDNESDAY + timedelta(days=1), 480),
        ("c", WEDNESDAY + timedelta(days=1), 480),
    )
    totals = {"open_tasks": 3, "mins": 1440, "unestimated": 0, "overdue": 0}
    for horizon in (1, 14, 30):
        got = cap.person_capacity(
            schedule=schedule, spans=[], totals=totals, dated=dated,
            today=WEDNESDAY, horizon_days=horizon,
        )
        want_risk = workload.at_risk_tasks(
            schedule, dated, [], WEDNESDAY, horizon_days=horizon,
        )
        assert got["at_risk"] == want_risk
        want = workload.classify({
            "open_tasks": 3, "unestimated": 0, "overdue": 0,
            "contracted_hours": got["contracted_hours"],
            "committed_this_week": got["committed_this_week"],
            "at_risk": want_risk,
        })
        assert (got["pill"], got["reason"], got["flags"], got["hours_basis"]) == (
            want["pill"], want["reason"], want["flags"], want["hours_basis"],
        )
    assert got["pill"] == "at_risk"


def test_nothing_estimated_turns_the_hours_basis_off() -> None:
    got = cap.person_capacity(
        schedule=_schedule(), spans=[],
        totals={"open_tasks": 4, "mins": 0, "unestimated": 4, "overdue": 0},
        dated=[], today=WEDNESDAY,
    )
    assert got["hours_basis"] is False
    assert "no estimate" in (got["note"] or "")


def test_the_dashboard_row_is_projected_from_the_leaf_module() -> None:
    """§13.3 rule 2. The dashboard must CALL the leaf, not keep a copy."""
    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[2]
        / "apps/services/gateway/gateway/routes/people/dashboard.py"
    ).read_text(encoding="utf-8")
    assert "person_capacity(" in source
    for arithmetic in ("classify(", "at_risk_tasks(", "working_hours_between("):
        assert arithmetic not in source, f"dashboard.py still computes {arithmetic}"


# ── Hermetic: the HR tier, over the route's own body ─────────────────────────


class _Result:
    def __init__(self, rows: list[Any] | None = None, scalar: Any = None):
        self._rows = rows or []
        self._scalar = scalar

    def fetchall(self) -> list[Any]:
        return list(self._rows)

    def fetchone(self) -> Any:
        return self._rows[0] if self._rows else None

    def scalar(self) -> Any:
        return self._scalar


class _FakeDB:
    """Answers the capacity body's statements by shape.

    Hermetic on purpose for the DECISIONS — which keys a row carries for
    which caller. The SQL itself runs against Postgres in the R8 half below.
    """

    def __init__(self, *, estimated: bool = True):
        self.statements: list[str] = []
        self.estimated = estimated

    async def execute(self, sql: Any, params: dict | None = None) -> _Result:
        statement = " ".join(str(sql).split())
        self.statements.append(statement)
        if "count(DISTINCT t.id)" in statement:
            return _Result(scalar=3)
        if "AS due_next_7d" in statement:            # load_sql
            return _Result([
                SimpleNamespace(who="ana@x.io", open_tasks=2, overdue=0,
                                due_next_7d=1, later=1,
                                est_mins=120 if self.estimated else 0,
                                estimated=1 if self.estimated else 0),
                SimpleNamespace(who="", open_tasks=1, overdue=0, due_next_7d=0,
                                later=1, est_mins=0, estimated=0),
            ])
        if "FROM people_absences" in statement or "FROM people_skills" in statement:
            return _Result([])
        if "FROM org_settings" in statement:
            return _Result([])
        if hits(statement, "FROM people"):
            return _Result([SimpleNamespace(
                id="00000000-0000-0000-0000-00000000000a", name="Ana",
                email="ana@x.io", working_hours=None, end_date=None,
                max_concurrent_tasks=2)])
        if "AS in_progress" in statement:            # capacity_totals_sql
            return _Result([SimpleNamespace(
                who="ana@x.io", open_tasks=2,
                mins=120 if self.estimated else 0,
                unestimated=1 if self.estimated else 2,
                overdue=0, in_progress=1, next_due=None)])
        if "CAST(:until AS date)" in statement:      # capacity_dated_sql
            return _Result([])
        return _Result([])


class _Vis:
    unrestricted = True

    @property
    def params(self) -> dict[str, Any]:
        return {"vis_org": "org-1"}

    def task_clause(self, alias: str = "t") -> str:
        return "TRUE"


def _body(db: _FakeDB, *, hr: bool, horizon: int = 14) -> dict[str, Any]:
    import gateway.routes.projects.analytics as analytics

    async def _no_node(*_a, **_k):
        return "TRUE"

    original = route.scope_clause
    route.scope_clause = _no_node  # type: ignore[assignment]
    original_vis = analytics.task_visibility_clause
    try:
        analytics.task_visibility_clause = lambda _v, _a: "TRUE"  # type: ignore[assignment]
        return asyncio.run(route.capacity_body(
            db, _Vis(), hr_visible=hr, project_id=None, include_subtree=True,
            horizon_days=horizon, today=WEDNESDAY,
        ))
    finally:
        route.scope_clause = original  # type: ignore[assignment]
        analytics.task_visibility_clause = original_vis  # type: ignore[assignment]


def test_the_spec_hr_keys_are_all_in_the_routes_list() -> None:
    assert set(_SPEC_HR_KEYS) <= set(route.HR_KEYS)


def test_without_the_grant_every_hr_key_is_ABSENT_not_null() -> None:
    """§10.3 item 2. `None` would still say the field exists for this person,
    and a zero reads as "free"."""
    body = _body(_FakeDB(), hr=False)
    assert body["hr_visible"] is False
    assert body["rows"], "no rows came back"
    for row in body["rows"]:
        present = sorted(set(row) & set(route.HR_KEYS))
        assert present == [], f"HR keys leaked to a caller without the grant: {present}"


def test_without_the_grant_no_hr_statement_runs_at_all() -> None:
    db = _FakeDB()
    _body(db, hr=False)
    for fragment in ("AS in_progress", "FROM people_absences", "FROM people_skills",
                     "CAST(:until AS date)"):
        assert not any(fragment in s for s in db.statements), fragment


def test_with_the_grant_the_hr_half_arrives() -> None:
    body = _body(_FakeDB(), hr=True)
    ana = next(r for r in body["rows"] if r["assignee"] == "ana@x.io")
    for key in _SPEC_HR_KEYS:
        assert key in ana, key
    assert ana["name"] == "Ana"
    assert ana["all_work"]["in_progress"] == 1
    assert ana["max_concurrent_tasks"] == 2


def test_no_estimate_means_no_spare_or_committed_hours_and_says_why() -> None:
    """§10.3 item 3. The keys go; the reason stays."""
    body = _body(_FakeDB(estimated=False), hr=True)
    ana = next(r for r in body["rows"] if r["assignee"] == "ana@x.io")
    assert ana["hours_basis"] is False
    for key in route.HOURS_KEYS:
        assert key not in ana, key
    assert ana["hours_note"], "the row does not say why its hours are missing"
    # Working time is a schedule fact and stays.
    assert "working_hours_horizon" in ana


def test_the_response_prints_both_windows() -> None:
    body = _body(_FakeDB(), hr=True, horizon=10)
    assert body["windows"]["week"] == {
        "starts_on": "2026-09-21", "ends_on": "2026-09-27", "used_for": "pill",
    }
    assert body["windows"]["horizon"]["starts_on"] == "2026-09-23"
    assert body["windows"]["horizon"]["ends_on"] == "2026-10-03"
    assert body["windows"]["horizon"]["days"] == 10
    assert body["horizon_days"] == 10


def test_the_unassigned_row_is_always_there_and_last() -> None:
    body = _body(_FakeDB(), hr=True)
    assert body["rows"][-1]["kind"] == "unassigned"
    assert body["rows"][-1]["assignee"] is None
    assert sum(1 for r in body["rows"] if r["kind"] == "unassigned") == 1


def test_an_unassigned_row_carries_no_hr_half_even_for_an_admin() -> None:
    body = _body(_FakeDB(), hr=True)
    nobody = body["rows"][-1]
    assert not set(nobody) & set(route.HR_KEYS)


def test_the_route_is_mounted() -> None:
    from gateway.routes.projects.core import router

    paths = {getattr(r, "path", "") for r in router.routes}
    assert "/projects/analytics/capacity" in paths


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
    """Apply the tenant ladder ONCE for this module, never once per test.

    ⚠️ A replay is not free. Migration 42 drops columns that earlier rungs add
    back, so every replay spends attribute numbers on
    `email_assistant_settings`, and Postgres stops at 1600. Per-test replays
    reached that limit on the shared scratch database on 2026-09-23.
    """
    if not _TENANT_URL:
        pytest.skip("TENANT_LADDER_DATABASE_URL unset")
    from sqlalchemy import create_engine

    from tests.unit._tenant_ladder import apply_ladder

    eng = create_engine(_TENANT_URL, future=True)
    with eng.begin() as conn:
        apply_ladder(conn)
    eng.dispose()


@pytest.fixture
def seeded(_ladder):
    """Two projects in one org, three people, and open work across both.

    ``ana`` holds two open tasks in HERE (one estimated) and one in ELSEWHERE.
    ``bo`` shares one task in HERE with ana. One HERE task has nobody. One
    HERE task is done. ``ana`` has a directory row and a skill.
    """
    from sqlalchemy import create_engine

    eng = create_engine(_TENANT_URL, future=True)
    tag = uuid.uuid4().hex[:8]
    ana, bo = f"ana-{tag}@example.test", f"bo-{tag}@example.test"
    made: dict[str, Any] = {"ana": ana, "bo": bo}
    with eng.begin() as c:
        org = str(c.execute(
            text("SELECT id FROM organization ORDER BY created_at LIMIT 1")
        ).scalar_one())
        made["org"] = org
        for key in ("here", "elsewhere"):
            pid = str(c.execute(
                text(
                    "INSERT INTO pm_projects (name, status, source, created_by,"
                    " organization_id, timezone, parent_project_id, owns_statuses)"
                    " VALUES (:n,'active','manual','cap@example.test',"
                    " CAST(:o AS uuid),'UTC',NULL,true) RETURNING id"
                ),
                {"n": f"cap-{key}-{tag}", "o": org},
            ).scalar_one())
            made[key] = pid
            for name, cat, pos in (("To do", "todo", 0),
                                   ("Doing", "in_progress", 1),
                                   ("Done", "done", 2)):
                made[f"{key}:{cat}"] = str(c.execute(
                    text(
                        "INSERT INTO pm_task_statuses (project_id,name,color,"
                        " position,category) VALUES (CAST(:p AS uuid),:n,'gray',"
                        " :pos,:cat) RETURNING id"
                    ),
                    {"p": pid, "n": name, "pos": pos, "cat": cat},
                ).scalar_one())

        def task(project: str, title: str, *, cat: str = "todo",
                 who: tuple[str, ...] = (), est: int | None = None,
                 due_days: int | None = None) -> None:
            tid = str(c.execute(
                text(
                    "INSERT INTO pm_tasks (title, project_id, root_project_id,"
                    " status_id, created_by, organization_id, task_number,"
                    " estimate_mins, due_at)"
                    " SELECT :t, CAST(:p AS uuid), CAST(:p AS uuid),"
                    " CAST(:s AS uuid), 'cap@example.test', CAST(:o AS uuid),"
                    " COALESCE(MAX(task_number),0)+1, :est,"
                    " CASE WHEN CAST(:d AS int) IS NULL THEN NULL"
                    "      ELSE now() + make_interval(days => CAST(:d AS int)) END"
                    " FROM pm_tasks WHERE root_project_id = CAST(:p AS uuid)"
                    " RETURNING id"
                ),
                {"t": title, "p": made[project], "s": made[f"{project}:{cat}"],
                 "o": org, "est": est, "d": due_days},
            ).scalar_one())
            for person in who:
                c.execute(
                    text(
                        "INSERT INTO pm_task_assignees (task_id, assignee,"
                        " assigned_by) VALUES (CAST(:t AS uuid), :a,"
                        " 'cap@example.test')"
                    ),
                    {"t": tid, "a": person},
                )

        task("here", "ana sized", who=(ana,), est=120, due_days=3)
        task("here", "ana unsized", who=(ana,), cat="in_progress")
        task("here", "shared", who=(ana, bo))
        task("here", "nobody's")
        task("here", "finished", who=(ana,), cat="done", est=600)
        task("elsewhere", "ana elsewhere", who=(ana,), est=600, due_days=2)

        made["ana_id"] = str(c.execute(
            text(
                "INSERT INTO people (id, name, email, status, skills, source,"
                " source_key, organization_id, updated_by, updated_at,"
                " max_concurrent_tasks)"
                " VALUES (gen_random_uuid(), :n, :e, 'active', ARRAY[]::text[],"
                " 'manual', :k, CAST(:o AS uuid), 'test', now(), 1)"
                " RETURNING id"
            ),
            {"n": f"Ana {tag}", "e": ana, "k": f"manual:{tag}", "o": org},
        ).scalar_one())
        c.execute(
            text(
                "INSERT INTO people_skills (organization_id, person_id, skill,"
                " level) VALUES (CAST(:o AS uuid), CAST(:p AS uuid), 'CAD',"
                " 'expert'), (CAST(:o AS uuid), CAST(:p AS uuid), 'Python',"
                " 'learning')"
            ),
            {"o": org, "p": made["ana_id"]},
        )
    yield made
    with eng.begin() as c:
        ids = [made["here"], made["elsewhere"]]
        c.execute(text("DELETE FROM pm_tasks WHERE project_id = ANY(CAST(:p AS uuid[]))"),
                  {"p": ids})
        c.execute(text("DELETE FROM pm_task_statuses WHERE project_id = ANY(CAST(:p AS uuid[]))"),
                  {"p": ids})
        c.execute(text("DELETE FROM pm_projects WHERE id = ANY(CAST(:p AS uuid[]))"),
                  {"p": ids})
        c.execute(text("DELETE FROM people WHERE id = CAST(:i AS uuid)"),
                  {"i": made["ana_id"]})
    eng.dispose()


def _vis(org: str) -> Any:
    from gateway.routes.projects.core import Visibility

    return Visibility(unrestricted=True, email="", groups=(), organization_id=org)


async def _run(seeded: dict[str, Any], *, hr: bool) -> tuple[dict[str, Any], dict[str, Any]]:
    """The capacity body AND Load's own query, on one asyncpg connection."""
    from gateway.routes.projects.analytics import (
        load_open_where,
        load_params,
        load_sql,
        scope_clause,
    )
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool

    eng = create_async_engine(_async_url(), future=True, poolclass=NullPool)
    try:
        async with eng.connect() as db:
            vis = _vis(seeded["org"])
            body = await route.capacity_body(
                db, vis, hr_visible=hr, project_id=seeded["here"],
                include_subtree=True,
            )
            scope_sql = await scope_clause(db, vis, seeded["here"], True)
            where = load_open_where(scope_sql, vis)
            load_rows = (
                await db.execute(text(load_sql(where)),
                                 load_params(vis, seeded["here"]))
            ).fetchall()
    finally:
        await eng.dispose()
    return body, {str(r.who or ""): int(r.open_tasks) for r in load_rows}


@_needs_db
async def test_open_tasks_on_every_row_equals_loads_count(seeded) -> None:
    """§10.3 item 1, on a real database: same scope, same count, per person."""
    body, load = await _run(seeded, hr=True)
    by_row = {(r["assignee"] or ""): r["open_tasks"] for r in body["rows"]}
    assert set(by_row) == set(load) | {""}
    for who, count in load.items():
        assert by_row[who] == count, who
    # The fixture's own truth, so a query that returned nothing cannot pass.
    assert by_row[seeded["ana"]] == 3
    assert by_row[seeded["bo"]] == 1
    assert by_row[""] == 1
    assert body["total_tasks"] == 4


@_needs_db
async def test_the_hours_read_all_the_work_the_caller_can_see(seeded) -> None:
    """ana's ELSEWHERE task is not in this scope's count, and IS in her hours.
    A person full on another project has no spare hours for this one."""
    body, _ = await _run(seeded, hr=True)
    ana = next(r for r in body["rows"] if r["assignee"] == seeded["ana"])
    assert ana["open_tasks"] == 3
    assert ana["all_work"]["open_tasks"] == 4
    assert ana["all_work"]["in_progress"] == 1
    assert ana["hours_basis"] is True
    # 120 + 600 estimated minutes are due inside the horizon.
    assert ana["committed_hours_horizon"] == 12.0
    assert ana["max_concurrent_tasks"] == 1
    assert ana["over_concurrency"] is False
    assert ana["skills"][0] == {"skill": "CAD", "level": "expert"}


@_needs_db
async def test_without_the_grant_the_real_rows_carry_no_hr_key(seeded) -> None:
    body, load = await _run(seeded, hr=False)
    assert body["hr_visible"] is False
    for row in body["rows"]:
        assert not set(row) & set(route.HR_KEYS), row["assignee"]
    # The task half is unchanged by the grant.
    by_row = {(r["assignee"] or ""): r["open_tasks"] for r in body["rows"]}
    for who, count in load.items():
        assert by_row[who] == count


@_needs_db
async def test_a_person_the_directory_does_not_know_has_no_hours(seeded) -> None:
    """bo holds work and has no `people` row: no contracted week, so the
    hours are off and the row says why."""
    body, _ = await _run(seeded, hr=True)
    bo = next(r for r in body["rows"] if r["assignee"] == seeded["bo"])
    assert bo["in_directory"] is False
    assert bo["hours_basis"] is False
    for key in route.HOURS_KEYS:
        assert key not in bo
    assert bo["hours_note"]


@_needs_db
@pytest.mark.parametrize("hr", [True, False])
async def test_a_report_that_asks_for_capacity_renders_the_routes_body(
    seeded, monkeypatch, hr: bool,
) -> None:
    """§10.3 item 6, end to end: the report route runs the capacity body on a
    real database, and the READER's grant decides the HR half."""
    from contextlib import asynccontextmanager

    from acb_auth import UserContext, UserRole, build_access
    from gateway.routes.projects import reports as rep
    from sqlalchemy import create_engine
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool

    sync = create_engine(_TENANT_URL, future=True)
    with sync.begin() as c:
        rid = str(c.execute(
            text(
                "INSERT INTO pm_reports (project_id, organization_id, name,"
                " config, created_by) VALUES (CAST(:p AS uuid),"
                " CAST(:o AS uuid), :n,"
                " CAST('{\"sections\": [\"capacity\"]}' AS jsonb),"
                " 'cap@example.test') RETURNING id"
            ),
            {"p": seeded["here"], "o": seeded["org"],
             "n": f"cap-report-{uuid.uuid4().hex[:6]}"},
        ).scalar_one())

    eng = create_async_engine(_async_url(), future=True, poolclass=NullPool)

    @asynccontextmanager
    async def _session(*_a, **_k):
        async with eng.connect() as conn:
            yield conn

    async def _visibility(_db, _user):
        return _vis(seeded["org"])

    monkeypatch.setattr(rep, "_tenant_session", _session)
    monkeypatch.setattr(rep, "resolve_visibility", _visibility)
    grants = ["feature:projects"] + (["admin:members:read"] if hr else [])
    user = UserContext(email="cap@example.test", role=UserRole.EMPLOYEE,
                       access=build_access(grants))
    try:
        body = await rep.render_report(rid, user=user)
    finally:
        await eng.dispose()
        with sync.begin() as c:
            c.execute(text("DELETE FROM pm_reports WHERE id = CAST(:i AS uuid)"),
                      {"i": rid})
        sync.dispose()

    assert set(body["sections"]) == {"capacity"}
    section = body["sections"]["capacity"]
    assert section["hr_visible"] is hr
    assert section["total_tasks"] == 4
    ana = next(r for r in section["people"] if r["assignee"] == seeded["ana"])
    assert ana["open_tasks"] == 3
    assert section["people"][-1]["kind"] == "unassigned"
    if hr:
        assert "all_work" in ana
    else:
        for row in section["people"]:
            assert not set(row) & set(route.HR_KEYS)


# ── Review round 1: the dated window (P1 x2) ─────────────────────────────────


def test_dated_until_covers_sunday_and_the_whole_horizon() -> None:
    """ONE exclusive upper bound for the dated fetch, shared by the dashboard
    and the capacity route. It must cover this Sunday (the pill's week) AND
    the horizon's last day, which `due <= horizon_end` treats as inside."""
    # Monday, horizon 1: the week still runs to Sunday.
    assert cap.dated_until(MONDAY, 1) == MONDAY + timedelta(days=7)
    # Monday, horizon 14: the horizon's last day is inside, so +15.
    assert cap.dated_until(MONDAY, 14) == MONDAY + timedelta(days=15)
    # The dashboard's own bound, unchanged by the move.
    assert cap.dated_until(WEDNESDAY, workload.HORIZON_DAYS) == (
        WEDNESDAY + timedelta(days=workload.HORIZON_DAYS + 1)
    )


def test_the_dashboard_and_the_route_read_one_bound() -> None:
    from pathlib import Path

    root = Path(__file__).resolve().parents[2] / "apps/services/gateway/gateway"
    for rel in ("routes/people/dashboard.py", "routes/projects/analytics_capacity.py"):
        source = (root / rel).read_text(encoding="utf-8")
        assert "dated_until(" in source, f"{rel} builds its own dated bound"


@pytest.fixture
def boundary(_ladder):
    """One person with a directory row (the default 40h week) and two tasks.

    * ``week`` — 50 estimated hours due FRIDAY of this week.
    * ``edge`` — 1 estimated hour due EXACTLY on Monday + 14, the last day of
      a 14-day horizon counted from Monday.

    Dates are fixed at noon UTC so the day a task falls on does not move with
    the session time zone.
    """
    from sqlalchemy import create_engine

    today = date.today()
    monday = today - timedelta(days=today.isoweekday() - 1)
    eng = create_engine(_TENANT_URL, future=True)
    tag = uuid.uuid4().hex[:8]
    who = f"cy-{tag}@example.test"
    made: dict[str, Any] = {"who": who, "monday": monday}
    with eng.begin() as c:
        org = str(c.execute(
            text("SELECT id FROM organization ORDER BY created_at LIMIT 1")
        ).scalar_one())
        made["org"] = org
        made["project"] = pid = str(c.execute(
            text(
                "INSERT INTO pm_projects (name, status, source, created_by,"
                " organization_id, timezone, parent_project_id, owns_statuses)"
                " VALUES (:n,'active','manual','cap@example.test',"
                " CAST(:o AS uuid),'UTC',NULL,true) RETURNING id"
            ),
            {"n": f"cap-edge-{tag}", "o": org},
        ).scalar_one())
        sid = str(c.execute(
            text(
                "INSERT INTO pm_task_statuses (project_id,name,color,position,"
                " category) VALUES (CAST(:p AS uuid),'To do','gray',0,'todo')"
                " RETURNING id"
            ),
            {"p": pid},
        ).scalar_one())
        for n, (title, due, est) in enumerate((
            ("week", monday + timedelta(days=4), 3000),
            ("edge", monday + timedelta(days=14), 60),
        ), start=1):
            tid = str(c.execute(
                text(
                    "INSERT INTO pm_tasks (title, project_id, root_project_id,"
                    " status_id, created_by, organization_id, task_number,"
                    " estimate_mins, due_at) VALUES (:t, CAST(:p AS uuid),"
                    " CAST(:p AS uuid), CAST(:s AS uuid), 'cap@example.test',"
                    " CAST(:o AS uuid), :n, :est,"
                    " CAST(:due AS timestamptz)) RETURNING id"
                ),
                {"t": title, "p": pid, "s": sid, "o": org, "n": n, "est": est,
                 "due": f"{due.isoformat()}T12:00:00+00:00"},
            ).scalar_one())
            c.execute(
                text(
                    "INSERT INTO pm_task_assignees (task_id, assignee,"
                    " assigned_by) VALUES (CAST(:t AS uuid), :a,"
                    " 'cap@example.test')"
                ),
                {"t": tid, "a": who},
            )
        made["person"] = str(c.execute(
            text(
                "INSERT INTO people (id, name, email, status, skills, source,"
                " source_key, organization_id, updated_by, updated_at)"
                " VALUES (gen_random_uuid(), :n, :e, 'active', ARRAY[]::text[],"
                " 'manual', :k, CAST(:o AS uuid), 'test', now()) RETURNING id"
            ),
            {"n": f"Cy {tag}", "e": who, "k": f"manual:edge-{tag}", "o": org},
        ).scalar_one())
    yield made
    with eng.begin() as c:
        c.execute(text("DELETE FROM pm_tasks WHERE project_id = CAST(:p AS uuid)"),
                  {"p": made["project"]})
        c.execute(text("DELETE FROM pm_task_statuses WHERE project_id = CAST(:p AS uuid)"),
                  {"p": made["project"]})
        c.execute(text("DELETE FROM pm_projects WHERE id = CAST(:p AS uuid)"),
                  {"p": made["project"]})
        c.execute(text("DELETE FROM people WHERE id = CAST(:i AS uuid)"),
                  {"i": made["person"]})
    eng.dispose()


async def _edge_row(made: dict[str, Any], horizon: int) -> dict[str, Any]:
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool

    eng = create_async_engine(_async_url(), future=True, poolclass=NullPool)
    try:
        async with eng.connect() as db:
            body = await route.capacity_body(
                db, _vis(made["org"]), hr_visible=True,
                project_id=made["project"], include_subtree=True,
                horizon_days=horizon, today=made["monday"],
            )
    finally:
        await eng.dispose()
    return next(r for r in body["rows"] if r["assignee"] == made["who"])


@_needs_db
async def test_a_short_horizon_still_sees_the_whole_week_for_the_pill(boundary) -> None:
    """P1. Monday, horizon 1, 50h due Friday against a 40h week. The pill
    reads the Monday-to-Sunday week, so the fetch must reach Sunday even when
    the horizon ends on Tuesday. Bounded at the horizon, the row read `idle`."""
    row = await _edge_row(boundary, horizon=1)
    assert row["committed_hours_this_week"] == 50.0
    assert "overloaded" in row["flags"]
    assert "idle" not in row["flags"]


@_needs_db
async def test_the_horizons_last_day_is_inside_and_the_dashboard_agrees(boundary) -> None:
    """P1. A task due exactly on today + horizon is inside the horizon, for
    capacity AND for the People dashboard, on a real database."""
    from gateway.routes.people import dashboard
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool

    row = await _edge_row(boundary, horizon=14)
    assert row["committed_hours_horizon"] == 51.0, "the boundary day was dropped"

    eng = create_async_engine(_async_url(), future=True, poolclass=NullPool)
    try:
        async with eng.connect() as db:
            dated = await dashboard._dated_tasks(
                db, _vis(boundary["org"]), boundary["monday"],
            )
    finally:
        await eng.dispose()
    titles = {t["title"] for t in dated.get(boundary["who"], [])}
    assert titles == {"week", "edge"}
