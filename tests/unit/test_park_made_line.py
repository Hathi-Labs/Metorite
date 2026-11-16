"""Task 3595 PARK-LINE: a parked reply names the approved acts that it ran.

Spec: ``project-docs/specs/chat_run_continuity.md`` §4.12.7, row PARK-LINE.

In production (run dd49a886) a reply made a project, a child project, three
tasks and a reminder on five approved cards. It then stopped at a card that
nobody answered, and the run parked after 10 minutes. The reply's last text
was the turn-start line, so the member never heard what the reply had made.
Now ``pending_ask.park`` sends ONE server line, with no model call, after the
"Not done" line of task 3130 and before ``RUN_FINISHED parked``.

The rule for "an act ran": the gate wrapper (``_tool_injection._agated``)
records the act of the call's last card when the member approved that card
AND the tool returned with no raise AND its result does not say "not done"
(``ask_tools.result_says_not_run``). A delegation tool records nothing
itself. Only a run that is not delegated opens the record
(``executor._open_approved_acts``), so a child writes into its parent's.

Each rule has a test, named here so that a rename cannot hide one:

* ``test_a_parked_reply_names_each_approved_act_before_run_finished`` (a)
* ``test_both_lines_come_in_order_before_run_finished`` (a, order)
* ``test_an_approved_tool_that_raises_is_not_named`` (b)
* ``test_a_not_done_result_is_not_named`` (c)
* ``test_a_partial_receipt_of_projects_is_not_named`` (c)
* ``test_a_card_that_did_not_approve_is_not_named`` (d)
* ``test_a_declined_card_on_the_real_path_is_not_named`` (d)
* ``test_a_park_with_no_act_sends_the_bytes_of_before`` (e)
* ``test_a_child_act_and_the_parent_act_share_one_line`` (f)
* ``test_the_run_opens_the_record_only_when_not_delegated`` (f)
* ``test_the_act_in_the_line_is_inert`` (g)
* ``test_two_threads_never_share_a_record`` (h)
* ``test_the_words_are_pinned``
* ``test_a_call_with_no_card_names_no_act``

Run::

    uv run pytest tests/unit/test_park_made_line.py -q
"""
# The fixtures imported by name below are redefined as test arguments (F811).
# ruff: noqa: F811
from __future__ import annotations

import ast
import asyncio
import re
from pathlib import Path
from typing import Any

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


def _tool(name: str, title: str, result: Any = "Created.", *, raises: bool = False) -> Any:
    """A gated tool that asks one card, then returns *result* on approve."""

    async def tool(subject: str = "") -> Any:
        approved = await ask_tools.request_confirmation(title, f"«{subject}»")
        if not approved:
            return ask_tools.card_not_done_text()
        if raises:
            raise RuntimeError("the write failed")
        return result

    tool.__name__ = name
    tool.__qualname__ = name
    return _tool_injection._gate_injected_tool(tool)


def _scripted(name: str, title: str, outcome: str, result: Any = "Created.") -> Any:
    """A gated tool whose card ends with *outcome*, with no card on screen.
    It sets the frame as ``request_confirmation`` sets it."""

    async def tool() -> Any:
        ask_tools._LAST_OUTCOME.set(None)
        ask_tools._LAST_TITLE.set(title)
        await asyncio.sleep(0)
        ask_tools._LAST_OUTCOME.set(outcome)
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
            assert not ask_tools.result_says_not_run(await call)
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
    reminder = _tool("create_reminder", "Create this reminder?", "Reminder set.")
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
        f"\n\nApproved and run in this reply: «create this reminder». {STOP}"]


@pytest.mark.parametrize("result", [
    "Error: the gateway refused the write.",
    "Declined: the member said no on the card, so nothing was done.",
    "Timed out: the card got no answer in time, so nothing was done.",
    "[blocked by permission policy: denied]",
    "Created 2. The assignees were NOT saved: 403 Forbidden.",
    "Created 2. The assignees may or may not be saved.",
    "stopped: 1 of 3 moves failed.",
    "Ops: the repeat rule is refused. Nothing was created.",
    "Done 2 of 3. 1 could not move.",
    {"ok": False, "error": "refused"},
])
def test_a_not_done_result_is_not_named(result: Any):
    tool = _scripted("create_task", "Create this task?", CARD_APPROVED, result)

    async def body() -> None:
        assert await tool() == result, "the wrapper gives the result unchanged"

    assert _acts_of(body) == ""


def test_a_partial_receipt_of_projects_is_not_named():
    """The real receipt of a create whose later write failed
    (``writes._create_stopped``) says NOT saved, so it is never named."""
    import httpx
    from skill_projects import writes

    receipt = writes._create_stopped(
        {"id": "t1", "full_id": "OPS-12", "number": 12}, [], "assignees",
        httpx.HTTPStatusError("403", request=httpx.Request("PUT", "http://x"),
                              response=httpx.Response(403)), "")
    assert ask_tools.result_says_not_run(receipt), receipt


@pytest.mark.parametrize("outcome", [
    CARD_DECLINED, CARD_TIMED_OUT, CLOSED_BY_YOUR_MESSAGE, CARD_NOT_SHOWN,
    CARD_OTHER_MEMBER, None,
])
def test_a_card_that_did_not_approve_is_not_named(outcome: str | None):
    tool = _scripted("create_task", "Create this task?", outcome)  # type: ignore[arg-type]

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
    showed no card: the wrapper clears the title before each call."""
    approved = _scripted("create_task", "Create this task?", CARD_APPROVED)

    async def read_tasks() -> str:
        return "3 tasks."

    read = _tool_injection._gate_injected_tool(read_tasks)

    async def body() -> None:
        await approved()
        await read()

    assert _acts_of(body) == "Approved and run in this reply: «create this task». " + STOP


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
    the child's approved card is the last card of the frame."""
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
