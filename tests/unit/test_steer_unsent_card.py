"""A card that a chat message closed says so, and every steer gets an answer (task 3130).

Production, 2026-11-13: a member asked for an email reply, and the reply card
opened. The member did not press it. They typed two more messages into the
same run, and the first one closed the card (task 2494, ``close_on_steer``).
The stored answer met only the last message. It never said that the reply was
not sent, and the other message got no answer.

Now:

* The SERVER ends the answer with one plain line for each run where a chat
  message closed a write card that nobody answered
  (``ask_tools.take_closed_card_line``). The executor takes the line before
  it saves the native session, so the session holds it, and sends it just
  before each ``RUN_FINISHED``. A parked run sends it too. The relay keeps
  it, so the stored message holds it.
* A card that was approved or declined adds no line. A later card takes the
  place of a closed card ONLY when both have the same act key: the tool
  that drew the card, and the arguments that name its targets
  (``ask_tools.card_call``, opened at the injection seam). No key, no change.
* The act in the line is inert text in a « » fence: no markdown, no link.
* A run that takes two or more steers asks, in the steer frame, for an
  answer to each message (``steer.steers_frame``). One steer is as before.
  The words hold in a shared room.
* "do it" draws the same card again from the session history.

Each rule names the mutation that turns it red (R7):

1. Drop ``_card_closed_by_message`` from ``ask_tools._block_on`` ->
   ``test_a_closed_card_ends_the_answer_with_one_line`` and
   ``test_each_app_card_door_records_the_closed_card``.
2. Record an approved or declined card ->
   ``test_an_answered_card_adds_no_line``.
3. Drop ``_card_opened`` (a redraft leaves the old line) ->
   ``test_a_redraft_of_the_same_act_takes_the_place_of_the_closed_card``.
4. Match by title, or let a card with another key or with no key take the
   place of a closed card -> ``test_a_card_of_another_act_leaves_the_line``
   and ``test_a_card_with_no_key_takes_the_place_of_none``.
4b. Drop the identity arguments of a call with no object id, or read a
    place id as an object id -> ``test_a_new_object_keys_on_its_identity``,
    ``test_a_place_id_is_not_an_object_id`` and
    ``test_two_new_mails_to_one_person_are_two_acts``.
4c. Let a batch card take the place of a closed card that its items do not
    hold, or split the families ->
    ``test_a_batch_card_takes_the_place_of_a_single_only_by_its_items``.
5. Drop ``_card_call`` from the gate wrapper, or give a delegation tool a
   key -> ``test_the_injection_seam_binds_the_act_key``.
6. Let a markdown or link character, or a fence mark, reach the line ->
   ``test_the_act_in_the_line_is_inert``.
7. Take the line after the session save, or drop the closing line from it
   -> ``test_the_executor_ends_each_finished_run_with_the_line`` and
   ``test_the_native_session_holds_the_line``.
8. One shared record for every run, or a release that does nothing ->
   ``test_each_run_has_its_own_record_and_release_leaves_none``.
9. Drop the line from a parked run -> ``test_a_parked_run_sends_the_line``.
10. Drop the frame of two steers, in a live run or in a replay ->
    ``test_two_steers_in_one_call_get_the_frame``,
    ``test_a_second_steer_in_a_later_call_gets_the_frame`` and
    ``test_a_replay_of_two_steers_gets_the_frame``.
"""
# The fixtures imported by name below are redefined as test arguments (F811).
# ruff: noqa: F811
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
from orchestrator import executor, steer, stream_relay

from tests.unit import test_steer_user_turn as base
from tests.unit.test_chat_deploy_recovery import fake_redis, liveness  # noqa: F401
from tests.unit.test_pending_ask_flow import ask_db, flag_on  # noqa: F401

ROOT = Path(__file__).resolve().parents[2]
THREAD = base.THREAD
MEMBER = base.MEMBER
GRAPH = "also draw the sales graph for this quarter"

#: The words after the act, for one closed card and for two or more.
TAIL_ONE = "The card closed when a new message came in. Say 'do it' to get it back."
TAIL_MANY = "The cards closed when a new message came in. Say 'do it' to get them back."
#: The exact line for one closed reply card.
ONE_LINE = f"Not done: «reply to puneet@acme.test». {TAIL_ONE}"
#: The exact frame of a run that took two steers.
TWO_STEERS = ("[2 new messages came in during this reply. Answer each one. "
              "If you leave one out, name it and say why.]")


