"""agent-app-builder — App Workshop builder.

Builds and edits small internal web apps (Custom Apps) inside the app's own
workspace.  The executor binds each chat session's working directory to the
app workspace via ``allow_session_workspace`` (see config.json), so this
agent needs no per-app configuration of its own.

Two engines (WS-43h, D84, spec ``maf_coding_engine.md`` §7.8). The factory
decides on each run:

* ``MAF_CODING_SCOPE`` names ``app_builder`` for the run's own org: a MAF
  harness agent (``orchestrator.code_session.build_app_builder_agent``), on
  the same factory as ``code_task``. Its file tools and its ``run_command``
  run through the sandbox broker.
* Every other run, and a run with no org: the ``GitHubCopilotAgent``,
  unchanged. It stays until WS-43j (task 2283) retires it.

Exports:
    build_agents()    -> list[Any]  (one agent, of the engine of this run)
    build_agent()     -> GitHubCopilotAgent
    build_maf_agent() -> agent_framework.Agent
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

#: The tools of the MAF engine, after the text of ``instructions.md``. The
#: text of that file does not change.
MAF_ENGINE_NOTE = """

## Your tools

- The `file_access_*` tools read, write, edit, list and search the files of
  the app. A path is relative to the app, for example `app.json`,
  `index.html` or `src/App.tsx`.
- `run_command` runs one bash command in a Linux sandbox. The sandbox sees
  the app at `/workspace`, and each command starts there. It has no network.
  Run the build with it.
"""


def maf_instructions() -> str:
    """``instructions.md`` for the MAF engine, with the sandbox paths of the scripts.

    The broker mounts a copy of ``build/`` at ``APP_BUILDER_BUILD_TARGET``,
    so the commands name that path, never a host path.
    """
    from orchestrator.sandbox_broker import APP_BUILDER_BUILD_TARGET

    return _RAW_INSTRUCTIONS.replace(
        "__T2_BUILD_SCRIPT__", f"node {APP_BUILDER_BUILD_TARGET}/build_t2.mjs",
    ).replace(
        "__T2_INSTALL_SCRIPT__", f"node {APP_BUILDER_BUILD_TARGET}/install_t2_deps.mjs",
    ) + MAF_ENGINE_NOTE


def _llm_provider() -> dict[str, Any]:
    # ⚠️ No ``headers`` here, on purpose (H-181): one agent serves every
    # person. The orchestrator stamps the run's X-CC-* headers when each run
    # creates or resumes its session (session_kwargs_for_this_run).
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


def build_maf_agent() -> Any:
    """The MAF harness agent of app-builder (WS-43h, §7.8)."""
    from orchestrator.code_session import build_app_builder_agent

    return build_app_builder_agent(
        instructions=maf_instructions(),
        model="tier-balanced",
        description="App Workshop builder: edits an existing App Workshop app.",
    )


def _maf_engine_for_run() -> bool:
    """True when ``MAF_CODING_SCOPE`` names ``app_builder`` for this run's org."""
    try:
        from orchestrator.code_session import app_builder_engine_for_run
    except ImportError:
        return False
    return app_builder_engine_for_run()


def build_agents() -> list[Any]:
    """One agent, of the engine that this run's org gets (§7.8)."""
    if _maf_engine_for_run():
        return [build_maf_agent()]
    return [build_agent()]
