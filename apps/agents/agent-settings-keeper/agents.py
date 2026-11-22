"""Settings Keeper - the owner of the ``settings`` tool pack (PAR-5, task 3108).

Spec: ``project-docs/specs/chat_parity.md`` §6.3, rung 3.

This agent is **not a second assistant** (D99, D125). The member talks to
Mira only. The pack machinery (``acb_skills.tool_packs.owner_tools``) reads
:func:`_agent_tools` from this module, and the main assistant holds those
tools when the member asks about a setting. The tools are in
``acb_skills.settings_tools`` and ``acb_skills.profile_tools``, and this
module keeps no copy. They call the gateway routes over HTTP as the member,
and take no org and no member argument.

It never runs as an agent. The name "settings-keeper" is in no agent list:
not the registry, the picker, a specialist tool or the registry block. Only
the pack loader reads its folder, from the list of pack owners in
``gateway.routes.agent``. A hand-off to it, and a run of it, is refused in every run (``tool_packs.pack_only_refusal``).
``build_agents()`` stays because the folder fences build each agent folder
(``tests/unit/test_agent_voice.py``).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from acb_common import get_settings
from acb_skills.profile_tools import READ_TOOLS as PROFILE_READ_TOOLS
from acb_skills.settings_tools import SETTINGS_TOOLS

_INSTRUCTIONS_FILE = Path(__file__).parent / "instructions.md"
INSTRUCTIONS = (
    _INSTRUCTIONS_FILE.read_text(encoding="utf-8")
    if _INSTRUCTIONS_FILE.exists()
    else "You hold the settings tools. You are not a second assistant."
)

_TOOLS = [*SETTINGS_TOOLS, *PROFILE_READ_TOOLS]


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
    """Construct the Settings Keeper as a native MAF agent (as ``agent-crm`` does)."""
    from acb_llm.attribution import attributed_openai
    from agent_framework import Agent
    from agent_framework.openai import OpenAIChatCompletionClient

    prov = _llm_provider()
    client = OpenAIChatCompletionClient(
        model=os.environ.get("SETTINGS_AGENT_MODEL", "tier-balanced"),
        async_client=attributed_openai(
            base_url=prov["base_url"],
            api_key=prov["api_key"],
            default_headers={"X-CC-Agent": "settings-keeper", "X-CC-Source": "chat"},
        ),
    )
    return [
        Agent(
            client=client,
            instructions=INSTRUCTIONS,
            name="settings-keeper",
            description=(
                "Owns the settings tool pack: read the member's settings and "
                "assistant profile, change the member's own look, time zone, "
                "starred apps and number system with an undo, and change the "
                "organization's default look, number system and logo after one card."
            ),
            tools=_agent_tools(),
        )
    ]


__all__ = ["INSTRUCTIONS", "_agent_tools", "_register_agent_tools", "build_agents"]
