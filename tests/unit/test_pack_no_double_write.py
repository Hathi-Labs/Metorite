"""WS-67 OA-8i: no double write between a pack and its specialist in one turn.

``project-docs/specs/one_assistant_context.md`` §4.3.2, "As built, OA-8i".
OA-8e run 4, ``mix_task_from_mail`` turn 2 (repeats 2 and 3): the run held
``email`` and ``projects``, and the Projects specialist stayed. The one
assistant made the task with its own pack tool, then handed the same task to
``projects-assistant``, which made it again. Two cards, two writes.

The guard only refuses. Each case names, in its docstring, the mutation that
turns it red (R7).

1. Forward: a write tool of a pack, then a hand-off to its owner -> refused,
   on every hand-off seam. A cancelled card counts as a write.
2. A read-only tool, then a hand-off -> it runs.
3. Reverse: a hand-off, then a write tool of the owner's pack -> refused,
   while the specialist tool stays in the run. A hand-off that failed or
   timed out still refuses the write, with its own text.
4. One model step with both calls: exactly one runs, in either order.
5. Email and CRM parity. Both directions hold only while the specialist tool
   stays in the run, so the email setup hand-off stays open after a mail read.
6. The order of the answers: an older refusal keeps its text.
7. Flag off: no record, so nothing changes.

Run: ``uv run pytest tests/unit/test_pack_no_double_write.py -q``.
"""
from __future__ import annotations

import asyncio
import contextlib
import types
from collections.abc import Iterator
from typing import Any

import pytest

pytest.importorskip("agent_framework", reason="MAF not installed")

from acb_skills import skill_families as sf
from acb_skills import tool_annotations as ta
from acb_skills import tool_packs as tp
from orchestrator import _tool_injection as ti
from orchestrator import executor as ex

from tests.unit import test_loader_replaces_handoff as L
from tests.unit._egress_mark_store import install_memory_mark_store
from tests.unit._projects_agent_fakes import approve, deny, empty_list, fake_gateway
from tests.unit.test_tool_packs_choice import CONFIG, MEMBER, ORG, SESSION

PA = "projects-assistant"
EA = "email-assistant"
CRM = "crm-assistant"
PROJ = frozenset(("projects", "projects-plan", "projects-structure", "projects-bulk",
                  "projects-reminders"))
UUID = "0b0b0b0b-0000-4000-8000-0000000000b1"
#: The turn of OA-8e run 4 on the Tasks page. The surface loads the core
#: pack, and the plan and structure packs cannot join it, so the specialist
#: tool stays (the "Open gap" of §4.3.2).
WORDS = "Make a task from it"

_READ_ONLY = {"read_only": True, "destructive": False, "idempotent": True, "open_world": False}
_WRITE = {"read_only": False, "destructive": False, "idempotent": False, "open_world": False}


@pytest.fixture
def store(monkeypatch) -> Any:
    return install_memory_mark_store(monkeypatch)


@pytest.fixture(autouse=True)
def _facts_tools_stay(monkeypatch: pytest.MonkeyPatch) -> None:
    """The base of an org on ``CRM_QUESTIONS_ORGS``, as ``test_projects_packs``
    pins it. The plan and structure packs then do not fit beside the core
    pack, so the specialist stays."""
    monkeypatch.setattr(ti, "_facts_removed", ti._facts_withheld)


@pytest.fixture
def agents() -> list[Any]:
    return L._fresh()


async def _apply_on(agents: list[Any], words: str = WORDS, surface: str = "tasks",
                    allowed: frozenset[str] = PROJ) -> Any:
    """The real layer (a) of one member turn, as ``executor._apply_pack_turn``
    runs it."""
    from orchestrator.agents import PACK_DELEGATION_BULLETS

    with L._tenant():
        turn = await tp.begin_turn(ORG, SESSION, MEMBER, words, surface)
        return await ti._apply_tool_packs(
            agents, turn, L._run_ctx(), allowed=allowed, agent_name="orchestrator",
            tool_scope=list(CONFIG["tool_scope"]), agent_config=CONFIG,
            bullets=PACK_DELEGATION_BULLETS, offer_loader=True)


def _tool(agents: list[Any], name: str) -> Any:
    return next(t for t in agents[0].default_options["tools"] if ti._tool_name(t) == name)


def _posts(calls: list[dict]) -> list[dict]:
    return [c for c in calls if c["method"] == "POST"]


