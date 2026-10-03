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

**A native MAF agent since 2026-10-03** (agent_architecture.md §11.3). It
was a ``GitHubCopilotAgent``, so every turn spawned a Copilot CLI, resumed an
SDK session and ran the SDK permission hook. It used none of that: its tools
are plain callables and it has no MCP server. It now builds the same
``agent_framework.Agent`` + ``OpenAIChatCompletionClient`` pair as
agent-email-assistant and agent-crm, so the executor runs it on Tier 1.

Exports:
    build_agents() -> list[Agent]   (Dynamic Agent Loader entry point)
    build_agent()  -> Agent
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from acb_common import get_settings

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

#: The name the registry, the run allowlist and the usage ledger know.
AGENT_NAME = "task-manager"

#: The build-time model. The executor replaces it with the run's resolved tier
#: (``_apply_model_for_maf_agent``), exactly as it did on the Copilot path.
MODEL = "tier-balanced"


def _llm_provider() -> dict[str, Any]:
    """BYOK provider config pointing at the gateway's /v1 (litellm SDK).

    The gateway uses the litellm Python SDK directly — no separate proxy.

    Prefer the gateway's real key from Settings (``litellm_master_key``) over a
    bare ``sk-local`` fallback. The Copilot path got its provider from the
    executor at run time. The native path does not, so the key built here is
    the one the run presents.

    ⚠️ No member header here, on purpose (H-181). One agent serves every
    person, so a header stamped at build time would bill the wrong member.
    :func:`acb_llm.attribution.attributed_openai` stamps the member, the app
    and the run on EACH request, from the run context.
    """
    settings = get_settings()
    base_url = (
        os.environ.get("LITELLM_BASE_URL", "")
        or getattr(settings, "litellm_base_url", "")
        or "http://127.0.0.1:8080"
    ).rstrip("/")
    api_key = (
        os.environ.get("LITELLM_MASTER_KEY", "")
        or getattr(settings, "litellm_master_key", "")
        or "sk-local"
    )
    return {"type": "openai", "base_url": f"{base_url}/v1", "api_key": api_key}


def build_agent() -> Any:
    """Construct the task manager as a NATIVE MAF agent backed by the gateway.

    Use ``OpenAIChatCompletionClient``, NOT ``OpenAIChatClient``. The latter
    targets OpenAI's *Responses* API, which the gateway's ``v1_compat`` shim
    does not implement. Imported lazily so the module still loads where the
    optional deps differ.

    No permission handler and no session wiring here: the executor owns both,
    and a factory that sets one outranks platform policy (§3.2).
    """
    from acb_llm.attribution import attributed_openai
    from agent_framework import Agent
    from agent_framework.openai import OpenAIChatCompletionClient

    prov = _llm_provider()
    client = OpenAIChatCompletionClient(
        model=MODEL,
        # Stamp identity so v1_compat attributes this agent's model calls and
        # cost to it on the observability bus (specs/observability_e2.md §6.2).
        async_client=attributed_openai(
            base_url=prov["base_url"],
            api_key=prov["api_key"],
            default_headers={"X-CC-Agent": AGENT_NAME, "X-CC-Source": "chat"},
        ),
    )
    return Agent(
        client=client,
        instructions=INSTRUCTIONS,
        name=AGENT_NAME,
        description=(
            "GTD task manager — captures thoughts, clarifies the inbox, "
            "organizes it, and answers status, progress and workload "
            "questions with citations."
        ),
        tools=list(_TOOLS),
    )


def build_agents() -> list[Any]:
    """Dynamic Agent Loader entry point."""
    return [build_agent()]


__all__ = ["AGENT_NAME", "INSTRUCTIONS", "MODEL", "build_agent", "build_agents"]
