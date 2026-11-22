"""Task 3595 PARK-LINE: a parked reply names the approved acts that it ran.

Spec: ``project-docs/specs/chat_run_continuity.md`` §4.12.7, row PARK-LINE.

In production (run dd49a886) a reply made a project, a child project, three
tasks and a reminder on five approved cards. It then stopped at a card that
nobody answered, and the run parked after 10 minutes. The reply's last text
was the turn-start line, so the member never heard what the reply had made.
Now ``pending_ask.park`` sends ONE server line, with no model call, after the
"Not done" line of task 3130 and before ``RUN_FINISHED parked``.

The line is a statement of consent: "Approved and run in this reply: ...".
So it must NEVER name an act that did not run. The rule is POSITIVE PROOF by
the tool, and it fails safe (the redesign after two reviews found real
not-done texts that a text check read as "ran"):

* A tool calls ``ask_tools.act_ran()`` at its own SUCCESS SITE, after the
  write landed in full. The flag belongs to the call: the gate wrapper
  clears it before each call (``forget_card_marks``), and each new card
  clears it (``_open_card_frame``).
* The gate wrapper (``_tool_injection._agated``) records the act of the
  call's last card only when the flag is set AND the member approved that
  card AND the tool is not a delegation tool. A raise records nothing.
* No result text is read. A tool that never calls ``act_ran`` names nothing.
* Only the tools in ``ACT_RAN_SITES`` call it. A new opt-in is a reviewed
  change of that list (``test_only_the_listed_tools_call_act_ran``).

Each rule has a test, named here so that a rename cannot hide one:

* ``test_a_parked_reply_names_each_approved_act_before_run_finished`` (a)
* ``test_both_lines_come_in_order_before_run_finished`` (a, order)
* ``test_an_approved_tool_that_raises_is_not_named`` (b)
* ``test_a_tool_that_does_not_say_it_ran_is_not_named`` (c)
* ``test_a_card_that_did_not_approve_is_not_named`` (d)
* ``test_a_declined_card_on_the_real_path_is_not_named`` (d)
* ``test_a_park_with_no_act_sends_the_bytes_of_before`` (e)
* ``test_a_child_act_and_the_parent_act_share_one_line`` (f)
* ``test_the_run_opens_the_record_only_when_not_delegated`` (f)
* ``test_the_act_in_the_line_is_inert`` (g)
* ``test_two_threads_never_share_a_record`` (h)
* ``test_the_words_are_pinned``
* ``test_a_call_with_no_card_names_no_act``
* ``test_the_proof_of_one_call_never_names_the_next``
* ``test_a_second_card_in_one_call_needs_its_own_proof``
* ``test_each_opted_in_tool_is_named_only_on_its_success_path`` (OPTED_IN)
* ``test_a_tool_that_is_not_opted_in_is_never_named`` (NOT_OPTED_IN)
* ``test_a_profile_write_refused_after_the_approve_is_not_named``
* ``test_the_incident_reply_names_its_acts`` (run dd49a886)
* ``test_an_approved_plan_card_is_no_act``
* ``test_only_the_listed_tools_call_act_ran`` (the source fence)
* ``test_the_text_check_is_gone``

Mutations (R7), each red, then restored:

* M1. ``agents.update_deal_status`` calls ``act_ran()`` before ``_patch``:
  the row "crm move, mirror refused" goes red.
* M2. ``ask_tools.note_approved_act`` drops the ``_ACT_RAN`` test: (c), the
  NOT_OPTED_IN table and the profile test go red.
* M3. ``_agated`` drops ``_forget_card_marks()``: ``test_a_call_with_no_card_names_no_act``
  and ``test_the_proof_of_one_call_never_names_the_next`` go red.
  ``forget_card_marks`` keeps the flag: the second test goes red.
* M4. ``forms._write_batch`` calls ``_act_ran()`` on a partial batch (the
  test is ``if out.made:``): the partial rows of OPTED_IN go red.

Run::

    uv run pytest tests/unit/test_park_made_line.py -q
"""
# The fixtures imported by name below are redefined as test arguments (F811).
# ruff: noqa: F811
from __future__ import annotations

import ast
import asyncio
import contextlib
import os
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from acb_skills import ask_tools
from acb_skills.ask_tools import (
    CARD_APPROVED,
    CARD_DECLINED,
    CARD_NOT_SHOWN,
    CARD_OTHER_MEMBER,
    CARD_TIMED_OUT,
    CLOSED_BY_YOUR_MESSAGE,
)
from orchestrator import _tool_injection, executor, pending_ask, stream_relay

from tests.unit.test_chat_deploy_recovery import fake_redis, liveness  # noqa: F401
from tests.unit.test_pending_ask_flow import (  # noqa: F401
    _ALICE,
    _ORG_A,
    _bind,
    _events,
    ask_db,
    flag_on,
)
from tests.unit.test_profile_write_tools import gateway, own_chat  # noqa: F401

ROOT = Path(__file__).resolve().parents[2]
EXECUTOR = ROOT / "apps/services/orchestrator/orchestrator/executor.py"

WATCH = "Watch this project?"
STOP = "This reply stopped at the card «watch this project» to wait for your answer."
NOT_DONE_TAIL = "The card closed when a new message came in. Say 'do it' to get it back."


@pytest.fixture(autouse=True)
def _open_gate(monkeypatch):
    """The gate allows each test tool, and no test tool is an egress tool.
    This file tests what the wrapper records, not the B6 policy."""
    monkeypatch.setattr(_tool_injection, "_permission_gate",
                        lambda *a, **k: (True, "allowed"))
    monkeypatch.setattr(_tool_injection, "_is_egress", lambda _fn: False)


# ── The tools of the reply ──────────────────────────────────────────────────


