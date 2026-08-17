# 02 · How the Web App Is Built (Frontend + Backend)

An agent platform is, at the surface, a fairly ordinary web app: a browser talking to an HTTP API. The
interesting parts (agents, tools, streaming) sit *behind* that boundary. This chapter covers the
"ordinary web app" scaffolding — the monorepo, the backend, the frontend, and the wire between them —
so the later chapters can focus on the agent-specific machinery.

---

## 1. The monorepo

Everything lives in one Git repository with two language ecosystems side by side:

```
Metorite/
├── apps/                      # Deployable Python services (one process each)
│   ├── gateway/               #   FastAPI — the HTTP/WS front door
│   ├── orchestrator/          #   agent loader + MAF runner
│   ├── ingestion/             #   ClickUp / Zoho / Gmail sync workers
│   ├── email_ingestion/       #   mailbox sync (IMAP / Graph / Gmail)
│   ├── reconciler/            #   nightly drift detection
│   └── action_broker/         #   approval-gated write executor
├── packages/                  # Shared Python libs (a uv workspace)
│   ├── acb_llm/               #   LLM routing + key store + context fitting
│   ├── acb_auth/              #   auth deps + roles
│   ├── acb_graph/             #   Postgres + pgvector access
│   ├── acb_skills/            #   dynamic agent loader + tools + integrations
│   └── acb_common/            #   settings, logging
├── workbench/control_plane/   # Next.js frontend (TypeScript)
├── infra/                     # docker-compose, Postgres SQL, LiteLLM config
├── deploy/hostinger/          # systemd units + Caddy config
└── .github/workflows/         # CI/CD
```

**Why a monorepo when agents live in separate repos?** The *platform* (gateway, orchestrator, UI,
shared libs) is one coherent thing that ships together — a monorepo keeps it atomic and lets a single PR
touch backend + frontend + infra. The *agents* are the thing that must evolve independently, so *they*
get their own repos and are cloned at runtime (chapter 06). Don't confuse the two.

**Python side — uv workspace.** The `packages/acb_*` libraries are workspace members. `uv sync` installs
the whole graph from one lockfile (`uv.lock`). An app imports a shared lib as a normal package
(`from acb_llm import complete`). Adding a dependency to one member: `uv add --package acb_llm httpx`.

**Type/quality gates.** `ruff` for lint+format, `mypy` for types, `pytest` for tests — all run in CI
(chapter 04). Type hints are required on public functions; everything in a request path is `async`.

**General principle:** *pick one dependency manager per language and one lockfile per ecosystem.* The
whole "does it build" question then has a single answer, which is what makes push-to-deploy safe.

---

## 2. The backend — FastAPI gateway

The gateway (`apps/gateway`) is a standard FastAPI app: a set of routers mounted on one ASGI app,
fronted by `uvicorn`. Its route modules (`gateway/routes/`) map one-to-one onto product surfaces:

| Route module | Responsibility |
|---|---|
| `agent.py` | Run agents: `POST /agent/run`, `GET /agent/run/stream` (SSE), the registry, HITL input responses, and the self-mutation approval queue. |
| `v1_compat.py` | The OpenAI-compatible `POST /v1/chat/completions` — lets any OpenAI SDK talk to the platform (chapter 09). |
| `oauth.py` | Generic third-party OAuth (Zoho, ClickUp, Google) — authorize/callback/refresh (chapter 05). |
| `email/…` | The whole email assistant: accounts, messages, AI chat, automation rules, settings, and email OAuth. |
| `integrations.py` | Integration Registry CRUD, MCP server registry, plugin registry, connectivity tests. |
| `settings.py` | Runtime config: LLM tier→model mapping, provider-key testing/injection. |
| `chat.py` | Chat session + message persistence (for stream-reconnection recovery). |

**Cross-cutting conventions** (from `AGENTS.md`, enforced across the app):
- Every endpoint takes an auth dependency (`Depends(get_current_user)`), except `/health`.
- `async/await` throughout — no blocking calls in a request path.
- All persistent state is Postgres or Redis; the process itself is stateless and restartable.

**The shape of a streaming endpoint** — the workhorse pattern, since agents stream:

```python
@app.get("/agent/run/stream")
async def run_stream(agent: str, user: UserContext = Depends(get_current_user)):
    return StreamingResponse(
        run_agent_stream(agent, payload, ...),   # async generator yielding SSE frames
        media_type="text/event-stream",
    )
```

