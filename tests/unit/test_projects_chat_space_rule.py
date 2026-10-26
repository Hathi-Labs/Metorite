"""The Projects chat and the space rule (2026-10-26).

Production, 2026-10-26, org Fracktal. The member had the space "Company
Operations" open and asked for tasks. The chat filed six tasks on the space
itself, where no board draws them. Then ``projects_tree`` labelled the five
full projects inside the space as folders, and the model told the member the
space held "five empty folders and no projects".

This file holds four things:

1. ``projects_tree`` derives a level from the kind and the ancestry, never
   from the depth, and it prints the task counts the tree carries.
2. Every chat door that places a task refuses a team space BEFORE its card,
   and lists the projects inside. A personal root is not a space.
3. The gateway's 422 reaches the model as text, so it can recover.
4. The page is a hint only (owner, 2026-10-26). No tool reads the open page,
   a name resolves over the whole tree, the personal tree too, and the same
   request gives the same calls whichever node the page has open.

No gateway, no database: the client is patched at httpx.
"""

from __future__ import annotations

import ast
import inspect
from typing import Any

import pytest

pytest.importorskip("skill_projects", reason="skill-projects not installed")

import skill_projects
from skill_projects import reads as R
from skill_projects import writes as W

from tests.unit._projects_agent_fakes import (
    SKILL_DIR,
    FakeResponse,
    approve,
    deny,
    fake_gateway,
    writes,
)

SPACE = "0f8fad5b-d9cb-469f-a165-70867728950e"
HR = "1f8fad5b-d9cb-469f-a165-70867728950e"
FIN = "2f8fad5b-d9cb-469f-a165-70867728950e"
SUB = "3f8fad5b-d9cb-469f-a165-70867728950e"
FOLDER = "4f8fad5b-d9cb-469f-a165-70867728950e"
INNER = "5f8fad5b-d9cb-469f-a165-70867728950e"
MINE = "6f8fad5b-d9cb-469f-a165-70867728950e"
ORPHAN = "7f8fad5b-d9cb-469f-a165-70867728950e"
OTHER_SPACE = "8f8fad5b-d9cb-469f-a165-70867728950e"
TASK = "9f8fad5b-d9cb-469f-a165-70867728950e"
S1 = "af8fad5b-d9cb-469f-a165-70867728950e"

TREE = {
    "total": 7,
    "rows": [
        {
            "id": SPACE, "name": "Company Operations", "parent_project_id": None,
            "kind": "project", "tasks": 446, "done": 40, "cancelled": 2,
            "children": [
                {
                    "id": HR, "name": "HR", "parent_project_id": SPACE,
                    "kind": "project", "tasks": 398, "done": 30, "cancelled": 1,
                    "children": [
                        {"id": SUB, "name": "Hiring", "parent_project_id": HR,
                         "kind": "project", "tasks": 12, "done": 2, "cancelled": 0,
                         "children": []},
                    ],
                },
                {"id": FIN, "name": "Finance & Accounting", "parent_project_id": SPACE,
                 "kind": "project", "tasks": 39, "done": 8, "cancelled": 1, "children": []},
            ],
        },
        {
            "id": OTHER_SPACE, "name": "Fracktory", "parent_project_id": None,
            "kind": "project", "tasks": 5, "done": 0, "cancelled": 0,
            "children": [
                {
                    "id": FOLDER, "name": "Print services", "parent_project_id": OTHER_SPACE,
                    "kind": "folder", "tasks": 5, "done": 0, "cancelled": 0,
                    "children": [
                        {"id": INNER, "name": "FDM prints", "parent_project_id": FOLDER,
                         "kind": "project", "tasks": 5, "done": 0, "cancelled": 0,
                         "children": []},
                    ],
                },
            ],
        },
        # A project whose parent this member cannot see: it draws at the top,
        # and it is NOT a space.
        {"id": ORPHAN, "name": "Shared launch", "parent_project_id": "hidden-parent",
         "kind": "project", "tasks": 3, "done": 1, "cancelled": 0, "children": []},
    ],
    "personal": [
        {"id": MINE, "name": "My tasks", "parent_project_id": None, "kind": "project",
         "personal": True, "tasks": 4, "done": 0, "cancelled": 0, "children": []},
    ],
}

NODES = {
    SPACE: {"id": SPACE, "name": "Company Operations", "parent_project_id": None,
            "kind": "project", "personal": False, "status": "active"},
    HR: {"id": HR, "name": "HR", "parent_project_id": SPACE, "kind": "project",
         "personal": False, "status": "active"},
    MINE: {"id": MINE, "name": "My tasks", "parent_project_id": None, "kind": "project",
           "personal": True, "status": "active"},
}


