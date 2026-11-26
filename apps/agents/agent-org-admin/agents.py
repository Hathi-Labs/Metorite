"""Organization Members - the owner of the ``org-admin`` tool pack (PAR-2a, PAR-4a, PAR-4c,
PAR-4d, PAR-6a).

Spec: ``project-docs/specs/chat_parity.md`` section 6.3, rung 1 (Metorite
task 3102) and rung 3 (PAR-4a, task 3107, PAR-4c, task 3935, PAR-4d, task
3936, and PAR-6a, task 3109).

This agent is **not a second assistant** (D99, D125). The member talks to
Mira only. The pack machinery (``acb_skills.tool_packs.owner_tools``) reads
:func:`_agent_tools` from this module, and the main assistant holds those
tools in a turn about the members or the Integrations of the organization.
The tools are in ``acb_skills.org_admin_tools`` (PAR-2a),
``acb_skills.org_admin_more_tools`` (PAR-4a: groups, roles, overrides and the
name of the org. PAR-4c: the removal and the purge of a member. PAR-4d: the
seats), ``acb_skills.integrations_admin_tools`` (PAR-6a) and
``acb_skills.people_tools`` (P-5b-1 and P-5b-5, the pack ``people``), and this module
keeps no copy. They call the admin and Integrations routes over
HTTP as the member, and take no org or member argument. The routes decide
each right.

It never runs as an agent, as ``studio-builder`` does. The name "org-admin"
is in no agent list: not the registry, the picker, a specialist tool or the
registry block. Only the pack loader reads its folder, from the owner list
of ``gateway.routes.agent``. A hand-off to it, and a run of it, is refused in
every run (``tool_packs.pack_only_refusal``). ``build_agents()`` stays because
the folder fences build each agent folder (``tests/unit/test_agent_voice.py``).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from acb_common import get_settings
from acb_skills.integrations_admin_tools import INTEGRATIONS_ADMIN_TOOLS
from acb_skills.org_admin_more_tools import ORG_ADMIN_MORE_TOOLS, ORG_ADMIN_SEAT_TOOLS
from acb_skills.org_admin_tools import ORG_ADMIN_TOOLS
from acb_skills.people_tools import PEOPLE_TOOLS

_INSTRUCTIONS_FILE = Path(__file__).parent / "instructions.md"
INSTRUCTIONS = (
    _INSTRUCTIONS_FILE.read_text(encoding="utf-8")
    if _INSTRUCTIONS_FILE.exists()
    else "You hold the member tools of the organization. You are not a second assistant."
)

_TOOLS = [*ORG_ADMIN_TOOLS, *ORG_ADMIN_MORE_TOOLS, *ORG_ADMIN_SEAT_TOOLS, *PEOPLE_TOOLS,
          *INTEGRATIONS_ADMIN_TOOLS]


def _register_agent_tools() -> dict[str, Any]:
    """Tool map for the gateway's direct calls (importlib path)."""
    return {fn.__name__: fn for fn in _TOOLS}


def _agent_tools() -> list[Any]:
    """Each tool as the agent registers it. The pack reads this list."""
    return list(_TOOLS)


def _llm_provider() -> dict[str, Any]:
    """BYOK provider config pointing at the gateway's /v1."""
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
    """Construct the owner as a native MAF agent (as ``agent-studio-builder`` does)."""
    from acb_llm.attribution import attributed_openai
    from agent_framework import Agent
    from agent_framework.openai import OpenAIChatCompletionClient

    prov = _llm_provider()
    client = OpenAIChatCompletionClient(
        model="tier-balanced",
        async_client=attributed_openai(
            base_url=prov["base_url"],
            api_key=prov["api_key"],
            default_headers={"X-CC-Agent": "org-admin", "X-CC-Source": "chat"},
        ),
    )
    return [
        Agent(
            client=client,
            instructions=INSTRUCTIONS,
            name="org-admin",
            description=(
                "Owns the org-admin tool pack: list the members of the organization and "
                "the sign-in requests, set the roles of a member, suspend or reactivate a "
                "member, and approve or deny a sign-in request. It also manages the groups, "
                "the custom roles, the overrides of a member, the name of the "
                "organization and the seats, and it removes or purges a member. It also "
                "reads, tests and disconnects the Integrations of the organization, revokes "
                "an agent connection and sets the switches of an MCP server. Each change asks "
                "the member first."
            ),
            tools=_agent_tools(),
        )
    ]


__all__ = ["INSTRUCTIONS", "_agent_tools", "_register_agent_tools", "build_agents"]
