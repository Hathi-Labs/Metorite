"""WS-27bm S7c — conflicts: where the plan interferes with itself.

Spec: ``project-docs/specs/projects_ai_chat.md`` §10.5, §13.5.

Two halves, as in ``test_projects_candidates.py``.

* **Hermetic** — the rules: the shared fixture through ``dependency_conflict``,
  the severity, the sort and the cap, one row for a late and misordered pair,
  ``parallel_person``'s rule 6 cases, the HR kinds against the S7b predicates
  and against ``at_risk_tasks``, the 422, and the HR gate at the route. These
  never skip.
* **R8, on a real Postgres through asyncpg** — each of the seven kinds once,
  a hidden blocker that leaves no trace, a blocker in a stopped project that
  still counts, a ``relates_to`` link and a closed blocker that give nothing,
  the project-scope ``parallel_person`` case, the window, and the HR kinds
  ABSENT without the grant.

⚠️ The R8 half SKIPS without ``TENANT_LADDER_DATABASE_URL``, and a skip is not
a pass.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import uuid
from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from fastapi import HTTPException
from gateway import conflicts as rules
from gateway.routes.projects import analytics_conflicts as route
from gateway.routes.projects import candidates
from sqlalchemy import text

REPO = Path(__file__).resolve().parents[2]
GATEWAY = REPO / "apps/services/gateway/gateway"
FIXTURE = REPO / "tests" / "fixtures" / "projects_dependency_conflicts.json"

_TENANT_URL = os.environ.get("TENANT_LADDER_DATABASE_URL", "").strip()
_needs_db = pytest.mark.skipif(
    not _TENANT_URL,
    reason=(
        "TENANT_LADDER_DATABASE_URL unset — R8 requires a REAL Postgres. A "
        "skip here is not a pass; CI must set it."
    ),
)

TODAY = date(2026, 9, 24)
HR_KIND_NAMES = ("overcommitted", "absent_on_due", "over_concurrency", "leaving")


def _cases() -> list[dict[str, Any]]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))["cases"]


# ── Hermetic: the one fixture (item 1) ───────────────────────────────────────


def test_the_fixture_is_present_and_non_trivial() -> None:
    """A fixture that quietly disappeared would leave this file asserting
    nothing while ``timeline.test.ts`` stayed green."""
    cases = _cases()
    assert len(cases) >= 10
    assert any(c["conflict"] for c in cases) and any(not c["conflict"] for c in cases)


def test_every_due_at_in_the_fixture_is_noon_utc() -> None:
    """§13.5 rule 2. At noon UTC the browser's local day and the server's UTC
    day are one day, so no case depends on the machine's time zone."""
    for case in _cases():
        for side in ("blocker", "blocked"):
            for key in ("due_at", "completed_at"):
                value = case[side].get(key)
                if value:
                    assert value.endswith("T12:00:00Z"), (case["name"], key)


@pytest.mark.parametrize("case", _cases(), ids=lambda c: c["name"])
def test_dependency_conflict_agrees_with_the_fixture(case: dict[str, Any]) -> None:
    assert rules.dependency_conflict(case["blocker"], case["blocked"]) is case["conflict"]


def test_timeline_test_reads_the_same_fixture() -> None:
    """The other runner. If it stops reading this file, the two copies of the
    rule can drift and nothing says so."""
    ts = REPO / "workbench/control_plane/src/app/projects/lib/timeline.test.ts"
    assert "tests/fixtures/projects_dependency_conflicts.json" in ts.read_text(encoding="utf-8")


def test_the_day_of_a_due_at_is_its_utc_date() -> None:
    """§13.5 rule 3. 23:30 in India on the 10th is 18:00 UTC on the 10th, and
    01:00 in India on the 11th is still the 10th in UTC."""
    ist = timezone(timedelta(hours=5, minutes=30))
    assert rules.utc_day(datetime(2026, 8, 11, 1, 0, tzinfo=ist)) == date(2026, 8, 10)
    assert rules.utc_day("2026-08-10T23:30:00+05:30") == date(2026, 8, 10)
    assert rules.utc_day(None) is None


# ── Hermetic: shape, severity, sort and cap (item 2, rule 11) ────────────────


def test_there_are_seven_kinds_and_two_severities() -> None:
    assert rules.KINDS == (
        "dependency_order", "blocker_late", "parallel_person", "overcommitted",
        "absent_on_due", "over_concurrency", "leaving",
    )
    assert set(rules.SEVERITY) == set(rules.KINDS)
    assert set(rules.SEVERITY.values()) == {"high", "medium"}
    high = {k for k, v in rules.SEVERITY.items() if v == "high"}
    assert high == {"blocker_late", "overcommitted", "absent_on_due", "leaving"}
    assert set(HR_KIND_NAMES) == rules.HR_KINDS


def test_an_unknown_kind_is_refused() -> None:
    with pytest.raises(ValueError):
        rules.conflict_row("clash", task_ids=[], people=[], sentence="", due_on=None)


