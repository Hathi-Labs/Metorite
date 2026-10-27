"""WS-64 chat wave 2 — the Projects chat and the MCP edge match the app.

An audit of origin/main (2026-10-26) compared the chat tools
(``skill_projects``) and the MCP edge (``agent_edge.tools``) with the
Projects app and the gateway. This file holds the hermetic half of each fix.
The server rules run on a real Postgres in ``test_projects_chat_wave2_r8.py``.

1. A subtask in another project than its parent is refused before the card.
2. A re-parent across projects is refused before the card.
3. A due day renders in the member's zone (the write is the gateway's).
4. An archived project is refused before the card, for a full_id too.
5. ``projects_tree`` lists the personal tree, and "my X" prefers it.
6. The ``bulk_update`` receipt prints the values it applied.
7. ``add_subtasks`` ends a failed step in a partial receipt.
8. ``list_tasks`` names six categories and reads triage on request, and
   ``project_summary`` prints open, done and cancelled apart.
10. ``get_project`` of the MCP edge marks the lane that a create uses.
11. ``move_projects`` moves several nodes with one card, and a failed
    sibling write in ``move_project`` ends in a receipt.
12. The ``create_project`` card names the level of the new node.
13. A run that fails with no answer saves a notice, never an empty reply.

No gateway, no database: the client is patched at httpx.

R7, the mutations (run after a commit, because ``git checkout`` restores HEAD):
each test names the function whose change it catches in its docstring.
"""

from __future__ import annotations

from typing import Any

import pytest

pytest.importorskip("skill_projects", reason="skill-projects not installed")

from skill_projects import forms as F
from skill_projects import guarded as G
from skill_projects import reads as R
from skill_projects import writes as W
from skill_projects.client import GatewayRefusal
from skill_projects.refusals import refusals_as_text

from tests.unit._projects_agent_fakes import (
    FakeResponse,
    answer_rows,
    approve,
    deny,
    fake_gateway,
    writes,
)

SPACE = "0a8fad5b-d9cb-469f-a165-70867728950e"
A = "1a8fad5b-d9cb-469f-a165-70867728950e"
B = "2a8fad5b-d9cb-469f-a165-70867728950e"
ARCH = "3a8fad5b-d9cb-469f-a165-70867728950e"
FOLDER = "4a8fad5b-d9cb-469f-a165-70867728950e"
SUB = "5a8fad5b-d9cb-469f-a165-70867728950e"
MINE = "6a8fad5b-d9cb-469f-a165-70867728950e"
MY_HIRING = "7a8fad5b-d9cb-469f-a165-70867728950e"
PARENT = "8a8fad5b-d9cb-469f-a165-70867728950e"
CHILD = "9a8fad5b-d9cb-469f-a165-70867728950e"
LANE = "aa8fad5b-d9cb-469f-a165-70867728950e"
DONE = "ba8fad5b-d9cb-469f-a165-70867728950e"

NODES: dict[str, dict[str, Any]] = {
    SPACE: {"id": SPACE, "name": "Company", "parent_project_id": None, "kind": "project",
            "personal": False, "status": "active", "level": "space"},
    A: {"id": A, "name": "Hiring", "parent_project_id": SPACE, "kind": "project",
        "personal": False, "status": "active", "level": "project"},
    B: {"id": B, "name": "Ops", "parent_project_id": SPACE, "kind": "project",
        "personal": False, "status": "active", "level": "project"},
    ARCH: {"id": ARCH, "name": "Old launch", "parent_project_id": SPACE, "kind": "project",
           "personal": False, "status": "active", "level": "project",
           "archived_at": "2026-10-01T00:00:00+00:00"},
    FOLDER: {"id": FOLDER, "name": "Print", "parent_project_id": SPACE, "kind": "folder",
             "personal": False, "status": "active", "level": "folder"},
    SUB: {"id": SUB, "name": "Interviews", "parent_project_id": A, "kind": "project",
          "personal": False, "status": "active", "level": "subproject"},
    MINE: {"id": MINE, "name": "My tasks", "parent_project_id": None, "kind": "project",
           "personal": True, "status": "active", "level": "space"},
    MY_HIRING: {"id": MY_HIRING, "name": "Hiring", "parent_project_id": MINE,
                "kind": "project", "personal": True, "status": "active", "level": "project"},
}

