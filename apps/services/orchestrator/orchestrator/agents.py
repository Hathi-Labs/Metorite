"""MAF-based orchestrator agent (WBS 0.7 — replaces LangGraph graph.py harness).

Architecture:
    A single MAF ``Agent`` backed by the gateway /v1 (litellm SDK).  Specialist agents from
    registered GitHub repos are exposed as native MAF tools via agent.as_tool(),
    making cross-agent routing automatic and based on each agent's description.

    Dynamic capability registry: at orchestrator build time, all registered
    agents are loaded and exposed as tools. The LLM routes to the right one
    based on the description in each agent's config.json.

Exports:
    build_orchestrator_agent(*, with_history: bool = True) -> Agent
    build_agents() -> list[Agent]   ← Dynamic Agent Loader entry point
"""
from __future__ import annotations

import asyncio
from typing import Any

from acb_common import get_logger, get_settings
from agent_framework import Agent
from agent_framework.openai import OpenAIChatCompletionClient

from orchestrator.retrieval import format_context, retrieve
from orchestrator.sales_views import sales_context as _sales_context_fn

# Memory layer (WBS 2.5) — imported lazily; graceful if acb_memory not installed.
try:
    from acb_memory import add_memories_background, get_memory_context, search_entity_timeline
    _MEMORY_AVAILABLE = True
except ImportError:
    _MEMORY_AVAILABLE = False

    async def get_memory_context(*_a, **_kw) -> str:  # type: ignore[misc]
        return ""

    async def add_memories_background(*_a, **_kw) -> None:  # type: ignore[misc]
        pass

    async def search_entity_timeline(*_a, **_kw) -> str:  # type: ignore[misc]
        return ""

_log = get_logger("orchestrator.agents")

# ---------------------------------------------------------------------------
# System instructions
# ---------------------------------------------------------------------------

_PULL_INSTRUCTIONS = """\
You are the AI Company Brain orchestrator for Fracktal Works.

You have three categories of tools:

RETRIEVAL TOOLS (use for broad company data questions):
- retrieve_entity_context: search projects, tasks, deals, people
- retrieve_sales_context: Zoho pipeline, customer health, deal stages
- search_timeline: time-stamped facts about entities (deal stage changes, action history)

SPECIALIST AGENT TOOLS (use when the request is clearly in one agent's domain):
- Each registered agent appears as a tool named after it (e.g. agent_sales_assistant, task_manager).
- Call the specialist tool and relay its full response.
- If a request spans multiple domains, call multiple specialist tools and synthesise.

CREATION / IMPROVEMENT TOOLS:
- spawn_copilot_agent: when the user asks to CREATE, BUILD, or FIX any skill, script, or automation.
- delegate_to_agent: fallback for explicit named delegation when the specialist tool is unavailable.

MEMORY TOOLS (active read/write — maintain continuity across conversations):
- remember(query): search episodic memory for past facts about the current user.
  Call BEFORE making claims about user preferences, history, or context.
- recall_timeline(entity_name, query): search the knowledge graph for time-stamped
  facts about an entity (deal, person, project, company).
- save_memory(fact): persist a single important fact about the current user to
  episodic memory. Future conversations will automatically recall this.
- save_episode(name, content, source?): record a time-stamped episode in the
  knowledge graph (deal stage change, meeting outcome, milestone reached).
  Graphiti extracts entities, relationships, and timestamps automatically.

When to actively save vs. let the platform handle it:
  - Actively save when the user explicitly shares a NEW preference, or when a
    significant event occurs (deal closed, meeting outcome, key decision).
  - Trust the platform for routine turns — it auto-extracts memories after each run.

Rules:
1. For data questions: call retrieve_entity_context or retrieve_sales_context FIRST.
2. For specialist work: call the matching specialist tool directly — the tool description
   tells you exactly what each agent handles. Do not ask the user which agent to use.
3. For multi-domain requests: call multiple specialist tools concurrently (MAF supports this).
4. For creation tasks: call spawn_copilot_agent with a precise description.
5. Every factual claim from retrieval must cite [entity:uuid] tokens exactly as returned.
6. Never expose raw SQL, internal UUIDs outside of citations, or stack traces.
7. Be concise. Bullet points for lists.
8. Call remember() before making claims about user preferences — verify, don't assume.
"""

# ---------------------------------------------------------------------------
# Retrieval tools (sync Postgres calls wrapped in asyncio.to_thread)
# ---------------------------------------------------------------------------