def test_rows_sort_by_kind_then_by_day_stably() -> None:
    def row(kind: str, day: int | None, tag: str) -> dict[str, Any]:
        on = TODAY + timedelta(days=day) if day is not None else None
        return rules.conflict_row(kind, task_ids=[tag], people=[], sentence=tag, due_on=on)

    rows = [
        row("leaving", 1, "a"), row("dependency_order", 5, "b"),
        row("dependency_order", 2, "c"), row("over_concurrency", None, "d"),
        row("dependency_order", 2, "e"), row("blocker_late", 9, "f"),
    ]
    got = [r["task_ids"][0] for r in rules.sort_rows(rows)]
    assert got == ["c", "e", "b", "f", "d", "a"]


def test_the_response_keeps_200_rows_and_counts_them_all() -> None:
    rows = [
        rules.conflict_row("dependency_order", task_ids=[str(i)], people=[],
                           sentence="x", due_on=TODAY)
        for i in range(250)
    ]
    got = rules.summarise(rows, rules.KINDS)
    assert len(got["rows"]) == rules.MAX_ROWS == 200
    assert got["total"] == 250 and got["by_kind"]["dependency_order"] == 250
    assert got["truncated"] is True


def test_a_kind_the_caller_may_not_see_is_not_even_a_zero() -> None:
    shown = [k for k in rules.KINDS if k not in rules.HR_KINDS]
    got = rules.summarise([], shown)
    assert set(got["by_kind"]) == {"dependency_order", "blocker_late", "parallel_person"}


# ── Hermetic: the dependency kinds (item 4, rule 5) ──────────────────────────


def _pair(*, blocker: dict[str, Any], blocked: dict[str, Any], late: bool) -> dict[str, Any]:
    return {
        "blocker": {"id": "k", "title": "Order steel", "completed_at": None, **blocker},
        "blocked": {"id": "b", "title": "Weld frame", **blocked},
        "blocker_overdue": late,
    }


def test_a_late_and_misordered_pair_gives_one_blocker_late_row() -> None:
    """Owner, 2026-09-24. The late blocker is the fact to act on, and it
    implies the order problem."""
    pair = _pair(
        blocker={"start_date": "2026-09-10", "due_at": "2026-09-20T12:00:00Z"},
        blocked={"start_date": "2026-09-15", "due_at": "2026-09-30T12:00:00Z"},
        late=True,
    )
    assert rules.dependency_conflict(pair["blocker"], pair["blocked"])
    rows = route.dependency_rows([pair], {})
    assert [r["kind"] for r in rows] == ["blocker_late"]
    assert rows[0]["task_ids"] == ["b", "k"]
    assert rows[0]["severity"] == "high"


def test_a_misordered_pair_on_time_gives_dependency_order() -> None:
    pair = _pair(
        blocker={"start_date": None, "due_at": "2026-10-12T12:00:00Z"},
        blocked={"start_date": "2026-10-10", "due_at": None},
        late=False,
    )
    [row] = route.dependency_rows([pair], {})
    assert row["kind"] == "dependency_order" and row["severity"] == "medium"
    assert row["due_on"] == "2026-10-10"
    assert "Nothing has been rescheduled" in row["sentence"]


def test_a_well_ordered_pair_on_time_gives_nothing() -> None:
    pair = _pair(
        blocker={"start_date": None, "due_at": "2026-10-10T12:00:00Z"},
        blocked={"start_date": "2026-10-10", "due_at": None},
        late=False,
    )
    assert route.dependency_rows([pair], {}) == []


def test_a_completed_blocker_gives_no_dependency_order() -> None:
    pair = _pair(
        blocker={"start_date": None, "due_at": "2026-10-12T12:00:00Z",
                 "completed_at": "2026-10-01T12:00:00Z"},
        blocked={"start_date": "2026-10-10", "due_at": None},
        late=False,
    )
    assert route.dependency_rows([pair], {}) == []


def test_the_dependency_sql_reads_blocks_only_and_drops_a_closed_blocker() -> None:
    """Item 4, the text half. The R8 test below proves it on a real database."""
    sql = route.dependency_sql("TRUE", "TRUE")
    assert "l.link_type = 'blocks'" in sql
    assert "JOIN pm_tasks t ON t.id = l.target_task_id" in sql
    assert "JOIN pm_tasks k ON k.id = l.source_task_id" in sql
    assert "ks.category <> ALL(CAST(:closed AS text[]))" in sql
    assert "k.archived_at IS NULL" in sql
    # ⚠️ Rule 4: the blocker is NOT held to the reportable clause, so a
    # blocker in a stopped project still counts.
    assert "reportable" not in sql


# ── Hermetic: parallel_person (item 9, rule 6) ──────────────────────────────


def _span(tid: str, root: str, start: int, due: int, *, in_scope: bool = True) -> dict[str, Any]:
    return {
        "id": tid, "root_project_id": root, "in_scope": in_scope,
        "start_date": (TODAY + timedelta(days=start)).isoformat(),
        "due_at": f"{(TODAY + timedelta(days=due)).isoformat()}T12:00:00Z",
    }


def _busiest(tasks: list[dict[str, Any]]) -> Any:
    return rules.busiest_parallel_day(tasks, TODAY, TODAY + timedelta(days=14))


def test_two_tasks_give_no_parallel_row() -> None:
    assert _busiest([_span("a", "A", 1, 6), _span("b", "B", 1, 6)]) is None


def test_three_tasks_in_one_top_level_project_give_no_row() -> None:
    """A sub-project counts as its root, so three tasks under one root are one
    project's plan, not parallel work across projects."""
    assert _busiest([_span("a", "A", 1, 6), _span("b", "A", 1, 6), _span("c", "A", 1, 6)]) is None


