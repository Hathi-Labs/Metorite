# 00 · Glossary

Every acronym, product, and term used in this course. Skim once; refer back as needed.

## Core concepts

| Term | Meaning |
|---|---|
| **Agent** | An LLM given an identity (instructions), a set of **tools** it can call, and optionally memory. It runs in a loop: think → call a tool → observe the result → repeat → answer. |
| **Orchestration** | Coordinating one or more agents to complete a task: routing an event to the right agent, running multi-agent workflows (handoff, fan-out), persisting state, and handling failure. |
| **Tool calling** | The mechanism by which an LLM triggers real code. The model emits a structured "call this function with these arguments"; the runtime executes it and feeds the result back. Also called *function calling*. |
| **HITL** | *Human-in-the-loop.* A workflow pauses and waits for a person to approve an action before it happens (e.g. before writing to a CRM). |
| **Event-driven** | Work is triggered by *events* (a webhook, a cron tick, an inbound email) rather than by a person clicking "run". |
| **Ambient / Pull / Push** | The three interaction modes. *Pull* = user asks a question. *Push* = system proactively notifies the user. *Ambient* = system reacts to background events with no user in the loop. |

## The runtimes & libraries

| Term | Meaning |
|---|---|
| **MAF — Microsoft Agent Framework** | The Python agent-execution runtime Metorite uses (`agent-framework-*` packages). Provides `Agent`, tool wiring, multi-agent builders (`HandoffBuilder`, `ConcurrentBuilder`), context providers, and native OpenTelemetry. The *sole* agent runtime in this system. |
| **LiteLLM** | A Python library that gives **one OpenAI-compatible API for ~100 model providers** (Anthropic, OpenAI, DeepSeek, Gemini, …). Metorite calls `litellm.acompletion()` directly — no separate proxy container. |
| **MCP — Model Context Protocol** | An open protocol (from Anthropic) for exposing tools/data to an agent as a **standard server**. An MCP server for ClickUp exposes ClickUp's API as agent-callable tools. Lets you plug in an integration without writing bespoke tool code. |
| **CopilotKit** | A **React UI library** (`@copilotkit/react-*`) that renders the chat window and injects context. It is *not* an LLM and *not* an orchestrator. It authored the AG-UI protocol. |
| **AG-UI** | The streaming **protocol** between an agent backend and a browser UI. Carries text deltas, reasoning, tool-call lifecycle, generative UI, and HITL. Metorite's MAF backend speaks AG-UI; CopilotKit speaks it in the browser. |
| **GitHub Copilot SDK** | A Python library (`github-copilot-sdk`) that drives the Copilot **CLI** as an agent with built-in shell/file/MCP tools. Used two ways here: wrapped by MAF as `GitHubCopilotAgent`, and standalone inside the **self-mutation** sandbox. |
| **Self-mutation** | When an agent's code fails, the system spawns a Copilot SDK sandbox that reads the error, writes a fix to the agent's *own* repo, runs tests, and opens a PR. Capped at 1 attempt per failure; a human must merge. |

## Infrastructure & ops

| Term | Meaning |
|---|---|
| **Gateway** | The FastAPI service (`apps/gateway`) that is the front door: auth, event routing, HTTP/WS endpoints, the AG-UI chat endpoint, HITL approvals. |
| **Orchestrator** | The service (`apps/orchestrator`) that loads and runs agents on MAF. |
| **Control Plane / Workbench** | The Next.js frontend (`workbench/control_plane`) — chat, observability, HITL approvals. Not an editor. |
| **Action Broker** | The approval-queue + audit + write-executor component. Every external write goes through it. |
| **Entity Graph** | The durable business memory: people, tasks, deals, projects, stored in Postgres + pgvector. A read-mostly mirror of the source systems. |
| **Integration Registry** | Where all third-party credentials live (encrypted, in Postgres). Agents *declare* which integrations they need by name; the loader injects only those. Credentials never live in agent repos. |
| **BYOK** | *Bring Your Own Key.* Routing model calls through the user's own provider API keys rather than a platform key. |
| **VPS** | *Virtual Private Server.* A single cloud Linux box (here: a Hostinger KVM). Metorite runs its whole production stack on one. |
| **Caddy** | A reverse proxy that terminates HTTPS (automatic Let's Encrypt certs) and routes to the gateway and workbench. |
| **systemd** | The Linux service manager. Runs the gateway and workbench as managed services (`acb-gateway`, `acb-workbench`). |
| **pgvector** | A Postgres extension for vector similarity search — used for embeddings/semantic memory. |
| **uv** | A fast Python package/workspace manager (from Astral). Manages the monorepo's Python packages. |
| **OTel — OpenTelemetry** | Vendor-neutral tracing/metrics standard. MAF emits spans automatically. |

## Metorite-specific naming

| Term | Meaning |
|---|---|
| **`acb_*` packages** | Shared internal Python libraries (`acb_llm`, `acb_auth`, `acb_graph`, …). "acb" = the project's internal prefix. |
| **Tier-fast / balanced / powerful** | The three routing tiers. Aliases that resolve to concrete models (e.g. a cheap DeepSeek model → a mid model → a reasoning model). |
| **`agent-<name>` / `skill-<name>` repos** | Each specialist agent and each reusable skill is its *own* GitHub repo, cloned at runtime. |
| **`MetoriteCopilotAgent`** | The project's subclass of MAF's `GitHubCopilotAgent`, adding BYOK forwarding and rich event streaming. |
