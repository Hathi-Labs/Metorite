# MCP & Plugin Integration — Design Brainstorm

> **Status:** Phase A SHIPPED · Phases B–C not started (was: Brainstorm / Design proposal)
> **Date:** 2026-06-14 · **Re-dated 2026-08-09:** Phase A shipped (D7) with the MAF-side injection gap ticketed as WS-8c (`agent_architecture.md` §12.2); Phases B/C remain research.
> **Scope:** How Model Context Protocol (MCP) servers and Claude-style plugins
> extend Metorite beyond the current REST API integration model.

> **Update 2026-08-01 (doc-truth pass):** **Phase A (MCP Server Registry, §6) is SHIPPED** —
> the `mcp_servers` table (`infra/postgres/13_mcp_servers.sql`), gateway CRUD + connectivity-test
> endpoints (`GET/POST/DELETE /integrations/mcp`, `POST /integrations/mcp/test` in
> `apps/services/gateway/gateway/routes/integrations.py`), and per-run injection with the
> `agent_scope` filter (`_inject_mcp_servers` in
> `apps/services/orchestrator/orchestrator/_tool_injection.py`, invoked from `executor.py` on every
> agent load) — see `core_module_map.md` B4. **Phases B (plugins) and C (store) remain not
> started.** Per `work_plan.md` decision D7. Note the shipped injection differs from the §2.2
> sketch: it writes `agent._mcp_servers` (the field the Copilot SDK actually reads), NOT
> `default_options` — and it only takes effect on Copilot-SDK agents (see the §2.2 note).

---

## 1. The Three Integration Tiers

Metorite's Integrations page now surfaces three distinct integration
patterns — each suited to a different class of problem:

| Tier | Pattern | Discovery | Auth | Best For |
|------|---------|-----------|------|----------|
| **APIs** | REST endpoints + hardcoded resolvers | Manual (we define `_REGISTRY` entries) | API keys stored in env → Integration Registry | Known services with stable contracts (Zoho, ClickUp, Apollo) |
| **MCPs** | Model Context Protocol servers | Runtime (server self-describes tools) | API key / token via Integration Registry | Infrastructure you control (databases, file systems, internal tools) |
| **Plugins** | Self-describing manifest + OpenAPI spec | Install-time (manifest → tool list) | OAuth 2 / API key via Integration Registry | SaaS integrations, community sharing, zero-code installs |

---

## 2. MCP Servers — Deeper Dive

### 2.1 What MCP Adds Beyond REST APIs

REST APIs require us to write a resolver for every service (see
`packages/acb_skills/acb_skills/integrations.py` — each `_zoho_crm()`,
`_apollo()`, etc.). MCP flips this: the **server** describes its own tools.

| Capability | REST API (current) | MCP Server (proposed) |
|---|---|---|
| Tool discovery | Hand-coded in `integrations.py` | Server advertises `tools/list` at connection time |
| Tool invocation | Custom HTTP logic per service | Standardised `tools/call` with typed params |
| Streaming results | Manual SSE/WebSocket handling | Native streaming in MCP transport |
| Binary data | Manual base64/file handling | Native resource protocol for files, images, blobs |
| Schema evolution | We update resolver code | Server updates its manifest — agent picks it up automatically |
| Local access | N/A | stdio transport for local DBs, file systems, CLIs |