def _gateway(call: dict) -> Any:
    if call["method"] == "POST":
        return {"id": UUID, "title": (call.get("json") or {}).get("title", ""), "number": 7}
    return empty_list(call)


@contextlib.contextmanager
def _queue() -> Iterator[None]:
    """The run queue of a streamed chat run, so a specialist tool goes through
    the streaming seam (``executor._run_sub_agent_streaming``)."""
    token = ex._active_run_queue.set(asyncio.Queue())
    try:
        yield
    finally:
        ex._active_run_queue.reset(token)


async def _held_projects(agents: list[Any]) -> Any:
    """The run of the defect: it holds the core pack, the specialist stays,
    and the hand-off is open before any call."""
    applied = await _apply_on(agents)
    assert applied is not None and applied.record.slugs == ("projects",), "non-vacuity"
    assert PA not in applied.record.via_loader and PA not in applied.record.replaced
    assert "projects_assistant" in L._names(agents[0].default_options["tools"])
    assert type(_tool(agents, "create_personal_task")).__name__ == "_StrictTool"
    return applied


def _wrote(tool: str) -> str:
    return tp.WROTE_HANDOFF_REFUSED.format(tool=tool)


# ── 1. Forward: a pack write, then a hand-off ───────────────────────────────


async def test_a_task_made_by_the_pack_refuses_each_hand_off_to_the_specialist(
    store, agents, monkeypatch,
) -> None:
    """OA-8e run 4: ``create_personal_task``, then a hand-off of the same task.
    The hand-off answers the refusal on each seam: ``call_agent``, the
    streaming seam and the specialist tool. One card, one write, no sub-run.
    Mutation: drop the record of the write in ``PackRun.write_refusal`` ->
    each hand-off runs -> red. Mutation: drop the ``wrote_refusal`` step of
    ``PackRun.refusal`` -> red."""
    ran = L._fake_run_agent(monkeypatch)
    asked = approve(monkeypatch)
    monkeypatch.setattr("acb_skills.ask_tools.confirmation_channel_open", lambda: True)
    calls = fake_gateway(monkeypatch, _gateway)
    applied = await _held_projects(agents)
    make = _tool(agents, "create_personal_task")
    specialist = _tool(agents, "projects_assistant")
    with L._in_run(applied):
        assert tp.delegation_refusal(PA) is None, "non-vacuity: open before the write"
        made = await make.func(title="Confirm 250 boards for Priya")
        direct = await L.agent_tools.call_agent(PA, "Make a task from the mail of Priya")
        seam = await ex._run_sub_agent_streaming(PA, "Make a task", "r-sub", asyncio.Queue())
        with _queue():
            via_tool = await specialist.func(task="Make a task from the mail of Priya")
    want = _wrote("create_personal_task")
    assert str(made).startswith("Captured"), made
    assert direct == seam == via_tool == want
    assert ran == [] and len(asked) == 1 and len(_posts(calls)) == 1
    assert applied.record.wrote == {PA: "create_personal_task"} and applied.record.handed == set()
    assert "create_personal_task" in want and "projects" not in want.lower()


async def test_a_cancelled_card_still_refuses_the_hand_off(store, agents, monkeypatch) -> None:
    """The member cancels the card. The tool answers ``Cancelled`` as a plain
    result, so the seam cannot tell it from a write. The write is recorded
    before the gate, so the model cannot try the same work again through the
    specialist. Mutation: record the write after the call only when the
    answer is no cancel -> the hand-off runs -> red."""
    ran = L._fake_run_agent(monkeypatch)
    asked = deny(monkeypatch)
    monkeypatch.setattr("acb_skills.ask_tools.confirmation_channel_open", lambda: True)
    calls = fake_gateway(monkeypatch, _gateway)
    applied = await _held_projects(agents)
    with L._in_run(applied):
        made = await _tool(agents, "create_personal_task").func(title="Order the bearings")
        out = await L.agent_tools.call_agent(PA, "Make the task: order the bearings")
    assert str(made).startswith("Cancelled"), made
    assert len(asked) == 1 and _posts(calls) == []
    assert out == _wrote("create_personal_task") and ran == []


# ── 2. A read, then a hand-off ──────────────────────────────────────────────