@pytest.fixture(autouse=True)
def _clean():
    steer._PENDING_GUIDANCE.clear()
    steer._CLOSED.clear()
    yield
    steer._PENDING_GUIDANCE.clear()
    steer._CLOSED.clear()


# ── The tools whose calls give the act keys ─────────────────────────────────


async def update_task(task_id: str, title: str = "") -> str:
    """Update one Projects task."""
    return "ok"


async def delete_tag(tag_id: str) -> str:
    """Delete one tag."""
    return "ok"


async def delete_view(view_id: str) -> str:
    """Delete one view."""
    return "ok"


async def create_lead(name: str, email: str = "") -> str:
    """Create one CRM lead."""
    return "ok"


async def create_task(title: str, assignees: list[str] | None = None) -> str:
    """Create one Projects task."""
    return "ok"


async def compose_reply(email_id: str, account_id: str = "a1", reply_all: bool = False,
                        draft: str = "") -> str:
    """Reply to one email."""
    return "ok"


async def send_email(to: list[str], subject: str = "", body: str = "") -> str:
    """Send a new email."""
    return "ok"


async def _card(title: str, then: str, *, call: tuple[Any, ...] | None = None,
                detail: str = "d", **kw: Any) -> tuple[Any, dict]:
    """Show one card that closes on a steer, and end it with *then*: "steer",
    "APPROVE" or "REJECT". *call* is the tool call that draws the card, as
    the injection seam names it: ``(fn, kwargs)`` or ``(fn, args, kwargs)``.
    Returns the card's answer and its event."""
    queue: asyncio.Queue = asyncio.Queue()
    base._bind_run(queue)
    start = ask_tools.request_confirmation(title=title, detail=detail, close_on_steer=True, **kw)
    if call is None:
        task = asyncio.ensure_future(start)
    else:
        fn, args, kwargs = (call[0], (), call[1]) if len(call) == 2 else call
        with ask_tools.card_call(fn, tuple(args), dict(kwargs)):
            task = asyncio.ensure_future(start)
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
        seen["answer"], _ = await _card("Reply to puneet@acme.test?", "steer",
                                        call=(compose_reply, {"email_id": "e1"}))
        seen["first"] = ask_tools.take_closed_card_line()
        seen["again"] = ask_tools.take_closed_card_line()

    assert _in_run(body) == ""
    assert ask_tools.is_revised(seen["answer"])
    assert seen["first"] == ONE_LINE
    assert seen["again"] == "", "the line comes once"


@pytest.mark.parametrize("answer", ["APPROVE", "REJECT"])
def test_an_answered_card_adds_no_line(answer: str):
    async def body() -> None:
        await _card("Reply to puneet@acme.test?", answer, call=(compose_reply, {"email_id": "e1"}))

    assert _in_run(body) == ""


def test_a_redraft_of_the_same_act_takes_the_place_of_the_closed_card():
    """"Make it shorter, and reply to all": the model calls the same tool on
    the same email, and the member sends it. The act is done."""

    async def body() -> None:
        await _card("Reply to puneet@acme.test?", "steer",
                    call=(compose_reply, {"email_id": "e1", "draft": "long"}))
        await _card("Reply to 2 recipients?", "APPROVE",
                    call=(compose_reply, {"email_id": "e1", "reply_all": True, "draft": "short"}))

    assert _in_run(body) == ""


def test_a_redraft_that_closes_again_keeps_one_line():
    async def body() -> None:
        await _card("Reply to puneet@acme.test?", "steer", call=(compose_reply, {"email_id": "e1"}))
        await _card("Reply to puneet@acme.test?", "steer", call=(compose_reply, {"email_id": "e1"}))

    assert _in_run(body) == ONE_LINE