def _gateway(call: dict) -> Any:
    path, method = call["path"], call["method"]
    if method != "GET":
        return {"id": TASK, "task_number": 1, "title": "x", "status_id": S1,
                "project_id": HR}
    if path == "/projects/tree":
        return TREE
    for pid, node in NODES.items():
        if path == f"/projects/nodes/{pid}":
            return node
        if path == f"/projects/nodes/{pid}/statuses":
            return {"rows": [{"id": S1, "name": "To do", "category": "todo",
                              "is_default": True}]}
    if path == f"/projects/tasks/{TASK}":
        return {"id": TASK, "task_number": 7, "title": "Onboard Priya",
                "project_id": HR, "root_project_id": SPACE, "status_id": S1}
    return {"rows": [], "total": 0, "people": [], "agents": []}


# ── 1. projects_tree: the level from the kind and the ancestry ─────────────

async def test_the_tree_labels_each_level_from_kind_and_ancestry(monkeypatch) -> None:
    fake_gateway(monkeypatch, _gateway)
    text = await R.projects_tree()
    assert "- «Company Operations» [space]" in text
    assert "- «HR» [project]" in text, "a project in a space is a project, not a folder"
    assert "- «Finance & Accounting» [project]" in text
    assert "- «Hiring» [subproject]" in text
    assert "- «Print services» [folder]" in text
    assert "- «FDM prints» [project]" in text, "a folder adds no generation"
    assert "- «Shared launch» [project]" in text, "a hidden parent is not a space"
    assert "[folder] · folder" not in text.replace("«Print services» [folder] · folder", "")


async def test_the_tree_prints_the_counts_so_empty_is_never_inferred(monkeypatch) -> None:
    fake_gateway(monkeypatch, _gateway)
    text = await R.projects_tree()
    assert "«HR» [project] · tasks 398 (done 30, cancelled 1)" in text
    assert "«Company Operations» [space] · tasks 446 (done 40, cancelled 2)" in text


def test_tree_level_is_the_gateways_rule() -> None:
    assert R.tree_level(False, 1) == "space"
    assert R.tree_level(False, 2) == "project"
    assert R.tree_level(False, 3) == "subproject"
    assert R.tree_level(True, 2) == "folder"


# ── 2. Every chat door refuses a space before its card ─────────────────────

def test_a_personal_root_is_not_a_space() -> None:
    assert W.is_team_space(NODES[SPACE]) is True
    assert W.is_team_space(NODES[MINE]) is False
    assert W.is_team_space(NODES[HR]) is False
    # A partial read is not judged. The gateway still refuses the write.
    assert W.is_team_space({"id": SPACE, "name": "x"}) is False


async def test_create_task_on_a_space_lists_the_projects_and_asks(monkeypatch) -> None:
    calls = fake_gateway(monkeypatch, _gateway)
    cards = approve(monkeypatch)
    text = await W.create_task(project_id=SPACE, title="Onboard Priya")
    assert "«Company Operations» is a space" in text
    assert "nothing was created" in text
    assert f"«HR» · full_id: {HR}" in text
    assert f"«HR / Hiring» · full_id: {SUB}" in text
    assert "ask the member which project" in text
    assert cards == [], "the refusal comes before the card"
    assert writes(calls) == []


async def test_create_task_in_a_project_still_draws_its_card(monkeypatch) -> None:
    fake_gateway(monkeypatch, _gateway)
    cards = deny(monkeypatch)
    await W.create_task(project_id=HR, title="Onboard Priya")
    assert len(cards) == 1


async def test_create_task_on_the_personal_root_is_not_refused(monkeypatch) -> None:
    fake_gateway(monkeypatch, _gateway)
    cards = deny(monkeypatch)
    text = await W.create_task(project_id=MINE, title="Call the bank")
    assert "is a space" not in text
    assert len(cards) == 1


async def test_create_tasks_on_a_space_is_refused_before_the_card(monkeypatch) -> None:
    from skill_projects import forms as F

    calls = fake_gateway(monkeypatch, _gateway)
    cards = approve(monkeypatch)
    text = await F.create_tasks(project_id=SPACE, tasks='[{"title": "Onboard Priya"}]')
    assert "«Company Operations» is a space" in text
    assert cards == []
    assert writes(calls) == []


async def test_capture_intake_on_a_space_is_refused(monkeypatch) -> None:
    from skill_projects import inbox as I

    calls = fake_gateway(monkeypatch, _gateway)
    cards = approve(monkeypatch)
    text = await I.capture_intake(title="From the inbox", project_id=SPACE)
    assert "«Company Operations» is a space" in text
    assert cards == []
    assert writes(calls) == []


async def test_move_task_into_a_space_is_refused(monkeypatch) -> None:
    calls = fake_gateway(monkeypatch, _gateway)
    cards = approve(monkeypatch)
    text = await W.move_task(task_ids=TASK, destination_project_id=SPACE)
    assert "«Company Operations» is a space" in text
    assert "nothing was moved" in text
    assert cards == []
    assert writes(calls) == []