async def test_a_read_only_call_leaves_the_hand_off_open(store, agents, monkeypatch) -> None:
    """``find_tasks`` is marked read-only in the risk registry, so it records
    nothing, and the hand-off runs. Mutation: count every pack call as a
    write (drop the ``annotated_read_only`` test) -> red."""
    ran = L._fake_run_agent(monkeypatch)
    fake_gateway(monkeypatch, _gateway)
    applied = await _held_projects(agents)
    assert ta.get_annotations("find_tasks")["read_only"] is True, "non-vacuity"
    with L._in_run(applied):
        await _tool(agents, "find_tasks").func(query="boards")
        out = await L.agent_tools.call_agent(PA, "Show the burndown of Website Relaunch")
    assert ran == [PA] and "ran" in out
    assert applied.record.wrote == {} and applied.record.handed == {PA}


# ── 3. Reverse: a hand-off, then a pack write ───────────────────────────────


async def test_a_pack_write_after_a_hand_off_to_its_owner_refuses(
    store, agents, monkeypatch,
) -> None:
    """The specialist had the work, and the specialist tool stays in the run.
    A write tool of its pack then answers the short refusal, shows no card and
    sends nothing. The hand-off stays open, so the step keeps a path. A read
    still runs. Mutation: drop the ``handed`` check of
    ``PackRun.write_refusal`` -> the second write lands -> red."""
    ran = L._fake_run_agent(monkeypatch)
    asked = approve(monkeypatch)
    monkeypatch.setattr("acb_skills.ask_tools.confirmation_channel_open", lambda: True)
    calls = fake_gateway(monkeypatch, _gateway)
    applied = await _held_projects(agents)
    with L._in_run(applied):
        handed = await L.agent_tools.call_agent(PA, "Make a task from the mail of Priya")
        made = await _tool(agents, "create_personal_task").func(title="Confirm 250 boards")
        read = await _tool(agents, "find_tasks").func(query="boards")
        again = await L.agent_tools.call_agent(PA, "Also set its due date to Friday")
    assert "ran" in handed and ran == [PA, PA] and "ran" in again
    assert made == tp.HANDED_WRITE_REFUSED.format(tool="create_personal_task")
    assert asked == [] and _posts(calls) == [] and not str(read).startswith("Not run")
    assert applied.record.wrote == {} and applied.record.handed == {PA}


def _failed(tool: str = "create_personal_task") -> str:
    return tp.HANDED_FAILED_WRITE_REFUSED.format(tool=tool)


async def test_a_failed_hand_off_on_the_streaming_seam_still_refuses_with_its_own_text(
    store, agents, monkeypatch,
) -> None:
    """The specialist tool goes through ``_run_sub_agent_streaming``, which
    records the hand-off before any work. Here the sub-run fails at its load.
    A failed specialist may have written part of the work, so the write still
    refuses. The answer says that the hand-off did not finish, so the model
    does not hand the step over again (OA-8i review, P2). Mutation: ask the
    seam with ``starts=False`` -> no record -> the write lands -> red.
    Mutation: drop the ``handed_failed`` branch of ``write_refusal`` -> the
    answer sends the model back to the failed hand-off -> red."""
    started: list[str] = []

    async def no_load(name: str, **_k: Any) -> Any:
        started.append(name)
        raise RuntimeError("the test stops the sub-run at the load")

    monkeypatch.setattr(ex, "_load_agent_off_loop", no_load)
    calls = fake_gateway(monkeypatch, _gateway)
    approve(monkeypatch)
    applied = await _held_projects(agents)
    with L._in_run(applied), _queue():
        out = await _tool(agents, "projects_assistant").func(task="Make a task from it")
        made = await _tool(agents, "create_personal_task").func(title="Confirm 250 boards")
    assert started == [PA], "non-vacuity: the hand-off passed its refusal"
    assert "failed" in str(out), out
    assert applied.record.handed == {PA} and applied.record.handed_failed == {PA}
    assert made == _failed() and _posts(calls) == []
    assert "did not finish" in made and "hand this step over" not in made


