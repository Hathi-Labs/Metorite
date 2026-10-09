"""The agents learn where each part of an answer goes.

Spec: ``project-docs/specs/projects_ai_chat.md`` §24 (owner, 2026-10-08).
The chat draws each read under its step, keeps a waiting ask in view, and
shows one answer card. An agent that draws a card for every read, or four
cards for one answer, undoes that.

The rule reaches an agent in ONE of two ways (§24.4, 2026-10-09):

* The injected UI directive ends with ``addendum.PLACEMENT_RULE``
  (``test_genui_proactive_directive.py``). Every agent that holds
  ``emit_generative_ui`` reads it. The Projects and email agents get the
  rule this way ONLY. A second copy in their instructions cost each model
  request its tokens and said the same thing twice.
* Three agents still carry the section "Where each part of your answer
  goes", word for word, until they drop it the same way.

Mutations this file catches (R7): delete the section from one of the three;
edit it in one of them only; put the section back into the Projects or the
email agent; drop the rule from the injected directive, or take
``emit_generative_ui`` away from the Projects or the email agent; bring back
"Prefer a card for a list"; drop the vocabulary rule from the Projects agent;
send the email list back to a card per read.
"""
from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
#: The agents that carry the section in their own instructions.
SECTION_AGENTS = ("agent-crm", "agent-whatsapp-assistant", "agent-orchestrator")
#: The agents that read the rule from the injected directive only.
DIRECTIVE_AGENTS = {
    "agent-projects": "projects-assistant",
    "agent-email-assistant": "email-assistant",
}
AGENTS = (*SECTION_AGENTS, *DIRECTIVE_AGENTS)
HEADING = "## Where each part of your answer goes"


def _text(agent: str) -> str:
    return (ROOT / "apps/agents" / agent / "instructions.md").read_text(encoding="utf-8").replace("\r\n", "\n")


def _section(agent: str) -> str:
    text = _text(agent)
    assert HEADING in text, f"{agent} has no placement section"
    body = text.split(HEADING, 1)[1]
    return re.split(r"\n## ", body, maxsplit=1)[0].strip()


def test_each_section_agent_carries_the_one_section_word_for_word() -> None:
    sections = {a: _section(a) for a in SECTION_AGENTS}
    first = sections[SECTION_AGENTS[0]]
    for agent, body in sections.items():
        assert body == first, f"{agent} differs from {SECTION_AGENTS[0]}"


def test_the_section_holds_the_four_rules() -> None:
    body = _section(SECTION_AGENTS[0])
    assert "Never draw a read's result again as" in body
    assert "Give a list the member asked for once." in body
    assert "Draw one card for an answer, at most." in body
    assert "Put it after your text." in body
    assert "fewer than six items as a\n  Markdown list, with no card" in body
    assert "A question to the member is not the answer card." in body


def test_no_agent_is_told_to_prefer_a_card_for_a_list() -> None:
    for agent in AGENTS:
        assert "Prefer a card for a list" not in _text(agent), agent


@pytest.mark.parametrize("agent", sorted(DIRECTIVE_AGENTS))
def test_a_directive_agent_does_not_repeat_the_rule(agent: str) -> None:
    """The rule comes from the directive, so the instructions must not
    carry a second copy of it."""
    from acb_skills.addendum import PLACEMENT_RULE

    text = _text(agent)
    assert HEADING not in text, f"{agent} carries the section again"
    assert PLACEMENT_RULE not in text, agent


def _built_instructions(agent_dir: str, monkeypatch: pytest.MonkeyPatch) -> str:
    """The instructions of *agent_dir* after the real injection, as a run sees them."""
    pytest.importorskip("agent_framework")
    from acb_common.settings import get_settings
    from orchestrator import _tool_injection as ti

    monkeypatch.setenv("OPENAI_API_KEY", "sk-placement-dummy")
    monkeypatch.setattr(ti, "_build_registry_block", lambda: "Registered agents: (stub)")
    # No database: the skill toggles and the app grants read pooled tables.
    import orchestrator.app_tools as app_tools
    monkeypatch.setattr(ti, "_load_disabled_skill_families", lambda name: frozenset())
    monkeypatch.setattr(app_tools, "load_app_action_tools", lambda name: [])
    get_settings.cache_clear()
    path = ROOT / "apps/agents" / agent_dir
    cfg: dict[str, Any] = json.loads((path / "config.json").read_text(encoding="utf-8"))
    spec = importlib.util.spec_from_file_location(
        "placement_" + agent_dir.replace("-", "_"), path / "agents.py",
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    agents = mod.build_agents()
    ti._apply_own_tool_scope(agents, cfg.get("own_tool_scope") or None)
    ti._inject_agent_tools(
        agents, tool_scope=cfg.get("tool_scope") or None,
        agent_name=DIRECTIVE_AGENTS[agent_dir], agent_config=cfg,
    )
    get_settings.cache_clear()
    return agents[0].default_options["instructions"]


@pytest.mark.parametrize("agent", sorted(DIRECTIVE_AGENTS))
def test_a_directive_agent_reads_the_rule_exactly_once(
    agent: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from acb_skills.addendum import PLACEMENT_RULE

    built = _built_instructions(agent, monkeypatch)
    assert built.count(PLACEMENT_RULE) == 1, agent


def test_the_projects_agent_creates_several_words_without_a_picker() -> None:
    """H-273: several new tags or types are ONE batch call, with no picker."""
    text = _text("agent-projects")
    assert "**Several new words in one turn.**" in text
    assert "For 2 or more new tags, call\n  `create_tags` once" in text
    assert "call `create_types` once" in text
    assert "Do not draw a picker first" in text
    assert "create them one after\n  another" not in text


def test_the_email_agent_shows_a_list_as_one_board() -> None:
    text = _text("agent-email-assistant")
    assert "call `present_email_groups` ONCE" in text
    assert "renders the results of `query_inbox` / `find_priority`\nas ONE interactive card" not in text
