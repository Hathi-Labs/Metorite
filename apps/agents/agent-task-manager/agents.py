"""agent-task-manager — the GTD Task Manager agent.

The agent behind the /tasks app (spec: project-docs/specs/
task_manager_app.md §3.1): captures thoughts, clarifies the inbox through
the GTD decision tree, organizes them, and answers status/progress/workload
questions.

Tool surface:
  skill-task-gtd     — the GTD engine over the gateway /tasks API.

⚠️ **There is no external PM system, and there is no connector.** **D52**
(2026-08-24, board WS-39 S1) retired ClickUp outright: Metorite is the
project-management system of record. ``skill-clickup-sync`` is deleted and the
gateway's connector registry is empty by decision. Status and progress questions
are answered from Metorite's own store.

⚠️ **This agent is scheduled to be re-pointed, not retired.** **D53** makes
``pm_tasks``/``pm_task_personal`` the one task store and the ``gtd_*`` tables its
predecessor; WS-39 **S3a** moves the surface. Until then this agent still reads
the GTD store, which is correct-but-temporary — do not build new behaviour on
``gtd_*`` here.

Exports:
    build_agents() -> list[GitHubCopilotAgent]   (Dynamic Agent Loader entry point)
    build_agent()  -> GitHubCopilotAgent
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from agent_framework_github_copilot import GitHubCopilotAgent

_INSTRUCTIONS_FILE = Path(__file__).parent / "instructions.md"
INSTRUCTIONS = _INSTRUCTIONS_FILE.read_text(encoding="utf-8") if _INSTRUCTIONS_FILE.exists() else (
    "You are the task-manager agent. Answer questions about tasks and projects "
    "using the provided tools. Always cite the task URL when available."
)


# ---------------------------------------------------------------------------
# Tools
#   skill-task-gtd — capture/clarify/organize/list over the gateway /tasks
#                    API. The connector registry is empty (D52), so there is no
#                    outward path at all — every read is Metorite's own store.
# ---------------------------------------------------------------------------

_TOOLS: list = []

try:
    from skill_task_gtd import (
        gtd_accounts,
        gtd_add_subtasks,
        gtd_archive,
        gtd_capture,
        gtd_capture_many,
        gtd_clarify,
        gtd_complete,
        gtd_day_digest,
        gtd_delegate,
        gtd_detail,
        gtd_estimate_stats,
        gtd_inbox_insights,
        gtd_list,
        gtd_list_projects,
        gtd_list_schedule,
        gtd_move,
        gtd_organize,
        gtd_people,
        gtd_plan_day,
        gtd_plan_project,
        gtd_replan_day,
        gtd_rollover,
        gtd_schedule,
        gtd_set_one_thing,
        gtd_set_stage,
        gtd_subtasks,
        gtd_sync,
        gtd_unschedule,
        gtd_update,
    )
    _TOOLS += [
        gtd_capture, gtd_capture_many, gtd_list, gtd_list_projects,
        gtd_accounts, gtd_people, gtd_inbox_insights, gtd_clarify,
        gtd_organize, gtd_update, gtd_sync, gtd_plan_project,
        gtd_schedule, gtd_unschedule, gtd_list_schedule,
        # Manage existing tasks — the app's full action surface over chat
        # (complete/reopen, buckets, stage, delegate, subtasks, archive, detail)
        gtd_complete, gtd_move, gtd_detail, gtd_set_stage, gtd_delegate,
        gtd_subtasks, gtd_add_subtasks, gtd_archive,
        # AI day-management (planner over chat) — calendar_ai_review.md §4.2/4.4
        gtd_plan_day, gtd_replan_day, gtd_rollover, gtd_day_digest,
        gtd_estimate_stats, gtd_set_one_thing,
    ]
except ImportError:
    # skill-task-gtd not installed yet — agent still boots.
    pass


# ---------------------------------------------------------------------------
# Agent factory
# ---------------------------------------------------------------------------

def _llm_provider() -> dict[str, Any]:
    """Return BYOK provider config pointing at the gateway's /v1 endpoint.

    The gateway uses the litellm Python SDK directly — no separate proxy.
    """
    base_url = os.environ.get("LITELLM_BASE_URL", "http://127.0.0.1:8080")
    api_key = os.environ.get("LITELLM_MASTER_KEY", "sk-local")
    return {"type": "openai", "base_url": f"{base_url}/v1", "api_key": api_key}


def build_agent() -> GitHubCopilotAgent:
    # No on_permission_request here: the executor injects the risk-aware
    # permission handler (permission_policy) when none is set.  Setting one
    # here pre-populates ``_permission_handler``, which makes the executor's
    # ``if ... is None`` guard skip — silently disabling B6 for this agent.
    return GitHubCopilotAgent(
        instructions=INSTRUCTIONS,
        tools=_TOOLS,
        default_options={
            "model": "tier-balanced",
            "provider": _llm_provider(),
            "mcp_servers": {},
        },
    )


def build_agents() -> list[GitHubCopilotAgent]:
    """Dynamic Agent Loader entry point."""
    return [build_agent()]


__all__ = ["INSTRUCTIONS", "build_agent", "build_agents"]