async def retrieve_entity_context(query: str) -> str:
    """Search the entity graph for projects, tasks, deals, and people relevant to the query."""
    # WS-29 acb_graph slice 7: resolve the run's tenant HERE, on the event-loop
    # frame, BEFORE the asyncio.to_thread hop — _RUN_ORG is a plain dict and
    # contextvars do not cross the hop, so the opener must be captured on this
    # frame and closed over by _sync. flag OFF → the unbound get_session
    # (byte-identical); flag ON + org → tenant_session(org) so the FORCE-RLS'd
    # project/task/person/deal reads RETURN ROWS instead of 0; flag ON + no org →
    # None → fail closed (the agent degrades to "no entities", never crashes).
    from orchestrator.executor import _graph_session_opener_current
    _open = _graph_session_opener_current()

    def _sync() -> str:
        if _open is None:
            return "(no matching entities found)"
        with _open() as s:
            hits = retrieve(s, query)
        return format_context(hits) if hits else "(no matching entities found)"
    return await asyncio.to_thread(_sync)


async def retrieve_sales_context(query: str) -> str:
    """Search the sales entity graph for customer 360 summaries and deal pipeline data."""
    # Same before-the-hop tenant capture as retrieve_entity_context (WS-29
    # acb_graph slice 7). sales_context() reads customer/deal/person through the
    # session passed to it, so binding the opener here binds sales_views.py's
    # reads transitively — no change is needed in that module.
    from orchestrator.executor import _graph_session_opener_current
    _open = _graph_session_opener_current()

    def _sync() -> str:
        if _open is None:
            return "(no matching sales data found)"
        with _open() as s:
            hits = _sales_context_fn(s, query)
        return format_context(hits) if hits else "(no matching sales data found)"
    return await asyncio.to_thread(_sync)


async def search_timeline(entity_name: str, query: str) -> str:
    """Search the bi-temporal knowledge graph for time-stamped facts about an entity.

    Use this tool to answer time-aware questions such as:
    - "When did deal X change stage?"
    - "What actions were taken on project Y last month?"
    - "What is the history of contact Rahul's involvement in deals?"

    Returns a formatted list of timestamped facts extracted from all past
    events, conversations, and ingestion records involving the entity.

    Args:
        entity_name: Name of the entity to search (deal name, person, project, company).
        query:       What aspect of the entity's timeline to focus on.

    Returns:
        Formatted string with timestamped facts, or empty string if Graphiti
        is not enabled or no facts found.
    """
    return await search_entity_timeline(entity_name, query)


async def spawn_copilot_agent(    task: str,
    agent_name: str = "orchestrator",
    agent_dir: str | None = None,
) -> str:
    """Spawn a GitHub Copilot SDK agent to create or modify code, skills, or scripts.

    Use this tool when the user asks to:
    - Create a new skill or script in an agent repo
    - Add a new capability / automation to an existing agent
    - Fix a bug or improve an existing script
    - Write tests, add documentation, or refactor code
    - Self-improve any part of the system

    The Copilot SDK agent runs in an isolated container with full access to the
    agent's repository, executes the task autonomously (write code, run tests,
    commit, push), and returns a summary of what was done.

    Args:
        task: Natural language description of what to build or fix.
              Be specific: include file paths, function names, inputs/outputs.
        agent_name: Which agent repo to work in (default: current agent).
        agent_dir: Absolute path to the local clone (auto-resolved if omitted).

    Returns:
        Summary of actions taken and files changed.
    """
    import uuid as _uuid  # noqa: PLC0415

    from orchestrator.mutation import (
        _build_telemetry,  # noqa: PLC0415
        _run_mutation_sandbox,
    )

    run_id = str(_uuid.uuid4())
    settings = get_settings()

    # Build a "task" telemetry block — reuse the mutation infrastructure
    # but with a custom prompt describing the desired new capability.
    class _FakeError(Exception):
        pass

    telemetry = _build_telemetry(
        agent_name, run_id, run_id[:8],
        f"ProactiveTask: {task}",
        settings,
        agent_dir=agent_dir,
        incompatibility=False,
    )
    # Override the prompt with a creation-oriented instruction
    telemetry["_task_override"] = task

    # Use a creation prompt instead of a fix prompt
    from orchestrator.mutation import _build_mutation_prompt  # noqa: PLC0415
    original_error = telemetry["error"]
    telemetry["error"] = (
        f"PROACTIVE TASK (not an error):\n{task}\n\n"
        f"Create or modify the code as described. Commit and push when done."
    )

    # _run_mutation_sandbox returns (commit_staged, commit_sha, diff_text, test_summary, container_id)
    commit_staged, commit_sha, _diff, _test_summary, container_id = await _run_mutation_sandbox(
        agent_name, run_id, run_id[:8], telemetry, settings
    )

    telemetry["error"] = original_error  # restore for audit

    if container_id:
        return (
            f"Copilot SDK agent started (container {container_id}). "
            f"Task: {task!r}. "
            f"It will commit and push on completion. "
            f"The next agent run will pick up the changes automatically."
        )
    return (
        f"Could not start Copilot SDK container. "
        f"Task was: {task!r}. "
        f"Check that Docker is running and GITHUB_TOKEN is configured."
    )