@pytest.mark.parametrize(("first", "then", "act"), [
    # The review's five cases. Each later card is another act, so the line stays.
    (("Update this task?", update_task, {"task_id": "12", "title": "A"}),
     ("Update this task?", update_task, {"task_id": "15", "title": "A"}),
     "update this task"),
    (("Delete this tag?", delete_tag, {"tag_id": "t1"}),
     ("Delete this view?", delete_view, {"view_id": "v1"}),
     "delete this tag"),
    (("Create this CRM lead?", create_lead, {"name": "Veena"}),
     ("Create this task?", create_task, {"title": "Call Veena"}),
     "create this CRM lead"),
    (("Reply to kim@acme.test?", compose_reply, {"email_id": "e1"}),
     ("Reply to 2 recipients?", compose_reply, {"email_id": "e2", "reply_all": True}),
     "reply to kim@acme.test"),
    (("Reply to kim@acme.test?", compose_reply, {"email_id": "e1"}),
     ("Email to kim@acme.test", send_email, {"to": ["kim@acme.test"]}),
     "reply to kim@acme.test"),
], ids=["update-12-then-15", "tag-then-view", "lead-then-task", "reply-other-email",
        "reply-then-new-email"])
def test_a_card_of_another_act_leaves_the_line(first: tuple, then: tuple, act: str):
    async def body() -> None:
        await _card(first[0], "steer", call=(first[1], first[2]))
        await _card(then[0], "APPROVE", call=(then[1], then[2]))

    assert _in_run(body) == f"Not done: «{act}». {TAIL_ONE}"


def test_free_text_does_not_change_the_key():
    """The same task with a new title is the same act: a redraft."""

    async def body() -> None:
        await _card("Update this task?", "steer",
                    call=(update_task, {"task_id": "12", "title": "Old"}))
        await _card("Update this task?", "APPROVE",
                    call=(update_task, {"task_id": "12", "title": "New"}))

    assert _in_run(body) == ""


def test_a_card_with_no_key_takes_the_place_of_none():
    """A card that no named call drew (an ungated tool, ``approve_all``)
    has no key. It never takes the place of a closed card, and a closed
    card with no key is never taken by a keyed one."""

    async def body() -> None:
        await _card("Update this task?", "steer", call=(update_task, {"task_id": "12"}))
        await _card("Update this task?", "APPROVE")
        await _card("Create this task?", "steer")
        await _card("Create this task?", "APPROVE")
        await _card("Create this task?", "APPROVE", call=(create_task, {"title": "x"}))

    assert _in_run(body) == (
        f"Not done: «update this task» and «create this task». {TAIL_MANY}")


def test_the_act_key_rule():
    """The key: each id and ``to`` always. The identity arguments only in a
    call with no object id: a tool that creates, or ids that name a place.
    Free text never. Case and spaces do not count."""
    new_mail = ask_tools.act_key("m", "send_email", {
        "to": ["Kim@Acme.test", "raj@acme.test"], "subject": " Rates  for Q4 ", "body": "text",
        "account_id": "A1", "cc": ["x@acme.test"], "draft_id": None,
    })
    assert new_mail == ("m", "send_email", (
        ("account_id", "a1"), ("subject", "rates for q4"),
        ("to", ("kim@acme.test", "raj@acme.test"))))
    # An object id: the title is the change, not the object.
    assert ask_tools.act_key("m", "update_task", {"task_id": "12", "title": "New"}) == (
        "family", "update_task", (("task_id", "12"),))
    # A place id only: the title names the new task.
    assert ask_tools.act_key("m", "create_task", {"project_id": "P1", "title": "Demo"}) == (
        "family", "create_task", (("project_id", "p1"), ("title", "demo")))
    # A tool that creates: its record id is a place.
    assert ask_tools.act_key("m", "log_activity", {
        "entity": "lead", "record_id": "L1", "activity_type": "call", "subject": "Intro",
    }) == ("m", "log_activity", (("record_id", "l1"), ("subject", "intro")))
    # A Studio manifest names its app one level down.
    assert ask_tools.act_key("m", "studio_plan", {"manifest": {"name": "Leave", "x": 1}}) == (
        "m", "studio_plan", (("manifest.name", "leave"),))
    assert ask_tools.act_key("m", "", {"task_id": "1"}) is None