def _tool(name: str, title: str, result: Any = "Created.", *, raises: bool = False,
          ran: bool = True) -> Any:
    """A gated tool that asks one card. On approve it writes, then says at
    its success site that the act ran (*ran*), and returns *result*."""

    async def tool(subject: str = "") -> Any:
        approved = await ask_tools.request_confirmation(title, f"«{subject}»")
        if not approved:
            return ask_tools.card_not_done_text()
        if raises:
            raise RuntimeError("the write failed")
        if ran:
            ask_tools.act_ran()
        return result

    tool.__name__ = name
    tool.__qualname__ = name
    return _tool_injection._gate_injected_tool(tool)


def _scripted(name: str, title: str, outcome: str | None, result: Any = "Created.", *,
              ran: bool = True) -> Any:
    """A gated tool whose card ends with *outcome*, with no card on screen.
    It sets the frame as ``request_confirmation`` sets it. With *ran* it
    says that the act ran, whatever the outcome, so a test can prove that
    the wrapper reads the outcome too."""

    async def tool() -> Any:
        ask_tools._LAST_OUTCOME.set(None)
        ask_tools._open_card_frame(title)
        await asyncio.sleep(0)
        ask_tools._LAST_OUTCOME.set(outcome)
        if ran:
            ask_tools.act_ran()
        return result

    tool.__name__ = name
    tool.__qualname__ = name
    return _tool_injection._gate_injected_tool(tool)


def _acts_of(body: Any, waiting: str = WATCH) -> str:
    """Run *body* inside one run's record. Returns the park line."""

    async def run() -> str:
        token = executor._open_approved_acts(False)
        try:
            await body()
            return ask_tools.take_park_line(waiting)
        finally:
            ask_tools.release_approved_acts(token)

    return asyncio.run(run())


async def _answer_next(tid: str, answer: str) -> None:
    """Answer the next card of thread *tid* that waits in this process."""
    for _ in range(500):
        rids = [r for r in list(executor._pending_user_input)
                if executor._pending_user_input.owner_of(r) == tid]
        if rids:
            assert executor.resolve_user_input(rids[0], answer, was_freeform=False,
                                               thread_id=tid)
            return
        await asyncio.sleep(0.01)
    raise AssertionError("no card waited")


async def _park_the_next_card(tid: str, ask_db: Any) -> None:
    """Park the card that waits now, as ``wait_user_future`` parks it."""
    for _ in range(500):
        rids = [rid for (org, rid) in ask_db.rows
                if org == _ORG_A and ask_db.rows[(org, rid)]["state"] == "open"
                and rid in executor._pending_user_input]
        if rids:
            break
        await asyncio.sleep(0.01)
    else:
        raise AssertionError("the card never waited")
    rid = rids[0]
    for _ in range(100):
        if await pending_ask.park(rid, tid, executor._pending_user_input[rid]):
            return
        await asyncio.sleep(0.05)
    raise AssertionError("the run never parked")


def _parked_run(tid: str, ask_db: Any, liveness: Any, monkeypatch: Any,
                body: Any, *, record: bool = True) -> list[dict[str, Any]]:
    """Run *body* as a chat run on thread *tid* that parks on its last card.
    Returns the events of the thread's stream."""
    monkeypatch.setattr(pending_ask, "park_after_seconds", lambda: 3600.0)
    liveness.store[f"cc:runactor:{tid}"] = _ALICE

    async def the_run() -> None:
        executor._stream_relay_thread_id.set(tid)
        _bind(_ORG_A)
        ask_tools.open_closed_cards_for_run()
        if record:
            executor._open_approved_acts(False)
        await body(tid)

    async def go() -> None:
        run = asyncio.create_task(the_run())
        stream_relay._DETACHED_TASKS[tid] = run
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(run, timeout=60)

    asyncio.run(go())
    return _events(liveness, tid)


def _texts(events: list[dict[str, Any]]) -> list[str]:
    return [e["delta"] for e in events if e.get("type") == "TEXT_MESSAGE_CONTENT"]


# ── (a) The line names each approved act, before RUN_FINISHED ───────────────


def test_a_parked_reply_names_each_approved_act_before_run_finished(
        flag_on, ask_db, liveness, monkeypatch):
    project = _tool("create_project", "Create this project?", "Created project «Fair».")
    task = _tool("create_task", "Create this task?", "Created task 12.")
    watch = _tool("watch_project", WATCH)

    async def body(tid: str) -> None:
        for tool, subject in ((project, "Fair"), (task, "Stand")):
            call = asyncio.ensure_future(tool(subject=subject))
            await _answer_next(tid, "APPROVE")
            await call
        parked = asyncio.ensure_future(watch(subject="Fair"))
        await _park_the_next_card(tid, ask_db)
        await parked

    events = _parked_run("t-park-3595-a", ask_db, liveness, monkeypatch, body)
    assert events[-1] == {"type": "RUN_FINISHED", "threadId": "t-park-3595-a", "parked": True}
    assert [e["type"] for e in events[-4:-1]] == [
        "TEXT_MESSAGE_START", "TEXT_MESSAGE_CONTENT", "TEXT_MESSAGE_END"]
    assert events[-3]["delta"] == (
        "\n\nApproved and run in this reply: «create this project» and "
        f"«create this task». {STOP}")
    assert _texts(events) == [events[-3]["delta"]], "one line, once"


