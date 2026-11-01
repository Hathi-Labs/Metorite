"""Studio Builder - the owner of the ``studio`` tool pack (STU-G1).

Spec: ``project-docs/specs/ai_studio.md`` section 6.2.

This agent is **not a second assistant** (D99, D125). The member talks to
Mira only. The pack machinery (``acb_skills.tool_packs.owner_tools``) reads
:func:`_agent_tools` from this module, and the main assistant holds those
tools in a Studio turn. The tools are in ``acb_skills.studio_tools`` and this
module keeps no copy. They are stateless, read the action catalog over HTTP
as the member, and take no org or member argument.

It never runs as an agent. The name "studio-builder" is in no agent list: not
the registry, the picker, a specialist tool or the registry block. Only the
pack loader reads its folder, from ``gateway.routes.agent._PACK_OWNER_AGENTS``.
A hand-off to it, and a run of it, is refused in every run
(``tool_packs.pack_only_refusal``). ``build_agents()`` stays because the
folder fences build each agent folder (``tests/unit/test_agent_voice.py``).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from acb_common import get_settings
from acb_skills.studio_tools import STUDIO_TOOLS

_INSTRUCTIONS_FILE = Path(__file__).parent / "instructions.md"
INSTRUCTIONS = (
    _INSTRUCTIONS_FILE.read_text(encoding="utf-8")
    if _INSTRUCTIONS_FILE.exists()
    else "You hold the AI Studio builder tools. You are not a second assistant."
)

_TOOLS = list(STUDIO_TOOLS)


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
    """Construct the Studio Builder as a native MAF agent (as ``agent-crm`` does)."""
    from acb_llm.attribution import attributed_openai
    from agent_framework import Agent
    from agent_framework.openai import OpenAIChatCompletionClient

    prov = _llm_provider()
    client = OpenAIChatCompletionClient(
        model=os.environ.get("STUDIO_AGENT_MODEL", "tier-balanced"),
        async_client=attributed_openai(
            base_url=prov["base_url"],
            api_key=prov["api_key"],
            default_headers={"X-CC-Agent": "studio-builder", "X-CC-Source": "chat"},
        ),
    )
    return [
        Agent(
            client=client,
            instructions=INSTRUCTIONS,
            name="studio-builder",
            description=(
                "Owns the studio tool pack: draft, patch, validate and outline an "
                "AI Studio app manifest, and list the actions an app may use."
            ),
            tools=_agent_tools(),
        )
    ]


__all__ = ["INSTRUCTIONS", "_agent_tools", "_register_agent_tools", "build_agents"]
