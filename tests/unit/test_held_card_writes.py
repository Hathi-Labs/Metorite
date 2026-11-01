"""RX-1 (D150): a held chat keeps the writes that a card shows.

Owner directive of 2026-11-01. In one chat the owner read mail, sent mail and
then said "Remind me to call Rajendra". The chat answered that it held no tool
that creates anything, and that a new chat would do it. The rule of this slice:
keep the guard on what leaves Metorite, and keep a write that the member
approves on a server-built card.

``project-docs/work_plan.md`` D150, ``one_assistant_context.md`` §4.2.3, and
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
                 "projects-reminders")

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
    "update_reminder", "update_status", "update_tag", "update_task", "update_type",
    "watch",
})
#: The four CRM record writes. Each says ``open_world=True`` and keeps saying it.
KEPT_CRM = frozenset({"convert_lead", "create_lead", "log_activity", "update_deal_status"})

#: The Projects writes that a held chat does not hold: every class C tool but
#: ``promote_reminder``. A covered run still holds them.
OFF_PROJECTS = frozenset({
    "add_report_recipient", "archive_project", "archive_task", "attach_to_task",
    "bulk_update", "convert_folder_to_project", "delete_area", "delete_attachment",
    "delete_comment", "delete_field", "delete_project", "delete_status", "delete_tag",
    "delete_task", "delete_type", "delete_view", "grant_access", "merge_tags",
    "merge_tasks", "move_project", "nudge_task", "remove_report_recipient", "rename_area",
    "report_delete", "revert_activity", "revoke_access", "set_report_schedule",
    "set_status_set", "unarchive_project",
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
_DOORS = {"_confirm", "_ask_batch", "update_task", "update_project", "request_confirmation"}
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
    is not on this list by the rule of D150."""
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
    assert reminders == set(SKILL_FAMILIES["projects-reminders"]["tools"])


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
    """H-236 holds a covered run for the data of a sandboxed turn, and D150 speaks
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
    monkeypatch.setattr(ti, "read_untrusted_answer", lambda *_a: False)
    agents = [_agent_with([projects_tools["delete_task"]])]
    ti._apply_no_egress(agents, PA)
    assert [_name(t) for t in agents[0].default_options["tools"]] == ["delete_task"]


def test_a_covered_run_in_a_chat_that_read_mail_drops_them(monkeypatch, projects_tools) -> None:
    """The mail is what the member can act on, so the chat rule wins. Mutation:
    drop the mark from ``_covered_hold`` -> red."""
    monkeypatch.setattr(ti, "_run_covered", lambda *_a, **_k: True)
    monkeypatch.setattr(ti, "read_untrusted_answer", lambda *_a: True)
    agents = [_agent_with([projects_tools["delete_task"]])]
    ti._apply_no_egress(agents, PA)
    assert agents[0].default_options["tools"] == []


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