def test_the_injection_seam_binds_the_act_key():
    """``_gate_injected_tool`` is the boundary every injected tool and every
    native agent's own tool crosses. It names the call for the card. A
    delegation tool gets no key, so a card of its child run has none."""
    from orchestrator import _tool_injection

    async def update_task_probe(task_id: str, title: str = "") -> Any:
        """Read the key that a card of this call would bind."""
        return ask_tools._CARD_CALL.get()

    async def call_specialist(task: str, record_id: str = "") -> Any:
        """A specialist tool of the orchestrator: a delegation."""
        return ask_tools._CARD_CALL.get()

    call_specialist.__delegates_to__ = "crm-assistant"  # type: ignore[attr-defined]
    gated = _tool_injection._gate_injected_tool(update_task_probe)
    delegated = _tool_injection._gate_injected_tool(call_specialist)

    async def run() -> tuple[Any, Any, Any]:
        executor._stream_relay_thread_id.set(THREAD)
        by_name = await gated(task_id="12", title="Free text")
        by_place = await gated("12", "Other text")
        return by_name, by_place, await delegated(task="x", record_id="r1")

    by_name, by_place, child = asyncio.run(run())
    want = frozenset({(__name__, "update_task_probe", (("task_id", "12"),))})
    assert by_name == want and by_place == want
    assert child is None, "a delegation opens a scope with no key"
    assert ask_tools._CARD_CALL.get() is None, "the scope ends with the call"


def test_a_card_outside_a_run_record_records_nothing():
    """No record (a batch run, WhatsApp): the card works as before, and no
    line is made."""

    async def run() -> tuple[Any, str]:
        out, _ = await _card("Reply to puneet@acme.test?", "steer")
        return out, ask_tools.take_closed_card_line()

    out, line = asyncio.run(run())
    assert ask_tools.is_revised(out) and line == ""


def test_each_run_has_its_own_record_and_release_leaves_none():
    """Two runs in turn on one task, then two at once. Each has its own
    record, and a release leaves no record behind."""

    async def in_turn() -> tuple[Any, str, str]:
        first = ask_tools.open_closed_cards_for_run()
        ask_tools._card_closed_by_message("Reply to puneet@acme.test?", None)
        ask_tools.release_closed_cards(first)  # the run ends with its line untaken
        after = (ask_tools._RUN_CLOSED_CARDS.get(), ask_tools.take_closed_card_line())
        second = ask_tools.open_closed_cards_for_run()
        try:
            return after[0], after[1], ask_tools.take_closed_card_line()
        finally:
            ask_tools.release_closed_cards(second)

    left, line_after_release, line_of_second = asyncio.run(in_turn())
    assert left is None and line_after_release == "", "a release leaves no record"
    assert line_of_second == "", "the next run starts with an empty record"

    async def one_run(title: str) -> str:
        token = ask_tools.open_closed_cards_for_run()
        try:
            ask_tools._card_closed_by_message(title, None)
            await asyncio.sleep(0)
            return ask_tools.take_closed_card_line()
        finally:
            ask_tools.release_closed_cards(token)

    async def at_once() -> list[str]:
        return list(await asyncio.gather(one_run("Reply to a@acme.test?"),
                                         one_run("Reply to b@acme.test?")))

    assert asyncio.run(at_once()) == [
        f"Not done: «reply to a@acme.test». {TAIL_ONE}",
        f"Not done: «reply to b@acme.test». {TAIL_ONE}",
    ]


def test_two_closed_cards_are_named_in_one_line():
    async def body() -> None:
        await _card("Reply to puneet@acme.test?", "steer", call=(compose_reply, {"email_id": "e1"}))
        await _card("Create «Demo» with 2 tasks?", "steer", call=(create_task, {"title": "Demo"}))

    assert _in_run(body) == (
        f"Not done: «reply to puneet@acme.test» and «create Demo with 2 tasks». {TAIL_MANY}")


def test_three_closed_cards_take_the_comma_form():
    assert ask_tools.closed_card_line(
        ["Reply to a@acme.test?", "Reply to b@acme.test?", "Reply to c@acme.test?"]) == (
        "Not done: «reply to a@acme.test», «reply to b@acme.test», and "
        f"«reply to c@acme.test». {TAIL_MANY}")


# ── 1b. A call with no object id keys on what it makes (the real tools) ─────


def _real(name: str) -> Any:
    """A real card tool, so its own signature binds the key."""
    if name in ("create_lead", "log_activity"):
        from tests.unit._crm_agent_fakes import load_agent_module

        return getattr(load_agent_module("crm_assistant_agent_3130_keys"), name)
    from skill_projects import forms, guarded, writes

    for module in (writes, forms, guarded):
        if hasattr(module, name):
            return getattr(module, name)
    raise AssertionError(name)


