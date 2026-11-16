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
AND the tool returned with no raise AND its result proves that the act ran
(``ask_tools.result_says_not_run`` is False). A delegation tool records
nothing itself. Only a run that is not delegated opens the record
(``executor._open_approved_acts``), so a child writes into its parent's.

The result check fails safe (review of task 3595, P1): when unsure, the line
does not name the act, so it never claims more than ran. A mapping result
(Studio) ran only when ``ok`` is True. A result that is no text and no
mapping, an empty text, or a JSON text that does not parse, did not run. A
text did not run when its first word is a not-done word ("Not scheduled.",
"Nothing was changed.", "[not run"), or when it holds a mark of
``_NOT_RUN_MARKS`` OUTSIDE its « » fences and quoted titles. A card that
approves a PLAN (``studio_plan``) records no act (``card_records_no_act``).

The table ``REAL_RESULTS`` holds the real texts of each write family, built
through the real helper or the real tool, with the verdict of each.

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
* ``test_each_real_result_has_its_verdict`` (the table, review P1)
* ``test_a_send_later_that_is_not_on_is_not_named`` (review P1-1 A)
* ``test_a_held_publish_that_failed_is_not_named`` (review P1-1 B)
* ``test_a_full_batch_is_named_beside_the_project`` (review P1-2)
* ``test_a_mark_inside_a_fence_or_a_quote_is_data`` (review P1-2)
* ``test_an_approved_plan_card_is_no_act`` (review P2)

Mutations (R7), each red, then restored: drop the first-word rule; drop the
mapping ``ok`` rule; read the marks inside « »; drop
``card_records_no_act()`` from ``studio_plan``.

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


# ── The rule on REAL results (review of task 3595, P1 and P2) ────────────────
#
# Each entry is a real text of a write family, made by the real helper or the
# real tool, never a string written here. The verdict is True when the line
# must NOT name the act. Fail safe: a partial is never named.

EMAIL_AGENT = ROOT / "apps/agents/agent-email-assistant/agents.py"
CRM_AGENT = ROOT / "apps/agents/agent-crm/agents.py"
_MODULES: dict[str, Any] = {}
BOX = "a0000000-0000-4000-8000-000000000001"


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


def _framed_card(monkeypatch: Any, answer: Any = True) -> list[str]:
    """The card seam, with the frame of the real ``request_confirmation``:
    the title and the outcome of this call's card. A callable *answer* gets
    the card's keywords (a card with rows). Returns the titles."""
    titles: list[str] = []

    async def card(*args: Any, **kwargs: Any) -> Any:
        title = str(kwargs.get("title", args[0] if args else "")).strip()[:120]
        titles.append(title)
        ask_tools._LAST_OUTCOME.set(None)
        ask_tools._LAST_TITLE.set(title)
        await asyncio.sleep(0)
        ask_tools._LAST_OUTCOME.set(CARD_APPROVED if answer else CARD_DECLINED)
        return answer(kwargs) if callable(answer) else answer

    monkeypatch.setattr(ask_tools, "request_confirmation", card)
    monkeypatch.setattr(ask_tools, "confirmation_channel_open", lambda: True)
    return titles


def _all_rows(kwargs: dict[str, Any]) -> frozenset[str]:
    """The member approves a card with rows, with each row ticked."""
    return frozenset(str(r["id"]) for r in kwargs.get("rows") or [])


async def _create_tasks(monkeypatch: Any, tool: Any, *, refuse: tuple[str, ...] = (),
                        ticked: Any = _all_rows) -> str:
    """*tool* (``create_tasks``, maybe wrapped) on the three rows of
    ``test_projects_create_tasks.THREE``, over its fake gateway."""
    from acb_skills.write_artifact import artifact_context_scope, bind_artifact_context

    from tests.unit._projects_agent_fakes import fake_gateway, form_stub
    from tests.unit.test_projects_create_tasks import THREE, UUID, _gateway

    _framed_card(monkeypatch, ticked)
    form_stub(monkeypatch, {})
    fake_gateway(monkeypatch, _gateway(refuse=refuse))
    with artifact_context_scope():
        bind_artifact_context(agent_name="projects-assistant", no_egress=False)
        return await tool(UUID, THREE)


