"""A specialist's confirm card reaches the member through the one assistant.

D99 (``navigation_shell.md`` §7.1, NS-6). The member talks to
``orchestrator`` only, and a Projects write runs in projects-assistant, which
the orchestrator calls. §7.1 says: "The confirm cards must not change. A
specialist's card must reach the member through the general agent's run."
``_tool_injection.py`` says that each delegate's own ``request_confirmation``
gate still asks a human. This suite proves that round trip through the REAL
code, with only the model wire and the Projects HTTP client replaced:

1. ``run_agent_stream("orchestrator")`` runs as the web chat runs it
   (``shows_cards=True``), with a thread of its own.
2. The orchestrator calls projects-assistant, by ``call_agent`` and by its
   specialist tool. The sub-run is ``executor._run_sub_agent_streaming``,
   then the batch ``run_agent`` of the real projects-assistant factory.
3. projects-assistant calls its real ``comment`` tool. The tool reads the
   task, and its ``_confirm`` calls the real ``request_confirmation``.
4. The card is an event of the PARENT's stream, and the parent's thread owns
   it (``executor._pending_user_input.owner_of``). The relay copy of the card
   goes to the parent's thread too, which a reconnect replays.
5. An answer for another thread is refused, and the gate stays shut. The
   member's answer for the parent's thread (``executor.resolve_user_input``,
   which ``POST /agent/respond-input`` calls) opens the specialist's gate.
   APPROVE posts the comment. REJECT posts nothing.
6. A parent that shows no cards (``shows_cards=False``) passes that on: the
   sub-run's card is denied at once, and nothing is posted.

Why it holds: the sub-run of a MAF specialist runs in the parent's context,
so it inherits the parent's ``_active_run_queue`` and
``_stream_relay_thread_id``. ``request_confirmation`` puts the card on that
queue (its Path A), and ``_PendingUserInput`` records the owner from that
thread.

What this suite does NOT prove: the browser. Nobody has clicked Approve on a
card in the dock for a Projects write through ``orchestrator`` (§7.1). A
Copilot SDK specialist (``task-manager``, ``app-builder``) parks its native
``ask_user`` through ``_make_user_input_handler(<parent thread>)``, and this
suite does not run that path.

Mutations this suite catches (R7), each run red by hand on this branch:

* ``_run_sub_agent_streaming`` binds the sub-run's own relay thread
  (``_stream_relay_thread_id.set(run_id)``). The sub-run then owns the card,
  the member's answer for the parent's thread is refused, and nothing posts.
* ``_run_sub_agent_streaming`` hides the parent's queue from the sub-run
  (``_active_run_queue.set(None)`` before ``run_agent``). The card leaves by
  the relay only, never reaches the parent's live stream, and the run waits
  until the suite's time limit.

Run::

    uv run pytest tests/unit/test_delegated_confirmation_card.py -v
"""
from __future__ import annotations

import asyncio
from typing import Any

import pytest

pytest.importorskip("agent_framework", reason="MAF not installed")
pytest.importorskip("skill_projects", reason="skill-projects not installed")

from evals.routing import dataset as ds
from evals.routing import shell
from evals.routing.harness import (
    ORCHESTRATOR,
    ReplyModel,
    RoutingHarness,
    RunRecord,
    specialist_tool,
    text_turn,
    tool_turn,
)
from tests.unit._projects_agent_fakes import fake_gateway

PA = "projects-assistant"
TASK = ds.TASK_ID
WORDS = "Comment on this task: on it, I will send it today."
BODY = "On it, I will send it today."
#: The time limit of one run. A run that passes ends in a few seconds.
LIMIT = 45.0


