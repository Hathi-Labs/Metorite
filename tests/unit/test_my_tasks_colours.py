"""My Tasks · the lane colour and the tag colours on the personal lens.

Continuity P3, items 1 and 2. My Tasks drew every tag grey and coloured a
lane by its category only, while Projects draws the registry colour and the
lane's stored colour. The lens row now carries both, so the client can pass
them through the same `statusAccent` path Projects uses.

Hermetic: the fake mirrors the SQL. The SQL itself is verified against a real
Postgres (R8) in the PR that adds it, and `test_the_tag_colours_*` below pins
the two anchors that keep the subquery inside one tenant.
"""

from __future__ import annotations

import pytest
from gateway.routes.projects import core as pm_core
from gateway.routes.projects import personal as pm_personal
from gateway.routes.projects import tasks as pm_tasks
from gateway.routes.projects import tree as pm_tree

from tests.unit._projects_fakes import (
    FakeProjectsDB,
    bind_db,
    member_user,
    page,
    silence_events,
)

MODULES = (pm_core, pm_tree, pm_tasks, pm_personal)

ALICE = member_user("alice@fracktal.in")


@pytest.fixture
def db(monkeypatch: pytest.MonkeyPatch) -> FakeProjectsDB:
    fake = FakeProjectsDB()
    bind_db(monkeypatch, fake, MODULES)
    silence_events(monkeypatch, MODULES)
    return fake


def _seed(db: FakeProjectsDB):
    project = db.seed_project(name="Sales", subject="org")
    lane = db.seed_status(project.id, name="Review", category="in_progress")
    # A custom lane colour. Category alone would say "in_progress" blue.
    db.rows("pm_task_statuses")[-1]["color"] = "violet"
    task = db.seed_task(project.id, lane.id, tags=["Bug", "ops", "loose"])
    db.seed(
        "pm_task_assignees", task_id=task.id, assignee="alice@fracktal.in",
        assigned_by="owner@fracktal.in",
    )
    org = db.rows("pm_projects")[-1]["organization_id"]
    # Root-local "bug" is red. The org-wide "bug" is green, and loses.
    db.seed("pm_tags", project_id=project.id, name="bug", color="red")
    db.seed("pm_tags", project_id=None, organization_id=org, name="bug", color="green")
    # Org-wide only: it answers for the task.
    db.seed("pm_tags", project_id=None, organization_id=org, name="ops", color="amber")
    return task


async def test_the_lens_row_carries_the_lane_colour(db: FakeProjectsDB) -> None:
    _seed(db)
    rows = (await pm_personal.my_inbox(user=ALICE, page=page())).rows
    assert rows[0]["status_color"] == "violet"


async def test_the_lens_row_carries_each_tag_colour(db: FakeProjectsDB) -> None:
    _seed(db)
    rows = (await pm_personal.my_inbox(user=ALICE, page=page())).rows
    # Keyed by lower(name). A root-local row shadows the org-wide one, an
    # org-wide row answers when the root has none, and an unregistered tag
    # is absent (the client draws it grey, as Projects does).
    assert rows[0]["tag_colors"] == {"bug": "red", "ops": "amber"}


async def test_the_single_read_carries_the_same_colours(db: FakeProjectsDB) -> None:
    task = _seed(db)
    single = await pm_personal.my_task(str(task.id), user=ALICE)
    assert single["status_color"] == "violet"
    assert single["tag_colors"] == {"bug": "red", "ops": "amber"}


def test_the_tag_colours_subquery_stays_inside_the_tasks_root_and_tenant() -> None:
    """The two anchors. Without the root, a second project's tag of the same
    name answers. Without the tenant, the org-wide arm (`project_id IS NULL`)
    is anchored by nothing but RLS, which `core.vocabulary_scope` refuses to
    rely on alone."""
    sql = " ".join(pm_personal._MY_TASKS_SQL.split())
    assert "g.project_id = t.root_project_id" in sql
    assert "g.organization_id = t.organization_id" in sql
    # Root-local first, so DISTINCT ON keeps it.
    assert "ORDER BY lower(g.name), (g.project_id IS NULL)" in sql
