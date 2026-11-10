"""agent-apis-config — API Configuration Assistant.

Helps users discover, add, and configure API connections for Metorite.
Uses web_search (SerpAPI) to find accurate API documentation and authentication guides.

**A native MAF agent since 2026-10-03** (agent_architecture.md §11.3). It was
a ``GitHubCopilotAgent`` and used no Copilot-only capability: one plain tool
here, ``fetch_page`` and ``install_dependency`` injected by the executor, and
no MCP server. It now builds the same ``agent_framework.Agent`` +
``OpenAIChatCompletionClient`` pair as agent-email-assistant and agent-crm, so
the executor runs it on Tier 1.

Exports:
    build_agents() -> list[Agent]
    build_agent()  -> Agent
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from acb_common import get_settings

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

#: The name the registry, the run allowlist and the usage ledger know.
AGENT_NAME = "apis-config"

#: The build-time model. The executor replaces it with the run's resolved tier
#: (``_apply_model_for_maf_agent``), exactly as it did on the Copilot path.
MODEL = "tier-balanced"


def _llm_provider() -> dict[str, Any]:
    """BYOK provider config pointing at the gateway's /v1 (litellm SDK).

    Prefer the gateway's real key from Settings (``litellm_master_key``) over a
    bare ``sk-local`` fallback. On the native path the key built here is the
    one the run presents.

    ⚠️ No member header here, on purpose (H-181): one agent serves every
    person. :func:`acb_llm.attribution.attributed_openai` stamps the member,
    the app and the run on EACH request, from the run context.
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
    """Construct the API Configuration Assistant as a NATIVE MAF agent.

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
            "API Configuration Assistant — discovers APIs, generates credential "
            "schemas, and guides setup. Ask it to add any API service by name."
        ),
        tools=list(_TOOLS),
    )


def build_agents() -> list[Any]:
    """Dynamic Agent Loader entry point."""
    return [build_agent()]


__all__ = ["AGENT_NAME", "INSTRUCTIONS", "MODEL", "build_agent", "build_agents"]
