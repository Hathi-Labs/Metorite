# Integrations — one registry, and an admin console over it

> **Board row:** WS-54 · **Created:** 2026-10-11 · **Owner question:** 2026-10-11
> **Status:** 🔨 **IN-0 BUILT 2026-10-11** (not merged). Its part (e) is held
> behind H-244. IN-1 to IN-7 are a 📝 PLAN, verified against code and
> production on 2026-10-11 at `origin/main` `ba6bbb7b6`.

> **Decision:** D96 (§10). The orchestrator took it on 2026-10-11 when the owner
> asked, and the owner was told the same day. The owner may overrule it.

> **Sibling:** `crm_platform.md` (WS-53) builds the first row of the registry,
> the Zoho connection (CRM-Z2). This file owns the registry as a whole, the
> console, and the retirement of the old Integrations page.

---

## 0. The answer in one paragraph

An admin manages every connection of the organization in one place, the
Integrations app. A member connects a tool inside the app that needs it, such
as "Connect Zoho" inside the CRM. Both places read and write one registry,
`integration_connections`.

A customer credential lives only in that registry, encrypted, and reaches a
run as an argument. It never goes into `os.environ` or `.env`. MCP servers are
connections too, and an agent on MAF must be able to use them. The page that
exists today does none of this, and it has live cross-tenant holes. So the
first slice, IN-0, closes the holes before anything else.

---

## 1. Scope and non-goals

### 1.1 What the owner asked (2026-10-11)

"One place where an admin can manage all of their integrations, MCPs, and so
on." And: should an integration live in its app, or in a separate app? §3.1 is
the answer: both, over one registry.

### 1.2 In scope

- The stopgap that closes the cross-tenant writes and reads (IN-0).
- The registry read model over every connection kind (IN-1).
- The admin console (IN-2), which replaces `/integrations`.
- Customer credentials per request, never in the process environment (IN-3, IN-5).
- MCP servers per org, and an MCP client for MAF agents (IN-4).
- A decision on custom APIs and plugins, which store data that nothing reads (IN-6).
- GitHub per org (IN-7).

### 1.3 Non-goals

- **Moving `email_accounts` or `wa_accounts` into the registry.** A mailbox and
  a WhatsApp number stay in their own tables. The read model unions them (IN-1).
- **Operator keys.** The keys Metorite pays for (model providers, the mail
  apps, the Zoho OAuth client) belong to the operator plane (D56, CP-10). The
  console never shows or writes them.
- **A marketplace or a plugin store.** `mcp_plugin_integration.md` Phases B and C
  stay unstarted.
- **A connector for a new provider.** Each provider comes with the app that
  needs it, as Zoho comes with the CRM.

---

## 2. Measured state (2026-10-11)

### 2.1 Production

- 0 rows in `mcp_servers`, 0 in `plugins`, 0 in `custom_api_definitions`.
- 1 row in `provider_keys` with `credential_type = 'integration'`, in 1 org.
- 8 orgs other than Fracktal. Each has an `executive`, so each has a member who
  holds `feature:integrations` and the EXECUTIVE role.
- `/integrations` is `preview` (`src/lib/nav.ts:495-503`), so it has no nav
  entry. The routes answer.

So no secret has crossed a tenant yet. The paths below are open all the same.

### 2.2 Defects

Each one is real at `ba6bbb7b6`. The last column names the slice that fixes it.