TREE = {
    "total": 6,
    "rows": [{
        **NODES[SPACE], "children": [
            {**NODES[A], "children": [{**NODES[SUB], "children": []}]},
            {**NODES[B], "children": []},
            {**NODES[FOLDER], "children": []},
        ],
    }],
    "personal": [{**NODES[MINE], "children": [{**NODES[MY_HIRING], "children": []}]}],
}

TASKS: dict[str, dict[str, Any]] = {
    PARENT: {"id": PARENT, "task_number": 8, "title": "Order the nozzle", "project_id": A,
             "root_project_id": SPACE, "status_id": LANE},
    CHILD: {"id": CHILD, "task_number": 9, "title": "Check the stock", "project_id": B,
            "root_project_id": SPACE, "status_id": LANE,
            "due_at": "2026-09-01T12:00:00+00:00", "assignees": ["pm@fracktal.in"]},
}


def _gateway(call: dict) -> Any:
    path, method = call["path"], call["method"]
    if method == "GET":
        if path == "/projects/tree":
            return TREE
        for pid, node in NODES.items():
            if path == f"/projects/nodes/{pid}":
                return node
            if path == f"/projects/nodes/{pid}/statuses":
                return {"rows": [{"id": LANE, "name": "To do", "category": "todo"},
                                 {"id": DONE, "name": "Done", "category": "done"}]}
            if path == f"/projects/nodes/{pid}/summary":
                return {"tasks": 3}
        for tid, task in TASKS.items():
            if path == f"/projects/tasks/{tid}":
                return task
        return {"rows": [], "total": 0, "people": [], "agents": []}
    return {"id": CHILD, "task_number": 10, "title": "x", "status_id": LANE, "project_id": A}


async def _as_text(fn: Any, **kwargs: Any) -> str:
    """The tool as the agent registers it: a refusal reaches the model as text."""
    return await refusals_as_text(fn)(**kwargs)


# ── 1. A subtask lives in its parent's project ──────────────────────────────

async def test_create_task_under_a_parent_of_another_project_is_refused(monkeypatch) -> None:
    """Catches ``writes._prepare_new_task`` without ``foreign_parent_refusal``."""
    calls = fake_gateway(monkeypatch, _gateway)
    cards = approve(monkeypatch)
    text = await _as_text(W.create_task, project_id=B, title="Step", parent_task_id=PARENT)
    assert "subtask lives in its parent's project" in text
    assert f"project_id {A}" in text
    assert cards == [], "the refusal comes before the card"
    assert writes(calls) == []


async def test_create_task_in_the_parents_project_draws_its_card(monkeypatch) -> None:
    fake_gateway(monkeypatch, _gateway)
    cards = deny(monkeypatch)
    await W.create_task(project_id=A, title="Step", parent_task_id=PARENT)
    assert len(cards) == 1


async def test_create_tasks_with_a_parent_of_another_project_is_refused(monkeypatch) -> None:
    calls = fake_gateway(monkeypatch, _gateway)
    cards = approve(monkeypatch)
    text = await _as_text(
        F.create_tasks, project_id=B,
        tasks=f'[{{"title": "Step", "parent_task_id": "{PARENT}"}}]',
    )
    assert "subtask lives in its parent's project" in text
    assert cards == []
    assert writes(calls) == []


def test_the_batch_card_never_offers_a_subtask_in_another_project() -> None:
    """``forms._parent_words`` named ", in another project" (item 1)."""
    words = F._parent_words({"task_number": 8, "title": "Order the nozzle", "project_id": A})
    assert words == "subtask of #8 «Order the nozzle»"
    assert "another project" not in words


# ── 2. A re-parent stays inside one project ────────────────────────────────

