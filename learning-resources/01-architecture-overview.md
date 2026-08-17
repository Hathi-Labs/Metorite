# 01 · System Architecture Overview

This is the whole system on one page. Read it before any other chapter — everything else is a zoom-in
on a box or an arrow drawn here.

---

## 1. What problem is this shape solving?

Metorite is "an operating system for a company": a fleet of specialist AI agents (sales, triage,
delivery, reconciler, task-management) act on company data living in ClickUp, Zoho CRM, Odoo, Gmail,
and WhatsApp. The design is driven by a handful of hard constraints, and **those constraints explain
every architectural choice** — so if you're building your own, start by deciding where you land on each:

| Driver | Metorite's stance | Why it shapes the architecture |
|---|---|---|
| **Trust of external systems** | Source of truth is ClickUp/Zoho/Odoo; the platform is a *read-mostly mirror* and **every write is human-approved**. | Forces an Action Broker + approval-queue between agents and the outside world. |
| **Independent evolution of agents** | Each agent and skill is its **own Git repo**, cloned at runtime. Core carries no agent logic. | Forces a *dynamic loader* instead of a monolith; agents update without redeploying Core. |
| **Cost** | **Tiered LLM routing** — cheap model classifies, expensive model reasons. | Forces an abstraction over model providers (LiteLLM) and per-call tier selection. |
| **Recoverability** | Agents **fix their own bugs** via PRs (self-mutation), capped and human-gated. | Forces a sandboxed coding-agent runtime + a PR/rollback loop. |
| **One small team, one cheap box** | Whole stack runs on a **single VPS** with Docker + systemd + Caddy. | Forces a modest, boring, single-VM deployment story rather than Kubernetes. |

If your product doesn't share a driver, you can drop the box it justifies. A pure Q&A assistant needs
none of the Action Broker, self-mutation, or dynamic loading — see [chapter 14](./14-build-your-own.md)
for the minimal subset.

---

## 2. The components (container view)

```
                          ┌──────────────────────────────────────────┐
     Browser ── HTTPS ──▶ │  Caddy (reverse proxy, auto-TLS)          │
                          └───────────────┬──────────────┬───────────┘
                                          │              │
                        metorite.…   │              │  api.metorite.…
                                          ▼              ▼
                          ┌───────────────────────┐   ┌────────────────────────────┐
                          │  Control Plane         │   │  Gateway (FastAPI)         │
                          │  Next.js (workbench)   │   │  • auth (SSO + Bearer)     │
                          │  • Chat (CopilotKit)   │◀─▶│  • event routing           │
                          │  • Observability       │   │  • AG-UI chat endpoint     │
                          │  • HITL approvals      │   │  • HITL / approvals API    │
                          └───────────────────────┘   └───────┬───────────┬────────┘
                                                               │           │
                                       AG-UI / SSE stream ◀────┘           │
                                                                           ▼
                                                          ┌────────────────────────────┐
                                                          │  Orchestrator              │
                                                          │  • Dynamic Agent Loader    │
                                                          │  • MAF run / run_stream    │
                                                          │  • tool injection          │
                                                          │  • Self-Mutation trigger   │
                                                          └───┬───────┬───────────┬────┘
                                                              │       │           │
                          ┌───────────────────────────────────┘       │           │
                          ▼                                            ▼           ▼
              ┌───────────────────────┐              ┌────────────────────┐  ┌──────────────────┐
              │  Git (agent-*/skill-* │              │  LiteLLM (acb_llm) │  │ Copilot SDK      │
              │  repos, cloned at run)│              │  tiered routing    │  │ mutation sandbox │
              └───────────────────────┘              │  → model providers │  │ (Docker, ephem.) │
                                                     └────────────────────┘  └──────────────────┘
   Shared state:
   ┌─────────────────────────────────────────────────────────────────────────────────────┐
   │  Postgres + pgvector  (entity graph · memory · audit · integrations · approval queue) │
   │  Redis                (event bus + streaming reconnection + chat history)             │
   └─────────────────────────────────────────────────────────────────────────────────────┘
```

**One-line purpose of each service** (all live in this monorepo under `apps/`):

- **Gateway** (`apps/gateway`, FastAPI) — the front door. Authenticates every request, routes events to
  agents, serves the streaming chat endpoint, exposes the HITL approval and settings APIs.
- **Orchestrator** (`apps/orchestrator`) — loads agent code from Git, runs it on MAF, injects tools,
  streams results back, and triggers self-mutation on failure.
- **Control Plane / Workbench** (`workbench/control_plane`, Next.js) — the human UI: chat,
  observability (audit + spend), and the approval queue. *Not* an editor.
- **Ingestion / Reconciler / Action Broker / Email ingestion** (`apps/*`) — background workers that
  pull events in from source systems, run nightly drift checks, and execute approved writes back out.
