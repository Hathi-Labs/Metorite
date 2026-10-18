"""``file_tasks`` — file several tasks into their projects under ONE card.

Owner, 2026-10-18: "I have been trying to move tasks from my inbox to the
appropriate project in the project app, but it constantly fails." The run
read the inbox and seven projects, and then asked an approval card. Nobody
answered for ten minutes, so the run parked with nothing written, and a late
answer restarted the run from the start. ``move_task`` moves from ONE source
to ONE destination per call, with a card per call, so an inbox into seven
projects was seven cards inside one long run.

``file_tasks`` plans every move first through ``writes._plan_move`` (the
planner of ``move_task``), shows ONE card with a checkbox for each task, and
applies each ticked move through ``_Move.send`` (the write of ``move_task``).

What this file holds (R7, each one a mutation that fails a test here):

1. Three destinations draw ONE card, and one approval applies every move.
2. An unticked row is not moved, and the receipt says so.
3. A task that lacks a required field is left out and named, on the card and
   in the receipt. With ``fields`` on its row, it moves through the narrow
   route, as ``move_task`` sends it.
4. A declined card writes nothing.
5. A task the member cannot see, an archived project and a folder are named
   on the card and left out (R5: the gateway's own 404 decides).
6. A move that fails after the card is a row of the receipt, never a raise,
   and the counts are right.
7. Subtasks with no answer are ONE question before any card (D-PM-38).
8. The tool is exported, annotated as a write, a composite of ``move_task``,
   in the agent's tool scope, and named by the agent's instructions.

No gateway and no database: the client is patched at httpx, as in
``test_projects_agent_writes.py``. The tool adds no SQL and no route, so R8
does not bind here: the move routes keep their own real-database suites.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

pytest.importorskip("skill_projects", reason="skill-projects not installed")

import skill_projects
import skill_projects.client as client
from skill_projects import manifest as m
from skill_projects import writes as W

from tests.unit._projects_agent_fakes import (
    AGENT_DIR,
    FakeResponse,
    answer_rows,
    approve,
    deny,
    empty_list,
    fake_gateway,
    writes,
)

INBOX = "10000000-0000-4000-8000-000000000000"
WEB = "20000000-0000-4000-8000-000000000000"
OPS = "30000000-0000-4000-8000-000000000000"
SALES = "40000000-0000-4000-8000-000000000000"
OLD = "50000000-0000-4000-8000-000000000000"
FOLDER = "60000000-0000-4000-8000-000000000000"
FIELD_ID = "70000000-0000-4000-8000-000000000000"

T1 = "a1000000-0000-4000-8000-000000000000"
T2 = "a2000000-0000-4000-8000-000000000000"
T3 = "a3000000-0000-4000-8000-000000000000"
T4 = "a4000000-0000-4000-8000-000000000000"
HIDDEN = "a9000000-0000-4000-8000-000000000000"

NODES: dict[str, dict[str, Any]] = {
    INBOX: {"id": INBOX, "name": "My tasks"},
    WEB: {"id": WEB, "name": "Website"},
    OPS: {"id": OPS, "name": "Ops"},
    SALES: {"id": SALES, "name": "Sales"},
    OLD: {"id": OLD, "name": "Old launch", "archived_at": "2026-09-01T00:00:00+00:00"},
    FOLDER: {"id": FOLDER, "name": "Clients", "kind": "folder"},
}

TASKS: dict[str, dict[str, Any]] = {
    tid: {
        "id": tid,
        "title": title,
        "task_number": number,
        "project_id": INBOX,
        "status_id": None,
        "assignees": [],
    }
    for tid, title, number in (
        (T1, "Fix the login", 1),
        (T2, "Order the nozzle", 2),
        (T3, "Call the vendor", 3),
        (T4, "Draft the brief", 4),
    )
}

#: The preview's answer per task, keyed by task id. The default is a clean
#: move into another space that drops nothing.
PREVIEWS: dict[str, dict[str, Any]] = {}


def _preview(task_id: str) -> dict[str, Any]:
    base = {
        "task_count": 1,
        "drops": [],
        "required_missing": [],
        "crosses_status_set": False,
        "crosses_root": True,
        "subtasks": {"count": 0},
    }
    return {**base, **PREVIEWS.get(task_id, {})}


def responder(call: dict) -> Any:
    path, method = call["path"], call["method"]
    if method == "GET" and path.startswith("/projects/tasks/"):
        tid = path.rsplit("/", 1)[-1]
        if tid in TASKS:
            return TASKS[tid]
        return FakeResponse({"detail": "Task not found"}, status_code=404)
    if method == "GET" and path.startswith("/projects/nodes/") and path.endswith("/fields"):
        return {
            "rows": [
                {
                    "id": FIELD_ID,
                    "name": "Cost centre",
                    "field_key": "cost_centre",
                    "field_type": "number",
                    "required": True,
                }
            ]
        }
    if method == "GET" and path.startswith("/projects/nodes/"):
        pid = path.split("/")[3]
        if pid in NODES:
            return NODES[pid]
        return FakeResponse({"detail": "Project not found"}, status_code=404)
    if path == "/projects/tasks/move/preview":
        return _preview(call["json"]["task_ids"][0])
    if path == "/projects/tasks/move":
        return {"moved": len(call["json"]["task_ids"])}
    if method == "POST" and path.endswith("/move"):
        return {**TASKS[path.split("/")[3]], "project_id": call["json"]["project_id"]}
    return empty_list(call)


@pytest.fixture(autouse=True)
def _clean_previews() -> Any:
    PREVIEWS.clear()
    yield
    PREVIEWS.clear()


def _moves(*rows: tuple[str, str] | dict[str, Any]) -> str:
    out = []
    for row in rows:
        if isinstance(row, dict):
            out.append(row)
        else:
            out.append({"task_id": row[0], "destination_project_id": row[1]})
    return json.dumps(out)


def _bulk_moves(calls: list[dict]) -> list[dict]:
    return [c["json"] for c in writes(calls) if c["path"] == "/projects/tasks/move"]


def _moved_ids(calls: list[dict]) -> list[str]:
    ids: list[str] = []
    for c in writes(calls):
        if c["path"] == "/projects/tasks/move":
            ids.extend(c["json"]["task_ids"])
        elif c["path"].endswith("/move"):
            ids.append(c["path"].split("/")[3])
    return ids


# ── 1. Three destinations, one card, every move ─────────────────────────────


async def test_three_destinations_are_one_card_and_one_approval_moves_them_all(
    monkeypatch,
) -> None:
    PREVIEWS[T2] = {"drops": ["cost"]}
    asked = approve(monkeypatch)
    calls = fake_gateway(monkeypatch, responder)
    out = await skill_projects.file_tasks(_moves((T1, WEB), (T2, OPS), (T3, SALES), (T4, WEB)))
    assert len(asked) == 1, f"{len(asked)} cards, not one"
    card = asked[0]
    assert card["title"] == "Move 4 tasks into 3 projects?"
    # One checkbox per task, grouped by destination in the member's order.
    assert [r["id"] for r in card["rows"]] == ["row-1", "row-4", "row-2", "row-3"]
    assert "to Website" in card["rows"][0]["hint"]
    assert "drops cost" in card["rows"][2]["hint"]
    assert "destination 1: «Website» · 2 tasks" in card["context"]
    assert "destination 2: «Ops» · 1 task · drops cost" in card["context"]
    # Every move is move_task's own write, one task each, after the card.
    bodies = _bulk_moves(calls)
    assert [(b["task_ids"], b["destination_project_id"]) for b in bodies] == [
        ([T1], WEB),
        ([T4], WEB),
        ([T2], OPS),
        ([T3], SALES),
    ]
    # The member saw exactly these drops (D-PM-29), so the apply sends them.
    assert bodies[2]["accept_drops"] is True and bodies[2]["accepted_drops"] == ["cost"]
    assert "accept_drops" not in bodies[0]
    assert out.splitlines()[0] == "Moved 4 tasks into 3 projects:"
    assert out.count("full_id:") == 4
    assert "#1 «Fix the login» · unassigned · in «Website»" in out
    assert "#2 «Order the nozzle» · unassigned · in «Ops»" in out
    assert "Do not move them again" in out
    assert "stopped:" not in out


async def test_everything_before_the_card_is_a_read(monkeypatch) -> None:
    import acb_skills.ask_tools as ask_tools

    calls = fake_gateway(monkeypatch, responder)

    async def marker(**kwargs: Any) -> Any:
        calls.append({"method": "CARD", "path": "", "json": kwargs, "params": {}, "headers": {}})
        return frozenset(r["id"] for r in kwargs["rows"])

    monkeypatch.setattr(ask_tools, "request_confirmation", marker)
    await skill_projects.file_tasks(_moves((T1, WEB), (T2, OPS), (T3, SALES)))
    at = next(i for i, c in enumerate(calls) if c["method"] == "CARD")
    assert all(m.is_read(c["method"], c["path"]) for c in calls[:at])
    assert sum(1 for c in calls if c["method"] == "CARD") == 1
    assert len(writes(calls[at + 1 :])) == 3


# ── 2. An unticked row is not moved ─────────────────────────────────────────


async def test_an_unticked_row_is_not_moved_and_the_receipt_names_it(monkeypatch) -> None:
    answer_rows(monkeypatch, ["row-1", "row-3"])
    calls = fake_gateway(monkeypatch, responder)
    out = await skill_projects.file_tasks(_moves((T1, WEB), (T2, OPS), (T3, SALES)))
    assert _moved_ids(calls) == [T1, T3]
    assert out.splitlines()[0] == "Moved 2 of 3 tasks. 1 was unticked."
    assert "left out: row 2 #2 «Order the nozzle», unticked on the card." in out


async def test_a_card_that_answers_a_row_it_did_not_offer_moves_nothing(monkeypatch) -> None:
    answer_rows(monkeypatch, ["row-1", "row-99"])
    calls = fake_gateway(monkeypatch, responder)
    out = await skill_projects.file_tasks(_moves((T1, WEB), (T2, OPS)))
    assert out == W.FORGED_BATCH_ROWS
    assert writes(calls) == []


# ── 3. A required field ─────────────────────────────────────────────────────


async def test_a_task_that_needs_a_field_is_left_out_and_named(monkeypatch) -> None:
    PREVIEWS[T2] = {"required_missing": ["Cost centre"]}
    asked = approve(monkeypatch)
    calls = fake_gateway(monkeypatch, responder)
    out = await skill_projects.file_tasks(_moves((T1, WEB), (T2, OPS), (T3, SALES)))
    card = asked[0]
    assert card["title"] == "Move 2 tasks into 2 projects?"
    assert [r["id"] for r in card["rows"]] == ["row-1", "row-3"]
    assert (
        "cannot move 1: #2 «Order the nozzle»: The destination requires «Cost centre» (number)"
        in card["context"]
    )
    assert _moved_ids(calls) == [T1, T3]
    assert out.splitlines()[0] == "Moved 2 of 3 tasks. 1 could not move."
    assert "not moved: row 2 #2 «Order the nozzle»: The destination requires «Cost centre»" in out


async def test_a_row_with_its_fields_moves_through_the_narrow_route(monkeypatch) -> None:
    PREVIEWS[T2] = {"required_missing": ["Cost centre"]}
    approve(monkeypatch)
    calls = fake_gateway(monkeypatch, responder)
    out = await skill_projects.file_tasks(
        _moves(
            (T1, WEB),
            {"task_id": T2, "destination_project_id": OPS, "fields": {"Cost centre": 4200}},
        )
    )
    narrow = [c for c in writes(calls) if c["path"] == f"/projects/tasks/{T2}/move"]
    assert narrow and narrow[0]["json"] == {
        "project_id": OPS,
        "custom_fields": {"cost_centre": 4200},
    }
    assert _moved_ids(calls) == [T1, T2]
    assert out.splitlines()[0] == "Moved 2 tasks into 2 projects:"


# ── 4. A declined card ──────────────────────────────────────────────────────


async def test_a_declined_card_writes_nothing(monkeypatch) -> None:
    deny(monkeypatch)
    calls = fake_gateway(monkeypatch, responder)
    out = await skill_projects.file_tasks(_moves((T1, WEB), (T2, OPS), (T3, SALES)))
    assert out == W.CANCELLED
    assert writes(calls) == []


# ── 5. What the member cannot see, and where a task cannot land ─────────────


async def test_an_invisible_task_and_a_hidden_project_are_named_not_moved(monkeypatch) -> None:
    gone = "b0000000-0000-4000-8000-000000000000"
    asked = approve(monkeypatch)
    calls = fake_gateway(monkeypatch, responder)
    out = await skill_projects.file_tasks(_moves((T1, WEB), (HIDDEN, OPS), (T3, gone)))
    context = asked[0]["context"]
    assert f"task «{HIDDEN}»: not found, or not visible to you" in context
    assert f"#3 «Call the vendor»: the destination {gone} is not found" in context
    assert [r["id"] for r in asked[0]["rows"]] == ["row-1"]
    assert _moved_ids(calls) == [T1]
    assert f"not moved: row 2 task «{HIDDEN}»: not found, or not visible to you" in out


async def test_an_archived_project_and_a_folder_are_named_not_moved(monkeypatch) -> None:
    asked = approve(monkeypatch)
    calls = fake_gateway(monkeypatch, responder)
    out = await skill_projects.file_tasks(_moves((T1, OLD), (T2, FOLDER), (T3, WEB)))
    context = asked[0]["context"]
    assert "#1 «Fix the login»: «Old launch» is archived" in context
    assert "#2 «Order the nozzle»: «Clients» is a folder" in context
    assert _moved_ids(calls) == [T3]
    # Neither left-out row was planned: no preview names them.
    previews = [c["json"]["task_ids"] for c in calls if c["path"] == "/projects/tasks/move/preview"]
    assert previews == [[T3]]
    assert "2 could not move." in out.splitlines()[0]


async def test_a_task_already_there_is_named(monkeypatch) -> None:
    asked = approve(monkeypatch)
    fake_gateway(monkeypatch, responder)
    out = await skill_projects.file_tasks(_moves((T1, INBOX), (T2, WEB)))
    assert "#1 «Fix the login»: already in «My tasks»" in asked[0]["context"]
    assert "not moved: row 1 #1 «Fix the login»: already in «My tasks»" in out


async def test_when_no_task_can_move_there_is_no_card(monkeypatch) -> None:
    asked = approve(monkeypatch)
    calls = fake_gateway(monkeypatch, responder)
    out = await skill_projects.file_tasks(_moves((T1, OLD), (HIDDEN, WEB)))
    assert asked == []
    assert writes(calls) == []
    assert out.startswith("No task of these can move. Nothing was changed.")
    assert f"task «{HIDDEN}»" in out


# ── 6. A move that fails after the card ─────────────────────────────────────


async def test_a_refused_move_is_a_row_of_the_receipt_and_the_counts_hold(monkeypatch) -> None:
    def refusing(call: dict) -> Any:
        if call["path"] == "/projects/tasks/move" and call["json"]["task_ids"] == [T2]:
            return FakeResponse({"detail": "The destination changed since you looked."}, 409)
        return responder(call)

    approve(monkeypatch)
    calls = fake_gateway(monkeypatch, refusing)
    out = await skill_projects.file_tasks(_moves((T1, WEB), (T2, OPS), (T3, SALES)))
    assert len(_bulk_moves(calls)) == 3, "a failed row stopped the rows after it"
    lines = out.splitlines()
    assert lines[0] == "Moved 2 of 3 tasks. 1 was refused."
    assert any(
        line.startswith("failed: row 2 #2 «Order the nozzle» to «Ops» refused (409)")
        for line in lines
    )
    assert "stopped: 1 of 3 moves failed." in lines
    assert out.count("full_id:") == 2


async def test_a_lost_connection_is_unknown_and_never_raises(monkeypatch) -> None:
    def dropping(call: dict) -> Any:
        if call["path"] == "/projects/tasks/move" and call["json"]["task_ids"] == [T1]:
            raise httpx.ConnectError("reset")
        return responder(call)

    approve(monkeypatch)
    fake_gateway(monkeypatch, dropping)
    out = await skill_projects.file_tasks(_moves((T1, WEB), (T2, OPS)))
    assert (
        out.splitlines()[0] == "Moved 1 of 2 tasks. 1 may have moved: read the task before a retry."
    )
    assert "unknown: row 1 #1 «Fix the login» to «Website»" in out
    assert "stopped: 1 of 2 moves failed." in out


# ── 7. Subtasks: one question for the selection, before any card ────────────


async def test_subtasks_with_no_answer_are_one_question_before_any_card(monkeypatch) -> None:
    PREVIEWS[T1] = {"subtasks": {"count": 2}}
    PREVIEWS[T3] = {"subtasks": {"count": 1}}
    asked = approve(monkeypatch)
    calls = fake_gateway(monkeypatch, responder)
    out = await skill_projects.file_tasks(_moves((T1, WEB), (T2, OPS), (T3, SALES)))
    assert asked == []
    assert writes(calls) == []
    assert "#1 «Fix the login» has 2 subtasks (every level)" in out
    assert "#3 «Call the vendor» has 1 subtask (every level)" in out
    assert out.count("Ask the member once") == 1
    assert "include_subtasks=yes or include_subtasks=no" in out


async def test_an_answer_about_subtasks_takes_them_along(monkeypatch) -> None:
    PREVIEWS[T1] = {"subtasks": {"count": 2}}
    asked = approve(monkeypatch)
    calls = fake_gateway(monkeypatch, responder)
    await skill_projects.file_tasks(_moves((T1, WEB), (T2, OPS)), include_subtasks="yes")
    assert len(asked) == 1
    assert "2 subtasks (every level) moved too" in asked[0]["rows"][0]["hint"]
    assert all(b.get("include_subtasks") is True for b in _bulk_moves(calls))


# ── Input refused before any call ───────────────────────────────────────────


@pytest.mark.parametrize(
    ("moves", "said"),
    [
        ("not json", "moves is a JSON list"),
        ("[]", "moves is a JSON list"),
        (
            json.dumps([{"task_id": T1, "destination_project_id": WEB, "status": "Done"}]),
            "Row 1 has a key that file_tasks does not take: status",
        ),
        (json.dumps([{"task_id": "abc", "destination_project_id": WEB}]), "Row 1: task_id"),
        (_moves((T1, WEB), (T1, OPS)), "Rows 1 and 2 name the same task"),
        (json.dumps([{"task_id": T1, "destination_project_id": WEB}] * 51), "The limit"),
    ],
)
async def test_bad_rows_are_refused_before_any_call(moves: str, said: str, monkeypatch) -> None:
    approve(monkeypatch)
    calls = fake_gateway(monkeypatch, responder)
    out = await skill_projects.file_tasks(moves)
    assert said in out
    assert calls == []


async def test_a_run_with_nobody_to_act_as_makes_no_call(monkeypatch) -> None:
    approve(monkeypatch)
    calls = fake_gateway(monkeypatch, responder, user=None)
    with pytest.raises(client.GatewayRefusal):
        await skill_projects.file_tasks(_moves((T1, WEB), (T2, OPS)))
    assert calls == []


# ── 8. The agent holds it, as a write ───────────────────────────────────────


def test_the_tool_is_exported_annotated_as_a_write_and_a_move_task_composite() -> None:
    from acb_skills.tool_annotations import TOOL_ANNOTATIONS

    assert "file_tasks" in skill_projects.__all__
    hints = TOOL_ANNOTATIONS["file_tasks"]
    assert hints["read_only"] is False
    assert hints["open_world"] is False
    assert m.COMPOSITE["file_tasks"] == frozenset({"move_task"})
    assert m.tool_class("file_tasks") == m.tool_class("move_task") == "B"


def test_the_projects_assistant_holds_the_tool_and_its_instructions_name_it() -> None:
    config = json.loads((AGENT_DIR / "config.json").read_text(encoding="utf-8"))
    assert "file_tasks" in config["own_tool_scope"]
    assert "move_task" in config["own_tool_scope"]
    text = (AGENT_DIR / "instructions.md").read_text(encoding="utf-8")
    start = text.index("## Filing tasks into projects")
    section = text[start : text.index("\n## ", start + 1)]
    assert "`file_tasks`" in section
    assert "Do not call\n  `move_task` once for each task" in section
