"""A new step lands in its parent's lane from every door (D-PM-38, D79).

Spec: ``project-docs/specs/project_management_app.md`` §11.42.

The rule is ``core.parent_lane_status``, ONE seam. ``POST /projects/tasks``
calls it for a step with no stated status, and so the Projects panel, the
board and both chat tools reach it. ``personal.step_status`` calls it for My
Tasks. Before §11.42 only My Tasks had the rule, and every other door put a
step in the project's FIRST lane.

Hermetic, against the shared ``FakeProjectsDB``. The set check is a SQL
clause, and a fake agrees with whatever SQL it is handed, so the clause is
also asserted as text here and driven on a real Postgres by
``tests/live/live_subtask_parent_lane.py`` (R8).
"""

from __future__ import annotations

import pytest
from gateway.routes.projects import core as pm_core
from gateway.routes.projects import tasks as pm_tasks

from tests.unit._projects_fakes import (
    FakeProjectsDB,
    bind_db,
    projects_user,
    silence_events,
)

MODULES = (pm_core, pm_tasks)
USER = projects_user()


@pytest.fixture
def db(monkeypatch: pytest.MonkeyPatch) -> FakeProjectsDB:
    fake = FakeProjectsDB()
    bind_db(monkeypatch, fake, MODULES)
    silence_events(monkeypatch, MODULES)
    return fake


def _board(db: FakeProjectsDB):
    """A project whose FIRST lane is Backlog, so the first-lane rule and the
    parent's lane give different answers."""
    project = db.seed_project(name="Website")
    lanes = {
        "backlog": db.seed_status(project.id, name="Backlog", category="backlog",
                                  position=0),
        "todo": db.seed_status(project.id, name="To do", category="todo",
                               position=1),
        "review": db.seed_status(project.id, name="In review",
                                 category="in_progress", position=2),
        "done": db.seed_status(project.id, name="Done", category="done",
                               position=3),
        "triage": db.seed_status(project.id, name="Triage", category="triage",
                                 position=4),
    }
    return project, lanes


async def _step(project, parent, status_id=None) -> dict:
    return await pm_tasks.create_task(
        pm_tasks.TaskIn(
            project_id=str(project.id), parent_task_id=str(parent.id),
            status_id=str(status_id) if status_id else None, title="A step",
        ),
        user=USER,
    )


async def test_a_projects_panel_step_under_in_review_lands_in_in_review(
    db: FakeProjectsDB,
) -> None:
    """TaskBody's and TaskBoard's "add subtask" post a parent and no status."""
    project, lanes = _board(db)
    parent = db.seed_task(project.id, lanes["review"].id, title="Parent")
    step = await _step(project, parent)
    assert step["status_id"] == str(lanes["review"].id)


@pytest.mark.parametrize("closed", ["done", "triage"])
async def test_a_parent_in_a_closed_or_triage_lane_falls_back(
    db: FakeProjectsDB, closed: str,
) -> None:
    project, lanes = _board(db)
    parent = db.seed_task(project.id, lanes[closed].id, title="Parent")
    step = await _step(project, parent)
    assert step["status_id"] == str(lanes["backlog"].id)


async def test_a_stated_status_id_is_kept(db: FakeProjectsDB) -> None:
    project, lanes = _board(db)
    parent = db.seed_task(project.id, lanes["review"].id, title="Parent")
    step = await _step(project, parent, lanes["todo"].id)
    assert step["status_id"] == str(lanes["todo"].id)


async def test_a_parent_in_another_status_set_falls_back(
    db: FakeProjectsDB,
) -> None:
    """The parent's lane belongs to another project's set. The step cannot
    hold it, so it takes the first lane of its own set."""
    project, lanes = _board(db)
    other = db.seed_project(name="Elsewhere")
    theirs = db.seed_status(other.id, name="Doing", category="in_progress",
                            position=0)
    parent = db.seed_task(other.id, theirs.id, title="Their parent")
    step = await _step(project, parent)
    assert step["status_id"] == str(lanes["backlog"].id)


async def test_a_task_with_no_parent_still_takes_the_first_lane(
    db: FakeProjectsDB,
) -> None:
    project, lanes = _board(db)
    row = await pm_tasks.create_task(
        pm_tasks.TaskIn(project_id=str(project.id), title="Top level"),
        user=USER,
    )
    assert row["status_id"] == str(lanes["backlog"].id)


async def test_the_lane_read_checks_the_status_set_in_sql(
    db: FakeProjectsDB,
) -> None:
    """The set check is the SQL clause. Without it a parent in another
    project hands the step a lane of a set it does not belong to."""
    project, lanes = _board(db)
    parent = db.seed_task(project.id, lanes["review"].id, title="Parent")
    await _step(project, parent)
    reads = [
        s for s in db.statements_touching("pm_task_statuses")
        if "CAST(:sid AS uuid)" in s
    ]
    assert reads, "the parent's lane was never read"
    assert all("project_id = CAST(:home AS uuid)" in s for s in reads)


def test_there_is_one_copy_of_the_rule() -> None:
    """``personal.step_status`` delegates. A second copy of the lane rule is
    how the doors disagreed in the first place."""
    import inspect

    from gateway.routes.projects import personal

    src = inspect.getsource(personal.step_status)
    assert "return await parent_lane_status(" in src
    assert "pm_task_statuses" not in src
