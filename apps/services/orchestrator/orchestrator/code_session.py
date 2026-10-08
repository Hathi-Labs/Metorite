"""One-shot coding session for the ``code_task`` platform skill.

⚠️ **Two engines (D82, WS-43e).** :func:`run_maf_code_session` runs the
session on a MAF harness agent, with each command in the sandbox broker's
container. ``code_tools.code_task`` takes it only for an org that
``MAF_CODING_SCOPE`` names as ``code_task:<org>``. Every other run takes
:func:`run_copilot_code_session`, unchanged. WS-43j removes the Copilot half
after the parity eval (an owner gate, WS43-G7). The text below is the
Copilot half.

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
from pathlib import Path
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
    from orchestrator.executor import _install_copilot_permission_handler

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
    _install_copilot_permission_handler(agent)

    _log.info(
        "code_session.start", workspace=workspace, model=model,
        task_preview=task[:120], sandboxed=sandbox_handle is not None,
    )
    from acb_skills.write_artifact import (
        derive_artifact_context,
        enter_artifact_context,
        reset_artifact_context,
    )

    from orchestrator.copilot_agent import carry_run_context

    # H-201 (§21.16): the sandbox root lives in this call's own copy of the
    # run's artifact context, and the token gives the run its exact context
    # back. The code-task session's SDK callbacks run in that copy.
    artifact_token = enter_artifact_context()
    try:
        if sandbox_handle is not None:
            derive_artifact_context(permission_check_root=CONTAINER_WORKSPACE)
        carry_run_context(agent)
        async with agent:
            result = await asyncio.wait_for(agent.run(task), timeout=timeout)
    finally:
        reset_artifact_context(artifact_token)
        if sandbox_handle is not None:
            await stop_copilot_sandbox(sandbox_handle)
    text = getattr(result, "text", None) or str(result)
    _log.info("code_session.done", chars=len(text))
    return text


# ── The MAF engine (WS-43e, D82, spec maf_coding_engine.md §7.6) ─────────────
#
# Dark by construction. ``code_tools.code_task`` takes this path only when
# ``MAF_CODING_SCOPE`` names ``code_task`` for the run's own organization
# (:func:`maf_engine_for_run`), and the scope is empty by default. Every other
# run keeps :func:`run_copilot_code_session`, unchanged.
#
# The model loop runs HERE, on the host, with no shell. Each command of the
# session runs in the sandbox broker's container, which has no network and no
# key (§7.1). The model calls go through the gateway ``/v1``, so the Router
# binds the tier and passes the credentials per call (D56, D58). Nothing in
# this half imports ``copilot`` (done-when 1, fence WS43-F15).
#
# Fences: WS43-F7 ``tests/unit/test_maf_code_session.py``, WS43-F8
# ``tests/unit/test_maf_harness_contract.py`` and WS43-F13
# ``tests/unit/test_no_host_git_on_sandbox_dir.py``.

#: The ``X-CC-Source`` of every model call of the session (done-when 6).
CODE_TASK_SOURCE = "code_task"
#: The scope target of this engine (§7.1).
CODE_TASK_TARGET = "code_task"

#: The ``create_harness_agent`` flags of the session. WS43-F8 pins each one.
#: Todo and mode add round trips (§5.6 item 4). Web search would give the
#: session a network that the container does not have. File memory would
#: write to the gateway's working dir (§4.4). The file tools ask no person,
#: because a one-shot session has no person, and the store is the boundary.
HARNESS_FLAGS: dict[str, bool] = {
    "disable_todo": True,
    "disable_mode": True,
    "disable_web_search": True,
    "disable_file_memory": True,
    "file_access_disable_readonly_tool_approval": True,
    "file_access_disable_write_tool_approval": True,
}

#: The function invocation configuration of the session's client. The tool
#: calls of one model response run in model order, one at a time (§7.5).
FUNCTION_INVOCATION: dict[str, bool] = {"allow_concurrent_invocation": False}

#: The nudge of the empty-answer retry (§5.6 item 1, §7.6 middleware 1).
EMPTY_ANSWER_NUDGE = (
    "Your last answer was empty. Continue the task. When it is done, end with "
    "the report: what you created or changed, how to run it, and the key output."
)

_MAF_HARNESS_INSTRUCTIONS = """You are Metorite's coding engine, invoked as a \
bounded tool by another agent. You write, edit, run and test scripts in THIS \
agent's workspace. You have NO memory of previous sessions. The workspace is \
the memory. Follow this contract exactly.

Your tools:
- `run_command(command, timeout_s)` runs one bash command in a Linux sandbox. \
The sandbox sees the workspace at `/workspace`. It has NO network and NO \
credentials.
- The `file_access_*` tools read, write, edit, list, search and delete the \
files of the workspace. Their paths start with `agent-data/`, `inputs/` or \
`outputs/`.
- `load_skill`, `read_skill_resource` and `run_skill_script` use the skills \
under `agent-data/skills/`.
- `request_network_access(reason, hosts)` asks for the network.

