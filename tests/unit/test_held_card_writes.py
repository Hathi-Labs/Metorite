"""RX-1 (D169): a held chat keeps the writes that a card shows.

Owner directive of 2026-11-01. In one chat the owner read mail, sent mail and
then said "Remind me to call Rajendra". The chat answered that it held no tool
that creates anything, and that a new chat would do it. The rule of this slice:
keep the guard on what leaves Metorite, and keep a write that the member
approves on a server-built card.

``project-docs/work_plan.md`` D169, ``one_assistant_context.md`` §4.2.3, and
``acb_skills.egress.HELD_CARD_WRITE_TOOLS``. This file is the table of the rule.
Each case names, in its docstring, the mutation that turns it red (R7).

**What the code said before this slice.** Each Projects tool says
``open_world=False``, so a held Projects pack dropped nothing, class C included.
The two OTHER texts told the model that it held no write. So the refusal of
the owner's report came from the text, and the delete tools were in reach.

1. The table: the list, and each Projects tool in exactly one row.
2. Each listed tool stays, as the real tool object, and each dangerous tool
   stays off.
3. It fails closed: the mark alone, the list alone and a changed parameter
   list each keep nothing. A new write of a listed owner is off.
4. MCP: a tool of an MCP server never passes.
5. The card: each listed tool awaits its card door before it writes.
6. The pack path, the call in the same turn, and the delegated run.
7. The reader for a later write that acts at once.

Run: ``uv run pytest tests/unit/test_held_card_writes.py -q``.
"""
from __future__ import annotations

import ast
import inspect
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("agent_framework", reason="MAF not installed")
import skill_projects
from acb_skills import egress as eg
from acb_skills import tool_packs as tp
from acb_skills.skill_families import SKILL_FAMILIES
from agent_framework import Agent, FunctionTool
from orchestrator import _tool_injection as ti
from skill_projects import manifest

from tests.unit._crm_agent_fakes import load_agent_module as load_crm_module
from tests.unit._egress_mark_store import install_memory_mark_store
from tests.unit._projects_agent_fakes import approve, fake_gateway
from tests.unit._projects_agent_fakes import load_agent_module as load_projects_module
from tests.unit.test_tool_packs_choice import MEMBER, SESSION, _bound, _name, _run_ctx, _tenant

REPO = Path(__file__).resolve().parents[2]
SKILL_DIR = REPO / "apps" / "skills" / "skill-projects" / "skill_projects"
CRM_AGENT = REPO / "apps" / "agents" / "agent-crm" / "agents.py"
PA = "projects-assistant"
CRM = "crm-assistant"
PROJECT_PACKS = ("projects", "projects-plan", "projects-structure", "projects-bulk",
                 "projects-reminders", "projects-reports", "projects-authority")

# ── 1. The table ────────────────────────────────────────────────────────────

#: The Projects writes that a held chat keeps. Each is class B, and
#: ``promote_reminder`` is the one class C entry: the member's own private
#: record, by the owner's words.
KEPT_PROJECTS = frozenset({
    "add_subtasks", "add_task_ref", "assign", "capture_intake", "comment", "complete",
    "create_area", "create_field", "create_personal_task", "create_project",
    "create_status", "create_tag", "create_tags", "create_task", "create_tasks",
    "create_type", "create_types", "defer", "edit_comment", "edit_project", "edit_task",
    "file_tasks", "link_tasks", "link_tasks_batch", "mark_notifications_read",
    "move_task", "promote_reminder", "propose_plan", "remove_task_ref",
    "reopen_reminder", "report_save", "reschedule_reminder", "resolve_reminder",
    "save_view", "set_my_overlay", "set_recurrence", "set_reminder", "triage_intake",
    "unarchive_task", "unlink_tasks", "update_field", "update_project",
    "update_reminder", "update_status", "update_tag", "update_task", "update_tasks",
    "update_type", "watch",
})
#: The four CRM record writes. Each says ``open_world=True`` and keeps saying it.
KEPT_CRM = frozenset({"convert_lead", "create_lead", "log_activity", "update_deal_status"})

#: The Projects writes that a held chat does not hold: every class C tool but
#: ``promote_reminder``. A covered run still holds them. ``answer_decision``
#: (WS-ALN ALN-2a) is off too, until ALN-2b decides whether a held chat keeps it.
OFF_PROJECTS = frozenset({
    "add_report_recipient", "answer_decision", "archive_project", "archive_task",
    "attach_to_task",
    "bulk_update", "convert_folder_to_project", "delete_area", "delete_attachment",
    "delete_comment", "delete_field", "delete_project", "delete_status", "delete_tag",
    "delete_task", "delete_type", "delete_view", "grant_access", "merge_tags",
    "merge_tasks", "move_project", "nudge_task", "remove_report_recipient", "rename_area",
    "report_delete", "revert_activity", "revoke_access", "set_report_schedule",
    "set_status_set", "unarchive_project",
    # WS-ALN ALN-4, off in a held chat as every class C tool but promote_reminder.
    "pin_authority", "unpin_authority",
})

#: The class A Projects tools that write a file of the chat. They stay.
CHAT_FILES = frozenset({"export_tasks", "render_report", "status_report"})


def test_the_list_is_this_table() -> None:
    """Mutation: add a tool to the list -> red. It is the first mutation of the
    slice: an open-world tool on the list needs a person to change this table."""
    assert set(eg.HELD_CARD_WRITE_TOOLS) == KEPT_PROJECTS | KEPT_CRM


def test_each_projects_tool_is_in_exactly_one_row() -> None:
    """A new Projects write fails here until a person gives it a row. Each tool
    is a read, a chat file, a kept write or an off write. The classes of the
    route manifest and the rows agree."""
    tools = set(skill_projects.__all__)
    assert not (KEPT_PROJECTS & OFF_PROJECTS)
    reads = tools - KEPT_PROJECTS - OFF_PROJECTS - CHAT_FILES
    assert {manifest.tool_class(name) for name in reads} == {"A"}, sorted(reads)
    for name in reads:
        assert getattr(skill_projects, name).__tool_risk__["read_only"] is True, name
    assert {n for n in tools if manifest.tool_class(n) == "C"} == (
        OFF_PROJECTS | {"promote_reminder"})
    assert {n for n in tools if manifest.tool_class(n) == "B"} == (
        KEPT_PROJECTS - {"promote_reminder"})


@pytest.fixture(scope="module")
def projects_tools() -> dict[str, Any]:
    """The owner's own tool objects: the ``_StrictTool`` that a pack builds on."""
    return {_name(t): t for t in load_projects_module("rx1_projects")._agent_tools()}


@pytest.fixture(scope="module")
def crm_tools() -> dict[str, Any]:
    return {_name(t): t for t in load_crm_module("rx1_crm")._agent_tools()}


# ── 2. Each listed tool stays, and each dangerous tool stays off ────────────


@pytest.mark.parametrize("name", sorted(KEPT_PROJECTS))
def test_a_listed_projects_write_stays(name: str, projects_tools: dict[str, Any]) -> None:
    """Mutation: remove ``card_write`` from a kept tool -> red. Mutation: add a
    parameter to a kept tool -> red, because the pin no longer matches."""
    tool = projects_tools[name]
    assert eg.is_held_card_write(tool) is True
    assert eg.is_egress_tool(tool) is False
    assert eg.is_held_off_write(tool) is False
    assert eg.is_held_chat_off(tool) is False
    assert tool.func.__tool_risk__[eg.CARD_WRITE_KEY] is True


@pytest.mark.parametrize("name", sorted(KEPT_CRM))
def test_a_listed_crm_write_stays_and_keeps_its_true_annotation(
    name: str, crm_tools: dict[str, Any],
) -> None:
    """The list keeps a CRM write, and never a bare ``open_world=False``. D-CRM-9
    can queue the write for Zoho, so the annotation stays true. Mutation: say
    ``open_world=False`` on a CRM write -> red."""
    tool = crm_tools[name]
    assert tool.__tool_risk__["open_world"] is True
    assert eg.is_held_card_write(tool) is True
    assert eg.is_held_chat_off(tool) is False


@pytest.mark.parametrize("name", sorted(OFF_PROJECTS))
def test_a_class_c_projects_tool_stays_off_in_a_held_chat(
    name: str, projects_tools: dict[str, Any],
) -> None:
    """H-236 itself does not call it an egress tool, so a covered run keeps it.
    A held chat holds it off. Mutation: drop ``is_held_off_write`` from
    ``is_held_chat_off`` -> red."""
    tool = projects_tools[name]
    assert eg.is_held_card_write(tool) is False
    assert eg.is_egress_tool(tool) is False
    assert eg.is_held_off_write(tool) is True
    assert eg.is_held_chat_off(tool) is True


