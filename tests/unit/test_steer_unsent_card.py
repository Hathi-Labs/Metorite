"""A card that the member's chat message closed says so, and every steer gets an answer (task 3130).

Production, 2026-11-13: a member asked for an email reply, and the reply card
opened. The member did not press it. They typed two more messages into the
same run, and the first one closed the card (task 2494, ``close_on_steer``).
The stored answer met only the last message. It never said that the reply was
not sent, and the other message got no answer.

Now:

* The SERVER ends the answer with one plain line for each run where a chat
  message closed a write card that the member did not answer
  (``ask_tools.take_closed_card_line``, sent by ``executor._closed_card_events``
  just before each ``RUN_FINISHED``). The relay keeps it, so the stored
  message holds it. A card that the member approved or declined adds no line,
  and a later card of the same act takes the place of the closed one.
* A run that takes two or more steers asks, in the steer frame, for an
  answer to each message (``steer.steers_frame``). One steer is as before.
* The act stays reachable: after the card closes, "do it" draws the same card
  again from the history.

Each rule names the mutation that turns it red (R7):

1. Drop ``_card_closed_by_message`` from ``ask_tools._block_on`` ->
   ``test_a_closed_card_ends_the_answer_with_one_line`` and
   ``test_each_app_card_door_records_the_closed_card``.
2. Record an approved or declined card ->
   ``test_an_answered_card_adds_no_line``.
3. Drop ``_card_opened`` (a redraft leaves the old line) ->
   ``test_a_redraft_of_the_act_takes_the_place_of_the_closed_card``.
4. Make every later card take the place of a closed card ->
   ``test_a_card_of_another_act_leaves_the_line``.
5. Drop ``_closed_card_events`` before a ``RUN_FINISHED``, or put it before
   the CRM note -> ``test_the_executor_ends_each_finished_run_with_the_line``.
6. Drop the frame of two steers, in a live run or in a replay ->
   ``test_two_steers_in_one_call_get_the_frame``,
   ``test_a_second_steer_in_a_later_call_gets_the_frame`` and
   ``test_a_replay_of_two_steers_gets_the_frame``.
"""
from __future__ import annotations

import ast
import asyncio
import json
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from acb_skills import ask_tools
from agent_framework import Agent, AgentSession, Content, Message
from orchestrator import executor, steer

from tests.unit import test_steer_user_turn as base

ROOT = Path(__file__).resolve().parents[2]
THREAD = base.THREAD
MEMBER = base.MEMBER
GRAPH = "also draw the sales graph for this quarter"

#: The exact line for one closed card (the brief of task 3130).
ONE_LINE = ("Not done: reply to puneet@acme.test. The card closed when you sent a new "
            "message. Say 'do it' to get it back.")
#: The exact frame of a run that took two steers.
TWO_STEERS = ("[The member added 2 messages to this reply. Answer each one. "
              "If you leave one out, name it and say why.]")


@pytest.fixture(autouse=True)
def _clean():
    steer._PENDING_GUIDANCE.clear()
    steer._CLOSED.clear()
    yield
    steer._PENDING_GUIDANCE.clear()
    steer._CLOSED.clear()


async def _card(title: str, then: str, *, detail: str = "d", **kw: Any) -> tuple[Any, dict]:
    """Show one card that closes on a steer, and end it with *then*: "steer",
    "APPROVE" or "REJECT". Returns the card's answer and its event."""
    queue: asyncio.Queue = asyncio.Queue()
    base._bind_run(queue)
    task = asyncio.ensure_future(ask_tools.request_confirmation(
        title=title, detail=detail, close_on_steer=True, **kw))
    card = await asyncio.wait_for(queue.get(), timeout=5)
    assert card["name"] == "confirmation_requested"
    if then == "steer":
        assert steer.buffer_guidance(THREAD, MEMBER, GRAPH)
    else:
        base._answer(card, then)
    closed = await asyncio.wait_for(queue.get(), timeout=5)
    assert closed["name"] == "confirmation_resolved"
    out = await asyncio.wait_for(task, timeout=5)
    # The model reads the note, so it is not new for the next card.
    steer.take_steer_turns(THREAD)
    return out, card


