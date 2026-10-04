"""Golden trajectory: no egress for an agent that a covered run calls (H-236).

Locks the decision and its effect end to end, offline: a covered
projects-assistant run delegates, the sub-run decides ``no_egress`` from the
parent's binding, its injection drops every egress tool and keeps the reads
and delegation, a grandchild inherits the flag, and a call to an egress tool
gets the refusal text instead of a send. An uncovered parent changes nothing.
The full fence, through the real executor and the real request bodies, is
``tests/unit/test_delegation_no_egress.py`` (WS43-F24).
"""
from __future__ import annotations

import types

import pytest
from acb_skills import egress as eg
from orchestrator import _tool_injection as ti
from orchestrator import executor
from orchestrator import sandbox_broker as sb

ORG = "aaaaaaaa-0000-0000-0000-00000000e236"
PA = "projects-assistant"


@pytest.fixture
def covered(monkeypatch):
    """The broker covers projects-assistant in ORG, and the run is in ORG."""
    monkeypatch.setattr(executor, "_current_run_org", lambda: ORG)
    monkeypatch.setattr(sb, "covers", lambda agent, org: agent == PA and org == ORG)
    monkeypatch.setattr(ti, "_load_disabled_skill_families", lambda name: frozenset())
    monkeypatch.setattr(ti, "_build_registry_block", lambda: "Registered agents: (stub)")
    monkeypatch.setenv("AGENT_PERMISSION_MODE", "approve_all")


def _send_email() -> str:
    return "sent"


def _read_email() -> str:
    return "mail"


def _injected(no_egress: bool) -> set[str]:
    from acb_skills.tool_annotations import annotate

    send = annotate(destructive=True, open_world=True)(_send_email)
    read = annotate(read_only=True)(_read_email)
    agent = types.SimpleNamespace(name="probe", default_options={"tools": [send, read]})
    agents = [agent]
    ti._inject_agent_tools(agents, agent_name="probe", agent_config={"name": "probe"},
                           no_egress=no_egress)
    return {eg.tool_name(t) for t in agents[0].default_options["tools"]}


def test_a_covered_parent_marks_its_delegation_and_the_grandchild(covered) -> None:
    child = ti._delegated_no_egress({"agent_name": PA})
    assert child is True
    grandchild = ti._delegated_no_egress({"agent_name": "email-assistant", "no_egress": child})
    assert grandchild is True


def test_an_uncovered_parent_marks_nothing(covered) -> None:
    assert ti._delegated_no_egress({"agent_name": "orchestrator"}) is False
    assert ti._delegated_no_egress({}) is False


def test_the_delegated_run_keeps_reads_and_delegation_and_loses_egress(covered) -> None:
    held = _injected(no_egress=True)
    assert "_read_email" in held and "call_agent" in held
    assert not held & {"_send_email", "web_search", "fetch_page"}
    open_run = _injected(no_egress=False)
    assert {"_send_email", "web_search", "fetch_page"} <= open_run


async def test_a_named_egress_call_gets_the_refusal_not_a_send() -> None:
    call = types.SimpleNamespace(function=types.SimpleNamespace(name="web_search"), result=None)
    ran: list[str] = []

    async def go() -> None:
        ran.append("sent")

    await eg.RefuseEgressTools().process(call, go)
    assert ran == []
    assert call.result == eg.EGRESS_WITHHELD_ANSWER.format(name="web_search")
