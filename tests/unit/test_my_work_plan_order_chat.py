"""SW-68a — the chat ``my_work`` plan read, with a fake gateway (no database).

Any failure of the plan read keeps today's order and today's text. The plan is
an aid, and it never hides the work (P2-1). A blocker is named by its number
and title, and a blocker that the member cannot see has no name.

Mutations (R7). ``R`` is ``apps/skills/skill-projects/skill_projects/reads.py``.

* C1. ``R`` ``_plan_ordered`` catches only ``GatewayRefusal``
  (``test_any_failure_of_the_plan_read_keeps_the_old_text``).
* C2. ``R`` ``_blocker_text`` returns the bare id (``test_a_blocker_is_named_by_number_and_title``).
"""
from __future__ import annotations

from typing import Any

import httpx
from acb_common.tool_refusals import GatewayRefusal
from skill_projects import reads

A, B, BLOCKER = (f"00000000-0000-4000-8000-00000000000{n}" for n in (1, 2, 3))
EPIC = "00000000-0000-4000-8000-0000000000e1"


def _rows() -> list[dict[str, Any]]:
    return [{"id": A, "title": "First", "task_number": 1, "status_id": "s1"},
            {"id": B, "title": "Second", "task_number": 2, "status_id": "s1"}]


def _fake(monkeypatch, plan: Any) -> None:
    """``plan`` is the answer of the plan route, or an exception to raise."""

    async def get(path: str, params: dict[str, Any] | None = None) -> Any:
        if path == "/projects/assigned-to-me":
            return {"rows": _rows(), "total": 2}
        if path == "/projects/plan/order":
            if isinstance(plan, BaseException):
                raise plan
            return plan
        if path == "/projects/my/today":
            raise GatewayRefusal("no clock")
        return {"rows": []}

    monkeypatch.setattr(reads, "get", get)


async def _text(monkeypatch, plan: Any) -> str:
    _fake(monkeypatch, plan)
    return await reads.my_work()


async def test_any_failure_of_the_plan_read_keeps_the_old_text(monkeypatch):
    old = await _text(monkeypatch, {"order": [A, B], "plan": {}})
    failures: list[Any] = [
        httpx.ReadTimeout("slow"), httpx.ConnectError("down"),
        httpx.RemoteProtocolError("bad"), GatewayRefusal("no", status=500),
        RuntimeError("anything"), ["not", "a", "dict"], None,
        {"order": "x", "plan": {}}, {"order": [A], "plan": {}},
        {"order": [A, A], "plan": {}},
    ]
    for failure in failures:
        assert await _text(monkeypatch, failure) == old, failure


async def test_a_plan_order_is_taken_and_the_note_is_printed(monkeypatch):
    plan = {B: {"epic_id": EPIC, "ready": True, "slack_mins": 0, "blocked_by": [],
                "blocked_by_hidden": 0}}
    text = await _text(monkeypatch, {"order": [B, A], "plan": plan, "labels": {}})
    assert text.index("Second") < text.index("First")
    assert "plan: ready · slack 0 mins" in text


async def test_a_blocker_is_named_by_number_and_title(monkeypatch):
    plan = {A: {"epic_id": EPIC, "ready": False, "slack_mins": 0,
                "blocked_by": [BLOCKER], "blocked_by_hidden": 1}}
    labels = {BLOCKER: {"number": 7, "title": "Fix the extruder"}}
    text = await _text(monkeypatch, {"order": [B, A], "plan": plan, "labels": labels})
    assert "blocked by #7" in text and "Fix the extruder" in text
    assert BLOCKER not in text
    assert "blocked by a task hidden from you" in text


async def test_with_the_flag_off_the_text_is_the_text_of_main(monkeypatch):
    """The gateway route answers the input order, no plan and no labels while
    the org is off. The text is then the text of main, byte for byte. Main
    makes no plan read, so the baseline stubs ``_plan_ordered`` out."""
    on_route = await _text(monkeypatch, {"order": [A, B], "plan": {}, "labels": {}})

    async def no_plan(rows):
        return rows, {}, {}

    _fake(monkeypatch, {"order": [A, B], "plan": {}, "labels": {}})
    monkeypatch.setattr(reads, "_plan_ordered", no_plan)
    assert await reads.my_work() == on_route


async def test_a_slow_plan_read_keeps_the_old_text(monkeypatch):
    import asyncio

    old = await _text(monkeypatch, {"order": [A, B], "plan": {}})
    monkeypatch.setattr(reads, "PLAN_ORDER_BUDGET_S", 0.1)

    async def get(path, params=None):
        if path == "/projects/plan/order":
            await asyncio.sleep(30)
        if path == "/projects/assigned-to-me":
            return {"rows": _rows(), "total": 2}
        if path == "/projects/my/today":
            raise GatewayRefusal("no clock")
        return {"rows": []}

    monkeypatch.setattr(reads, "get", get)
    assert await reads.my_work() == old


async def test_view_archived_reads_the_one_list_route_over_mine(monkeypatch):
    """ARC-1 chat parity. ``view="archived"`` reads ``scope=mine`` with
    ``archived_only`` (the Archive chip's read) and keeps the plan read out."""
    seen: list[tuple[str, dict[str, Any]]] = []

    async def get(path: str, params: dict[str, Any] | None = None) -> Any:
        seen.append((path, params or {}))
        if path == "/projects/tasks":
            return {"rows": [{**_rows()[0], "archived_at": "2026-11-01T00:00:00Z"}], "total": 1}
        if path == "/projects/my/today":
            raise GatewayRefusal("no clock")
        return {"rows": []}

    monkeypatch.setattr(reads, "get", get)
    out = await reads.my_work(view="archived")
    listed = [p for path, p in seen if path == "/projects/tasks"]
    assert listed and listed[0]["scope"] == "mine" and listed[0]["archived_only"] is True
    assert "My archive (1 total" in out
    assert "First" in out
    assert all(path != "/projects/plan/order" for path, _ in seen)
