"""Two writes in ONE model step raise two cards that wait together (task #1804).

Decision D168.1 says that related changes go on one card. Decision D173 groups
the approval cards that wait together into ONE card with tabs
(``workbench/control_plane/src/lib/cardTabs.ts``). For that grouping to work,
the model must send writes that do not depend on each other in ONE step, and
the backend must hold both gates open at the same time. This suite proves the
backend half through the REAL executor, with only the model wire and the
Projects HTTP client replaced:

1. The model sends two ``comment`` calls in one response.
2. The run raises two ``confirmation_requested`` events with two request ids.
3. The suite holds the first card and answers none until the second one
   arrives. A backend that runs the two calls one after the other never raises
   the second card, so the run hits the time limit and the suite fails.
4. Both gates are open at the same moment, and both belong to the chat thread.
   This is the input that D173 groups.
5. The answers open both gates. Both comments post.

Two paths: the Projects assistant run as the chat's own agent, and a hand-off
from the orchestrator by ``call_agent``.

Mutation (R7): make ``request_confirmation`` take a module-level lock for the
whole wait. The second card never appears, and both tests fail on the time
limit.

Run: ``uv run pytest tests/unit/test_parallel_cards_wait_together.py -v``
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

pytest.importorskip("agent_framework", reason="MAF not installed")
pytest.importorskip("skill_projects", reason="skill-projects not installed")

from evals.routing import dataset as ds
from evals.routing import harness as hx
from evals.routing import shell
from evals.routing.harness import (
    ORCHESTRATOR,
    ReplyModel,
    RoutingHarness,
    RunRecord,
    text_turn,
    tool_turn,
)
from tests.unit._projects_agent_fakes import fake_gateway

PA = "projects-assistant"
TASK = ds.TASK_ID
WORDS = "Comment twice on this task."
BODIES = ("First note.", "Second note.")
#: The time limit of one run. A run that passes ends in a few seconds.
LIMIT = 45.0


def _two_writes_turn() -> list[dict[str, Any]]:
    """ONE model response that holds two ``comment`` calls."""
    calls = [{"index": i, "id": f"call_{i + 1}", "type": "function",
              "function": {"name": "comment",
                           "arguments": json.dumps({"task_id": TASK, "body": body})}}
             for i, body in enumerate(BODIES)]
    return [{"role": "assistant", "tool_calls": calls}, {"finish_reason": "tool_calls"}]


def _drive(monkeypatch: pytest.MonkeyPatch, *, handoff: bool) -> tuple[RunRecord, dict[str, Any]]:
    calls: list[dict] = []

    def answer(call: dict) -> dict:
        calls.append(call)
        if call["method"] == "GET":
            return {"id": TASK, "task_number": 12, "title": "Draft the launch email"}
        return {"id": "comment-1"}

    fake_gateway(monkeypatch, answer, user=ds.MEMBER)
    seen: dict[str, Any] = {"calls": calls, "held": [], "open_together": None}
    original = hx._answer_card

    def hold_then_answer(record: RunRecord, value: dict[str, Any], reply: str) -> bool:
        """Keep each card open until two cards wait. Then answer both."""
        from orchestrator import executor

        seen["held"].append((record, value, reply))
        if len(seen["held"]) < 2:
            return True
        rids = [str(v["request_id"]) for _r, v, _a in seen["held"]]
        futures = [executor._pending_user_input.get(rid) for rid in rids]
        seen["open_together"] = [f is not None and not f.done() for f in futures]
        seen["owners"] = [executor._pending_user_input.owner_of(rid) for rid in rids]
        return all([original(r, v, a) for r, v, a in seen["held"]])

    monkeypatch.setattr(hx, "_answer_card", hold_then_answer)

    pa_model = ReplyModel([_two_writes_turn(), text_turn("Both comments are posted.")])
    with RoutingHarness() as harness:
        if handoff:
            harness.models = {
                ORCHESTRATOR: ReplyModel([
                    tool_turn("call_agent", {"agent_name": PA, "message": WORDS}),
                    text_turn("Projects has it.")]),
                PA: pa_model,
            }
            record = asyncio.run(harness.drive(
                WORDS, shell.system_context("/projects", ds.case("P3").facts),
                member=ds.MEMBER, org=ds.ORG, on_card=lambda _r, _v: "APPROVE",
                timeout=LIMIT))
        else:
            harness.models = {PA: pa_model}
            record = asyncio.run(harness.drive(
                WORDS, shell.system_context("/projects", ds.case("P3").facts),
                member=ds.MEMBER, org=ds.ORG, on_card=lambda _r, _v: "APPROVE",
                timeout=LIMIT, agent=PA))
    return record, seen


@pytest.mark.parametrize("handoff", [False, True], ids=["chat_agent", "hand_off"])
def test_two_writes_of_one_step_raise_two_cards_that_wait_together(
    monkeypatch, handoff: bool,
) -> None:
    record, seen = _drive(monkeypatch, handoff=handoff)
    assert record.error is None, record.error

    # Two different cards, both open at the same moment, both the chat's own.
    assert seen["open_together"] == [True, True], seen["open_together"]
    ids = {str(v["request_id"]) for _r, v, _a in seen["held"]}
    assert len(ids) == 2, ids
    assert {v["title"] for _r, v, _a in seen["held"]} == {"Post this comment?"}
    assert set(seen["owners"]) == {record.thread_id}, seen["owners"]
    assert len(record.cards) == 2 and all(c["answered"] for c in record.cards), record.cards

    # Neither comment posted before the cards were answered, and both after.
    posts = [c for c in seen["calls"] if c["method"] == "POST"]
    assert sorted(c["json"]["body"] for c in posts) == sorted(BODIES), posts