def test_a_shared_end_day_gives_no_row() -> None:
    """The due day is the handover, as in the dependency rule. Two tasks end
    on the day the third starts, so on no day do all three run."""
    tasks = [_span("a", "A", 1, 5), _span("b", "B", 2, 5), _span("c", "C", 5, 9)]
    assert _busiest(tasks) is None


def test_a_task_with_one_date_does_not_count() -> None:
    tasks = [_span("a", "A", 1, 6), _span("b", "B", 1, 6)]
    tasks.append({"id": "c", "root_project_id": "C", "in_scope": True,
                  "start_date": None, "due_at": f"{(TODAY + timedelta(days=3)).isoformat()}T12:00:00Z"})
    assert _busiest(tasks) is None


def test_three_tasks_in_two_top_level_projects_give_the_busiest_day() -> None:
    tasks = [_span("a", "A", 1, 6), _span("b", "A", 2, 6), _span("c", "B", 3, 7),
             _span("d", "B", 4, 5)]
    day, found = _busiest(tasks)
    assert day == TODAY + timedelta(days=4)
    assert {t["id"] for t in found} == {"a", "b", "c", "d"}


def test_in_a_project_scope_one_in_scope_task_and_two_elsewhere_give_one_row() -> None:
    """Item 9's project-scope case. The in-scope task is listed first."""
    tasks = [
        _span("elsewhere-1", "A", 1, 6, in_scope=False),
        _span("elsewhere-2", "A", 1, 6, in_scope=False),
        _span("here", "B", 2, 6, in_scope=True),
    ]
    rows = route.parallel_rows({"bo@x.in": tasks}, {}, TODAY, TODAY + timedelta(days=14))
    assert len(rows) == 1
    assert rows[0]["task_ids"][0] == "here"
    assert rows[0]["kind"] == "parallel_person" and rows[0]["severity"] == "medium"


def test_no_row_fires_when_no_task_on_the_day_is_in_scope() -> None:
    tasks = [_span(t, r, 1, 6, in_scope=False) for t, r in (("a", "A"), ("b", "B"), ("c", "C"))]
    assert _busiest(tasks) is None


def test_a_parallel_row_names_at_most_five_tasks_and_the_total() -> None:
    tasks = [_span(f"t{i}", "A" if i % 2 else "B", 1, 6) for i in range(7)]
    [row] = route.parallel_rows({"bo@x.in": tasks}, {}, TODAY, TODAY + timedelta(days=14))
    assert len(row["task_ids"]) == 5
    assert row["tasks_total"] == 7
    assert row["day"] == (TODAY + timedelta(days=1)).isoformat()


def test_a_person_gives_at_most_one_parallel_row() -> None:
    tasks = [_span("a", "A", 1, 3), _span("b", "B", 1, 3), _span("c", "C", 1, 3),
             _span("d", "A", 8, 10), _span("e", "B", 8, 10), _span("f", "C", 8, 10)]
    assert len(route.parallel_rows({"bo@x.in": tasks}, {}, TODAY,
                                   TODAY + timedelta(days=14))) == 1


def test_a_parallel_day_outside_the_window_gives_no_row() -> None:
    tasks = [_span("a", "A", 20, 25), _span("b", "B", 20, 25), _span("c", "C", 20, 25)]
    assert _busiest(tasks) is None


# ── Hermetic: the HR kinds are the S7b predicates (item 7, rule 7) ──────────


def _helper(**over: Any) -> dict[str, Any]:
    base = {"spans": [], "end_date": None, "max_concurrent_tasks": None, "in_progress": 0}
    base.update(over)
    return base


_HELPERS = [
    _helper(),
    _helper(spans=[{"starts_on": TODAY + timedelta(days=3), "ends_on": TODAY + timedelta(days=4),
                    "kind": "away", "hours_per_day": None}]),
    _helper(spans=[{"starts_on": TODAY, "ends_on": TODAY + timedelta(days=1),
                    "kind": "partial", "hours_per_day": 4}]),
    _helper(end_date=TODAY + timedelta(days=1)),
    _helper(end_date=TODAY + timedelta(days=3)),
    _helper(max_concurrent_tasks=1, in_progress=2),
    _helper(max_concurrent_tasks=2, in_progress=2),
    _helper(end_date=TODAY + timedelta(days=1), max_concurrent_tasks=0, in_progress=1,
            spans=[{"starts_on": TODAY - timedelta(days=1), "ends_on": TODAY,
                    "kind": "holiday", "hours_per_day": None}]),
]
_DUES = [TODAY + timedelta(days=3), TODAY - timedelta(days=2), TODAY + timedelta(days=30), None]