async def delegate_to_agent(agent_name: str, message: str) -> str:
    """Delegate a task to a specialist agent and return its response.

    Use this tool when the user's request is clearly in the domain of a specialist agent.
    The specialist agent loads its own system prompt, skills, and tools from its GitHub repo
    and handles the request fully — its response is returned here verbatim.

    When to delegate (examples):
    - Outbound prospecting, lead scraping, Apollo/Google Maps search → "agent-sales-assistant"
    - Task management, sprint status, workload queries → "task-manager"

    Do NOT delegate:
    - Broad company data questions that span multiple agents (use your own retrieval tools)
    - Questions you can already answer from the entity graph

    Args:
        agent_name: Exact registered name, e.g. "agent-sales-assistant", "task-manager".
        message:    The user's full request, reworded if needed to be self-contained.

    Returns:
        The specialist agent's full response text.
    """
    import uuid as _uuid  # noqa: PLC0415

    from orchestrator.executor import (
        AgentRunError,  # noqa: PLC0415
        _active_run_model,
        _active_run_queue,
    )

    run_id = str(_uuid.uuid4())
    # Inherit the orchestrator's resolved tier so the specialist runs on the
    # same model the user chose, not its own config default.
    _parent_model = _active_run_model.get(None)

    # Use streaming path if a parent SSE queue is active (gives live UI progress).
    event_queue = _active_run_queue.get(None)
    if event_queue is not None:
        try:
            from orchestrator.executor import _run_sub_agent_streaming  # noqa: PLC0415
            return await _run_sub_agent_streaming(
                agent_name, message, run_id, event_queue, model=_parent_model,
            )
        except Exception as exc:  # noqa: BLE001
            return f"Agent {agent_name!r} failed: {exc}"

    try:
        from orchestrator.executor import run_agent  # noqa: PLC0415
        result = await run_agent(
            agent_name,
            {"message": message, "mode": "chat"},
            run_id=run_id,
            model=_parent_model,
        )
        text = result.get("result") or result.get("answer") or ""
        if isinstance(text, dict):
            text = text.get("content", str(text))
        return str(text) if text else "(agent returned empty response)"
    except AgentRunError as exc:
        return f"Agent {agent_name!r} failed: {exc.original}"
    except Exception as exc:  # noqa: BLE001
        return f"Could not reach agent {agent_name!r}: {exc}"


# ---------------------------------------------------------------------------
# Dynamic capability registry — load all registered agents as MAF tools
# ---------------------------------------------------------------------------

