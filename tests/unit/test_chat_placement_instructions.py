"""The agents learn where each part of an answer goes.

Spec: ``project-docs/specs/projects_ai_chat.md`` §24 (owner, 2026-10-08).
The chat draws each read under its step, keeps a waiting ask in view, and
shows one answer card. An agent that draws a card for every read, or four
cards for one answer, undoes that. So five agents carry ONE section, word for
word, and the injected UI directive carries the same rule
(``test_genui_proactive_directive.py``).

Mutations this file catches (R7): delete the section from one agent; edit it
in one agent only; bring back "Prefer a card for a list"; drop the
vocabulary rule from the Projects agent; send the email list back to a card
per read.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
AGENTS = ("agent-projects", "agent-email-assistant", "agent-crm",
          "agent-whatsapp-assistant", "agent-orchestrator")
HEADING = "## Where each part of your answer goes"


def _text(agent: str) -> str:
    return (ROOT / "apps/agents" / agent / "instructions.md").read_text(encoding="utf-8").replace("\r\n", "\n")


def _section(agent: str) -> str:
    text = _text(agent)
    assert HEADING in text, f"{agent} has no placement section"
    body = text.split(HEADING, 1)[1]
    return re.split(r"\n## ", body, maxsplit=1)[0].strip()


def test_each_agent_carries_the_one_section_word_for_word() -> None:
    sections = {a: _section(a) for a in AGENTS}
    first = sections[AGENTS[0]]
    for agent, body in sections.items():
        assert body == first, f"{agent} differs from {AGENTS[0]}"


def test_the_section_holds_the_four_rules() -> None:
    body = _section(AGENTS[0])
    assert "Never draw a read's result again as" in body
    assert "Draw one card for an answer, at most." in body
    assert "Put it after your text." in body
    assert "fewer than six items as a\n  Markdown list, with no card" in body
    assert "A question to the member is not the answer card." in body


def test_no_agent_is_told_to_prefer_a_card_for_a_list() -> None:
    for agent in AGENTS:
        assert "Prefer a card for a list" not in _text(agent), agent


def test_the_projects_agent_creates_several_words_without_a_picker() -> None:
    text = _text("agent-projects")
    assert "**Several new words in one turn.**" in text
    assert "do not draw a picker\n  first" in text
    assert "call the next create only after the last receipt" in text


def test_the_email_agent_shows_a_list_as_one_board() -> None:
    text = _text("agent-email-assistant")
    assert "call `present_email_groups` ONCE" in text
    assert "renders the results of `query_inbox` / `find_priority`\nas ONE interactive card" not in text