1. FIRST read `agent-data/SCRIPTS.md` (if it exists). It is the manifest of \
the scripts that previous sessions made. If the task concerns an existing \
script, EDIT IT IN PLACE. Do not write a duplicate.
2. Put a reusable script under `agent-data/scripts/`. Put a reusable skill \
under `agent-data/skills/<name>/`, as `SKILL.md` with `name` and \
`description` front matter, and its scripts under `scripts/`.
3. Put one-off scratch work and generated data or output files under \
`outputs/`.
4. Run what you write, with `run_command`. Fix errors until it works, or \
until you can say exactly why it cannot work.
5. Before you finish, update `agent-data/SCRIPTS.md`: one section for each \
script (name, purpose, usage and arguments, a last-changed note). Make the \
file if it is missing.
6. Do not use git. Nothing in this session is committed or pushed.
7. Never touch files outside the workspace. Never install system packages. \
An install needs an approved network request first. Then `pip install` puts \
the package in this chat's own `.local` folder.
8. A script gets no credential and no network in the sandbox. Never \
hard-code, print, log or write a credential value into any file.
9. End with a concise report: what you created or changed, how to run it, \
and the key output of the final run.
"""


class CodeSessionError(RuntimeError):
    """The MAF coding session gave no report. Nothing ran on the host."""


class CodeSessionRefused(CodeSessionError):
    """The sandbox refused or failed. ``code_task`` does not fall back (§7.1 rule 14)."""


class CodeSessionEmpty(CodeSessionError):
    """The model gave an empty answer twice (§7.6 middleware 1)."""


def maf_engine_for_run() -> bool:
    """True when ``MAF_CODING_SCOPE`` names ``code_task`` for the run's own org.

    ``code_tools.code_task`` asks this on each call (§7.6). The org comes from
    the run binding (``executor._current_run_org``), never from input (R5).
    A run with no org, and a scope that does not parse, answer ``False``, so
    the run keeps the Copilot path, unchanged (done-when 2).
    """
    try:
        from orchestrator import sandbox_broker as sb
        from orchestrator.executor import _current_run_org
    except ImportError:
        return False
    org = _current_run_org()
    return bool(org) and sb.maf_coding_scope_allows(CODE_TASK_TARGET, str(org).strip())


def last_assistant_text(response: Any) -> str:
    """The text of the LAST assistant message of *response*, never ``response.text``.

    ``response.text`` joins the text of every message. In the spike it held
    the compaction lines (``[Tool results: …]``) and the narration of every
    turn (§5.6 item 3). So the report is the last assistant message.
    """
    for message in reversed(list(getattr(response, "messages", None) or [])):
        if str(getattr(message, "role", "")) == "assistant":
            return str(getattr(message, "text", "") or "").strip()
    return ""


async def run_with_empty_retry(agent: Any, task: str) -> str:
    """Run *task*, and ask again ONCE when the final answer is empty.

    §7.6 middleware 1, for the silent empty answer of §5.6 item 1. A second
    empty answer raises :class:`CodeSessionEmpty`. The retry runs in the same
    session, so the model sees the work it did before.
    """
    session = agent.create_session()
    report = last_assistant_text(await agent.run(task, session=session))
    if report:
        return report
    _log.warning("code_session.empty_answer", attempt=1)
    report = last_assistant_text(await agent.run(EMPTY_ANSWER_NUDGE, session=session))
    if report:
        return report
    _log.warning("code_session.empty_answer", attempt=2)
    raise CodeSessionEmpty("the coding session gave an empty answer twice.")


def code_task_client(model: str) -> Any:
    """The session's chat client: the gateway ``/v1``, so every call goes through the Router.

    ``OpenAIChatCompletionClient`` with :func:`acb_llm.attribution.attributed_openai`,
    which stamps the member, the app and the run on each request. The headers
    name the CALLING agent and ``X-CC-Source: code_task`` (done-when 6). The
    key is the LLM API key, never the identity token, and it stays on the
    host: the container gets commands, never a client (§4.2).
    """
    from acb_llm.attribution import attributed_openai
    from acb_skills.write_artifact import artifact_context
    from agent_framework.openai import OpenAIChatCompletionClient

    settings = get_settings()
    gw_base = (
        getattr(settings, "litellm_base_url", "") or "http://127.0.0.1:8080"
    ).rstrip("/")
    gw_key = (getattr(settings, "llm_api_key", "") or "sk-local").strip()
    caller = str(artifact_context().get("agent_name") or "code-task")
    return OpenAIChatCompletionClient(
        model=model,
        async_client=attributed_openai(
            base_url=f"{gw_base}/v1",
            api_key=gw_key,
            default_headers={"X-CC-Agent": caller, "X-CC-Source": CODE_TASK_SOURCE},
        ),
        function_invocation_configuration=dict(FUNCTION_INVOCATION),
    )


def _session_tool_pin() -> list[Any]:
    """The pair that keeps the session to its own tools (``acb_skills.tool_guard``).

    A tool that is not in ``CODE_TASK_SESSION_TOOLS`` is taken out of each
    model request, and refused on a call. So an ``agent-framework-core``
    upgrade that adds a hosted tool, such as a web search or a shell, gives
    the session no new door. No session tool is an egress tool (H-236),
    because the container has no network.
    """
    from acb_skills.sandbox_tools import CODE_TASK_SESSION_TOOLS
    from acb_skills.tool_guard import RefuseTools, WithholdTools, tool_name

    def foreign(item: Any) -> bool:
        return tool_name(item) not in CODE_TASK_SESSION_TOOLS

    def answer(name: str) -> str:
        return f"{name} is not a tool of this coding session. Nothing ran."

    return [WithholdTools(foreign), RefuseTools(foreign, answer)]


def build_code_task_agent(*, store: Any, skills: Any, tools: list[Any], model: str) -> Any:
    """The harness agent of one session (§7.6). WS43-F8 pins its parameters."""
    from agent_framework import create_harness_agent

    return create_harness_agent(
        code_task_client(model),
        name="code-task",
        agent_instructions=_MAF_HARNESS_INSTRUCTIONS,
        tools=tools,
        file_access_store=store,
        skills_provider=skills,
        middleware=_session_tool_pin(),
        **HARNESS_FLAGS,
    )


async def run_maf_code_session(
    *,
    task: str,
    workspace: str,
    timeout: float = CODE_SESSION_TIMEOUT_SECONDS,
    model: str = "tier-balanced",
) -> str:
    """Run one bounded MAF harness session in *workspace*; return its report.

    §7.6. The sandbox container is acquired FIRST, so a broker that refuses or
    fails gives :class:`CodeSessionRefused` before any model call, and nothing
    runs on the host (done-when 3). The run binding gives the org, the agent,
    the thread and the dir (R5). *workspace* must be the dir that the binding
    gives, or the session refuses.

    The session runs in the run's own artifact context (H-201), so each tool
    reads the right tenant. The session holds the lease of its container to
    the end, so no eviction stops it between two commands. The report is the
    last assistant message (:func:`last_assistant_text`). Raises
    :class:`CodeSessionError` or :class:`TimeoutError`.
    """
    from acb_skills import sandbox_tools
    from acb_skills.write_artifact import enter_artifact_context, reset_artifact_context

    from orchestrator import sandbox_broker as sb

    token = enter_artifact_context()
    try:
        try:
            binding = sb.read_run_binding()
        except sb.SandboxError as exc:
            raise CodeSessionRefused(f"the sandbox refused this run: {exc}") from exc
        if binding.target != CODE_TASK_TARGET or not sb.maf_coding_scope_allows(
            CODE_TASK_TARGET, binding.org,
        ):
            raise CodeSessionRefused(
                "MAF_CODING_SCOPE does not name code_task for this agent and organization."
            )
        from acb_skills.agent_paths import is_tenant_instance

        if is_tenant_instance(binding.instance):
            # A shared agent's tenant dir holds every thread's inputs and
            # outputs and every member's skills. The `code_task` container
            # mounts it whole and read-write, so it may not start there until
            # this target gets the thread and skill covers of `projects`
            # (review, WS-43f). D85 keeps code_task from such a run today.
            raise CodeSessionRefused(
                "code_task runs in the sandbox only for a personal working dir."
            )
        try:
            same_dir = Path(workspace).resolve() == binding.workspace
        except (OSError, RuntimeError):
            same_dir = False
        if not same_dir:
            raise CodeSessionRefused("the workspace of this call is not the run's own dir.")
        broker = sb.get_broker()
        try:
            handle = await broker.acquire()
        except sb.SandboxError as exc:
            raise CodeSessionRefused(f"the sandbox did not start: {exc}") from exc
        _log.info(
            "code_session.maf_start", org=binding.org, agent=binding.agent,
            model=model, task_preview=task[:120],
        )
        try:
            store, skills, tools = sandbox_tools.code_task_session_parts(binding)
            agent = build_code_task_agent(store=store, skills=skills, tools=tools, model=model)
            report = await asyncio.wait_for(run_with_empty_retry(agent, task), timeout=timeout)
        finally:
            await broker.release(handle)
    finally:
        reset_artifact_context(token)
    _log.info("code_session.maf_done", chars=len(report))
    return report