@pytest.mark.parametrize(("tool", "title", "first", "other", "same"), [
    ("create_lead", "Create this CRM lead?",
     (("Veena Rao",), {"email": "veena@acme.test"}),
     ((), {"lead_name": "Raj Iyer"}),
     ((), {"lead_name": " veena  RAO", "email": "veena@work.test"})),
    ("create_task", "Create this task?",
     ((), {"project_id": "P1", "title": "Demo", "assignees": "Kiran"}),
     ((), {"project_id": "P1", "title": "Call Raj"}),
     ((), {"project_id": "P1", "title": "Demo", "assignees": "Kiran, Veena"})),
    ("log_activity", "Log this call on «Acme»?",
     ((), {"entity": "lead", "record_id": "L1", "activity_type": "call", "subject": "Intro"}),
     ((), {"entity": "lead", "record_id": "L1", "activity_type": "call", "subject": "Follow up"}),
     ((), {"entity": "lead", "record_id": "L1", "activity_type": "call", "subject": "Intro",
           "body": "Talked about Q4."})),
], ids=["create_lead", "create_task", "log_activity"])
def test_a_new_object_keys_on_its_identity(tool: str, title: str, first: tuple, other: tuple,
                                           same: tuple):
    """"Add lead Veena", a steer "also add Raj" closes the card, and the
    member approves the Raj card. Veena was never made, so the line stays.
    The same lead drawn again, with a new email, is the same act."""
    fn = _real(tool)

    async def another() -> None:
        await _card(title, "steer", call=(fn, *first))
        await _card(title, "APPROVE", call=(fn, *other))

    async def again() -> None:
        await _card(title, "steer", call=(fn, *first))
        await _card(title, "APPROVE", call=(fn, *same))

    act = ask_tools.card_act(title)
    assert _in_run(another) == f"Not done: «{act}». {TAIL_ONE}"
    assert _in_run(again) == ""


async def grant_access(project_id: str, subject: str) -> str:
    """Give one person or group access to a project (a tool that creates
    nothing, with a place id only)."""
    return "ok"


def test_a_place_id_is_not_an_object_id():
    """``project_id`` names a place. A grant to the sales group and a grant
    to Kim in one project are two acts, so the line stays."""

    async def body() -> None:
        await _card("Change who can see this project?", "steer",
                    call=(grant_access, {"project_id": "P1", "subject": "group:sales"}))
        await _card("Change who can see this project?", "APPROVE",
                    call=(grant_access, {"project_id": "P1", "subject": "kim@acme.test"}))

    assert _in_run(body) == f"Not done: «change who can see this project». {TAIL_ONE}"


def test_two_new_mails_to_one_person_are_two_acts():
    async def body() -> None:
        await _card("Email to kim@acme.test", "steer",
                    call=(send_email, {"to": ["kim@acme.test"], "subject": "Rates"}))
        await _card("Email to kim@acme.test", "APPROVE",
                    call=(send_email, {"to": ["kim@acme.test"], "subject": "Lunch"}))

    assert _in_run(body) == f"Not done: «email to kim@acme.test». {TAIL_ONE}"


@pytest.mark.parametrize(("closed", "opened", "line_stays"), [
    # A batch that holds the closed task makes it: the line goes.
    (("create_task", {"project_id": "P1", "title": "Demo"}),
     ("create_tasks", {"project_id": "P1", "tasks": json.dumps(
         [{"title": "Demo"}, {"title": "Call Raj"}])}), False),
    # A batch without it, or in another project, does not make it.
    (("create_task", {"project_id": "P1", "title": "Demo"}),
     ("create_tasks", {"project_id": "P1", "tasks": json.dumps([{"title": "Call Raj"}])}), True),
    (("create_task", {"project_id": "P1", "title": "Demo"}),
     ("create_tasks", {"project_id": "P2", "tasks": json.dumps([{"title": "Demo"}])}), True),
    # The same for a change: the batch must hold the task.
    (("update_task", {"task_id": "12", "due": "2026-12-01"}),
     ("update_tasks", {"changes": json.dumps(
         [{"task_id": "12", "due": "2026-12-01"}, {"task_id": "15", "status": "Done"}])}), False),
    (("update_task", {"task_id": "12", "due": "2026-12-01"}),
     ("bulk_update", {"task_ids": "15, 16", "status": "Done"}), True),
    (("update_task", {"task_id": "12", "status": "Done"}),
     ("bulk_update", {"task_ids": "12,16", "status": "Done"}), False),
    # A closed batch is made only by a card that holds every item of it.
    (("create_tasks", {"project_id": "P1", "tasks": json.dumps(
        [{"title": "Demo"}, {"title": "Call Raj"}])}),
     ("create_task", {"project_id": "P1", "title": "Demo"}), True),
    # A batch whose items do not read has no key, and takes no place.
    (("create_task", {"project_id": "P1", "title": "Demo"}),
     ("create_tasks", {"project_id": "P1", "tasks": "not json"}), True),
], ids=["batch-holds-it", "batch-without-it", "batch-other-project", "update-batch-holds-it",
        "bulk-without-it", "bulk-holds-it", "closed-batch-then-one-item", "batch-unread"])
