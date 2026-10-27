"""WS-64 OP-11 — one server definition of "mine", hermetic half.

Spec: ``project-docs/specs/one_projects_app.md`` §9 OP-11.

``core.mine_clause`` is the one "mine". ``personal.MY_TASKS_FROM``, the
assignee arm of ``/projects/assigned-to-me``, ``GET /projects/tasks?scope=mine``,
``GET /projects/calendar?scope=mine`` and ``GET /projects/my/summary`` all
compose it. These tests pin the SQL text and the identity rule with no
database. ``test_projects_mine_r8.py`` proves the sets on Postgres.

Mutations this file names (each fails at least one test):

* M1. Drop an arm from ``mine_clause``.
* M2. Read the member from a query parameter, not the session.
* M3. Drop ``task_visibility_clause`` from a ``scope=mine`` read.
* M4. Drop the archived-project clause from the Overview.
* M5. Name a child the reader cannot see in ``fold_mine_summary``.
* M6. Accept an address for ``subject``.
"""

from __future__ import annotations

import inspect
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException
from gateway.routes.projects import analytics as pm_analytics
from gateway.routes.projects import calendar as pm_calendar
from gateway.routes.projects import core as pm_core
from gateway.routes.projects import me as pm_me
from gateway.routes.projects import mine as pm_mine
from gateway.routes.projects import personal as pm_personal
from gateway.routes.projects import report_scope as pm_scope
from gateway.routes.projects import tasks as pm_tasks
from gateway.routes.projects.core import MINE_ARMS, mine_clause

from tests.unit._projects_fakes import member_user, page

ME = "Mine.Member@Example.test"
ME_LOWER = ME.lower()
USER = member_user(ME)
ORG = "11111111-1111-4111-8111-111111111111"


class _Result:
    def __init__(self, rows: list[Any] | None = None, scalar: Any = 0) -> None:
        self._rows = rows or []
        self._scalar = scalar

    def fetchall(self) -> list[Any]:
        return list(self._rows)

    def fetchone(self) -> Any:
        return self._rows[0] if self._rows else None

    def scalar(self) -> Any:
        return self._scalar

    def one(self) -> Any:
        return SimpleNamespace(
            mins=0, estimated=0, tasks=0, completed=0, cancelled=0, measured=0,
            no_start=0, median_hours=None, p90_hours=None,
            period_start=date(2026, 10, 1), period_end=date(2026, 10, 27),
        )


