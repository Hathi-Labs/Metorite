# External Library & Architecture Reference

> Consolidated reference for the runtime libraries and memory design Metorite depends on. Consult when implementing orchestration, Copilot agent wrappers, or memory wiring. For *why* decisions were made see [`system_architecture.md`](system_architecture.md) (ADRs); for *what/when* see [`project_plan.md`](project_plan.md).
> Last verified 2026-06-04; versions updated 2026-06-10. (Rewritten 2026-06-20 from the former `ref_maf.md` / `ref_copilot_sdk.md` / `ref_memory_architecture.md`, whose source bytes were corrupted.)
> ⚠️ **Stale-warning 2026-08-09:** last verified 2026-06-04 — pins may lag `uv.lock` (e.g. agent-framework-core 1.8.1 is live per multi_agent_orchestration.md). `uv.lock` is the source of truth for versions; re-verify any claim here before relying on it.

**Contents:** [1. MAF](#1-microsoft-agent-framework-maf) · [2. GitHub Copilot SDK](#2-github-copilot-sdk) · [3. Memory architecture](#3-memory-architecture)

---

## 1. Microsoft Agent Framework (MAF)

The **sole agent execution runtime** for Metorite — background event runs and interactive chat both go through MAF. (See ADR-026 in `system_architecture.md`.)

### Versions in use
| Package | Version |
|---|---|
| `agent-framework-core` | 1.8.0 |
| `agent-framework-github-copilot` | 1.0.0rc1 |
| `github-copilot-sdk` | 1.0.0 |

### Sub-packages
`agent-framework` (meta — installs all) · `-core` (engine, workflows, orchestrations) · `-openai` (`OpenAIChatClient` / `OpenAIChatCompletionClient`) · `-foundry` (Azure AI Foundry) · `-ag-ui` (`add_agent_framework_fastapi_endpoint`) · `-github-copilot` (`GitHubCopilotAgent`) · `-redis` (`RedisHistoryProvider`, `RedisContextProvider`) · `-mem0` (`Mem0ContextProvider`) · `-durabletask` (durable workflow hosting — Phase 2) · `-azure-cosmos` (checkpoint storage — Phase 2) · `-a2a` (Agent-to-Agent proxy) · `-anthropic` (direct Claude client) · `-declarative` (YAML agents) · `-devui` (dev UI).

```bash
pip install agent-framework-core              # core + OpenAI + workflows
pip install agent-framework-ag-ui             # AG-UI streaming endpoint
pip install agent-framework-github-copilot --pre   # GitHubCopilotAgent
pip install agent-framework-redis --pre       # Redis history/context providers
pip install agent-framework-mem0 --pre        # Mem0 context provider
```

### Core Agent API
```python
from agent_framework import Agent
from agent_framework.openai import OpenAIChatClient   # points at LiteLLM in our setup

agent = Agent(
    client=OpenAIChatClient(),
    name="my-agent",
    instructions="You are a helpful assistant.",
    tools=[my_tool_fn],                  # @tool-decorated functions
    context_providers=[my_provider],     # ContextProvider subclasses
    middleware=[my_middleware],
)

result = await agent.run("Hello!")                  # stateless; result.text / result.messages
session = agent.create_session()                    # multi-turn within session
await agent.run("My name is Alice.", session=session)
async for update in await agent.run("…", stream=True):   # streaming
    print(update.text, end="", flush=True)
```

### GitHubCopilotAgent (MAF + Copilot SDK bridge)
A first-class MAF `BaseAgent` — participates in `HandoffBuilder`/`ConcurrentBuilder` like any agent. The Copilot SDK runs the internal reasoning/tool loop; MAF wraps it. In Metorite we subclass it as `MetoriteCopilotAgent` (`apps/orchestrator/orchestrator/copilot_agent.py`) for BYOK forwarding + rich event streaming.

```python
from agent_framework_github_copilot import GitHubCopilotAgent

agent = GitHubCopilotAgent(
    instructions="…",
    tools=[my_tool_fn],                  # MAF FunctionTools — auto-translated to CopilotTool
    context_providers=[my_provider],
    default_options={
        "model": "claude-sonnet-4-5",
        "mcp_servers": {                 # Integration Registry credential injection
            "clickup": {"command": "uvx", "args": ["mcp-clickup"], "env": {...}},
        },
        "provider": {                    # BYOK through LiteLLM
            "type": "openai",
            "base_url": "http://127.0.0.1:8080/v1",
            "api_key": LITELLM_KEY,
        },
        "on_permission_request": PermissionHandler.approve_all,
    },
)
```
- `mcp_servers=` is how Integration Registry credentials reach the Copilot CLI.
- `provider=` routes through LiteLLM BYOK instead of the GitHub Copilot cloud backend.

### Multi-agent orchestration
```python
from agent_framework import HandoffBuilder, ConcurrentBuilder, GroupChatBuilder

# Handoff: triage → specialist
HandoffBuilder().add_agent(triage, can_handoff_to=[crm, tasks]).add_agent(crm).add_agent(tasks).build()
# Concurrent: fan-out / fan-in
ConcurrentBuilder().add_agents([crm, tasks, invoice]).build()
# Group chat: agents converse
GroupChatBuilder().add_agents([writer, reviewer]).build()
```
`WorkflowBuilder` is also wired (infra-ready) for explicit sequential/fan-out pipelines via `add_chain()` / `add_fan_out_edges()` / `add_fan_in_edges()`.

### AG-UI endpoint (replaces the old `copilot_chat.py`)
```python
from agent_framework.ag_ui import add_agent_framework_fastapi_endpoint
add_agent_framework_fastapi_endpoint(app, agent, "/copilot/chat", dependencies=[Depends(verify_api_key)])
```
AG-UI carries streaming chat, backend tool rendering, HITL confirmation, generative UI (`STATE_SNAPSHOT` / `STATE_DELTA` / `CUSTOM`), shared state, predictive updates, and interrupt/resume.

### Observability
MAF has built-in OpenTelemetry — call `configure_otel_providers(OTEL_EXPORTER_OTLP_ENDPOINT=…)` once at startup; no per-agent SDK imports. (Langfuse is removed from the Phase-0 stack; wire any OTLP backend later if tracing is needed.)

---

## 2. GitHub Copilot SDK

`github-copilot-sdk` 1.0.0 (Python). Requires Python 3.11+ and the GitHub Copilot CLI on `PATH` (and `pwsh` 7.x on Linux for the shell tool). Used **only** inside `GitHubCopilotAgent`/`MetoriteCopilotAgent` (MAF wrappers) and the mutation sandbox (`acb-mutation-runner`) — never called directly by application code (constraint C-08).

- **What it is:** a CLI-driven agent runtime (the Copilot CLI is the orchestrator) with built-in shell, file read/write, and MCP-server tools; streams reasoning, tool name/args/result, and partial output.
- **MAF bridge:** the `agent-framework-github-copilot` 1.0.0rc1 release relaxed the SDK pin to `<2,>=1.0.0`, allowing full re-integration; MAF FunctionTools auto-translate to CopilotTools.
- **BYOK:** pass `default_options["provider"]` (`type/base_url/api_key`) to route through LiteLLM instead of the Copilot cloud backend.
- **Permissions:** `on_permission_request` gates shell/write ops — `approve_all` for dev/sandbox; a custom handler in production.
- **Mutation container:** receives the prompt + LiteLLM BYOK creds via env vars; the agent repo is mounted at `/workspace/repo`; container self-destructs after the run.

---

## 3. Memory Architecture

**Status: DECIDED — Mem0 + Graphiti ACTIVE (M2.8, 2026-06-12).** Four layers, each a different scope; not redundant.

| Layer | Storage | Scope | Status |
|---|---|---|---|
| In-process `AgentSession` | Python dict (`session.state`) | One run/conversation; lost on restart | Built-in |
| Conversation history | Redis (`RedisHistoryProvider`) | Multi-turn per thread, survives restart | Interactive chat path only |
| Business entity graph | Postgres + pgvector | Durable company facts (people, tasks, deals…) | Core |
| Episodic memory | Mem0 (`Mem0ContextProvider`, pgvector backend) | Cross-run learned facts per agent | Active |
| Bi-temporal KG | Graphiti + Neo4j (`--profile memory`) | Time-aware entity timeline | Active |

- **In-process session** — `ContextProvider.before_run()` / `after_run()` hooks; only for within a single webhook-triggered run.
- **Redis history** — wire only on interactive/operator-path agents; background event agents use in-memory `AgentSession` only.
- **Entity graph** — the authoritative business memory; agents cite graph nodes.
- **Mem0 + Graphiti** — post-run extraction fires after every chat (`enrich_instructions_with_memory()` + background add); injected into both orchestrator and Copilot SDK agents (`payload.memory_context` → system message). All embedding/LLM calls route through LiteLLM — zero hardcoded keys. CRUD via `/memory/*`; Memory Manager UI in the Control Plane.

**When to use which:** scratchpad for one run → in-process session · "what did we say earlier in this chat" → Redis history · "what is true about the company" → entity graph · "what has this agent learned across runs" → Mem0 · "how did this entity change over time" → Graphiti.