async def test_a_timed_out_hand_off_counts_as_not_finished(store, agents, monkeypatch) -> None:
    """``call_agent`` stops a slow sub-run at its time limit. The sub-run may
    have written already, so the write refuses, with the text of a hand-off
    that did not finish. Mutation: record the end of the hand-off only in
    the ``except`` of the seam, not in its ``finally`` -> a cancel records
    nothing -> red."""

    async def slow_load(_name: str, **_k: Any) -> Any:
        await asyncio.sleep(5)

    monkeypatch.setattr(ex, "_load_agent_off_loop", slow_load)
    monkeypatch.setattr(L.agent_tools, "_SUB_AGENT_TIMEOUT", 0.05)
    calls = fake_gateway(monkeypatch, _gateway)
    approve(monkeypatch)
    applied = await _held_projects(agents)
    with L._in_run(applied), _queue():
        out = await L.agent_tools.call_agent(PA, "Make a task from the mail of Priya")
        made = await _tool(agents, "create_personal_task").func(title="Confirm 250 boards")
    assert "timed out" in str(out), out
    assert applied.record.handed_failed == {PA} and made == _failed()
    assert _posts(calls) == []


def test_a_hand_off_that_finished_keeps_the_plain_answer() -> None:
    """Only a hand-off that did not finish changes the answer, and only an
    owner that a hand-off reached is recorded. Mutation: record every end as
    failed -> red."""
    some = _record(("projects",))
    some.handoff_ended(PA, finished=False)
    assert some.handed_failed == set(), "no hand-off started"
    some.handoff_starts(PA)
    tp.handoff_ended(PA, {tp.PACKS_KEY: some}, finished=True)
    assert some.write_refusal(PA, "create_task") == tp.HANDED_WRITE_REFUSED.format(
        tool="create_task")
    tp.handoff_ended(PA, {tp.PACKS_KEY: some}, finished=False)
    assert some.write_refusal(PA, "create_task") == _failed("create_task")
    tp.handoff_ended(PA, None, finished=False)  # no record: nothing, and no raise


async def test_a_stop_before_the_sub_run_starts_still_counts_as_not_finished(
    store, agents, monkeypatch,
) -> None:
    """The seam records the hand-off, then awaits the mail mark and the
    working set before its work. A time-out there must still reach the
    ``finally``, so the record learns that the hand-off did not finish.
    Mutation: move the two awaits back before the ``try`` -> the cancel skips
    the ``finally`` -> ``handed_failed`` stays empty -> red."""
    loads: list[str] = []

    async def slow_set(*_a: Any, **_k: Any) -> Any:
        await asyncio.sleep(5)
        return None, ""

    async def load(name: str, **_k: Any) -> Any:
        loads.append(name)
        raise AssertionError("the sub-run must not reach the load")

    monkeypatch.setattr(ex, "_email_working_set_for", slow_set)
    monkeypatch.setattr(ex, "_load_agent_off_loop", load)
    monkeypatch.setattr(L.agent_tools, "_SUB_AGENT_TIMEOUT", 0.05)
    fake_gateway(monkeypatch, _gateway)
    approve(monkeypatch)
    applied = await _held_projects(agents)
    with L._in_run(applied), _queue():
        out = await L.agent_tools.call_agent(PA, "Make a task from the mail of Priya")
        made = await _tool(agents, "create_personal_task").func(title="Confirm 250 boards")
    assert "timed out" in str(out) and loads == []
    assert applied.record.handed_failed == {PA} and made == _failed()


# ── 3a. A pack tool that writes only a file of the chat ─────────────────────


def test_the_chat_file_tools_equal_the_projects_chat_file_writers() -> None:
    """``CHAT_FILE_TOOLS`` cannot drift. It equals ``CHAT_FILE_WRITERS`` of the
    Projects coverage fence, and the class A Projects tools that the risk
    registry does not mark read-only, and each one is a pack tool. A new tool
    that writes only a chat file must join both. Mutation: drop
    ``export_tasks`` from ``CHAT_FILE_TOOLS`` -> red."""
    import skill_projects
    from skill_projects import manifest as m

    from tests.unit.test_projects_chat_coverage import CHAT_FILE_WRITERS

    assert CHAT_FILE_WRITERS == tp.CHAT_FILE_TOOLS
    class_a_writes = {
        name for name in skill_projects.__all__
        if m.tool_class(name) == "A" and not ta.TOOL_ANNOTATIONS[name]["read_only"]}
    assert class_a_writes == tp.CHAT_FILE_TOOLS
    in_packs = {n for slug in sf.pack_slugs() for n in sf.SKILL_FAMILIES[slug]["tools"]}
    assert tp.CHAT_FILE_TOOLS <= in_packs