#: What leaves Metorite. The email sends of the card flows have their own pin.
LEAVES = ("fetch_page", "web_search", "run_workflow", "spawn_copilot_agent",
          "forward_email", "unsubscribe_sender", *sorted(eg.STORE_WRITES))


@pytest.mark.parametrize("name", LEAVES)
def test_what_leaves_metorite_stays_off(name: str, monkeypatch) -> None:
    """With ``EMAIL_HELD_CARD_SEND`` off, a forward and an unsubscribe stay off.
    Mutation: put a store write on the list -> red, because the list test and
    ``STORE_WRITES`` both name it, and the store write comes first."""
    from acb_common import get_settings

    monkeypatch.setattr(get_settings(), "email_held_card_send", False)
    assert eg.is_egress_tool(name) is True
    assert eg.is_held_chat_off(name) is True
    assert name not in eg.HELD_CARD_WRITE_TOOLS


def test_a_store_write_on_the_list_would_still_be_off(monkeypatch) -> None:
    """The order inside ``is_egress_tool``: the store writes come before the
    list. Mutation: swap the two checks -> red."""
    async def save_memory(text: str) -> str:
        return text

    save_memory.__tool_risk__ = {  # type: ignore[attr-defined]
        "open_world": False, eg.CARD_WRITE_KEY: True}
    monkeypatch.setitem(eg.HELD_CARD_WRITE_TOOLS, "save_memory", frozenset({"text"}))
    assert eg.is_egress_tool(save_memory) is True


@pytest.mark.parametrize("name", sorted(CHAT_FILES))
def test_a_chat_file_tool_stays(name: str, projects_tools: dict[str, Any]) -> None:
    """It writes only a file of the chat, so it is no write of the app. Mutation:
    drop ``CHAT_FILE_TOOLS`` from ``is_held_off_write`` -> red."""
    assert eg.is_held_chat_off(projects_tools[name]) is False


def test_a_read_of_a_listed_owner_stays(projects_tools, crm_tools) -> None:
    """Mutation: read ``read_only`` as False by default -> each read is off -> red."""
    for tools in (projects_tools, crm_tools):
        reads = [t for n, t in tools.items()
                 if getattr(getattr(t, "func", t), "__tool_risk__", {}).get("read_only")]
        assert reads
        assert not [_name(t) for t in reads if eg.is_held_chat_off(t)]


# ── 3. It fails closed ──────────────────────────────────────────────────────


def _writer(name: str, params: tuple[str, ...], **risk: Any) -> Any:
    """A coroutine function called *name*, with *params* as its parameters."""

    async def fn(**_kwargs: Any) -> str:
        return "ok"

    fn.__name__ = name
    fn.__signature__ = inspect.Signature([  # type: ignore[attr-defined]
        inspect.Parameter(p, inspect.Parameter.KEYWORD_ONLY) for p in params])
    fn.__tool_risk__ = {"read_only": False, **risk}  # type: ignore[attr-defined]
    return fn


REMINDER_PIN = tuple(eg.HELD_CARD_WRITE_TOOLS["set_reminder"])


def test_the_mark_alone_keeps_nothing() -> None:
    """A tool off the list stays off, with the mark. Mutation: read only the
    annotation in ``is_held_card_write`` -> red."""
    fn = _writer("delete_task", ("task_id", "subtasks_to_delete"),
                 open_world=False, **{eg.CARD_WRITE_KEY: True})
    assert eg.is_held_card_write(fn) is False
    assert eg.is_held_off_write(fn) is True


def test_the_list_alone_keeps_nothing() -> None:
    """A listed name with no mark stays off. Mutation: drop the mark test from
    ``is_held_card_write`` -> red. It is the card of the second mutation."""
    fn = _writer("set_reminder", REMINDER_PIN, open_world=False)
    assert eg.is_held_card_write(fn) is False
    assert eg.is_held_off_write(fn) is True


@pytest.mark.parametrize("params", [(*REMINDER_PIN, "notify_to"), REMINDER_PIN[:-1]],
                         ids=["a-new-parameter", "a-lost-parameter"])
def test_a_changed_parameter_list_breaks_the_pin(params: tuple[str, ...]) -> None:
    """Mutation: drop the pin compare -> red."""
    fn = _writer("set_reminder", params, open_world=False, **{eg.CARD_WRITE_KEY: True})
    assert eg.is_held_card_write(fn) is False
    assert eg.is_held_off_write(fn) is True


def test_an_open_world_tool_off_the_list_is_egress() -> None:
    """The mark does not widen H-236 for a tool that nobody listed. Mutation:
    treat the mark as enough in ``is_egress_tool`` -> red."""
    fn = _writer("export_contacts_to_zoho", ("record_id",),
                 open_world=True, **{eg.CARD_WRITE_KEY: True})
    assert eg.is_egress_tool(fn) is True


def test_a_new_write_of_a_listed_owner_is_off_with_a_bare_open_world_false(
    monkeypatch,
) -> None:
    """This is the point of the owner test. A bare ``open_world=False`` keeps a
    tool for H-236, and it keeps no write of a listed owner in a held chat.
    Mutation: drop ``HELD_WRITE_OWNERS`` from the rule -> red."""
    fn = _writer("share_project_link", ("project_id",), open_world=False)
    assert eg.is_held_off_write(fn) is False, "non-vacuity: not a tool of a listed owner yet"
    monkeypatch.setattr(eg, "_owner_tool_names", lambda: frozenset({"share_project_link"}))
    assert eg.is_held_off_write(fn) is True
    assert eg.is_egress_tool(fn) is False


def test_an_unannotated_tool_of_a_listed_owner_counts_as_a_write() -> None:
    """Mutation: treat no annotation as a read -> red."""

    async def my_work() -> str:
        return ""

    assert eg.is_held_off_write(my_work) is True


# ── 4. MCP: a tool of an MCP server never passes ────────────────────────────


def _mcp_tool(**props: Any) -> FunctionTool:
    fn = _writer("set_reminder", REMINDER_PIN, open_world=False, **{eg.CARD_WRITE_KEY: True})
    return FunctionTool(name="set_reminder", description="x", func=fn,
                        additional_properties=props, approval_mode="never_require")


@pytest.mark.parametrize("props", [{"_mcp_is_tool": True}, {eg.MCP_LOADER_KEY: True}],
                         ids=["mcp-tool", "mcp-loader"])
def test_an_mcp_tool_with_a_borrowed_name_and_mark_is_egress(props: dict[str, Any]) -> None:
    """The name, the mark and the pin all match, and the tool came from an MCP
    server. Mutation: drop the ``_from_mcp`` test from ``is_egress_tool`` or
    from ``is_held_card_write`` -> red."""
    tool = _mcp_tool(**props)
    assert eg._from_mcp(tool) is True
    assert eg.is_held_card_write(tool) is False
    assert eg.is_egress_tool(tool) is True
    assert eg.is_held_chat_off(tool) is True


def test_no_pack_tool_of_a_listed_owner_comes_from_mcp(projects_tools, crm_tools) -> None:
    """The finding of the slice, as a fence. Each Projects and CRM tool is native
    (``_StrictTool`` or a plain function). The rule has no exception for MCP
    because it needs none. Mutation: serve a pack tool through MCP -> red."""
    assert not [n for n, t in {**projects_tools, **crm_tools}.items() if eg._from_mcp(t)]


# ── 5. The card: each listed tool awaits its door before it writes ──────────

#: A form delegates its card to the class B tool that it wraps. A batch uses
#: ``_ask_batch``, and ``test_projects_agent_writes.py`` holds that as a caller
#: of ``_confirm``.
_DOORS = {"_confirm", "_ask_batch", "_ask_reminder", "update_task", "update_project",
          "request_confirmation"}
_WRITES = {"post", "patch", "put", "delete", "post_file", "_post", "_patch", "_put", "_delete"}


def _functions(path: Path) -> dict[str, ast.AsyncFunctionDef]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {n.name: n for n in tree.body if isinstance(n, ast.AsyncFunctionDef)}


def _skill_functions() -> dict[str, ast.AsyncFunctionDef]:
    found: dict[str, ast.AsyncFunctionDef] = {}
    for name in ("writes.py", "guarded.py", "forms.py", "inbox.py"):
        found.update(_functions(SKILL_DIR / name))
    return found


def _calls(fn: ast.AsyncFunctionDef, names: set[str]) -> list[int]:
    return sorted(
        node.lineno for node in ast.walk(fn)
        if isinstance(node, ast.Call) and getattr(node.func, "id", "") in names)


