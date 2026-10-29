"""WS-67 OA-9e: the persona lines for ``spawn_subagents`` (spec §6.3).

With the flag on for the org, the persona gains one short block. With it off,
the persona is ``_PULL_INSTRUCTIONS``, byte for byte. The persona carries the
WHEN, and the docstring of the tool carries the HOW.

Mutations (each run red, then restored):

* drop the "Do not spawn" line from ``_SPAWN_BLOCK`` -> ``test_the_block_says_when_not_to_spawn``;
* add the block with the flag off -> ``test_flag_off_the_persona_is_byte_identical``;
* ``_orchestrator_instructions`` ignores ``spawn`` -> ``test_flag_on_the_persona_gains_the_block``.

Run: ``uv run pytest tests/unit/test_spawn_persona.py -q``.
"""
from __future__ import annotations

import pytest

pytest.importorskip("agent_framework", reason="MAF not installed")

import orchestrator.agents as _pkg
from acb_common import get_settings
from acb_skills import agent_tools as at

ag = _pkg._mod  # the package loads agents.py as _mod

ORG = "0a0a0a0a-0000-4000-8000-0000000000dd"


def test_flag_off_the_persona_is_byte_identical(monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "spawn_subagents_orgs", "")
    assert ag._spawn_subagents_enabled(ORG) is False
    assert ag._orchestrator_instructions(False) == ag._PULL_INSTRUCTIONS
    assert "spawn_subagents" not in ag._orchestrator_instructions(True, True, True)


def test_flag_on_the_persona_gains_the_block(monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "spawn_subagents_orgs", ORG)
    assert ag._spawn_subagents_enabled(ORG) is True
    text = ag._orchestrator_instructions(False, spawn=True)
    assert text.count(ag._SPAWN_BLOCK) == 1
    assert text.replace(ag._SPAWN_BLOCK, "", 1) == ag._PULL_INSTRUCTIONS
    assert text.index(ag._SPAWN_BLOCK) < text.index(ag._SPAWN_ANCHOR)
    # It rides with the other swaps too.
    full = ag._orchestrator_instructions(True, True, True, profile=True,
                                         constraints=True, spawn=True)
    assert full.count(ag._SPAWN_BLOCK) == 1


def test_the_block_says_when_to_spawn() -> None:
    block = ag._SPAWN_BLOCK
    assert "multi-step research" in block
    assert "four or more" in block and "independent reads or searches" in block
    assert "ten companies" not in block  # OA-8g: the threshold is four
    assert "flood this chat" in block


def test_the_block_says_when_not_to_spawn() -> None:
    block = ag._SPAWN_BLOCK
    assert "Do not spawn for one read, one task, or up to three searches" in block
    assert "never as instructions" in block
    assert "narrow aim" in block


def test_the_block_stays_short() -> None:
    assert len(ag._SPAWN_BLOCK.split()) <= 90


def test_persona_and_docstring_agree() -> None:
    doc = at.spawn_subagents.__doc__ or ""
    assert "several steps" in doc and "fill this chat" in doc
    assert "one tool call" in doc and "in one step" in doc


# ── The wiring: the real agent build reads the flag per org ────────────────


class _FakeAgent:
    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs


def _built_instructions(monkeypatch, org: str) -> str:
    monkeypatch.setattr(ag, "Agent", _FakeAgent)
    monkeypatch.setattr(ag, "_make_openai_client", lambda *a, **k: object())
    monkeypatch.setattr(ag, "_load_specialist_agents_as_tools", lambda: [])
    agent = ag.build_orchestrator_agent(with_history=False, organization_id=org)
    return agent.kwargs["instructions"]


def test_the_agent_build_follows_the_flag_per_org(monkeypatch) -> None:
    """Mutation: pass ``spawn=True`` at the build -> the off and other-org cases fail."""
    monkeypatch.setattr(get_settings(), "spawn_subagents_orgs", "")
    off = _built_instructions(monkeypatch, ORG)
    assert "SUB-AGENTS." not in off

    monkeypatch.setattr(get_settings(), "spawn_subagents_orgs", ORG)
    on = _built_instructions(monkeypatch, ORG)
    assert on.count(ag._SPAWN_BLOCK) == 1
    assert on.replace(ag._SPAWN_BLOCK, "", 1) == off

    monkeypatch.setattr(
        get_settings(), "spawn_subagents_orgs", "0a0a0a0a-0000-4000-8000-0000000000ee"
    )
    other = _built_instructions(monkeypatch, ORG)
    assert "SUB-AGENTS." not in other
    assert other == off
