"""One-shot Copilot SDK coding session for the ``code_task`` platform skill.

The Copilot SDK is Metorite's coding ENGINE (chat_agent_framework_review
§2): native MAF agents delegate script authoring/editing to a bounded Copilot
session through the ``code_task`` tool (acb_skills.code_tools) instead of
being standalone Copilot agents themselves.

Each session is deliberately per-call (no service_session_id persistence):
continuity lives in the WORKSPACE, not the conversation — the harness prompt
enforces the manifest-first convention (read ``agent-data/SCRIPTS.md``, edit
scripts in place under ``agent-data/scripts/``, update the manifest), and the
skill layer mirrors the results into the blob store so scripts survive
restarts, redeploys, and volume wipes.

BYOK: the session routes through the gateway ``/v1`` (same provider block the
executor builds for Tier-1.5 agents), so it inherits the platform's model
tiers, context-window guard, and cost observability.
"""
from __future__ import annotations

import asyncio
from typing import Any

from acb_common import get_logger, get_settings

_log = get_logger("orchestrator.code_session")

# Hard wall-clock budget for one coding session. Generous enough to write,
# run, and fix a script; far below the sub-agent budget so a wedged session
# surfaces to the calling agent as a tool error, not a hung turn.
CODE_SESSION_TIMEOUT_SECONDS = 600.0

_HARNESS_INSTRUCTIONS = """You are Metorite's coding engine, invoked as a \
bounded tool by another agent. You write, edit, run, and test scripts inside \
THIS agent's workspace. You have NO memory of previous sessions — the \
workspace is the memory. Follow this contract exactly:

1. FIRST read `agent-data/SCRIPTS.md` (if it exists) — the manifest of \
scripts previous sessions created. If the task concerns an existing script, \
EDIT IT IN PLACE rather than writing a duplicate.
2. TWO script homes — pick the right one:
   a. `agent-data/scripts/` — the agent's personal reusable scripts. This is \
the DEFAULT home for new scripts (not git-tracked; the platform persists them \
separately).
   b. Git-TRACKED repo source (`skills/*/scripts/`, `agents.py`, other \
checked-in code) — the agent's BUILT-IN skills. When the task is to fix or \
change one of these, edit it in place there; do NOT copy it into agent-data/.
3. One-off scratch work and generated data/output files go under `outputs/`.
4. Run what you write. Fix errors until it works or you can explain exactly \
why it cannot.
5. Before finishing, update `agent-data/SCRIPTS.md`: one section per script \
(name, purpose, usage/args, last-changed note). Create the file if missing. \
(Workspace scripts only — repo skills are catalogued by their own SKILL.md.)
6. If you changed git-TRACKED files: `git add` the specific files and \
`git commit` locally with a clear message (identity is pre-configured). \
NEVER push and never create a branch — the platform queues every local \
commit for human approval and pushes it after approval. Never commit \
`agent-data/`, `inputs/`, or `outputs/` (ignored runtime state).
7. Never touch files outside the working directory. Never install system \
packages; Python deps go through `uv pip install` into the current venv only \
when genuinely needed.
7b. Integration credentials: if the task lists available integrations, \
scripts must read their env vars with `os.getenv` at RUN time. NEVER \
hard-code, print, log, or write a credential value into any file — scripts \
must degrade with a clear message when a var is unset.
8. End with a concise report: what you created/changed, how to run it, and \
the final run's key output.
"""


async def run_copilot_code_session(
    *,
    task: str,
    workspace: str,
    timeout: float = CODE_SESSION_TIMEOUT_SECONDS,
    model: str = "tier-balanced",
) -> str:
    """Run one bounded Copilot coding session in *workspace*; return its report.

    Raises on timeout or session failure — the skill layer turns that into a
    structured tool error for the calling agent.

    BO-7 phase 2: when ``"code_task"`` is in ``settings.copilot_sandbox_scope``,
    runs the session's CLI inside a hardened container (copilot_sandbox.py)
    instead of the host process. Falls back to the existing in-process session
    unchanged whenever the sandbox fails to spawn or come up in time — the
    scope flag never turns a spawn failure into a hard error.
    """
    from orchestrator.copilot_agent import MetoriteCopilotAgent
    from orchestrator.executor import _copilot_permission_handler

    settings = get_settings()
    gw_base = (
        getattr(settings, "litellm_base_url", "") or "http://127.0.0.1:8080"
    ).rstrip("/")
    # /v1 only — LLM API key, not the identity token (BO-2 residual #4).
    gw_key = (getattr(settings, "llm_api_key", "") or "sk-local").strip()

    default_options: dict[str, Any] = {
        "model": model,
        "provider": {
            "type": "openai",
            "base_url": f"{gw_base}/v1",
            "api_key": gw_key,
        },
        "working_directory": workspace,
    }

    scope = str(getattr(settings, "copilot_sandbox_scope", "") or "")
    sandbox_handle = None
    permission_check_root_prev: str | None = None
    if "code_task" in {s.strip() for s in scope.split(",") if s.strip()}:
        from orchestrator.copilot_sandbox import (
            CONTAINER_WORKSPACE,
            spawn_copilot_sandbox,
            stop_copilot_sandbox,
        )
        sandbox_handle = await spawn_copilot_sandbox(
            workspace=workspace, label="code_task", settings=settings,
        )
        if sandbox_handle is not None:
            default_options["working_directory"] = CONTAINER_WORKSPACE

    agent = MetoriteCopilotAgent(
        name="code-task",
        instructions=_HARNESS_INSTRUCTIONS,
        default_options=default_options,
    )
    if sandbox_handle is not None:
        agent._sandbox_cli_url = sandbox_handle.cli_url
    try:
        if getattr(agent, "_permission_handler", None) is None:
            agent._permission_handler = _copilot_permission_handler()
    except Exception:
        pass

    _log.info(
        "code_session.start", workspace=workspace, model=model,
        task_preview=task[:120], sandboxed=sandbox_handle is not None,
    )
    try:
        if sandbox_handle is not None:
            from acb_skills.write_artifact import _WRITE_ARTIFACT_CONTEXT
            permission_check_root_prev = _WRITE_ARTIFACT_CONTEXT.get("permission_check_root")
            _WRITE_ARTIFACT_CONTEXT["permission_check_root"] = CONTAINER_WORKSPACE
        async with agent:
            result = await asyncio.wait_for(agent.run(task), timeout=timeout)
    finally:
        if sandbox_handle is not None:
            from acb_skills.write_artifact import _WRITE_ARTIFACT_CONTEXT
            if permission_check_root_prev is None:
                _WRITE_ARTIFACT_CONTEXT.pop("permission_check_root", None)
            else:
                _WRITE_ARTIFACT_CONTEXT["permission_check_root"] = permission_check_root_prev
            await stop_copilot_sandbox(sandbox_handle)
    text = getattr(result, "text", None) or str(result)
    _log.info("code_session.done", chars=len(text))
    return text