def test_both_lines_come_in_order_before_run_finished(flag_on, ask_db, liveness, monkeypatch):
    """The "Not done" line of task 3130 first, then this line, then the end."""
    task = _tool("create_task", "Create this task?", "Created task 12.")
    watch = _tool("watch_project", WATCH)

    async def body(tid: str) -> None:
        ask_tools._card_closed_by_message("Reply to puneet@acme.test?", None)
        call = asyncio.ensure_future(task(subject="Stand"))
        await _answer_next(tid, "APPROVE")
        await call
        parked = asyncio.ensure_future(watch(subject="Fair"))
        await _park_the_next_card(tid, ask_db)
        await parked

    events = _parked_run("t-park-3595-b", ask_db, liveness, monkeypatch, body)
    assert events[-1]["type"] == "RUN_FINISHED" and events[-1]["parked"] is True
    assert [e["type"] for e in events[-7:-1]] == [
        "TEXT_MESSAGE_START", "TEXT_MESSAGE_CONTENT", "TEXT_MESSAGE_END"] * 2
    assert _texts(events) == [
        f"\n\nNot done: «reply to puneet@acme.test». {NOT_DONE_TAIL}",
        f"\n\nApproved and run in this reply: «create this task». {STOP}",
    ]


# ── (b) (c) (d) What is not an act that ran ─────────────────────────────────


def test_an_approved_tool_that_raises_is_not_named(flag_on, ask_db, liveness, monkeypatch):
    task = _tool("create_task", "Create this task?", raises=True)
    reminder = _tool("set_reminder", "Set this reminder?", "Reminder set.")
    watch = _tool("watch_project", WATCH)

    async def body(tid: str) -> None:
        call = asyncio.ensure_future(task(subject="Stand"))
        await _answer_next(tid, "APPROVE")
        with pytest.raises(RuntimeError):
            await call
        call = asyncio.ensure_future(reminder(subject="Friday"))
        await _answer_next(tid, "APPROVE")
        await call
        parked = asyncio.ensure_future(watch(subject="Fair"))
        await _park_the_next_card(tid, ask_db)
        await parked

    events = _parked_run("t-park-3595-raise", ask_db, liveness, monkeypatch, body)
    assert _texts(events) == [
        f"\n\nApproved and run in this reply: «set this reminder». {STOP}"]


@pytest.mark.parametrize("result", [
    # A text that says "done" proves nothing: the tool did not say it ran.
    "Created task 12.",
    "Nudged «Priya» about #12 «Stand». They see it in their Metorite bell.",
    # The texts of the two reviews, which a text check read as "ran".
    "Nobody was told. «Priya» cannot open this task, so the nudge reached no one.",
    "That page does not exist, or you cannot read it.",
    "Knowledge is not open to you.",
    "Applied to 0 of 2 tasks.",
    "archive: affected 0 message(s).",
    {"ok": True, "decision": "approved"},
    None,
])
def test_a_tool_that_does_not_say_it_ran_is_not_named(result: Any):
    tool = _scripted("create_task", "Create this task?", CARD_APPROVED, result, ran=False)

    async def body() -> None:
        assert await tool() == result, "the wrapper gives the result unchanged"

    assert _acts_of(body) == ""


@pytest.mark.parametrize("outcome", [
    CARD_DECLINED, CARD_TIMED_OUT, CLOSED_BY_YOUR_MESSAGE, CARD_NOT_SHOWN,
    CARD_OTHER_MEMBER, None,
])
def test_a_card_that_did_not_approve_is_not_named(outcome: str | None):
    """Even a tool that (wrongly) says that the act ran is not named when
    the member did not approve its card."""
    tool = _scripted("create_task", "Create this task?", outcome)

    async def body() -> None:
        await tool()

    assert _acts_of(body) == ""


def test_a_declined_card_on_the_real_path_is_not_named(flag_on, ask_db, liveness, monkeypatch):
    task = _tool("create_task", "Create this task?", "Created task 12.")
    watch = _tool("watch_project", WATCH)

    async def body(tid: str) -> None:
        call = asyncio.ensure_future(task(subject="Stand"))
        await _answer_next(tid, "REJECT")
        assert (await call).startswith("Declined")
        parked = asyncio.ensure_future(watch(subject="Fair"))
        await _park_the_next_card(tid, ask_db)
        await parked

    events = _parked_run("t-park-3595-no", ask_db, liveness, monkeypatch, body)
    assert _texts(events) == []


def test_a_call_with_no_card_names_no_act():
    """An approved card of an EARLIER call never names a later call that
    showed no card: the wrapper clears the title and the proof before each
    call. Mutation M3 (no clear) names «create this task» twice."""
    approved = _scripted("create_task", "Create this task?", CARD_APPROVED)

    async def read_tasks() -> str:
        return "3 tasks."

    read = _tool_injection._gate_injected_tool(read_tasks)

    async def body() -> None:
        await approved()
        await read()

    assert _acts_of(body) == "Approved and run in this reply: «create this task». " + STOP


def test_the_proof_of_one_call_never_names_the_next():
    """Two gated calls in a row. Call A says that its act ran. Call B has an
    approved card of its own (set the way a card door that keeps the flag
    would set it), and never says that its act ran. Only A is named."""
    first = _scripted("create_task", "Create task A?", CARD_APPROVED)

    async def watch_project() -> str:
        ask_tools._LAST_TITLE.set(WATCH)
        ask_tools._LAST_OUTCOME.set(CARD_APPROVED)
        return "Watching."

    second = _tool_injection._gate_injected_tool(watch_project)

    async def body() -> None:
        await first()
        await second()

    assert _acts_of(body) == ask_tools.park_line(["Create task A?"], WATCH)

    async def direct() -> bool:
        ask_tools.act_ran()
        ask_tools.forget_card_marks()
        return ask_tools._ACT_RAN.get()

    assert asyncio.run(direct()) is False, "forget_card_marks clears the proof"