| Id | Defect | Evidence | Slice |
|---|---|---|---|
| **IN-D1** | 🔴 **A customer key goes into the process environment and `.env`.** Every org on the box then uses it. | `routes/integrations.py:1044,1048` (configure), `:1210,1214` (PUT `/keys`), `:1276` (DELETE `/keys`), `:2195-2196` (GitHub device), `:2389-2390` (GitHub CLI), `routes/oauth.py:255-256`, `acb_llm/key_store.py:459` | IN-0, then IN-5 |
| **IN-D2** | 🔴 **The writers have no role check.** PUT and DELETE `/keys`, `/discover`, `/custom`, `/mcp`, `/mcp/test`, `/plugins`, the GitHub device flow, and the OAuth authorize and refresh check `feature:integrations` only. `integrations:manage` exists, and no route requires it. | `integrations.py:1118,1238,1302,1491,1565,1791,1834,1857,1931,2026,2108,2151`, `oauth.py:126,215`, `acb_auth/permissions.py:156` | IN-0 |
| **IN-D3** | 🔴 **The MCP list reads every org's rows and returns their secrets.** `mcp_servers` is exempt from RLS, and the list opens an unbound session. It returns `headers` and `env_vars`. | `integrations.py:1762-1787` | IN-0 |
| **IN-D4** | 🔴 **The MCP delete removes a row of any org by name.** | `integrations.py:1844` | IN-0 |
| **IN-D5** | 🔴 **Server-side request forgery.** `/mcp/test` makes the gateway GET any URL with the caller's headers. Plugin install fetches any URL. | `integrations.py:1857-1887`, `:1948-1983` | IN-0 |
| **IN-D6** | **MCP register and plugin install cannot succeed.** They put `%s` inside `text()`, omit `organization_id`, and use `ON CONFLICT (name)` against a key that migration 158 changed. No test covers them. | `integrations.py:1811-1825`, `:1994-2011` | IN-4 |
| **IN-D7** | **The agent resolvers read only the environment.** A per-org key never reaches a run, and `credential()` lets the environment win over the per-run value. | `acb_skills/integrations.py:63-196`, `:322-339` | IN-3 |
| **IN-D8** | **Status is deployment-wide.** `configured` comes from the environment, and `_is_configured` answers True for an unknown service. `/test` tests the environment. | `integrations.py:388-424` (`:420`), `:744-778`, `:1595-1605` | IN-1 |
| **IN-D9** | **The old OAuth route is dead and unscoped.** Its state binds no org and no member, it writes `.env`, `refresh_access_token` has no caller, and no UI uses it. | `routes/oauth.py:61-119`, `:231-264` | IN-5 (retire), CRM-Z2 replaces it for Zoho |
| **IN-D10** | **GitHub is one token for the deployment**, and connect-cli imports the server's own `gh` token. | `integrations.py:2327-2390` | IN-7 |
| **IN-D11** | **A model-output token writes configure with the internal token and no tenant** (`<<<SETUP:svc:KEY=val>>>`). | `orchestrator/executor.py:5737-5755`, `src/app/api/agent/chat/route.ts:927` (the pattern) and `:937` (the POST), HANDOFF H-244 | IN-0 (e), held behind H-244 |
| **IN-D12** | **MCP on MAF does nothing.** Injection returns early for an agent that is not Copilot, and no MAF MCP client exists. The page says "every agent can discover" the servers. | `orchestrator/_tool_injection.py:2039-2041`, `integrations/page.tsx:1333,1407`, WS-8c, H-217 | IN-4 |
| **IN-D13** | **Custom APIs and plugins store data that nothing reads.** `custom_api_definitions.service_id` is unique across every org. | `12_custom_api_definitions.sql:8`, `integrations.py:641,815,1467` | IN-6 |
| **IN-D14** | **No single registry.** Connections live in `provider_keys`, `email_accounts`, `wa_accounts`, `mcp_servers`, `.env` and a disk token cache. The page fakes WhatsApp's `configured` from a second fetch. | `integrations/page.tsx:816-831`, `zoho/client.py:17` | IN-1 |
| **IN-D15** | **The discover call to the model is not metered** (H-287). | `integrations.py:1315` | IN-6 |
| **IN-D16** | **Stale text.** The page says "credentials encrypted at rest" while it also writes `.env`. The `email_accounts` comment says AES-GCM, and the cipher is Fernet. `mcp_plugin_integration.md` still says injection writes `agent._mcp_servers`. | `integrations/page.tsx:866`, `17_email_accounts.sql:21`, `mcp_plugin_integration.md:12-14` | IN-2 |
| **IN-D17** | **One tenant changes the Copilot model of every tenant.** `POST /settings/llm/copilot-model` writes `COPILOT_CHAT_MODEL` into the `.env` of the deployment. It needs only `feature:models`, and every org admin holds it. The value takes effect for all orgs at the next restart. Found 2026-10-11 in the IN-0 review. | `routes/settings.py:940-956` | IN-5 |
| **IN-D18** | **The GitHub account card shows the operator's account to a member.** `GET /integrations/github/account` reads the deployment `GITHUB_TOKEN` and runs `gh auth status` on the box. It returns the operator's login and scopes to any member with `feature:integrations`. Found 2026-10-11 in the IN-0 review. | `routes/integrations.py:2367-2439` on the IN-0 branch (`:2252-2324` at `b41674ea3`) | IN-7 |

