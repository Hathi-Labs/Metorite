"""SW-68a — the MCP ``my_work`` plan read, on the fake gateway (no database).

Spec: ``project-docs/specs/agent_coordination.md`` SW-68 and
``project-docs/specs/external_agent_access.md`` §12.4.

The R8 file ``test_my_work_plan_order_r8.py`` proves the order on a real
database. This file proves the edge side with a fake gateway:

* the edge sends the WHOLE assigned list (held and blocked rows too) to the
  plan route and hides rows only after it has the order;
* the flag ``MY_WORK_PLAN_ORDER_ORGS`` is off by default: no plan call, and the
  answer is the old answer;
* a plan read that hangs, fails or answers a wrong shape keeps today's order,
  and ``my_work`` never turns into "busy".

Mutations (R7). ``T`` is ``apps/services/agent_edge/agent_edge/tools.py``.

* E1. ``T`` ``_my_work`` sends only the shown rows to ``_plan_of``
  (``test_the_whole_assigned_list_goes_to_the_plan``).
* E2. ``T`` ``_plan_of`` drops the ``asyncio.timeout``
  (``test_a_hanging_plan_read_keeps_the_old_order``).
* E3. ``T`` ``_plan_of`` skips the flag test
  (``test_with_the_flag_off_the_edge_sends_no_plan_read``).
"""
# ruff: noqa: F811 (a pytest fixture is imported, then named as an argument)
from __future__ import annotations

import asyncio
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from acb_common import get_settings
from agent_edge import gateway_client, tools

from tests.unit.test_mcp_edge_context import (  # noqa: F401 - fixtures and helpers
    _assign,
    _task_row,
    _uuid,
    _work,
    board,
    edge,
)

ROUTE = "/projects/plan/order"


@pytest.fixture
def plan_on(monkeypatch):
    monkeypatch.setattr(get_settings(), "my_work_plan_order_orgs", "*")


def _plan(epic: str, *, ready: bool = True, slack: int | None = 0,
          blocked_by: list[str] | None = None, hidden: int = 0) -> dict[str, Any]:
    return {"epic_id": epic, "ready": ready, "slack_mins": slack,
            "blocked_by": blocked_by or [], "blocked_by_hidden": hidden}


def _plan_calls(board) -> list[Any]:
    return [r for r in board.requests if r.path == ROUTE]


class TestTheFlag:
    async def test_with_the_flag_off_the_edge_sends_no_plan_read(self, board):
        a, b = _uuid(9001), _uuid(9002)
        _assign(board, a, b)
        board.answers[("GET", ROUTE)] = (200, {"order": [b, a], "plan": {
            b: _plan(_uuid(9100))}})
        rows = await _work()
        assert [r["task_id"] for r in rows] == [a, b]
        assert all("plan" not in r for r in rows)
        assert _plan_calls(board) == []

    async def test_with_the_flag_on_the_edge_takes_the_order_and_adds_plan(
            self, board, plan_on):
        a, b = _uuid(9011), _uuid(9012)
        _assign(board, a, b)
        epic = _uuid(9100)
        board.answers[("GET", ROUTE)] = (200, {"order": [b, a], "plan": {
            b: _plan(epic, slack=0)}})
        rows = await _work()
        assert [r["task_id"] for r in rows] == [b, a]
        assert rows[0]["plan"] == {"epic_id": epic, "ready": True, "slack_mins": 0,
                                   "blocked_by": [], "blocked_by_hidden": 0}
        assert "plan" not in rows[1]


class TestTheWholeList:
    async def test_the_whole_assigned_list_goes_to_the_plan(self, board, plan_on):
        """A blocked row and a held row still go to the route, so the slot that
        the plan gives them is the slot that the chat gives them (P1)."""
        plain, direct, held, epic_one = (_uuid(9021 + n) for n in range(4))
        _assign(board, plain, direct, held, epic_one)
        board.tasks[direct]["blocked_by_count"] = 1
        board.tasks[held]["held_by"] = {"stale": False}
        board.answers[("GET", ROUTE)] = (200, {
            "order": [epic_one, direct, plain, held], "plan": {}})
        rows = await _work()
        sent = _plan_calls(board)[0].pairs["task_ids"].split(",")
        assert sorted(sent) == sorted([plain, direct, held, epic_one])
        # The edge hides the held and the blocked rows AFTER it has the order.
        assert [r["task_id"] for r in rows] == [epic_one, plain]


class TestAFailureKeepsTheOldOrder:
    async def _old(self, board) -> list[str]:
        return [r["task_id"] for r in await _work()]

    @pytest.mark.parametrize("answer", [
        (200, ["not", "a", "dict"]),
        (200, {"order": "x", "plan": {}}),
        (200, {"order": [], "plan": {}}),
        (500, {"detail": "boom"}),
        (404, {"detail": "Not Found"}),
    ])
    async def test_a_bad_answer_keeps_the_old_order(self, board, plan_on, answer):
        a, b = _uuid(9031), _uuid(9032)
        _assign(board, a, b)
        board.answers[("GET", ROUTE)] = answer
        rows = await _work()
        assert [r["task_id"] for r in rows] == [a, b]
        assert all("plan" not in r for r in rows)

    async def test_a_hanging_plan_read_keeps_the_old_order(
            self, board, plan_on, monkeypatch):
        a, b = _uuid(9041), _uuid(9042)
        _assign(board, a, b)
        monkeypatch.setattr(tools, "PLAN_ORDER_BUDGET_S", 0.2)
        real = tools.gateway_get

        async def slow(key, lookup, template, **kw):
            if template == ROUTE:
                await asyncio.sleep(30)
            return await real(key, lookup, template, **kw)

        monkeypatch.setattr(tools, "gateway_get", slow)
        started = asyncio.get_running_loop().time()
        rows = await _work()
        assert asyncio.get_running_loop().time() - started < 10
        assert [r["task_id"] for r in rows] == [a, b]
        assert all("plan" not in r for r in rows)

    async def test_a_busy_plan_read_does_not_make_my_work_busy(
            self, board, plan_on, monkeypatch):
        a = _uuid(9051)
        _assign(board, a)

        async def down(key, lookup, template, **kw):
            raise gateway_client.GatewayUnreachable()

        real = tools.gateway_get

        async def pick(key, lookup, template, **kw):
            if template == ROUTE:
                return await down(key, lookup, template, **kw)
            return await real(key, lookup, template, **kw)

        monkeypatch.setattr(tools, "gateway_get", pick)
        rows = await _work()
        assert [r["task_id"] for r in rows] == [a]