### 2.2 Proposed Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    Control Plane (Next.js)                   │
│  ┌─────────────────────────────────────────────────────────┐│
│  │  Integrations Page                                      ││
│  │  ┌──────────┐  ┌──────────────┐  ┌──────────────────┐  ││
│  │  │ APIs tab │  │  MCPs tab    │  │  Plugins tab     │  ││
│  │  │ (active) │  │  (coming)    │  │  (coming)        │  ││
│  │  └──────────┘  └──────────────┘  └──────────────────┘  ││
│  └─────────────────────────────────────────────────────────┘│
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                  Gateway (FastAPI)                           │
│  ┌─────────────────────────────────────────────────────────┐│
│  │  /integrations/mcp/                                     ││
│  │    GET    → list registered MCP servers                  ││
│  │    POST   → register new MCP server (url + auth)         ││
│  │    DELETE → remove                                       ││
│  │    POST /test → connectivity check                       ││
│  └─────────────────────────────────────────────────────────┘│
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                  Postgres — mcp_servers table                │
│  Columns:                                                   │
│    name          TEXT PRIMARY KEY                            │
│    transport     TEXT  — 'stdio' | 'http-sse'               │
│    command       TEXT  — for stdio (e.g. 'npx -y @model...')│
│    url           TEXT  — for http-sse                        │
│    auth_config   JSONB — {type, token_env_var, ...}         │
│    agent_scope   JSONB — ["*"] or ["agent-sales", ...]     │
│    enabled       BOOL                                        │
│    created_at    TIMESTAMPTZ                                  │
│    updated_at    TIMESTAMPTZ                                  │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│              Orchestrator — executor.py                      │
│                                                              │
│  run_agent_stream(agent_name, payload):                      │
│    1. Load agent via Dynamic Agent Loader                    │
│    2. Query mcp_servers WHERE agent_scope includes agent     │
│    3. Build mcp_servers= config dict:                        │
│       {                                                      │
│         "postgres": {                                        │
│           "transport": "stdio",                              │
│           "command": "npx -y @modelcontextprotocol/...",    │
│           "env": {"PGHOST": "...", "PGPASSWORD": "..."}      │
│         },                                                   │
│         "brave-search": {                                    │
│           "transport": "http-sse",                           │
│           "url": "https://mcp.brave.com/sse",                │
│           "headers": {"Authorization": "Bearer <token>"}     │
│         }                                                    │
│       }                                                      │
│    4. Pass to GitHubCopilotAgent(default_options={           │
│         "mcp_servers": {...}                                 │
│       })                                                     │
│    5. MAF runtime connects to MCP servers, fetches           │
│       tool manifests, injects tools into session             │
└─────────────────────────────────────────────────────────────┘
```

> **Update 2026-08-01 (doc-truth pass):** as shipped, `_inject_mcp_servers` is invoked for *every*
> loaded agent, but it merges into `agent._mcp_servers` — a **Copilot-SDK** field (the SDK's
> session config reads `self._mcp_servers`; `default_options["mcp_servers"]` as sketched above is
> never read by anyone). Native-MAF agents (e.g. the email assistant) have no code path that reads
> that attribute, so MCP injection is **effectively Copilot-only today — a silent no-op for
> native-MAF agents**. A MAF-side MCP path is an open gap (also flagged in
> `drawio_integration.md` §1.4 and `core_module_map.md` B4).

### 2.3 Credential Flow for MCP

MCP servers need auth tokens just like REST APIs. The Integration Registry
already stores these. The flow:

```
Agent config.json declares:
  "integrations": ["clickup", "brave-search"]

* clickup     → REST API resolver (_clickup in integrations.py)
* brave-search → MCP server (new entry in mcp_servers table)

At runtime:
  1. build_integrations() resolves REST API credentials → state["integrations"]
  2. _build_mcp_servers() queries mcp_servers table, looks up credentials
     from Integration Registry for each server's declared auth needs
  3. Merged mcp_servers= dict injected into GitHubCopilotAgent