async def test_a_reparent_across_projects_is_refused_before_the_card(monkeypatch) -> None:
    """Catches ``writes._plan_reparent`` without ``foreign_parent_refusal``."""
    calls = fake_gateway(monkeypatch, _gateway)
    cards = approve(monkeypatch)
    text = await W.move_task(task_ids=CHILD, parent_task_id=PARENT)
    assert "subtask lives in its parent's project" in text
    assert "destination_project_id" in text
    assert cards == []
    assert writes(calls) == []


# ── 3. A due day in the member's zone ──────────────────────────────────────

def _zone_gateway(zone: str, due: str) -> Any:
    def answer(call: dict) -> Any:
        if call["path"] == "/projects/my/today":
            return {"today": "2026-10-27", "timezone": zone, "stored": True, "valid": True}
        if call["path"] == "/projects/tasks" and call["method"] == "GET":
            return {"rows": [{**TASKS[CHILD], "due_at": due}], "total": 1}
        return _gateway(call)
    return answer


@pytest.mark.parametrize("zone,due,day", [
    # Noon on 2026-10-30 in Los Angeles is 19:00 UTC on the same day.
    ("America/Los_Angeles", "2026-10-30T19:00:00+00:00", "2026-10-30"),
    # 02:00 UTC on the 31st is still the evening of the 30th there.
    ("America/Los_Angeles", "2026-10-31T02:00:00+00:00", "2026-10-30"),
    # Noon in Kolkata is 06:30 UTC on the same day.
    ("Asia/Kolkata", "2026-10-30T06:30:00+00:00", "2026-10-30"),
    # 20:00 UTC on the 29th is the early morning of the 30th there.
    ("Asia/Kolkata", "2026-10-29T20:00:00+00:00", "2026-10-30"),
])
async def test_list_tasks_names_the_day_in_the_members_zone(monkeypatch, zone, due, day) -> None:
    """Catches ``reads._day`` slicing the UTC string again."""
    calls = fake_gateway(monkeypatch, _zone_gateway(zone, due))
    text = await R.list_tasks()
    assert f"due {day}" in text
    assert [c["path"] for c in calls].count("/projects/my/today") == 1


async def test_a_page_with_no_due_instant_reads_no_zone(monkeypatch) -> None:
    calls = fake_gateway(monkeypatch, _zone_gateway("Asia/Kolkata", ""))
    await R.list_tasks()
    assert "/projects/my/today" not in [c["path"] for c in calls]


def test_the_zone_is_reset_after_the_block() -> None:
    with R.in_member_zone("America/Los_Angeles"):
        assert R._day("2026-10-31T02:00:00+00:00") == "2026-10-30"
    assert R._day("2026-10-31T02:00:00+00:00") == "2026-10-31"
    assert R._day("2026-10-30") == "2026-10-30"


# ── 4. An archived destination ─────────────────────────────────────────────

async def test_create_task_by_full_id_in_an_archived_project_is_refused(monkeypatch) -> None:
    """Catches ``writes.space_refusal`` without the archived check."""
    calls = fake_gateway(monkeypatch, _gateway)
    cards = approve(monkeypatch)
    text = await W.create_task(project_id=ARCH, title="Late task")
    assert "«Old launch» is archived, so nothing was created" in text
    assert cards == []
    assert writes(calls) == []


async def test_move_task_into_an_archived_project_is_refused(monkeypatch) -> None:
    calls = fake_gateway(monkeypatch, _gateway)
    cards = approve(monkeypatch)
    text = await W.move_task(task_ids=CHILD, destination_project_id=ARCH)
    assert "is archived, so nothing was moved" in text
    assert cards == []
    assert writes(calls) == []
    # The preview is a read, and the refusal comes before it too.
    assert "/projects/tasks/move/preview" not in [c["path"] for c in calls]


# ── 5. The personal tree ───────────────────────────────────────────────────

async def test_projects_tree_lists_the_personal_tree_in_its_own_section(monkeypatch) -> None:
    """Catches ``reads.projects_tree`` reading ``rows`` only."""
    fake_gateway(monkeypatch, _gateway)
    text = await R.projects_tree()
    assert "Your personal projects (only you see these):" in text
    head, mine = text.split("Your personal projects (only you see these):")
    assert "«My tasks» [personal]" in mine
    assert f"full_id: {MY_HIRING}" in mine
    assert MY_HIRING not in head