def _load_specialist_agents_as_tools() -> list[Any]:
    """Load every registered agent as a streaming-capable FunctionTool.

    Each specialist is exposed as a direct async closure that calls
    _run_sub_agent_streaming when a parent SSE queue is active (so the
    orchestrator LLM gets live sub-agent progress in the ThinkingContainer)
    or falls back to the batch delegate_to_agent path otherwise.

    Returns a list of FunctionTool objects ready to pass to Agent(tools=[...]).
    Non-loadable agents are skipped with a warning (orchestrator still starts).
    """
    tools: list[Any] = []
    try:
        from gateway.routes.agent import (
            _AGENT_REGISTRY,  # noqa: PLC0415
            _load_dynamic_agents,
        )
        all_agents = _load_dynamic_agents() + _AGENT_REGISTRY
    except ImportError:
        return tools

    settings = get_settings()
    skip_names = {"orchestrator", "strategy"}  # avoid circular delegation

    for entry in all_agents:
        name: str = entry.get("name", "")
        description: str = entry.get("description", "")
        if not name or name in skip_names or not description:
            continue
        try:
            # Enrich the tool description with tags and integrations from config
            # so the orchestrator LLM can route reliably (per SDK best practices:
            # specific descriptions = better intent matching)
            tags = entry.get("tags", [])
            integrations = entry.get("integrations", [])
            extra = ""
            if tags:
                extra += f" Tags: {', '.join(tags)}."
            if integrations:
                extra += f" Requires: {', '.join(integrations)}."

            # Build the tool as a direct async closure that calls the real specialist
            # via _run_sub_agent_streaming (live-streams into parent SSE queue) or
            # falls back to the batch run_agent path.  This gives the orchestrator
            # the same streaming sub-agent visibility that call_agent provides.
            _tool_name = name.replace("-", "_")
            _agent_name = name  # capture for closure
            _description = (
                f"{description}{extra} "
                f"Use this tool for requests clearly in the domain of {name}. "
                f"Do not use for broad company-data questions that span multiple agents."
            )

            async def _specialist_fn(task: str, _n: str = _agent_name) -> str:
                import uuid as _uuid  # noqa: PLC0415
                run_id = str(_uuid.uuid4())
                from orchestrator.executor import (  # noqa: PLC0415
                    _active_run_model,
                    _active_run_queue,
                )
                eq = _active_run_queue.get(None)
                # Inherit the orchestrator's resolved tier for the specialist.
                _pm = _active_run_model.get(None)
                if eq is not None:
                    try:
                        from orchestrator.executor import _run_sub_agent_streaming  # noqa: PLC0415
                        return await _run_sub_agent_streaming(
                            _n, task, run_id, eq, model=_pm,
                        )
                    except Exception as exc:  # noqa: BLE001
                        return f"Agent {_n!r} failed: {exc}"
                return await delegate_to_agent(_n, task)

            _specialist_fn.__name__ = _tool_name
            _specialist_fn.__doc__ = _description

            from agent_framework import FunctionTool  # noqa: PLC0415
            tool = FunctionTool(
                name=_tool_name,
                description=_description,
                func=_specialist_fn,
                input_model={
                    "type": "object",
                    "properties": {
                        "task": {"type": "string", "description": f"Task for {name}. Include all context needed — this agent has no memory of the current conversation."}
                    },
                    "required": ["task"],
                    "additionalProperties": False,
                },
            )
            tools.append(tool)
            _log.info("orchestrator.specialist_tool_registered", agent=name)
        except Exception as exc:  # noqa: BLE001
            _log.warning("orchestrator.specialist_tool_skipped", agent=name, error=str(exc))

    return tools


# ---------------------------------------------------------------------------
# Agent factory
# ---------------------------------------------------------------------------

def _make_openai_client(
    agent_name: str = "orchestrator", source: str = "chat",
) -> OpenAIChatCompletionClient:
    """Build an OpenAIChatCompletionClient pointing at the gateway's own /v1 endpoint.

    The gateway's /v1/chat/completions reads keys from the encrypted Postgres
    store — no separate proxy process needed.  Internal-only (localhost:8080).

    ``default_headers`` stamps the agent identity on every completion request so
    the gateway (v1_compat) can attribute the model call + cost back to THIS
    agent on the observability bus — otherwise a completion arrives as a bare
    HTTP request with no run context and can only be tagged by app. Fail-soft:
    if the header is ever stripped, v1_compat just falls back to source="chat".
    """
    settings = get_settings()
    gateway_base = getattr(settings, "litellm_base_url", "http://127.0.0.1:8080")
    # /v1 only — the LLM API key, NEVER gateway_internal_token. This client is
    # handed to agent code; giving it the identity token is what let any agent
    # authenticate as the platform (BO-2 residual #4). /v1 accepts this key via
    # acb_auth.require_llm_api_auth.
    gateway_key = getattr(settings, "llm_api_key", "") or "sk-local"
    return OpenAIChatCompletionClient(
        base_url=f"{gateway_base}/v1",
        api_key=gateway_key,
        model="tier-balanced",
        default_headers={"X-CC-Agent": agent_name, "X-CC-Source": source},
    )