```

### 2.4 What MCP Servers Enable

| MCP Server | Enables | Agent Use Case |
|---|---|---|
| **Postgres** | Agents run SQL directly | "Show me revenue by customer this quarter" — agent queries DB, returns table |
| **Filesystem** | Agents read/write workspace files | "Save this report as PDF and put it in outputs/" |
| **Brave Search** | Live web search | "Research competitor X's latest funding round" |
| **GitHub** | PR, issue, code management | "Create a PR with the schema migration from yesterday's meeting" |
| **Slack** | Read/post to channels | "Summarise what the sales team discussed in #deals this week" |
| **Puppeteer** | Headless browser automation | "Screenshot the dashboard at this URL and attach it" |
| **Memory** | Persistent knowledge graph | "What do we know about Acme Corp from past conversations?" |
| **Sequential Thinking** | Multi-step reasoning | Complex analysis that benefits from structured thought chains |
| **Custom Internal** | Any in-house service | "Check inventory levels for SKU-1234 in Odoo" |

### 2.5 Local vs Remote MCP

| Transport | When | Example |
|---|---|---|
| **stdio** (local process) | Server runs on same machine as Metorite | Postgres MCP, Filesystem MCP — needs local access |
| **HTTP/SSE** (remote) | Server is hosted elsewhere | Brave Search, GitHub — cloud-hosted MCP servers |

For Docker deployment: stdio MCP servers run as sidecar containers in the
same docker-compose network. The orchestrator container spawns them as
subprocesses (stdio) or connects via HTTP (SSE).

---

## 3. Claude-Style Plugins — Deeper Dive

### 3.1 Plugin Anatomy

A Claude-style plugin is two files served from a URL:

```
https://my-plugin.example.com/
├── .well-known/
│   └── ai-plugin.json          ← manifest
└── openapi.yaml                 ← API spec
```

**`ai-plugin.json` (manifest):**
```json
{
  "schema_version": "v1",
  "name_for_human": "Stripe",
  "name_for_model": "stripe",
  "description_for_human": "Process payments and manage subscriptions.",
  "description_for_model": "Use when the user asks about payments, invoices, or subscriptions. Supports creating payment links, listing customers, and checking subscription status.",
  "auth": {
    "type": "oauth2",
    "client_url": "https://connect.stripe.com/oauth/authorize",
    "scope": "read_write",
    "authorization_url": "https://connect.stripe.com/oauth/token"
  },
  "api": {
    "type": "openapi",
    "url": "https://my-plugin.example.com/openapi.yaml"
  },
  "logo_url": "https://my-plugin.example.com/logo.png",
  "contact_email": "dev@example.com",
  "legal_info_url": "https://example.com/terms"
}
```

**`openapi.yaml` (API spec):**
```yaml
openapi: 3.0.1
info:
  title: Stripe Plugin
  version: 1.0.0
paths:
  /v1/customers:
    get:
      operationId: listCustomers
      summary: List all customers
      description: Returns a paginated list of Stripe customers.
      parameters:
        - name: limit
          in: query
          schema: { type: integer, default: 10 }
  /v1/payment_links:
    post:
      operationId: createPaymentLink
      summary: Create a payment link
      description: Generates a Stripe payment link for a given amount and currency.
      requestBody:
        required: true
        content:
          application/json:
            schema:
              type: object
              properties:
                amount: { type: integer, description: "Amount in cents" }
                currency: { type: string, default: "usd" }
              required: [amount]
```

### 3.2 How Plugins Differ from MCP

This is a critical distinction:

| Dimension | MCP Server | Claude Plugin |
|---|---|---|
| **Protocol** | MCP (JSON-RPC over stdio/SSE) | REST (OpenAPI-described HTTP) |
| **Tool description** | Server's `tools/list` response (runtime) | OpenAPI spec (install-time parsing) |
| **Auth model** | Token injected at connection time | OAuth 2 flow (user grants access) or API key |
| **Who runs it** | You host the MCP server | The plugin provider hosts the API |
| **Distribution** | Internal — you control the server | Public — anyone can publish a manifest URL |
| **Best for** | Infrastructure you own (DBs, file systems) | SaaS services (Stripe, HubSpot, Notion, Slack) |
| **Streaming** | Native streaming support | HTTP request-response (no native streaming) |
| **Binary data** | Native resource protocol | Base64 in JSON responses |

**Key insight:** MCP is for tools where you run the server. Plugins are for
tools where someone else runs the API. They complement each other.

### 3.3 Proposed Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                  Postgres — plugins table                    │
│  Columns:                                                   │
│    id              UUID PRIMARY KEY                          │
│    name            TEXT UNIQUE  — 'stripe', 'hubspot'        │
│    manifest_url    TEXT  — URL to ai-plugin.json             │
│    openapi_url     TEXT  — URL to openapi.yaml               │
│    manifest        JSONB — cached manifest content           │
│    openapi_spec    JSONB — cached & parsed OpenAPI spec      │
│    auth_type       TEXT  — 'oauth2' | 'api_key' | 'none'    │
│    auth_config     JSONB — OAuth endpoints, scopes, etc.     │
│    credentials     JSONB — encrypted tokens (via pgcrypto)   │
│    tools_generated JSONB — converted tool definitions        │
│    enabled         BOOL                                      │
│    version         TEXT  — from manifest                     │
│    created_at      TIMESTAMPTZ                               │
│    updated_at      TIMESTAMPTZ                               │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│           Plugin Tool Generator (new module)                 │
│   packages/acb_skills/acb_skills/plugin_tools.py             │
│                                                              │
│  async def install_plugin(manifest_url: str) → Plugin:       │
│    1. Fetch manifest (ai-plugin.json)                        │
│    2. Fetch OpenAPI spec                                     │
│    3. Validate both                                          │
│    4. Convert OpenAPI operations → tool definitions:         │
│       For each path + method:                                │
│         • tool_name = f"{plugin_name}_{operationId}"         │
│         • description = summary + description                │
│         • parameters = OpenAPI params → JSON Schema          │
│    5. Store in plugins table                                 │
│    6. Return Plugin with tool list                           │
│                                                              │
│  async def get_plugin_tools() → list[Callable]:              │
│    """Return async def tool functions for all enabled        │
│    plugins. Each function wraps an HTTP call to the          │
│    plugin's API with credentials from Integration Registry."""│
│    for plugin in enabled_plugins:                            │
│      for tool_def in plugin.tools_generated:                 │
│        yield _make_plugin_tool_fn(plugin, tool_def)          │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│              Executor — tool injection                       │
│                                                              │
│  _build_extended_tools(agent):                               │
│    tools = []                                                │
│    tools.extend(agent.tools)           # agent's own tools   │
│    tools.extend(_injected_tools())     # call_agent, web_... │
│    tools.extend(await get_plugin_tools())  # plugin tools    │
│    return tools                                              │
│                                                              │
│  Tools injected at GitHubCopilotAgent construction time.     │
│  The LLM sees plugin tools alongside its own tools in the    │
│  system prompt.                                              │
└─────────────────────────────────────────────────────────────┘
```