def _batch(*, refuse: tuple[str, ...] = (), ticked: Any = _all_rows) -> Any:
    async def build(monkeypatch: Any) -> str:
        import skill_projects

        return await _create_tasks(monkeypatch, skill_projects.create_tasks,
                                   refuse=refuse, ticked=ticked)
    return build


async def _single_create(_mp: Any) -> str:
    """The real receipt of one ``create_task`` whose TITLE holds a mark."""
    from skill_projects import reads, writes

    task = {"id": "t1", "full_id": "OPS-12", "number": 12, "task_number": 12,
            "title": "Fix failed payment", "status_name": "To do"}
    return writes.NO_REPEAT.receipt(task, reads._task_line(task), {}, None)


async def _create_stopped(_mp: Any) -> str:
    import httpx
    from skill_projects import writes

    return writes._create_stopped(
        {"id": "t1", "full_id": "OPS-12", "number": 12}, [], "assignees",
        httpx.HTTPStatusError("403", request=httpx.Request("PUT", "http://x"),
                              response=httpx.Response(403)), "")


async def _forged(_mp: Any) -> str:
    from skill_projects import forms

    return forms.FORGED_ROWS


def _tags(*, refuse: bool) -> Any:
    """The real receipt of ``create_tags`` on two names, through the tool."""
    async def build(monkeypatch: Any) -> str:
        import skill_projects
        from acb_skills.write_artifact import artifact_context_scope, bind_artifact_context

        from tests.unit._projects_agent_fakes import fake_gateway
        from tests.unit.test_projects_create_vocab import THREE, UUID, _gateway

        _framed_card(monkeypatch, _all_rows)
        fake_gateway(monkeypatch, _gateway(forbid=("vip",) if refuse else ()))
        with artifact_context_scope():
            bind_artifact_context(agent_name="projects-assistant", no_egress=False)
            return await skill_projects.create_tags(UUID, THREE)
    return build


def _reminder(head: str, hint_name: str, row: dict[str, Any] | None = None) -> Any:
    async def build(_mp: Any) -> str:
        from skill_projects import writes

        saved = row if row is not None else {
            "id": "r1", "title": "Call the failed vendor", "state": "pending",
            "kind": "action", "fire_at": "2099-10-30T11:30:00+00:00", "tz": "Asia/Kolkata"}
        return writes._reminder_receipt(head, saved, getattr(writes, hint_name), undo="drop")
    return build


def _send_later(monkeypatch: Any, *, refuse: bool) -> Any:
    """The real ``schedule_send`` of the email agent, behind an approved card.
    The route answers, or says that send later is not on (a 404)."""
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
    return agents.schedule_send


def _email(*, refuse: bool) -> Any:
    async def build(monkeypatch: Any) -> str:
        return await _send_later(monkeypatch, refuse=refuse)(
            BOX, "d1", "2099-01-05T09:00:00+05:30")
    return build


async def _held_forward_refused(_mp: Any) -> str:
    """The real answer of a held forward card that the route refused after
    the approve (``_held_refusal``). Only its first word says "not done"."""
    agents = _agent(EMAIL_AGENT, "park_line_email")
    exc = agents.GatewayError("conflict", 409, detail="The email moved after the card showed it.")
    return agents._held_refusal(exc, "Not forwarded")


def _crm_move(deal: str, *, mirror: bool = False) -> Any:
    """The real ``update_deal_status`` of the CRM agent, behind an approved card."""
    async def build(monkeypatch: Any) -> str:
        agents = _agent(CRM_AGENT, "park_line_crm")

        async def get(path: str, params: Any = None) -> Any:
            if path == "/crm/statuses/deal":
                return [{"id": "s2", "name": "Won", "type": "won"}]
            return {"id": "d1", "name": deal}

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
        return await agents.update_deal_status("b0000000-0000-4000-8000-000000000002", "Won")
    return build


def _crm_constant(name: str) -> Any:
    async def build(_mp: Any) -> str:
        return getattr(_agent(CRM_AGENT, "park_line_crm"), name)
    return build