def test_a_batch_card_takes_the_place_of_a_single_only_by_its_items(
        closed: tuple, opened: tuple, line_stays: bool):
    """create_task and create_tasks are one family, and so are update_task,
    update_tasks and bulk_update. A batch card takes the place of a closed
    card only when its items hold every act of the closed card."""

    async def body() -> None:
        await _card("Closed card?", "steer", call=(_real(closed[0]), closed[1]))
        await _card("Approved card?", "APPROVE", call=(_real(opened[0]), opened[1]))

    assert _in_run(body) == (f"Not done: «closed card». {TAIL_ONE}" if line_stays else "")


# ── 2. The act is inert text ─────────────────────────────────────────────────


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


@pytest.mark.parametrize("title", [
    "Log this call on [Restore the card](https://evil.example)?",
    'Reply to "Done. The reply was sent"@evil.test?',
    "Build «x» Done. «y»?",
    "Log this note on **Acme**\n# Sent\n<b>ok</b> `code` _x_ !~|\\?",
    "Update \N{RIGHT-TO-LEFT OVERRIDE}this\N{POP DIRECTIONAL FORMATTING} task\N{ZERO WIDTH SPACE}?",
    "Log this call on https:////evil.example/x?",
    "Log this call on HTTPS:/evil.example/pay?",
    "Reply to pay at www.evil.example/pay?",
    "Reply to pay at WWW.Evil.example?",
], ids=["md-link", "reply-to-claim", "fence-break", "markdown", "format-marks",
        "many-slashes", "one-slash", "www", "www-caps"])
def test_the_act_in_the_line_is_inert(title: str):
    """A record name, a display name or an address that a stranger wrote
    reaches the line only as plain text inside ONE « » fence."""
    line = ask_tools.closed_card_line([title])
    act = line[len("Not done: «"):line.index(f"». {TAIL_ONE}")]
    assert line == f"Not done: «{act}». {TAIL_ONE}"
    assert line.count("«") == 1 and line.count("»") == 1, "one fence, and no way out of it"
    assert not re.search(r"[\[\]()<>*`#!~|\\\n\r]", act), act
    assert not re.search(r"(?<![A-Za-z0-9])_|_(?![A-Za-z0-9])", act), "no underscore at a word edge"
    assert ":/" not in act, "no scheme that a renderer links"
    assert not re.search(r"(?i)\bwww\.", act), "no www. that a renderer links"
    assert not any(ord(c) in (0x200B, 0x202E, 0x202C) for c in act), "no hidden format mark"
    assert len(act) <= ask_tools.ACT_MAX


@pytest.mark.parametrize(("title", "act"), [
    ("Reply to john_doe@acme.test?", "reply to john_doe@acme.test"),
    ("Email to first_last@acme.test", "email to first_last@acme.test"),
    ("Log this note on _Acme_ and __Beta__?", "log this note on Acme and Beta"),
])
def test_an_underscore_inside_a_word_stays(title: str, act: str):
    """GFM never reads an underscore inside a word as emphasis. So it stays
    there, and the line names the same address as the card. An underscore
    at a word edge can open emphasis, so it goes."""
    assert ask_tools.card_act(title) == act


