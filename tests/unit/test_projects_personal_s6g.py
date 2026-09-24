"""WS-39 S6g — one promote path: Clarify's organize takes the promote answers.

Spec: ``project-docs/specs/my_tasks_cutover.md`` §5 S6g.

The Move dialog sent ``custom_fields`` and ``assignees`` with the move, and
Clarify's organize could not. So a personal capture filed into a company
project with a required field was refused AFTER the card had moved it. These
tests pin the three gateway halves of the fix:

1. ``OrganizeIn.custom_fields`` reaches the ``MoveTask`` that ``_organize``
   builds, and it satisfies the required-field guard;
2. without it the same decision is refused with the field's name, and nothing
   moves (the overlay stays the capture's);
3. an organize that moved the task emits ``pm.task.moved``, and one that did
   not move it does not.

The transaction claim (a refusal leaves no half-write) is a Postgres claim, and
``tests/live/live_ws39_s6g.py`` makes it against a real database (R8).

Hermetic: no Postgres, no network.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import HTTPException
from gateway.routes.projects import bulk as pm_bulk
from gateway.routes.projects import core as pm_core
from gateway.routes.projects import personal as pm_personal
from gateway.routes.projects import tasks as pm_tasks
from gateway.routes.projects import tree as pm_tree

from tests.unit._projects_fakes import (
    FakeProjectsDB,
    bind_db,
    member_user,
    silence_events,
)

MODULES = (pm_core, pm_tree, pm_tasks, pm_personal, pm_bulk)
ALICE = member_user("alice@fracktal.in")


@pytest.fixture
def events(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict]]:
    return silence_events(monkeypatch, MODULES)


@pytest.fixture
def db(monkeypatch: pytest.MonkeyPatch, events: list) -> FakeProjectsDB:
    fake = FakeProjectsDB()
    bind_db(monkeypatch, fake, MODULES)
    monkeypatch.setattr(
        "gateway.routes.tasks.task_memory.remember_decision_background",
        lambda **kwargs: None,
    )
    return fake


def _board_with_required_field(
    db: FakeProjectsDB, monkeypatch: pytest.MonkeyPatch,
) -> Any:
    """A company project whose required field a capture cannot carry."""
    project = db.seed_project(name="Printer v3", subject="org")
    lane = db.seed_status(project.id, name="To do", category="todo", is_default=True)
    db.seed(
        "pm_custom_fields", project_id=project.id, field_key="customer_po",
        name="PO", field_type="text", required=True,
    )

    # The fake cannot run `_REMAP_TARGET_SQL`; the landing lane is decided here,
    # the way `test_projects_landing.py` decides it.
    async def one(db: Any, *, status_id: str, owner_id: str) -> str:
        return str(lane.id)

    monkeypatch.setattr(pm_tasks, "remap_one_status", one)
    return project


async def _captured(db: FakeProjectsDB, title: str = "Fix the jam") -> dict:
    out = await pm_personal.capture(pm_personal.CaptureIn(title=title), user=ALICE)
    # Postgres fills `custom_fields` with its column default ('{}'). The fake
    # stores only the columns the INSERT names, so the default is set here.
    for row in db.rows("pm_tasks"):
        if str(row["id"]) == str(out["id"]):
            row.setdefault("custom_fields", {})
    return out


def _row(db: FakeProjectsDB, task_id: str) -> dict:
    row = dict(next(t for t in db.rows("pm_tasks") if str(t["id"]) == str(task_id)))
    row["custom_fields"] = pm_core.from_jsonb(row.get("custom_fields"))
    return row


def _overlay(db: FakeProjectsDB, task_id: str) -> dict | None:
    return next(
        (r for r in db.rows("pm_task_personal")
         if str(r["task_id"]) == str(task_id) and r["member_email"] == "alice@fracktal.in"),
        None,
    )


def test_organize_in_carries_the_promote_answers() -> None:
    """The model is the contract the client's `OrganizeBody` writes to."""
    body = pm_personal.OrganizeIn(
        kind="next", next_action="Fix it", project_id="p",
        custom_fields={"customer_po": "PO-1"},
    )
    assert body.custom_fields == {"customer_po": "PO-1"}
    assert pm_personal.OrganizeIn(kind="next", next_action="x").custom_fields is None