def _studio(kind: str) -> Any:
    """The one result shape of the Studio tools (``studio_tools._result``)."""
    async def build(_mp: Any) -> Any:
        import json

        from acb_skills import studio_tools as st

        if kind == "saved":
            return st._result(True, [], ["+ objects.orders"], {"name": "Orders"},
                              decision="approved")
        if kind == "publish_422":  # ``_held_publish`` and ``_package_fail``
            return st._package_fail(st._Reply(422, {"detail": {
                "message": "The draft is not valid.",
                "errors": [{"path": "objects.0", "message": "A name is required."}]}}),
                "published", decision="approved")
        if kind == "publish_409":
            return st._refusal(st._PKG_MOVED.format(act="published"), decision="approved")
        if kind.endswith("_text"):
            return json.dumps(await _studio(kind.removesuffix("_text"))(_mp))
        raise AssertionError(kind)
    return build


def _value(value: Any) -> Any:
    async def build(_mp: Any) -> Any:
        return value
    return build


def _outcome(outcome: str) -> Any:
    async def build(_mp: Any) -> str:
        return ask_tools.card_outcome_text(outcome)
    return build


#: ``(name, builder, not run)``. True: the line must NOT name the act.
REAL_RESULTS: list[tuple[str, Any, bool]] = [
    # Projects, through the real tool or the real helper.
    ("projects batch, 3 made", _batch(), False),
    ("projects batch, 2 made and 1 refused", _batch(refuse=("Print the badges",)), True),
    ("projects batch, 1 row unticked", _batch(ticked=lambda _kw: frozenset({"row-1", "row-3"})),
     True),
    ("projects create, a mark in the title", _single_create, False),
    ("projects create, assignees NOT saved", _create_stopped, True),
    ("projects batch, a forged row", _forged, True),
    ("projects tags, 3 added", _tags(refuse=False), False),
    ("projects tags, 2 added and 1 refused", _tags(refuse=True), True),
    ("reminder set", _reminder("Reminder set", "_RECEIPT_CARD"), False),
    ("reminder already set", _reminder("Reminder already set", "_ALREADY_SET"), True),
    ("reminder with no id from the server", _reminder("Reminder set", "_RECEIPT_CARD", row={}), True),
    # Email: schedule_send through the real tool.
    ("email send later, scheduled", _email(refuse=False), False),
    ("email send later, not on (P1-1 A)", _email(refuse=True), True),
    ("email held forward, refused (409)", _held_forward_refused, True),
    # CRM: update_deal_status through the real tool, and the real constants.
    ("crm move, done", _crm_move("Acme renewal"), False),
    ("crm move, mirror refused", _crm_move("Acme renewal", mirror=True), True),
    ("crm pipelines off", _crm_constant("_PIPELINES_OFF"), True),
    ("crm plan changed", _crm_constant("_PLAN_CHANGED"), True),
    ("crm feed changed", _crm_constant("_FEED_CHANGED"), True),
    # Studio: the one result shape, as a mapping and as JSON text.
    ("studio save, ok", _studio("saved"), False),
    ("studio save, ok, as JSON text", _studio("saved_text"), False),
    ("studio held publish, 422 (P1-1 B)", _studio("publish_422"), True),
    ("studio held publish, 422, as JSON text", _studio("publish_422_text"), True),
    ("studio held publish, draft moved (409)", _studio("publish_409"), True),
    # Shapes that give no proof.
    ("no result", _value(None), True),
    ("an empty text", _value("   "), True),
    ("a list", _value(["Created."]), True),
    ("a mapping with no ok", _value({"id": "x"}), True),
    ("a JSON text that does not parse", _value('{"ok": tru'), True),
    ("a not-run tag", _value("[not run: the gate held the call]"), True),
    # The card outcomes of the one table: none of them ran.
    *[(f"card outcome {k}", _outcome(k), True)
      for k in ask_tools.CARD_OUTCOMES if k != CARD_APPROVED],
]


@pytest.mark.parametrize(("name", "build", "not_run"), REAL_RESULTS,
                         ids=[r[0] for r in REAL_RESULTS])
def test_each_real_result_has_its_verdict(name: str, build: Any, not_run: bool, monkeypatch):
    result = asyncio.run(build(monkeypatch))
    assert ask_tools.result_says_not_run(result) is not_run, (name, result)


