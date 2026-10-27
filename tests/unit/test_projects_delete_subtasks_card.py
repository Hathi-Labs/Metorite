"""OP-7 / D-OP-10 — the chat card for ``delete_task`` lets the member pick the subtasks.

The card is the ordinary confirmation card with rows (``_confirm(rows=...)``).
The task is the first row, so a yes always ticks it, and a card with the task
row cleared is no yes. Each subtask is one row, ticked when the model chose it.
The ids sent to ``DELETE /projects/tasks/{id}`` are the card's ticks, never the
model's argument. ``test_projects_agent_writes.py`` holds the generic fences
(a denied card writes nothing, the card comes before the write).
"""

from __future__ import annotations

from typing import Any

import skill_projects
from skill_projects import writes as W

from tests.unit import test_projects_agent_writes as tw
from tests.unit._projects_agent_fakes import (
    answer_rows,
    approve,
    fake_gateway,
    writes,
)

LIVE, OTHER = tw.LIVE, tw.OTHER
SUB_A, SUB_A1, SUB_B = tw.SUB_A, tw.SUB_A1, tw.SUB_B


def _delete_gateway(call: dict) -> Any:
    """The fake gateway, with a delete that reports what the core would."""
    if call["method"] == "DELETE" and call["path"].startswith("/projects/tasks/"):
        sent = call["params"].get("delete_subtask_ids") or []
        return {
            "deleted": LIVE,
            "cascaded": {
                "subtasks_deleted": len(sent),
                "subtasks_promoted": 3 - len(sent),
                "subtasks_kept_hidden": 1,
            },
        }
    return tw.responder(call)


def _task_delete(calls: list[dict]) -> dict:
    sent = [c for c in writes(calls) if c["method"] == "DELETE"]
    assert len(sent) == 1, sent
    return sent[0]


async def test_the_card_lists_the_subtask_tree_with_none_ticked(monkeypatch) -> None:
    asked = approve(monkeypatch)
    calls = fake_gateway(monkeypatch, _delete_gateway)
    out = await skill_projects.delete_task(LIVE)
    rows = asked[0]["rows"]
    # Depth first: the grandchild sits under its own parent.
    assert [r["id"] for r in rows] == [LIVE, SUB_A, SUB_A1, SUB_B]
    assert [r["checked"] for r in rows] == [True, False, False, False]
    assert rows[2]["label"].startswith("— ")  # a grandchild is indented
    assert "archived" in rows[3]["hint"]
    assert "0 of 3 subtasks marked to delete" in asked[0]["detail"]
    assert "1 subtask hidden from you, kept and moved up" in asked[0]["detail"]
    # The default is the old rule: no ids on the wire.
    assert not _task_delete(calls)["params"]
    assert "3 subtasks moved up one level" in out
    assert "Kept, because hidden from you: 1 subtask" in out


async def test_all_pre_ticks_every_visible_subtask(monkeypatch) -> None:
    asked = approve(monkeypatch)
    calls = fake_gateway(monkeypatch, _delete_gateway)
    out = await skill_projects.delete_task(LIVE, subtasks_to_delete="all")
    assert all(r["checked"] for r in asked[0]["rows"])
    assert sorted(_task_delete(calls)["params"]["delete_subtask_ids"]) == sorted(
        [SUB_A, SUB_B, SUB_A1]
    )
    assert "3 subtasks deleted too" in out


async def test_a_list_pre_ticks_only_the_named_ids(monkeypatch) -> None:
    asked = approve(monkeypatch)
    calls = fake_gateway(monkeypatch, _delete_gateway)
    await skill_projects.delete_task(LIVE, subtasks_to_delete=f"{SUB_A1}, {SUB_B}")
    ticked = {r["id"] for r in asked[0]["rows"] if r["checked"]}
    assert ticked == {LIVE, SUB_A1, SUB_B}
    assert sorted(_task_delete(calls)["params"]["delete_subtask_ids"]) == sorted([SUB_A1, SUB_B])


async def test_the_member_changes_the_ticks_on_the_card(monkeypatch) -> None:
    """The model asked for none. The member ticked one. The ids sent are the card's."""
    answer_rows(monkeypatch, frozenset({LIVE, SUB_B}))
    calls = fake_gateway(monkeypatch, _delete_gateway)
    await skill_projects.delete_task(LIVE, subtasks_to_delete="none")
    assert _task_delete(calls)["params"]["delete_subtask_ids"] == [SUB_B]