def test_a_second_card_in_one_call_needs_its_own_proof(monkeypatch):
    """A new card clears the proof (``_open_card_frame``). A tool that wrote
    after card 1 and then stopped after card 2 names nothing: only the act
    of the LAST card can be named, and card 2 has no proof."""
    _framed_card(monkeypatch)

    async def two_cards() -> str:
        await ask_tools.request_confirmation(title="Create this task?")
        ask_tools.act_ran()
        await ask_tools.request_confirmation(title="Assign it to Priya?")
        return "Created task 12. Not assigned."

    tool = _tool_injection._gate_injected_tool(two_cards)

    async def body() -> None:
        await tool()

    assert _acts_of(body) == ""


# ── (e) No act: the bytes of before ─────────────────────────────────────────


def test_a_park_with_no_act_sends_the_bytes_of_before(flag_on, ask_db, liveness, monkeypatch):
    watch = _tool("watch_project", WATCH)

    async def body(tid: str) -> None:
        ask_tools._card_closed_by_message("Reply to puneet@acme.test?", None)
        parked = asyncio.ensure_future(watch(subject="Fair"))
        await _park_the_next_card(tid, ask_db)
        await parked

    events = _parked_run("t-park-3595-none", ask_db, liveness, monkeypatch, body)
    assert events[-1] == {"type": "RUN_FINISHED", "threadId": "t-park-3595-none",
                          "parked": True}
    assert [e["type"] for e in events[-4:-1]] == [
        "TEXT_MESSAGE_START", "TEXT_MESSAGE_CONTENT", "TEXT_MESSAGE_END"]
    assert _texts(events) == [f"\n\nNot done: «reply to puneet@acme.test». {NOT_DONE_TAIL}"]

    # With no record at all (a batch run), the park sends no line either.
    plain = _parked_run("t-park-3595-bare", ask_db, liveness, monkeypatch,
                        lambda tid: _one_card(tid, ask_db), record=False)
    assert _texts(plain) == []
    assert plain[-1]["type"] == "RUN_FINISHED"


async def _one_card(tid: str, ask_db: Any) -> None:
    parked = asyncio.ensure_future(_tool("watch_project", WATCH)(subject="Fair"))
    await _park_the_next_card(tid, ask_db)
    await parked


# ── (f) A child run writes into its parent's record ─────────────────────────


def test_a_child_act_and_the_parent_act_share_one_line():
    """A delegation tool starts a child run in the same context, as
    ``call_agent`` does. The child opens no record of its own, so its act
    joins the parent's. The delegation tool itself names no act, although
    the child's approved card, and its proof, are the last of the frame."""
    child_tool = _scripted("create_task", "Create this task?", CARD_APPROVED, "Created task 12.")
    parent_tool = _scripted("create_project", "Create this project?", CARD_APPROVED)

    async def call_specialist(task: str) -> str:
        token = executor._open_approved_acts(True)  # the child run is delegated
        try:
            await child_tool()
        finally:
            ask_tools.release_approved_acts(token)
        return "The projects assistant created task 12."

    call_specialist.__delegates_to__ = "projects-assistant"  # type: ignore[attr-defined]
    delegate = _tool_injection._gate_injected_tool(call_specialist)

    async def body() -> None:
        await parent_tool()
        await delegate(task="Make the stand task")

    assert _acts_of(body) == (
        "Approved and run in this reply: «create this project» and «create this task». "
        + STOP)


def test_the_run_opens_the_record_only_when_not_delegated():
    """``run_agent_stream`` reads ``_delegated_run`` first, then opens the
    record through ``_open_approved_acts(_delegated_run)``, and releases it
    in its ``finally``."""
    assert executor._open_approved_acts(True) is None
    token = executor._open_approved_acts(False)
    try:
        assert isinstance(ask_tools._RUN_APPROVED_ACTS.get(), ask_tools.ApprovedActs)
    finally:
        ask_tools.release_approved_acts(token)
    assert ask_tools._RUN_APPROVED_ACTS.get() is None

    tree = ast.parse(EXECUTOR.read_text(encoding="utf-8"))
    fn = next(n for n in tree.body
              if isinstance(n, ast.AsyncFunctionDef) and n.name == "run_agent_stream")
    src = ast.get_source_segment(EXECUTOR.read_text(encoding="utf-8"), fn) or ""
    delegated = src.index("_delegated_run = bool(artifact_context().get(\"run_id\"))")
    opened = src.index("_approved_acts_token = _open_approved_acts(_delegated_run)")
    assert delegated < opened, "the run knows it is delegated before it opens"
    assert src.count("_open_approved_acts(") == 1
    assert "release_approved_acts(_approved_acts_token)" in src


# ── (g) The act is inert ─────────────────────────────────────────────────────


@pytest.mark.parametrize("title", [
    "Create [win](https://evil.test/x) **now**?",
    "Create `x` <b>y</b> www.evil.test #1 ![i](j)?",
    "Watch «proj» | ~~gone~~ \\ _ok_?",
    "Create\nthis\ttask https:////evil.test?",
])
def test_the_act_in_the_line_is_inert(title: str):
    tool = _scripted("create_task", title, CARD_APPROVED)

    async def body() -> None:
        await tool()

    line = _acts_of(body, waiting=title)
    head, _, _rest = line.partition(": ")
    assert head == ask_tools.PARK_LINE_LEAD
    acts = re.findall(r"«([^«»]*)»", line)
    assert len(acts) == 2 and line.count("«") == 2 and line.count("»") == 2
    for act in acts:
        assert not re.search(r"[\[\]()<>*`#!~|\\]", act), act
        assert "://" not in act and "www." not in act.lower(), act
        assert "\n" not in act and "\t" not in act
        assert not re.search(r"(^|\W)_|_(\W|$)", act), act


# ── (h) Two threads never share a record ─────────────────────────────────────


