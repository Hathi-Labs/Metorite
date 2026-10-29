"""WS-67 SUB-1: a child cannot write, send, automate, ask or delegate (R7 fence).

Spec ``project-docs/specs/one_assistant_context.md`` §6.8 (Metorite #822) and
§4.5, OA-9b narrowing 7. It is part of the OA-9b gate.

SUB-1 rule 1: a child never writes memory, sends outside, creates an
automation, asks the member or delegates. Three layers hold it:

1. ``parse_jobs`` refuses a job that names such a tool;
2. the injection gives the worker the tools of the job only;
3. the allow-list pair of ``subagent_contract.child_pin`` takes every tool that
   is not in the job out of each model request and refuses a call to one.
   **This is the layer that counts**, because it covers what arrives after the
   first two: the floor tools that no opt-out removes, a granted app tool, an
   MCP tool and a tool that ``add_tools`` adds.

Each blocked class gets a parametrised case. A case gives the REAL worker
(``apps/agents/agent-worker``, with its real pair) a pool that holds the
blocked tool, scripts a model that calls it, and asserts that the tool BODY ran
0 times. The control case runs a job tool, so the pair does not block everything.

Hermetic: no database, no network. Only the model wire is replaced.

Mutations this file catches (each run red, then restored):

* M1 the worker drops the pair (``middleware=[]``) -> every ``test_a_child_cannot_*`` case;
* M2 ``child_pin`` allows a floor tool -> ``test_the_floor_tools_...``, ``test_a_child_cannot_ask_the_member``;
* M3 ``MEMBER_TOOLS`` is emptied -> ``test_the_parser_refuses_each_blocked_tool``;
* M4 a name leaves a production table (``STORE_WRITES`` or ``DELEGATION_TOOLS``)
  -> ``test_the_tables_match_production``.
"""
from __future__ import annotations

import asyncio
import json

import pytest
from acb_skills import egress as eg
from acb_skills import subagent_contract as sc
from acb_skills.profile_tools import WRITE_TOOLS as PROFILE_WRITES
from acb_skills.write_artifact import artifact_context_scope, bind_artifact_context

from tests.unit._subagent_harness import (
    REPO_ROOT,
    WORKER_DIR,
    WireModel,
    build_worker,
    completion,
    counted,
)

CONFIG = json.loads((REPO_ROOT / WORKER_DIR / "config.json").read_text(encoding="utf-8"))

#: The five store writes, then the profile writes (``profile_tools.WRITE_TOOLS``).
#: The spec of 2026-10-27 counted five profile writes. The tuple holds six
#: since ``profile_interview``, and the table follows the code.
MEMORY_WRITES = [
    "save_memory", "save_episode", "save_agent_memory", "save_org_memory", "save_note",
    "profile_remember", "profile_forget", "profile_set", "profile_alias",
    "profile_undo", "profile_interview",
]
SENDS = [
    "send_email", "forward_email", "unsubscribe_sender", "send_draft", "schedule_send",
    "create_lead", "update_deal_status", "log_activity", "convert_lead",
]
AUTOMATION = [
    "run_workflow", "create_rule", "update_rule", "create_rules_from_prompt",
    "install_default_rules", "learn_rule_pattern", "run_rules", "delete_rule",
    "crm_propose_rule", "set_report_schedule", "set_recurrence",
]
ASK_THE_MEMBER = [
    "ask_questions", "ask_user", "request_confirmation", "share_artifact",
    "emit_generative_ui",
]
SPECIALISTS = [
    "apis_config", "app_builder", "crm_assistant", "email_assistant",
    "projects_assistant", "whatsapp_assistant",
]
DELEGATION = [
    "call_agent", "call_agents_parallel", "call_agent_background", "delegate_to_agent",
    "spawn_subagents", "spawn_copilot_agent", *SPECIALISTS,
]
EVERY_BLOCKED = [*MEMORY_WRITES, *SENDS, *AUTOMATION, *ASK_THE_MEMBER, *DELEGATION]

PAIR_ANSWER = "is not a tool of this sub-agent job. Nothing ran."