async def test_custom_fields_reach_the_move_and_satisfy_the_required_field(
    db: FakeProjectsDB, monkeypatch: pytest.MonkeyPatch, events: list,
) -> None:
    board = _board_with_required_field(db, monkeypatch)
    task = await _captured(db)
    events.clear()

    out = await pm_personal.organize_my_task(
        task["id"],
        pm_personal.OrganizeIn(
            kind="next", next_action="Fix the jam", project_id=str(board.id),
            custom_fields={"customer_po": "PO-77"},
        ),
        user=ALICE,
    )

    row = _row(db, task["id"])
    assert str(row["project_id"]) == str(board.id)
    assert row["custom_fields"] == {"customer_po": "PO-77"}
    assert _overlay(db, task["id"])["disposition"] == "NEXT"
    # Still mine: the capture was self-assigned and the decision kept it.
    owners = {a["assignee"] for a in db.rows("pm_task_assignees")
              if str(a["task_id"]) == task["id"]}
    assert owners == {"alice@fracktal.in"}
    assert out["project_id"] == str(board.id)
    kinds = [kind for kind, _ in events]
    assert "pm.task.moved" in kinds, "a promote from Clarify is a move"
    assert "pm.task.updated" in kinds


async def test_without_the_answer_the_decision_is_refused_and_nothing_moves(
    db: FakeProjectsDB, monkeypatch: pytest.MonkeyPatch, events: list,
) -> None:
    board = _board_with_required_field(db, monkeypatch)
    task = await _captured(db)
    root = _row(db, task["id"])["project_id"]
    events.clear()

    with pytest.raises(HTTPException) as caught:
        await pm_personal.organize_my_task(
            task["id"],
            pm_personal.OrganizeIn(
                kind="next", next_action="Fix the jam", project_id=str(board.id),
            ),
            user=ALICE,
        )
    assert caught.value.status_code == 422
    assert "PO" in str(caught.value.detail)
    assert str(_row(db, task["id"])["project_id"]) == str(root)
    assert _overlay(db, task["id"])["disposition"] == "INBOX"
    assert not [k for k, _ in events if k == "pm.task.moved"]


async def test_an_organize_that_does_not_move_emits_no_move(
    db: FakeProjectsDB, events: list,
) -> None:
    task = await _captured(db)
    events.clear()
    await pm_personal.organize_my_task(
        task["id"],
        pm_personal.OrganizeIn(kind="next", next_action="Do it", context="@home"),
        user=ALICE,
    )
    kinds = [kind for kind, _ in events]
    assert kinds == ["pm.task.updated"]


async def test_custom_fields_without_a_move_are_ignored(
    db: FakeProjectsDB,
) -> None:
    """The answers belong to a destination. With no move there is none, so
    they do not reach the task's own fields through this door."""
    task = await _captured(db)
    await pm_personal.organize_my_task(
        task["id"],
        pm_personal.OrganizeIn(
            kind="next", next_action="Do it", custom_fields={"customer_po": "x"},
        ),
        user=ALICE,
    )
    assert not _row(db, task["id"]).get("custom_fields")


def test_organize_takes_no_owner_list() -> None:
    """S6g repair (P2-d). No client sent `assignees`, and `_organize` passed
    it to the move even when the project did not change. The field is gone.
    A delegate still names its one person, through `assignee`."""
    assert "assignees" not in pm_personal.OrganizeIn.model_fields
    import inspect
    src = inspect.getsource(pm_personal._organize)
    assert "payload.assignees" not in src
    # The promote answers count only when the task changes project.
    assert "custom_fields=payload.custom_fields if dest else None" in src