@pytest.mark.parametrize("helper", _HELPERS)
@pytest.mark.parametrize("due", _DUES)
def test_the_hr_kinds_agree_with_candidate_warnings(helper: dict[str, Any], due: Any) -> None:
    """§10.5 item 7. For the same inputs the conflicts rows carry the kinds
    and the text the picker's warnings carry. The window is wide here, so
    only the predicates decide."""
    task = {"id": "t", "title": "Weld the frame",
            "due_at": f"{due.isoformat()}T12:00:00Z" if due else None}
    person = {"email": "ana@x.in", "name": "Ana"}
    rows = route.task_warning_rows(task, person, helper, today=TODAY,
                                   horizon_end=TODAY + timedelta(days=90))
    over = route.concurrency_row(person, helper, [])
    got = [(r["kind"], r["sentence"]) for r in rows + ([over] if over else [])]
    want = candidates.warning_kinds(helper, due, today=TODAY)
    assert [k for k, _ in got] == [k for k, _ in want]
    for (_, sentence), (_, said) in zip(got, want, strict=True):
        assert sentence.endswith(f"{said}.")
    assert [w for _, w in want] == candidates.candidate_warnings(helper, due, today=TODAY)


def test_a_task_due_after_the_horizon_gives_no_dated_hr_row() -> None:
    helper = _helper(end_date=TODAY + timedelta(days=1))
    task = {"id": "t", "title": "x", "due_at": f"{(TODAY + timedelta(days=30)).isoformat()}T12:00:00Z"}
    rows = route.task_warning_rows(task, {"email": "a@x.in", "name": None}, helper,
                                   today=TODAY, horizon_end=TODAY + timedelta(days=14))
    assert rows == []


def test_an_overdue_task_is_always_in_the_window() -> None:
    """§13.5 rule 10. Rule 7 checks an overdue task TODAY."""
    helper = _helper(spans=[{"starts_on": TODAY, "ends_on": TODAY, "kind": "away",
                             "hours_per_day": None}])
    task = {"id": "t", "title": "x", "due_at": f"{(TODAY - timedelta(days=5)).isoformat()}T12:00:00Z"}
    [row] = route.task_warning_rows(task, {"email": "a@x.in", "name": "A"}, helper,
                                    today=TODAY, horizon_end=TODAY + timedelta(days=1))
    assert row["kind"] == "absent_on_due"
    assert "already overdue" in row["sentence"]


def test_overcommitted_rows_are_at_risk_tasks_filtered_to_the_scope() -> None:
    """§10.5 item 8. The rows are the S7a walk's own output, kept to the
    scope, in the walk's order, with the walk's shortfall."""
    from gateway.workload import at_risk_tasks

    schedule = {"hours_per_day": 8, "days": [1, 2, 3, 4, 5]}
    tasks = [
        {"id": f"t{i}", "title": f"Task {i}", "estimate_mins": 60 * 20,
         "due_at": datetime.combine(TODAY + timedelta(days=i), datetime.min.time(), tzinfo=UTC)}
        for i in range(1, 6)
    ]
    risky = at_risk_tasks(schedule, tasks, [], TODAY, horizon_days=14)
    assert len(risky) >= 2, "the case must hold more than one at-risk task"
    in_scope = {"t2", "t4", "t5"}
    rows = route.overcommitted_rows({"email": "cy@x.in", "name": "Cy"}, risky, in_scope)
    want = [r for r in risky if r["task_id"] in in_scope]
    assert [r["task_ids"] for r in rows] == [[r["task_id"]] for r in want]
    assert [r["shortfall_hours"] for r in rows] == [r["shortfall_hours"] for r in want]
    assert all("other projects" in r["sentence"] for r in rows)


# ── Hermetic: the route (items 6 and 10) ─────────────────────────────────────


@pytest.mark.parametrize("bad", [0, 91, -1, "soon"])
def test_a_horizon_outside_1_to_90_is_422_before_a_session(bad: Any, monkeypatch) -> None:
    def _session(*_a, **_k):
        raise AssertionError("a session opened for a refused horizon")

    monkeypatch.setattr(route, "_tenant_session", _session)
    with pytest.raises(HTTPException) as err:
        asyncio.run(route.conflicts(horizon_days=bad, user=SimpleNamespace()))
    assert err.value.status_code == 422


def test_the_route_passes_the_callers_grant_not_a_constant(monkeypatch) -> None:
    """R7 fence for rule 9. Hard-coding ``hr_visible=True`` fails this."""
    from contextlib import asynccontextmanager

    from acb_auth import UserContext, UserRole, build_access

    seen: list[bool] = []

    @asynccontextmanager
    async def _session(*_a, **_k):
        yield None

    async def _vis(_db, _user):
        return SimpleNamespace(unrestricted=True)

    async def _body(_db, _vis, *, hr_visible, **_k):
        seen.append(hr_visible)
        return {}

    monkeypatch.setattr(route, "_tenant_session", _session)
    monkeypatch.setattr(route, "resolve_visibility", _vis)
    monkeypatch.setattr(route, "conflicts_body", _body)
    for grants, want in ((["feature:projects"], False),
                         (["feature:projects", "admin:members:read"], True)):
        user = UserContext(email="c@example.test", role=UserRole.EMPLOYEE,
                           access=build_access(grants))
        asyncio.run(route.conflicts(user=user))
        assert seen[-1] is want


def test_the_route_is_mounted() -> None:
    from gateway.routes.projects import router

    paths = {getattr(r, "path", "") for r in router.routes}
    assert "/projects/analytics/conflicts" in paths


def test_the_conflicts_modules_never_write() -> None:
    from tests.unit.test_people_dashboard import _strip_prose

    for rel in ("routes/projects/analytics_conflicts.py", "conflicts.py"):
        code = _strip_prose((GATEWAY / rel).read_text(encoding="utf-8"))
        for verb in ("INSERT", "UPDATE", "DELETE"):
            assert not re.search(rf"\b{verb}\b", code), (rel, verb)