def test_the_real_batch_receipt_has_no_mark_outside_its_fences(monkeypatch):
    """Review P1-2 (b): the hint of a full batch was "To retry a failed row",
    so each full batch read as not run. The hint now holds no mark."""
    receipt = asyncio.run(_batch()(monkeypatch))
    assert receipt.startswith("Created 3 tasks in «Ops»:"), receipt
    assert "To retry a row that is not in this list, call create_tasks" in receipt
    src = (ROOT / "apps/skills/skill-projects/skill_projects/forms.py").read_text(encoding="utf-8")
    assert "retry a failed row" not in src


def test_a_send_later_that_is_not_on_is_not_named(monkeypatch):
    """Review P1-1 (A): a plain card was approved, the route said that send
    later is not on, and nothing was scheduled. The line names nothing."""

    async def body() -> None:
        tool = _tool_injection._gate_injected_tool(_send_later(monkeypatch, refuse=True))
        out = await tool(BOX, "d1", "2099-01-05T09:00:00+05:30")
        assert out.startswith("Not scheduled. Send later is not on"), out

    assert _acts_of(body) == ""


def test_a_held_publish_that_failed_is_not_named():
    """Review P1-1 (B): ``studio_publish`` in a held chat answers
    ``{"ok": False, ...}`` after a 422. The old check read ``str(result)``."""

    async def body() -> None:
        result = await _studio("publish_422")(None)
        tool = _scripted("studio_publish", "Publish the app «Orders»?", CARD_APPROVED, result)
        assert (await tool())["ok"] is False

    assert _acts_of(body) == ""


def test_a_full_batch_is_named_beside_the_project(monkeypatch):
    """Review P1-2 (c), the incident of run dd49a886: a project, then three
    tasks on one card. Both acts are named. A partial batch is NOT named:
    fail safe, so the line never claims a task that was not made."""
    import skill_projects

    def run(refuse: tuple[str, ...]) -> tuple[str, str]:
        receipts: list[str] = []

        async def body() -> None:
            await _scripted("create_project", "Create this project?", CARD_APPROVED)()
            batch = _tool_injection._gate_injected_tool(skill_projects.create_tasks)
            receipts.append(await _create_tasks(monkeypatch, batch, refuse=refuse))

        return _acts_of(body), receipts[0]

    line, receipt = run(())
    assert receipt.startswith("Created 3 tasks in «Ops»:"), receipt
    assert line == (
        "Approved and run in this reply: «create this project» and "
        f"«create 3 tasks in Ops». {STOP}")
    line, receipt = run(("Print the badges",))
    assert receipt.startswith("Created 2 of 3 tasks in «Ops»."), receipt
    assert line == f"Approved and run in this reply: «create this project». {STOP}"


@pytest.mark.parametrize(("result", "not_run"), [
    ("Created:\n- #12 «Fix failed payment» · status «To do»", False),
    ("Approved the knowledge entry 'Why the build failed' in «Ops».", False),
    ('Created lead "Not done yet Ltd" (id=7), owned by you.', False),
    ("Created:\n- #12 «Fix it» · the assignees failed", True),
    ("Created «Fix it. stopped: 1 of 2 failed", True),  # a fence with no end
    ("Couldn't move 'Acme': the route refused.", True),
    ("Note: you asked for High, and the task reads Low.", False),
])
def test_a_mark_inside_a_fence_or_a_quote_is_data(result: str, not_run: bool):
    """Review P1-2 (a): a mark inside « » or a quoted title is member data.
    A fence with no end stays, so its marks still count (fail safe). "Note"
    is no first word of a result that did nothing."""
    assert ask_tools.result_says_not_run(result) is not_run, result


def test_an_approved_plan_card_is_no_act(monkeypatch):
    """Review P2: the approve of the plan card "Build X?" only records a plan,
    so the line never names «build X». ``studio_save`` and
    ``studio_publish`` ask their own cards for the acts."""
    from acb_skills import studio_tools as st

    from tests.unit.test_studio_builder_tools import _serve, _valid

    _serve(monkeypatch)

    async def kept(_doc: Any) -> bool:
        return True

    monkeypatch.setattr(st, "_record_plan", kept)
    titles = _framed_card(monkeypatch)

    async def body() -> None:
        manifest = await _valid()
        out = await _tool_injection._gate_injected_tool(st.studio_plan)(manifest)
        assert out["ok"] is True and out["decision"] == "approved", out

    assert _acts_of(body) == ""
    assert titles and titles[0].startswith("Build "), titles