def _gateway(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    """The Projects API: the task reads, a comment posts. Returns the calls."""

    def answer(call: dict) -> dict:
        if call["method"] == "GET":
            return {"id": TASK, "task_number": 12, "title": "Draft the launch email"}
        return {"id": "comment-1"}

    return fake_gateway(monkeypatch, answer, user=ds.MEMBER)


def _models(form: str) -> dict[str, ReplyModel]:
    call = (tool_turn("call_agent", {"agent_name": PA, "message": WORDS})
            if form == "call_agent" else tool_turn(specialist_tool(PA), {"task": WORDS}))
    return {
        ORCHESTRATOR: ReplyModel([call, text_turn("Projects has it.")]),
        PA: ReplyModel([tool_turn("comment", {"task_id": TASK, "body": BODY}),
                        text_turn("Done on #12.")]),
    }


def _run(monkeypatch: pytest.MonkeyPatch, form: str, answer: str, *,
         shows_cards: bool = True) -> tuple[RunRecord, dict[str, Any], RoutingHarness]:
    """One turn in Projects, with the member's *answer* to the card."""
    calls = _gateway(monkeypatch)
    seen: dict[str, Any] = {"calls": calls}

    def on_card(record: RunRecord, value: dict[str, Any]) -> str:
        from orchestrator import executor

        rid = str(value["request_id"])
        seen["calls_at_card"] = [(c["method"], c["path"]) for c in calls]
        # A viewer of another chat, or a forged thread, cannot answer it.
        seen["other_thread_answered"] = executor.resolve_user_input(
            rid, "APPROVE", thread_id="another-thread")
        seen["no_thread_answered"] = executor.resolve_user_input(
            rid, "APPROVE", thread_id=None)
        fut = executor._pending_user_input.get(rid)
        seen["gate_still_shut"] = fut is not None and not fut.done()
        return answer

    with RoutingHarness() as harness:
        harness.models = models = _models(form)
        record = asyncio.run(harness.drive(
            WORDS, shell.system_context("/projects", ds.case("P3").facts),
            member=ds.MEMBER, org=ds.ORG, on_card=on_card, timeout=LIMIT,
            shows_cards=shows_cards,
        ))
    seen["models"] = models
    return record, seen, harness


def _index(events: list[dict[str, Any]], pred: Any) -> int:
    for i, e in enumerate(events):
        if pred(e):
            return i
    return -1


def _custom(name: str) -> Any:
    return lambda e: e.get("type") == "CUSTOM" and e.get("name") == name


def _tool_results(model: ReplyModel) -> list[str]:
    return [str(m.get("content")) for b in model.bodies for m in b.get("messages") or []
            if m.get("role") == "tool"]


@pytest.mark.parametrize("form", ["call_agent", "tool"])
def test_the_card_is_the_parents_and_the_answer_opens_the_gate(monkeypatch, form) -> None:
    record, seen, harness = _run(monkeypatch, form, "APPROVE")
    assert record.error is None, record.error
    assert record.routed == [PA] and record.reached == [PA], (record.routed, record.reached)

    # One card, on the parent's own stream, owned by the parent's thread.
    (card,) = record.cards
    assert card["title"] == "Post this comment?", card
    assert card["owner"] == record.thread_id, card
    assert card["answered"] is True, card
    rid = card["request_id"]
    ev = record.events
    at = _index(ev, lambda e: _custom("confirmation_requested")(e)
                and e["value"]["request_id"] == rid)
    resolved = _index(ev, lambda e: _custom("confirmation_resolved")(e)
                      and e["value"]["request_id"] == rid)
    delegated = _index(ev, lambda e: e.get("type") == "TOOL_CALL_START")
    sub_text = _index(ev, lambda e: e.get("type") == "SUB_AGENT_TEXT_DELTA")
    finished = _index(ev, lambda e: e.get("type") == "RUN_FINISHED")
    assert -1 < delegated < at < resolved < sub_text < finished, (delegated, at, resolved,
                                                                  sub_text, finished)
    assert ev[resolved]["value"]["answer"] == "APPROVE"

    # The relay copy, which a reconnect replays, is the parent thread's too.
    relayed = [tid for tid, line in harness.relayed if rid in line]
    assert relayed and set(relayed) == {record.thread_id}, relayed

    # While the card waited: a read only, and no other thread could answer.
    assert seen["calls_at_card"] == [("GET", f"/projects/tasks/{TASK}")], seen["calls_at_card"]
    assert seen["other_thread_answered"] is False
    assert seen["no_thread_answered"] is False
    assert seen["gate_still_shut"] is True

    # The member's answer opened the specialist's own gate: the comment posts.
    posts = [c for c in seen["calls"] if c["method"] == "POST"]
    assert [(c["path"], c["json"]) for c in posts] == [
        (f"/projects/tasks/{TASK}/comments", {"body": BODY}),
    ], posts
    assert any("Commented on #12" in r for r in _tool_results(seen["models"][PA])), \
        _tool_results(seen["models"][PA])


def test_a_rejected_card_posts_nothing(monkeypatch) -> None:
    record, seen, _harness = _run(monkeypatch, "call_agent", "REJECT")
    (card,) = record.cards
    assert card["owner"] == record.thread_id and card["answered"] is True, card
    resolved = [e for e in record.events if _custom("confirmation_resolved")(e)]
    assert [e["value"]["answer"] for e in resolved] == ["REJECT"]
    assert [c for c in seen["calls"] if c["method"] != "GET"] == []
    assert any("Cancelled" in r for r in _tool_results(seen["models"][PA])), \
        _tool_results(seen["models"][PA])


def test_a_parent_that_shows_no_cards_passes_that_on(monkeypatch) -> None:
    """``shows_cards=False`` is the parent's switch, and the sub-run is inside
    it: the card is denied at once, no card event is sent, nothing posts."""
    record, seen, _harness = _run(monkeypatch, "call_agent", "APPROVE", shows_cards=False)
    assert record.error is None, record.error
    assert record.reached == [PA]
    assert record.cards == []
    assert not [e for e in record.events if _custom("confirmation_requested")(e)]
    assert [c for c in seen["calls"] if c["method"] != "GET"] == []
    assert any("Cancelled" in r for r in _tool_results(seen["models"][PA])), \
        _tool_results(seen["models"][PA])
