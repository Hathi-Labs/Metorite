"""A space holds projects, not tasks (2026-10-26).

``project_management_app.md`` "What each level does": a space and a folder
hold no tasks. Production showed the gap on 2026-10-26. The chat filed six
tasks on the space "Company Operations", and the member found none of them,
because a space draws a dashboard and no board.

The rule is ONE helper, ``core.assert_holds_tasks``. It extends the folder
rule of migration 193 at the same three sites, and it adds the intake capture.
``tree.move_node`` refuses a project that holds live tasks of its own when a
move would make it a space.

A personal root is a root too, and it is NOT a space here. It holds the
member's Inbox by design (``personal.create_personal_task``).

Hermetic. The SQL of the move refusal runs on a real Postgres in
``test_projects_space_holds_no_tasks_r8.py`` (R8).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from gateway.routes.projects import core as pm_core
from gateway.routes.projects import intake as pm_intake
from gateway.routes.projects import move as pm_move
from gateway.routes.projects import tasks as pm_tasks
from gateway.routes.projects import tree as pm_tree

from tests.unit._projects_fakes import (
    FakeProjectsDB,
    bind_db,
    projects_user,
    silence_events,
)

MODULES = (pm_core, pm_tree, pm_tasks, pm_move, pm_intake)
USER = projects_user()


@pytest.fixture
def db(monkeypatch: pytest.MonkeyPatch) -> FakeProjectsDB:
    fake = FakeProjectsDB()
    bind_db(monkeypatch, fake, MODULES)
    silence_events(monkeypatch, MODULES)
    return fake


def _space_and_project(db: FakeProjectsDB):
    """A space with its own lanes, and one project inside it."""
    space = db.seed_project(name="Company Operations", personal_owner=None)
    todo = db.seed_status(space.id, name="To do", category="todo", is_default=True)
    project = db.seed_project(
        name="HR", parent=space.id, personal_owner=None, owns_statuses=False,
    )
    return space, project, todo


# ── The pure rule ──────────────────────────────────────────────────────────

def _node(**columns) -> SimpleNamespace:
    base = {"name": "N", "kind": "project", "parent_project_id": None, "personal_owner": None}
    base.update(columns)
    return SimpleNamespace(**base)


def test_a_team_root_is_a_space():
    assert pm_core.is_team_space(_node()) is True


def test_a_personal_root_is_not_a_space():
    """The member's own root holds the Inbox, so it stays a home for tasks."""
    assert pm_core.is_team_space(_node(personal_owner="me@x.io")) is False
    pm_core.assert_holds_tasks(_node(personal_owner="me@x.io"), then="Create the task in")


def test_a_project_under_a_space_holds_tasks():
    assert pm_core.is_team_space(_node(parent_project_id="p")) is False
    pm_core.assert_holds_tasks(_node(parent_project_id="p"), then="Create the task in")


def test_a_space_is_refused_by_name_with_the_way_out():
    with pytest.raises(HTTPException) as exc:
        pm_core.assert_holds_tasks(_node(name="Company Operations"), then="Create the task in")
    assert exc.value.status_code == 422
    assert exc.value.detail == (
        "Company Operations is a space. A space holds projects, not tasks. "
        "Create the task in a project inside it."
    )


def test_a_folder_keeps_its_own_words():
    with pytest.raises(HTTPException) as exc:
        pm_core.assert_holds_tasks(
            _node(kind="folder", parent_project_id="p"), then="Move the task into",
        )
    assert exc.value.detail == (
        "A folder holds projects, not tasks. Move the task into a project inside it."
    )


# ── Every door that places a task ──────────────────────────────────────────

async def test_create_refuses_a_space(db: FakeProjectsDB) -> None:
    space, _project, _todo = _space_and_project(db)
    with pytest.raises(HTTPException) as exc:
        await pm_tasks.create_task(
            pm_tasks.TaskIn(project_id=str(space.id), title="Onboard Priya"), user=USER,
        )
    assert exc.value.status_code == 422
    assert "Company Operations is a space" in exc.value.detail
    assert db.rows("pm_tasks") == []


async def test_create_in_a_project_inside_the_space_still_works(db: FakeProjectsDB) -> None:
    _space, project, _todo = _space_and_project(db)
    created = await pm_tasks.create_task(
        pm_tasks.TaskIn(project_id=str(project.id), title="Onboard Priya"), user=USER,
    )
    assert created["project_id"] == str(project.id)


async def test_create_on_a_personal_root_still_works(db: FakeProjectsDB) -> None:
    mine = db.seed_project(name="My tasks", personal_owner=USER.email)
    db.seed_status(mine.id, name="Backlog", category="backlog", is_default=True)
    created = await pm_tasks.create_task(
        pm_tasks.TaskIn(project_id=str(mine.id), title="Call the bank"), user=USER,
    )
    assert created["project_id"] == str(mine.id)


async def test_a_single_move_into_a_space_is_refused(db: FakeProjectsDB) -> None:
    space, project, todo = _space_and_project(db)
    task = db.seed_task(project.id, todo.id, root=space.id, title="A")
    with pytest.raises(HTTPException) as exc:
        await pm_tasks.move_task(
            str(task.id), pm_tasks.MoveTask(project_id=str(space.id)), user=USER,
        )
    assert exc.value.status_code == 422
    assert "is a space" in exc.value.detail


async def test_a_bulk_move_into_a_space_is_refused(db: FakeProjectsDB) -> None:
    space, project, todo = _space_and_project(db)
    task = db.seed_task(project.id, todo.id, root=space.id, title="A")
    with pytest.raises(HTTPException) as exc:
        await pm_move.move_tasks(
            pm_move.MoveIn(task_ids=[str(task.id)], destination_project_id=str(space.id)),
            user=USER,
        )
    assert exc.value.status_code == 422
    assert "is a space" in exc.value.detail


async def test_a_capture_into_a_space_is_refused(db: FakeProjectsDB) -> None:
    space, _project, _todo = _space_and_project(db)
    with pytest.raises(HTTPException) as exc:
        await pm_intake.capture_intake(
            pm_intake.IntakeIn(project_id=str(space.id), title="From the inbox"), user=USER,
        )
    assert exc.value.status_code == 422
    assert "is a space" in exc.value.detail


async def test_a_task_already_on_a_space_still_edits(db: FakeProjectsDB) -> None:
    """The six production tasks must stay readable and editable. A PATCH that
    does not change the project places nothing, so the rule does not run."""
    space, _project, todo = _space_and_project(db)
    task = db.seed_task(space.id, todo.id, title="Legacy")
    after = await pm_tasks.patch_task(
        str(task.id), pm_tasks.TaskIn(title="Legacy, renamed"), user=USER,
    )
    assert after["title"] == "Legacy, renamed"


async def test_a_project_with_tasks_cannot_become_a_space(db: FakeProjectsDB) -> None:
    space, project, todo = _space_and_project(db)
    db.seed_task(project.id, todo.id, root=space.id, title="A")
    with pytest.raises(HTTPException) as exc:
        await pm_tree.move_node(
            str(project.id), pm_tree.MoveProject(parent_project_id=None), user=USER,
        )
    assert exc.value.status_code == 422
    assert "HR holds 1 task of its own" in exc.value.detail
