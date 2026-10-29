"""WS-67 OA-8g: the pack rules and the load words that OA-8e run 2 found.

``project-docs/specs/one_assistant_context.md`` §4.3 ("As built, OA-8g").
Each case names the mutation that turns it red (R7).

Run: ``uv run pytest tests/unit/test_pack_rules_oa8g.py -q``.
"""
from __future__ import annotations

import pytest
from acb_skills import tool_packs as tp

SLUGS = ("projects", "projects-plan", "projects-structure", "projects-bulk")
HAND_OFF = ("While a Projects pack is held, do its work with its tools. "
            "Do not call call_agent for work that a held or offered pack covers.")


def _flat(text: str) -> str:
    return " ".join(text.split())


@pytest.mark.parametrize("slug", SLUGS)
def test_each_projects_pack_keeps_the_work_when_it_is_held(slug: str) -> None:
    """Mutation: drop the bullet from one pack -> that pack is red."""
    assert HAND_OFF in _flat(tp.PACKS[slug].rules)


def test_the_hand_off_bullet_is_not_in_an_email_or_crm_pack() -> None:
    """Mutation: copy the bullet into the CRM rules -> red."""
    for slug in ("email", "email-setup", "crm-records"):
        assert "Projects pack is held" not in tp.PACKS[slug].rules


def test_the_core_route_bullet_offers_every_pack_before_the_agent() -> None:
    """Mutation: restore "Neither fits beside these tools, so call call_agent"
    -> red."""
    rules = _flat(tp._PROJECTS_RULES)
    assert "When load_skill_tools offers one, load it." in rules
    assert "Neither fits" not in rules


def test_a_private_task_is_made_at_once() -> None:
    """Mutation: drop "Do not ask which project" -> red."""
    rules = _flat(tp._PROJECTS_RULES)
    assert 'For "remind me", "my own task" or "a private task", call it at once.' in rules
    assert "Do not ask which project." in rules
    assert "create_personal_task, in the member's own personal project" in rules


def test_a_convert_proposes_the_deal_name_and_the_card_confirms() -> None:
    """Mutation: drop the sentence, or ask in text -> red."""
    rules = _flat(tp._CRM_RULES)
    assert ("To convert a lead, call convert_lead at once, with a proposed "
            "deal_name: the lead's company. The card confirms the name.") in rules
    assert "Do not ask for the name, the amount or the date in text." in rules
    # The CRM rules still name only tools of their own pack.
    assert "create_task" not in rules


def test_mirror_mode_does_not_send_the_model_to_another_pack() -> None:
    """Mutation: drop the sentence -> red."""
    assert "Do not load another pack to move the deal." in _flat(tp._CRM_RULES)


@pytest.mark.parametrize(("words", "slug"), [
    ("Who is overloaded right now?", "projects-plan"),
    ("Is anyone overbooked?", "projects-plan"),
    ("Who has capacity this week?", "projects-plan"),
    ("Add tasks to Website Relaunch: QA pass, copy freeze", "projects-bulk"),
    ("Make a task for each of these: brief, copy", "projects-bulk"),
])
def test_the_new_words_load_their_pack(words: str, slug: str) -> None:
    """Mutation: drop the word from its list -> red."""
    assert tp.matched_layer(slug, words) == "words"


@pytest.mark.parametrize("words", [
    "Add a task: book the venue", "Create a task for Priya",
    "Draft the reply to Anil", "How is the pipeline looking?",
    "Move the Vantage deal to Proposal",
])
def test_the_new_words_load_no_pack_for_one_task_or_a_deal(words: str) -> None:
    """Mutation: add a bare "task" or "add" to the bulk words -> red."""
    assert tp.matched_layer("projects-bulk", words) is None
    assert tp.matched_layer("projects-plan", words) is None