async def test_file_tasks_marks_a_space_row_and_leaves_it_out(monkeypatch) -> None:
    fake_gateway(monkeypatch, _gateway)
    rows = [W._FileRow(1, TASK, SPACE, "")]
    await W._file_destinations(rows)
    assert "is a space" in rows[0].why


# ── 3. The gateway's 422 reaches the model ──────────────────────────────────

async def test_the_gateways_space_refusal_reaches_the_model(monkeypatch) -> None:
    """A node read without the parent key is not judged by the chat, so the
    POST goes out. The gateway's words then come back as text."""
    pytest.importorskip("agent_framework", reason="agent_framework not installed")
    from tests.unit._projects_agent_fakes import load_agent_module

    agent = load_agent_module("projects_assistant_agent_space").build_agents()[0]
    tool = next(t for t in agent.default_options["tools"] if getattr(t, "name", "") == "create_task")
    said = (
        "Company Operations is a space. A space holds projects, not tasks. "
        "Create the task in a project inside it."
    )

    def answer(call: dict) -> Any:
        if call["method"] == "POST" and call["path"] == "/projects/tasks":
            return FakeResponse({"detail": said}, status_code=422)
        if call["path"] == f"/projects/nodes/{SPACE}":
            return {"id": SPACE, "name": "Company Operations", "status": "active"}
        return _gateway(call)

    fake_gateway(monkeypatch, answer)
    approve(monkeypatch)
    result = await tool.invoke(arguments={"project_id": SPACE, "title": "Onboard Priya"})
    text = result if isinstance(result, str) else "\n".join(
        str(getattr(c, "text", "") or "") for c in result
    )
    assert f"Gateway said: «{said}»" in text


# ── 4. The page is a hint, never a dependency ──────────────────────────────

#: Parameter names that would carry the open page. `page` alone is paging,
#: and `context` is a GTD context such as @office.
_PAGE_PARAMS = {
    "page_context", "focus", "focus_id", "selected", "selected_project",
    "open_node", "open_project", "current_project",
}


def test_no_projects_tool_takes_or_reads_the_open_page() -> None:
    """Every tool takes its project from its own arguments. None has a
    parameter for the open page, and no module reads a page ContextVar."""
    for name in skill_projects.__all__:
        params = inspect.signature(getattr(skill_projects, name)).parameters
        bad = [p for p in params if p.lower() in _PAGE_PARAMS]
        assert not bad, f"{name} takes the open page: {bad}"
    for path in SKILL_DIR.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "ContextVar":
                label = ast.unparse(node.args[0]) if node.args else ""
                assert not any(w in label.lower() for w in ("page", "focus", "selected")), (
                    f"{path.name} keeps the open page in a ContextVar: {label}"
                )


@pytest.mark.parametrize("page", ["space", "project", "none"])
async def test_the_same_request_gives_the_same_calls_on_any_page(monkeypatch, page) -> None:
    """Owner, 2026-10-26: the chat works the same whichever node is open.
    The page reaches the model as text, never the tool, so the tool's calls
    for one request do not change with the page."""
    from skill_projects import choices

    monkeypatch.setattr(choices, "assist_on", lambda: True)
    calls = fake_gateway(monkeypatch, _gateway)
    cards = deny(monkeypatch)
    # The page is not an input of the tool. Each run names the project by NAME.
    await W.create_task(project_id="HR", title="Onboard Priya")
    shape = [(c["method"], c["path"]) for c in calls]
    assert shape[0] == ("GET", "/projects/tree")
    assert ("GET", f"/projects/nodes/{HR}") in shape
    assert len(cards) == 1
    assert writes(calls) == []
    _SHAPES[page] = shape
    if len(_SHAPES) == 3:
        assert _SHAPES["space"] == _SHAPES["project"] == _SHAPES["none"]


_SHAPES: dict[str, list[tuple[str, str]]] = {}


async def test_a_name_resolves_in_the_personal_tree_too(monkeypatch) -> None:
    from skill_projects import choices

    monkeypatch.setattr(choices, "assist_on", lambda: True)
    fake_gateway(monkeypatch, _gateway)
    pid, name = await W._project_ref("My tasks")
    assert (pid, name) == (MINE, "My tasks")


async def test_two_projects_with_one_name_ask_and_never_pick(monkeypatch) -> None:
    from skill_projects import choices
    from skill_projects.client import GatewayRefusal

    twin = {"id": INNER, "name": "HR", "parent_project_id": FOLDER, "kind": "project",
            "children": []}
    tree = {**TREE, "rows": [*TREE["rows"], {**TREE["rows"][1], "children": [twin]}]}

    def answer(call: dict) -> Any:
        return tree if call["path"] == "/projects/tree" else _gateway(call)

    monkeypatch.setattr(choices, "assist_on", lambda: True)
    fake_gateway(monkeypatch, answer)
    with pytest.raises(GatewayRefusal) as refused:
        await W._project_ref("HR")
    assert "matches more than one project" in str(refused.value)