---

## 3. The design

### 3.1 Connect in the app, manage in one place

| Who | Where | Does what |
|---|---|---|
| A member | Inside the app that needs it | Connects their own account: a mailbox, a WhatsApp number, later a calendar |
| An admin | Inside the app that needs it | Connects an org account: Zoho for the CRM |
| An admin | The Integrations console | Sees every connection, with its app, owner, scopes, health and last sync. Reconnects, disconnects, and adds a connection that has no app, such as an MCP server |
| The operator | The Operator Console | Holds the keys Metorite pays for (D56). Never shown to a customer |

The console links to the owning app for setup. It never repeats a connect flow
that an app owns. This is the Email rule (D-EM-1) made general.

### 3.2 One registry

`integration_connections` holds every org-level and member-level connection
that no app table already holds. CRM-Z2 creates it. Its shape is in
`crm_platform.md` §5.3.

The console reads one **read model**, `GET /integrations/connections`. It
unions three sources and gives one row shape:

| Source | Rows |
|---|---|
| `integration_connections` | Zoho, MCP servers (after IN-4), GitHub (after IN-7), the next providers |
| `email_accounts` | Mailboxes. An admin sees each mailbox's owner, provider and health, and never its content (D-EM-4) |
| `wa_accounts` | WhatsApp numbers |

**One row shape:** `id`, `kind`, `provider`, `owning_app`, `scope` (`org` or
`member`), `owner`, `status`, `last_sync_at`, `last_error`, `setup_href`. The
shape holds no credential, ever.

### 3.3 The credential rule

1. **A customer credential lives in one encrypted row.** It is Fernet through
   `key_store`. One store module per table is its only reader.
2. **It reaches a run as an argument**, through the per-run context that
   `test_integration_env_scoping.py` already fences. It never goes into
   `os.environ`, `.env`, a disk cache, a log or the browser.
3. **The operator's keys stay in the operator plane.** They may stay in the
   environment, because they are one value for every org by design.
4. **The console never returns a stored value.** It returns "set" or "not
   set", and the last 4 characters at most.

Rule 2 for every integration is `work_plan.md` §6 gate (f) work, the end of
process-global credential injection (D58.2, H-43). IN-3 builds it for the
integration resolvers. IN-5 removes the old writes.

### 3.4 MCP

- **An MCP server is a connection** with `kind = 'mcp'` and an org scope. Its
  auth header is a credential, so rule 3.3 applies.
- **MAF agents get an MCP client** (IN-4). This is WS-8c. Without it, an MCP
  server reaches no agent, because D92 moves every agent to MAF.
- **MCP calls are egress.** They pass the egress guard and the per-run
  `integrations:use:<svc>` grant, as every tool does (H-236).
- **Zoho's MCP** is the agent action plane for the CRM (`crm_platform.md` §7.2,
  CRM-Z9). It is a member-scoped connection, because Zoho applies each
  member's own roles.

### 3.5 Who may do what

| Act | Floor |
|---|---|
| Read the console | `admin:members:read` (the admin test that `/auth/me` reports as `is_admin`) |
| Add, change or delete an org connection | `integrations:manage` |
| Connect or disconnect your own member connection | The member, in the owning app |
| Disconnect another member's connection | `integrations:manage`, and the member is told |
| See a stored value | Nobody |

---

## 4. Security and tenancy

1. Every table that holds a connection has FORCE RLS. If it does not, only a
   store that binds the tenant reads it (R5).
2. No tenant and no identity come from the request.
3. The gateway fetches a URL that a customer typed only through its one URL
   guard, `gateway/outbound_guard.py`. The guard refuses a private, loopback,
   link-local or metadata address, and it does not follow a redirect. Do not
   use `acb_skills/egress.py` here, because it classifies tools, not URLs.
   IN-4 adds a port allowlist.