def test_a_long_title_is_cut_to_the_cap():
    act = ask_tools.card_act("Reply to " + "x" * 300 + "@acme.test?")
    assert len(act) == ask_tools.ACT_MAX and act.endswith("…")


def test_the_outcome_word_is_the_one_of_hrd_7():
    """HRD-7 grows one vocabulary of card outcomes from this word."""
    assert ask_tools.CLOSED_BY_YOUR_MESSAGE == "closed by your message"
    spec = (ROOT / "project-docs/specs/chat_failure_modes.md").read_text(encoding="utf-8")
    assert "closed by your message" in spec


# ── 3. Every app's card door records the closed card ────────────────────────


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
    assert lines == {
        "email": f"Not done: «send this mail». {TAIL_ONE}",
        "projects": f"Not done: «create this task». {TAIL_ONE}",
        "crm": f"Not done: «create this CRM lead». {TAIL_ONE}",
    }


# ── 4. The executor sends the line last, and the session and message hold it ─


def test_the_executor_ends_each_finished_run_with_the_line():
    """A source fence: each RUN_FINISHED of ``run_agent_stream`` follows the
    line step, and the line step follows the CRM note. Each run that saves a
    native session takes the line first, and gives it to the save. The run
    opens one record and releases it in the same finally."""
    src = (ROOT / "apps/services/orchestrator/orchestrator/executor.py").read_text(
        encoding="utf-8")
    body = src[src.index("async def run_agent_stream("):src.index("def _classify_error(")]
    finishes = [m.start() for m in re.finditer(r'"type": "RUN_FINISHED"', body)]
    assert len(finishes) == 3
    for at in finishes:
        before = body[max(0, at - 700):at]
        assert "_closed_card_events(" in before
        assert before.rindex("_closed_card_events(") > before.rindex("_crm_note_events()")
    saves = [m.start() for m in re.finditer(r"await _finish_native_session\(", body)]
    assert len(saves) == 2
    for at in saves:
        before = body[max(0, at - 160):at]
        assert "_card_line = _take_card_line()" in before
        assert before.rstrip().endswith("_close_native_session(_session_turn, _card_line)"), (
            "the line goes into the session right before the save")
    assert body.count("_closed_card_events(_card_line, run_id)") == 2
    assert body.count("_closed_card_events(_take_card_line(), run_id)") == 1
    assert body.count("_closed_cards_token = open_closed_cards_for_run()") == 1
    assert body.count("release_closed_cards(_closed_cards_token)") == 1


def test_the_native_session_holds_the_line(monkeypatch):
    """The session that the next turn loads ends with the line, as the stored
    message does. A run with no line saves the session as before."""
    from orchestrator import native_session_store as store

    saved: list[list[Any]] = []

    async def finish_turn(turn: Any, *, token_budget: int) -> str:
        saved.append(list(turn.session.state[store.HISTORY_SOURCE_ID]["messages"]))
        return "saved"

    monkeypatch.setattr(store, "finish_turn", finish_turn)

    def turn() -> Any:
        session = AgentSession()
        session.state[store.HISTORY_SOURCE_ID] = {"messages": [
            Message("user", ["Reply to Puneet"]),
            Message("assistant", ["Here is the sales graph."]),
        ]}
        return store.SessionTurn(
            organization_id="o", thread_id=THREAD, agent_name="orchestrator",
            outcome=store.HIT, session=session, history=store.SessionHistoryProvider(),
            fingerprint="", save_digest="")

    async def save(line: str) -> None:
        # The order of each finished run: the line, then the save.
        session_turn = turn()
        executor._close_native_session(session_turn, line)
        await executor._finish_native_session(session_turn)

    asyncio.run(save(ONE_LINE))
    asyncio.run(save(""))
    with_line, without = saved
    assert len(with_line) == 2, "the line joins the answer, as one more text part"
    assert with_line[-1].text.endswith(f"\n\n{ONE_LINE}")
    assert with_line[-1].text.startswith("Here is the sales graph.")
    assert without[-1].text == "Here is the sales graph."

    lone = turn()
    lone.session.state[store.HISTORY_SOURCE_ID]["messages"].pop()
    assert store.add_closing_line(lone, ONE_LINE)
    last = lone.session.state[store.HISTORY_SOURCE_ID]["messages"][-1]
    assert base._role(last) == "assistant" and last.text.strip() == ONE_LINE