def test_the_leaf_module_opens_no_session() -> None:
    """R5. The rules are pure; only the route acquires a session."""
    code = (GATEWAY / "conflicts.py").read_text(encoding="utf-8")
    for name in ("_tenant_session", "get_db", "create_async_engine", "sqlalchemy"):
        assert name not in code, name


# ── R8: a real Postgres, through asyncpg ─────────────────────────────────────


def _async_url() -> str:
    url = _TENANT_URL
    if "postgresql+psycopg" in url:
        return url.replace("postgresql+psycopg", "postgresql+asyncpg")
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return url


def _noon_utc(days: int | None) -> str | None:
    """A due date ``days`` after the process's own ``date.today()``, at noon
    UTC. Counted from the process date, as the route counts, never from the
    database's ``now()`` (the S7b lesson)."""
    if days is None:
        return None
    return f"{(date.today() + timedelta(days=days)).isoformat()}T12:00:00+00:00"


def _day(days: int | None) -> str | None:
    return None if days is None else (date.today() + timedelta(days=days)).isoformat()


@pytest.fixture(scope="module")
def _ladder():
    """Apply the tenant ladder ONCE for this module (the S7a lesson)."""
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
    """Six projects, five people, and one task or link for each case.

    Projects: ``A`` (with a sub-project ``A1``), ``B`` and ``C`` are active
    roots. ``S`` is stopped. ``H`` is active, and the restricted viewer holds
    a grant on ``A`` only.

    * ``T1`` (A) is blocked by ``K1`` (A, due +5): misordered, on time →
      ``dependency_order``. ``K4`` relates to ``T1`` and ``K5`` (closed) blocks
      it, with dates that would conflict: neither gives a row.
    * ``T2`` (A) is blocked by ``K2`` (A, due -1): late AND misordered → one
      ``blocker_late``.
    * ``T4`` (A) is blocked by ``K6`` in the STOPPED project → a row.
    * ``T5`` (A) is blocked by ``KH`` in ``H`` → a row, unless the viewer
      cannot see ``H``.
    * ``bo`` holds P1 (A), P2 (A1) and P3 (B), all running on day +3 →
      ``parallel_person``.
    * ``cy`` holds O1 (A, 50h due +2) and O2 (B, 40h due +3) →
      ``overcommitted``.
    * ``ana`` has two tasks in progress over a ceiling of 1, leaves on +2 and
      is away +4..+5. AN1 is due +4 → ``absent_on_due``, ``leaving``,
      ``over_concurrency``. AN3 is due +30, outside a 14-day horizon.
    * ``dan`` is away today, and DN1 is overdue → ``absent_on_due``.
    """
    from sqlalchemy import create_engine

    eng = create_engine(_TENANT_URL, future=True)
    tag = uuid.uuid4().hex[:8]
    made: dict[str, Any] = {"tag": tag, "viewer": f"viewer-{tag}@example.test"}
    with eng.begin() as c:
        org = str(c.execute(
            text("SELECT id FROM organization ORDER BY created_at LIMIT 1")
        ).scalar_one())
        made["org"] = org

        def project(key: str, status: str = "active", parent: str | None = None) -> None:
            made[key] = str(c.execute(
                text(
                    "INSERT INTO pm_projects (name, status, source, created_by,"
                    " organization_id, timezone, parent_project_id, owns_statuses)"
                    " VALUES (:n, :st, 'manual', 'cf@example.test', CAST(:o AS uuid),"
                    " 'UTC', CAST(:par AS uuid), :owns) RETURNING id"
                ),
                {"n": f"cf-{key}-{tag}", "st": status, "o": org,
                 "par": made[parent] if parent else None, "owns": parent is None},
            ).scalar_one())

        def status(root: str, name: str, category: str, pos: int) -> None:
            made[f"{root}:{category}"] = str(c.execute(
                text(
                    "INSERT INTO pm_task_statuses (project_id, name, color, position,"
                    " category) VALUES (CAST(:p AS uuid), :n, 'gray', :pos, :cat)"
                    " RETURNING id"
                ),
                {"p": made[root], "n": name, "pos": pos, "cat": category},
            ).scalar_one())

        for key in ("A", "B", "C", "H"):
            project(key)
        project("S", "stopped")
        project("A1", parent="A")
        for root in ("A", "B", "C", "H", "S"):
            status(root, "To do", "todo", 0)
        status("A", "Doing", "in_progress", 1)
        status("A", "Done", "done", 2)
        c.execute(
            text(
                "INSERT INTO pm_project_grants (project_id, subject, created_by,"
                " organization_id) VALUES (CAST(:p AS uuid), :s, 'cf@example.test',"
                " CAST(:o AS uuid))"
            ),
            {"p": made["A"], "s": made["viewer"], "o": org},
        )

        def person(key: str, *, end: int | None = None, ceiling: int | None = None) -> None:
            made[key] = f"{key}-{tag}@example.test"
            made[f"{key}_id"] = str(c.execute(
                text(
                    "INSERT INTO people (id, name, email, status, skills, source,"
                    " source_key, organization_id, updated_by, updated_at, end_date,"
                    " max_concurrent_tasks)"
                    " VALUES (gen_random_uuid(), :n, :e, 'active', ARRAY[]::text[],"
                    " 'manual', :k, CAST(:o AS uuid), 'test', now(),"
                    " CAST(:end AS date), :ceiling) RETURNING id"
                ),
                {"n": f"{key.title()} {tag}", "e": made[key], "k": f"manual:cf-{key}-{tag}",
                 "o": org, "end": _day(end), "ceiling": ceiling},
            ).scalar_one())

        def away(key: str, start: int, end: int, kind: str = "away") -> None:
            c.execute(
                text(
                    "INSERT INTO people_absences (organization_id, person_id, starts_on,"
                    " ends_on, kind, created_by) VALUES (CAST(:o AS uuid),"
                    " CAST(:p AS uuid), CAST(:s AS date), CAST(:e AS date), :k,"
                    " 'cf@example.test')"
                ),
                {"o": org, "p": made[f"{key}_id"], "s": _day(start), "e": _day(end), "k": kind},
            )

        def task(key: str, where: str, *, root: str | None = None, lane: str = "todo",
                 start: int | None = None, due: int | None = None,
                 est: int | None = None, who: tuple[str, ...] = ()) -> None:
            root = root or where
            made[key] = str(c.execute(
                text(
                    "INSERT INTO pm_tasks (title, project_id, root_project_id,"
                    " status_id, created_by, organization_id, task_number,"
                    " estimate_mins, start_date, due_at)"
                    " SELECT :t, CAST(:p AS uuid), CAST(:r AS uuid), CAST(:s AS uuid),"
                    " 'cf@example.test', CAST(:o AS uuid),"
                    " COALESCE(MAX(task_number),0)+1, :est, CAST(:start AS date),"
                    " CAST(:due AS timestamptz)"
                    " FROM pm_tasks WHERE root_project_id = CAST(:r AS uuid)"
                    " RETURNING id"
                ),
                {"t": f"{key} {tag}", "p": made[where], "r": made[root],
                 "s": made[f"{root}:{lane}"], "o": org, "est": est,
                 "start": _day(start), "due": _noon_utc(due)},
            ).scalar_one())
            for w in who:
                c.execute(
                    text(
                        "INSERT INTO pm_task_assignees (task_id, assignee, assigned_by)"
                        " VALUES (CAST(:t AS uuid), :a, 'cf@example.test')"
                    ),
                    {"t": made[key], "a": made[w]},
                )

        def link(source: str, target: str, kind: str = "blocks") -> None:
            c.execute(
                text(
                    "INSERT INTO pm_task_links (source_task_id, target_task_id,"
                    " link_type, created_by) VALUES (CAST(:s AS uuid),"
                    " CAST(:t AS uuid), :k, 'cf@example.test')"
                ),
                {"s": made[source], "t": made[target], "k": kind},
            )

        person("bo")
        person("cy")
        person("ana", end=2, ceiling=1)
        person("dan")
        away("ana", 4, 5)
        away("dan", 0, 1, "holiday")

        # The dependency kinds.
        task("T1", "A", start=3, due=8)
        task("K1", "A", due=5)
        task("K4", "A", due=9)
        task("K5", "A", lane="done", due=9)
        link("K1", "T1")
        link("K4", "T1", "relates_to")
        link("K4", "T1", "duplicates")
        link("K5", "T1")
        task("T2", "A", start=-5, due=3)
        task("K2", "A", start=-10, due=-1)
        link("K2", "T2")
        task("T4", "A", start=2, due=12)
        task("K6", "S", due=9)
        link("K6", "T4")
        task("T5", "A", start=2, due=12)
        task("KH", "H", due=9)
        link("KH", "T5")

        # parallel_person — bo, across A (with A1 under it) and B.
        task("P1", "A", start=1, due=6, who=("bo",))
        task("P2", "A1", root="A", start=2, due=6, who=("bo",))
        task("P3", "B", start=3, due=7, who=("bo",))

        # overcommitted — cy.
        task("O1", "A", due=2, est=3000, who=("cy",))
        task("O2", "B", due=3, est=2400, who=("cy",))

        # The other HR kinds — ana and dan.
        task("AN1", "A", lane="in_progress", due=4, who=("ana",))
        task("AN2", "A", lane="in_progress", who=("ana",))
        task("AN3", "A", due=30, who=("ana",))
        task("DN1", "A", due=-3, who=("dan",))
    yield made
    with eng.begin() as c:
        ids = [made[k] for k in ("A1", "A", "B", "C", "H", "S")]
        c.execute(text("DELETE FROM pm_tasks WHERE project_id = ANY(CAST(:p AS uuid[]))"),
                  {"p": ids})
        c.execute(text("DELETE FROM pm_task_statuses WHERE project_id = ANY(CAST(:p AS uuid[]))"),
                  {"p": ids})
        c.execute(text("DELETE FROM pm_project_grants WHERE project_id = ANY(CAST(:p AS uuid[]))"),
                  {"p": ids})
        for key in ("A1", "A", "B", "C", "H", "S"):
            c.execute(text("DELETE FROM pm_projects WHERE id = CAST(:p AS uuid)"),
                      {"p": made[key]})
        c.execute(text("DELETE FROM people WHERE id = ANY(CAST(:i AS uuid[]))"),
                  {"i": [made[f"{k}_id"] for k in ("bo", "cy", "ana", "dan")]})
    eng.dispose()


