"""WS-28c — the org chart endpoint (§5.4).

The tree and its cycle guard live in the CLIENT (`lib/chart.ts`, vitest); this
side owns the flat list: directory-tier fields only, alumni off the chart, a
manager who left resolving to "no manager" (a root, §5.4's honest defect), the
group overlay joined through `app_user`, and no write anywhere in the module —
re-parenting goes through the ordinary person PATCH.
"""

from __future__ import annotations

import asyncio
import re
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from acb_auth import UserContext, UserRole, build_access
from gateway.routes.people import chart as chart_mod
from tests.unit._sql_match import hits

REPO = Path(__file__).resolve().parents[2]
SOURCE = (REPO / "apps/services/gateway/gateway/routes/people/chart.py"
          ).read_text(encoding="utf-8")


def run(coro):
    return asyncio.run(coro)


def _user(*grants: str) -> UserContext:
    return UserContext(email="member@fracktal.in", role=UserRole.EMPLOYEE,
                       access=build_access(list(grants)))


MEMBER = _user("feature:people")
ADMIN = _user("feature:people", "admin:members:manage")


def person(pid: str, name: str, **over: Any) -> SimpleNamespace:
    base = dict(id=pid, name=name, title=None, department=None, team=None,
                avatar=None, email=f"{pid}@x.invalid", status="active",
                manager_id=None)
    base.update(over)
    return SimpleNamespace(**base)


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return list(self._rows)


class FakeDB:
    def __init__(self, *, people=(), groups=(), members=()):
        self.people = list(people)
        self.groups = list(groups)
        self.members = list(members)

    async def execute(self, sql: Any, params: dict | None = None) -> _Result:
        s = " ".join(str(sql).split())
        if hits(s, "FROM people"):
            # NULL must stay on the chart: bare `status <> 'alumni'` is NULL
            # for a NULL status and silently drops the row.
            assert "status IS NULL OR status <> 'alumni'" in s
            return _Result(self.people)
        if "FROM org_group_member" in s or " FROM org_group " in s:
            # ⚠️ org_group is EXEMPT from generated RLS — the tenant predicate
            # must be explicit or the legend lists every customer's groups.
            assert "current_setting('app.tenant_id'" in s
        if "FROM org_group_member" in s:
            return _Result(self.members)
        if "FROM org_group" in s:
            return _Result(self.groups)
        return _Result([])


def bind(monkeypatch, db: FakeDB) -> None:
    @asynccontextmanager
    async def _tenant_session(organization_id: str | None = None):
        yield db

    monkeypatch.setattr(chart_mod, "_tenant_session", _tenant_session,
                        raising=False)


def test_a_manager_off_the_chart_means_root(monkeypatch) -> None:
    """The alumni manager: §5.10 lists the defect, the chart SHOWS it — the
    orphan surfaces as a root rather than pointing at a node that is not
    there."""
    bind(monkeypatch, FakeDB(people=[
        person("a", "Asha"),
        person("b", "Priya", manager_id="a"),
        person("c", "Dev", manager_id="gone-alumni-id"),
    ]))
    out = run(chart_mod.get_chart(user=MEMBER))
    by_id = {n.id: n for n in out.nodes}
    assert by_id["b"].manager_id == "a"
    assert by_id["c"].manager_id is None


def test_the_overlay_joins_groups_on_lowered_email(monkeypatch) -> None:
    bind(monkeypatch, FakeDB(
        people=[person("a", "Asha", email="Asha@X.invalid")],
        groups=[SimpleNamespace(id="g1", slug="sales", display_name="Sales")],
        members=[SimpleNamespace(slug="sales", email="asha@x.invalid")]))
    out = run(chart_mod.get_chart(user=MEMBER))
    assert out.nodes[0].groups == ["sales"]
    assert [g.slug for g in out.groups] == ["sales"]


def test_can_manage_reflects_the_admin_write_class(monkeypatch) -> None:
    bind(monkeypatch, FakeDB())
    assert run(chart_mod.get_chart(user=MEMBER)).can_manage is False
    assert run(chart_mod.get_chart(user=ADMIN)).can_manage is True


def test_no_hr_fields_on_a_node() -> None:
    """Directory tier (§4.2 D): a chart node may not grow skills, hours or
    capacity — those are the HR tier's, behind `admin:members:read`."""
    fields = set(chart_mod.ChartNode.model_fields)
    assert fields == {"id", "name", "title", "department", "team", "avatar",
                      "email", "status", "manager_id", "groups"}


def test_the_chart_never_writes() -> None:
    """Re-parenting is the ordinary PATCH's job; this surface only reads."""
    from tests.unit.test_people_dashboard import _strip_prose

    code = _strip_prose(SOURCE)
    for verb in ("INSERT", "UPDATE", "DELETE"):
        assert not re.search(rf"\b{verb}\b", code), verb


def test_no_ranking_of_people_on_the_chart() -> None:
    from tests.unit.test_people_dashboard import _PERFORMANCE, _strip_prose

    assert not _PERFORMANCE.findall(_strip_prose(SOURCE))