def _run_child(blocked: str, *, allowed: tuple[str, ...] = ("remember",)):
    """Run the real worker on a job of *allowed* tools, with *blocked* in its pool.

    The scripted model calls *blocked* once, then answers. Returns
    ``(calls, wire)``: the names of the tool bodies that ran, and the wire.
    """
    calls: list[str] = []
    wire = WireModel([
        completion(calls=[(blocked, "{}")]),
        completion(text='{"items": []}'),
    ])
    job = sc.Job(number=1, job="Find the facts.", tools=allowed, tier="fast")
    with artifact_context_scope():
        bind_artifact_context(run_id="run-b", agent_name="orchestrator", no_egress=False)
        sc.bind_job(job)
        pool = [counted(blocked, calls)]
        pool += [counted(n, calls) for n in allowed if n != blocked]
        agent = build_worker(wire, pool)
        asyncio.run(agent.run("do the job"))
    return calls, wire


def _answer_after_the_call(wire: WireModel) -> str:
    """The text the model read for its tool call: the content of the tool message."""
    messages = wire.bodies[1]["messages"]
    return " ".join(str(m.get("content")) for m in messages if m.get("role") == "tool")


def _assert_blocked(blocked: str) -> None:
    calls, wire = _run_child(blocked)
    assert calls == [], f"the body of {blocked} ran: {calls}"
    assert blocked not in wire.tool_names(0), f"the model request listed {blocked}"
    assert len(wire.bodies) >= 2, "the model never called the tool, so the case proves nothing"
    assert PAIR_ANSWER in _answer_after_the_call(wire), _answer_after_the_call(wire)


# ── Layer 3 for each blocked class: the body runs 0 times ───────────────────


@pytest.mark.parametrize("tool", MEMORY_WRITES)
def test_a_child_cannot_call_a_memory_write(tool: str) -> None:
    _assert_blocked(tool)


@pytest.mark.parametrize("tool", SENDS)
def test_a_child_cannot_call_a_send(tool: str) -> None:
    _assert_blocked(tool)


@pytest.mark.parametrize("tool", AUTOMATION)
def test_a_child_cannot_call_an_automation_tool(tool: str) -> None:
    _assert_blocked(tool)


@pytest.mark.parametrize("tool", ASK_THE_MEMBER)
def test_a_child_cannot_ask_the_member(tool: str) -> None:
    _assert_blocked(tool)


@pytest.mark.parametrize("tool", DELEGATION)
def test_a_child_cannot_delegate(tool: str) -> None:
    _assert_blocked(tool)


# ── The control: the pair does not block everything ─────────────────────────


def test_a_job_tool_still_runs() -> None:
    calls: list[str] = []
    wire = WireModel([
        completion(calls=[("remember", "{}")]),
        completion(text='{"items": []}'),
    ])
    job = sc.Job(number=1, job="Find the facts.", tools=("remember",), tier="fast")
    with artifact_context_scope():
        bind_artifact_context(run_id="run-c", agent_name="orchestrator", no_egress=False)
        sc.bind_job(job)
        agent = build_worker(wire, [counted("remember", calls)])
        asyncio.run(agent.run("do the job"))
    assert calls == ["remember"]
    assert wire.tool_names(0) == ["remember"]


# ── Layer 1 alone: the parser ───────────────────────────────────────────────


@pytest.mark.parametrize("tool", EVERY_BLOCKED)
def test_the_parser_refuses_each_blocked_tool(tool: str) -> None:
    """The parent holds the tool, and the job names it. The parser still refuses."""
    plan = sc.parse_jobs(
        json.dumps([{"job": "Do a thing.", "tools": [tool]}]),
        parent_tools=set(EVERY_BLOCKED) | {"remember"},
        delegating=[*SPECIALISTS, "spawn_copilot_agent"],
    )
    assert plan.jobs == (), f"the parser accepted {tool}"
    assert len(plan.refused) == 1 and "did not run" in plan.refused[0]


@pytest.mark.parametrize("tool", EVERY_BLOCKED)
def test_no_blocked_tool_reaches_a_child_through_a_pack(tool: str) -> None:
    """OA-9c: a pack tool joins a child only through ``child_may_hold``. With
    the marks of the three pack owners loaded, each blocked tool fails it."""
    from tests.unit._native_maf_harness import load_agent_module

    for rel in ("apps/agents/agent-email-assistant", "apps/agents/agent-crm",
                "apps/agents/agent-projects"):
        load_agent_module(rel)
    assert not sc.child_may_hold(tool), f"a pack could give a child {tool}"