> **Update 2026-08-01 (doc-truth pass):** unbuilt (Phase B). If implemented, don't copy this
> Copilot-only sketch — the shipped platform tool injection (`_inject_agent_tools`) already covers
> both runtimes (MAF `agent.tools` + Copilot `agent._tools`); plugin tools should ride that path.

### 3.4 OAuth 2 Flow for Plugins

```
User clicks "Connect Stripe" in Control Plane
  → Gateway initiates OAuth 2 flow:
      1. Redirect to Stripe's authorization page
         (with our client_id, redirect_uri, scope, state)
      2. User approves in Stripe
      3. Stripe redirects to our callback:
         GET /integrations/plugins/oauth/callback?code=...&state=...
      4. Gateway exchanges code for access_token + refresh_token
      5. Encrypts tokens → stores in plugins.credentials (pgcrypto)
      6. Redirects user back to Control Plane with "Connected ✓"

On each agent run:
  → Executor loads plugin tools
  → _make_plugin_tool_fn() reads credentials from plugins table
  → If access_token expired, uses refresh_token to get new one
  → Injects fresh token into API request headers
```

### 3.5 What Plugins Enable

| Plugin Category | Examples | Enabled Agent Capabilities |
|---|---|---|
| **Payments** | Stripe, Razorpay | "Create an invoice for ₹50,000 and send the payment link" |
| **CRM** | HubSpot, Salesforce | "Find all deals closing this month and summarise pipeline" |
| **Productivity** | Notion, Airtable, Linear | "Create a Linear issue from this Zoho deal" |
| **Communication** | Slack, Discord, Teams | "Post the weekly report to #exec-updates" |
| **Calendar** | Google Calendar, Cal.com | "Schedule a follow-up call with Acme Corp next Tuesday" |
| **Documents** | Google Drive, Dropbox | "Save the proposal PDF to the Client Proposals folder" |
| **Analytics** | Mixpanel, Amplitude | "What was our DAU trend last week?" |
| **E-commerce** | Shopify, WooCommerce | "Check inventory and flag items below reorder threshold" |
| **Developer** | Jira, GitLab, Sentry | "File a bug report from this customer complaint" |
| **Community** | Custom plugins built by users | Infinite extensibility — the plugin store model |

### 3.6 Plugin vs MCP — Decision Framework

