"""``update_tasks`` — many edits, a different change for each task, ONE card.

WS-PJP PJP-4 (D168.1): three or more related changes with different values
come on one card with a tick box for each change. ``bulk_update`` keeps the
other case: ONE value across many tasks.

``update_tasks`` plans every row through ``_plan_task_edit``, the planner that
``update_task`` also calls, so a row is refused for the same reason and in the
same words. It shows ONE card with a row for each change, and patches each
ticked row on the route of ``update_task``. It shares the card, the apply and
the receipt with ``file_tasks`` and ``link_tasks_batch``.

What this file holds (R7, each one a mutation that fails a test here):

1. A row that ``update_task`` refuses is refused in the same words, after the
   batch strips "Nothing was changed.". It stays off the card.
2. Twelve rows draw ONE card with twelve rows, shown as before → after.
3. Ticking nine of twelve writes nine, and the receipt names the other three.
4. A card that answers a row it did not offer writes nothing.
5. A row that needs the include_subtasks answer is refused. No prompt comes.
6. A row sends the body that ``update_task`` sends for the same arguments.
7. More than 50 rows, and two rows for one task, are refused before any read.
8. The tool is exported, a write, a composite of ``update_task``, in the
   agent's tool scope and in the ``projects-bulk`` pack.

No gateway and no database: the client is patched at httpx. The tool adds no
SQL and no route, so R8 does not bind here.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest

pytest.importorskip("skill_projects", reason="skill-projects not installed")

import skill_projects
from skill_projects import manifest as m
from skill_projects import writes as W

from tests.unit._projects_agent_fakes import (
    AGENT_DIR,
    FakeResponse,
    answer_rows,
    approve,
    fake_gateway,
    writes,
)

TODO, DONE = "a1000000-0000-4000-8000-000000000001", "a1000000-0000-4000-8000-000000000002"
PROJECT = "b1000000-0000-4000-8000-000000000001"
CHILD = "c9000000-0000-4000-8000-000000000001"


def _tid(n: int) -> str:
    return str(uuid.UUID(int=0xD1000000000040008000000000000000 + n))


def _task(n: int) -> dict[str, Any]:
    return {
        "id": _tid(n),
        "title": f"Task {n}",
        "task_number": 100 + n,
        "status_id": TODO,
        "project_id": PROJECT,
        "assignees": [],
        "due_at": "2026-01-01",
    }


TASKS = {_tid(n): _task(n) for n in range(1, 16)}
#: Tasks that have one open subtask.
PARENTS: set[str] = set()


def responder(call: dict) -> Any:
    path, method = call["path"], call["method"]
    parts = path.split("/")
    if method == "GET" and path == f"/projects/nodes/{PROJECT}/statuses":
        return {
            "rows": [
                {"id": TODO, "name": "To do", "category": "todo"},
                {"id": DONE, "name": "Done", "category": "done"},
            ],
            "counts": {},
        }
    if method == "GET" and path.endswith("/relations"):
        subs = [{"id": CHILD, "title": "step", "category": "todo"}] if parts[3] in PARENTS else []
        return {"subtasks": subs, "links": [], "blocked_by": []}
    if method == "GET" and path.endswith("/statuses"):
        return {
            "rows": [
                {"id": TODO, "name": "To do", "category": "todo"},
                {"id": DONE, "name": "Done", "category": "done"},
            ],
            "counts": {},
        }
    if method == "GET" and path.startswith("/projects/tasks/") and len(parts) == 4:
        if parts[3] in TASKS:
            return TASKS[parts[3]]
        return FakeResponse({"detail": "Task not found"}, status_code=404)
    if method == "PATCH" and path.startswith("/projects/tasks/"):
        return {**TASKS[parts[3]], **call["json"]}
    return {"rows": []}


@pytest.fixture(autouse=True)
def _clean() -> Any:
    PARENTS.clear()
    yield
    PARENTS.clear()


def _changes(*rows: dict[str, Any]) -> str:
    return json.dumps(list(rows))


def _twelve() -> str:
    return _changes(*({"task_id": _tid(n), "due": f"2026-02-{n:02d}"} for n in range(1, 13)))


def _patched(calls: list[dict]) -> list[dict]:
    return [c for c in writes(calls) if c["method"] == "PATCH"]


# ── 1. A bad row reads as update_task reads it ──────────────────────────────


async def test_a_refused_row_has_the_text_of_update_task(monkeypatch) -> None:
    asked = approve(monkeypatch)
    fake_gateway(monkeypatch, responder)
    single = await skill_projects.update_task(_tid(1), priority="no-such-level")
    out = await skill_projects.update_tasks(
        _changes(
            {"task_id": _tid(1), "priority": "no-such-level"},
            {"task_id": _tid(2), "due": "2026-03-01"},
        )
    )
    # The row's reason is update_task's text, without the batch-wide sentence.
    assert f"not changed: row 1 #101 «Task 1»: {W._row_reason(single)}" in out
    assert "Nothing was changed.:" not in out
    # The row stays off the card, and the card note names it.
    assert [r["id"] for r in asked[-1]["rows"]] == ["row-2"]
    assert W._row_reason(single) in asked[-1]["context"]


async def test_a_row_with_no_field_is_refused_like_update_task(monkeypatch) -> None:
    approve(monkeypatch)
    fake_gateway(monkeypatch, responder)
    single = await skill_projects.update_task(_tid(1))
    out = await skill_projects.update_tasks(_changes({"task_id": _tid(1)}))
    assert single == "Nothing to change. Pass at least one field."
    assert "No change of these can be made. Nothing was changed." in out
    assert f"row 1 #101 «Task 1»: {single}" in out


async def test_a_task_the_member_cannot_see_is_named_and_left_out(monkeypatch) -> None:
    asked = approve(monkeypatch)
    fake_gateway(monkeypatch, responder)
    hidden = _tid(99)
    out = await skill_projects.update_tasks(
        _changes({"task_id": hidden, "due": "2026-03-01"}, {"task_id": _tid(2), "due": "2026-03-01"})
    )
    assert "not found, or not visible to you" in out
    assert [r["id"] for r in asked[-1]["rows"]] == ["row-2"]


# ── 2. Twelve rows, one card ────────────────────────────────────────────────


async def test_twelve_rows_draw_one_card_with_twelve_rows(monkeypatch) -> None:
    asked = approve(monkeypatch)
    calls = fake_gateway(monkeypatch, responder)
    out = await skill_projects.update_tasks(_twelve())
    assert len(asked) == 1
    card = asked[0]
    assert card["title"] == "Update these tasks?"
    assert [r["id"] for r in card["rows"]] == [f"row-{n}" for n in range(1, 13)]
    assert card["rows"][0]["label"] == "#101 Task 1"
    assert card["rows"][0]["hint"].startswith("due_at: 2026-01-01 → 2026-02-01")
    assert len(_patched(calls)) == 12
    assert out.splitlines()[0] == "Changed 12 tasks:"
    assert "Do not change them again" in out


async def test_everything_before_the_card_is_a_read(monkeypatch) -> None:
    import acb_skills.ask_tools as ask_tools

    calls = fake_gateway(monkeypatch, responder)

    async def marker(**kwargs: Any) -> Any:
        calls.append({"method": "CARD", "path": "", "json": kwargs, "params": {}, "headers": {}})
        return frozenset(r["id"] for r in kwargs["rows"])

    monkeypatch.setattr(ask_tools, "request_confirmation", marker)
    await skill_projects.update_tasks(_twelve())
    at = next(i for i, c in enumerate(calls) if c["method"] == "CARD")
    assert all(m.is_read(c["method"], c["path"]) for c in calls[:at])


# ── 3. Ticking nine writes nine ─────────────────────────────────────────────


async def test_ticking_nine_of_twelve_writes_nine(monkeypatch) -> None:
    answer_rows(monkeypatch, [f"row-{n}" for n in range(1, 10)])
    calls = fake_gateway(monkeypatch, responder)
    out = await skill_projects.update_tasks(_twelve())
    patched = _patched(calls)
    assert [c["path"].split("/")[3] for c in patched] == [_tid(n) for n in range(1, 10)]
    assert out.splitlines()[0].startswith("Changed 9 of 12 tasks.")
    assert "3 were unticked." in out
    assert out.count("left out: row") == 3


async def test_a_declined_card_writes_nothing(monkeypatch) -> None:
    answer_rows(monkeypatch, [])
    calls = fake_gateway(monkeypatch, responder)
    out = await skill_projects.update_tasks(_twelve())
    assert out == W.CANCELLED
    assert _patched(calls) == []


async def test_a_failed_row_does_not_stop_the_next(monkeypatch) -> None:
    approve(monkeypatch)

    def failing(call: dict) -> Any:
        if call["method"] == "PATCH" and call["path"].endswith(_tid(2)):
            return FakeResponse({"detail": "Version conflict"}, status_code=409)
        return responder(call)

    calls = fake_gateway(monkeypatch, failing)
    out = await skill_projects.update_tasks(
        _changes(*({"task_id": _tid(n), "due": "2026-03-01"} for n in (1, 2, 3)))
    )
    assert len(_patched(calls)) == 3
    assert "failed: row 2 #102 «Task 2» refused (409)" in out
    assert out.splitlines()[0].startswith("Changed 2 of 3 tasks.")


# ── 4. A forged id ──────────────────────────────────────────────────────────


async def test_a_card_that_answers_a_forged_row_writes_nothing(monkeypatch) -> None:
    import acb_skills.ask_tools as ask_tools

    async def forged(**kwargs: Any) -> Any:
        return frozenset({"row-1", "row-99"})

    monkeypatch.setattr(ask_tools, "request_confirmation", forged)
    calls = fake_gateway(monkeypatch, responder)
    out = await skill_projects.update_tasks(_twelve())
    assert out == W.FORGED_BATCH_ROWS
    assert _patched(calls) == []


# ── 5. A row that needs a question ──────────────────────────────────────────


async def test_a_row_that_would_ask_about_subtasks_is_refused_with_no_prompt(monkeypatch) -> None:
    asked = approve(monkeypatch)
    calls = fake_gateway(monkeypatch, responder)
    PARENTS.add(_tid(1))
    out = await skill_projects.update_tasks(
        _changes(
            {"task_id": _tid(1), "status": "Done"},
            {"task_id": _tid(2), "status": "Done"},
        )
    )
    assert "not changed: row 1 #101 «Task 1»: the task has open subtasks" in out
    assert "finish them too" in out
    # One card, for the row without subtasks. No second card for the question.
    assert len(asked) == 1
    assert [r["id"] for r in asked[0]["rows"]] == ["row-2"]
    assert [c["path"].split("/")[3] for c in _patched(calls)] == [_tid(2)]


async def test_a_lone_question_row_draws_no_card(monkeypatch) -> None:
    asked = approve(monkeypatch)
    calls = fake_gateway(monkeypatch, responder)
    PARENTS.add(_tid(1))
    out = await skill_projects.update_tasks(_changes({"task_id": _tid(1), "status": "Done"}))
    assert asked == []
    assert _patched(calls) == []
    assert "No change of these can be made." in out


async def test_a_row_with_the_answer_goes_on_the_card(monkeypatch) -> None:
    asked = approve(monkeypatch)
    calls = fake_gateway(monkeypatch, responder)
    PARENTS.add(_tid(1))
    await skill_projects.update_tasks(
        _changes({"task_id": _tid(1), "status": "Done", "include_subtasks": "yes"})
    )
    assert len(asked) == 1
    assert _patched(calls)[0]["params"] == {"include_subtasks": True}


# ── 6. A row sends what update_task sends ───────────────────────────────────


async def test_a_row_patches_the_body_of_update_task(monkeypatch) -> None:
    approve(monkeypatch)
    single_calls = fake_gateway(monkeypatch, responder)
    await skill_projects.update_task(
        _tid(1), title="New title", due="2026-05-05", status="Done", tags="a,b", estimate_mins=30
    )
    batch_calls = fake_gateway(monkeypatch, responder)
    await skill_projects.update_tasks(
        _changes(
            {
                "task_id": _tid(1),
                "title": "New title",
                "due": "2026-05-05",
                "status": "Done",
                "tags": ["a", "b"],
                "estimate_mins": 30,
            }
        )
    )
    one, many = _patched(single_calls)[0], _patched(batch_calls)[0]
    assert (many["path"], many["json"], many["params"]) == (
        one["path"],
        one["json"],
        one["params"],
    )


# ── 7. The limits ───────────────────────────────────────────────────────────


async def test_more_than_fifty_rows_are_refused_before_any_read(monkeypatch) -> None:
    calls = fake_gateway(monkeypatch, responder)
    rows = [{"task_id": _tid(1), "due": "2026-03-01"}] * 51
    out = await skill_projects.update_tasks(json.dumps(rows))
    assert out == f"That is 51 rows. The limit for one card is {W.MAX_BATCH}."
    assert calls == []


async def test_two_rows_for_one_task_are_refused(monkeypatch) -> None:
    calls = fake_gateway(monkeypatch, responder)
    out = await skill_projects.update_tasks(
        _changes({"task_id": _tid(1), "due": "2026-03-01"}, {"task_id": _tid(1), "title": "x"})
    )
    assert out.startswith("Rows 1 and 2 name the same task.")
    assert calls == []


async def test_a_key_the_tool_does_not_take_is_refused(monkeypatch) -> None:
    fake_gateway(monkeypatch, responder)
    out = await skill_projects.update_tasks(_changes({"task_id": _tid(1), "colour": "red"}))
    assert out.startswith("Row 1 has a key that update_tasks does not take: colour")


# ── 8. Registration ─────────────────────────────────────────────────────────


def test_the_tool_is_registered_as_a_write_in_its_pack_and_scope() -> None:
    from acb_skills import skill_families as sf
    from acb_skills.tool_annotations import TOOL_ANNOTATIONS

    assert "update_tasks" in skill_projects.__all__
    assert TOOL_ANNOTATIONS["update_tasks"]["read_only"] is False
    assert TOOL_ANNOTATIONS["update_tasks"]["open_world"] is False
    assert m.COMPOSITE["update_tasks"] == frozenset({"update_task"})
    assert m.tool_class("update_tasks") == "B"
    assert "update_tasks" in sf.SKILL_FAMILIES["projects-bulk"]["tools"]
    config = json.loads((AGENT_DIR / "config.json").read_text(encoding="utf-8"))
    assert "update_tasks" in config["own_tool_scope"]


def test_the_rules_state_the_d168_split() -> None:
    from acb_skills import tool_packs as tp

    text = (AGENT_DIR / "instructions.md").read_text(encoding="utf-8")
    for body in (text, tp._PROJECTS_BULK_RULES):
        assert "update_tasks" in body and "update_task" in body and "bulk_update" in body