def _vis(seeded: dict[str, Any], *, restricted: bool) -> Any:
    from gateway.routes.projects.core import Visibility

    if restricted:
        return Visibility(unrestricted=False, email=seeded["viewer"], groups=(),
                          organization_id=seeded["org"])
    return Visibility(unrestricted=True, email="", groups=(), organization_id=seeded["org"])


async def _body(seeded: dict[str, Any], *, restricted: bool = False, hr: bool = True,
                project: str | None = None, horizon: int = 14) -> dict[str, Any]:
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool

    eng = create_async_engine(_async_url(), future=True, poolclass=NullPool)
    try:
        async with eng.connect() as db:
            return await route.conflicts_body(
                db, _vis(seeded, restricted=restricted), hr_visible=hr,
                project_id=seeded[project] if project else None,
                include_subtree=True, horizon_days=horizon,
            )
    finally:
        await eng.dispose()


def _rows(body: dict[str, Any], kind: str, *ids: str) -> list[dict[str, Any]]:
    """The rows of one kind that name every id given."""
    return [r for r in body["rows"] if r["kind"] == kind and all(i in r["task_ids"] for i in ids)]


def _mine(body: dict[str, Any], seeded: dict[str, Any]) -> list[dict[str, Any]]:
    """The rows this fixture built. Other suites may leave work in the tenant."""
    ours = {v for k, v in seeded.items() if isinstance(v, str) and len(v) == 36}
    return [r for r in body["rows"] if set(r["task_ids"]) & ours]