def _in_run(body: Any) -> str:
    """Run *body* inside one run's record of closed cards. Returns the line."""

    async def run() -> str:
        token = ask_tools.open_closed_cards_for_run()
        try:
            await body()
            return ask_tools.take_closed_card_line()
        finally:
            ask_tools.release_closed_cards(token)

    return asyncio.run(run())


# ── 1. The line of a closed card ─────────────────────────────────────────────


def test_a_closed_card_ends_the_answer_with_one_line():
    seen: dict[str, Any] = {}

    async def body() -> None:
        seen["answer"], _ = await _card("Reply to puneet@acme.test?", "steer")
        seen["first"] = ask_tools.take_closed_card_line()
        seen["again"] = ask_tools.take_closed_card_line()

    assert _in_run(body) == ""
    assert ask_tools.is_revised(seen["answer"])
    assert seen["first"] == ONE_LINE
    assert seen["again"] == "", "the line comes once"


@pytest.mark.parametrize("answer", ["APPROVE", "REJECT"])
def test_an_answered_card_adds_no_line(answer: str):
    async def body() -> None:
        await _card("Reply to puneet@acme.test?", answer)

    assert _in_run(body) == ""


def test_a_redraft_of_the_act_takes_the_place_of_the_closed_card():
    """"Add Kim to it": the model draws the reply again with Kim, and the
    member sends it. The act is done, so no line says that it is not."""

    async def body() -> None:
        await _card("Reply to puneet@acme.test?", "steer")
        await _card("Reply to puneet@acme.test, kim@acme.test?", "APPROVE")

    assert _in_run(body) == ""


def test_a_redraft_that_closes_again_keeps_one_line():
    async def body() -> None:
        await _card("Reply to puneet@acme.test?", "steer")
        await _card("Reply to puneet@acme.test?", "steer")

    assert _in_run(body) == ONE_LINE


def test_a_card_of_another_act_leaves_the_line():
    """The member approved a task card after the reply card closed. The
    reply is still not sent, so the line names it."""

    async def body() -> None:
        await _card("Reply to puneet@acme.test?", "steer")
        await _card("Create this task?", "APPROVE")
        await _card("Reply to raj@acme.test?", "APPROVE")

    assert _in_run(body) == ONE_LINE


def test_two_closed_cards_are_named_in_one_line():
    async def body() -> None:
        await _card("Reply to puneet@acme.test?", "steer")
        await _card("Create «Demo» with 2 tasks?", "steer")

    assert _in_run(body) == (
        "Not done: reply to puneet@acme.test, and create Demo with 2 tasks. The cards "
        "closed when you sent a new message. Say 'do it' to get them back.")


def test_a_card_outside_a_run_record_records_nothing():
    """No record (a batch run, WhatsApp): the card works as before, and no
    line is made."""

    async def run() -> tuple[Any, str]:
        out, _ = await _card("Reply to puneet@acme.test?", "steer")
        return out, ask_tools.take_closed_card_line()

    out, line = asyncio.run(run())
    assert ask_tools.is_revised(out) and line == ""


@pytest.mark.parametrize(("title", "act"), [
    ("Reply to puneet@acme.test?", "reply to puneet@acme.test"),
    ("Send this mail?", "send this mail"),
    ("Email to kim@acme.test", "email to kim@acme.test"),
    ("Create «Demo» with 3 tasks?", "create Demo with 3 tasks"),
    ("CRM lead for «Veena»?", "CRM lead for Veena"),
    ("  ", "the act on the card"),
])
def test_the_act_comes_from_the_card_title(title: str, act: str):
    assert ask_tools.card_act(title) == act


def test_the_outcome_word_is_the_one_of_hrd_7():
    """HRD-7 grows one vocabulary of card outcomes from this word."""
    assert ask_tools.CLOSED_BY_YOUR_MESSAGE == "closed by your message"
    spec = (ROOT / "project-docs/specs/chat_failure_modes.md").read_text(encoding="utf-8")
    assert "closed by your message" in spec


