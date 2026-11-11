"""CAL-1a (task 1675): the ``calendar-day`` tool pack of the main chat.

The pack holds the sixteen day planner, calendar settings, day rule and day flow tools of ``skill_my_tasks``. The Projects
assistant builds them in ``_agent_tools()`` only. Each case names, in its
docstring, the mutation that turns it red (R7).

Run: ``uv run pytest tests/unit/test_calendar_day_pack.py -q``.
"""
from __future__ import annotations

import pytest

pytest.importorskip("agent_framework", reason="MAF not installed")
import skill_my_tasks
import skill_projects
from acb_skills import skill_families as sf
from acb_skills import tool_packs as tp
from acb_skills.tool_guard import tool_name
from orchestrator import _tool_injection as ti

from tests.unit.test_tool_packs_choice import EVERY, _turn

PA = "projects-assistant"
SLUG = "calendar-day"
EIGHT = (
    "my_tasks_day_digest", "my_tasks_list_schedule", "my_tasks_plan_day",
    "my_tasks_replan_day", "my_tasks_rollover", "my_tasks_schedule",
    "my_tasks_set_one_thing", "my_tasks_unschedule",
)
#: CAL-2 (task 1676): the two calendar settings tools.
SETTINGS = ("my_tasks_get_calendar_settings", "my_tasks_update_calendar_settings")
#: CAL-4 (task 1678): the two day flows.
FLOWS = ("my_tasks_start_my_day", "my_tasks_close_my_day")
#: CAL-3 (task 1677): the four day rule tools. The pack holds sixteen with CAL-4.
DAY_RULES = ("my_tasks_list_day_rules", "my_tasks_add_day_rule",
             "my_tasks_edit_day_rule", "my_tasks_delete_day_rule")
SIXTEEN = tuple(sorted((*EIGHT, *SETTINGS, *DAY_RULES, *FLOWS)))


def test_the_family_is_the_sixteen_planner_settings_day_rule_and_flow_tools() -> None:
    """Mutation: drop a tool, add the seventeenth (estimate_stats), or name one that
    skill_my_tasks lacks -> red."""
    fam = sf.SKILL_FAMILIES[SLUG]
    assert tuple(fam["tools"]) == SIXTEEN == tuple(sorted(SIXTEEN))
    assert fam["owner"] == PA and sf.is_pack(SLUG) and not fam["core"]
    assert set(SIXTEEN) <= set(skill_my_tasks.__all__)
    assert len(SIXTEEN) <= tp.MAX_PACK_TOOLS


def test_the_specialist_keeps_its_own_tool_list() -> None:
    """The planner tools are in no Projects family, in ``skill_projects.__all__``
    or in the specialist's own agent. Mutation: add them to ``_TOOLS`` or make
    ``build_agents`` use ``_agent_tools()`` -> red."""
    mod = ti._pack_owner_module(PA)
    own = {tool_name(t) for t in mod._own_tools()}
    assert own == set(skill_projects.__all__)
    assert not own & set(SIXTEEN)
    assert set(SIXTEEN) <= {tool_name(t) for t in mod._agent_tools()}
    for slug, fam in sf.SKILL_FAMILIES.items():
        if slug != SLUG and fam.get("owner") == PA:
            assert not set(fam["tools"]) & set(SIXTEEN), slug


def test_the_pack_builds_the_same_functions_with_their_risk() -> None:
    """The pack reuses the skill_my_tasks functions (one write path), strict and
    with the risk of each. Mutation: copy the bodies, or drop the wrapper -> red."""
    mod = ti._pack_owner_module(PA)
    built = ti._owner_pack_tools(SLUG, PA, mod)
    assert tuple(tool_name(t) for t in built) == SIXTEEN
    for tool in built:
        name = tool_name(tool)
        assert type(tool).__name__ == "_StrictTool", name
        assert tool.parameters().get("additionalProperties") is False, name
        src = getattr(skill_my_tasks, name)
        assert getattr(tool.func, "__wrapped__", None) is src, name
        assert getattr(tool.func, "__tool_risk__", None) == getattr(src, "__tool_risk__", None)