async def test_a_report_then_a_hand_off_stays_open(store, agents, monkeypatch) -> None:
    """On a Projects page, "show the stuck tasks" loads ``projects-plan`` in
    place of the core pack, and the specialist stays. The model draws
    ``status_report``, then routes a structure step to ``call_agent``. The
    report wrote only a chat file, so the hand-off runs, and nothing says
    that the report "already did this work". Mutation: drop the
    ``CHAT_FILE_TOOLS`` skip in ``pack_write_refusal`` -> red."""
    ran = L._fake_run_agent(monkeypatch)
    fake_gateway(monkeypatch, _gateway)
    applied = await _apply_on(agents, "Show the stuck tasks", "projects")
    assert applied is not None and applied.record.slugs == ("projects-plan",), "non-vacuity"
    assert PA not in applied.record.replaced
    assert ta.get_annotations("status_report")["read_only"] is False, "non-vacuity"
    with L._in_run(applied):
        report = await _tool(agents, "status_report").func(project_id="")
        out = await L.agent_tools.call_agent(PA, "Rename the status Doing to In progress")
    assert report != ti.PACK_CALL_REFUSED.format(tool="status_report"), "non-vacuity: it ran"
    assert not str(report).startswith("Not run"), report
    assert "ran" in out and ran == [PA]
    assert applied.record.wrote == {} and applied.record.handed == {PA}


# ── 4. One model step with both calls ───────────────────────────────────────


@pytest.mark.parametrize("write_first", [True, False], ids=["write-first", "hand-off-first"])
async def test_of_a_write_and_a_hand_off_in_one_step_exactly_one_runs(
    store, monkeypatch, write_first: bool,
) -> None:
    """MAF runs the calls of one model response at once (``asyncio.gather``).
    The check and the record have no await between them, so the call that
    starts first wins, and the other refuses. Mutation: put an await between
    the check and the record (record after ``_mark_pack_session``) -> both
    run -> red."""
    ran = L._fake_run_agent(monkeypatch)
    approve(monkeypatch)
    monkeypatch.setattr("acb_skills.ask_tools.confirmation_channel_open", lambda: True)
    calls = fake_gateway(monkeypatch, _gateway)
    agents = L._fresh()
    applied = await _held_projects(agents)
    make = _tool(agents, "create_personal_task")
    with L._in_run(applied):
        write = make.func(title="Confirm 250 boards")
        hand = L.agent_tools.call_agent(PA, "Make a task from the mail of Priya")
        if write_first:
            made, handed = await asyncio.gather(write, hand)
        else:
            handed, made = await asyncio.gather(hand, write)
    landed = len(_posts(calls))
    if write_first:
        assert landed == 1 and ran == [] and handed == _wrote("create_personal_task")
    else:
        assert landed == 0 and ran == [PA]
        assert made == tp.HANDED_WRITE_REFUSED.format(tool="create_personal_task")


# ── 5. Email and CRM parity ─────────────────────────────────────────────────


def _probe(name: str) -> Any:
    """A tool of a stub owner. Its own mark says it sends nothing off the
    platform, so no held build drops it. The OA-8i rule reads the risk
    registry by name, as ``child_may_hold`` does."""

    async def probe(email_id: str = "", query: str = "", deal_id: str = "") -> str:
        return f"{name} done"

    probe.__name__ = name
    probe.__tool_risk__ = dict(_READ_ONLY)  # type: ignore[attr-defined]
    return probe


def _owner_stub(monkeypatch, owner: str, names: tuple[str, ...]) -> None:
    stub = types.SimpleNamespace(_agent_tools=lambda: [_probe(n) for n in names])
    monkeypatch.setitem(ti._PACK_OWNER_MODULES, owner, stub)


@pytest.fixture
def email_owner(monkeypatch) -> None:
    """The email owner as probes. ``query_inbox`` keeps its real mark: no
    email tool says ``read_only=True`` (OA-9c). ``read_email`` is marked
    read-only here, to show that the mark decides and not the owner."""
    names = tuple(sf.SKILL_FAMILIES["email"]["tools"]) + tuple(sf.SKILL_FAMILIES["email-setup"]["tools"])
    _owner_stub(monkeypatch, EA, names)
    monkeypatch.setattr(ti, "_load_disabled_skill_families", lambda _name: frozenset())
    monkeypatch.setitem(ta.TOOL_ANNOTATIONS, "query_inbox", dict(_WRITE))
    monkeypatch.setitem(ta.TOOL_ANNOTATIONS, "read_email", dict(_READ_ONLY))
    for name in ("create_rule", "draft_reply"):
        monkeypatch.setitem(ta.TOOL_ANNOTATIONS, name, dict(_WRITE))