@_needs_db
async def test_each_of_the_seven_kinds_is_built_once_on_a_real_database(seeded) -> None:
    """§10.5 item 2. Every kind appears for the fixture, and no row carries a
    kind outside the seven."""
    s = seeded
    body = await _body(s, project="A")
    assert {r["kind"] for r in body["rows"]} <= set(rules.KINDS)
    assert _rows(body, "dependency_order", s["T1"], s["K1"])
    assert _rows(body, "blocker_late", s["T2"], s["K2"])
    assert _rows(body, "parallel_person", s["P1"], s["P2"], s["P3"])
    assert _rows(body, "overcommitted", s["O1"])
    assert _rows(body, "absent_on_due", s["AN1"])
    assert _rows(body, "leaving", s["AN1"])
    assert _rows(body, "over_concurrency", s["AN1"], s["AN2"])
    kinds = {r["kind"] for r in _mine(body, s)}
    assert kinds == set(rules.KINDS)
    for row in body["rows"]:
        assert set(row) >= {"kind", "task_ids", "people", "sentence", "severity"}
        assert row["severity"] == rules.SEVERITY[row["kind"]]


@_needs_db
async def test_a_hidden_blocker_gives_no_row_and_leaves_no_trace(seeded) -> None:
    """§10.5 item 3. The viewer holds a grant on A and none on H. The row
    for ``T5`` needs ``KH``, so it is gone, and neither ``KH``'s id nor its
    title appears anywhere in the body."""
    s = seeded
    seen = await _body(s, restricted=False, project="A")
    assert _rows(seen, "dependency_order", s["T5"], s["KH"]), "the fixture must hold the row"
    body = await _body(s, restricted=True, project="A")
    raw = json.dumps(body)
    assert s["KH"] not in raw
    assert f"KH {s['tag']}" not in raw
    assert not [r for r in body["rows"] if s["T5"] in r["task_ids"]]
    # The rest of A is still there: the grant test measures the grant.
    assert _rows(body, "dependency_order", s["T1"], s["K1"])


@_needs_db
async def test_relates_to_duplicates_and_a_closed_blocker_give_nothing(seeded) -> None:
    """§10.5 item 4, on a real database. K4 relates to and duplicates T1,
    and K5 is done. Both have dates that would conflict."""
    s = seeded
    body = await _body(s)
    raw = json.dumps(body["rows"])
    assert s["K4"] not in raw
    assert s["K5"] not in raw


@_needs_db
async def test_a_late_misordered_pair_gives_one_row(seeded) -> None:
    s = seeded
    body = await _body(s)
    named = [r for r in body["rows"] if s["K2"] in r["task_ids"]]
    assert [r["kind"] for r in named] == ["blocker_late"]


@_needs_db
async def test_a_blocker_in_a_stopped_project_still_gives_a_row(seeded) -> None:
    """§10.5 item 5. Owner, 2026-09-24: the blocked task still waits on work
    that nobody is doing."""
    s = seeded
    body = await _body(s, project="A")
    assert _rows(body, "dependency_order", s["T4"], s["K6"])


@_needs_db
async def test_without_the_hr_grant_the_four_hr_kinds_are_absent(seeded) -> None:
    """§10.5 item 6. Not empty: absent, from the rows, from ``by_kind`` and
    from ``kinds``."""
    s = seeded
    body = await _body(s, hr=False, project="A")
    assert body["hr_visible"] is False
    raw = json.dumps(body)
    for kind in HR_KIND_NAMES:
        assert kind not in raw, kind
    assert set(body["by_kind"]) == {"dependency_order", "blocker_late", "parallel_person"}
    assert _rows(body, "dependency_order", s["T1"], s["K1"]), "the task kinds stay"


