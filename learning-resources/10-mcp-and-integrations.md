# 10 · MCP & Connecting to External Apps

An agent is only as useful as the systems it can touch. Metorite connects to ClickUp, Zoho CRM,
Gmail, Microsoft 365, WhatsApp, and more. There are **two complementary ways** it does this, and knowing
when to use each is the point of this chapter:

1. **MCP (Model Context Protocol)** — a *standard* adapter that exposes an app's operations as
   agent-callable tools, ideal for giving an agent live, interactive access.
2. **Classic integration workers** — webhooks + REST + OAuth, ideal for *ingesting* data into the platform
   and executing approved writes back out.

---

## 1. MCP — the universal tool adapter

### The problem it solves

Without MCP, every integration means hand-writing tool functions (chapter 08) for each app: a
`create_clickup_task`, a `search_zoho_deals`, and so on — bespoke code you own and maintain. MCP
standardizes this. An **MCP server** is a process that speaks a common protocol and exposes an app's
capabilities as a menu of tools. An **MCP client** (your agent runtime) connects to it and those tools
appear in the agent's tool list automatically. Anyone can publish an MCP server for an app; you consume it
without writing integration code.

Think of it as **"USB for agent tools"**: a standard plug, so any compliant tool source works with any
compliant agent runtime.

### How Metorite uses it

The platform has an **MCP server registry** (Postgres `mcp_servers` table, managed via
`/integrations/mcp` CRUD endpoints and a Control Plane UI). Each entry describes how to reach one server:

```
name          e.g. "clickup"
transport     "stdio"  (launch a subprocess and talk over stdin/stdout)
              or "http-sse"  (connect to a running HTTP server)
command/url   how to start / where to find it
env_vars      secrets/config injected into the server process
agent_scope   which agents may use it (["*"] = all)
enabled       on/off
```

At agent-build time, the declared MCP servers are handed to the agent. For a MAF/Copilot agent this is
literally a `mcp_servers=` config:

```python
GitHubCopilotAgent(
    instructions="…",
    mcp_servers={"clickup": {"type": "stdio", "command": "uvx", "args": ["mcp-clickup"],
                              "env": {"CLICKUP_API_TOKEN": "…"}}},
)
```

Two things to notice:

- **Credential injection happens here.** The `env` for an MCP server is populated from the encrypted
  Integration Registry (chapter 05) based on what the agent's `config.json` *declared* it needs — the
  server process receives the tokens, the agent code never sees raw secrets.
- **Transport choice.** `stdio` runs the server as a child process (simple, local, great for CLI-style
  tools). `http-sse` connects to a separately-running/remote server (good for containerized or shared
  servers). Same tools, different plumbing.

### MCP tools *are* just tools

Once connected, MCP-provided tools enter the exact same tool-call loop as native functions (chapter 08).
The model can't tell the difference and doesn't need to — it calls `create_task(...)` whether that's your
Python function or an MCP server's tool. **MCP changes where a tool comes from, not how it's called.**

### A related idea: OpenAI-style plugins

Metorite also supports installing **plugins** from an `ai-plugin.json` manifest + an OpenAPI spec: it
fetches the OpenAPI document and *auto-generates* tool definitions from its paths. This is the same goal as
MCP — turn an external API into agent tools without bespoke code — via a different standard (OpenAPI
instead of MCP). Both are "adapters that manufacture tools from a description."

---

## 2. Classic integrations — webhooks, REST, OAuth

MCP is great for *interactive* agent access. But a lot of integration work is **ambient ingestion**: keep
the platform's mirror of company data fresh, and react to changes. That's handled by dedicated worker apps
(`apps/ingestion`, `apps/email_ingestion`), and the pattern is consistent across sources.

### The ingestion pattern

```
External system changes
   │  (a) push: webhook            (b) pull: scheduled sync
   ▼                                ▼
POST /webhooks/<source>          scheduler tick (e.g. every 5 min)
   │ verify signature               │ incremental via a cursor
   │ (HMAC or shared token)         │ (modified-since / history_id)
   ▼                                ▼
enqueue to Redis Stream ──────────▶ background worker:
   return 200 immediately             fetch full object via REST (auth-refresh as needed)
                                       normalize → write to the entity graph (Postgres)
                                          → agents react to the new facts
```

Concrete instances:

| Source | Auth | Ingest mechanism |
|---|---|---|
| **ClickUp** | Personal API token in an `Authorization` header. | Webhook (`/webhooks/clickup`, HMAC-SHA256 verified) → fetch full task via REST. |
| **Zoho CRM** | OAuth 2.0 refresh-token flow (chapter 05); token cached ~1 h. | Webhook (`/webhooks/zoho`, shared-secret) + incremental sync with `If-Modified-Since`. |
| **Gmail** | Google Workspace **service account** with domain-wide delegation (impersonate a mailbox). | Pub/Sub push (`/webhooks/gmail`) → `users.history.list` since a stored `history_id`. |
| **Microsoft 365 email** | Per-user OAuth (chapter 05), tokens encrypted per account. | Scheduled Graph API sync; token refreshed on 401 or near expiry. |
| **Apollo / Google Maps / Instantly / SMTP / …** | API keys / SMTP creds in the encrypted store. | Called on demand by prospecting/sending skills. |

Key robustness ideas, all worth copying:

- **Fire-and-forget webhooks.** Verify signature, enqueue, return `200` fast. Do the slow work
  out-of-band. A burst can't overload you, and a redelivered webhook can be made idempotent.
- **Verify every webhook's signature.** HMAC or a shared secret — the endpoint is public and otherwise
  spoofable.
- **Incremental sync via a durable cursor.** Store the last `history_id`/`modified-since` and only pull
  deltas. Full re-syncs are for bootstrap and recovery.
- **Idempotent upserts.** Write on `(account_id, provider_message_id)` conflict-update, so replays and
  overlapping syncs converge instead of duplicating.

### Writes go through the Action Broker

Reads flow in freely; **writes back to source systems do not.** Every mutation (create a task, update a
deal) is proposed to the HITL approval queue (chapter 06 §6) and executed by the Action Broker only after a
human approves. This is the "read-mostly mirror" principle — the platform is never the sole owner of a
fact, and it can't silently corrupt a CRM.

---

## 3. When to use which

| You want… | Use |
|---|---|
| An agent to *interactively* query/act on an app during a run | **MCP server** (or plugin) → tools in the loop |
| To keep a fresh mirror of an app's data and *react* to changes | **Ingestion worker** (webhook + REST + cursor) |
| To *write back* to a source system | **Action Broker** (approval-gated), regardless of read mechanism |

Often you use both for the same app: an ingestion worker keeps the entity graph current *and* an MCP
server lets an agent poke the live API when a task needs something the mirror doesn't have.

---

## 4. The credential thread that ties it together

Across every mechanism, one rule holds: **the platform owns credentials; agents declare needs by name.**
The encrypted Integration Registry (chapter 05 §4) is the single source of truth. MCP servers get secrets
injected into their process env; ingestion workers read them from the store; OAuth refresh is managed
centrally. No integration credential ever lives in an agent's Git repo, and least-privilege is enforced at
load time by only resolving the integrations an agent *declared*.

Next: **[11 · Microsoft Agent Framework (MAF)](./11-microsoft-agent-framework.md)**.