def test_the_parser_accepts_a_read_tool() -> None:
    plan = sc.parse_jobs(
        json.dumps([{"job": "Do a thing.", "tools": ["remember", "web_search"]}]),
        parent_tools={"remember", "web_search"},
    )
    assert [j.tools for j in plan.jobs] == [("remember", "web_search")]


# ── Layer 3 alone: the pair, with the parser off ────────────────────────────


@pytest.mark.parametrize("tool", EVERY_BLOCKED)
def test_the_pair_stops_each_blocked_tool_with_the_parser_off(tool: str) -> None:
    """The tool reached the child by a route that the parser never saw (the floor,
    a granted app, an MCP server, ``add_tools``). A parser fed a lying
    ``read_only`` would accept a job that names a plain tool, so the parser is
    not what holds the child: the pair is. The job names ``remember`` only."""
    if tool not in sc.MEMBER_TOOLS and tool not in eg.DELEGATION_TOOLS | set(SPECIALISTS):
        lying = sc.parse_jobs(
            json.dumps([{"job": "Do a thing.", "tools": [tool]}]),
            parent_tools={tool}, read_only=lambda _n: True,
        )
        assert len(lying.jobs) == 1, "the parser is off, so it accepts the tool"
    _assert_blocked(tool)


# ── The floor tools that no opt-out removes ─────────────────────────────────


@pytest.mark.parametrize("floor_tool", ["ask_questions", "emit_generative_ui", "read_attachment"])
def test_the_floor_tools_that_cannot_be_opted_out_are_withheld(
    floor_tool: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The injection leaves them in the child (``_FLOOR_KEEP``), and the pair
    takes them out of the request."""
    from orchestrator import _tool_injection as ti

    monkeypatch.setenv("AGENT_PERMISSION_MODE", "approve_all")
    wire = WireModel([completion(text='{"items": []}')])
    with artifact_context_scope():
        bind_artifact_context(run_id="run-f", agent_name="orchestrator", no_egress=False)
        sc.bind_job(sc.Job(number=1, job="Find.", tools=("remember",), tier="fast"))
        agent = build_worker(wire)
        agents = [agent]
        ti._inject_agent_tools(
            agents, is_sub_agent=True,
            tool_scope=sc.child_tool_scope("worker", CONFIG["tool_scope"]),
            agent_name="worker", agent_config=CONFIG, no_egress=False,
        )
        pool = {getattr(t, "__name__", getattr(t, "name", ""))
                for t in agents[0].default_options["tools"]}
        assert floor_tool in pool, f"the injection no longer leaves {floor_tool} in the child"
        asyncio.run(agents[0].run("do it"))
    assert floor_tool not in wire.tool_names(0)
    assert wire.tool_names(0) == ["remember"]


# ── The tables follow production ────────────────────────────────────────────


def test_the_tables_match_production() -> None:
    """Catches M4: a name that leaves a production table leaves the test table
    behind, and the case no longer matches."""
    assert set(MEMORY_WRITES[:5]) == set(eg.STORE_WRITES)
    assert set(MEMORY_WRITES[5:]) == {t.__name__ for t in PROFILE_WRITES}
    assert set(ASK_THE_MEMBER) == set(sc.MEMBER_TOOLS) | {"emit_generative_ui"}
    assert set(DELEGATION[:5]) == set(eg.DELEGATION_TOOLS)
    assert len(DELEGATION) == 12
    assert len(MEMORY_WRITES) == 11 and len(SENDS) == 9 and len(AUTOMATION) == 11
    assert len(ASK_THE_MEMBER) == 5


def test_every_specialist_name_is_a_real_tool_of_the_orchestrator(monkeypatch) -> None:
    """The delegation table names the specialist tools by the names the orchestrator
    gives them (``name.replace("-", "_")``), and ``_delegating_names`` finds them."""
    from acb_skills.agent_tools import _delegating_names
    from gateway.routes import agent as routes

    monkeypatch.setattr(routes, "_load_dynamic_agents", lambda: [])
    names = _delegating_names()
    assert set(SPECIALISTS) <= names
    assert "spawn_copilot_agent" in names
    assert "worker" not in names
