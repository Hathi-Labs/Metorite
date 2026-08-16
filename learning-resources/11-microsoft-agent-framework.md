# 11 · Microsoft Agent Framework (MAF)

MAF is the runtime that ties everything together — the concrete implementation of the "run loop" from
chapter 07 and the multi-agent patterns from chapter 06. Metorite's founding constraint is that
**MAF is the sole agent execution runtime**: every agent, whether backed by a plain model or by the GitHub
Copilot SDK, runs through MAF. This chapter is a practical tour of what MAF gives you and how the platform
uses it.

---

## 1. What MAF is

MAF (`agent-framework-*` Python packages) is Microsoft's open agent framework. It provides:

- an **`Agent`** abstraction (instructions + tools + model client + context providers),
- **tool calling** (plain Python functions become tools; the loop is handled for you),
- **multi-agent orchestration builders** (`HandoffBuilder`, `ConcurrentBuilder`, `GroupChatBuilder`,
  `WorkflowBuilder`),
- **context providers** for memory (conversation history, episodic memory),
- a **streaming protocol bridge** to the browser (AG-UI, chapter 13),
- and **native OpenTelemetry** for observability.

It's a meta-package with focused sub-packages, and Metorite pins specific ones:

| Package | Role |
|---|---|
| `agent-framework-core` | The engine: `Agent`, workflows, orchestration builders. |
| `agent-framework-openai` | `OpenAIChatCompletionClient` — points at the platform's `/v1` gateway (LiteLLM). |
| `agent-framework-github-copilot` | `GitHubCopilotAgent` — a MAF agent backed by the Copilot SDK (chapter 12). |
| `agent-framework-ag-ui` | The FastAPI streaming endpoint (`add_agent_framework_fastapi_endpoint`). |
| `agent-framework-redis` | `RedisHistoryProvider` — conversation memory. |
| `agent-framework-mem0` | `Mem0ContextProvider` — episodic memory. |

---

## 2. The `Agent` — the unit of execution

The core API is small (this is the same snippet as chapter 07, now in context):

```python
from agent_framework import Agent
from agent_framework.openai import OpenAIChatCompletionClient

agent = Agent(
    client=OpenAIChatCompletionClient(base_url=".../v1", api_key="sk-local", model="tier-balanced"),
    name="task-manager",
    instructions="You manage ClickUp tasks.",
    tools=[create_task, list_tasks],       # plain async functions — MAF derives schemas
    context_providers=[history, memory],   # optional
)

result = await agent.run("Create a task to follow up with Acme")   # stateless
async for update in agent.run("…", stream=True):                    # streaming
    for content in update.contents: ...                             # text / function_call / result / reasoning
```

Note the model client points at the platform's *own* gateway (`/v1`), not directly at a provider. That's
how tiered routing, key management, and BYOK (chapter 09) apply uniformly — MAF never talks to a provider
directly; it goes through LiteLLM.

**Streaming shape.** In stream mode, MAF yields `AgentResponseUpdate` objects whose `.contents` carry
typed pieces: `text` (answer tokens), `text_reasoning` (thinking), `function_call` (a tool invocation),
`function_result` (a tool's return). Metorite maps each of these onto an AG-UI SSE event (chapter 13)
so the frontend can render text, thinking, and tool activity distinctly.

---

## 3. Tools in MAF

MAF accepts **plain async functions** as tools and derives the JSON schema from the type hints + docstring
(chapter 08). Metorite injects its shared toolbox by appending to the agent's tool list at load time.
For Copilot-SDK-backed agents the same functions are normalized to the SDK's `CopilotTool` format — MAF's
`FunctionTool`s "auto-translate," which is precisely why the platform can run one tool set across both
agent kinds.

---

## 4. Multi-agent orchestration builders

Beyond a single agent, MAF ships composable builders (chapter 06 §5):

```python
from agent_framework import HandoffBuilder, ConcurrentBuilder, GroupChatBuilder

HandoffBuilder(source=triage, targets=[sales, ops]).build()   # triage routes to a specialist
ConcurrentBuilder(agents=[research, crm]).build()             # fan-out / fan-in
GroupChatBuilder().add_agents([writer, reviewer]).build()     # agents converse
```

`WorkflowBuilder` exists for explicit sequential/fan-out/fan-in pipelines when you want a *fixed* graph.
In practice Metorite leans on **delegation-as-tool** (`call_agent`) for dynamic routing and reserves
the builders for known topologies — but they're all first-class MAF constructs.

---

## 5. The AG-UI endpoint — one runtime for chat *and* background

A standout MAF capability: the same agents that run background events also serve the interactive chat, via
one line:

```python
from agent_framework.ag_ui import add_agent_framework_fastapi_endpoint
add_agent_framework_fastapi_endpoint(app, agent, "/copilot/chat", dependencies=[Depends(verify_api_key)])
```

This wraps a MAF agent as a FastAPI endpoint speaking the **AG-UI streaming protocol** — tool calling,
streaming text/reasoning, HITL, and generative UI all included. The Control Plane connects to it and gets
the full agent experience. The architectural win (ADR-026): **there is no separate "chat runtime" vs.
"background runtime."** Interactive and ambient runs are the *same* MAF agents, so behaviour can't drift
between them. (Conversation history for chat is persisted via `RedisHistoryProvider`, keyed by `thread_id`.)

---

## 6. Context providers — memory as a plug-in

Memory attaches to an agent as a **context provider** with `before_run` / `after_run` hooks:

- `RedisHistoryProvider` — multi-turn conversation history per thread.
- `Mem0ContextProvider` — cross-run episodic memory (pgvector-backed).

At run start, providers enrich the prompt (inject relevant memories); after a run, they extract and persist
new facts (fire-and-forget). This is the mechanism behind chapter 07's "memory captured in chat benefits
background runs." It's clean because memory is *orthogonal* to agent logic — an agent author doesn't write
memory code; they attach a provider.

---

## 7. Observability for free

MAF emits **OpenTelemetry** spans automatically. You call `configure_otel_providers(...)` once at
orchestrator startup and every agent run, tool call, and model call is traced — no per-agent SDK imports,
no instrumentation code in agent repos. Point it at any OTLP backend when you want a tracing UI. (Combined
with the append-only audit log, this is the platform's observability story.)

---

## 8. Why standardize on one runtime

Metorite's global constraint — *MAF is the only agent runtime; the Copilot SDK is a mutation-sandbox /
model-backend only* — is a deliberate anti-fragmentation choice. Earlier iterations had a separate Copilot
SDK chat path *and* a MAF path, which meant two streaming implementations, two tool systems, two sets of
bugs. Collapsing to one runtime (once `agent-framework-github-copilot` could wrap the Copilot SDK) means:

- one tool-injection path, one streaming/AG-UI path, one memory story;
- chat and background runs literally share code;
- the Copilot SDK becomes "just another model backend" (`GitHubCopilotAgent`) rather than a parallel
  universe.

**The lesson for your own build:** pick *one* agent runtime and make everything else conform to it, even
if that means wrapping a second tool in the first's abstractions. Two runtimes is more than twice the
work.

Next: **[12 · GitHub Copilot SDK & Self-Mutation](./12-copilot-sdk-self-mutation.md)**.