def test_two_threads_never_share_a_record():
    async def one_run(title: str) -> str:
        token = executor._open_approved_acts(False)
        try:
            await _scripted("create_task", title, CARD_APPROVED)()
            await asyncio.sleep(0)
            return ask_tools.take_park_line("")
        finally:
            ask_tools.release_approved_acts(token)

    async def at_once() -> list[str]:
        return list(await asyncio.gather(one_run("Create task A?"), one_run("Create task B?")))

    tail = "This reply stopped at a card to wait for your answer."
    assert asyncio.run(at_once()) == [
        f"Approved and run in this reply: «create task A». {tail}",
        f"Approved and run in this reply: «create task B». {tail}",
    ]
    assert ask_tools._RUN_APPROVED_ACTS.get() is None, "a release leaves no record"


# ── The words ───────────────────────────────────────────────────────────────


def test_the_words_are_pinned():
    line = ask_tools.park_line
    assert line([], WATCH) == ""
    assert line(["Create this task?"], WATCH) == (
        f"Approved and run in this reply: «create this task». {STOP}")
    assert line(["Create this project?", "Create this task?", "Create a reminder?"], WATCH) == (
        "Approved and run in this reply: «create this project», «create this task», "
        f"and «create a reminder». {STOP}")
    assert line(["Create this task?"], "") == (
        "Approved and run in this reply: «create this task». "
        "This reply stopped at a card to wait for your answer.")
    # The same act twice is two acts: two tasks were made.
    assert line(["Create this task?", "Create this task?"], WATCH).count("«create this task»") == 2
    # History words: no "done", no link, no id.
    sample = line(["Create this task?", "Create this project?"], WATCH)
    assert "done" not in sample.lower() and "http" not in sample and "](" not in sample


# ── The REAL tools: each opted-in tool, on its success and its other paths ──
#
# Each row drives the real tool, behind the real gate wrapper, over a fake
# gateway, with the frame of the real card (``_framed_card``). The verdict is
# True when the line names the act of the tool's card.

EMAIL_AGENT = ROOT / "apps/agents/agent-email-assistant/agents.py"
CRM_AGENT = ROOT / "apps/agents/agent-crm/agents.py"
_MODULES: dict[str, Any] = {}
BOX = "a0000000-0000-4000-8000-000000000001"
PAGE = "3f2c1a4e-0000-4000-8000-000000000001"
#: The title of each card that the fake card door showed, in order.
_SHOWN: list[str] = []


@pytest.fixture(autouse=True)
def _no_card_shown() -> Iterator[None]:
    _SHOWN.clear()
    yield
    _SHOWN.clear()


def _agent(path: Path, name: str) -> Any:
    """The agent module at *path*, loaded once."""
    if name not in _MODULES:
        import importlib.util

        spec = importlib.util.spec_from_file_location(name, path)
        assert spec and spec.loader
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _MODULES[name] = mod
    return _MODULES[name]


def _framed_card(monkeypatch: Any, answer: Any = True) -> None:
    """The card seam, with the frame of the real ``request_confirmation``:
    the title, the outcome, and no proof yet (``_open_card_frame``). A
    callable *answer* gets the card's keywords (a card with rows)."""

    async def card(*args: Any, **kwargs: Any) -> Any:
        title = str(kwargs.get("title", args[0] if args else "")).strip()[:120]
        _SHOWN.append(title)
        ask_tools._LAST_OUTCOME.set(None)
        ask_tools._open_card_frame(title)
        await asyncio.sleep(0)
        ask_tools._LAST_OUTCOME.set(CARD_APPROVED if answer else CARD_DECLINED)
        return answer(kwargs) if callable(answer) else answer

    monkeypatch.setattr(ask_tools, "request_confirmation", card)
    monkeypatch.setattr(ask_tools, "confirmation_channel_open", lambda: True)


def _all_rows(kwargs: dict[str, Any]) -> Any:
    """The member approves the card: each row of a card with rows ticked,
    and True for a card with no rows."""
    if kwargs.get("rows") is None:
        return True
    return frozenset(str(r["id"]) for r in kwargs.get("rows") or [])


@contextlib.contextmanager
def _projects(monkeypatch: Any, responder: Any, answer: Any = _all_rows) -> Iterator[None]:
    """A Projects chat run over *responder*. The card shows, and the member
    approves it (each row of a card with rows)."""
    from acb_skills.write_artifact import artifact_context_scope, bind_artifact_context
    from skill_projects import writes

    from tests.unit._projects_agent_fakes import fake_gateway, form_stub

    async def card_shows() -> bool:
        return False

    _framed_card(monkeypatch, answer)
    form_stub(monkeypatch, {})
    fake_gateway(monkeypatch, responder)
    monkeypatch.setattr(writes, "_reminder_acts_at_once", card_shows)
    with artifact_context_scope():
        bind_artifact_context(agent_name="projects-assistant", no_egress=False)
        yield


def _gated(fn: Any) -> Any:
    return _tool_injection._gate_injected_tool(fn)


def _batch_gateway(**kw: Any) -> Any:
    from tests.unit.test_projects_create_tasks import _gateway

    return _gateway(**kw)


def _project(*, child: bool = False, no_id: bool = False) -> Any:
    async def run(monkeypatch: Any) -> str:
        import skill_projects

        from tests.unit import test_projects_agent_writes as tw

        def answer(call: dict) -> Any:
            if no_id and call["method"] == "POST" and call["path"] == "/projects/nodes":
                return {"name": "Fair"}
            return tw.responder(call)

        with _projects(monkeypatch, answer):
            return await _gated(skill_projects.create_project)(
                "Stand" if child else "Fair", parent_project_id=tw.UUID if child else "")
    return run


def _one_task(*, assignees: str = "", refuse_assign: bool = False) -> Any:
    async def run(monkeypatch: Any) -> str:
        import skill_projects

        from tests.unit.test_projects_create_tasks import UUID

        with _projects(monkeypatch, _batch_gateway(refuse_assign=refuse_assign)):
            return await _gated(skill_projects.create_task)(UUID, "Stand", assignees=assignees)
    return run