async def test_my_hiring_means_the_personal_one(monkeypatch) -> None:
    from skill_projects import choices

    monkeypatch.setattr(choices, "assist_on", lambda: True)
    fake_gateway(monkeypatch, _gateway)
    assert await W._project_ref("my Hiring") == (MY_HIRING, "Hiring")


async def test_a_team_and_a_personal_project_of_one_name_ask(monkeypatch) -> None:
    from skill_projects import choices

    monkeypatch.setattr(choices, "assist_on", lambda: True)
    fake_gateway(monkeypatch, _gateway)
    with pytest.raises(GatewayRefusal) as refused:
        await W._project_ref("Hiring")
    said = str(refused.value)
    assert f"(team, full_id {A})" in said
    assert f"(personal, full_id {MY_HIRING})" in said
    assert "Ask the member which one" in said


# ── 6. The bulk receipt prints what it applied ─────────────────────────────

async def test_the_bulk_receipt_prints_the_applied_values(monkeypatch) -> None:
    """Catches ``guarded.bulk_update`` printing the rows it read before the card."""
    def answer(call: dict) -> Any:
        if call["method"] == "POST" and call["path"] == "/projects/tasks/bulk":
            return {"requested": 1, "applied": 1, "skipped": [], "failed": [],
                    "results": [{"task_id": CHILD, "changed": ["due_at", "status"],
                                 "status": "Done"}]}
        return _gateway(call)

    fake_gateway(monkeypatch, answer)
    approve(monkeypatch)
    text = await G.bulk_update(task_ids=CHILD, status="Done", due="2026-10-30",
                               tags_add="urgent")
    assert "due 2026-10-30" in text
    assert "status «Done»" in text
    assert "2026-09-01" not in text


def test_the_applied_view_merges_tags_assignees_and_the_shelf() -> None:
    task = {"id": CHILD, "tags": ["a", "b"], "assignees": ["x@y.test"], "category": "todo",
            "completed_at": None}
    after = G.bulk_applied_view(task, {
        "patch": {"status": "Done"}, "tags_add": ["c"], "tags_remove": ["a"],
        "assignees_add": ["z@y.test"],
    }, {"status": "Done"})
    assert after["tags"] == ["b", "c"]
    assert after["assignees"] == ["x@y.test", "z@y.test"]
    assert after["status_name"] == "Done"
    assert "category" not in after
    assert G.bulk_applied_view(task, {"action": "archive"}, {})["archived_at"]


# ── 7. add_subtasks ends a failed step in its receipt ──────────────────────

async def test_add_subtasks_reports_a_partial_result(monkeypatch) -> None:
    """Catches ``writes.add_subtasks`` raising after a step already exists."""
    made: list[str] = []

    def answer(call: dict) -> Any:
        if call["method"] == "POST" and call["path"] == "/projects/tasks":
            title = call["json"]["title"]
            if title == "Two":
                return FakeResponse({"detail": "A title is too long."}, status_code=422)
            made.append(title)
            return {"id": CHILD, "task_number": 20 + len(made), "title": title,
                    "status_id": LANE, "project_id": A}
        return _gateway(call)

    fake_gateway(monkeypatch, answer)
    approve(monkeypatch)
    text = await W.add_subtasks(PARENT, "One\nTwo\nThree")
    assert made == ["One", "Three"], "a failed step does not stop the next one"
    assert "Added 2 of 3 subtasks" in text
    assert "failed: step 2 «Two»" in text
    assert "stopped: 1 of 3 steps failed." in text
    assert "Never add them again" in text


# ── 8. list_tasks and project_summary ──────────────────────────────────────

def test_list_tasks_names_all_six_categories() -> None:
    doc = R.list_tasks.__doc__ or ""
    for category in ("backlog", "todo", "in_progress", "done", "cancelled", "triage"):
        assert category in doc, category