class _RecordingDB:
    """Records each statement and its binds. Answers the directory."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def execute(self, stmt: Any, params: dict[str, Any] | None = None) -> _Result:
        sql = str(stmt)
        self.calls.append((sql, dict(params or {})))
        if "FROM app_user au" in sql and "organization_id" in sql:
            return _Result([SimpleNamespace(organization_id=ORG)])
        if sql.startswith("SELECT 1 FROM app_user"):
            return _Result([SimpleNamespace()])
        return _Result()

    async def commit(self) -> None:
        return None

    def sql_with(self, needle: str) -> list[tuple[str, dict[str, Any]]]:
        return [(s, p) for s, p in self.calls if needle in s]


@pytest.fixture
def db(monkeypatch: pytest.MonkeyPatch) -> _RecordingDB:
    from contextlib import asynccontextmanager

    fake = _RecordingDB()

    @asynccontextmanager
    async def _session(organization_id: str | None = None) -> Any:
        yield fake

    for module in (pm_core, pm_tasks, pm_calendar, pm_mine, pm_me, pm_analytics, pm_personal):
        monkeypatch.setattr(module, "_tenant_session", _session)
    return fake


# ── The one fragment ────────────────────────────────────────────────────────


def test_mine_clause_has_the_three_arms_joined_by_or() -> None:
    """M1. Each arm of §9 OP-11 item 3 is in the one fragment."""
    sql = mine_clause("t")
    assert MINE_ARMS == ("assigned", "personal", "waiting")
    assert "lower(a.assignee) = :who" in sql
    assert "lower(proj.personal_owner) = :who" in sql
    assert "p.disposition = 'WAITING'" in sql
    assert sql.count(" OR ") >= 2
    # The overlay narrows and never widens: the WAITING arm carries the
    # member's grant closure on the task's root.
    assert "t.root_project_id IN (" in mine_clause("t", arm="waiting")
    assert "pm_project_grants" in mine_clause("t", arm="waiting")


def test_the_assigned_arm_is_the_assignee_alone() -> None:
    sql = mine_clause("t", arm="assigned")
    assert "pm_task_assignees" in sql
    assert "personal_owner" not in sql and "WAITING" not in sql


def test_an_unknown_arm_is_refused() -> None:
    with pytest.raises(ValueError):
        mine_clause("t", arm="watching")


def test_the_alias_is_honoured() -> None:
    sql = mine_clause("x")
    assert "x.id" in sql and "x.project_id" in sql and "x.root_project_id" in sql
    assert " t." not in sql


def test_my_tasks_from_composes_the_one_fragment_under_the_tenant() -> None:
    """``MY_TASKS_FROM`` holds no arm of its own any more."""
    body = pm_personal.MY_TASKS_FROM
    assert mine_clause("t") in body
    assert "t.organization_id = CAST(:vis_org AS uuid)" in body
    assert body.index("t.organization_id") < body.index(mine_clause("t"))
    assert "proj.archived_at IS NULL OR CAST(:archived AS boolean)" in body


def test_assigned_to_me_reads_the_assignee_arm_under_the_tenant() -> None:
    source = inspect.getsource(pm_me.assigned_to_me)
    assert 'mine_clause("t", arm="assigned")' in source
    assert "t.organization_id = CAST(:vis_org AS uuid)" in source
    assert "pr.archived_at IS NOT NULL" in source


async def test_mine_binds_take_the_member_from_the_directory(db: _RecordingDB) -> None:
    binds = await pm_core.mine_binds(db, ME)
    assert binds["who"] == ME_LOWER and binds["vis_email"] == ME_LOWER
    assert binds["vis_org"] == ORG
    assert binds["vis_groups"] == []


# ── scope=mine on the list and the calendar ─────────────────────────────────


@pytest.mark.parametrize("route", ["list", "calendar"])
def test_scope_is_a_word_and_no_route_takes_an_address(route: str) -> None:
    """M2. The member is the session's. No parameter names a person."""
    fn = pm_tasks.list_tasks if route == "list" else pm_calendar.get_calendar
    params = inspect.signature(fn).parameters
    assert params["scope"].default is None
    assert "member" not in params and "who" not in params and "email" not in params


def test_an_unknown_scope_is_a_422() -> None:
    assert pm_mine.check_scope(None) is False
    assert pm_mine.check_scope("") is False
    assert pm_mine.check_scope("mine") is True
    with pytest.raises(HTTPException) as err:
        pm_mine.check_scope("someone@else.test")
    assert err.value.status_code == 422


async def test_the_list_with_scope_mine_keeps_every_board_rule(db: _RecordingDB) -> None:
    """M3. The clause is ANDed beside the visibility clause, and the archive
    rules and the triage exclusion stay."""
    await pm_tasks.list_tasks(user=USER, scope="mine", page=page())
    counts = db.sql_with("SELECT count(*) FROM pm_tasks t")
    assert counts, "the list counted nothing"
    sql, params = counts[-1]
    assert mine_clause("t") in sql
    assert "t.organization_id = CAST(:vis_org AS uuid)" in sql
    assert "t.archived_at IS NULL" in sql
    assert "p.archived_at IS NULL" in sql
    assert "s_triage" in sql
    assert params["who"] == ME_LOWER
    assert params["vis_org"] == ORG


async def test_the_list_without_scope_is_unchanged(db: _RecordingDB) -> None:
    await pm_tasks.list_tasks(user=USER, page=page())
    sql, params = db.sql_with("SELECT count(*) FROM pm_tasks t")[-1]
    assert mine_clause("t") not in sql
    assert "who" not in params


