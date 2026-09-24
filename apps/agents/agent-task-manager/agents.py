"""agent-task-manager — the My Tasks agent.

The agent behind the /tasks app (spec: project-docs/specs/
task_manager_app.md §3.1): captures thoughts, clarifies the inbox through
the GTD decision tree, organizes them, and answers status/progress/workload
questions.

Tool surface:
  skill-my-tasks     — the task tools over the gateway's lens routes
                       (``/projects/my/*``, ``/projects/tasks/*``), plus the
                       ``/tasks/*`` doors that pick their store at call time
                       (``item_source()`` for the AI and plan routes,
                       ``agent_source()`` for four day-planner routes) or read
                       a table that survives (``day-state``, ``people``).

⚠️ **There is no external PM system, and there is no connector.** **D52**
(2026-08-24, board WS-39 S1) retired ClickUp outright: Metorite is the
project-management system of record. ``skill-clickup-sync`` is deleted and the
gateway's connector registry is empty by decision. Status and progress questions
are answered from Metorite's own store.

**Re-pointed in S8a (2026-09-23, my_tasks_cutover.md §5).** **D53** makes
``pm_tasks``/``pm_task_personal`` the one task store. The skill reads and
writes it through the same routes the browser uses. Nothing here touches
the retired task store any more.

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
#   skill-my-tasks — capture/clarify/organize/list over the gateway's lens
#                    routes (S8a). The connector registry is empty (D52), so
#                    there is no outward path at all — every read is
#                    Metorite's one task store.
# ---------------------------------------------------------------------------

_TOOLS: list = []

try:
    from skill_my_tasks import (
        my_tasks_accounts,
        my_tasks_add_subtasks,
        my_tasks_archive,
        my_tasks_capture,
        my_tasks_capture_many,
        my_tasks_clarify,
        my_tasks_complete,
        my_tasks_day_digest,
        my_tasks_delegate,
        my_tasks_detail,
        my_tasks_estimate_stats,
        my_tasks_inbox_insights,
        my_tasks_list,
        my_tasks_list_projects,
        my_tasks_list_schedule,
        my_tasks_move,
        my_tasks_organize,
        my_tasks_people,
        my_tasks_plan_day,
        my_tasks_plan_project,
        my_tasks_replan_day,
        my_tasks_rollover,
        my_tasks_schedule,
        my_tasks_set_one_thing,
        my_tasks_set_stage,
        my_tasks_subtasks,
        my_tasks_sync,
        my_tasks_unschedule,
        my_tasks_update,
    )
    _TOOLS += [
        my_tasks_capture, my_tasks_capture_many, my_tasks_list, my_tasks_list_projects,
        my_tasks_accounts, my_tasks_people, my_tasks_inbox_insights, my_tasks_clarify,
        my_tasks_organize, my_tasks_update, my_tasks_sync, my_tasks_plan_project,
        my_tasks_schedule, my_tasks_unschedule, my_tasks_list_schedule,
        # Manage existing tasks — the app's full action surface over chat
        # (complete/reopen, buckets, stage, delegate, subtasks, archive, detail)
        my_tasks_complete, my_tasks_move, my_tasks_detail, my_tasks_set_stage, my_tasks_delegate,
        my_tasks_subtasks, my_tasks_add_subtasks, my_tasks_archive,
        # AI day-management (planner over chat) — calendar_ai_review.md §4.2/4.4
        my_tasks_plan_day, my_tasks_replan_day, my_tasks_rollover, my_tasks_day_digest,
        my_tasks_estimate_stats, my_tasks_set_one_thing,
    ]
except ImportError:
    # skill-my-tasks not installed yet — agent still boots.
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
