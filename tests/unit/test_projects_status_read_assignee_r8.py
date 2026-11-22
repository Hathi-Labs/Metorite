"""Task 3865 - a member who opens a task by assignment reads its project's lanes.

Production, 18 Nov 2026: the chat set a task to "In progress". It read the task
(200), then ``GET /projects/nodes/<id>/statuses`` answered 404 "Project not
found". ``update_task`` and ``vocabulary`` both pass the task's own
``project_id``. ``load_visible_task`` lets a task in through its assignee arm
when the project is not granted. ``load_visible_project`` has no such arm.

The fix is ``core.load_readable_project``: the project, or the project of a task
the caller can open. Only the READ routes use it. R5 holds: a member who can open
no task and no node of the project still gets the 404.

R7 fence. Mutation M1: make ``load_readable_project`` return the project with no
task check, and ``test_a_stranger_still_gets_the_404`` goes red. Mutation M2:
use it in ``create_status``, and ``test_the_write_routes_keep_the_strict_check``
goes red.

The R8 half needs ``TENANT_LADDER_DATABASE_URL``. A skip is not a pass::

    eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_projects_status_read_assignee_r8.py -v -rs
"""

from __future__ import annotations

import inspect
import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import text

pytest.importorskip("sqlalchemy")
pytest.importorskip("gateway.routes.projects", reason="gateway not installed")

from tests.unit import _r8_lists as r8_lists
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    promoted,
)
from tests.unit.test_projects_status_set_read_r8 import (
    _principal,
    _project,
    _session,
    _user,
)

MEMBER = "assignee@statusread.test"
STRANGER = "stranger@statusread.test"


def test_this_suite_is_named_in_the_ci_skip_guard() -> None:
    assert "tests/unit/test_projects_status_read_assignee_r8.py" in r8_lists.must_run()


def test_the_write_routes_keep_the_strict_check() -> None:
    from gateway.routes.projects import admin

    for fn in (admin.create_status, admin.create_type):
        src = inspect.getsource(fn)
        assert "load_readable_project" not in src
        assert "read=True" not in src
    for fn in (admin.list_statuses, admin.describe_status_set, admin.list_types):
        src = inspect.getsource(fn)
        assert "load_readable_project" in src or "read=True" in src, fn.__name__


def test_the_readable_project_checks_the_task_clause() -> None:
    from gateway.routes.projects import core

    src = inspect.getsource(core.load_readable_project)
    assert "task_visibility_clause(vis)" in src
    assert "organization_id = CAST(:vis_org AS uuid)" in src


async def test_a_visible_project_needs_no_second_query() -> None:
    from gateway.routes.projects import core

    calls: list[str] = []

    class _Db:
        async def execute(self, stmt, params=None):
            calls.append(str(stmt))
            return SimpleNamespace(fetchone=lambda: SimpleNamespace(id="p"))

    vis = SimpleNamespace(
        project_clause=lambda col="id": "TRUE", params={}, agent=None,
    )
    row = await core.load_readable_project(_Db(), vis, str(uuid.uuid4()))
    assert row.id == "p" and len(calls) == 1


async def test_a_hidden_project_with_no_task_keeps_the_original_404() -> None:
    from fastapi import HTTPException
    from gateway.routes.projects import core

    class _Db:
        async def execute(self, stmt, params=None):
            return SimpleNamespace(fetchone=lambda: None)

    vis = core.Visibility(
        unrestricted=False, email="a@x.test", groups=(),
        organization_id=str(uuid.uuid4()),
    )
    with pytest.raises(HTTPException) as err:
        await core.load_readable_project(_Db(), vis, str(uuid.uuid4()))
    assert err.value.status_code == 404 and err.value.detail == "Project not found"


def _task(c, org, project, status, number, *, assignee):
    tid = str(c.execute(text(
        "INSERT INTO pm_tasks (title, project_id, root_project_id, status_id,"
        " created_by, organization_id, task_number) VALUES ('t', CAST(:p AS uuid),"
        " CAST(:p AS uuid), CAST(:s AS uuid), 'seed@statusread.test',"
        " CAST(:o AS uuid), :n) RETURNING id"),
        {"p": project, "s": status, "o": org, "n": number}).scalar_one())
    if assignee:
        c.execute(text(
            "INSERT INTO pm_task_assignees (task_id, assignee, assigned_by,"
            " organization_id) VALUES (CAST(:t AS uuid), :e, 'seed@statusread.test',"
            " CAST(:o AS uuid))"), {"t": tid, "e": assignee, "o": org})
    return tid


@pytest.fixture(scope="module")
def world(promoted):  # noqa: F811
    a = promoted.org_a
    ns = SimpleNamespace(p=promoted, a=a)
    with promoted.admin_engine.begin() as c:
        _user(c, a, MEMBER)
        _user(c, a, STRANGER)
        ns.space = _project(c, a, "Statusread space", parent=None, owns=True)
        ns.other = _project(c, a, "Statusread other", parent=None, owns=True)
        ns.lane = str(c.execute(text(
            "INSERT INTO pm_task_statuses (project_id, name, color, position,"
            " category, organization_id) VALUES (CAST(:p AS uuid), 'In progress',"
            " 'gray', 10, 'active', CAST(:o AS uuid)) RETURNING id"),
            {"p": ns.space, "o": a}).scalar_one())
        ns.visible_task = _task(c, a, ns.space, ns.lane, 9101, assignee=MEMBER)
        _task(c, a, ns.space, ns.lane, 9102, assignee=None)
    ns.member_u = _principal(MEMBER, a, perms=("feature:projects",))
    ns.stranger_u = _principal(STRANGER, a, perms=("feature:projects",))
    return ns


async def _get(call, user, path: str):
    key = str(id(user))
    call.users[key] = user
    return await call.client.get(path, headers={"x-test-as": key})


@_DB_GATE
async def test_an_assignee_reads_the_lanes_of_the_task_project(world) -> None:
    w = world
    async with _session(w.p) as call:
        res = await _get(call, w.member_u, f"/projects/nodes/{w.space}/statuses")
        task = await _get(call, w.member_u, f"/projects/tasks/{w.visible_task}")
        sset = await _get(call, w.member_u, f"/projects/nodes/{w.space}/status-set")
        types = await _get(call, w.member_u, f"/projects/nodes/{w.space}/types")
    assert task.status_code == 200, task.text
    assert res.status_code == 200, res.text
    assert "In progress" in [r["name"] for r in res.json()["rows"]]
    # Only the tasks this member can open are counted: one of the two.
    assert sum(res.json()["counts"].values()) == 1
    assert sset.status_code == 200 and types.status_code == 200


@_DB_GATE
async def test_a_stranger_still_gets_the_404(world) -> None:
    w = world
    async with _session(w.p) as call:
        res = await _get(call, w.stranger_u, f"/projects/nodes/{w.space}/statuses")
        other = await _get(call, w.member_u, f"/projects/nodes/{w.other}/statuses")
    assert res.status_code == 404 and other.status_code == 404
