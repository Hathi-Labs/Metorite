"""WS-64 — nothing is filed under an archived project (hermetic).

A child created or moved under an archived parent kept ``archived_at`` NULL, so
it showed in every list the archive hides. ``core.assert_not_archived`` is the
one guard, and each write route calls it after its visibility load.
SQL filters are fenced against Postgres in
``test_projects_archived_filters_r8.py``.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from gateway.routes.projects import core, me, tree
from gateway.routes.projects import tasks as pm_tasks
from gateway.routes.projects.core import assert_not_archived

from tests.unit._projects_fakes import (
    FakeProjectsDB,
    bind_db,
    projects_user,
    silence_events,
)

USER = projects_user()

ROUTES = Path(tree.__file__).parent


def _src(name: str) -> str:
    return (ROUTES / name).read_text(encoding="utf-8")


def test_an_archived_node_is_refused_with_a_422_that_names_it():
    node = SimpleNamespace(name="Old plan", archived_at="2026-10-01")
    with pytest.raises(HTTPException) as err:
        assert_not_archived(node, then="Create the task in")
    assert err.value.status_code == 422
    assert "Old plan is archived" in err.value.detail
    assert "Create the task in an active project" in err.value.detail


def test_an_active_node_passes():
    assert_not_archived(SimpleNamespace(name="x", archived_at=None), then="Move")


def test_a_row_with_no_archived_field_passes():
    assert_not_archived(SimpleNamespace(name="x"), then="Move")


@pytest.mark.parametrize("module,call", [
    ("tree.py", 'assert_not_archived(parent_row, then="Create it in")'),
    ("tree.py", 'assert_not_archived(destination, then="Move it into")'),
    ("tasks.py", 'assert_not_archived(target, then="Create the task in")'),
    ("tasks.py", 'assert_not_archived(dest, then="Move the task into")'),
    ("move.py", 'assert_not_archived(dest, then="Move into")'),
    ("intake.py", 'assert_not_archived(target, then="Capture into")'),
])
def test_each_placing_route_calls_the_guard(module, call):
    assert call in _src(module)


@pytest.fixture
def db(monkeypatch: pytest.MonkeyPatch) -> FakeProjectsDB:
    fake = FakeProjectsDB()
    mods = (core, tree, pm_tasks)
    bind_db(monkeypatch, fake, mods)
    silence_events(monkeypatch, mods)
    return fake


def _space_with_two_projects(db: FakeProjectsDB, *, first_archived: bool):
    space = db.seed_project(name="Ops", personal_owner=None)
    todo = db.seed_status(space.id, name="To do", category="todo", is_default=True)
    first = db.seed_project(
        name="Old plan", parent=space.id, personal_owner=None, owns_statuses=False,
        archived_at="2026-10-01" if first_archived else None,
    )
    second = db.seed_project(
        name="New plan", parent=space.id, personal_owner=None, owns_statuses=False,
    )
    return space, first, second, todo


async def test_create_task_into_an_archived_project_is_a_422(db: FakeProjectsDB):
    _space, old, _new, _todo = _space_with_two_projects(db, first_archived=True)
    with pytest.raises(HTTPException) as err:
        await pm_tasks.create_task(
            pm_tasks.TaskIn(project_id=str(old.id), title="Late idea"), user=USER,
        )
    assert err.value.status_code == 422
    assert "Old plan is archived" in err.value.detail
    assert db.rows("pm_tasks") == []


async def test_a_task_can_still_move_out_of_an_archived_project(db: FakeProjectsDB):
    space, old, new, todo = _space_with_two_projects(db, first_archived=True)
    task = db.seed_task(old.id, todo.id, root=space.id, title="Rescue me")
    moved = await pm_tasks.move_task(
        str(task.id), pm_tasks.MoveTask(project_id=str(new.id)), user=USER,
    )
    assert moved["project_id"] == str(new.id)


async def test_a_recurring_task_in_an_archived_project_spawns_no_successor(
    db: FakeProjectsDB, monkeypatch: pytest.MonkeyPatch,
):
    """``spawn_successor`` asks ``is_runnable_with_ancestors``, whose SQL tests
    ``archived_at`` up the chain (R8 in ``test_projects_archived_filters_r8``).
    Here the wiring: a False answer returns None before any read or write."""
    from gateway.routes.projects import recurrence

    async def archived(_db, _pid):
        return False

    monkeypatch.setattr(recurrence, "is_runnable_with_ancestors", archived)
    task = SimpleNamespace(
        id="t1", project_id="p1", recurrence_id="r1", recurrence_spawned_at=None,
    )
    assert await recurrence.spawn_successor(db, task, actor_id="a@x.io") is None
    assert db.rows("pm_tasks") == []
    assert "archived_at IS NULL" in core._ANCESTOR_RUNNABLE_SQL


async def test_a_members_own_task_root_cannot_be_archived(db: FakeProjectsDB):
    mine = db.seed_project(name="My tasks", personal_owner=USER.email)
    with pytest.raises(HTTPException) as err:
        await tree.archive_node(str(mine.id), user=USER)
    assert err.value.status_code == 422
    assert "own task root cannot be archived" in err.value.detail


def test_the_personal_capture_helper_guards_an_archived_target():
    from gateway.routes.projects import personal

    src = (ROUTES / "personal.py").read_text(encoding="utf-8")
    at = src.index("async def create_personal_task(")
    assert 'assert_not_archived(target, then="Capture into")' in src[at:at + 4500]
    assert personal.assert_not_archived is assert_not_archived


def test_the_portfolio_and_node_summaries_skip_archived_projects():
    assert "archived_at IS NULL" in tree.NODE_DESCENDANTS_SQL
    # Both arms of the recursive walk carry the filter.
    assert tree.NODE_DESCENDANTS_SQL.count("archived_at IS NULL") == 2


def test_the_archived_node_walk_drops_both_filters():
    assert "archived_at IS NULL" not in tree.NODE_DESCENDANTS_ARCHIVED_SQL
    assert "WITH RECURSIVE" in tree.NODE_DESCENDANTS_ARCHIVED_SQL
    assert "NODE_DESCENDANTS_ARCHIVED_SQL" in _src("tree.py")


def test_the_reports_open_predicate_walks_for_an_archived_project():
    """Every Reports read (overdue by project, workload, pulse, capacity,
    waiting, rebalance) takes its WHERE from ``load_open_where`` or the same
    clause. That clause tests ``archived_at`` up the chain, so one rule keeps
    an archived project out of all of them."""
    from gateway.routes.projects.core import (
        reportable_project_clause,
        reportable_with_ancestors_clause,
    )

    assert "archived_at IS NULL" in reportable_project_clause("chain")
    assert "archived_at" in reportable_with_ancestors_clause("t")
    # load_open_where, the stuck route and the outlook each carry it.
    assert _src("analytics.py").count("reportable_with_ancestors_clause('t')") >= 3


def test_my_work_sql_filters_the_project():
    from gateway.routes.projects.personal import MY_TASKS_FROM

    assert "proj.archived_at IS NULL" in MY_TASKS_FROM


def test_assigned_to_me_filters_the_project():
    assert "pr.archived_at IS NOT NULL" in Path(me.__file__).read_text(
        encoding="utf-8",
    )