async def _email_only(agents: list[Any]) -> Any:
    """``email`` is held, and ``email-setup`` is off for the org, so the
    loader cannot reach the owner and the hand-off for setup work stays open
    (H-417). The specialist tool left with the pack."""
    applied = await _apply_on(agents, "check my inbox", "", frozenset({"email"}))
    assert applied is not None and applied.record.slugs == ("email",), "non-vacuity"
    assert EA not in applied.record.via_loader and EA in applied.record.replaced
    return applied


async def test_email_parity_the_setup_hand_off_stays_open_after_a_mail_read(
    store, agents, monkeypatch, email_owner,
) -> None:
    """OA-8i review, P1. "Find the newsletters in my inbox and make a rule
    that archives them": the model calls ``query_inbox`` from the pack, then
    hands the rule to the email assistant, as the email rules route it. No
    email tool says ``read_only=True``, so the read is recorded as a write.
    The email pack took the specialist tool away (the owner is in
    ``replaced``), so the hand-off carries only setup work that the run
    cannot hold, and it stays OPEN. A read-only mark records nothing.
    Mutation: drop the ``replaced`` check of ``PackRun.wrote_refusal`` -> the
    hand-off answers the refusal and no rule is made -> red."""
    ran = L._fake_run_agent(monkeypatch)
    applied = await _email_only(agents)
    reader, query = (_tool(agents, n) for n in ("read_email", "query_inbox"))
    with L._in_run(applied):
        await reader(email_id="m-1")
        assert applied.record.wrote == {} and tp.delegation_refusal(EA) is None
        await query(query="newsletters")
        assert applied.record.wrote == {EA: "query_inbox"}, "non-vacuity: a read counts"
        assert tp.delegation_refusal(EA) is None
        out = await L.agent_tools.call_agent(EA, "Make a rule that archives newsletters.")
    assert "ran" in out and ran == [EA]
    assert applied.record.handed == {EA}


async def test_email_parity_a_pack_call_after_a_setup_hand_off_still_runs(
    store, agents, monkeypatch, email_owner,
) -> None:
    """The reverse holds only while the specialist tool stays in the run. The
    email pack took that tool away, so the hand-off reached the owner only for
    setup work, as the rules route it. A mail call after it must run (OA-7i,
    acceptance 8). Mutation: drop the ``replaced`` check of
    ``PackRun.write_refusal`` -> the mail call refuses -> red."""
    ran = L._fake_run_agent(monkeypatch)
    applied = await _email_only(agents)
    with L._in_run(applied):
        out = await L.agent_tools.call_agent(EA, "Make a rule that files newsletters.")
        drafted = await _tool(agents, "draft_reply")(email_id="m-1")
    assert "ran" in out and ran == [EA] and drafted == "draft_reply done"
    assert applied.record.handed == {EA} and applied.record.wrote == {}


CRM_TOOLS = tuple(sf.SKILL_FAMILIES["crm-records"]["tools"])


async def test_crm_parity_the_write_is_recorded_and_the_pack_refusal_keeps_its_text(
    store, agents, monkeypatch,
) -> None:
    """``crm-records`` is the only CRM pack, so a run that holds it refuses
    the hand-off already. A write is recorded, and the older refusal keeps its
    text. A read records nothing. Mutation: put the OA-8i answer before the
    pack refusal in ``PackRun.refusal`` -> red."""
    _owner_stub(monkeypatch, CRM, CRM_TOOLS)
    monkeypatch.setattr(ti, "_load_disabled_skill_families", lambda _name: frozenset())

    async def native() -> str:
        return "native"

    monkeypatch.setattr("acb_skills.crm_tools.crm_mode", native)
    monkeypatch.setitem(ta.TOOL_ANNOTATIONS, "get_pipeline", dict(_READ_ONLY))
    monkeypatch.setitem(ta.TOOL_ANNOTATIONS, "log_activity", dict(_WRITE))
    applied = await _apply_on(agents, "log a call on the deal", "crm", frozenset({"crm-records"}))
    assert applied is not None and applied.record.slugs == ("crm-records",), "non-vacuity"
    with L._in_run(applied):
        await _tool(agents, "get_pipeline")()
        assert applied.record.wrote == {}
        await _tool(agents, "log_activity")(deal_id="d-1")
        refused = tp.delegation_refusal(CRM)
    assert applied.record.wrote == {CRM: "log_activity"}
    assert refused == tp.PACKS["crm-records"].delegation_refusal