- **Shared packages** (`packages/acb_*`) — reusable libraries: `acb_llm` (LLM routing), `acb_auth`
  (auth), `acb_graph` (Postgres/pgvector), `acb_skills` (the dynamic loader + tools + Integration
  Registry), `acb_common` (settings/logging).

---

## 3. The core loop (the thing to memorise)

Every feature is a variation of this arc:

```
 EVENT                 ROUTE                 LOAD                     RUN                       GOVERN
──────────         ────────────        ─────────────────      ──────────────────────      ───────────────────
webhook / cron  →  gateway picks   →   loader git-pulls   →   MAF runs the agent in a  →   risky write? → pause
/ chat message     the target          the agent's repo,      think→tool→observe loop,     in Action Broker,
                   agent               injects its             streaming every step to      wait for human OK.
                                       credentials + tools     the UI over AG-UI.           everything → audit log.
                                                                     │
                                                              agent code threw?
                                                                     ▼
                                                        Self-Mutation: Copilot SDK sandbox
                                                        writes a fix to the agent's repo,
                                                        runs tests, opens a PR (max 1 try).
```

Chapters map onto this arc:
- **Route** → [06 Orchestration](./06-orchestration.md)
- **Load** → [06 Orchestration](./06-orchestration.md) (Dynamic Agent Loader) + [05 Auth/OAuth](./05-auth-and-oauth.md) (credential injection)
- **Run** → [07 Agents](./07-agents.md), [08 Tool Calling](./08-tool-calling.md), [11 MAF](./11-microsoft-agent-framework.md), [09 LiteLLM](./09-litellm-routing.md), [10 MCP](./10-mcp-and-integrations.md)
- **Stream to UI** → [13 AG-UI](./13-ag-ui-and-generative-ui.md), [02 Web App](./02-web-application.md)
- **Govern** → [06 Orchestration](./06-orchestration.md) (HITL + Action Broker), [12 Self-Mutation](./12-copilot-sdk-self-mutation.md)

---

## 4. Two runtimes people confuse — get these straight now

Newcomers conflate four "Copilot/agent" things. They are unrelated:

| Name | What it actually is | Where it runs |
|---|---|---|
| **MAF** (Microsoft Agent Framework) | The **agent execution runtime** — the loop, tools, multi-agent orchestration. | Backend (Python), the orchestrator. |
| **GitHub Copilot SDK** | A **CLI-driven coding agent** with shell/file tools. Used two ways: wrapped by MAF as a model backend, and standalone in the **self-mutation sandbox**. | Backend (Python) + ephemeral Docker. |
| **CopilotKit** | A **React UI library** for rendering the chat. Not an LLM, not an orchestrator. | Frontend (browser). |
| **AG-UI** | The **streaming protocol** between the agent backend and the browser. | The wire between them. |

The rule the codebase enforces: **MAF is the sole agent runtime.** The Copilot SDK only appears (a) as
a model backend wrapped inside MAF's `GitHubCopilotAgent`, and (b) inside the mutation sandbox. See
[11](./11-microsoft-agent-framework.md) and [12](./12-copilot-sdk-self-mutation.md).

---

## 5. Data & state at a glance

- **Postgres (+ pgvector)** is the one durable store. It holds five logically separate things: the
  **entity graph** (business facts), **memory** (episodic/vector), the **audit log** (append-only),
  the **Integration Registry** (encrypted third-party credentials), and the **approval queue** (HITL).
  One database to back up — a deliberate small-team choice (see ADR-002).
- **Redis** is the event bus (Redis Streams decouple ingestion from orchestration) and also backs
  streaming reconnection and multi-turn chat history.
- **Git** is the source of truth for all *agent-editable* artefacts. Agent code, skills, prompts, and
  routing config live in Git and change only via PR.

---

## 6. Design principles you can steal

1. **Source systems are authoritative; you are a mirror.** Never let an agent be the only place a fact
   lives. Gate writes.
2. **Make agents data, not code-in-your-binary.** Loading agents from Git at runtime means you ship the
   platform once and evolve behaviour continuously.
3. **Abstract the model provider on day one.** Tiered routing + one client (`acb_llm`/LiteLLM) means you
   swap models, control cost, and add fallback without touching agent code.
4. **Stream everything; audit everything.** The same event stream that renders the live UI is the audit
   trail. Observability is not an afterthought bolted on later.
5. **Human-in-the-loop is a first-class state, not an exception.** The approval queue is a table in
   Postgres; a paused workflow survives restarts.
6. **Boring infra scales further than you think.** One VPS, Docker for stateful bits, systemd for apps,
   Caddy for TLS. You do not need Kubernetes to ship.

Next: **[02 · How the Web App Is Built](./02-web-application.md)**.