@pytest.mark.parametrize("name", sorted(KEPT_PROJECTS))
def test_a_kept_projects_write_awaits_its_card_before_any_write(name: str) -> None:
    """Mutation: remove the card from a kept tool -> red. A tool with no card
    is not on this list by the rule of D169."""
    fn = _skill_functions()[name]
    doors = _calls(fn, _DOORS)
    assert doors, f"{name} shows no card"
    writes = _calls(fn, _WRITES)
    assert not writes or doors[0] < writes[0], f"{name} writes before its card"


@pytest.mark.parametrize("name", sorted(KEPT_CRM))
def test_a_kept_crm_write_awaits_its_card_before_any_write(name: str) -> None:
    """Mutation: remove the card from a CRM write -> red."""
    fn = _functions(CRM_AGENT)[name]
    doors = _calls(fn, {"request_confirmation"})
    writes = _calls(fn, {"_post", "_patch"})
    assert doors and writes and doors[0] < writes[0], name


# ── 6. The pack path, the call in the same turn, and the delegated run ──────


@pytest.fixture
def store(monkeypatch) -> Any:
    return install_memory_mark_store(monkeypatch)


async def _build(slug: str, **over: Any) -> Any:
    ctx = {**_run_ctx(**over), tp.PACK_RUN_KEY: "rk-rx1"}
    with _tenant():
        return await ti._pack_tools(slug, ctx, holds=())


async def _mark_chat() -> None:
    with _tenant(), _bound(**_run_ctx()):
        assert await eg.mark_untrusted_answer()


async def test_a_chat_that_read_mail_builds_each_project_pack_with_the_table(store) -> None:
    """THE acceptance, at the seam the owner hit. The chat read mail, each pack
    builds held, each listed write is there, and each class C tool is not.
    Mutation: skip ``_is_held_off`` in ``_pack_tools`` -> ``delete_task`` stays
    -> red."""
    await _mark_chat()
    built: dict[str, Any] = {}
    for slug in PROJECT_PACKS:
        build = await _build(slug)
        assert build.held is True, slug
        built[slug] = build
    tools = {_name(t) for b in built.values() for t in b.tools}
    gone = {name for b in built.values() for name in b.dropped}
    assert gone == OFF_PROJECTS
    assert tools >= KEPT_PROJECTS and tools >= CHAT_FILES
    assert not tools & OFF_PROJECTS
    reminders = {_name(t) for t in built["projects-reminders"].tools}
    # The class C tool of this pack (the decision answer) is off in a held chat.
    assert reminders == set(SKILL_FAMILIES["projects-reminders"]["tools"]) - OFF_PROJECTS


async def test_a_held_load_of_the_crm_pack_keeps_the_nine_tools(store, monkeypatch) -> None:
    """Mutation: take the CRM writes off the list -> the held pack drops them -> red."""
    async def crm_mode() -> str:
        return "live"

    monkeypatch.setattr("acb_skills.crm_tools.crm_mode", crm_mode)
    await _mark_chat()
    build = await _build("crm-records")
    assert build.held is True and build.dropped == ()
    assert {_name(t) for t in build.tools} == set(SKILL_FAMILIES["crm-records"]["tools"])


def _answer_gateway(monkeypatch) -> list[dict[str, Any]]:
    def responder(call: dict[str, Any]) -> Any:
        path = call["path"]
        if path.endswith("/my/today"):
            return {"timezone": "Asia/Kolkata", "day_start_hour": 9}
        if path.endswith("/my/reminders") and call["method"] == "POST":
            return {"id": "11111111-1111-4111-8111-111111111111", "title": "Call Ravi",
                    "fire_at": "2026-11-02T11:30:00+00:00", "tz": "Asia/Kolkata",
                    "kind": "action", "status": "open"}
        return {"rows": [], "total": 0}

    return fake_gateway(monkeypatch, responder)


async def test_a_reminder_after_mail_in_the_same_turn_shows_its_one_card(
    store, monkeypatch,
) -> None:
    """The first call of the turn read mail, so the pack was built open and the
    chat is marked now. ``set_reminder`` still runs, held, behind its one card.
    Mutation: take ``set_reminder`` off the list -> the held answer -> red."""
    calls = _answer_gateway(monkeypatch)
    cards = approve(monkeypatch)
    monkeypatch.setattr("acb_skills.ask_tools.confirmation_channel_open", lambda: True)
    build = await _build("projects-reminders")
    assert build.held is False
    tool = next(t for t in build.tools if _name(t) == "set_reminder")
    await _mark_chat()
    with _tenant(), _bound(**_run_ctx(), **{tp.PACK_RUN_KEY: "rk-rx1"}):
        answer = await tool.func(title="Call Ravi", in_minutes=30)
    assert "off now" not in str(answer), answer
    assert len(cards) == 1, cards
    assert [c["path"] for c in calls if c["method"] == "POST"][-1].endswith("/my/reminders")


async def test_a_class_c_tool_after_mail_in_the_same_turn_answers_held(
    store, monkeypatch,
) -> None:
    """The pack was built open, so ``delete_task`` is on the list of the run. The
    chat read mail in this turn, so the call answers the held text. No card and
    no gateway call. Mutation: skip ``_is_held_off`` in ``_pack_call`` -> a card
    -> red."""
    calls = _answer_gateway(monkeypatch)
    cards = approve(monkeypatch)
    build = await _build("projects-bulk")
    tool = next(t for t in build.tools if _name(t) == "delete_task")
    await _mark_chat()
    with _tenant(), _bound(**_run_ctx(), **{tp.PACK_RUN_KEY: "rk-rx1"}):
        answer = await tool.func(task_id="11111111-1111-4111-8111-111111111111")
    assert answer == eg.EGRESS_HELD_PACK_ANSWER_OTHER.format(name="delete_task"), answer
    assert cards == [] and calls == []


def _agent_with(tools: list[Any]) -> Agent:
    from agent_framework.openai import OpenAIChatCompletionClient

    client = OpenAIChatCompletionClient(
        model="m", api_key="k", base_url="http://127.0.0.1:9/v1")
    return Agent(client=client, name="projects-assistant", instructions="x", tools=tools)


def test_a_held_chat_drops_class_c_from_a_delegated_run(monkeypatch, projects_tools) -> None:
    """A hand-off from a held chat must not reach what the pack dropped. The run
    is held and not covered. Mutation: skip the drop in
    ``_withhold_egress_from_agent`` -> ``delete_task`` stays -> red."""
    monkeypatch.setattr(ti, "_run_covered", lambda *_a, **_k: False)
    agents = [_agent_with([projects_tools["delete_task"], projects_tools["set_reminder"]])]
    ti._apply_no_egress(agents, PA)
    assert [_name(t) for t in agents[0].default_options["tools"]] == ["set_reminder"]


@pytest.mark.parametrize("name", sorted(KEPT_CRM))
def test_a_covered_chain_keeps_the_crm_writes_off(name: str, crm_tools) -> None:
    """H-236 holds a covered run for the data of a sandboxed turn, and D169 speaks
    of a chat that read mail. So a run under a covered run holds no CRM write, as
    it did. ``test_a_covered_parent_calls_crm_assistant_and_the_writes_are_gone``
    proves it through the executor. Mutation: drop ``covered_chain`` from
    ``is_egress_tool`` -> red."""
    tool = crm_tools[name]
    assert eg.is_egress_tool(tool) is False, "non-vacuity: a held chat keeps it"
    with _bound(**_run_ctx(no_egress=True, covered_chain=True)):
        assert eg.covered_chain() is True
        assert eg.is_egress_tool(tool) is True
        assert eg.is_held_chat_off(tool) is True


def test_only_an_explicit_true_is_a_chain() -> None:
    """Mutation: read any value as a chain -> a stray key takes a write away."""
    assert eg.covered_chain() is False
    with _bound(**_run_ctx(covered_chain="yes")):
        assert eg.covered_chain() is False


def test_the_chain_passes_down_from_each_kind_of_parent(monkeypatch) -> None:
    """The grandchild of a covered run reads its parent's key, and a child asks
    the cover of its parent again. Mutation: read only the agent's own cover ->
    the CRM child of a covered Projects run holds the writes -> red."""
    covered = {PA}
    monkeypatch.setattr(ti, "_run_covered", lambda agent, _org=None: agent in covered)
    assert ti._covered_chain(PA, {}) is True, "the covered run itself"
    assert ti._covered_chain(CRM, {"agent_name": PA}) is True, "its child"
    assert ti._covered_chain(CRM, {"agent_name": "email-assistant",
                                   eg.COVERED_CHAIN_KEY: True}) is True, "its grandchild"
    assert ti._covered_chain(CRM, {"agent_name": "orchestrator"}) is False
    assert ti._covered_chain(CRM, {}) is False


