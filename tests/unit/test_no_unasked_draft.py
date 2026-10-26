"""A correction is not a request (WS-67 OA-7g, shipped live 2026-10-26).

Spec ``project-docs/specs/one_assistant_context.md`` §4, D117. In the OA-5
measure, the member only corrected a date, and both shapes then made a
reply card and saved a draft (``docs/one_assistant_measure_2026-10-25.md``
§4). The owner chose to ship the rule live, not behind ``CHAT_CONSTRAINTS``.
So the orchestrator's persona holds it with every flag, and every delegated
task head holds it too.

Mutations this file catches (R7):

* the rule leaves ``_PULL_INSTRUCTIONS``, or a swap drops it;
* the rule moves behind a flag of the persona;
* the rule leaves ``handoff._RULES``, so a delegated run never reads it;
* the rule leaves the email assistant's ``instructions.md``, so the Email
  app's own chat never reads it;
* the rule moves under the task mark, so the tier question reads it.
"""
from __future__ import annotations

import itertools

import pytest
from acb_skills import handoff as h

RULE = "A correction is not a request."


def _mod():
    import orchestrator.agents as pkg  # the package loads agents.py as _mod

    return pkg._mod


@pytest.mark.parametrize(
    ("company_search", "profile", "constraints"),
    list(itertools.product((False, True), repeat=3)),
)
def test_the_persona_holds_the_rule_with_every_flag(
    company_search: bool, profile: bool, constraints: bool,
) -> None:
    persona = _mod()._orchestrator_instructions(
        company_search, profile=profile, constraints=constraints,
    )
    assert persona.count(RULE) == 1
    assert "Draft, save, send or change nothing until" in persona


@pytest.mark.parametrize("turns", [None, [], ["the date is the 14th"]])
def test_every_task_head_holds_the_rule(turns: list[str] | None) -> None:
    task = h.task_with_member_words("Fix the date.", turns)
    assert task.count(RULE) == 1


def test_the_email_rail_holds_the_rule() -> None:
    """The Email app's own chat runs the email assistant with no task head."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    text = (root / "apps/agents/agent-email-assistant/instructions.md").read_text(
        encoding="utf-8")
    assert text.count("**A correction is not a request**") == 1


def test_the_tier_question_never_reads_the_rule() -> None:
    task = h.task_with_member_words("send it", ["the date is the 14th"])
    assert RULE not in h.task_of(task)
    assert h.task_of(task) == "send it"