# ── The purge: My Tasks never hard-deletes a team task (S6g P0) ──────────────

async def test_purge_deletes_a_task_in_my_tree(db: FakeProjectsDB, events: list) -> None:
    task = await _captured(db, "A stray thought")
    events.clear()
    out = await pm_personal.purge_my_task(task["id"], user=ALICE)
    assert out.deleted == task["id"]
    assert not [t for t in db.rows("pm_tasks") if str(t["id"]) == task["id"]]
    assert [k for k, _ in events] == ["pm.task.deleted"]


async def test_purge_refuses_a_board_task_and_deletes_nothing(
    db: FakeProjectsDB, monkeypatch: pytest.MonkeyPatch, events: list,
) -> None:
    board = _board_with_required_field(db, monkeypatch)
    lane = next(s for s in db.rows("pm_task_statuses") if str(s["project_id"]) == str(board.id))
    task = db.seed_task(board.id, lane["id"], title="The team's task")
    db.seed("pm_task_assignees", task_id=task.id, assignee="alice@fracktal.in",
            assigned_by="bob@fracktal.in")
    events.clear()
    with pytest.raises(HTTPException) as caught:
        await pm_personal.purge_my_task(str(task.id), user=ALICE)
    assert caught.value.status_code == 409
    assert "team board" in str(caught.value.detail)
    assert [t for t in db.rows("pm_tasks") if str(t["id"]) == str(task.id)]
    assert not events


async def test_the_projects_delete_route_shares_the_one_body() -> None:
    """One delete body, two doors: the board's route and My Tasks' purge."""
    import inspect
    assert "delete_task_in(db, doomed)" in inspect.getsource(pm_tasks.delete_task)
    assert "delete_task_in(db, task)" in inspect.getsource(pm_personal.purge_my_task)


async def test_purge_refuses_a_task_in_my_tree_that_somebody_else_is_on(
    db: FakeProjectsDB, events: list,
) -> None:
    """S6g repair (P2-a). A colleague on a task makes it theirs too."""
    task = await _captured(db, "Ours, somehow")
    db.seed("pm_task_assignees", task_id=task["id"], assignee="bob@fracktal.in",
            assigned_by="alice@fracktal.in")
    events.clear()
    with pytest.raises(HTTPException) as caught:
        await pm_personal.purge_my_task(task["id"], user=ALICE)
    assert caught.value.status_code == 409
    assert "Somebody else" in str(caught.value.detail)
    assert [t for t in db.rows("pm_tasks") if str(t["id"]) == task["id"]]
    assert not events


async def test_bulk_assignees_add_runs_the_assign_guard_per_task(
    db: FakeProjectsDB, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """S6g repair (P2-a). The bulk path skipped `assert_assignable_here`. A
    colleague added to a task in my tree now fails for THAT task, and a team
    task in the same selection still takes the colleague."""
    mine = await _captured(db, "Private")
    board = _board_with_required_field(db, monkeypatch)
    lane = next(s for s in db.rows("pm_task_statuses") if str(s["project_id"]) == str(board.id))
    team = db.seed_task(board.id, lane["id"], title="Team work")
    out = await pm_bulk.bulk_edit(
        pm_bulk.BulkIn(task_ids=[mine["id"], str(team.id)], assignees_add=["bob@fracktal.in"]),
        user=ALICE,
    )
    failed = {f["task_id"]: f["reason"] for f in out["failed"]}
    assert mine["id"] in failed and "personal project" in failed[mine["id"]]
    on_mine = {a["assignee"] for a in db.rows("pm_task_assignees") if str(a["task_id"]) == mine["id"]}
    on_team = {a["assignee"] for a in db.rows("pm_task_assignees") if str(a["task_id"]) == str(team.id)}
    assert "bob@fracktal.in" not in on_mine
    assert "bob@fracktal.in" in on_team