def test_a_covered_run_keeps_its_pinned_class_c_tools(monkeypatch, projects_tools) -> None:
    """H-236 did not change: a covered run with no mark keeps
    ``COVERED_PROJECTS_TOOLS``. Mutation: make ``_covered_hold`` False -> red."""
    monkeypatch.setattr(ti, "_run_covered", lambda *_a, **_k: True)
    agents = [_agent_with([projects_tools["delete_task"]])]
    with _bound(**{eg.CHAT_MARK_KEY: False}):
        ti._apply_no_egress(agents, PA)
    assert [_name(t) for t in agents[0].default_options["tools"]] == ["delete_task"]


def test_a_covered_run_in_a_chat_that_read_mail_drops_them(monkeypatch, projects_tools) -> None:
    """The mail is what the member can act on, so the chat rule wins. Mutation:
    drop the mark from ``_covered_hold`` -> red."""
    monkeypatch.setattr(ti, "_run_covered", lambda *_a, **_k: True)
    agents = [_agent_with([projects_tools["delete_task"]])]
    with _bound(**{eg.CHAT_MARK_KEY: True}):
        ti._apply_no_egress(agents, PA)
    assert agents[0].default_options["tools"] == []


def test_a_frame_with_no_bound_mark_drops_them(monkeypatch, projects_tools) -> None:
    """A path that bound no answer fails closed. Mutation: read a missing key as
    clear -> red."""
    monkeypatch.setattr(ti, "_run_covered", lambda *_a, **_k: True)
    agents = [_agent_with([projects_tools["delete_task"]])]
    with _bound(agent_name=PA):
        ti._apply_no_egress(agents, PA)
    assert agents[0].default_options["tools"] == []


async def test_a_mark_survives_a_restart(store) -> None:
    """The process cache is empty after a restart, and Redis keeps the mark. The
    run boundary reads Redis, so ``_covered_hold`` sees it. Mutation: read
    ``read_untrusted_answer`` at the boundary -> red."""
    await _mark_chat()
    eg._MARKED_SESSIONS.clear()
    ctx = _run_ctx()
    with _tenant(), _bound(**ctx):
        assert eg.read_untrusted_answer() is False, "non-vacuity: the cache is empty"
        assert await ti._chat_marked(ctx) is True


async def test_a_fault_of_the_mark_reads_as_marked(monkeypatch) -> None:
    """Mutation: read a fault as clear -> red."""

    class _Broken:
        async def exists(self, *_a: Any) -> int:
            raise ConnectionError("down")

    monkeypatch.setattr(eg, "get_tenant_redis", lambda: _Broken())
    ctx = _run_ctx(member="")
    with _tenant(), _bound(**ctx):
        assert await ti._chat_marked(ctx) is True


async def test_a_clear_chat_reads_clear(store) -> None:
    """Non-vacuity of the two above."""
    ctx = _run_ctx()
    with _tenant(), _bound(**ctx):
        assert await ti._chat_marked(ctx) is False


async def test_a_parent_mark_passes_down(store) -> None:
    """A child in a batch path holds a session of its own. Mutation: drop the
    parent's key -> red."""
    ctx = {"session_id": "child", eg.CHAT_MARK_KEY: True}
    with _tenant():
        assert await ti._chat_marked(ctx) is True


async def test_the_guard_refuses_a_class_c_call_that_joined_later(projects_tools) -> None:
    """The pair is the second layer of a delegated held run. Mutation: pass
    ``is_egress_tool`` alone to ``RefuseEgressTools`` -> the tool runs -> red."""
    class _Call:
        function = projects_tools["delete_task"]
        result: Any = None

    ran: list[bool] = []

    async def call_next() -> None:
        ran.append(True)

    call = _Call()
    with _bound(**_run_ctx(no_egress=True, agent_name=PA)):
        await eg.RefuseEgressTools().process(call, call_next)
    assert ran == [] and "off" in str(call.result)
    await eg.RefuseEgressTools(chat_hold=False).process(_Call(), call_next)
    assert ran == [True], "a covered run keeps the tool"


# ── 7. The reader for a later write that acts at once ───────────────────────


async def test_the_reader_keeps_the_card_in_a_held_run(store) -> None:
    """CF-6 lets a reminder write act at once. A held chat keeps that write
    because of its card, so the act-at-once path reads this first. Mutation: the
    reader answers False for a held run -> red."""
    with _tenant(), _bound(**_run_ctx(no_egress=True)):
        assert await eg.write_must_show_card() is True


async def test_the_reader_fails_closed_with_no_run() -> None:
    """Mutation: read an empty context as open -> red."""
    assert await eg.write_must_show_card() is True


async def test_the_reader_lets_an_open_run_act(store) -> None:
    """Non-vacuity: a clean chat may act at once."""
    with _tenant(), _bound(**_run_ctx(no_egress=False)):
        assert await eg.write_must_show_card() is False


async def test_the_reader_sees_a_mark_in_an_open_run(store) -> None:
    """An open run in a marked chat is not ``no_egress``, and the card stays.
    Mutation: read ``no_egress_for_this_run`` alone -> red."""
    await _mark_chat()
    with _tenant(), _bound(**_run_ctx(no_egress=False)):
        assert eg.no_egress_for_this_run() is False, "non-vacuity"
        assert await eg.write_must_show_card() is True


async def test_the_reader_keeps_the_card_when_redis_fails(monkeypatch) -> None:
    """A fault reads as marked. Mutation: pass ``fail_closed=False`` -> red."""
    class _Broken:
        async def exists(self, *_a: Any) -> int:
            raise ConnectionError("down")

    monkeypatch.setattr(eg, "get_tenant_redis", lambda: _Broken())
    with _tenant(), _bound(**_run_ctx(no_egress=False, member="")):
        assert await eg.write_must_show_card() is True


async def test_a_pack_call_reads_the_held_answer_through_the_scope(store) -> None:
    """The later seam reads the reader inside a pack call. ``owner_scope`` binds
    the held answer, so the reader sees it with no Redis read."""
    with _tenant(), _bound(**_run_ctx(no_egress=False)), tp.owner_scope(PA, True):
        assert await eg.write_must_show_card() is True


def test_the_reader_is_on_the_public_list() -> None:
    assert "write_must_show_card" in eg.__all__
    assert inspect.iscoroutinefunction(eg.write_must_show_card)


# ── 8. A top-level run in a marked chat (D169 item 3) ───────────────────────


def test_a_top_level_run_binds_no_egress_false_so_the_old_rule_missed_it(
    projects_tools,
) -> None:
    """The finding: a class C tool is no egress tool, so only the chat hold can
    take it from a run that binds ``no_egress=False``."""
    assert eg.is_egress_tool(projects_tools["delete_task"]) is False
    assert eg.is_held_off_write(projects_tools["delete_task"]) is True


def test_a_run_that_holds_a_class_c_tool_gets_the_chat_hold(projects_tools) -> None:
    """Mutation: drop ``_apply_chat_hold`` from the injection -> red."""
    agents = [_agent_with([projects_tools["delete_task"], projects_tools["set_reminder"]])]
    ti._apply_chat_hold(agents, PA)
    assert _chat_holds(agents[0])


def test_a_run_with_no_such_tool_keeps_its_agent_object(projects_tools) -> None:
    """Each other run keeps its object. Mutation: wrap each agent -> red."""
    agents = [_agent_with([projects_tools["set_reminder"]])]
    plain = agents[0]
    ti._apply_chat_hold(agents, PA)
    assert agents[0] is plain


def _chat_holds(agent: Any) -> list[Any]:
    return [p for p in getattr(agent, "context_providers", None) or []
            if isinstance(p, eg.ChatHoldProvider)]


@pytest.mark.parametrize(("marked", "off"), [(True, True), (False, False)])
def test_the_chat_hold_follows_the_mark(marked: bool, off: bool, projects_tools) -> None:
    """A clear chat keeps its tools. Mutation: drop the mark from ``_off`` -> red."""
    hold = eg.ChatHoldProvider(owner=PA)
    with _bound(**{eg.CHAT_MARK_KEY: marked}):
        assert hold._off(projects_tools["delete_task"]) is off
        assert hold._off(projects_tools["set_reminder"]) is False


def test_the_chat_hold_sees_a_mark_of_this_turn(projects_tools, monkeypatch) -> None:
    """The boundary read clear, then the turn read mail. Mutation: read only the
    bound answer -> red."""
    monkeypatch.setattr(eg, "read_untrusted_answer", lambda *_a: True)
    hold = eg.ChatHoldProvider(owner=PA)
    with _bound(**{eg.CHAT_MARK_KEY: False}):
        assert hold._off(projects_tools["delete_task"]) is True


