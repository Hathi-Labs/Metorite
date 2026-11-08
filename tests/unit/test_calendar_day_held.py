"""CAL-1a (task 1675), fix round 1: the ``calendar-day`` pack in a held chat (RX-1, D169).

A held chat is one that read outside mail. The choice, and why:

* **The Tasks assistant keeps what it has on main.** None of the eight planner
  tools is held off for ``task-manager``. ``is_held_off_write`` matches on the
  bare name before the owner, so the pack must not put the names in
  ``egress._owner_tool_names()``.
* **The pack keeps its two reads** (``my_tasks_day_digest`` and
  ``my_tasks_list_schedule``, ``read_only=True``).
* **The pack holds off its six writes.** They show no card: a plan is proposed
  in text and applied by a second call, so mail that the chat read could steer
  the apply. ``egress.HELD_OFF_PACK_WRITES`` names them by pack slug.

Run: ``uv run pytest tests/unit/test_calendar_day_held.py -q``.
"""
from __future__ import annotations

import pytest

pytest.importorskip("agent_framework", reason="MAF not installed")
import skill_my_tasks
from acb_skills import egress as eg
from acb_skills import tool_packs as tp
from acb_skills.tool_guard import tool_name
from orchestrator import _tool_injection as ti

from tests.unit._egress_mark_store import install_memory_mark_store
from tests.unit.test_calendar_day_pack import FOURTEEN, SLUG
from tests.unit.test_tool_packs_choice import _run_ctx, _tenant

READS = ("my_tasks_day_digest", "my_tasks_get_calendar_settings",
         "my_tasks_list_day_rules", "my_tasks_list_schedule")
WRITES = tuple(n for n in FOURTEEN if n not in READS)


@pytest.fixture(autouse=True)
def _held_card_writes_on(monkeypatch) -> None:
    from acb_common import get_settings

    monkeypatch.setattr(get_settings(), "held_card_writes_orgs", "*")


def test_the_reads_are_marked_read_only() -> None:
    """Mutation: drop ``read_only=True`` from either read -> red."""
    for name in READS:
        hints = getattr(getattr(skill_my_tasks, name), "__tool_risk__", None)
        assert hints and hints.get("read_only") is True, name
    for name in WRITES:
        assert not getattr(skill_my_tasks, name).__tool_risk__.get("read_only"), name


@pytest.mark.parametrize("name", FOURTEEN)
def test_the_tasks_assistant_keeps_every_planner_tool_in_a_held_chat(name: str) -> None:
    """The regression fence. On main no planner tool is held off for the Tasks
    assistant. Mutation: put the family in ``_owner_tool_names()`` -> red."""
    fn = getattr(skill_my_tasks, name)
    assert eg.is_held_off_write(fn, owner="task-manager") is False, name
    assert eg.is_held_off_write(name, owner="task-manager") is False, name
    assert name not in eg._owner_tool_names()


def test_the_pack_holds_off_its_ten_writes_and_keeps_its_four_reads() -> None:
    """Mutation: add a read to ``HELD_OFF_PACK_WRITES`` or drop a write -> red."""
    assert set(eg.HELD_OFF_PACK_WRITES[SLUG]) == set(WRITES)
    for name in WRITES:
        assert eg.is_held_off_pack_write(getattr(skill_my_tasks, name), SLUG), name
    for name in READS:
        assert not eg.is_held_off_pack_write(getattr(skill_my_tasks, name), SLUG), name
    assert not eg.is_held_off_pack_write("my_tasks_plan_day", "projects")


def test_the_switch_off_holds_nothing_off(monkeypatch) -> None:
    """With the org off a held chat drops what it dropped before the slice."""
    from acb_common import get_settings

    monkeypatch.setattr(get_settings(), "held_card_writes_orgs", "")
    for name in FOURTEEN:
        assert not eg.is_held_off_pack_write(name, SLUG)


async def test_check_my_inbox_and_rebuild_my_day_in_a_held_chat(monkeypatch) -> None:
    """The turn "check my inbox and rebuild my day": the chat read mail, so the
    pack builds held. The four reads stay and the ten writes go. Mutation: skip
    ``_is_held_off_pack`` in ``_pack_tools`` -> the writes stay -> red. Mutation:
    hold off a read -> red."""
    install_memory_mark_store(monkeypatch)
    choice = tp.choose(
        tp.PackTurn("o", "s", "m", 1, (), "check my inbox and rebuild my day"),
        allowed=frozenset({"email", SLUG}), base_tools=40)
    assert set(choice.slugs) == {"email", SLUG}
    ctx = {**_run_ctx(no_egress=True), tp.PACK_RUN_KEY: "rk-cal1a"}
    with _tenant():
        build = await ti._pack_tools(SLUG, ctx, holds=())
    assert build.held is True
    assert tuple(sorted(tool_name(t) for t in build.tools)) == tuple(sorted(READS))
    assert set(build.dropped) == set(WRITES)