```
                        ┌─────────────────────────────┐
                        │  Do you want to extend       │
                        │  Metorite with a new    │
                        │  tool or data source?        │
                        └─────────────┬───────────────┘
                                      │
                    ┌─────────────────┼─────────────────┐
                    ▼                 ▼                   ▼
            ┌──────────────┐  ┌──────────────┐  ┌──────────────┐
            │ The tool is a │  │ You run the  │  │ A third party│
            │ known service │  │ server       │  │ runs the API │
            │ we've already │  │ (database,   │  │ (SaaS) and   │
            │ integrated    │  │ filesystem,  │  │ provides an  │
            │ (Zoho,ClickUp)│  │ internal svc)│  │ OpenAPI spec │
            └──────┬───────┘  └──────┬───────┘  └──────┬───────┘
                   ▼                 ▼                   ▼
            ┌──────────────┐  ┌──────────────┐  ┌──────────────┐
            │  REST API    │  │  MCP Server  │  │   Plugin     │
            │  (existing)  │  │              │  │              │
            │              │  │ Register URL │  │ Install from │
            │ Add resolver │  │ in MCP tab   │  │ URL or store │
            │ to integra-  │  │ Auth via     │  │ OAuth 2 flow │
            │ tions.py     │  │ Integration  │  │ Auto-generate│
            │              │  │ Registry     │  │ tools from   │
            │              │  │              │  │ OpenAPI spec │
            └──────────────┘  └──────────────┘  └──────────────┘
```

---

## 4. Unified Runtime Tool Model

All three tiers should converge into a single tool representation at
runtime so agents don't need to know the difference:

```python
# Proposed: UnifiedTool — abstracts over API, MCP, and Plugin tools

@dataclass
class UnifiedTool:
    name: str                    # e.g. "stripe_listCustomers"
    description: str             # LLM-visible description
    parameters: dict             # JSON Schema for arguments
    source: Literal["api", "mcp", "plugin"]
    source_config: dict          # How to invoke:
                                 #   API: {resolver, endpoint}
                                 #   MCP: {server_name, tool_name}
                                 #   Plugin: {plugin_id, operation_id, base_url}

    async def invoke(self, args: dict, integrations: dict) -> str:
        """Single dispatch point. Routes to correct backend."""
        ...
```

This means:
- The executor builds one flat tool list from all three sources
- `_build_injected_tools_addendum()` describes all tools uniformly
- The agent's system prompt doesn't distinguish between API/MCP/Plugin tools
- Adding a new MCP server or plugin automatically extends every agent's
  capability — no agent code changes needed

---

## 5. What This Unlocks — Scenarios

### 5.1 Cross-System Workflows (Plugin + API + MCP)

> "A deal just closed in Zoho CRM. Create an invoice in Stripe, post a
>  celebratory message to Slack, and create a delivery task in ClickUp."

This single sentence triggers:
- **Zoho CRM** (REST API) → reads deal details
- **Stripe** (Plugin) → creates invoice via OpenAPI-described endpoint
- **Slack** (MCP) → posts message to #deals channel
- **ClickUp** (REST API) → creates task with delivery details

No agent code was written for Stripe or Slack — they were installed as
plugin/MCP and auto-discovered.

### 5.2 Self-Service Analytics (MCP)

> "Show me revenue by product category for Q2, compared to Q1."

- **Postgres MCP** → agent writes and runs the SQL query
- Agent formats results as a markdown table
- If the user asks for a chart: agent generates one via code execution

### 5.3 Research-Backed Prospecting (MCP)

> "Find 10 manufacturing companies in Bangalore that recently raised funding."

- **Brave Search MCP** → searches for news about funding rounds
- **Google Maps API** → finds manufacturing companies in Bangalore
- **Apollo API** → enriches with contact data
- Agent cross-references and returns a scored prospect list

### 5.4 Autonomous Operations (Plugin + MCP)

> "Every Monday at 9 AM, check what's overdue, post a summary to Slack,
>  and create follow-up tasks for each owner."

- **GitHub MCP** → checks PR and issue status
- **ClickUp API** → finds overdue tasks
- **Slack MCP** → posts summary
- **ClickUp API** → creates follow-up tasks per owner

Scheduled via the existing cron trigger system in `config.json`.

### 5.5 Community-Driven Extensibility (Plugin Store)

> A user writes a plugin for their industry-specific ERP. They publish the
> manifest URL. Another company in the same industry installs it from the
> store with one click. Metorite agents can now interact with that ERP.

This is the plugin store model: Metorite becomes a platform, not just
a product.

---

## 6. Implementation Roadmap

### Phase A — MCP Server Registry (estimated: 1-2 weeks)

> **Update 2026-08-01 (doc-truth pass):** SHIPPED — see the banner at the top of this document.
> (Injection targets `agent._mcp_servers`, not `default_options`; the UI tab is a skeleton.)