async def test_the_chat_hold_refuses_a_call_in_words_of_the_chat(projects_tools) -> None:
    """The model names a tool that left. Mutation: drop the refuse half -> red."""
    class _Context:
        def __init__(self) -> None:
            self.items: list[Any] = []

        def extend_middleware(self, _source: str, items: list[Any]) -> None:
            self.items.extend(items)

    class _Call:
        function = projects_tools["delete_task"]
        result: Any = None

    context = _Context()
    await eg.ChatHoldProvider(owner=PA).before_run(
        agent=None, session=None, context=context, state={})
    ran: list[bool] = []

    async def call_next() -> None:
        ran.append(True)

    call = _Call()
    with _bound(**{eg.CHAT_MARK_KEY: True}):
        await context.items[1].process(call, call_next)
    assert ran == [] and "delete_task is off now" in call.result
    assert "new chat" not in call.result


# ── 9. A write of an owner that no pack family names (item 11) ─────────────


def test_each_write_of_the_crm_agent_is_in_the_table() -> None:
    """A row in ``test_crm_agent_write.py`` turned that fence green with no list
    entry. This one reads the agent. Mutation: add a CRM write off the list -> red."""
    tools = load_crm_module("rx1_crm_table")._agent_tools()
    from acb_skills.tool_annotations import TOOL_ANNOTATIONS

    wrote = {_name(t) for t in tools
             if not (TOOL_ANNOTATIONS.get(_name(t)) or {}).get("read_only")}
    assert wrote - eg.HELD_OWNER_CHAT_FILES == KEPT_CRM


def test_a_new_write_of_an_owner_outside_a_family_is_off_in_its_run() -> None:
    """The rule read the pack families only. Mutation: drop ``_own_tool_of`` -> red."""
    fn = _writer("merge_leads", ("a", "b"), open_world=False)
    assert eg.is_egress_tool(fn) is False, "non-vacuity: H-236 alone keeps it"
    assert eg.is_held_off_write(fn) is False, "non-vacuity: no family names it"
    assert eg.is_held_off_write(fn, owner=CRM) is True
    assert eg.is_held_chat_off(fn, owner=CRM) is True


def test_a_held_delegated_run_of_the_owner_drops_it(monkeypatch) -> None:
    """The seam of the finding. Mutation: drop ``owner`` from ``_apply_no_egress``
    -> the write stays -> red."""
    monkeypatch.setattr(ti, "_run_covered", lambda *_a, **_k: False)
    agents = [_agent_with([_writer("merge_leads", ("a",), open_world=False)])]
    with _bound(**{eg.CHAT_MARK_KEY: True}):
        ti._apply_no_egress(agents, CRM)
    assert agents[0].default_options["tools"] == []


def test_the_owner_rule_drops_no_tool_of_the_crm_agent_today(crm_tools, monkeypatch) -> None:
    """Each tool of the agent is a read or a listed write, so a held delegated run
    keeps all of them. Mutation: drop the read test -> the reads leave -> red."""
    monkeypatch.setattr(ti, "_run_covered", lambda *_a, **_k: False)
    agents = [_agent_with(list(crm_tools.values()))]
    with _bound(**{eg.CHAT_MARK_KEY: True}):
        ti._apply_no_egress(agents, CRM)
    assert {_name(t) for t in agents[0].default_options["tools"]} == set(crm_tools)


def test_the_owner_rule_drops_only_class_c_from_the_projects_agent(
    projects_tools, monkeypatch,
) -> None:
    """Mutation: count the chat file tools as writes -> red."""
    monkeypatch.setattr(ti, "_run_covered", lambda *_a, **_k: False)
    agents = [_agent_with(list(projects_tools.values()))]
    with _bound(**{eg.CHAT_MARK_KEY: True}):
        ti._apply_no_egress(agents, PA)
    kept = {_name(t) for t in agents[0].default_options["tools"]}
    assert set(projects_tools) - kept == OFF_PROJECTS


def test_the_owner_keeps_its_reads_and_the_platform_tools(monkeypatch) -> None:
    """A read stays, and so does a tool that the platform injected. Mutation: drop
    the platform test -> red."""
    assert eg.is_held_off_write(_writer("read_lead", ("a",), read_only=True), owner=CRM) is False
    fn = _writer("ask_member", ("a",))
    assert eg.is_held_off_write(fn, owner=CRM) is True, "non-vacuity: no annotation is a write"
    monkeypatch.setattr(eg, "_platform_owned", lambda *_a: True)
    assert eg.is_held_off_write(fn, owner=CRM) is False


def test_a_tool_of_another_owner_stays_out_of_the_rule() -> None:
    """Mutation: drop the owner test -> each run of each agent loses it -> red."""
    fn = _writer("my_own_write", ("a",), open_world=False)
    assert eg.is_held_off_write(fn, owner="email-assistant") is False


# ── 10. A bare name holds to the pin (item 12) ─────────────────────────────


def test_a_bare_name_holds_to_the_pin_that_the_tool_registered(
    monkeypatch, projects_tools, crm_tools,
) -> None:
    """``permission_policy._reaches_out`` passes a bare name, and the pin read
    nothing for it. Mutation: answer True for a name -> red."""
    from acb_skills import permission_policy as pp
    from acb_skills import tool_annotations as ta

    assert eg.is_held_card_write("create_lead") is True
    assert pp._reaches_out({}, "create_lead", None) is False
    assert {n for n in eg.HELD_CARD_WRITE_TOOLS if not eg.is_held_card_write(n)} == set(), (
        "each pin agrees with the parameters that the tool registered")
    monkeypatch.setitem(ta.CARD_WRITE_PARAMS, "create_lead", frozenset({"lead_name", "owner"}))
    assert eg.is_held_card_write("create_lead") is False
    assert pp._reaches_out({}, "create_lead", None) is True, "a drifted tool reaches out"


def test_a_name_that_no_tool_registered_keeps_nothing(monkeypatch) -> None:
    """Mutation: read a missing record as a match -> red."""
    from acb_skills import tool_annotations as ta

    monkeypatch.delitem(ta.CARD_WRITE_PARAMS, "create_lead")
    assert eg.is_held_card_write("create_lead") is False


# ── 11. The kill switch (item 13) ──────────────────────────────────────────


def _switch(monkeypatch, value: str) -> None:
    from acb_common import get_settings

    monkeypatch.setattr(get_settings(), "held_card_writes_orgs", value)


def test_the_switch_is_off_by_default() -> None:
    """So the merge changes nothing until a person flips it. Mutation: a default of
    ``*`` -> red."""
    from acb_common.settings import Settings

    assert Settings.model_fields["held_card_writes_orgs"].default == ""


def test_the_switch_turns_off_with_no_restart_and_no_tenant_writes_it() -> None:
    """Mutation: a name with no ``_orgs`` -> a SIGHUP applies nothing -> red."""
    from acb_common.env_guard import is_platform_env
    from gateway.flag_reload import reloadable

    assert reloadable("HELD_CARD_WRITES_ORGS")
    assert is_platform_env("HELD_CARD_WRITES_ORGS")
    example = (REPO / ".env.example").read_text(encoding="utf-8")
    assert "\nHELD_CARD_WRITES_ORGS=\n" in example.replace("\r\n", "\n")


@pytest.mark.parametrize(("value", "org", "on"), [
    ("", "org-a", False),
    ("*", "", True),
    ("org-a", "org-a", True),
    ("org-b,  ORG-A ", "org-a", True),
    ("org-b", "org-a", False),
    ("org-b", "", False),
    (" , ", "org-a", False),
])
def test_the_reader_of_the_switch(value: str, org: str, on: bool, monkeypatch) -> None:
    """Mutation: read a list as ``*`` -> red."""
    _switch(monkeypatch, value)
    if org:
        assert eg.held_card_writes_on(org) is on
        with _tenant(org):
            assert eg.held_card_writes_on() is on
    else:
        assert eg.held_card_writes_on() is on


def test_a_fault_of_the_settings_reads_off(monkeypatch) -> None:
    """It fails closed to the state before the slice. Mutation: read a fault as on
    -> red."""
    import acb_common

    def boom() -> Any:
        raise RuntimeError("settings")

    monkeypatch.setattr(acb_common, "get_settings", boom)
    assert eg.held_card_writes_on("org-a") is False


@pytest.mark.parametrize("name", sorted(KEPT_CRM))
def test_off_a_crm_write_leaves_a_held_run(name: str, crm_tools, monkeypatch) -> None:
    """Mutation: ignore the switch in ``is_held_card_write`` -> red."""
    assert eg.is_egress_tool(crm_tools[name]) is False, "non-vacuity: on, it stays"
    _switch(monkeypatch, "")
    assert eg.is_held_card_write(crm_tools[name]) is False
    assert eg.is_egress_tool(crm_tools[name]) is True


