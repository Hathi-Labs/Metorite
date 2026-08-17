"""agent-app-builder — App Workshop builder.

Builds and edits small internal web apps (Custom Apps) inside the app's own
workspace.  The executor binds each chat session's working directory to the
app workspace via ``allow_session_workspace`` (see config.json), so this
agent needs no per-app configuration of its own.

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
_BUILD_SCRIPT = Path(__file__).parent / "build" / "build_t2.mjs"
_INSTALL_SCRIPT = Path(__file__).parent / "build" / "install_t2_deps.mjs"

_RAW_INSTRUCTIONS = _INSTRUCTIONS_FILE.read_text(encoding="utf-8") if _INSTRUCTIONS_FILE.exists() else (
    "You are the Metorite App Workshop builder. "
    "Edit the app workspace's index.html; keep it valid after every round."
)
# The T2 (React) build/install commands are literal, concrete strings in the
# system prompt — correct in dev and prod regardless of where the monorepo is
# checked out, with no env-passthrough plumbing for the agent to get right.
INSTRUCTIONS = _RAW_INSTRUCTIONS.replace(
    "__T2_BUILD_SCRIPT__", f"node {_BUILD_SCRIPT}",
).replace(
    "__T2_INSTALL_SCRIPT__", f"node {_INSTALL_SCRIPT}",
)


def _llm_provider() -> dict[str, Any]:
    base_url = os.environ.get("LITELLM_BASE_URL", "http://127.0.0.1:8080")
    api_key = os.environ.get("LITELLM_MASTER_KEY", "sk-local")
    return {"type": "openai", "base_url": f"{base_url}/v1", "api_key": api_key}


def build_agent() -> GitHubCopilotAgent:
    # No on_permission_request here: the executor injects the risk-aware
    # permission handler (permission_policy) when none is set.
    return GitHubCopilotAgent(
        instructions=INSTRUCTIONS,
        tools=[],
        default_options={
            "model": "tier-balanced",
            "provider": _llm_provider(),
            "mcp_servers": {},
        },
    )


def build_agents() -> list[GitHubCopilotAgent]:
    return [build_agent()]