def _tasks(*, refuse: tuple[str, ...] = (), ticked: Any = _all_rows,
           refuse_assign: bool = False) -> Any:
    async def run(monkeypatch: Any) -> str:
        import skill_projects

        from tests.unit.test_projects_create_tasks import THREE, UUID

        with _projects(monkeypatch, _batch_gateway(refuse=refuse, refuse_assign=refuse_assign),
                       ticked):
            return await _gated(skill_projects.create_tasks)(UUID, THREE)
    return run


def _tags(*, refuse: bool = False, ticked: Any = _all_rows) -> Any:
    async def run(monkeypatch: Any) -> str:
        import skill_projects

        from tests.unit.test_projects_create_vocab import THREE, UUID, _gateway

        with _projects(monkeypatch, _gateway(forbid=("vip",) if refuse else ()), ticked):
            return await _gated(skill_projects.create_tags)(UUID, THREE)
    return run


def _reminder(saved: dict[str, Any] | None = None) -> Any:
    async def run(monkeypatch: Any) -> str:
        import skill_projects

        from tests.unit.test_reminder_tools import Gateway

        with _projects(monkeypatch, Gateway(saved=saved), True):
            return await _gated(skill_projects.set_reminder)(
                title="Call the vendor", at="2099-10-30 17:00")
    return run


def _crm_move(*, mirror: bool = False) -> Any:
    """The real ``update_deal_status`` of the CRM agent, behind an approved card."""
    async def run(monkeypatch: Any) -> str:
        agents = _agent(CRM_AGENT, "park_line_crm")

        async def get(path: str, params: Any = None) -> Any:
            if path == "/crm/statuses/deal":
                return [{"id": "s2", "name": "Won", "type": "won"}]
            return {"id": "d1", "name": "Acme renewal"}

        async def patch(path: str, body: Any) -> Any:
            if mirror:
                raise agents._MirrorBlocked("The deal is Zoho's.")
            return {}

        async def block(payload: Any, note: str = "") -> str:
            return "deal: x"

        monkeypatch.setattr(agents, "_get", get)
        monkeypatch.setattr(agents, "_patch", patch)
        monkeypatch.setattr(agents, "_held_fields_block", block)
        _framed_card(monkeypatch)
        return await _gated(agents.update_deal_status)(
            "b0000000-0000-4000-8000-000000000002", "Won")
    return run


#: ``(name, run, named, a word of the real result)``. *named* is True when the
#: line names the act of the tool's card.
OPTED_IN: list[tuple[str, Any, bool, str]] = [
    ("project, made", _project(), True, "Created project «Fair»"),
    ("child project, made", _project(child=True), True, "Created project «Stand» under"),
    ("project, no id came back", _project(no_id=True), False, "full_id: None"),
    ("task, made", _one_task(), True, "Created:"),
    ("task, assignees refused", _one_task(assignees="Priya", refuse_assign=True), False,
     "NOT saved"),
    ("tasks, all 3 made", _tasks(), True, "Created 3 tasks in «Ops»:"),
    ("tasks, 1 of 3 refused", _tasks(refuse=("Print the badges",)), False,
     "Created 2 of 3 tasks"),
    ("tasks, 1 row unticked", _tasks(ticked=lambda _kw: frozenset({"row-1", "row-3"})), False,
     "left out: row 2"),
    ("tasks, the assignees of a made task refused", _tasks(refuse_assign=True), False,
     "not saved: the task"),
    ("tasks, a forged row", _tasks(ticked=lambda _kw: frozenset({"row-9"})), False,
     "Refused: the card answered with a row it did not offer"),
    ("tags, all made", _tags(), True, "Added 3 tags"),
    ("tags, 1 refused", _tags(refuse=True), False, "1 was not created"),
    ("tags, 1 row unticked", _tags(ticked=lambda _kw: frozenset({"row-1", "row-2"})), False,
     "unticked on the card"),
    ("reminder, set", _reminder(), True, "Reminder set:"),
    ("reminder, already set", _reminder({"already": True}), False, "Reminder already set:"),
    ("reminder, no id came back", _reminder({"id": None}), False, "no reminder id"),
    ("crm move, moved", _crm_move(), True, "Moved Acme renewal to Won."),
    ("crm move, mirror refused", _crm_move(mirror=True), False, "Zoho"),
]


@pytest.mark.parametrize(("name", "run", "named", "said"), OPTED_IN,
                         ids=[r[0] for r in OPTED_IN])
def test_each_opted_in_tool_is_named_only_on_its_success_path(
        name: str, run: Any, named: bool, said: str, monkeypatch):
    results: list[Any] = []

    async def body() -> None:
        results.append(await run(monkeypatch))

    line = _acts_of(body)
    assert said in str(results[0]), (name, results[0])
    assert _SHOWN, f"{name}: no card was shown"
    if named:
        assert line == ask_tools.park_line([_SHOWN[-1]], WATCH), (name, line)
    else:
        assert line == "", (name, results[0], line)


# ── The REAL tools that are not opted in: never named ────────────────────────


def _nudge(*, reached: bool) -> Any:
    async def run(monkeypatch: Any) -> str:
        import skill_projects

        from tests.unit import test_projects_agent_writes as tw

        def answer(call: dict) -> Any:
            if call["path"].endswith("/nudge") and not reached:
                return {"notified": [], "skipped": ["priya@x.io"], "last_nudged_at": None}
            return tw.responder(call)

        with _projects(monkeypatch, answer, True):
            return await _gated(skill_projects.nudge_task)(tw.WAITER)
    return run