@pytest.mark.parametrize("name", ["delete_task", "grant_access", "set_reminder", "create_task"])
def test_off_a_projects_write_stays_as_it_did(name: str, projects_tools, monkeypatch) -> None:
    """Off is the state before the slice: a held pack dropped no Projects tool.
    Mutation: leave ``is_held_off_write`` on -> each write leaves -> red."""
    _switch(monkeypatch, "")
    assert eg.is_held_off_write(projects_tools[name]) is False
    assert eg.is_held_chat_off(projects_tools[name]) is False


async def test_off_the_held_read_of_a_tool_is_open(monkeypatch) -> None:
    """Mutation: leave ``chat_is_held`` on -> a refusal of the old flow -> red."""
    _switch(monkeypatch, "")
    with _tenant(), _bound(**_run_ctx(no_egress=True)):
        assert await eg.chat_is_held() is False


async def test_on_the_held_read_of_a_tool_is_held(store) -> None:
    """Non-vacuity of the case above, with no fake."""
    with _tenant(), _bound(**_run_ctx(no_egress=True)):
        assert await eg.chat_is_held() is True


async def test_a_marked_top_level_run_is_held(store) -> None:
    """Mutation: read ``no_egress`` alone -> red."""
    await _mark_chat()
    with _tenant(), _bound(**_run_ctx(no_egress=False)):
        assert await eg.chat_is_held() is True


async def test_a_covered_chain_with_no_mark_is_not_held(store) -> None:
    """D169 item 5: a covered run keeps H-236 as it was. Mutation: drop the chain
    test -> red."""
    with _tenant(), _bound(**_run_ctx(no_egress=True, covered_chain=True)):
        assert await eg.chat_is_held() is False


def test_off_the_note_and_the_answer_are_the_old_words(monkeypatch) -> None:
    """Mutation: ignore the switch in the two choosers -> red."""
    _switch(monkeypatch, "")
    ctx = _run_ctx(**{eg.PACK_CALL_KEY: True, "agent_name": PA})
    monkeypatch.setattr(eg, "hold_is_for_mail", lambda *_a: True)
    assert eg.egress_hold_note(ctx) == eg.EGRESS_HOLD_NOTE_PACK_NO_WRITES
    assert eg.egress_held_answer("x", ctx) == eg.EGRESS_HELD_PACK_ANSWER_NO_WRITES.format(name="x")
    assert "new chat" in eg.EGRESS_HOLD_NOTE_PACK_NO_WRITES, "the text of before the slice"
    _switch(monkeypatch, "*")
    assert eg.egress_hold_note(ctx) == eg.EGRESS_HOLD_NOTE_PACK_OTHER


async def test_off_the_boundary_asks_redis_nothing(monkeypatch) -> None:
    """The merge adds no Redis call to a run. Mutation: read first -> red."""

    class _Trap:
        async def exists(self, *_a: Any) -> int:
            raise AssertionError("no read with the switch off")

    monkeypatch.setattr(eg, "get_tenant_redis", lambda: _Trap())
    _switch(monkeypatch, "")
    ctx = _run_ctx()
    with _tenant(), _bound(**ctx):
        assert await ti._chat_marked(ctx) is False


async def test_off_a_held_run_reads_no_crm_mode(monkeypatch, crm_tools) -> None:
    """With the switch off a held run reads nothing, as before the slice. Mutation:
    drop the switch from ``_drop_mirror_record_writes`` -> a read -> red."""
    from acb_skills import crm_tools as crm

    async def trap() -> str:
        raise AssertionError("no read with the switch off")

    monkeypatch.setattr(crm, "crm_mode", trap)
    _switch(monkeypatch, "")
    agents = [_agent_with([crm_tools["create_lead"]])]
    with _bound(no_egress=True):
        await ti._drop_mirror_record_writes(agents)
    assert [_name(t) for t in agents[0].default_options["tools"]] == ["create_lead"]


def test_off_no_run_gets_the_chat_hold(monkeypatch, projects_tools) -> None:
    """Mutation: ignore the switch in ``_apply_chat_hold`` -> red."""
    _switch(monkeypatch, "")
    agents = [_agent_with([projects_tools["delete_task"]])]
    plain = agents[0]
    ti._apply_chat_hold(agents, PA)
    assert agents[0] is plain


# ── The texts reach the model ───────────────────────────────────────────────


async def test_the_rules_of_a_held_reminder_pack_say_that_the_write_works(store) -> None:
    """The note of a held Projects pack goes after its rules, and the model reads
    that it may call ``set_reminder``. Mutation: use the old note -> red."""
    await _mark_chat()
    ctx = {"session_id": SESSION, "agent_name": PA, "member": MEMBER}
    with _tenant():
        block = tp._rules_block(tp.PACKS["projects-reminders"], True,
                                ("projects-reminders",), (), ctx)
    assert block.endswith(eg.EGRESS_HOLD_NOTE_PACK_OTHER)
    assert "new chat" not in block


# ── 12. WT-6a: the class C archive behind HELD_CLASS_C_ORGS ────────────────
#
# one_assistant_context.md §9.4, owner decision of 2026-11-07. Only the two
# archive tools join, behind their own flag. HELD_CARD_WRITES_ORGS alone never
# opens class C. The receipt offers Undo, which the browser runs.

CLASS_C_KEPT = frozenset({"archive_task", "archive_project"})


def _class_c(monkeypatch, value: str) -> None:
    from acb_common import get_settings

    monkeypatch.setattr(get_settings(), "held_class_c_orgs", value)


def test_the_class_c_set_is_the_two_archive_tools() -> None:
    """Mutation: add ``delete_task`` or ``merge_tasks`` to the set -> red. A delete,
    a merge, a move of a project and a revert stay off until a restore exists."""
    assert set(eg.HELD_CLASS_C_TOOLS) == CLASS_C_KEPT
    assert not set(eg.HELD_CLASS_C_TOOLS) & set(eg.HELD_CARD_WRITE_TOOLS)
    assert CLASS_C_KEPT <= OFF_PROJECTS
    assert {manifest.tool_class(n) for n in CLASS_C_KEPT} == {"C"}


def test_the_class_c_flag_is_off_by_default() -> None:
    """Mutation: a default of ``*`` -> red."""
    from acb_common.settings import Settings

    assert Settings.model_fields["held_class_c_orgs"].default == ""


def test_the_class_c_flag_reloads_and_no_tenant_writes_it() -> None:
    """Mutation: a name with no ``_orgs`` -> a SIGHUP applies nothing -> red."""
    from acb_common.env_guard import is_platform_env
    from gateway.flag_reload import reloadable

    assert reloadable("HELD_CLASS_C_ORGS")
    assert is_platform_env("HELD_CLASS_C_ORGS")
    example = (REPO / ".env.example").read_text(encoding="utf-8")
    assert "\nHELD_CLASS_C_ORGS=\n" in example.replace("\r\n", "\n")


@pytest.mark.parametrize("name", sorted(CLASS_C_KEPT))
def test_flag_off_an_archive_is_refused_in_a_held_chat(
    name: str, projects_tools: dict[str, Any], monkeypatch,
) -> None:
    """``HELD_CARD_WRITES_ORGS`` is on (``*``) and the class C flag is empty.
    Mutation: read ``HELD_CARD_WRITES_ORGS`` in place of the class C flag -> red."""
    _class_c(monkeypatch, "")
    tool = projects_tools[name]
    assert tool.func.__tool_risk__[eg.CARD_WRITE_KEY] is True, "non-vacuity: it is marked"
    assert eg.is_held_card_write(tool) is False
    assert eg.is_held_off_write(tool) is True
    assert eg.is_held_chat_off(tool) is True


@pytest.mark.parametrize("name", sorted(CLASS_C_KEPT))
def test_flag_on_for_the_org_an_archive_stays(
    name: str, projects_tools: dict[str, Any], monkeypatch,
) -> None:
    """Mutation: drop the class C set from ``is_held_card_write`` -> red."""
    from tests.unit.test_tool_packs_choice import ORG

    _class_c(monkeypatch, f"org-x, {ORG.upper()}")
    tool = projects_tools[name]
    with _tenant(ORG):
        assert eg.is_held_card_write(tool) is True
        assert eg.is_held_off_write(tool) is False
        assert eg.is_held_chat_off(tool) is False
        assert eg.is_egress_tool(tool) is False
    with _tenant("0a0a0a0a-0000-4000-8000-0000000000cc"):
        assert eg.is_held_chat_off(tool) is True, "another org stays off"


