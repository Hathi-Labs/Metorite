"""CAL-2 (task 1676): the calendar settings tools of the ``calendar-day`` pack.

The tools cover the calendar fields of ``GET`` and ``PUT /tasks/settings`` and
nothing else. The write proposes first (``confirmed=False`` writes nothing) and
saves after the member agrees. Each case names the mutation that turns it red (R7).

Run: ``uv run pytest tests/unit/test_calendar_settings_tools.py -q``.
"""
from __future__ import annotations

import inspect
from typing import Any

import pytest

pytest.importorskip("agent_framework", reason="MAF not installed")
import skill_my_tasks
from acb_skills import chat_parity as cp
from acb_skills import egress as eg
from acb_skills import skill_families as sf
from acb_skills.tool_guard import tool_name
from orchestrator import _tool_injection as ti
from skill_my_tasks import core

from tests.unit.test_calendar_day_pack import PA, SETTINGS, SLUG

GET, UPD = SETTINGS

#: What the gateway holds, with the non-calendar fields that must never show.
STORED: dict[str, Any] = {
    "chat_model": "tier-powerful", "capture_dedup": True, "background_sync": True,
    "day_templates": [{"days": [1], "start_hour": 9, "end_hour": 10, "kind": "focus"}],
    "planning_prompt": "", "day_start_hour": 9, "day_end_hour": 17,
    "daily_capacity_mins": 360, "buffer_mins": 0, "max_focus_run_mins": 90,
    "break_mins": 10, "lunch_start_hour": None, "lunch_end_hour": None,
    "energy_windows": [], "auto_rollover": True, "timezone": "Asia/Kolkata",
}


class Gateway:
    def __init__(self, stored: dict[str, Any] | None = None) -> None:
        self.stored = dict(STORED if stored is None else stored)
        self.calls: list[tuple[str, str, Any]] = []

    async def __call__(self, method: str, path: str, **kw: Any) -> Any:
        self.calls.append((method, path, kw.get("json")))
        return dict(self.stored)

    @property
    def writes(self) -> list[Any]:
        return [j for m, _, j in self.calls if m == "PUT"]


@pytest.fixture
def gw(monkeypatch: pytest.MonkeyPatch) -> Gateway:
    g = Gateway()
    monkeypatch.setattr(core, "_request", g)
    return g


async def test_get_returns_the_calendar_fields_only(gw: Gateway) -> None:
    """Mutation: print a model, a sync flag, capture_dedup or the templates -> red."""
    text = await core.my_tasks_get_calendar_settings()
    assert "Working hours: 9-17" in text and "Time zone: Asia/Kolkata" in text
    assert "Standing instruction: the default" in text and "Auto rollover: on" in text
    for banned in ("tier-powerful", "chat_model", "dedup", "sync", "template", "focus\n"):
        assert banned not in text, banned
    assert gw.calls == [("GET", "/tasks/settings", None)]


async def test_a_proposal_writes_nothing_and_shows_old_to_new(gw: Gateway) -> None:
    """The change shows as "Working hours: 9-17 -> 8-18" before any write.
    Mutation: write when ``confirmed`` is False -> red."""
    text = await core.my_tasks_update_calendar_settings(day_start_hour=8, day_end_hour=18)
    assert "• Working hours: 9-17 -> 8-18" in text and text.startswith("Not saved yet")
    assert gw.writes == []


async def test_a_confirmed_update_sends_only_the_given_keys(gw: Gateway) -> None:
    """Partial update. Mutation: send the whole settings body, or a None -> red."""
    text = await core.my_tasks_update_calendar_settings(auto_rollover=False, confirmed=True)
    assert gw.writes == [{"auto_rollover": False}]
    assert text.startswith("Saved.") and "Auto rollover: on -> off" in text


async def test_zero_stays_zero(gw: Gateway) -> None:
    """Task 1210: hour 0 is a value, not "not given". Mutation: test the
    argument for truth instead of ``is not None`` -> red."""
    await core.my_tasks_update_calendar_settings(
        day_start_hour=0, buffer_mins=0, break_mins=0, max_focus_run_mins=0, confirmed=True)
    sent = gw.writes[0]
    assert sent == {"day_start_hour": 0, "buffer_mins": 0, "break_mins": 0,
                    "max_focus_run_mins": 0}


@pytest.mark.parametrize("kw, word", [
    ({"day_start_hour": 24}, "day_start_hour"),
    ({"day_start_hour": -1}, "day_start_hour"),
    ({"day_end_hour": 25}, "day_end_hour"),
    ({"day_start_hour": 18}, "must end after it starts"),
    ({"daily_capacity_mins": 2000}, "daily_capacity_mins"),
    ({"buffer_mins": 90}, "buffer_mins"),
    ({"break_mins": -5}, "break_mins"),
    ({"lunch_start_hour": 12}, "both"),
    ({"lunch_start_hour": 14, "lunch_end_hour": 13}, "Lunch must end after"),
    ({"energy_windows": [{"start_hour": 9, "end_hour": 12, "energy": "huge"}]}, "energy window"),
    ({"energy_windows": [{"start_hour": 12, "end_hour": 9, "energy": "high"}]}, "energy window"),
    ({"timezone": "Mars/Base"}, "not a time zone"),
])
async def test_an_out_of_range_value_is_refused_and_nothing_is_written(
        gw: Gateway, kw: dict[str, Any], word: str) -> None:
    """Mutation: drop a range check -> the write goes out -> red."""
    text = await core.my_tasks_update_calendar_settings(confirmed=True, **kw)
    assert word in text and "I changed nothing" in text, text
    assert gw.writes == []


