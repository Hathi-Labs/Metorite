"""propose_plan files its tasks in a PROJECT, never on a space (2026-10-26).

Review of the no-tasks-in-a-space change. With no parent, propose_plan made a
new team root and posted every task onto it, and the gateway answered 422 after
the member approved. The write order is now: the space (no parent), the project
inside it, then the tasks in the project.

No gateway, no database: the client is patched at httpx.
"""

from __future__ import annotations

from typing import Any

import pytest

pytest.importorskip("skill_projects", reason="skill-projects not installed")

import skill_projects

from tests.unit._projects_agent_fakes import approve, fake_gateway, form_stub

SPACE = "0f8fad5b-d9cb-469f-a165-70867728950e"
HR = "1f8fad5b-d9cb-469f-a165-70867728950e"
TASK = "9f8fad5b-d9cb-469f-a165-70867728950e"
S1 = "af8fad5b-d9cb-469f-a165-70867728950e"
NEW_SPACE = "bf8fad5b-d9cb-469f-a165-70867728950e"
NEW_PROJECT = "cf8fad5b-d9cb-469f-a165-70867728950e"

TASKS = (
    '[{"title": "Call the vendor", "effort_mins": 60, "due": "2026-10-30"},'
    ' {"title": "Draft the brief", "effort_mins": 30, "due": "2026-10-31"}]'
)
FORM = {"Plan": 'Review plan — {"project": {"name": "Q4 launch"}, "tasks": ' + TASKS + "}"}

NODES = {
    SPACE: {"id": SPACE, "name": "Company Operations", "parent_project_id": None,
            "kind": "project", "personal": False},
    HR: {"id": HR, "name": "HR", "parent_project_id": SPACE, "kind": "project",
         "personal": False},
}


def _gateway(call: dict) -> Any:
    method, path = call["method"], call["path"]
    if method == "POST" and path == "/projects/nodes":
        body = call["json"]
        nid = NEW_PROJECT if body.get("parent_project_id") else NEW_SPACE
        return {"id": nid, "name": body["name"]}
    if method == "POST" and path == "/projects/tasks":
        return {"id": TASK, "task_number": 1, "title": call["json"]["title"],
                "status_id": S1, "project_id": call["json"]["project_id"]}
    if method == "GET":
        for pid, node in NODES.items():
            if path == f"/projects/nodes/{pid}":
                return node
    return {"rows": [], "total": 0, "people": [], "agents": [], "checks": []}


def _posts(calls: list[dict], path: str) -> list[dict]:
    return [c for c in calls if c["method"] == "POST" and c["path"] == path]


async def test_no_parent_makes_a_space_and_a_project_and_files_in_the_project(
    monkeypatch,
) -> None:
    form_stub(monkeypatch, FORM)
    cards = approve(monkeypatch)
    calls = fake_gateway(monkeypatch, _gateway)
    out = await skill_projects.propose_plan("Q4 launch", TASKS)
    assert "a new space «Q4 launch» with a project «Q4 launch»" in cards[0]["detail"]
    nodes = _posts(calls, "/projects/nodes")
    assert len(nodes) == 2
    assert not nodes[0]["json"].get("parent_project_id"), "the space is a root"
    assert nodes[1]["json"]["parent_project_id"] == NEW_SPACE
    tasks = _posts(calls, "/projects/tasks")
    assert len(tasks) == 2
    ids = {t["json"]["project_id"] for t in tasks}
    assert ids == {NEW_PROJECT}, "no task may be posted on the space"
    assert f"project_id: {NEW_PROJECT}" in out


async def test_a_space_parent_gets_a_project_and_the_tasks_go_in_the_project(
    monkeypatch,
) -> None:
    form_stub(monkeypatch, FORM)
    cards = approve(monkeypatch)
    calls = fake_gateway(monkeypatch, _gateway)
    await skill_projects.propose_plan("Q4 launch", TASKS, parent_project_id=SPACE)
    assert len(cards) == 1, "a project inside a space is allowed"
    nodes = _posts(calls, "/projects/nodes")
    assert len(nodes) == 1 and nodes[0]["json"]["parent_project_id"] == SPACE
    ids = {t["json"]["project_id"] for t in _posts(calls, "/projects/tasks")}
    assert ids == {NEW_PROJECT} and SPACE not in ids


async def test_a_project_parent_gets_a_subproject_and_the_tasks_go_in_it(monkeypatch) -> None:
    form_stub(monkeypatch, FORM)
    approve(monkeypatch)
    calls = fake_gateway(monkeypatch, _gateway)
    await skill_projects.propose_plan("Q4 launch", TASKS, parent_project_id=HR)
    nodes = _posts(calls, "/projects/nodes")
    assert len(nodes) == 1 and nodes[0]["json"]["parent_project_id"] == HR
    ids = {t["json"]["project_id"] for t in _posts(calls, "/projects/tasks")}
    assert ids == {NEW_PROJECT} and HR not in ids


def test_the_docstring_says_a_new_space_gets_a_project() -> None:
    doc = skill_projects.propose_plan.__doc__ or ""
    assert "empty makes a space." not in doc
    assert "one project of that name inside" in doc
