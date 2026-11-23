"""CAL-4 (task 1678): the two day flows of the ``calendar-day`` pack.

``my_tasks_start_my_day`` and ``my_tasks_close_my_day`` gather the steps of the
Start your day ritual and of the Day review in ONE reply. They write nothing:
every call is a ``GET`` or an ``apply=False`` proposal. A step that fails shows
its own line and the rest stays. Each case names the mutation that turns it red (R7).

Run: ``uv run pytest tests/unit/test_day_flows_tools.py -q``.
"""
from __future__ import annotations

from typing import Any

import pytest

pytest.importorskip("agent_framework", reason="MAF not installed")
from acb_skills import egress as eg
from skill_my_tasks import core

from tests.unit.test_calendar_day_pack import FLOWS, SLUG

START, CLOSE = FLOWS

SUMMARY = {
    "day": "2026-11-07", "overdue_count": 2, "unscheduled_count": 3,
    "one_thing": {"id": "t1", "title": "Ship the report"},
    "scheduled": [
        {"id": "a", "title": "Write notes", "done": True, "start": "2026-11-07T09:00:00",
         "end": "2026-11-07T10:00:00"},
        {"id": "b", "title": "Call Ravi", "done": False, "start": "2026-11-07T11:00:00",
         "end": "2026-11-07T11:30:00"},
    ],
}
PROPOSAL = {"applied": False, "blocks": [
    {"title": "Fix bug", "start": "2026-11-07T14:00:00", "end": "2026-11-07T15:00:00"}]}
INBOX = {"rows": [
    {"id": "n1", "title": "Draft spec", "disposition": "NEXT", "sort_key": 1},
    {"id": "n2", "title": "Already planned", "disposition": "NEXT", "sort_key": 2,
     "scheduled_start": "2026-11-07T09:00:00"},
], "total": 2}


class Gateway:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, Any]] = []
        self.fail: set[str] = set()

    async def __call__(self, method: str, path: str, **kw: Any) -> Any:
        self.calls.append((method, path, kw.get("json")))
        if path in self.fail:
            raise core._GatewayError(f"Tasks {method} {path} failed (500): boom", 500)
        if path == "/tasks/calendar/day-summary":
            return SUMMARY
        if path == "/projects/my/inbox":
            return INBOX
        return PROPOSAL


@pytest.fixture
def gw(monkeypatch: pytest.MonkeyPatch) -> Gateway:
    g = Gateway()
    monkeypatch.setattr(core, "_request", g)
    return g


def _paths(g: Gateway) -> list[tuple[str, str]]:
    return [(m, p) for m, p, _ in g.calls]


async def test_start_my_day_composes_the_steps_in_order(gw: Gateway) -> None:
    """Mutation: drop a step or swap two of them -> red."""
    out = await core.my_tasks_start_my_day(energy_note="low energy")
    assert _paths(gw) == [
        ("GET", "/tasks/calendar/day-summary"), ("POST", "/tasks/calendar/rollover-today"),
        ("GET", "/projects/my/inbox"), ("POST", "/tasks/calendar/plan-today")]
    heads = ["Your day so far", "Roll over overdue work", "Pick the ★ One Thing",
             "Rebuild my day"]
    assert [out.index(h) for h in heads] == sorted(out.index(h) for h in heads)
    assert "Draft spec" in out and "Already planned" not in out
    assert "Next steps" in out and "my_tasks_set_one_thing" in out


async def test_start_my_day_passes_the_energy_note_to_the_rebuild_only(gw: Gateway) -> None:
    """Mutation: send the note to the rollover, or drop it -> red."""
    await core.my_tasks_start_my_day(energy_note="low energy")
    bodies = {p: j for _, p, j in gw.calls if j is not None}
    assert bodies["/tasks/calendar/plan-today"] == {"apply": False, "energy_note": "low energy"}
    assert bodies["/tasks/calendar/rollover-today"] == {"apply": False}


@pytest.mark.parametrize("tool", [START, CLOSE])
async def test_a_flow_writes_nothing(gw: Gateway, tool: str) -> None:
    """Every call is a GET or an apply=False POST. Mutation: send apply=True, or
    call PUT/PATCH/DELETE -> red. The reply never says that something was applied."""
    out = await getattr(core, tool)()
    assert gw.calls
    for method, _path, body in gw.calls:
        assert method in {"GET", "POST"}
        if method == "POST":
            assert body["apply"] is False
    assert "Applied" not in out and "nothing is changed yet" in out


async def test_a_proposal_that_says_applied_still_reads_as_a_proposal(
        gw: Gateway, monkeypatch: pytest.MonkeyPatch) -> None:
    """Mutation: format with the ``applied`` flag of the answer -> red."""
    async def liar(method: str, path: str, **kw: Any) -> Any:
        if path.endswith("-today"):
            return {**PROPOSAL, "applied": True}
        return await gw(method, path, **kw)

    monkeypatch.setattr(core, "_request", liar)
    out = await core.my_tasks_start_my_day()
    assert "Applied" not in out and "Proposed plan" in out


async def test_a_failed_step_keeps_the_rest_and_is_named(gw: Gateway) -> None:
    """Mutation: let the exception out of ``_flow_step`` -> red."""
    gw.fail = {"/tasks/calendar/rollover-today"}
    out = await core.my_tasks_start_my_day()
    assert "The rollover proposal is not available now:" in out
    assert "boom" in out
    assert "Call Ravi" in out and "Draft spec" in out and "Fix bug" in out


async def test_every_step_may_fail_and_the_reply_still_comes(gw: Gateway) -> None:
    gw.fail = {"/tasks/calendar/day-summary", "/tasks/calendar/rollover-today",
               "/projects/my/inbox", "/tasks/calendar/plan-today"}
    out = await core.my_tasks_start_my_day()
    assert out.count("is not available now") == 4 and "Next steps" in out


async def test_close_my_day_shows_done_left_rollover_and_tomorrow(gw: Gateway) -> None:
    """Mutation: swap the done and not-done lists -> red."""
    out = await core.my_tasks_close_my_day()
    assert _paths(gw) == [
        ("GET", "/tasks/calendar/day-summary"), ("POST", "/tasks/calendar/rollover-today"),
        ("GET", "/projects/my/inbox")]
    assert "Done today: 1" in out and "Not done today: 1" in out
    assert out.index("Write notes") < out.index("Not done today") < out.index("Call Ravi")
    assert "Move what is not done back to the list" in out
    assert "date 2026-11-08" in out


async def test_close_my_day_says_it_cannot_fit_tomorrow(gw: Gateway) -> None:
    """The planner routes plan TODAY only. Mutation: call plan-today from the
    close flow, or drop the note -> red."""
    out = await core.my_tasks_close_my_day()
    assert "Fit tomorrow: not available" in out
    assert "/tasks/calendar/plan-today" not in [p for _, p, _ in gw.calls]


async def test_close_my_day_keeps_the_rest_when_the_summary_fails(gw: Gateway) -> None:
    gw.fail = {"/tasks/calendar/day-summary"}
    out = await core.my_tasks_close_my_day()
    assert "The summary of today is not available now:" in out
    assert "Fix bug" in out and "Draft spec" in out
    assert "date tomorrow's date" not in out and "tomorrow's date" in out


def test_both_flows_are_held_off_in_a_held_chat() -> None:
    """Mutation: take a flow out of ``HELD_OFF_PACK_WRITES`` -> red."""
    for name in FLOWS:
        assert name in eg.HELD_OFF_PACK_WRITES[SLUG]
