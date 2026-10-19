"""Task and project work routes to projects-assistant (D52, D53, D54).

Production, 2026-10-18: the owner asked the main chat to file inbox tasks
into projects. The orchestrator called ``task-manager`` beside
``projects-assistant``, because ``delegate_to_agent`` told the model "Task
management ... -> task-manager". Projects, My Tasks and Calendar are one task
store, and the Projects assistant holds the member's inbox (``my_work``),
its triage (``set_my_overlay``, ``defer``, ``bulk_update``) and private
capture with a calendar block (``create_personal_task``). task-manager runs on
the retired Copilot runtime, so a delegation to it is refused (D84, D92,
``tests/unit/test_copilot_specialist_refused.py``).

This suite fences each place that named task-manager for task work:

1. the orchestrator's persona and the ``delegate_to_agent`` docstring;
2. the email draft consult (``drafting._CONSULT_AGENTS`` and the planner);
3. the routing eval set (``evals/routing/dataset.py``).

Hermetic: no SQL, no network, no model. R8 binds nothing here.

Mutations this suite catches (R7), each run red by hand:

* the docstring routes tasks to task-manager again ->
  ``test_delegate_to_agent_sends_task_work_to_projects``;
* the persona drops its one-store line -> ``test_the_persona_names_the_one_store``;
* the consult planner accepts task-manager ->
  ``test_the_consult_planner_drops_task_manager``;
* a My Tasks case expects task-manager -> ``test_the_eval_set_expects_projects_for_tasks``.
"""
from __future__ import annotations

import asyncio
from typing import Any

import pytest

orch_agents = pytest.importorskip("orchestrator.agents")._mod
drafting = pytest.importorskip(
    "gateway.routes.email.automation.drafting",
    reason="gateway not installed in this environment",
)


def test_delegate_to_agent_sends_task_work_to_projects() -> None:
    doc = orch_agents.delegate_to_agent.__doc__ or ""
    assert '"task-manager"' not in doc, doc
    task_line = next(line for line in doc.splitlines() if "Tasks, projects" in line)
    assert "inbox" in task_line
    assert '"projects-assistant"' in doc


@pytest.mark.parametrize("company_search", [False, True])
def test_the_persona_names_the_one_store(company_search: bool) -> None:
    text = orch_agents._orchestrator_instructions(company_search)
    assert "task_manager" not in text
    assert "ONE task store. Send that work to projects_assistant." in text


def test_the_consult_agents_are_crm_and_projects() -> None:
    assert drafting._CONSULT_AGENTS == ("crm-assistant", "projects-assistant")


def test_the_consult_planner_drops_task_manager(monkeypatch) -> None:
    """The model may still answer with the old name. It is dropped, and a
    projects-assistant consult is kept."""
    seen: dict[str, Any] = {}

    async def _llm(_tier: str, messages: list[dict], **_kw: Any):
        seen["system"] = messages[0]["content"]
        return {"kind": "project/delivery", "consult": [
            {"agent": "task-manager", "question": "Status of order 12?"},
            {"agent": "projects-assistant", "question": "Status of order 12?"},
        ]}, "", "tier-fast"

    monkeypatch.setattr(drafting, "_llm_json", _llm)
    plan = asyncio.run(drafting._draft_consult_plan(
        {"from": "buyer@acme.example", "subject": "Order 12", "body": "Status?"},
    ))
    assert plan == [{"agent": "projects-assistant", "question": "Status of order 12?"}]
    assert "- projects-assistant:" in seen["system"]
    assert "task-manager" not in seen["system"]


def test_the_eval_set_expects_projects_for_tasks() -> None:
    from evals.routing import dataset as ds

    assert not [c.id for c in ds.CASES if c.expected == "task-manager"]
    for case_id in ("T1", "T2", "T3", "T4"):
        assert ds.case(case_id).expected == "projects-assistant", case_id