- [ ] New Postgres migration: `mcp_servers` table
- [ ] Gateway CRUD endpoints: `GET/POST/DELETE /integrations/mcp/`
- [ ] Gateway test endpoint: `POST /integrations/mcp/test`
- [ ] Executor: `_build_mcp_servers()` — queries table, builds config
- [ ] Executor: inject `mcp_servers=` into `GitHubCopilotAgent`
- [ ] UI: MCP tab — list, add form, test button, status indicator
- [ ] Documentation: how to run an MCP server with Metorite

### Phase B — Plugin System (estimated: 2-3 weeks)

- [ ] New Postgres migration: `plugins` table
- [ ] Plugin manifest parser: `packages/acb_skills/acb_skills/plugin_tools.py`
- [ ] OpenAPI → tool definition converter
- [ ] OAuth 2 flow: gateway endpoints for authorize + callback
- [ ] Credential storage: pgcrypto-encrypted tokens
- [ ] Token refresh: auto-refresh on 401 during tool invocation
- [ ] Executor: `_build_plugin_tools()` — loads plugin tools
- [ ] Gateway CRUD: `GET/POST/DELETE /integrations/plugins/`
- [ ] UI: Plugin tab — install from URL, OAuth connect flow, tool list preview

### Phase C — Plugin Store (estimated: 3-4 weeks)

- [ ] Plugin repository: curated list + community submissions
- [ ] Rating and review system
- [ ] Auto-update: check manifest for version bumps
- [ ] Plugin sandboxing: rate limiting, timeout, allowed domains
- [ ] UI: Plugin store browser — categories, search, install, rate

---

## 7. Security Considerations

| Concern | Mitigation |
|---|---|
| **Plugin code execution** | Plugins are NOT code — they are API descriptions. Only HTTP calls are proxied. No arbitrary code runs on Metorite servers. |
| **Credential leaks** | All tokens encrypted at rest (pgcrypto). Never logged. Never in agent repos. |
| **OAuth token scope** | Minimum required scopes declared in manifest. User approves only what's needed. |
| **Plugin supply chain** | Manifest + OpenAPI spec validated on install. Hash-pinned. Version-locked until user updates. |
| **Rate limiting** | Per-plugin rate limits prevent abuse. Configurable per agent. |
| **MCP server trust** | Only registered servers are connected. stdio servers run as isolated subprocesses. HTTP servers must use HTTPS + auth. |
| **Audit trail** | Every tool call (API/MCP/Plugin) logged with inputs, outputs, latency, and credential source. |

---

## 8. Open Questions

1. **MCP transport in Docker:** For stdio MCP servers, do we run them as
   sidecar containers or subprocesses in the orchestrator container?
   Sidecar is cleaner but adds docker-compose complexity. Subprocess is
   simpler but couples MCP server lifecycle to orchestrator.

2. **Plugin tool naming collisions:** What if two plugins expose a tool
   named `listCustomers`? Namespace by plugin ID (`stripe_listCustomers`)?
   Or let the user alias on install?

3. **Plugin auth per user vs per workspace:** Should OAuth tokens be
   per-user (each person connects their own Stripe) or per-workspace
   (one shared connection)? Both have use cases.

4. **Plugin versioning:** Should agents auto-update to new plugin versions
   or stay pinned? Pinned-by-default is safer; opt-in update.

5. **MCP vs Plugin overlap:** If someone publishes both an MCP server and
   a plugin for the same service, how does the user choose? The decision
   framework in §3.6 should guide them — but the UI should make it clear.

---

## 9. References

- [Model Context Protocol specification](https://modelcontextprotocol.io/)
- [Claude Plugins documentation](https://docs.anthropic.com/en/docs/agents-and-tools/claude-code/plugins)
- [OpenAPI 3.0 specification](https://spec.openapis.org/oas/v3.0.3)
- Metorite Integration Registry: `packages/acb_skills/acb_skills/integrations.py`
- Agent executor: `apps/services/orchestrator/orchestrator/executor.py` (MCP injection now in `apps/services/orchestrator/orchestrator/_tool_injection.py`)
- System architecture: `project-docs/system_architecture.md`
- Agent builder guide: `project-docs/agent_repo_compatibility.md`