@pytest.mark.parametrize("name", sorted(CLASS_C_KEPT))
def test_the_class_c_flag_alone_opens_nothing(
    name: str, projects_tools: dict[str, Any], monkeypatch,
) -> None:
    """With ``HELD_CARD_WRITES_ORGS`` off the held rule is off, as before RX-1.
    The class C flag adds no write to that state. Mutation: read the class C
    flag alone in ``is_held_card_write`` -> red."""
    _switch(monkeypatch, "")
    _class_c(monkeypatch, "*")
    assert eg.is_held_card_write(projects_tools[name]) is False


def test_flag_on_each_other_class_c_tool_stays_off(projects_tools, monkeypatch) -> None:
    """Mutation: read every class C tool through the flag -> ``delete_task`` stays -> red."""
    _class_c(monkeypatch, "*")
    still_off = {n for n in OFF_PROJECTS - CLASS_C_KEPT if eg.is_held_chat_off(projects_tools[n])}
    assert still_off == OFF_PROJECTS - CLASS_C_KEPT
    for name in ("delete_task", "delete_project", "merge_tasks", "move_project",
                 "revert_activity", "unarchive_project"):
        assert name in still_off, name


def test_a_fault_of_the_class_c_settings_reads_off(monkeypatch) -> None:
    """Mutation: read a fault as on -> red."""
    import acb_common

    def boom() -> Any:
        raise RuntimeError("settings")

    monkeypatch.setattr(acb_common, "get_settings", boom)
    assert eg.held_class_c_on("org-a") is False
    assert eg.held_class_c_on() is False


@pytest.mark.parametrize(("value", "org", "on"), [
    ("", "org-a", False),
    ("*", "", True),
    ("org-a", "org-a", True),
    ("org-b,  ORG-A ", "org-a", True),
    ("org-b", "org-a", False),
    ("org-b", "", False),
    (" , ", "org-a", False),
])
def test_the_reader_of_the_class_c_flag(value: str, org: str, on: bool, monkeypatch) -> None:
    """Mutation: read a list as ``*`` -> red."""
    _class_c(monkeypatch, value)
    if org:
        assert eg.held_class_c_on(org) is on
        with _tenant(org):
            assert eg.held_class_c_on() is on
    else:
        assert eg.held_class_c_on() is on


def test_the_class_c_reader_does_not_read_the_class_b_flag(monkeypatch) -> None:
    """Mutation: fall back to ``held_card_writes_orgs`` -> red."""
    _switch(monkeypatch, "*")
    _class_c(monkeypatch, "")
    assert eg.held_class_c_on("org-a") is False


@pytest.mark.parametrize("params", [("task_id",), ("task_id", "include_subtasks", "note")],
                         ids=["a-lost-parameter", "a-new-parameter"])
def test_a_changed_archive_parameter_list_breaks_the_pin(params, monkeypatch) -> None:
    """Mutation: drop the pin compare for the class C set -> red."""
    _class_c(monkeypatch, "*")
    fn = _writer("archive_task", params, open_world=False, **{eg.CARD_WRITE_KEY: True})
    assert eg.is_held_card_write(fn) is False
    good = _writer("archive_task", ("task_id", "include_subtasks"),
                   open_world=False, **{eg.CARD_WRITE_KEY: True})
    assert eg.is_held_card_write(good) is True, "non-vacuity: the pinned set passes"


def test_an_archive_with_no_mark_stays_off(monkeypatch) -> None:
    """Mutation: drop the mark test for the class C set -> red."""
    _class_c(monkeypatch, "*")
    fn = _writer("archive_project", ("project_id",), open_world=False)
    assert eg.is_held_card_write(fn) is False


def test_a_bare_archive_name_holds_to_its_registered_pin(monkeypatch) -> None:
    """``permission_policy`` passes a bare name. Mutation: answer True for it -> red."""
    from acb_skills import tool_annotations as ta

    _class_c(monkeypatch, "*")
    assert eg.is_held_card_write("archive_task") is True
    assert eg.is_held_card_write("archive_project") is True
    monkeypatch.setitem(ta.CARD_WRITE_PARAMS, "archive_task", frozenset({"task_id"}))
    assert eg.is_held_card_write("archive_task") is False
    _class_c(monkeypatch, "")
    assert eg.is_held_card_write("archive_project") is False


@pytest.mark.parametrize("name", sorted(CLASS_C_KEPT))
def test_a_kept_archive_awaits_its_card_before_any_write(name: str) -> None:
    """Mutation: post before ``_confirm`` -> red."""
    fn = _skill_functions()[name]
    doors = _calls(fn, _DOORS)
    writes_at = _calls(fn, _WRITES)
    assert doors and writes_at and doors[0] < writes_at[0], name


async def test_flag_on_a_held_pack_keeps_the_two_archives(store, monkeypatch) -> None:
    """THE acceptance at the pack seam. Mutation: skip the class C set -> red."""
    _class_c(monkeypatch, "*")
    await _mark_chat()
    tools: set[str] = set()
    gone: set[str] = set()
    for slug in PROJECT_PACKS:
        build = await _build(slug)
        assert build.held is True, slug
        tools |= {_name(t) for t in build.tools}
        gone |= set(build.dropped)
    assert tools >= CLASS_C_KEPT
    assert gone == OFF_PROJECTS - CLASS_C_KEPT


# ── 13. WT-6a: the receipt's Undo ───────────────────────────────────────────

TID = "11111111-1111-4111-8111-111111111111"
PID = "22222222-2222-4222-8222-222222222222"
STAMP = "2026-11-07T10:00:00.123456+00:00"
#: The root row's stamp that the project archive wrote. A read after the
#: archive gives STAMP, as if a later write by another member had moved it.
ARCHIVED_STAMP = "2026-11-07T10:00:01.654321+00:00"
CHILD = "33333333-3333-4333-8333-333333333333"


def _undo_spy(monkeypatch) -> list[tuple[str, str, Any]]:
    seen: list[tuple[str, str, Any]] = []

    async def spy(kind: str, row_id: str, version: Any) -> bool:
        seen.append((kind, row_id, version))
        return True

    monkeypatch.setattr("acb_skills.ask_tools.publish_card_undo", spy)
    return seen


def _archive_gateway(monkeypatch, *, subtasks: bool = False,
                     children: bool = False) -> list[dict[str, Any]]:
    def responder(call: dict[str, Any]) -> Any:
        path, method = call["path"], call["method"]
        if path.endswith(f"/tasks/{TID}") and method == "GET":
            return {"id": TID, "title": "Fix it", "task_number": 7, "project_id": PID,
                    "status_id": "s1", "archived_at": None}
        if path.endswith(f"/tasks/{TID}/relations") and children:
            return {"subtasks": [{"id": CHILD, "category": "todo"}]}
        if path.endswith(f"/tasks/{TID}/archive"):
            row = {"id": TID, "archived_at": STAMP, "updated_at": STAMP}
            if subtasks:
                row |= {"subtasks_archived": 1, "subtask_ids": [CHILD]}
            return row
        if path.endswith(f"/nodes/{PID}") and method == "GET":
            return {"id": PID, "name": "Ops", "archived_at": None, "updated_at": STAMP}
        if path.endswith(f"/nodes/{PID}/archive"):
            return {"project_id": PID, "archived": True, "projects": 1, "open_tasks": 0,
                    "updated_at": ARCHIVED_STAMP}
        if path.endswith(f"/nodes/{PID}/summary"):
            return {}
        return {"rows": [], "total": 0}

    return fake_gateway(monkeypatch, responder)


async def test_flag_on_an_approved_archive_offers_undo_with_its_version(monkeypatch) -> None:
    """Mutation: drop ``_offer_undo`` from ``archive_task`` -> red."""
    from skill_projects import guarded

    _class_c(monkeypatch, "*")
    _archive_gateway(monkeypatch)
    approve(monkeypatch)
    seen = _undo_spy(monkeypatch)
    answer = await guarded.archive_task(TID)
    assert answer.startswith("Archived"), answer
    assert seen == [("task", TID, STAMP)]


async def test_flag_on_an_approved_project_archive_offers_undo(monkeypatch) -> None:
    """The Undo token is the stamp that THIS archive wrote on the root row, and
    not a read after it. A read could see a later archive by another member,
    and the Undo would then clear that one. Mutation: read the node after the
    archive -> STAMP -> red."""
    from skill_projects import guarded

    _class_c(monkeypatch, "*")
    _archive_gateway(monkeypatch)
    approve(monkeypatch)
    seen = _undo_spy(monkeypatch)
    await guarded.archive_project(PID)
    assert seen == [("project", PID, ARCHIVED_STAMP)]