# ── 2. Every app's card door records the closed card ────────────────────────


def test_each_app_card_door_records_the_closed_card(monkeypatch):
    """Email (the held send card with rows), Projects and CRM all close in
    ``request_confirmation``. Each one gives its line."""
    from skill_projects import writes
    from skill_projects.refusals import refusals_as_text

    from tests.unit._crm_agent_fakes import load_agent_module

    crm = load_agent_module("crm_assistant_agent_3130")

    async def _post(path: str, payload: Any = None) -> Any:
        raise AssertionError("a closed card writes nothing")

    async def _block(payload: Any, note: str = "") -> str:
        return "\n".join(f"{k}: {v}" for k, v in payload.items())

    monkeypatch.setattr(crm, "_post", _post)
    monkeypatch.setattr(crm, "_held_fields_block", _block)

    async def projects_write() -> str:
        """A Projects write."""
        ok = await writes._confirm(title="Create this task?", detail="«Demo»",
                                   context="title: «Demo»")
        return "Made." if ok else writes.CANCELLED

    async def close_one(start: Any) -> Any:
        queue: asyncio.Queue = asyncio.Queue()
        base._bind_run(queue)
        task = asyncio.ensure_future(start())
        await asyncio.wait_for(queue.get(), timeout=5)
        steer.buffer_guidance(THREAD, MEMBER, GRAPH)
        await asyncio.wait_for(queue.get(), timeout=5)
        out = await asyncio.wait_for(task, timeout=5)
        steer.take_steer_turns(THREAD)
        return out

    lines: dict[str, str] = {}

    async def run() -> None:
        for name, start in (
            ("email", lambda: ask_tools.request_confirmation(
                title="Send this mail?", detail="To puneet@acme.test",
                rows=[{"id": "r1", "label": "puneet@acme.test", "checked": True}],
                request_id="a" * 32, with_answer=True, close_on_steer=True)),
            ("projects", refusals_as_text(projects_write)),
            ("crm", lambda: crm.create_lead("Veena Rao", email="veena@acme.test")),
        ):
            token = ask_tools.open_closed_cards_for_run()
            try:
                out = await close_one(start)
                lines[name] = ask_tools.take_closed_card_line()
            finally:
                ask_tools.release_closed_cards(token)
            if name == "projects":
                assert out == writes.CARD_REVISED

    asyncio.run(run())
    tail = "The card closed when you sent a new message. Say 'do it' to get it back."
    assert lines == {
        "email": f"Not done: send this mail. {tail}",
        "projects": f"Not done: create this task. {tail}",
        "crm": f"Not done: create this CRM lead. {tail}",
    }


# ── 3. The executor sends the line last, and the stored message holds it ────


def test_the_executor_ends_each_finished_run_with_the_line():
    """A source fence: each RUN_FINISHED of ``run_agent_stream`` follows the
    line step, and the line step follows the CRM note. The run opens one
    record and releases it in the same finally."""
    src = (ROOT / "apps/services/orchestrator/orchestrator/executor.py").read_text(
        encoding="utf-8")
    body = src[src.index("async def run_agent_stream("):src.index("def _classify_error(")]
    finishes = [m.start() for m in re.finditer(r'"type": "RUN_FINISHED"', body)]
    assert len(finishes) == 3
    for at in finishes:
        before = body[max(0, at - 700):at]
        assert "_closed_card_events(run_id)" in before
        assert before.rindex("_closed_card_events(run_id)") > before.rindex("_crm_note_events()")
    assert body.count("_closed_cards_token = open_closed_cards_for_run()") == 1
    assert body.count("release_closed_cards(_closed_cards_token)") == 1