async def test_clearing_the_task_row_cancels_and_writes_nothing(monkeypatch) -> None:
    answer_rows(monkeypatch, frozenset({SUB_A}))
    calls = fake_gateway(monkeypatch, _delete_gateway)
    out = await skill_projects.delete_task(LIVE, subtasks_to_delete="all")
    assert out == W.CANCELLED
    assert writes(calls) == []


async def test_an_id_that_is_not_a_visible_subtask_is_refused_before_the_card(monkeypatch) -> None:
    asked = approve(monkeypatch)
    calls = fake_gateway(monkeypatch, _delete_gateway)
    out = await skill_projects.delete_task(LIVE, subtasks_to_delete=OTHER)
    assert "not a subtask of this task that you can see" in out
    assert asked == [] and writes(calls) == []


async def test_a_task_with_no_subtasks_keeps_the_plain_card(monkeypatch) -> None:
    asked = approve(monkeypatch)

    def bare(call: dict) -> Any:
        if call["path"].endswith("/subtree"):
            return {"task_id": LIVE, "subtasks": [], "hidden": 0}
        return _delete_gateway(call)

    calls = fake_gateway(monkeypatch, bare)
    await skill_projects.delete_task(LIVE)
    assert asked[0].get("rows") is None
    assert "no subtasks" in asked[0]["detail"]
    assert not _task_delete(calls)["params"]


async def test_a_wide_tree_draws_no_boxes_and_refuses_a_list(monkeypatch) -> None:
    asked = approve(monkeypatch)
    many = [
        tw._sub(f"{i:08x}-d9cb-469f-a165-70867728950e", LIVE, f"t{i}") for i in range(100)
    ]

    def wide(call: dict) -> Any:
        if call["path"].endswith("/subtree"):
            return {"task_id": LIVE, "subtasks": many, "hidden": 0}
        return _delete_gateway(call)

    calls = fake_gateway(monkeypatch, wide)
    out = await skill_projects.delete_task(LIVE, subtasks_to_delete=many[0]["id"])
    assert "too many for tick boxes" in out and asked == [] and writes(calls) == []
    await skill_projects.delete_task(LIVE, subtasks_to_delete="all")
    assert asked[0].get("rows") is None
    assert len(_task_delete(calls)["params"]["delete_subtask_ids"]) == 100


def _uid(n: int) -> str:
    return f"{n:08x}-d9cb-469f-a165-70867728950e"


async def test_two_parents_each_with_a_testing_child_draw_depth_first(monkeypatch) -> None:
    """The route sends rows by depth. The card draws each parent, its child,
    its grandchild, and only then the next parent."""
    asked = approve(monkeypatch)
    p1, p2, c1, c2, g1, g2 = (_uid(n) for n in (1, 2, 3, 4, 5, 6))
    tree = [
        tw._sub(p1, LIVE, "Parent one"),
        tw._sub(p2, LIVE, "Parent two"),
        tw._sub(c1, p1, "Testing"),
        tw._sub(c2, p2, "Testing"),
        tw._sub(g1, c1, "Fix one"),
        tw._sub(g2, c2, "Fix two"),
    ]

    def two(call: dict) -> Any:
        if call["path"].endswith("/subtree"):
            return {"task_id": LIVE, "subtasks": tree, "hidden": 0}
        return _delete_gateway(call)

    fake_gateway(monkeypatch, two)
    await skill_projects.delete_task(LIVE)
    assert [r["id"] for r in asked[0]["rows"]] == [LIVE, p1, c1, g1, p2, c2, g2]
    labels = [r["label"] for r in asked[0]["rows"]][1:4]
    assert not labels[0].startswith("—")
    assert labels[1].startswith("— ") and not labels[1].startswith("— —")
    assert labels[2].startswith("— — ")


async def test_all_on_a_long_list_sends_the_flag_and_no_ids(monkeypatch) -> None:
    approve(monkeypatch)
    many = [tw._sub(_uid(i), LIVE, f"t{i}") for i in range(1, 251)]

    def wide(call: dict) -> Any:
        if call["path"].endswith("/subtree"):
            return {"task_id": LIVE, "subtasks": many, "hidden": 0}
        return _delete_gateway(call)

    calls = fake_gateway(monkeypatch, wide)
    await skill_projects.delete_task(LIVE, subtasks_to_delete="all")
    assert _task_delete(calls)["params"] == {"delete_all_subtasks": "true"}

