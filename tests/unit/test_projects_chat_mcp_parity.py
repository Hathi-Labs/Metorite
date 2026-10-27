"""The Projects chat and the MCP edge send the same request (WS-64 chat wave 2).

The audit of 2026-10-26 found the two clients of ``/projects`` apart: the
chat filed a subtask in the project that the model named, and the edge used
the parent's project. Both sent a bare due day. This file holds the two
together, one act at a time. For each act, the chat tool (``skill_projects``)
and the MCP tool (``agent_edge.tools``) run against their own fake gateway,
and the test compares the method, the route and the JSON body that each one
sent. A change to one client that the other does not make fails here.

The acts: a create, a create of a subtask, a change of the due date and a
re-parent. ``file_tasks`` moves tasks to another project. The edge has no
such tool (an agent moves a task inside its project only, MCP-G2), so its
row checks that the edge still sends no cross-project move.

No gateway, no database. The edge runs through its real ``/mcp`` endpoint
over its EA-6d fake gateway (``test_mcp_edge_assignees``).

R7, the mutations (run after a commit, because ``git checkout`` restores HEAD):

* **P1** ``writes._prepare_new_task`` drops ``parent_task_id`` from the body
  -> ``test_a_subtask_create_sends_the_same_body``.
* **P2** ``agent_edge.tools.CREATE_BODY_KEYS`` loses ``due_at``
  -> ``test_a_create_sends_the_same_body``.
* **P3** the chat's re-parent sends ``project_id`` too
  -> ``test_a_reparent_sends_the_same_body``.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

pytest.importorskip("skill_projects", reason="skill-projects not installed")
pytest.importorskip("agent_edge", reason="agent_edge not installed")

from agent_edge import tools
from skill_projects import writes as W

from tests.unit import test_mcp_edge_assignees as edge_suite
from tests.unit._projects_agent_fakes import approve, fake_gateway, writes
from tests.unit.test_mcp_edge_assignees import edge  # noqa: F401 - a fixture
from tests.unit.test_mcp_edge_task_writes import redis_fake  # noqa: F401 - a fixture

TASK, PROJECT, PARENT = edge_suite.TASK, edge_suite.PROJECT, edge_suite.PARENT
V1 = edge_suite.V1
S_TODO = edge_suite.t.S_TODO
DAY = "2026-10-30"


def _chat_gateway(call: dict) -> Any:
    """The chat's fake: the same task, parent and project as the edge's."""
    path, method = call["path"], call["method"]
    if method != "GET":
        return {"id": TASK, "task_number": 7, "title": "x", "status_id": S_TODO,
                "project_id": PROJECT, "updated_at": V1}
    if path == f"/projects/nodes/{PROJECT}":
        return {"id": PROJECT, "name": "Board", "parent_project_id": "a-space",
                "kind": "project", "personal": False, "status": "active"}
    if path == f"/projects/nodes/{PROJECT}/statuses":
        return {"rows": [{"id": S_TODO, "name": "To do", "category": "todo"}]}
    if path == f"/projects/tasks/{PARENT}":
        return {"id": PARENT, "task_number": 3, "title": "Parent", "project_id": PROJECT,
                "root_project_id": PROJECT, "status_id": S_TODO}
    if path == f"/projects/tasks/{TASK}":
        return {"id": TASK, "task_number": 7, "title": "old title", "project_id": PROJECT,
                "root_project_id": PROJECT, "status_id": S_TODO, "updated_at": V1}
    return {"rows": [], "total": 0, "people": [], "agents": []}


async def _chat(monkeypatch: Any, fn: Any, **kwargs: Any) -> list[tuple[str, str, Any]]:
    calls = fake_gateway(monkeypatch, _chat_gateway)
    approve(monkeypatch)
    await fn(**kwargs)
    return [(c["method"], c["path"], c["json"]) for c in writes(calls)]


async def _mcp(gateway: Any, name: str, args: dict[str, Any]) -> list[tuple[str, str, Any]]:
    await edge_suite._tool(name, args)
    return [(r.method, r.path, json.loads(r.body) if r.body else None)
            for r in edge_suite._writes(gateway)]


async def test_a_create_sends_the_same_body(monkeypatch, edge) -> None:  # noqa: F811
    chat = await _chat(monkeypatch, W.create_task, project_id=PROJECT, title="Ship it",
                       due=DAY)
    mcp = await _mcp(edge.gateway, "create_task", {
        "project_id": PROJECT, "title": "Ship it", "due_at": DAY,
        "idempotency_key": "parity-create-1"})
    assert chat == mcp == [("POST", "/projects/tasks",
                            {"project_id": PROJECT, "title": "Ship it", "due_at": DAY})]


async def test_a_subtask_create_sends_the_same_body(monkeypatch, edge) -> None:  # noqa: F811
    """Both clients send the parent's own project (item 1)."""
    chat = await _chat(monkeypatch, W.create_task, project_id=PROJECT, title="Step",
                       parent_task_id=PARENT)
    mcp = await _mcp(edge.gateway, "create_task", {
        "project_id": PROJECT, "title": "Step", "parent_task_id": PARENT,
        "idempotency_key": "parity-create-2"})
    assert chat == mcp == [("POST", "/projects/tasks", {
        "project_id": PROJECT, "title": "Step", "parent_task_id": PARENT})]


async def test_a_due_change_sends_the_same_body(monkeypatch, edge) -> None:  # noqa: F811
    """Both send the bare day. The gateway makes it noon in the member's
    zone (``due.member_due_values``), for each caller the same."""
    chat = await _chat(monkeypatch, W.update_task, task_id=TASK, due=DAY)
    mcp = await _mcp(edge.gateway, "update_task", {
        "task_id": TASK, "expected_version": V1, "due_at": DAY})
    assert [(m, p, b) for m, p, b in chat] == [("PATCH", f"/projects/tasks/{TASK}",
                                                 {"due_at": DAY})]
    assert mcp == chat


async def test_a_reparent_sends_the_same_body(monkeypatch, edge) -> None:  # noqa: F811
    chat = await _chat(monkeypatch, W.move_task, task_ids=TASK, parent_task_id=PARENT)
    mcp = await _mcp(edge.gateway, "update_task", {
        "task_id": TASK, "expected_version": V1, "parent_task_id": PARENT})
    assert chat == mcp == [("POST", f"/projects/tasks/{TASK}/move",
                            {"parent_task_id": PARENT})]


def test_the_edge_sends_no_move_to_another_project() -> None:
    """``file_tasks`` moves tasks between projects, through the bulk move
    or a move with ``project_id``. The edge has no tool for either, so an
    agent's move cannot cross a project where the chat's card would."""
    from acb_auth.agent_oauth import MCP_WRITE_ROUTES

    routes = set(MCP_WRITE_ROUTES)
    assert ("POST", "/projects/tasks") in routes, "the shape of the set changed"
    assert ("POST", "/projects/tasks/move") not in routes
    assert "project_id" not in tools.UPDATE_BODY_KEYS