async def test_a_project_archive_with_no_stamp_offers_no_undo(monkeypatch) -> None:
    """The root was already archived, or an older gateway answered. With no
    stamp of this archive, no line offers an Undo that could clear another."""
    from skill_projects import guarded

    _class_c(monkeypatch, "*")

    def responder(call: dict[str, Any]) -> Any:
        if call["path"].endswith(f"/nodes/{PID}/archive"):
            return {"project_id": PID, "archived": True, "projects": 1, "open_tasks": 0}
        if call["path"].endswith(f"/nodes/{PID}") and call["method"] == "GET":
            return {"id": PID, "name": "Ops", "archived_at": None, "updated_at": STAMP}
        return {"rows": [], "total": 0}

    fake_gateway(monkeypatch, responder)
    approve(monkeypatch)
    seen = _undo_spy(monkeypatch)
    await guarded.archive_project(PID)
    assert seen == []


async def test_flag_off_no_receipt_offers_undo(monkeypatch) -> None:
    """The receipt line stays dark with the flag. Mutation: drop the flag test
    from ``_offer_undo`` -> red."""
    from skill_projects import guarded

    _class_c(monkeypatch, "")
    _archive_gateway(monkeypatch)
    approve(monkeypatch)
    seen = _undo_spy(monkeypatch)
    await guarded.archive_task(TID)
    assert seen == []


async def test_a_declined_archive_offers_no_undo(monkeypatch) -> None:
    """Mutation: offer before the card -> red."""
    from skill_projects import guarded

    from tests.unit._projects_agent_fakes import deny

    _class_c(monkeypatch, "*")
    calls = _archive_gateway(monkeypatch)
    deny(monkeypatch)
    seen = _undo_spy(monkeypatch)
    await guarded.archive_task(TID)
    assert seen == [] and not [c for c in calls if c["method"] == "POST"]


def _chat_held(monkeypatch, answer: bool) -> None:
    """The chat read outside mail, or not. ``chat_is_held`` has tests of its own."""

    async def chat_is_held(_ctx: Any = None) -> bool:
        return answer

    monkeypatch.setattr(eg, "chat_is_held", chat_is_held)


def test_a_held_chat_refuses_the_subtasks_of_an_archive() -> None:
    """The table holds the refusal to the class C pin. Mutation: drop
    ``archive_task`` from ``HELD_REFUSED_PARAMS`` -> red."""
    assert eg.HELD_REFUSED_PARAMS["archive_task"] == {"include_subtasks"}
    assert eg.HELD_REFUSED_PARAMS["archive_task"] <= eg.HELD_CLASS_C_TOOLS["archive_task"]
    assert "include_subtasks" in eg.HELD_PARAM_REASONS


@pytest.mark.parametrize("value", ["yes", "no"])
async def test_a_held_archive_with_include_subtasks_is_refused(value: str, monkeypatch) -> None:
    """A held chat archives one task, with Undo, and never a subtree. Mutation:
    drop the refusal from ``archive_task`` -> a card and a cascade -> red."""
    from skill_projects import guarded

    _class_c(monkeypatch, "*")
    calls = _archive_gateway(monkeypatch, subtasks=True, children=True)
    cards = approve(monkeypatch)
    _chat_held(monkeypatch, True)
    seen = _undo_spy(monkeypatch)
    answer = await guarded.archive_task(TID, include_subtasks=value)
    assert answer == eg.held_param_refusal("archive_task", "include_subtasks")
    assert "new chat" not in answer.lower()
    assert cards == [] and seen == []
    assert not [c for c in calls if c["method"] == "POST"]


async def test_a_held_archive_without_include_subtasks_files_one_task_with_undo(
    monkeypatch,
) -> None:
    """The task has a subtask. A held chat does not ask about it, because it
    cannot answer yes. The card says that the subtask stays, the write files
    one task, and the receipt offers Undo. Mutation: ask in a held chat -> red."""
    from skill_projects import guarded

    _class_c(monkeypatch, "*")
    calls = _archive_gateway(monkeypatch, children=True)
    cards = approve(monkeypatch)
    _chat_held(monkeypatch, True)
    seen = _undo_spy(monkeypatch)
    answer = await guarded.archive_task(TID)
    assert answer.startswith("Archived"), answer
    assert len(cards) == 1 and "left on the board" in cards[0]["detail"]
    posts = [c for c in calls if c["method"] == "POST"]
    assert len(posts) == 1 and not (posts[0].get("params") or {}).get("include_subtasks")
    assert seen == [("task", TID, STAMP)]


async def test_an_open_chat_archive_that_filed_subtasks_offers_no_undo(monkeypatch) -> None:
    """Only an open chat can file subtasks. Undo restores ONE task, as the route
    does, so that receipt offers none. Mutation: offer it anyway -> red."""
    from skill_projects import guarded

    _class_c(monkeypatch, "*")
    calls = _archive_gateway(monkeypatch, subtasks=True, children=True)
    approve(monkeypatch)
    _chat_held(monkeypatch, False)
    seen = _undo_spy(monkeypatch)
    answer = await guarded.archive_task(TID, include_subtasks="yes")
    assert answer.startswith("Archived"), answer
    assert [c for c in calls if c["method"] == "POST"], "non-vacuity: it archived"
    assert seen == []


@pytest.mark.parametrize(("kind", "row_id", "version", "ok"), [
    ("task", TID, STAMP, True),
    ("project", PID.upper(), "2026-11-07T10:00:00Z", True),
    ("note", TID, STAMP, False),
    ("task", "../../members", STAMP, False),
    ("task", TID, None, False),
    ("task", TID, "2026-11-07T10:00:00", False),
    ("task", TID, "yesterday", False),
])
def test_the_undo_value_takes_only_a_row_and_a_version(kind, row_id, version, ok) -> None:
    """The id goes into a path, and the version into If-Match. Mutation: pass any
    id -> red."""
    from acb_skills.ask_tools import undo_value

    value = undo_value(kind, row_id, version)
    assert (value is not None) is ok
    if ok:
        assert set(value) == {"kind", "id", "version"}
        assert value["id"] == row_id.lower()


async def test_the_undo_names_the_card_that_the_member_approved() -> None:
    """The tool names no card. Mutation: publish with no approved card -> red."""
    from acb_skills import ask_tools as at

    sent: list[dict[str, Any]] = []

    async def publish(ev: dict[str, Any]) -> None:
        sent.append(ev)

    assert await at.publish_card_undo("task", TID, STAMP) is False, "no approved card"
    token = at._APPROVED_CARD.set(("a" * 32, publish))
    try:
        assert await at.publish_card_undo("task", TID, STAMP) is True
        assert await at.publish_card_undo("task", TID, STAMP) is False, "spent"
    finally:
        at._APPROVED_CARD.reset(token)
    assert len(sent) == 1, "one card gives one Undo"
    assert sent == [{"type": "CUSTOM", "name": at.CONFIRMATION_UNDO,
                     "value": {"request_id": "a" * 32, "kind": "task", "id": TID,
                               "version": STAMP}}]
    assert "token" not in str(sent)


async def test_a_card_with_no_chat_channel_clears_the_card_of_an_earlier_call() -> None:
    """A card of an earlier call must never take the Undo of this one. Mutation:
    drop the clear at the top of ``request_confirmation`` -> red."""
    from acb_skills import ask_tools as at

    sent: list[dict[str, Any]] = []

    async def publish(ev: dict[str, Any]) -> None:
        sent.append(ev)

    token = at._APPROVED_CARD.set(("c" * 32, publish))
    try:
        # No run queue and no relay thread: the non-interactive default.
        assert await at.request_confirmation(
            "Archive this task?", non_interactive_default="approve") is True
        assert await at.publish_card_undo("task", TID, STAMP) is False
    finally:
        at._APPROVED_CARD.reset(token)
    assert sent == []


async def test_a_fault_of_the_channel_gives_false(monkeypatch) -> None:
    from acb_skills import ask_tools as at

    async def publish(_ev: dict[str, Any]) -> None:
        raise ConnectionError("gone")

    token = at._APPROVED_CARD.set(("b" * 32, publish))
    try:
        assert await at.publish_card_undo("task", TID, STAMP) is False
    finally:
        at._APPROVED_CARD.reset(token)


@pytest.fixture(autouse=True)
def _held_card_writes_on(monkeypatch) -> None:
    """RX-1: this file tests the rule with the switch on. The switch off has its
    own test in ``test_held_card_writes.py``."""
    from acb_common import get_settings

    monkeypatch.setattr(get_settings(), "held_card_writes_orgs", "*")