4. A write to a connection writes one audit row: who, what, when, which org.
5. The old routes that write the environment stop doing it in IN-0. They keep
   the per-org store write.

---

## 5. Shell manifest (R9)

| Field | Value |
|---|---|
| `href` | `/integrations` |
| `label` | Integrations |
| `feature` | `integrations` |
| `launch` | `preview` until IN-2 is done and the owner promotes it |
| `team` | `admin` |
| `blurb` | "Every tool your company has connected" |
| `jobs` | `integrations.add_mcp`, `integrations.review` (a link to the list) |
| `needs` | `integrations`. A connection in `needs_reconnect` or `error`, for admins |
| `agent` | none |

---

## 6. Owner setup

| Act | Why it is the owner's | Slice |
|---|---|---|
| Confirm or overrule D96 | Product shape | Before IN-1 |
| Decide the fate of custom APIs and plugins (Q2) | A feature leaves the product | IN-6 |
| Promote `/integrations` to `live` | §6.0 C3, H-21 | After IN-2 |

---

## 7. Tickets

### 7.1 The order

```
IN-0 ─► IN-1 ─► IN-2 ─► (owner) promote
  │       ▲
  │   CRM-Z2 (creates integration_connections)
  ├─► IN-3 ─► IN-5
  ├─► IN-4 (needs IN-1 for the row; WS-8c for MAF)
  └─► IN-6, IN-7 in any order
```

IN-0 comes first and alone, because it closes live holes.

### 7.2 Slices

| Id | What | Gate | Done when |
|---|---|---|---|
| **IN-0** ✅ built 2026-10-11, (e) held behind H-244 | **Close the holes.** (a) Six routes stop writing `os.environ` and `.env`: configure, PUT and DELETE `/keys` keep the per-org store write. The GitHub device poll stores `github:token` for the org. connect-cli answers 410. The old OAuth authorize, callback and refresh are retired (410). A failed store write in configure answers 503. (b) Every writer requires `integrations:manage`. (c) The MCP list and delete filter on the caller's tenant, and the list returns key names only, never a `headers` or `env_vars` value. (d) `/mcp/test` and plugin install fetch only through `gateway/outbound_guard.py`. (e) **Held behind H-244 [OWNER]:** the `<<<SETUP:...>>>` path stops writing configure. After (a) it can write no env value, so holding it leaves no cross-tenant hole | 🟢 AGENT-SAFE for (a) to (d). ⚠️ It changes behaviour: a key set on the page stops reaching agents until IN-3. On prod the startup copy is already inert (9 orgs), so the cost is close to zero | A planted `os.environ` write in any of the six routes fails a fence. A member without `integrations:manage` gets 403 on each writer. Org B's MCP list shows none of org A's rows and no secret value. `/mcp/test` to `http://127.0.0.1` and to `http://169.254.169.254` is refused with no request sent. Each test red first |
| **IN-1** | **The read model.** `GET /integrations/connections` over the three sources of §3.2. Status per org, not per deployment. `_is_configured` answers False for an unknown service | 🟢 AGENT-SAFE. Needs CRM-Z2 for the table | Two orgs see only their own rows. No response holds a credential (a sentinel fence). A mailbox row shows owner and health and no message |
| **IN-2** | **The console.** It replaces the five tabs with one list of connections, filters by app and status, and opens a side panel with reconnect, disconnect, and a link to the owning app. MCP gets an "Add" form. The stale text goes (IN-D16). ⚠️ The MCP "Test" button sends `s.headers || {}`, and since IN-0 the list returns no headers. So a server that needs an auth header tests without it. The console must test with the stored headers | 🟢 AGENT-SAFE | Visual review passes. Every action reaches the store through the routes of IN-0 and IN-1. `AppTopBar` and the shared controls only |
| **IN-3** | **Per-run credentials for the integration resolvers.** `acb_skills` resolvers read the per-run context, filled from the per-org store, and the environment stops winning | 🔴 §6 gate (f) | A run in org A gets org A's key and a run in org B gets org B's, on one process at one time (R8) |
| **IN-4** | **MCP per org, and on MAF.** Fix register and install (IN-D6), move MCP rows into the registry, and give MAF agents an MCP client behind the egress guard (WS-8c). ⚠️ The INSERTs of `POST /mcp` and plugin install omit `organization_id` and use `ON CONFLICT (name)`. So both answer 500 on a migrated database. That is IN-D6 | 🟢 AGENT-SAFE | A registered server reaches a MAF agent in its org only. Its header never reaches a log |
| **IN-5** | **Retire the old writes.** Remove the env-write code, `_persist_tokens`, the dead OAuth route, and `configure_integrations`'s copy into the environment for customer keys. Move `COPILOT_CHAT_MODEL` out of the deployment `.env` (IN-D17) | 🔴 §6 gate (f) | `os.environ[` with a customer key appears nowhere outside the operator plane (fence) |
| **IN-6** | **Custom APIs and plugins.** Keep them with a reader and per-org keys, or retire them | 🔴 owner (Q2) | Per the answer |
| **IN-7** | **GitHub per org.** The device flow writes a member or org connection, not the deployment token. connect-cli goes. `GET /github/account` shows the org's own connection, never the operator's account (IN-D18) | 🟢 AGENT-SAFE | Two orgs hold two tokens. The server's own `gh` token is never imported |