def test_the_stored_message_holds_the_line():
    """The relay keeps every ``_sse`` event, and the fold of those events is
    the stored message. A reload shows the line."""
    from gateway.chat_fold import fold_run_events

    seen: dict[str, Any] = {}

    async def body() -> None:
        await _card("Reply to puneet@acme.test?", "steer")
        seen["events"] = executor._closed_card_events("run-3130")
        seen["again"] = executor._closed_card_events("run-3130")

    _in_run(body)
    events = seen["events"]
    assert [e["type"] for e in events] == [
        "TEXT_MESSAGE_START", "TEXT_MESSAGE_CONTENT", "TEXT_MESSAGE_END"]
    assert events[1]["delta"] == f"\n\n{ONE_LINE}", "one delta, no space after the line"
    assert seen["again"] == [], "the line comes once"
    answer = list(executor._text_message_events("Here is the sales graph."))
    folded = fold_run_events([
        {"type": "RUN_STARTED", "runId": "run-3130", "threadId": THREAD},
        *answer, *events,
        {"type": "RUN_FINISHED", "runId": "run-3130", "threadId": THREAD},
    ])
    assert folded is not None
    # The fold joins the segments with a blank line, as for the CRM note.
    assert folded["content"].startswith("Here is the sales graph.\n\n")
    assert folded["content"].endswith(f"\n\n{ONE_LINE}"), "the line is the end of the message"
    segments = folded["agent_state"]["segments"]
    assert [s["text"].strip() for s in segments] == ["Here is the sales graph.", ONE_LINE]


def test_a_run_with_no_closed_card_sends_no_line():
    async def body() -> None:
        assert executor._closed_card_events("r") == []

    _in_run(body)
    assert executor._closed_card_events("r") == [], "no record, no line"


# ── 4. Every steer gets an answer ───────────────────────────────────────────


def _middleware_call(mw: steer.SteerTurns, messages: list[Any]) -> list[Any]:
    ctx = SimpleNamespace(messages=list(messages))
    seen: list[list[Any]] = []

    async def call_next() -> None:
        seen.append(list(ctx.messages))

    asyncio.run(mw.process(ctx, call_next))
    return seen[0]


def test_the_frame_text_is_pinned():
    assert steer.steers_frame(2) == TWO_STEERS


def test_one_steer_gets_no_frame():
    steer.buffer_guidance(THREAD, MEMBER, "make it shorter")
    sent = _middleware_call(steer.SteerTurns(THREAD), [Message("user", ["Reply to Puneet"])])
    assert base._users(sent)[-1] == f"[steer from {MEMBER}] make it shorter"


def test_two_steers_in_one_call_get_the_frame():
    steer.buffer_guidance(THREAD, MEMBER, "make it shorter")
    steer.buffer_guidance(THREAD, MEMBER, GRAPH)
    sent = _middleware_call(steer.SteerTurns(THREAD), [Message("user", ["Reply to Puneet"])])
    assert base._users(sent) == [
        "Reply to Puneet",
        f"[steer from {MEMBER}] make it shorter",
        f"[steer from {MEMBER}] {GRAPH}\n{TWO_STEERS}",
    ]


def test_a_second_steer_in_a_later_call_gets_the_frame():
    mw = steer.SteerTurns(THREAD)
    tool = Message("tool", [Content.from_function_result("c1", result="The card is closed.")])
    steer.buffer_guidance(THREAD, MEMBER, "make it shorter")
    first = _middleware_call(mw, [Message("user", ["Reply to Puneet"]), tool])
    assert base._users(first)[-1] == f"[steer from {MEMBER}] make it shorter"
    steer.buffer_guidance(THREAD, MEMBER, GRAPH)
    later = [Message("user", ["Reply to Puneet"]), tool,
             Message("assistant", ["Drawing the graph."])]
    second = _middleware_call(mw, later)
    assert base._users(second) == [
        "Reply to Puneet",
        f"[steer from {MEMBER}] make it shorter",
        f"[steer from {MEMBER}] {GRAPH}\n{TWO_STEERS}",
    ], "the first turn stays as it was, and the newest turn carries the frame"


def test_a_replay_of_two_steers_gets_the_frame():
    one = [{"author": MEMBER, "text": "make it shorter"}]
    two = [*one, {"author": MEMBER, "text": GRAPH}]
    assert steer.format_replayed(one) == f"[steer from {MEMBER}] make it shorter"
    assert steer.format_replayed(two) == (
        f"[steer from {MEMBER}] make it shorter\n[steer from {MEMBER}] {GRAPH}\n{TWO_STEERS}")


