"""A correction is not a request (WS-67 OA-7g, shipped live 2026-10-26).

Decision D117 (``project-docs/work_plan.md`` §3), OA-7g. In the OA-5
measure, the member only corrected a date, and both shapes then made a
reply card and saved a draft (``docs/one_assistant_measure_2026-10-25.md``
§4). The owner chose to ship the rule live, not behind ``CHAT_CONSTRAINTS``.
So the orchestrator's persona holds it with every flag, every delegated task
head holds it, and the email assistant's own instructions hold it.

Mutations this file catches (R7):

* a sentence of the rule leaves ``_PULL_INSTRUCTIONS``, or a swap drops it;
* the rule moves behind a flag of the persona;
* a sentence of the rule leaves ``handoff._RULES``, so a delegated run never
  reads it;
* a sentence leaves the email assistant's built instructions, with or
  without narrowing, so the Email app's own chat never reads it;
* the rule moves under the task mark, so the tier question reads it;
* ``task_of`` finds a head by the whole rules block again, so a head that an
  earlier release built is not found after a deploy.
"""
from __future__ import annotations

import itertools
import re

import pytest
from acb_skills import handoff as h

from tests.unit._held_card_fakes import (  # noqa: F401 - OA-7b: owner and pack
    email_tool_mode,
    load_email_agent,
)

#: The four sentences of the rule. Each must reach each path whole.
SENTENCES = (
    "A correction is not a request.",
    "When the member only corrects a fact, use the corrected fact from now on"
    " and say what changed.",
    "Do not draft, send or change a record that the member did not ask for.",
    "When the member corrects an open draft, change that draft only.",
)


def _flat(text: str) -> str:
    """*text* with its line breaks and indents as single spaces, and with no
    bold marks (the email instructions put a title in bold)."""
    return re.sub(r"\s+", " ", text.replace("**", ""))


def _holds_the_rule(text: str) -> None:
    flat = _flat(text)
    for sentence in SENTENCES:
        assert flat.count(sentence) == 1, sentence


def _mod():
    import orchestrator.agents as pkg  # the package loads agents.py as _mod

    return pkg._mod


@pytest.mark.parametrize(
    ("company_search", "recency", "proposals", "profile", "constraints"),
    list(itertools.product((False, True), repeat=5)),
)
def test_the_persona_holds_the_rule_with_every_flag(
    company_search: bool, recency: bool, proposals: bool, profile: bool,
    constraints: bool,
) -> None:
    _holds_the_rule(_mod()._orchestrator_instructions(
        company_search, recency, proposals, profile=profile,
        constraints=constraints,
    ))


@pytest.mark.parametrize("turns", [None, [], ["the date is the 14th"]])
def test_every_task_head_holds_the_rule(turns: list[str] | None) -> None:
    task = h.task_with_member_words("Fix the date.", turns)
    _holds_the_rule(task.partition(h.TASK_MARK)[0])


@pytest.mark.parametrize("narrowing", [False, True])
def test_the_email_rail_holds_the_rule(narrowing: bool) -> None:
    """The Email app's own chat runs the email assistant with no task head."""
    _holds_the_rule(load_email_agent("ea_no_unasked_draft")._instructions(narrowing))


def test_the_tier_question_never_reads_the_rule() -> None:
    task = h.task_with_member_words("send it", ["the date is the 14th"])
    assert SENTENCES[0] not in h.task_of(task)
    assert h.task_of(task) == "send it"


def test_a_head_of_an_earlier_release_is_still_found() -> None:
    """A task built before a deploy has the older rules. ``task_of`` must
    still strip its head, or the tier question reads the whole head and a
    nested delegation adds a second one."""
    old = "## How to reply\n- an older rule\n" + h.TASK_MARK + "send it"
    assert h.task_of(old) == "send it"
    rebuilt = h.task_with_member_words(old, ["words"])
    assert rebuilt.count(h.TASK_MARK) == 1 and rebuilt.endswith("send it")