@pytest.mark.parametrize("kwargs,sent", [
    ({"status_category": "triage"}, True),
    ({"status_category": "todo,triage"}, True),
    ({"include_triage": True}, True),
    ({"status_category": "todo"}, False),
])
async def test_list_tasks_reads_triage_on_request(monkeypatch, kwargs, sent) -> None:
    """Catches ``reads.list_tasks`` never sending ``include_triage``."""
    calls = fake_gateway(monkeypatch, _gateway)
    await R.list_tasks(**kwargs)
    params = next(c["params"] for c in calls if c["path"] == "/projects/tasks")
    assert ("include_triage" in params) is sent


async def test_project_summary_prints_open_done_and_cancelled_apart(monkeypatch) -> None:
    """Catches ``reads.project_summary`` printing "tasks N" as the head count."""
    def answer(call: dict) -> Any:
        if call["path"] == f"/projects/nodes/{A}/summary":
            return {"name": "Hiring", "level": "project", "tasks": 13, "open": 7,
                    "overdue": 1, "projects": 1,
                    "by_category": {"todo": 7, "done": 4, "cancelled": 2},
                    "own": {"tasks": 3, "overdue": 0, "by_category": {"todo": 3}},
                    "children": [{"id": SUB, "name": "Interviews", "tasks": 10,
                                  "overdue": 1, "by_category": {"todo": 4, "done": 4,
                                                                "cancelled": 2}}]}
        return _gateway(call)

    fake_gateway(monkeypatch, answer)
    text = await R.project_summary(A)
    assert "open 7 · done 4 · cancelled 2 · overdue 1" in text
    assert "tasks 10 in every state (done 4, cancelled 2)" in text
    assert "  tasks 13 ·" not in text


# ── 10. get_project of the MCP edge ────────────────────────────────────────

def test_the_edge_marks_the_lane_that_a_create_uses() -> None:
    """Catches ``tools._project_status`` reading the stored ``is_default``."""
    pytest.importorskip("agent_edge", reason="agent_edge not installed")
    from agent_edge import tools

    rows = [
        {"id": "t", "name": "Triage", "category": "triage", "position": 0},
        {"id": "a", "name": "To do", "category": "todo", "position": 10, "is_default": False},
        {"id": "b", "name": "Backlog", "category": "backlog", "position": 20,
         "is_default": True},
    ]
    assert tools.default_lane_id(rows) == "a"
    marked = [tools._project_status(r, tools.default_lane_id(rows)) for r in rows]
    assert [m["is_default"] for m in marked] == [False, True, False]
    assert tools.default_lane_id([rows[0]]) is None


def test_the_edge_copy_of_the_triage_category_equals_the_gateway() -> None:
    pytest.importorskip("agent_edge", reason="agent_edge not installed")
    pytest.importorskip("gateway.routes.projects", reason="gateway not installed")
    from agent_edge import tools
    from gateway.routes.projects import core

    assert tools.TRIAGE_CATEGORY == core.TRIAGE_CATEGORY


# ── 11. move_projects, and a failed sibling write ──────────────────────────

async def test_move_projects_moves_the_ticked_nodes_with_one_card(monkeypatch) -> None:
    """Catches ``guarded.move_projects`` writing a row the member unticked."""
    calls = fake_gateway(monkeypatch, _gateway)
    cards = answer_rows(monkeypatch, {"row-1"})
    text = await G.move_projects(project_ids=f"{A},{B},{SUB}", parent_project_id=FOLDER)
    assert len(cards) == 1
    rows = cards[0]["rows"]
    assert [r["id"] for r in rows] == ["row-1", "row-2", "row-3"]
    moved = [c for c in writes(calls)]
    assert [c["path"] for c in moved] == [f"/projects/nodes/{A}/move"]
    assert moved[0]["json"] == {"parent_project_id": FOLDER}
    assert "Moved 1 of 3 projects" in text
    assert "left out: row 2" in text


