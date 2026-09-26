"""projects-assistant — the AI chat inside the Projects app (WS-27bm).

Spec: ``project-docs/specs/projects_ai_chat.md``.

A native MAF agent whose every tool is a thin wrapper over the gateway's
``/projects/*`` routes, called **as the acting member**: internal bearer plus
``X-User-Email`` from the per-run ContextVar the executor binds. So the agent
inherits the route's rule and nothing else decides authority. It never opens a
database session and never holds SQL (``agent-crm/agents.py`` proved the
shape, and its header carries the reasoning).

The tool surface lives in ``apps/skills/skill-projects``. This file holds the
agent's identity and nothing that could disagree with the skill:

* ``skill_projects.__all__`` is the tool list. ``config.json: own_tool_scope``
  is held equal to it by ``tests/unit/test_projects_agent.py``.
* ``skill_projects.manifest.MANIFEST`` is the route allowlist, and
  ``tests/unit/test_projects_chat_coverage.py`` holds it against the real
  router.

Slice 1 (this file's first version) ships the reads only. The write tools
arrive in S2 and S3 with their confirmation cards, and D-PM-35 keeps the two
hard deletes off the surface until WS-40 answers who may delete.

Registered as a MAF agent named ``projects-assistant``; ``build_agents()`` is
the Dynamic Agent Loader entry point. ⚠️ The directory is ``agent-projects``,
the agent is ``projects-assistant`` — ``local_path`` in ``_AGENT_REGISTRY`` is
the whole mapping.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from acb_common import get_settings

_INSTRUCTIONS_FILE = Path(__file__).parent / "instructions.md"
INSTRUCTIONS = (
    _INSTRUCTIONS_FILE.read_text(encoding="utf-8")
    if _INSTRUCTIONS_FILE.exists()
    else "You are the Projects assistant. Answer questions about projects and "
    "tasks with the provided tools, and cite the task number and title."
)

AGENT_NAME = "projects-assistant"

_TOOLS: list[Any] = []
try:
    import skill_projects

    _TOOLS = [getattr(skill_projects, name) for name in skill_projects.__all__]
except ImportError:
    # skill-projects not installed yet — the agent still boots, tool-less,
    # which the registration test reports rather than a silent half-load.
    pass


def _register_agent_tools() -> dict[str, Any]:
    """Tool map for the gateway's direct quick-action calls (importlib path)."""
    return {fn.__name__: fn for fn in _TOOLS}


def _llm_provider() -> dict[str, Any]:
    """BYOK provider config pointing at the gateway's ``/v1`` (litellm SDK)."""
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


def build_agents() -> list[Any]:
    """Construct the Projects assistant as a native MAF agent on the gateway's
    ``/v1``. ``OpenAIChatCompletionClient``, not ``OpenAIChatClient``: the
    latter speaks the Responses API, which ``v1_compat`` does not serve."""
    from agent_framework import Agent
    from agent_framework.openai import OpenAIChatCompletionClient
    from acb_llm.attribution import attributed_openai

    prov = _llm_provider()
    client = OpenAIChatCompletionClient(
        # D-AI-4: the app declares a default tier. Balanced, until the rate
        # card is priced (H-42) and the owner picks otherwise (spec §12.3).
        model=os.environ.get("PROJECTS_AGENT_MODEL", "tier-balanced"),
        # Usage slice 1: `attributed_openai` adds the member, app and run to
        # EVERY request, from the run context. A fixed header cannot, because
        # one client serves everyone who chats with this agent.
        async_client=attributed_openai(
            base_url=prov["base_url"],
            api_key=prov["api_key"],
            default_headers={"X-CC-Agent": AGENT_NAME, "X-CC-Source": "chat"},
        ),
    )
    return [
        Agent(
            client=client,
            instructions=INSTRUCTIONS,
            name=AGENT_NAME,
            description=(
                "Projects assistant — reads the Projects app as the member "
                "who is asking: the tree of spaces and projects, a node's "
                "summary, task lists with the app's own filters, one task in "
                "full with its timeline, the member's own work, who could "
                "take a task, a project's vocabulary, the five analytics "
                "reads (stuck, load, throughput, finished, outlook), the team's "
                "capacity (who holds the work, their hours, skills and "
                "at-risk tasks, for a member with HR read access), who fits "
                "a task best and who could help whom (ranked by skill, spare "
                "hours and availability, for the same member), where the plan "
                "interferes with itself (dependencies out of order, late "
                "blockers, parallel work, and for the same member overcommitment, "
                "absence, leaving and work over a ceiling), a table of "
                "tasks or the server's exact groups over them for a "
                "question no analytics read answers, and the "
                "saved reports, rendered now. Creates and updates tasks and "
                "projects, assigns, comments, links, moves, watches, defers "
                "and completes, saves a report, edits the project's statuses, "
                "types, fields and tags, sets a repeat rule, captures a "
                "private task and files the member's own triage. Every write "
                "shows the member a card first and does nothing if they "
                "decline. Archives, restores and moves a project, archives, "
                "merges and bulk-edits tasks, deletes a status, type, field, "
                "tag, view, report, comment or attachment and reverts a "
                "change, one act per card with the counts on the card. Draws a "
                "timeline, a board, a task table and a report as cards, edits "
                "a task or a project from a form in the chat, plans a project "
                "from a goal as an editable plan, and writes a status report. "
                "Reads the calendar, the intake queue, notifications, watchers, "
                "saved views and who may see a project; captures and triages "
                "intake, saves a view and clears the bell. Opens a task, a "
                "project or an app in the member's page. It never deletes a "
                "project or a task."
            ),
            tools=list(_TOOLS),
        )
    ]


__all__ = ["AGENT_NAME", "INSTRUCTIONS", "_register_agent_tools", "build_agents"]