@_needs_db
async def test_the_route_without_the_grant_carries_no_hr_kind(seeded, monkeypatch) -> None:
    """The same, through the route's own grant check on a real database."""
    from contextlib import asynccontextmanager

    from acb_auth import UserContext, UserRole, build_access
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool

    eng = create_async_engine(_async_url(), future=True, poolclass=NullPool)

    @asynccontextmanager
    async def _session(*_a, **_k):
        async with eng.connect() as conn:
            yield conn

    async def _resolve(_db, _user):
        return _vis(seeded, restricted=False)

    monkeypatch.setattr(route, "_tenant_session", _session)
    monkeypatch.setattr(route, "resolve_visibility", _resolve)
    try:
        user = UserContext(email="cf@example.test", role=UserRole.EMPLOYEE,
                           access=build_access(["feature:projects"]))
        body = await route.conflicts(project_id=seeded["A"], user=user)
    finally:
        await eng.dispose()
    assert body["hr_visible"] is False
    for kind in HR_KIND_NAMES:
        assert kind not in json.dumps(body), kind


@_needs_db
async def test_the_hr_kinds_follow_the_predicates_on_a_real_database(seeded) -> None:
    """§10.5 item 7 on real rows. The sentence carries the picker's text."""
    s = seeded
    body = await _body(s, project="A")
    [away] = _rows(body, "absent_on_due", s["AN1"])
    assert "Away (away) on the due date" in away["sentence"]
    [late] = _rows(body, "absent_on_due", s["DN1"])
    assert "today, and the task is already overdue" in late["sentence"]
    [leaving] = _rows(body, "leaving", s["AN1"])
    assert "Engagement ends" in leaving["sentence"]
    [over] = _rows(body, "over_concurrency", s["AN1"])
    assert "2 tasks in progress, over the limit of 1" in over["sentence"]


@_needs_db
async def test_overcommitted_equals_the_capacity_walk_filtered_to_the_scope(seeded) -> None:
    """§10.5 item 8. The capacity route's at-risk list for cy, kept to scope
    A, is the ``overcommitted`` rows for cy — same tasks, same shortfall."""
    from gateway.routes.projects.analytics_capacity import capacity_body
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool

    s = seeded
    eng = create_async_engine(_async_url(), future=True, poolclass=NullPool)
    try:
        async with eng.connect() as db:
            cap = await capacity_body(
                db, _vis(s, restricted=False), hr_visible=True,
                project_id=s["A"], include_subtree=True, horizon_days=14,
            )
    finally:
        await eng.dispose()
    [row] = [r for r in cap["rows"] if r["assignee"] == s["cy"]]
    in_a = {s[k] for k in ("O1",)}
    want = [(t["task_id"], t["shortfall_hours"]) for t in row["at_risk"] if t["task_id"] in in_a]
    assert want, "cy must be at risk in A"
    body = await _body(s, project="A")
    got = [(r["task_ids"][0], r["shortfall_hours"]) for r in body["rows"]
           if r["kind"] == "overcommitted" and r["people"][0]["email"] == s["cy"]]
    assert got == want
    # O2 is at risk too, in B. The portfolio has it and scope A does not.
    portfolio = await _body(s)
    assert _rows(portfolio, "overcommitted", s["O2"])
    assert not _rows(body, "overcommitted", s["O2"])


@_needs_db
async def test_parallel_person_in_a_project_scope_lists_the_in_scope_task_first(seeded) -> None:
    """§10.5 item 9's project-scope case, on a real database. In scope B, bo
    holds one task (P3) and two visible tasks in A. One row, P3 first, and
    the sub-project task counts under its root."""
    s = seeded
    body = await _body(s, project="B")
    [row] = [r for r in body["rows"] if r["kind"] == "parallel_person"
             and r["people"][0]["email"] == s["bo"]]
    assert row["task_ids"][0] == s["P3"]
    assert set(row["task_ids"]) == {s["P1"], s["P2"], s["P3"]}
    assert row["tasks_total"] == 3
    assert row["day"] == _day(3)
    assert "2 top-level projects" in row["sentence"]


@_needs_db
async def test_parallel_person_needs_the_third_task_to_be_visible(seeded) -> None:
    """The restricted viewer cannot see B, so bo holds two visible tasks."""
    s = seeded
    body = await _body(s, restricted=True, project="A")
    assert not [r for r in body["rows"] if r["kind"] == "parallel_person"
                and r["people"][0]["email"] == s["bo"]]


@_needs_db
async def test_the_horizon_bounds_the_dated_kinds_and_is_printed(seeded) -> None:
    """§10.5 item 10. AN3 is due in 30 days: outside 14, inside 40."""
    s = seeded
    short = await _body(s, project="A", horizon=14)
    assert short["window"]["starts_on"] == date.today().isoformat()
    assert short["window"]["ends_on"] == _day(14)
    assert short["window"]["days"] == 14
    assert not _rows(short, "leaving", s["AN3"])
    long = await _body(s, project="A", horizon=40)
    assert _rows(long, "leaving", s["AN3"])
    # The dependency kinds ignore the window.
    one = await _body(s, project="A", horizon=1)
    assert _rows(one, "dependency_order", s["T4"], s["K6"])