async def test_the_calendar_with_scope_mine_reads_the_same_clause(db: _RecordingDB) -> None:
    await pm_calendar.get_calendar(
        user=USER, date_from="2026-10-01", date_to="2026-11-01", scope="mine",
        include_undated=True,
    )
    reads = db.sql_with("FROM pm_tasks t WHERE")
    assert reads and all(mine_clause("t") in s for s, _ in reads)
    assert all(p["who"] == ME_LOWER for _, p in reads)


async def test_with_overlay_reads_the_callers_overlay_only(
    db: _RecordingDB, monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = [{"id": "22222222-2222-4222-8222-222222222222"}]
    vis = pm_core.Visibility(unrestricted=False, email=ME_LOWER, groups=(),
                             organization_id=ORG)
    await pm_mine.attach_my_overlay(db, vis, rows)
    sql, params = db.sql_with("LEFT JOIN pm_task_personal p")[-1]
    assert "lower(p.member_email) = :who" in sql
    assert "t.organization_id = CAST(:vis_org AS uuid)" in sql
    assert params["who"] == ME_LOWER and params["vis_org"] == ORG
    # No overlay row: every key is present and empty.
    assert rows[0]["disposition"] is None and rows[0]["defer_until"] is None
    assert set(rows[0]) >= {
        "disposition", "context", "energy", "defer_until", "is_mine", "status_category",
    }
    assert rows[0]["status_category"] is None
    # The stage comes from the status join of the same query (the Board tab).
    assert "s.category AS status_category" in sql


# ── GET /projects/my/summary ────────────────────────────────────────────────


def test_the_overview_takes_no_parameter_but_the_session() -> None:
    assert list(inspect.signature(pm_mine.my_summary).parameters) == ["user"]


async def test_the_overview_reads_the_list_predicate(db: _RecordingDB) -> None:
    """M4. The same WHERE as ``scope=mine`` with no filter."""
    body = await pm_mine.my_summary(user=USER)
    sql, params = db.sql_with("AS is_due_soon")[-1]
    where, _ = pm_mine.mine_where(pm_core.Visibility(
        unrestricted=False, email=ME_LOWER, groups=(), organization_id=ORG))
    assert where in sql
    assert "p.archived_at IS NULL" in sql and "t.archived_at IS NULL" in sql
    assert params["who"] == ME_LOWER
    assert body["level"] == "mine" and body["tasks"] == 0
    assert body["own"] == {"tasks": 0, "overdue": 0, "due_soon": 0, "by_category": {}}
    assert set(body["by_disposition"]) == set(pm_mine.DISPOSITION_KEYS)
    # The zone of the Today count. No settings row reads as UTC (item 8).
    assert body["time_zone"] == "UTC"


def test_the_overview_where_carries_the_visibility_clause() -> None:
    """M3 for the Overview. Every arm of mine is inside the member's reach
    on the R8 seed, so only this pin sees a dropped clause. It matters for an
    outside agent, whose narrowing rides on the visibility clause."""
    from acb_auth import AgentScope

    for vis in (
        pm_core.Visibility(unrestricted=False, email=ME_LOWER, groups=(),
                           organization_id=ORG),
        pm_core.Visibility(unrestricted=True, email=ME_LOWER, groups=(),
                           organization_id=ORG,
                           agent=AgentScope(connection_id="c", pinned_projects=(),
                                             include_personal=False)),
    ):
        where, _ = pm_mine.mine_where(vis)
        assert pm_core.task_visibility_clause(vis) in where
        assert mine_clause("t") in where


def _row(**kw: Any) -> SimpleNamespace:
    base = dict(
        id="t", project_id="p1", root_project_id="r1", category="todo",
        start_date=None, due_at=None, completed_at=None, p_disposition=None,
        p_defer_until=None, is_mine=True, has_assignee=True, is_overdue=False,
        is_due_soon=False, on_board=True,
    )
    base.update(kw)
    return SimpleNamespace(**base)


def _node(name: str, **kw: Any) -> SimpleNamespace:
    base = dict(name=name, kind="project", status="active", icon=None,
                icon_slot=None, personal_owner=None, archived_at=None)
    base.update(kw)
    return SimpleNamespace(**base)


def test_the_fold_names_only_a_project_the_reader_may_see() -> None:
    """M5 (audit P1-8). A task in no nameable project goes to ``own``."""
    rows = [
        _row(id="a", project_id="p1", root_project_id="r1", is_overdue=True),
        _row(id="b", project_id="p2", root_project_id="r1", category="done"),
        _row(id="c", project_id="p9", root_project_id="r9"),
        _row(id="d", project_id="p3", root_project_id="r8", is_due_soon=True),
    ]
    nodes = {"r1": _node("Space"), "p3": _node("Seen project"), "p1": _node("P1")}
    body = pm_mine.fold_mine_summary(rows, nodes)
    assert body["tasks"] == 4 and body["overdue"] == 1 and body["due_soon"] == 1
    assert body["by_category"] == {"todo": 3, "done": 1}
    names = {c["id"]: c["name"] for c in body["children"]}
    assert names == {"r1": "Space", "p3": "Seen project"}
    assert body["own"]["tasks"] == 1
    assert body["own"]["tasks"] + sum(c["tasks"] for c in body["children"]) == body["tasks"]
    assert "r9" not in str(body) and "p9" not in str(body)


def test_the_disposition_fold_follows_the_client_rules() -> None:
    now = datetime(2026, 10, 27, 6, 0, tzinfo=UTC)
    today = date(2026, 10, 27)
    rows = [
        _row(id="next-today", due_at=now - timedelta(days=1)),
        _row(id="next-later", due_at=now + timedelta(days=5)),
        _row(id="inbox", is_mine=False, has_assignee=False),
        _row(id="inbox-deferred", is_mine=False, has_assignee=False,
             p_defer_until=now + timedelta(days=2)),
        _row(id="waiting", p_disposition="WAITING", is_mine=False),
        _row(id="someday", category="backlog"),
        _row(id="done", category="done"),
        _row(id="trash", p_disposition="TRASH"),
        _row(id="not-mine-next", p_disposition="NEXT", is_mine=False),
        _row(id="personal-next", on_board=False),
        _row(id="untriaged-deferred", p_defer_until=now + timedelta(days=2)),
    ]
    counts = pm_mine.fold_dispositions(rows, at=now, today=today, zone="UTC")
    # inbox: the INBOX row, plus the untriaged board rows (next-today,
    # next-later, someday). A personal project, a stated overlay, a deferred
    # row and a DONE row are not in `inboxCount`.
    assert counts == {
        "inbox": 4, "today": 1, "next": 4, "waiting": 1, "someday": 1, "done": 1,
    }


# ── subject=me on Analytics ─────────────────────────────────────────────────


@pytest.mark.parametrize("name", ["stuck", "load", "throughput", "finished", "outlook"])
def test_each_route_takes_subject_as_a_word(name: str) -> None:
    params = inspect.signature(getattr(pm_analytics, name)).parameters
    assert params["subject"].default is None


async def test_an_address_is_a_422(db: _RecordingDB) -> None:
    """M6. ``subject`` is ``me`` or nothing."""
    vis = pm_core.Visibility(unrestricted=False, email=ME_LOWER, groups=(),
                             organization_id=ORG)
    assert await pm_scope.subject_me(db, USER, vis, None) is None
    with pytest.raises(HTTPException) as err:
        await pm_scope.subject_me(db, USER, vis, "someone@else.test")
    assert err.value.status_code == 422
    assert await pm_scope.subject_me(db, USER, vis, "me") == [ME_LOWER]


async def test_stuck_with_subject_me_narrows_to_my_tasks(db: _RecordingDB) -> None:
    body = await pm_analytics.stuck(user=USER, subject="me")
    assert body["scope"] == "portfolio"
    reads = db.sql_with("subject_people")
    assert reads and all(p["subject_people"] == [ME_LOWER] for _, p in reads)


async def test_outlook_refuses_a_subject_as_the_report_does(db: _RecordingDB) -> None:
    with pytest.raises(HTTPException) as err:
        await pm_analytics.outlook(user=USER, subject="me")
    assert err.value.status_code == 422
    assert pm_scope.NO_SUBJECT_REASON in str(err.value.detail)
    assert "outlook" in pm_scope.NO_SUBJECT_SECTIONS
