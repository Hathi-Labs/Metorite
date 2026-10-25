"""Golden trajectory: the member's words survive the handoff (WS-67 OA-1, OA-2).

A synthetic replay of the shape of the Navy-order chat of 2026-10-25
(``specs/one_assistant_context.md`` §1). No customer data is in it. In turn 3
the member has said that the attachments are only references, and the
orchestrator's model writes a task that holds its own guess ("e.g. CB6/CB7").
The real orchestrator delegates to the real email-assistant factory through
``evals.routing.harness``. Only the model wire is scripted.

The email assistant's model must get, word for word, each of the member's
messages of the chat, the correction included, above the task, with the rule
that they outrank the task. It must also get the reply rules of OA-2.
"""
from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("agent_framework", reason="MAF not installed")

from acb_skills.handoff import HEAD_MARK

from evals.routing import dataset as ds
from evals.routing.harness import (
    ORCHESTRATOR,
    ReplyModel,
    RoutingHarness,
    specialist_tool,
    text_turn,
    tool_turn,
)

EA = "email-assistant"
PAGE = "## Where the member is now\nThe member is in the Email app (/email)."

ASK = "What parts does the Navy order from the ordnance depot ask for?"
GUESS = "The order names the parts CB6 and CB7."
CORRECTION = ("The attachments are only references. Read the parts from the "
              "body of the email, not from the file names.")


def _replay() -> RoutingHarness:
    history = [
        {"role": "user", "content": ASK},
        {"role": "assistant", "content": GUESS},
    ]
    with RoutingHarness() as harness:
        harness.models = {
            ORCHESTRATOR: ReplyModel([
                # The model's own guess in the task: the failure of turn 3.
                tool_turn(specialist_tool(EA),
                          {"task": "List the parts of the Navy order (e.g. CB6/CB7)."}),
                text_turn("The body of the order lists the parts."),
            ]),
            EA: ReplyModel([
                text_turn("The body asks for part P-12 (quoted: the Navy order email). "
                          "The files look like drawings (inferred)."),
            ]),
        }
        record = asyncio.run(harness.drive(
            CORRECTION, PAGE, member=ds.MEMBER, org=ds.ORG, history=history,
            timeout=60.0,
        ))
        assert record.error is None, record.error
        return harness


def test_the_correction_reaches_the_specialist_word_for_word() -> None:
    ea = _replay().models[EA]
    assert ea.saw(HEAD_MARK), "the head block is missing"
    assert ea.saw(ASK), "the member's first question is missing"
    assert ea.saw(CORRECTION), "the member's correction is missing"
    assert ea.saw("They outrank the task below"), "the precedence rule is missing"
    assert not ea.saw(GUESS), "the assistant's own earlier claim is not the member's words"


def test_the_specialist_gets_the_reply_rules() -> None:
    ea = _replay().models[EA]
    assert ea.saw("(inferred)")
    assert ea.saw("A file name is a label")
    assert ea.saw("never the subject of the request")