# ── 5. The act stays reachable: "do it" draws the same card again ───────────


@pytest.mark.parametrize("stream", [False, True], ids=["batch", "stream"])
def test_do_it_draws_the_same_card_again(stream: bool):
    """A card, a steer about something else, then "do it". The next turn
    reads the act from the history and draws the same card on a new id."""
    from orchestrator._native_run_context import agent_for_run
    from orchestrator.native_session_store import SessionHistoryProvider
    from skill_projects import writes
    from skill_projects.refusals import refusals_as_text

    written: list[list[str]] = []

    async def create_task(title: str, assignees: list[str]) -> str:
        """Create a task (the Projects card door, as every Projects write)."""
        ok = await writes._confirm(
            title="Create this task?", detail=f"{title} · {', '.join(assignees)}",
            context=f"title: {title}\nassignees: {', '.join(assignees)}")
        if not ok:
            return writes.CANCELLED
        written.append(list(assignees))
        return f"Created task {title} for {', '.join(assignees)}."

    create_task = refusals_as_text(create_task)

    def again(messages: list[Any]) -> tuple[Any, ...]:
        # "do it": the model takes the act of the last call in the history.
        assert base._users(messages)[-1] == "do it"
        calls = [c for m in messages for c in m.contents
                 if getattr(c, "type", None) == "function_call" and c.name == "create_task"]
        args = calls[-1].arguments
        return ("call", "create_task", json.loads(args) if isinstance(args, str) else args)

    client = base.ScriptedClient([
        ("call", "create_task", {"title": "Demo", "assignees": ["Kiran"]}),
        ("text", "Here is the sales graph."),
        again,
        ("text", "Created the task for Kiran."),
    ])
    view = base._native_view(Agent(client=client, name="projects", tools=[create_task]))
    history = SessionHistoryProvider()
    session = AgentSession()
    cards: list[dict] = []

    async def turn(text: str, end: str) -> str:
        token = ask_tools.open_closed_cards_for_run()
        try:
            queue: asyncio.Queue = asyncio.Queue()
            base._bind_run(queue)
            run_view = agent_for_run(view, None, history=history)

            async def drive() -> None:
                if stream:
                    async for _u in run_view.run(text, stream=True, session=session):
                        pass
                else:
                    await run_view.run(text, session=session)

            task = asyncio.ensure_future(drive())
            card = await asyncio.wait_for(queue.get(), timeout=5)
            cards.append(card)
            if end == "steer":
                assert steer.buffer_guidance(THREAD, MEMBER, GRAPH, signal_id="s1", typed=True)
            else:
                base._answer(card, end)
            closed = await asyncio.wait_for(queue.get(), timeout=5)
            assert closed["name"] == "confirmation_resolved"
            await asyncio.wait_for(task, timeout=5)
            return ask_tools.take_closed_card_line()
        finally:
            ask_tools.release_closed_cards(token)

    first_line = asyncio.run(turn("Make a Demo task for Kiran", "steer"))
    assert written == [], "the closed card wrote nothing"
    assert first_line == ("Not done: create this task. The card closed when you sent a new "
                          "message. Say 'do it' to get it back.")

    second_line = asyncio.run(turn("do it", "APPROVE"))
    assert second_line == "", "the act is done now"
    first, second = (c["value"] for c in cards)
    assert second["request_id"] != first["request_id"], "a new card"
    assert (second["title"], second["detail"], second["context"]) == (
        first["title"], first["detail"], first["context"]), "the same act"
    assert written == [["Kiran"]], "the act happened once, as the first card drew it"
    do_it = client.requests[2]
    assert writes.CARD_REVISED in base._tools(do_it), "the history says the card closed"


def test_this_file_pins_each_rule_by_name():
    """Each test that the docstring names exists, so a rename cannot hide a rule."""
    tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))
    names = {n.name for n in tree.body if isinstance(n, ast.FunctionDef)}
    named = set(re.findall(r"``(test_[a-z0-9_]+)``", ast.get_docstring(tree) or ""))
    assert named and named <= names, named - names
