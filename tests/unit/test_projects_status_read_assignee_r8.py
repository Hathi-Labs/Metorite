"""Task 3865 - an assignee sets a status by name, with no wider project reads.

Production, 18 Nov 2026: the chat set a task to "In progress". It read the task
(200), then ``GET /projects/nodes/<id>/statuses`` answered 404 "Project not
found". ``update_task`` passes the task's own ``project_id``. The assignee arm
of ``load_visible_task`` lets the task in, and ``load_visible_project`` has no
such arm. Widening the project routes leaks (tag counts over sealed trees, the
names of ancestors), so they stay exactly as they were. The chat resolves the
status name through ``GET /projects/my/tasks/{id}/lanes`` instead: lanes only,
behind the task.

R7 fence. Mutation M1: put a task arm in ``load_visible_project`` and
``test_the_project_routes_stay_closed_to_an_assignee`` goes red. Mutation M2:
drop the 404 fall-back in ``writes._lanes_of_task`` and
``test_an_assignee_resolves_a_status_name_through_the_task`` goes red.

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


def test_the_project_routes_have_no_task_arm() -> None:
    """The project routes keep the project check and nothing wider."""
    from gateway.routes.projects import admin, core

    assert not hasattr(core, "load_readable_project")
    for fn in (admin.list_statuses, admin.describe_status_set, admin.list_types):
        assert "task_visibility_clause" not in inspect.getsource(fn)
    assert "_status_owner_for" in inspect.getsource(admin.list_statuses)


class _Refusal404(Exception):
    pass


async def test_an_assignee_resolves_a_status_name_through_the_task(monkeypatch) -> None:
    from skill_projects import writes

    calls: list[str] = []

    async def _get(path, *a, **k):
        calls.append(path)
        if "/nodes/" in path:
            raise writes.GatewayRefusal("not found", status=404)
        return {"rows": [{"id": "s1", "name": "In progress", "category": "in_progress"},
                         {"id": "s2", "name": "Done", "category": "done"}]}

    monkeypatch.setattr(writes, "get", _get)
    pid, tid = str(uuid.uuid4()), str(uuid.uuid4())
    row = await writes._resolve_status(pid, "in progress", tid)
    assert row["id"] == "s1"
    assert calls[-1] == f"/projects/my/tasks/{tid}/lanes"


async def test_a_stranger_still_gets_the_refusal(monkeypatch) -> None:
    from skill_projects import writes

    async def _get(path, *a, **k):
        raise writes.GatewayRefusal("not found", status=404)

    monkeypatch.setattr(writes, "get", _get)
    with pytest.raises(writes.GatewayRefusal) as err:
        await writes._resolve_status(str(uuid.uuid4()), "Done", str(uuid.uuid4()))
    assert err.value.status == 404
    # With no task named, the project read is the only read.
    with pytest.raises(writes.GatewayRefusal):
        await writes._resolve_status(str(uuid.uuid4()), "Done")


async def test_a_non_404_does_not_fall_back(monkeypatch) -> None:
    from skill_projects import writes

    calls: list[str] = []

    async def _get(path, *a, **k):
        calls.append(path)
        raise writes.GatewayRefusal("boom", status=500)

    monkeypatch.setattr(writes, "get", _get)
    with pytest.raises(writes.GatewayRefusal):
        await writes._resolve_status(str(uuid.uuid4()), "Done", str(uuid.uuid4()))
    assert len(calls) == 1


async def test_vocabulary_lists_the_task_lanes_for_an_assignee(monkeypatch) -> None:
    from skill_projects import reads

    async def _get(path, *a, **k):
        if "/nodes/" in path:
            raise reads.GatewayRefusal("not found", status=404)
        return {"rows": [{"id": "s1", "name": "In progress", "category": "in_progress"}]}

    monkeypatch.setattr(reads, "get", _get)
    pid, tid = str(uuid.uuid4()), str(uuid.uuid4())
    out = await reads.vocabulary(pid, tid)
    assert "In progress" in out and "not its project" in out
    assert "Tags (" not in out and "Types (" not in out
    with pytest.raises(reads.GatewayRefusal):
        await reads.vocabulary(pid)  # no task: the refusal stands


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
            " 'gray', 10, 'in_progress', CAST(:o AS uuid)) RETURNING id"),
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
async def test_the_project_routes_stay_closed_to_an_assignee(world) -> None:
    w = world
    async with _session(w.p) as call:
        task = await _get(call, w.member_u, f"/projects/tasks/{w.visible_task}")
        paths = ("statuses", "status-set", "types", "tags", "fields")
        got = {
            name: (await _get(call, w.member_u, f"/projects/nodes/{w.space}/{name}")).status_code
            for name in paths
        }
    assert task.status_code == 200, task.text
    assert got == dict.fromkeys(paths, 404), got


@_DB_GATE
async def test_an_assignee_reads_the_task_lanes_and_sets_a_status(world) -> None:
    w = world
    async with _session(w.p) as call:
        lanes = await _get(call, w.member_u, f"/projects/my/tasks/{w.visible_task}/lanes")
        assert lanes.status_code == 200, lanes.text
        rows = lanes.json()["rows"]
        assert [r["name"] for r in rows] == ["In progress"]
        assert set(lanes.json()) == {"rows", "total"}  # no counts, no project name
        key = str(id(w.member_u))
        call.users[key] = w.member_u
        patched = await call.client.patch(
            f"/projects/tasks/{w.visible_task}", json={"status_id": rows[0]["id"]},
            headers={"x-test-as": key},
        )
        assert patched.status_code == 200, patched.text


@_DB_GATE
async def test_a_stranger_still_gets_the_404(world) -> None:
    w = world
    async with _session(w.p) as call:
        lanes = await _get(call, w.stranger_u, f"/projects/my/tasks/{w.visible_task}/lanes")
        project = await _get(call, w.stranger_u, f"/projects/nodes/{w.space}/statuses")
    assert lanes.status_code == 404 and project.status_code == 404
