"""WS-28l — the Center landing rollup (§5.9).

One claim, three fences: **a projection, never a second count.** The load half
is the dashboard's own rollup by identity; the quality half is §5.10's
``collect`` by identity; and the only SQL this module runs itself is the
headcount GROUP BY over ``people`` — the one figure no other surface
computes.
"""

from __future__ import annotations

import asyncio
import re
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from acb_auth import UserContext, UserRole, build_access
from fastapi import HTTPException
from gateway.routes.people import dashboard as people_dashboard
from gateway.routes.people import overview as overview_mod
from gateway.routes.people import quality as quality_mod

REPO = Path(__file__).resolve().parents[2]
SOURCE = (REPO / "apps/services/gateway/gateway/routes/people/overview.py"
          ).read_text(encoding="utf-8")


def run(coro):
    return asyncio.run(coro)


def _user(*grants: str) -> UserContext:
    return UserContext(email="hr@fracktal.in", role=UserRole.EMPLOYEE,
                       access=build_access(list(grants)))


HR = _user("feature:people", "admin:members:read")
COLLEAGUE = _user("feature:people")

BOARD = SimpleNamespace(
    departments=[{"department": "R&D", "strain": 0.5}],
    org={"department": "Whole org", "away": ["Priya"], "headcount": 4},
    partial=True, work_visible=True)

QUALITY = SimpleNamespace(
    counts={"no_email": 2, "no_manager": 1},
    quality=SimpleNamespace(no_manager=[
        SimpleNamespace(model_dump=lambda: {"id": "boss", "name": "Asha"})]))


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return list(self._rows)


class FakeDB:
    def __init__(self, counted=()):
        self.counted = list(counted)
        self.statements: list[str] = []

    async def execute(self, sql: Any, params: dict | None = None) -> _Result:
        s = " ".join(str(sql).split())
        self.statements.append(s)
        return _Result(self.counted)


def bind(monkeypatch, db: FakeDB) -> None:
    @asynccontextmanager
    async def _tenant_session(organization_id: str | None = None):
        yield db

    monkeypatch.setattr(overview_mod, "_tenant_session", _tenant_session,
                        raising=False)

    async def _board(user):
        return BOARD

    async def _collect(_db, _user):
        return QUALITY

    monkeypatch.setattr(overview_mod, "get_dashboard", _board)
    monkeypatch.setattr(overview_mod, "collect", _collect)


def hc(dept: str, status: str, n: int) -> SimpleNamespace:
    return SimpleNamespace(department=dept, status=status, count=n)


def test_the_whole_surface_is_gated(monkeypatch) -> None:
    bind(monkeypatch, FakeDB())
    with pytest.raises(HTTPException) as exc:
        run(overview_mod.get_overview(user=COLLEAGUE))
    assert exc.value.status_code == 403


def test_the_load_half_is_the_dashboard_rollup_by_identity() -> None:
    """Un-monkeypatched, the names ARE the other modules' functions — the §5.9
    'projection, not new arithmetic' guarantee as an assertion."""
    assert overview_mod.get_dashboard is people_dashboard.get_dashboard
    assert overview_mod.collect is quality_mod.collect


def test_rollup_and_quality_pass_through_verbatim(monkeypatch) -> None:
    bind(monkeypatch, FakeDB([hc("R&D", "active", 3), hc("R&D", "alumni", 1)]))
    out = run(overview_mod.get_overview(user=HR))
    assert out.departments == BOARD.departments
    assert out.org == BOARD.org
    assert out.quality_counts == QUALITY.counts
    assert out.roots == [{"id": "boss", "name": "Asha"}]
    assert out.partial is True


def test_headcount_counts_alumni_too(monkeypatch) -> None:
    """The workload dashboard excludes alumni by design; a HEADCOUNT that did
    would say the company never loses anybody."""
    bind(monkeypatch, FakeDB([hc("R&D", "active", 3), hc("R&D", "alumni", 1)]))
    out = run(overview_mod.get_overview(user=HR))
    assert out.total_people == 4
    assert {(r.department, r.status, r.count) for r in out.headcount} == {
        ("R&D", "active", 3), ("R&D", "alumni", 1)}


def test_the_only_query_here_is_the_headcount(monkeypatch) -> None:
    db = FakeDB([hc("R&D", "active", 3)])
    bind(monkeypatch, db)
    run(overview_mod.get_overview(user=HR))
    assert len(db.statements) == 1
    assert "FROM people" in db.statements[0]
    assert "GROUP BY" in db.statements[0]


def test_no_second_count_in_the_source() -> None:
    """Structurally: the module never touches the Projects tables and contains
    exactly one SELECT — over ``people``. The cheapest way to reintroduce
    a second arithmetic is a convenience query; this makes it a red test."""
    from tests.unit.test_people_dashboard import _strip_prose

    code = _strip_prose(SOURCE)
    assert "pm_tasks" not in code
    assert len(re.findall(r"\bSELECT\b", code)) == 1


def test_no_ranking_of_people_on_the_landing() -> None:
    from tests.unit.test_people_dashboard import _PERFORMANCE, _strip_prose

    assert not _PERFORMANCE.findall(_strip_prose(SOURCE))


def test_the_landing_never_writes() -> None:
    from tests.unit.test_people_dashboard import _strip_prose

    code = _strip_prose(SOURCE)
    for verb in ("INSERT", "UPDATE", "DELETE"):
        assert not re.search(rf"\b{verb}\b", code), verb