def build_orchestrator_agent(
    *,
    with_history: bool = True,
) -> Agent:
    """Build the core orchestrator MAF agent with dynamic specialist agent tools.

    Loads every registered agent as a native MAF tool via agent.as_tool().
    The orchestrator LLM sees each specialist's description and routes to the
    right one automatically — no hard-coded routing table needed.
    """
    settings = get_settings()
    client = _make_openai_client()

    context_providers = []
    if with_history:
        from agent_framework.redis import RedisHistoryProvider  # noqa: PLC0415
        context_providers.append(
            RedisHistoryProvider(
                source_id="chat_history",
                redis_url=settings.redis_url,
                max_messages=100,
            )
        )

    # Build the instructions; append memory context when present.
    instructions = _PULL_INSTRUCTIONS

    # Memory tools — active read/write to Mem0 + Graphiti knowledge graph.
    # Gives the orchestrator the same memory capabilities that injected
    # tools provide to named agents via executor._inject_agent_tools().
    try:
        from acb_skills.memory_tools import (
            recall_timeline as _recall_timeline_tool,
        )
        from acb_skills.memory_tools import (  # noqa: PLC0415
            remember,
            save_episode,
            save_memory,
        )
        _memory_tools: list[Any] = [
            remember, _recall_timeline_tool, save_memory, save_episode,
        ]
    except ImportError:
        _memory_tools = []

    # Core tools always present
    core_tools: list[Any] = [
        retrieve_entity_context,
        retrieve_sales_context,
        search_timeline,
        spawn_copilot_agent,
        delegate_to_agent,
    ] + _memory_tools

    # Dynamic specialist tools — each registered agent becomes a callable FunctionTool.
    # The LLM routes to the right one from the description alone.
    specialist_tools = _load_specialist_agents_as_tools()
    all_tools = core_tools + specialist_tools

    _log.info(
        "orchestrator.tools_loaded",
        core=len(core_tools),
        specialist=len(specialist_tools),
        total=len(all_tools),
    )

    return Agent(
        client=client,
        name="orchestrator",
        instructions=instructions,
        tools=all_tools,
        context_providers=context_providers or None,
    )


async def enrich_instructions_with_memory(
    base_agent: Agent,
    user_id: str,
    user_query: str,
    thread_id: str | None = None,
) -> str:
    """Return an instructions string with Mem0 + Graphiti context appended.

    Called by the chat route BEFORE running the agent so per-request memory
    context is injected without rebuilding the agent on every request.

    Returns the base instructions unchanged when memory is disabled / empty.

    When ``thread_id`` is given, the assembled memory block is cached per
    session in Redis (specs/llm_caching_memory.md Phase 4) so it stays
    byte-stable across turns — otherwise Mem0's per-query semantic search
    returns a different block each turn and defeats cross-turn prompt caching.

    A CACHE BREAK sentinel is inserted between the stable base instructions and
    the dynamic memory block so the acb_llm.prompt_cache transform can put an
    Anthropic cache_control breakpoint exactly at that seam. It is consumed
    (Anthropic) or stripped (others) before the request leaves the gateway.
    """

    async def _build_memory_block() -> str:
        parts: list[str] = []
        # Mem0: relevant past facts for this user
        mem_ctx = await get_memory_context(user_id, user_query)
        if mem_ctx:
            parts.append("## Memory from past conversations\n" + mem_ctx)
        # Graphiti: time-aware facts about entities mentioned in the query
        entity_hint = user_query[:80] if user_query else ""
        if entity_hint:
            graph_ctx = await search_entity_timeline(entity_hint, user_query)
            if graph_ctx:
                parts.append(
                    "## Timeline facts from knowledge graph\n" + graph_ctx
                )
        return "\n\n".join(parts)

    # Session-scoped fetch (once per thread) → byte-stable memory across turns.
    memory_block = ""
    try:
        from acb_memory import get_session_memory  # noqa: PLC0415

        _redis = None
        try:
            from orchestrator.stream_relay import _get_client  # noqa: PLC0415
            _redis = await _get_client()
        except Exception:  # noqa: BLE001 — no Redis → fetch fresh each turn
            _redis = None

        memory_block = await get_session_memory(
            redis=_redis, thread_id=thread_id, build=_build_memory_block
        )
    except ImportError:
        memory_block = await _build_memory_block()

    # MAF Agent stores instructions in default_options["instructions"] (a dict).
    opts = base_agent.default_options
    base_instructions = (
        opts.get("instructions") if isinstance(opts, dict) else None
    ) or ""

    if not memory_block:
        return base_instructions  # type: ignore[return-value]

    from acb_llm.prompt_cache import CACHE_BREAK  # noqa: PLC0415
    return f"{base_instructions}\n{CACHE_BREAK}\n{memory_block}"


def build_agents() -> list[Agent]:
    """Dynamic Agent Loader entry point.  Returns list of MAF agents for this repo."""
    # Background / event-driven path: no Redis history (stateless runs).
    return [build_orchestrator_agent(with_history=False)]


__all__ = [
    "build_agents",
    "build_orchestrator_agent",
    "enrich_instructions_with_memory",
    "retrieve_entity_context",
    "retrieve_sales_context",
    "search_timeline",
    "spawn_copilot_agent",
    "delegate_to_agent",
]