async def test_lunch_off_and_an_energy_window_and_the_instruction(gw: Gateway) -> None:
    """Both lunch hours 0 turn lunch off, as the Calendar sheet does."""
    text = await core.my_tasks_update_calendar_settings(
        lunch_start_hour=12, lunch_end_hour=13,
        energy_windows=[{"start_hour": 9, "end_hour": 12, "energy": "high"}],
        planning_prompt="Never book meetings before 10.", confirmed=True)
    assert "Lunch: off -> 12-13" in text and "Energy windows: none -> 9-12 high" in text
    assert "Standing instruction: the default -> «Never book meetings before 10.»" in text
    assert gw.writes[0]["lunch_start_hour"] == 12


async def test_nothing_given_and_nothing_changed_write_nothing(gw: Gateway) -> None:
    assert "No setting was given" in await core.my_tasks_update_calendar_settings(confirmed=True)
    same = await core.my_tasks_update_calendar_settings(day_start_hour=9, confirmed=True)
    assert "Nothing changes" in same
    assert gw.writes == []


def test_the_signature_has_calendar_fields_only() -> None:
    """The tool has no parameter for a non-calendar field. Mutation: add
    ``chat_model`` or ``capture_dedup`` -> red."""
    params = set(inspect.signature(core.my_tasks_update_calendar_settings).parameters)
    assert params == {
        "planning_prompt", "day_start_hour", "day_end_hour", "daily_capacity_mins",
        "buffer_mins", "max_focus_run_mins", "break_mins", "lunch_start_hour",
        "lunch_end_hour", "energy_windows", "auto_rollover", "timezone", "confirmed"}


async def test_a_non_calendar_field_is_refused_by_name(gw: Gateway) -> None:
    """The strict pack tool answers an undeclared argument by name and calls
    nothing. Mutation: build the pack tool without ``extra=forbid`` -> red."""
    mod = ti._pack_owner_module(PA)
    tool = next(t for t in ti._owner_pack_tools(SLUG, PA, mod) if tool_name(t) == UPD)
    out = await tool.invoke(arguments={"chat_model": "tier-fast", "confirmed": True})
    text = out if isinstance(out, str) else "\n".join(str(getattr(c, "text", "")) for c in out)
    assert "chat_model" in text and "no argument" in text, text
    assert gw.calls == []


def test_the_risk_of_each_tool() -> None:
    """The read is read-only. The write is not. Mutation: drop ``read_only`` -> red."""
    assert core.my_tasks_get_calendar_settings.__tool_risk__["read_only"] is True
    assert not core.my_tasks_update_calendar_settings.__tool_risk__.get("read_only")


def test_the_held_table_holds_off_the_write_and_keeps_the_read(monkeypatch) -> None:
    """Mutation: drop the write from ``HELD_OFF_PACK_WRITES`` or add the read -> red."""
    from acb_common import get_settings

    monkeypatch.setattr(get_settings(), "held_card_writes_orgs", "*")
    assert UPD in eg.HELD_OFF_PACK_WRITES[SLUG] and GET not in eg.HELD_OFF_PACK_WRITES[SLUG]
    assert eg.is_held_off_pack_write(skill_my_tasks.my_tasks_update_calendar_settings, SLUG)
    assert not eg.is_held_off_pack_write(skill_my_tasks.my_tasks_get_calendar_settings, SLUG)
    for name in SETTINGS:
        assert eg.is_held_off_write(name, owner="task-manager") is False


def test_the_tasks_assistant_does_not_get_the_tools() -> None:
    """Mutation: add either tool to the task-manager imports -> red."""
    from pathlib import Path

    src = Path("apps/agents/agent-task-manager/agents.py").read_text(encoding="utf-8")
    for name in SETTINGS:
        assert name not in src


def test_the_family_lists_them_and_parity_covers_the_routes() -> None:
    """Mutation: move the routes back to DEBT -> red."""
    assert set(SETTINGS) <= set(sf.SKILL_FAMILIES[SLUG]["tools"])
    assert cp.COVERED["GET /api/tasks/settings"] == GET
    assert cp.COVERED["PUT /api/tasks/settings"] == UPD
    assert "GET /api/tasks/settings" not in cp.DEBT
    assert "PUT /api/tasks/settings" not in cp.DEBT


def test_a_capacity_change_inside_one_hour_figure_still_shows() -> None:
    """The verifier's finding 2: 100 -> 102 minutes is a change."""
    from skill_my_tasks.core import _cal_view

    assert _cal_view({"daily_capacity_mins": 100})["Daily capacity"] != \
        _cal_view({"daily_capacity_mins": 102})["Daily capacity"]
    assert _cal_view({"daily_capacity_mins": 360})["Daily capacity"] == "6 h"
