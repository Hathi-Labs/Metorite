"""agent-apis-config — API Configuration Assistant.

Helps users discover, add, and configure API connections for Metorite.
Uses web_search (SerpAPI) to find accurate API documentation and authentication guides.

Exports:
    build_agents() -> list[GitHubCopilotAgent]
    build_agent()  -> GitHubCopilotAgent
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from agent_framework_github_copilot import GitHubCopilotAgent

_INSTRUCTIONS_FILE = Path(__file__).parent / "instructions.md"
INSTRUCTIONS = _INSTRUCTIONS_FILE.read_text(encoding="utf-8") if _INSTRUCTIONS_FILE.exists() else (
    "You are the Metorite API Configuration Assistant. "
    "Help users discover and configure API connections."
)


# ---------------------------------------------------------------------------
# Tools — injected from acb_skills at executor level; also import directly
# ---------------------------------------------------------------------------

_TOOLS: list[Any] = []

try:
    from acb_skills.web_tools import web_search  # type: ignore[import]
    _TOOLS.append(web_search)
except ImportError:
    pass  # web_search injected by executor if SerpAPI is configured


# ---------------------------------------------------------------------------
# Agent factory
# ---------------------------------------------------------------------------

def _llm_provider() -> dict[str, Any]:
    base_url = os.environ.get("LITELLM_BASE_URL", "http://127.0.0.1:8080")
    api_key  = os.environ.get("LITELLM_MASTER_KEY", "sk-local")
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
    return [build_agent()]