@pytest.mark.parametrize("words", [
    "rebuild my day", "start my day please", "fit what's left", "I fell behind today",
    "block an hour for the report", "what is on my calendar",
])
def test_the_trigger_words_load_the_pack(words: str) -> None:
    """Mutation: drop a word from ``_CALENDAR_DAY_WORDS`` -> red."""
    choice = tp.choose(_turn(words=words), allowed=EVERY, base_tools=55)
    assert SLUG in choice.slugs, (words, choice)


def test_a_mail_turn_does_not_load_the_pack() -> None:
    """A bare "schedule" or "calendar" is no word. Mutation: add one -> red."""
    choice = tp.choose(_turn(words="schedule a meeting by mail"), allowed=EVERY, base_tools=55)
    assert SLUG not in choice.slugs
    assert not {"schedule", "calendar"} & set(tp.PACKS[SLUG].words)


def test_the_rules_carry_each_planner_rule() -> None:
    """One check for each rule of ``_CALENDAR_DAY_RULES``. Mutation: delete the
    sentence of a rule -> red."""
    rules = tp.rules_for(SLUG)
    flat = " ".join(rules.split())
    assert "Rebuild (my_tasks_plan_day)" in flat and "sweeps in the unfinished tasks" in flat
    assert "evicts the blocks that no longer fit" in flat
    assert "Fixed blocks never move" in flat
    assert "deep_work" in flat and "energy windows" in flat
    assert "apply=False" in flat and "show the member the block list" in flat
    assert "apply=True only after the member agrees" in flat
    for name in SIXTEEN:
        assert name in flat, name
    assert "No Projects agent exists to hand a task to." in rules
    assert "Never name a tool to the member." in rules


def test_the_pack_follows_the_core_projects_pack() -> None:
    """Production's ``TOOL_PACKS`` names ``projects`` and no new slug. Mutation:
    drop the ``FOLLOWS`` line -> the pack is off in production -> red."""
    assert tp.FOLLOWS[SLUG] == "projects"
    assert SLUG in tp.with_followers({"projects"})
    assert SLUG not in tp.with_followers({"projects-plan"})
    order = list(tp.PACKS)
    assert order.index(SLUG) > order.index("projects-authority")
    assert order.index(SLUG) < order.index("studio")


def test_the_cap_drops_the_pack_before_email() -> None:
    """The cap keeps three packs and 110 tools. The pack is late in the order,
    so a turn that over-fills drops it first. Mutation: move it before the email
    packs -> red."""
    words = "check my inbox and rebuild my day"
    cut = tp.choose(_turn(words=words), allowed=EVERY, base_tools=75)
    assert "email" in cut.slugs and SLUG in cut.skipped


def test_the_day_planner_pack_does_not_count_as_a_way_to_reach_the_owner() -> None:
    """The tools of this pack edit no task, so the pack must not stand in for a
    Projects work pack in the reach test of ``plan_loader``, and the core pack
    steps aside as before. Since CAL-3 (task 1677) the pack holds 14 tools (16 since CAL-4), more
    than the 11 of ``projects-reports``, so the COUNT alone cannot keep it out.
    ``REACH_NOT_WORK`` names it. Mutation: empty ``REACH_NOT_WORK`` -> the core
    pack stays, the specialist leaves, and no work pack can load -> red."""
    from tests.unit.test_projects_packs import _BASE, _SPECIALIST, _sticky_turn

    assert SLUG in tp.REACH_NOT_WORK
    assert len(SIXTEEN) >= tp.REACH_MIN_TOOLS, "non-vacuity: the count would let it in"
    turn = _sticky_turn("archive tasks that are done", "projects",
                        tp.Sticky("projects-bulk", (19, 0), 19))
    choice = tp.choose(turn, allowed=EVERY, base_tools=_BASE, specialists=_SPECIALIST)
    assert choice.slugs == ("projects-bulk",), choice
    assert choice.skipped == ("projects",) and choice.evicted == (), choice