# ── 6. The order of the answers ─────────────────────────────────────────────


def _record(slugs: tuple[str, ...], via: tuple[str, ...] = ()) -> tp.PackRun:
    turn = tp.PackTurn(ORG, SESSION, tp._member_part(MEMBER), 1, (), "", True)
    return tp.PackRun.of(turn, tp.Choice((), {}, {}), [(s, False) for s in slugs], via_loader=via)


def test_an_older_refusal_keeps_its_text_and_the_new_one_only_adds() -> None:
    """Every answer of OA-7c and OA-7i stays as it was. Only where the old
    rule gives None may OA-8i refuse. Mutation: return the OA-8i answer first
    -> red."""
    full = _record(tuple(PROJ))
    full.write_refusal(PA, "create_task")
    assert full.refusal(PA) == tp.PACKS["projects"].delegation_refusal
    reached = _record((), via=(PA,))
    reached.write_refusal(PA, "create_task")
    assert reached.refusal(PA) == tp.LOADER_HANDOFF_REFUSED.format(
        owner=PA, packs=", ".join(tp.owner_packs(PA)))
    some = _record(("projects",))
    assert some.refusal(PA) is None
    some.write_refusal(PA, "create_task")
    assert some.refusal(PA) == _wrote("create_task")
    assert some.refusal(EA) is None and some.refusal(CRM) is None


def test_an_ask_after_a_hand_off_ran_keeps_its_answer() -> None:
    """``call_agents_parallel`` asks the rule again after its branches ran, to
    frame each answer of the email owner. A hand-off that started must not
    read as refused then. Mutation: drop the ``handed`` check of
    ``wrote_refusal`` -> an answer of the email owner goes unframed -> red."""
    some = _record(("projects",))
    assert tp.delegation_refusal(PA, {tp.PACKS_KEY: some}, starts=True) is None
    assert some.handed == {PA}
    some.wrote[PA] = "create_task"  # a record that a fault could leave
    assert tp.delegation_refusal(PA, {tp.PACKS_KEY: some}) is None


def test_only_an_owner_of_a_pack_is_recorded_and_an_ask_records_nothing() -> None:
    """Mutation: record on every ask -> the plain ask records -> red."""
    some = _record(("projects",))
    assert tp.delegation_refusal(PA, {tp.PACKS_KEY: some}) is None
    assert some.handed == set()
    assert tp.delegation_refusal("web-researcher", {tp.PACKS_KEY: some}, starts=True) is None
    assert some.handed == set()


def test_the_texts_name_the_tool_and_no_other_assistant() -> None:
    """The house voice of the pack refusals: "Not run.", and no assistant
    named. Mutation: name the specialist in a text -> red."""
    for text in (tp.WROTE_HANDOFF_REFUSED, tp.HANDED_WRITE_REFUSED,
                 tp.HANDED_FAILED_WRITE_REFUSED):
        assert text.startswith("Not run.") and "{tool}" in text
        filled = text.format(tool="create_task")
        for owner in (PA, EA, CRM, "projects_assistant", "email_assistant", "agent"):
            assert owner not in filled, (owner, filled)


# ── 7. Flag off ─────────────────────────────────────────────────────────────


async def test_with_no_record_nothing_is_refused_or_recorded(monkeypatch) -> None:
    """With ``TOOL_PACKS`` unset the executor binds no record (OA-7c), so the
    rule has nothing to read. A spawn child has no record either. Mutation:
    create a record when none is bound -> red."""
    ran = L._fake_run_agent(monkeypatch)
    assert tp.packs_on(ORG) == frozenset(), "the flag is off in a unit test"
    with L._tenant(), L._bound(**L._run_ctx()):
        assert tp.pack_write_refusal(PA, "create_task") is None
        assert tp.delegation_refusal(PA, starts=True) is None
        out = await L.agent_tools.call_agent(PA, "Make a task")
        assert tp.pack_write_refusal(PA, "create_task") is None
    assert ran == [PA] and "ran" in out
    assert tp.pack_write_refusal(PA, "create_task", {tp.PACKS_KEY: None}) is None