def test_the_stored_message_holds_the_line():
    """The relay keeps every ``_sse`` event, and the fold of those events is
    the stored message. A reload shows the line."""
    from gateway.chat_fold import fold_run_events

    seen: dict[str, Any] = {}

    async def body() -> None:
        await _card("Reply to puneet@acme.test?", "steer", call=(compose_reply, {"email_id": "e1"}))
        seen["events"] = executor._closed_card_events(executor._take_card_line(), "run-3130")
        seen["again"] = executor._closed_card_events(executor._take_card_line(), "run-3130")

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
        assert executor._take_card_line() == ""

    _in_run(body)
    assert executor._take_card_line() == "", "no record, no line"
    assert executor._closed_card_events("", "r") == []


def test_a_parked_run_sends_the_line(flag_on, ask_db, liveness):
    """A run that parks on a later card ends with its own RUN_FINISHED. The
    line of a card that a chat message closed earlier comes just before it."""
    from tests.unit.test_pending_ask_flow import _ALICE, _ORG_A, _QUESTION, _bind, _events

    tid = "t-park-3130"
    liveness.store[f"cc:runactor:{tid}"] = _ALICE

    async def the_run() -> None:
        executor._stream_relay_thread_id.set(tid)
        _bind(_ORG_A)
        ask_tools.open_closed_cards_for_run()
        ask_tools._card_closed_by_message("Reply to puneet@acme.test?", None)
        await ask_tools.request_confirmation(_QUESTION, "To billing@acme.test")

    async def go() -> None:
        run = asyncio.create_task(the_run())
        stream_relay._DETACHED_TASKS[tid] = run
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(run, timeout=5)

    asyncio.run(go())
    events = _events(liveness, tid)
    assert events[-1] == {"type": "RUN_FINISHED", "threadId": tid, "parked": True}
    assert [e["type"] for e in events[-4:-1]] == [
        "TEXT_MESSAGE_START", "TEXT_MESSAGE_CONTENT", "TEXT_MESSAGE_END"]
    assert events[-3]["delta"] == f"\n\n{ONE_LINE}"


# ── 5. Every steer gets an answer ───────────────────────────────────────────


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


# ── 6. The act stays reachable: "do it" draws the same card again ───────────


@pytest.mark.parametrize("stream", [False, True], ids=["batch", "stream"])
def test_do_it_draws_the_same_card_again(stream: bool):
    """A card, a steer about something else, then "do it". The run ends as
    each finished run ends: ``executor._close_native_session`` puts the line
    in the session. The next turn reads the call, its arguments and the
    line from the session, and draws the same card on a new id. (The web
    chat after a steer reads the text history, task 2948: there only a
    live-model eval can prove it.)"""
    from orchestrator._native_run_context import agent_for_run
    from orchestrator.native_session_store import HIT, SessionHistoryProvider, SessionTurn
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
    assert first_line == f"Not done: «create this task». {TAIL_ONE}"
    # The end of a finished run: the line goes into the session, then the save.
    executor._close_native_session(SessionTurn(
        organization_id="o", thread_id=THREAD, agent_name="projects", outcome=HIT,
        session=session, history=history, fingerprint="", save_digest=""), first_line)

    second_line = asyncio.run(turn("do it", "APPROVE"))
    assert second_line == "", "the act is done now"
    first, second = (c["value"] for c in cards)
    assert second["request_id"] != first["request_id"], "a new card"
    assert (second["title"], second["detail"], second["context"]) == (
        first["title"], first["detail"], first["context"]), "the same act"
    assert written == [["Kiran"]], "the act happened once, as the first card drew it"
    do_it = client.requests[2]
    assert writes.CARD_REVISED in base._tools(do_it), "the history says the card closed"
    answers = [m.text for m in do_it if base._role(m) == "assistant" and m.text]
    assert answers and answers[-1].endswith(f"\n\n{first_line}"), (
        "the next turn reads the line at the end of the last answer")


def test_this_file_pins_each_rule_by_name():
    """Each test that the docstring names exists, so a rename cannot hide a rule."""
    tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))
    names = {n.name for n in tree.body if isinstance(n, ast.FunctionDef)}
    named = set(re.findall(r"``(test_[a-z0-9_]+)``", ast.get_docstring(tree) or ""))
    assert named and named <= names, named - names
