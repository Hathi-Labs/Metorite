"""Fence: the planning pages and the plan_slices prompt teach the gratification ladder.

The rule is section 6d of the orchestration playbook spec. The pages must state
the demo task, the `Rung N` tag, rung 0 and "the foundation rides with the rung".
"""
from __future__ import annotations

import pytest
from acb_playbook import loader
from agent_edge import playbook_prompts

_PAGES = ("start-here", "kit/plan-the-work", "kit/skill/planning")


def _page(slug: str) -> str:
    topic = loader.load_topic(slug)
    assert topic is not None, slug
    return topic.body.lower()


def _steps() -> str:
    return " ".join(playbook_prompts.PROMPTS["plan_slices"].steps).lower()


@pytest.mark.parametrize("slug", _PAGES)
def test_page_states_the_core_words(slug):
    body = _page(slug)
    assert "demo task" in body
    assert "rung n" in body
    assert "rung 0" in body


def test_plan_the_work_states_the_whole_rule():
    body = _page("kit/plan-the-work")
    assert "the foundation rides with the rung" in body
    assert "the tag `demo`" in body or "and the tag `demo`" in body
    assert "lowest open rung" in body
    assert "rung n demo" in body


def test_start_here_defines_the_words():
    body = _page("start-here")
    assert "foundation rides with the rung" in body
    for word in ("**rung**", "**demo task**", "**rung 0**"):
        assert word in body


def test_planning_skill_orders_by_rung_then_slack_then_priority():
    body = _page("kit/skill/planning")
    assert body.index("lowest open rung") < body.index("least slack") < body.index("higher priority")


def test_plan_slices_steps_group_into_rungs_first():
    steps = _steps()
    assert "demo" in steps
    assert "rung 0" in steps
    assert "foundation rides with the rung" in steps
    assert steps.index("group the work into rungs") < steps.index("cut the slices of rungs 1 to 3")


@pytest.mark.parametrize("slug", ("kit/plan-the-work", "kit/skill/planning"))
def test_page_names_the_default_and_the_way_to_change_it(slug):
    body = _page(slug)
    if slug == "kit/plan-the-work":
        assert "the ladder is the default" in body
        assert "asks for another way" in body
        assert "plan-driven" in body
    else:
        assert "where the ladder is off" in body
        assert "critical path" in body


def test_plan_slices_checks_the_way_of_working_first():
    steps = _steps()
    assert steps.index("way of working") < steps.index("group the work into rungs")
    assert "default" in steps