def _bulk() -> Any:
    async def run(monkeypatch: Any) -> str:
        import skill_projects

        from tests.unit import test_projects_agent_writes as tw

        def answer(call: dict) -> Any:
            if call["method"] == "POST" and call["path"] == "/projects/tasks/bulk":
                return {"applied": 0, "requested": 2, "results": [],
                        "skipped": [{"task_id": tw.UUID, "reason": "archived"},
                                    {"task_id": tw.OTHER, "reason": "archived"}]}
            return tw.responder(call)

        with _projects(monkeypatch, answer, True):
            return await _gated(skill_projects.bulk_update)(
                f"{tw.UUID},{tw.OTHER}", tags_add="q4")
    return run


def _knowledge(tool: str, status: int) -> Any:
    """The real ``propose_edit`` or ``propose_page``, and a gateway that
    answers *status*."""
    async def run(monkeypatch: Any) -> str:
        from acb_skills import knowledge_tools as kt

        real = httpx.AsyncClient

        def client(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
            transport = httpx.MockTransport(lambda _r: httpx.Response(status, json={}))
            return real(*args, transport=transport, **kwargs)

        monkeypatch.setattr(kt.httpx, "AsyncClient", client)
        monkeypatch.setenv("GATEWAY_URL", "http://gw.test")
        monkeypatch.setattr(kt, "_current_user_email", lambda: "rep@customer.example")
        _framed_card(monkeypatch)
        if tool == "propose_edit":
            return await _gated(kt.propose_edit)(
                page_id=PAGE, base_version=3, body="# Refunds\n\nForty days.",
                summary="The window is now forty days.")
        return await _gated(kt.propose_page)(
            title="Refund policy", body="# Refunds\n\nThirty days.", summary="The policy.")
    return run


def _send_later(*, refuse: bool) -> Any:
    """The real ``schedule_send`` of the email agent, behind an approved card.
    The route answers, or says that send later is not on (a 404). Its card
    is "Send this draft at ...?", and only a schedule ran."""
    async def run(monkeypatch: Any) -> str:
        agents = _agent(EMAIL_AGENT, "park_line_email")

        async def get(path: str, params: Any = None) -> Any:
            if path == "/email/accounts":
                return [{"id": BOX, "email_address": "dana@fracktal.test",
                         "display_label": "Fracktal"}]
            if path == "/email/messages/d1":
                return {"id": "d1", "account_id": BOX, "folder": "drafts", "subject": "Quote",
                        "to_addresses": [{"email": "ravi@contoso.test"}],
                        "cc_addresses": [], "bcc_addresses": [], "body_text": "Hi Ravi."}
            if path == "/email/drafts/seal":
                return {"text_digest": "a" * 64, "subject": "Quote", "body_text": "Hi Ravi."}
            return {}

        async def post(path: str, body: Any) -> Any:
            if refuse:
                raise agents.GatewayError("send later is not on", 404)
            return {"id": "s1"}

        monkeypatch.setattr(agents, "_get", get)
        monkeypatch.setattr(agents, "_post", post)
        monkeypatch.setattr(agents, "_held_card_mode", lambda: False)
        _framed_card(monkeypatch)
        return await _gated(agents.schedule_send)(BOX, "d1", "2099-01-05T09:00:00+05:30")
    return run


def _archive() -> Any:
    """The real ``manage_inbox`` archive, and a bulk route that changed 0 rows."""
    async def run(monkeypatch: Any) -> str:
        agents = _agent(EMAIL_AGENT, "park_line_email")

        async def parts(_ids: Any) -> tuple[str, str, str]:
            return "", "Each message stays in its own mailbox.", "messages: 2"

        async def post(path: str, body: Any) -> Any:
            return {"affected": 0}

        monkeypatch.setattr(agents, "_inbox_card_parts", parts)
        monkeypatch.setattr(agents, "_post", post)
        monkeypatch.setattr(agents, "_held_card_mode", lambda: False)
        _framed_card(monkeypatch)
        return await _gated(agents.manage_inbox)("archive", ["m1", "m2"])
    return run


#: ``(name, run, a word of the real result)``. None of these is named, on
#: any path: none of these tools calls ``act_ran``.
NOT_OPTED_IN: list[tuple[str, Any, str]] = [
    ("nudge, reached nobody", _nudge(reached=False),
     "Nobody was told. «priya@x.io» cannot open this task, so the nudge reached no one."),
    ("nudge, sent", _nudge(reached=True), "Nudged"),
    ("bulk, applied to 0 of 2", _bulk(), "Applied to 0 of 2 tasks."),
    ("knowledge edit, 404", _knowledge("propose_edit", 404),
     "That page does not exist, or you cannot read it."),
    ("knowledge edit, 401", _knowledge("propose_edit", 401), "Knowledge is not open to you."),
    ("knowledge page, 401", _knowledge("propose_page", 401), "Knowledge is not open to you."),
    ("email send later, scheduled", _send_later(refuse=False), "Scheduled"),
    ("email send later, not on", _send_later(refuse=True), "Not scheduled."),
    ("email archive, 0 changed", _archive(), "archive: affected 0 message(s)."),
]


@pytest.mark.parametrize(("name", "run", "said"), NOT_OPTED_IN,
                         ids=[r[0] for r in NOT_OPTED_IN])
def test_a_tool_that_is_not_opted_in_is_never_named(name: str, run: Any, said: str,
                                                    monkeypatch):
    results: list[Any] = []

    async def body() -> None:
        results.append(await run(monkeypatch))

    line = _acts_of(body)
    assert said in str(results[0]), (name, results[0])
    assert _SHOWN, f"{name}: no card was shown, so the row proves nothing"
    assert line == "", (name, results[0], line)


@pytest.mark.parametrize("case", ["closed_by_read", "learning_off"])
def test_a_profile_write_refused_after_the_approve_is_not_named(own_chat, monkeypatch,
                                                                case: str):
    """Review 2: the member approved the profile card, and then the write
    was refused. A read of other content closed the writes while the card
    waited, or Learning is off."""
    from acb_common import assistant_profile as ap
    from acb_skills import profile_tools as pt

    def approve(_kwargs: dict[str, Any]) -> bool:
        if case == "closed_by_read":
            ap.close_writes()  # a read of other content, while the card waited
        else:
            # The member switched Learning off while the card waited: the
            # gateway's answer to the write is a 409.
            own_chat.force = (409, {"detail": "Learning is off."})
        return True

    _framed_card(monkeypatch, approve)
    results: list[str] = []

    async def body() -> None:
        results.append(await _gated(pt.profile_alias)("Sam", "sam@alpha.example"))

    line = _acts_of(body)
    said = pt.CLOSED_BY_READ if case == "closed_by_read" else pt.LEARNING_OFF
    assert f'"reason":"{case}"' in results[0] and said in results[0], results[0]
    assert _SHOWN, "the card was shown and approved"
    assert line == ""


def test_the_incident_reply_names_its_acts(monkeypatch):
    """Run dd49a886: a project, a child project, three tasks on one batch
    card, and a reminder. Each card was approved and each write landed. The
    line names the four cards. A batch with a refused row is not named."""
    import skill_projects

    from tests.unit.test_projects_create_tasks import THREE, UUID

    def run(refuse: tuple[str, ...]) -> str:
        async def body() -> None:
            with _projects(monkeypatch, _batch_gateway(refuse=refuse)):
                await _gated(skill_projects.create_project)("Fair")
                await _gated(skill_projects.create_project)("Stand", parent_project_id=UUID)
                await _gated(skill_projects.create_tasks)(UUID, THREE)
                await _gated(skill_projects.set_reminder)(
                    title="Call the caterer", at="2099-10-30 17:00")

        return _acts_of(body)

    assert run(()) == (
        "Approved and run in this reply: «create this project», «create this project», "
        f"«create 3 tasks in Ops», and «set this reminder». {STOP}")
    assert run(("Print the badges",)) == (
        "Approved and run in this reply: «create this project», «create this project», "
        f"and «set this reminder». {STOP}")


def test_an_approved_plan_card_is_no_act(monkeypatch):
    """Review P2: the approve of the plan card "Build X?" only records a plan,
    so the line never names «build X». ``studio_plan`` does not call
    ``act_ran``, and ``studio_save`` and ``studio_publish`` ask their own
    cards for the acts."""
    from acb_skills import studio_tools as st

    from tests.unit.test_studio_builder_tools import _serve, _valid

    _serve(monkeypatch)

    async def kept(_doc: Any) -> bool:
        return True

    monkeypatch.setattr(st, "_record_plan", kept)
    _framed_card(monkeypatch)

    async def body() -> None:
        manifest = await _valid()
        out = await _gated(st.studio_plan)(manifest)
        assert out["ok"] is True and out["decision"] == "approved", out

    assert _acts_of(body) == ""
    assert _SHOWN and _SHOWN[0].startswith("Build "), _SHOWN


# ── The source fence: who may call act_ran ───────────────────────────────────

#: Each file that may name ``act_ran``, and the functions in it that call it.
#: A new opt-in is a change of this list, so a reviewer reads its success site.
ACT_RAN_SITES: dict[str, set[str]] = {
    "apps/skills/skill-projects/skill_projects/writes.py": {
        "_act_ran", "create_project", "create_task", "set_reminder"},
    "apps/skills/skill-projects/skill_projects/forms.py": {"_write_batch", "_vocab_write"},
    "apps/agents/agent-crm/agents.py": {"update_deal_status"},
}
_SKIP_DIRS = {"node_modules", ".venv", "__pycache__", ".next", ".git", "site-packages"}


def _callers_of_act_ran(path: Path) -> set[str] | None:
    """The name of each function in *path* that calls ``act_ran`` or
    ``_act_ran``. None when the code neither calls nor imports either: a
    word in a docstring is no call."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out: set[str] = set()
    imports = any(
        isinstance(node, ast.ImportFrom) and any(a.name in ("act_ran", "_act_ran")
                                                 for a in node.names)
        for node in ast.walk(tree))
    for fn in ast.walk(tree):
        if not isinstance(fn, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        for node in ast.walk(fn):
            if not isinstance(node, ast.Call):
                continue
            name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            if name in ("act_ran", "_act_ran"):
                out.add(fn.name)
    return out if (out or imports) else None


def test_only_the_listed_tools_call_act_ran():
    found: dict[str, set[str]] = {}
    for top in ("apps", "packages"):
        for here, dirs, files in os.walk(ROOT / top):
            dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
            for name in files:
                if not name.endswith(".py"):
                    continue
                path = Path(here) / name
                if "act_ran" not in path.read_text(encoding="utf-8", errors="replace"):
                    continue
                callers = _callers_of_act_ran(path)
                if callers is not None:
                    found[path.relative_to(ROOT).as_posix()] = callers
    assert found == ACT_RAN_SITES, (
        "A new caller of act_ran is a reviewed change: add it to ACT_RAN_SITES, "
        "and place the call at the success site only.")


def test_the_text_check_is_gone():
    """The redesign removed the check that read a result text as "ran"."""
    for name in ("result_says_not_run", "_NOT_RUN_MARKS", "_NOT_RUN_FIRST_WORDS",
                 "_RESULT_DATA", "_mapping_ran", "card_records_no_act", "forget_card_title"):
        assert not hasattr(ask_tools, name), name
    src = (ROOT / "apps/services/orchestrator/orchestrator/_tool_injection.py").read_text(
        encoding="utf-8")
    assert "_note_approved_act()" in src and "_note_approved_act(result)" not in src