async def test_move_projects_names_a_node_that_cannot_move(monkeypatch) -> None:
    calls = fake_gateway(monkeypatch, _gateway)
    cards = answer_rows(monkeypatch, {"row-1"})
    text = await G.move_projects(project_ids=f"{SUB},{A}", parent_project_id=A)
    # SUB is already under A, and A cannot move under itself.
    assert cards == []
    assert writes(calls) == []
    assert "No project of these can be moved" in text
    assert "is already under «Hiring»" in text
    assert "cannot be moved under itself" in text


async def test_a_failed_sibling_write_ends_in_a_receipt(monkeypatch) -> None:
    """Catches ``guarded._write_node_move`` raising with the siblings half
    numbered."""
    def answer(call: dict) -> Any:
        if call["method"] == "POST" and call["path"] == f"/projects/nodes/{B}/move":
            return FakeResponse({"detail": "The tree changed."}, status_code=409)
        return _gateway(call)

    calls = fake_gateway(monkeypatch, answer)
    spread = [(A, 100.0), (B, 200.0), (FOLDER, 300.0)]
    text = await G._write_node_move(SUB, NODES[SUB], SPACE, spread,
                                    {"parent_project_id": SPACE, "position": 150.0})
    assert isinstance(text, str)
    assert "did NOT move" in text
    assert "siblings numbered: 1 of 3" in text
    assert "stopped:" in text
    assert f"/projects/nodes/{SUB}/move" not in [c["path"] for c in calls]


# ── 12. The create_project card names the level ────────────────────────────

@pytest.mark.parametrize("parent,kind,level", [
    ("", "project", "space"),
    (SPACE, "project", "project"),
    (A, "project", "subproject"),
    (SUB, "project", "subproject"),
    (FOLDER, "project", "project"),
    (SPACE, "folder", "folder"),
])
async def test_the_create_project_card_names_the_level(monkeypatch, parent, kind, level) -> None:
    """Catches ``writes.create_project`` titling every card "project"."""
    fake_gateway(monkeypatch, _gateway)
    cards = deny(monkeypatch)
    await W.create_project(name="New", parent_project_id=parent, kind=kind)
    assert cards[0]["title"] == f"Create this {level}?"


# ── 13. A failed run never saves an empty reply ────────────────────────────

def _fold(*events: dict[str, Any]) -> Any:
    pytest.importorskip("gateway.chat_fold", reason="gateway not installed")
    from gateway.chat_fold import fold_run_events

    return fold_run_events([{"type": "RUN_STARTED", "runId": "r1"}, *events])


def test_a_timeout_with_no_answer_saves_the_notice() -> None:
    """Catches ``chat_fold.fold_run_events`` dropping the RUN_ERROR code
    (the incident of 2026-10-26, ``chat_fold.persisted chars=0``)."""
    folded = _fold({"type": "RUN_ERROR", "code": "timeout", "message": "stall"})
    assert folded["content"] == (
        "I could not finish this request: the AI service did not answer in time. "
        "Nothing was changed. Try again."
    )


def test_a_timeout_after_a_tool_says_that_steps_ran() -> None:
    folded = _fold(
        {"type": "TOOL_CALL_START", "toolCallId": "c1", "toolCallName": "call_agent"},
        {"type": "TOOL_CALL_RESULT", "toolCallId": "c1", "content": "ok"},
        {"type": "RUN_ERROR", "code": "timeout"},
    )
    assert folded["content"].startswith(
        "I could not finish this request: the AI service did not answer in time.")
    assert "Nothing was changed" not in folded["content"]
    assert "Check them before you try again" in folded["content"]


def test_an_error_after_an_answer_keeps_the_answer() -> None:
    folded = _fold(
        {"type": "TEXT_MESSAGE_CONTENT", "delta": "Half an answer"},
        {"type": "RUN_ERROR", "code": "timeout"},
    )
    assert folded["content"] == "Half an answer"


def test_a_run_that_finished_is_unchanged() -> None:
    assert _fold({"type": "RUN_FINISHED"}) is None
    folded = _fold({"type": "TEXT_MESSAGE_CONTENT", "delta": "Done."}, {"type": "RUN_FINISHED"})
    assert folded["content"] == "Done."
