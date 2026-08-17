# 14 · Build Your Own — A Minimal Blueprint

You've seen the whole of Metorite. It's a lot — dynamic loading, self-mutation, five source-system
integrations, a memory stack, HITL governance. **You do not need most of it to start.** This chapter
distills the platform down to its irreducible core, then shows the order to add capabilities so each one
earns its place.

---

## 1. The smallest thing that is still "an agent orchestration app"

Strip everything optional and you're left with five pieces:

```
┌─────────────┐   HTTP/SSE   ┌──────────────┐   run loop   ┌─────────────┐
│  Web UI     │─────────────▶│  API server  │─────────────▶│  One agent  │
│ (streams    │◀─────────────│  (auth +     │◀─────────────│ (LLM + tools│
│  events)    │   AG-UI/SSE  │   routing)   │              │  in a loop) │
└─────────────┘              └──────┬───────┘              └──────┬──────┘
                                    │                             │
                             ┌──────▼───────┐            ┌────────▼────────┐
                             │  Postgres    │            │  Model router   │
                             │ (state+audit)│            │ (LiteLLM, 1 tier)│
                             └──────────────┘            └─────────────────┘
```

1. **A model router.** Wrap LiteLLM. Even with one tier, route *through* it so swapping models later is a
   config change (chapter 09). Don't hardcode a provider.
2. **One agent.** An LLM in a think→tool→observe loop with 2–3 tools (chapter 07/08). Use an existing
   runtime (MAF, or even the raw OpenAI SDK's tool-calling) — don't write the loop yourself yet.
3. **An API server** with a streaming endpoint (SSE) and a single auth check (chapter 02/05).
4. **A web UI** that opens the SSE stream and renders text + tool activity (chapter 13).
5. **Postgres** for whatever state you have + an append-only audit log. One database (chapter 03).

That's a real, useful agent app. Everything else in this course is an *upgrade* to this core.

---

## 2. The build order (each step earns the next)

Add capability only when you feel the pain it solves. A sensible sequence:

| Step | Add | You'll want it when… | Chapter |
|---|---|---|---|
| 0 | The 5-piece core above | Always — start here. | 02, 07, 08, 09, 13 |
| 1 | **Tiered routing + context-fitting** | One model is too costly for cheap tasks, or prompts overflow. | 09 |
| 2 | **HITL approval queue** (a Postgres table) | An agent could do something irreversible. | 06 §6 |
| 3 | **Streaming reconnection** (tee events to Redis) | Users lose runs on refresh / flaky networks. | 06 §8, 13 |
| 4 | **Real auth** (SSO + gateway Bearer + roles) | More than one user, or you're going to production. | 05 |
| 5 | **External integrations** (MCP or webhook workers) | The agent needs to touch ClickUp/CRM/email. | 10 |
| 6 | **OAuth-to-third-parties + encrypted key store** | You act on a *user's* Gmail/CRM on their behalf. | 05 |
| 7 | **Memory** (conversation history, then episodic) | Agents repeat questions / forget across runs. | 07, 11 |
| 8 | **Multi-agent** (delegation-as-tool) | One agent's prompt is doing too many jobs. | 06 §5, 07 |
| 9 | **Dynamic agent loading** (agents in their own repos) | You want to ship agents without redeploying. | 06 §3 |
| 10 | **Self-mutation** (sandbox fixes failing agents) | You have many agents and want auto-recovery. | 12 |
| 11 | **Fallback / confidence escalation** | A cheap model sometimes isn't good enough. | 09 §4 |

**Resist the urge to build steps 9–11 first.** They're the most impressive but the least essential; they
only pay off once you have enough agents that manual deploys and manual fixes actually hurt.

---

## 3. Decisions that are hard to reverse — get them right early

Some choices are cheap to make now and expensive to retrofit:

- **Stream from day one.** Make your agent endpoint SSE and model output as *typed events* (chapter 13)
  even in the MVP. Bolting streaming onto a request/response design later touches everything.
- **Route models through an abstraction.** Never let app code name a concrete model (chapter 09). This is
  one function now; it's a refactor across the codebase later.
- **Put a server-side proxy between browser and agent API.** It's where auth lives; a browser must never
  hold a machine token (chapter 05). Adding this later means re-plumbing every call.
- **One durable store, audited.** Start with Postgres + an append-only audit log. The audit log is nearly
  free early and invaluable later — you cannot reconstruct history you didn't record.
- **A thin agent contract.** If you'll ever have more than one agent, define the smallest possible
  interface (Metorite's is one function, `build_agents()`), and make tools/memory/routing *platform
  services*, not per-agent code (chapter 07 §6).

---

## 4. Decisions you can safely defer

Equally important — things you should *not* over-engineer up front:

- **Kubernetes / multi-node.** A single VPS with Docker + systemd + Caddy goes remarkably far (chapter 03).
- **A separate vector DB / graph DB.** pgvector in your existing Postgres covers early semantic search;
  add specialized stores only when a real scale problem forces it (ADR-002).
- **A durable workflow engine.** For human approvals, a Postgres "pending action" row survives restarts
  and needs no workflow framework (chapter 06 §6). Reach for durable-task engines only for genuine
  multi-day pauses.
- **A dedicated LLM proxy service.** The LiteLLM *SDK*, in-process, avoids an extra service to run and
  monitor (chapter 09).
- **Self-mutation.** Genuinely cool, genuinely last. It presupposes agents-in-repos, tests-per-agent, and
  a sandbox. Earn it.

---

## 5. A concrete two-week starting point

If you want a checklist to actually begin:

1. **Day 1–2** — FastAPI app with `/health` and a `POST /chat/stream` SSE endpoint; wrap LiteLLM behind a
   `complete()` function; one hardcoded agent with a `get_weather`-style toy tool. Prove the tool-call loop
   streams to `curl`.
2. **Day 3–4** — Next.js page that opens the stream and renders text + tool activity. Add a design system
   (semantic tokens + a few shared components) *now*, while it's cheap.
3. **Day 5–6** — Postgres: persist chat sessions + an audit log. Add one real tool that reads a real API.
4. **Day 7–8** — Auth: SSO on the frontend, a Bearer token between the Next.js proxy and the API, and a
   role check on one endpoint.
5. **Day 9–10** — Deploy: one VPS, Docker for Postgres, systemd for the two apps, Caddy for TLS, and a
   GitHub Actions pipeline that gates on tests and SSH-deploys.
6. **Then** — add capabilities from the table in §2 as you hit their pain points.

At the end of that you have, in miniature, the same architecture as Metorite: a streaming,
authenticated, audited, tool-calling agent app on the open internet. Everything else is depth.

---

## 6. The three ideas to carry away

If you forget the rest of this course, keep these:

1. **An agent is an LLM in a tool-calling loop.** Orchestration is the safety, routing, streaming, and
   governance *around* that loop — and that's where most of the real engineering is.
2. **Make agents data, models a config, and writes human-gated.** Load behaviour at runtime, route every
   model call through an abstraction, and never let an agent be the only place a fact lives or an
   irreversible action happens unattended.
3. **Boring infra, streamed everywhere, audited always.** One VPS, SSE from day one, an append-only log.
   Ship the small correct thing; add depth when the pain is real.

← Back to the **[index](./README.md)**.