The generator yields `data: {json}\n\n` frames (the AG-UI protocol, chapter 13). Everything else — chat,
email AI, tool calls — is this same SSE shape.

---

## 3. The frontend — Next.js Control Plane

`workbench/control_plane` is a Next.js 16 / React 19 app (App Router, TypeScript, Tailwind v4). Its
pages are the human surfaces of the platform:

| Route | Purpose |
|---|---|
| `/chat` | The main multi-agent chat: session list, live streaming, tool activity, artifacts, memory panel. |
| `/email` | A full email client with AI automation (rules, drafts, digests, unsubscribe). |
| `/agents` | Browse/inspect the agent registry. |
| `/inbox` | The self-mutation HITL queue — approve/reject agent-authored commits. |
| `/observability`, `/dashboard`, `/memory`, `/integrations`, `/settings` | Audit/spend, memory management, integration setup, config. |
| `/signin` | NextAuth sign-in (Microsoft Entra ID). |

**Notable choice: no CopilotKit dependency, but CopilotKit's *patterns*.** Rather than pull in
`@copilotkit/react-*`, this frontend implements its own small hooks that mirror the CopilotKit/AG-UI
model. This is a legitimate design fork — you get full control of the streaming/reconnection logic at
the cost of writing it yourself. The key hooks:

- **`useAgentChat`** — opens the SSE stream, parses AG-UI events (`delta`, `reasoning`, `tool_start`,
  `tool_end`, `done`, `error`), and persists messages to Postgres so a dropped connection can be
  recovered. Backed by a *module-level* store so state survives component unmount.
- **`useAgentEvents`** — a subscriber bus that decouples "parse the stream" from "render the UI",
  filtered by `threadId` for multi-session isolation.
- **`useAgentState`** — applies `STATE_SNAPSHOT` / `STATE_DELTA` events to a shared agent-state object
  (generative UI, chapter 13).
- **`useFrontendTool`** — a registry of *browser-side* tools the agent can call (e.g. `setTheme`) whose
  descriptions get injected into the agent's system prompt.

**Design-system discipline.** `DESIGN_SYSTEM.md` mandates semantic Tailwind tokens (`bg-primary`,
`text-foreground`, `border-border`) — never raw hex — and shared components (`Tabs`, `FilterPills`,
`ConfirmationCard`, `ElicitationCard`, `ArtifactCard`, `MarkdownMessage`). This is what keeps an
AI-assisted codebase visually coherent as many hands (and agents) touch it.

---

## 4. The wire between them — Next.js API routes as a proxy

The browser never calls the FastAPI gateway directly. Every backend call goes through a **Next.js API
route** (`src/app/api/**/route.ts`) that proxies to the gateway. This indirection buys three things:

1. **Auth injection.** The browser has a NextAuth session (a signed cookie). The API route reads it
   server-side and attaches the machine credential + user identity headers the gateway expects:

   ```typescript
   const headers = {
     "Content-Type": "application/json",
     Authorization: `Bearer ${INTERNAL_TOKEN}`,          // machine trust
     "X-User-Email": session.user.email,                 // who
     "X-User-Role": isExec ? "executive" : "employee",   // what they may do
   };
   ```

   The gateway's `INTERNAL_TOKEN` is **never exposed to the browser** — it lives only in the Next.js
   server process. This is the whole reason for the proxy layer (chapter 05).

2. **Shape mapping.** The gateway speaks `snake_case`; the frontend speaks `camelCase`. The proxy layer
   (e.g. `email/lib/api.ts`) maps between them so neither side has to compromise its idioms.

3. **Streaming pass-through.** For SSE endpoints, the API route pipes the gateway's `text/event-stream`
   straight through to the browser unchanged.

So the full request path is:

```
Browser fetch → Next.js API route (adds auth, maps shape) → FastAPI gateway route
             → orchestrator / acb_llm / Postgres → SSE frames stream back the same way
```

---

## 5. What to copy for your own build

- **Split "platform" from "content."** Platform = monorepo, ships together. Content (agents, prompts) =
  loaded at runtime, evolves independently.
- **Put a server-side proxy between the browser and your agent API.** It is where auth lives. Never let a
  browser hold a machine token.
- **Make SSE your default response shape** for anything an agent produces — you'll want streaming
  everywhere, and retrofitting it is painful.
- **Adopt a design system on day one.** With AI writing much of the UI, semantic tokens + shared
  components are the only thing that stops visual entropy.

Next: **[03 · Hosting on a VPS](./03-vps-hosting.md)**.