---

## 8. Verification commands

```bash
bash scripts/dev_db.sh && eval "$(bash scripts/dev_db.sh --export)"
uv run pytest tests/unit/test_integrations_env_hardening.py \
  tests/unit/test_integrations_mail_app_keys.py \
  tests/unit/test_credential_tenant_threading.py \
  tests/unit/test_mt0d_per_org_credentials.py \
  tests/unit/test_mcp_servers_org_scope.py \
  tests/unit/test_integration_env_scoping.py \
  tests/unit/test_org_access_credentials.py \
  tests/unit/test_integrations_in0.py \
  tests/unit/test_integrations_mcp_tenant_r8.py
cd workbench/control_plane && npx tsc --noEmit && npx vitest run src/lib/missingIntegrations.test.ts src/app/integrations
```

Each slice adds its own test file to this list.

---

## 9. Fences (R7)

| Rule | Fence | Slice |
|---|---|---|
| No integrations route writes the process environment | A new AST fence over `routes/integrations.py` and `routes/oauth.py` | IN-0 |
| Every integrations writer requires `integrations:manage` | A route walk, in the shape of the `GATED_ROUTERS` walk in `test_org_access_enforcement.py` | IN-0 |
| The MCP list binds the tenant and returns no secret | R8, two orgs | IN-0 |
| A customer-typed URL passes the egress guard | A test with private and metadata addresses | IN-0 |
| No response of the read model holds a credential | A sentinel fence | IN-1 |
| A customer key reaches only its own org's run | R8 | IN-3 |

---

## 10. Decision and questions

### D96 — Integrations: connect in the app, manage in one console, over one registry (2026-10-11)

*The owner asked on 2026-10-11 whether an integration belongs in its app or in a
separate app, and asked the orchestrator to decide. The orchestrator decided
and told the owner the same day. The owner may overrule it.*

- **D96.1** A connect flow lives in the app that needs it. The console links to
  it and never repeats it.
- **D96.2** The Integrations app is an admin console over one registry. It also
  holds the connections that have no app, such as MCP servers.
- **D96.3** A customer credential never enters the process environment or
  `.env`. It reaches a run as an argument.
- **D96.4** An MCP server is a connection, and MAF agents get an MCP client
  (WS-8c).

**Why not one central connect page.** The app knows its scopes, its mode and its
import. Email already proved the in-app flow (D-EM-1). And the central page of
today is where every hole of §2.2 lives.

### Questions

| Id | Question | Recommendation |
|---|---|---|
| **Q1** | Ship IN-0 now, although a key set on the page stops reaching agents until IN-3? | **Decided: yes** (orchestrator, 2026-10-11, a security fix inside the dev window). The startup copy is already inert on prod, so almost nothing stops working |
| **Q2** | Custom APIs ("Add API" with a model-written schema) and plugins: keep, or retire? | Retire. 0 rows on prod, no reader, an unmetered model call. Bring them back on the registry when a customer asks |
| **Q3** | Should a member see the console, read-only, for their own connections? | No. A member sees their connections in each app. The console is for admins |
