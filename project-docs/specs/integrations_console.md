# Integrations — one registry, and an admin console over it

> **Board row:** WS-54 · **Created:** 2026-10-11 · **Owner question:** 2026-10-11
> **Status:** ✅ **IN-0, IN-1, IN-2 and IN-7 are MERGED** (2026-10-11: #859,
> #878, #880 and #891). Part (e) of IN-0 is held behind H-244.
> ✅ **IN-4 is MERGED** (2026-10-12, #905), for the org scope only.
>
> IN-3, IN-5 and IN-6 are a 📝 PLAN, verified against code and
> production on 2026-10-11 at `origin/main` `ba6bbb7b6`. The MAF MCP client
> and IN-D6 moved to WS-8c on 2026-10-11 (`agent_architecture.md` §12.2).

> **Built:** IN-1 on 2026-10-11, in `routes/integrations_connections.py`. It adds
> `GET /integrations/connections`, `POST /integrations/connections/{id}/disconnect`
> and `POST /integrations/mcp/{name}/test`. The tests are
> `test_integrations_connections.py` and `test_integrations_connections_r8.py`.

> **Decision:** D96 (§10). The orchestrator took it on 2026-10-11 when the owner
> asked, and the owner was told the same day. The owner may overrule it.

> **Sibling:** `crm_platform.md` (WS-53) builds the first row of the registry,
> the Zoho connection (CRM-Z2). This file owns the registry as a whole, the
> console, and the retirement of the old Integrations page.

> **IN-8 (added 2026-10-12):** 📝 PLAN. MCP OAuth for an org connection, under
> D98. The design is §3.6, and the slices are in §7.2. It ships dark behind
> `MCP_OAUTH_ENABLED`. Verified against code on 2026-10-12 at `origin/main`
> `713dc84ac`, and against the six providers on the same day.

> **IN-8a BUILT (2026-10-13, PR #931), dark.** The protocol half is
> `connections/mcp_oauth.py`, and the routes are in
> `routes/integrations_mcp_oauth.py`. The guard mode is
> `outbound_guard.request_https`. The BFF has the callback and the public
> CIMD document. Rows O1 to O6, O9 and O11 to O13 pass, and each was red
> first. No migration.

> **The Zoho CRM tile (BUILT 2026-10-14).** The APIs view showed Zoho CRM as
> "Not set up" while the CRM held a live Zoho connection. The gateway read
> the operator's `ZOHO_*` keys, and the client counted key rows only. No
> migration.

> `APP_OWNED_SERVICES` in `routes/integrations.py` now maps the tile to the
> CRM's row (`zoho`, `crm`). The status reads that row in the tenant session
> and offers no key form. The test reads the CRM's own credential.
> `APP_OWNED_TILES` in `lib/connections.ts` is the client mirror, and the
> panel links to CRM settings. The fences are `test_integrations_zoho_tile.py`,
> `test_integrations_zoho_tile_r8.py` and `appOwnedTile.test.ts`.

> **Two gaps stay for IN-3.** The agent gate (`?agent=`) still reads the
> operator's `ZOHO_*` keys, because the agent runtime
> (`acb_skills.integrations._zoho_crm`) reads them. IN-3 moves that runtime
> onto the org's CRM connection. A tenant can also still `PUT` a
> `ZOHO_CLIENT_ID` key into `provider_keys`, because the layer C allowlist
> holds it. Only the startup copy reads that row, and only on a box with one
> organization. IN-5 retires that copy.

**Five facts of IN-8a that the design did not state:**

1. **The runtime of IN-8a.** With the flag on, a run sends the stored access
   token as a fixed `Authorization` header. It leaves out a token that ends
   within 60 s (`mcp.oauth_expired`). IN-8b adds the refresh.
2. **The server must list S256.** An auth server that does not list `S256`
   in `code_challenge_methods_supported` gets `pkce_unsupported` (MCP
   2025-11-25).
3. **Redis holds the DCR secret sealed.** The pending flow holds it as Fernet
   ciphertext for its ten minutes.
4. **The row must still be the server.** The callback saves only when the url
   of the row equals the url of the consent. Else it revokes the new token
   and answers `server_gone`.
5. **The document comes from the gateway.** The BFF route
   `/oauth/mcp-client.json` reads `/integrations/mcp/oauth/client-metadata`
   with the service bearer, because the document names nobody.

The review of #931 added four rules, in §3.6.3 and §3.6.4. CIMD needs `iss`.
A delete revokes. A register keeps the tokens. The Test sends the bearer.

`expires_in` is bounded too. No value means no expiry, and `0` means the
token has ended. A value above ten years becomes ten years, and a negative
value answers `exchange_failed`.

> **IN-8b BUILT (2026-10-14, PR #1011), dark.** The refresh hop is
> `mcp_oauth.refresh`. The run path is `_oauth_header` and
> `mark_unauthorized` in `connections/mcp.py`, and rule 9 of
> `orchestrator/_maf_mcp.py`. Rows O7, O8 and O14 pass, and each was red
> first. The fences are `test_mcp_oauth_refresh.py` and
> `test_mcp_oauth_refresh_r8.py`. No migration.

**Five facts of IN-8b that the design did not state:**

1. **The header stays `static_headers`.** §3.6.7 asked for a
   `header_provider` that returns a cached value. A fixed header does the
   same thing, and MAF does not serialize the tool calls of a fixed header
   (the `static_headers` docstring, `_mcp.py:3834-3840`, MAF 1.19).
2. **Two time caps.** One token fetch gets 8 s (`REFRESH_FETCH_S`). A run
   waits 20 s for the step of one server (`REFRESH_BUDGET_S`). A fetch past
   its cap is not final. The run keeps the stored token while it lives past
   60 s.
3. **The 401 mark compares the token.** `mark_unauthorized` changes the row
   only when the row still holds the token that the run sent. A new consent
   between the refresh and the 401 stays connected.
4. **A token with no refresh token** gets `needs_reconnect`
   (`no_refresh_token`) when it ends. Only a new consent repairs it.
5. **The console shows `needs_reconnect`** on an MCP row, with its safe
   code. Before IN-8b, such a row showed as `disconnected`.

**The review of #1011 added three rules.**

- **A run that refreshes nothing takes no lock.** An unlocked read finds the
  rows that are due. Only a due row opens a second session and takes the row
  lock. `REFRESH_SLOTS` lets one process run two refresh steps at one time,
  so a slow provider cannot take the pool of every org.
- **A run refreshes only what it can use.** `servers_for_org` takes
  `oauth_wanted`. The injection passes its scope, transport and grant
  filter, and a Copilot agent wants no OAuth row.
- **A Stop never loses a rotated token.** The step runs in its own task, and
  the run waits on it through `asyncio.shield`. A cap or a Stop ends the
  wait, and the step still commits. Only a process restart can lose it.

**Review 2 of #1011 added four rules.**

- **One org holds one slot.** `REFRESH_SLOTS_PER_ORG` is 1, inside the two
  slots of the process. An org takes its own slot first.
- **A failed refresh waits.** A refresh that fails for a reason that is not
  final is not tried again by that process for 60 s (`REFRESH_BACKOFF_S`).
  A run in the gap uses the stored token while it lives.
- **A 401 with no refresh ends the token** (§3.6.7). The next run refreshes.
- **A short token is due at half its life.** The blob holds the issue time
  (`SourceCredential.issued_at`). A blob with no issue time keeps the margin of
  900 seconds. `fresh_credential` takes an `accept` check, so a row that is
  registered again with headers is never marked `undecryptable`.

The refresh does not revoke the old refresh token. A provider that rotates
ends it itself, and none of the six providers of §3.6.2 asks for a revoke.
~~The Test button still sends the stored token with no refresh (IN-8a, P2
3).~~ IN-8c changed this. See its fact 4 below.

> **IN-8c BUILT (2026-10-15), dark.** The console half is in
> `src/app/integrations/`, and the BFF route is
> `api/integrations/mcp/[name]/oauth/start`. The card line is rule 10 of
> `orchestrator/_maf_mcp.py`. Row O10 passes, and each fence was red first.
> The fences are `components/mcpConnect.test.ts`, the `route.test.ts` of the
> start, and the IN-8c cases of `test_maf_mcp_confirm.py`. No migration.

**Five facts of IN-8c that the design did not state:**

1. **Two new keys on the console row.** `acts_as` is the `connected_by` of
   an OAuth MCP row. `can_connect` is true for a member with
   `integrations:manage`. Both keep their defaults while
   `MCP_OAUTH_ENABLED` is off, so the console then draws nothing new.
2. **Which row offers a consent.** An OAuth row offers Reconnect. A row with
   no stored header offers Connect. A row with a header map, a disabled row
   and every row that is not MCP offer none.
3. **The provider in the label is the host.** The label reads "Acts as
   <admin> at <host>", and the host comes from the URL of the server. The
   token goes to that host, so the host names the place. A label is only a
   name that an admin typed.
4. **The Test follows the rule of a run.** `oauth_test_header` sends a due
   token through `_oauth_header`, the refresh step of a run. A Test after a
   401 then refreshes the token. A Test of a row in `needs_reconnect` sends
   nothing. No run uses a disabled row, so its Test gets no refresh and
   sends the stored token while it lives. A Test that gets a 401 does not
   mark the row. Only a run marks it.
5. **The card line sits after the hint line, on the same line.** The card
   draws the detail as one line. So the detail reads: the hint line, then
   "Acts as <admin> at <host>.", then the server and the tool.

**The BFF start.** It sends no body upstream, and it refuses a request that
another site sends. It passes on the URL of the gateway only when that URL
is an absolute `https` URL with no user part. Any other answer is a 502, so
the route cannot send a browser to a target that the request chose.

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
- MCP servers in the registry (IN-4). The MCP client for MAF agents is WS-8c.
- A decision on custom APIs and plugins, which store data that nothing reads (IN-6).
- GitHub per org (IN-7).
- MCP servers that need an OAuth consent, for an org connection (IN-8, D98).

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
- **A member connection to an MCP server over OAuth.** The owner put it after
  the org connections on 2026-10-12 (§3.6.1). IN-8 builds the org scope only.

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
| **IN-D3** | 🔴 **The MCP list reads every org's rows and returns their secrets.** `mcp_servers` is exempt from RLS, and the list opens an unbound session. It returns `headers` and `env_vars`. | `integrations.py:1762-1787` (`:1821-1855` at `713dc84ac`) | IN-0 |
| **IN-D4** | 🔴 **The MCP delete removes a row of any org by name.** | `integrations.py:1844` (`:1978` at `713dc84ac`) | IN-0 |
| **IN-D5** | 🔴 **Server-side request forgery.** `/mcp/test` makes the gateway GET any URL with the caller's headers. Plugin install fetches any URL. | `integrations.py:1857-1887`, `:1948-1983` | IN-0 |
| **IN-D6** | **MCP register and plugin install cannot succeed.** They put `%s` inside `text()`, omit `organization_id`, and use `ON CONFLICT (name)` against a key that migration 158 changed. No test covers them. | `integrations.py:1827-1841`, `:2073-2092` (at `c1850b5d9`) | WS-8c |
| **IN-D7** | **The agent resolvers read only the environment.** A per-org key never reaches a run, and `credential()` lets the environment win over the per-run value. | `acb_skills/integrations.py:63-196`, `:322-339` | IN-3 |
| **IN-D8** | **Status is deployment-wide.** `configured` comes from the environment, and `_is_configured` answers True for an unknown service. `/test` tests the environment. | `integrations.py:388-424` (`:420`), `:744-778`, `:1595-1605` | IN-1 |
| **IN-D9** | **The old OAuth route is dead and unscoped.** Its state binds no org and no member, it writes `.env`, `refresh_access_token` has no caller, and no UI uses it. | `routes/oauth.py:61-119`, `:231-264` | IN-5 (retire), CRM-Z2 replaces it for Zoho |
| **IN-D10** | **GitHub is one token for the deployment**, and connect-cli imports the server's own `gh` token. | `integrations.py:2327-2390` | IN-7 |
| **IN-D11** | **A model-output token writes configure with the internal token and no tenant** (`<<<SETUP:svc:KEY=val>>>`). | `orchestrator/executor.py:5737-5755`, `src/app/api/agent/chat/route.ts:927` (the pattern) and `:937` (the POST), HANDOFF H-244 | IN-0 (e), held behind H-244 |
| **IN-D12** | **MCP on MAF does nothing.** Injection returns early for an agent that is not Copilot, and no MAF MCP client exists. The page says "every agent can discover" the servers. | `orchestrator/_tool_injection.py:2063-2064` (at `c1850b5d9`), `integrations/page.tsx:1333,1407`, H-217 | WS-8c |
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
| `integration_connections`, GitHub App mode | One `github` row for each org, with `provider_meta.auth` of `app` or `token`. `github_integration.md` (2026-10-18) owns the App, its connect flow and its webhook. The customer-made OAuth App path and the device flow retire in its GH-7 |
| `email_accounts` | Mailboxes. An admin sees each mailbox's owner, provider and health, and never its content (D-EM-4) |
| `wa_accounts` | WhatsApp numbers |

**One row shape:** `id`, `kind`, `provider`, `owning_app`, `scope` (`org` or
`member`), `owner`, `status`, `last_sync_at`, `last_error`, `setup_href`. The
shape holds no credential, ever.

**The link of a missing connection (`ai_studio.md` I8a).** A connection that
nobody connected has no row, so its link cannot come from a row.
`setup_href_for(provider)` in `routes/integrations_connections.py` is the
provider-level map. It reads the owning app of the provider in the connector
list (§3.7 rule 3), then `_SETUP_HREF`. `mcp:<server>` opens the Add form of
the console with the name. The map holds Calendar and Knowledge too, so the
row of a calendar or a Drive connection links to its own app. This change is
live with no flag.

**An ask clears on a connect (`ai_studio.md` §8.2, I8b-1a).** When a store
writer leaves an org row `connected`, it deletes the open asks of members for
that provider.

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
  auth header is a credential, so rule 3.3 applies. IN-4 keeps the header map
  in `credentials_encrypted` only. At rest it is Fernet ciphertext, made with
  the one key of `acb_llm.key_store` (`ACB_MASTER_KEY`). No other column holds
  a header value.
- **MAF agents get an MCP client** (WS-8c, `agent_architecture.md` §12.2).
  Without it, an MCP server reaches no agent, because D92 moves every agent
  to MAF. IN-4 moves the rows and repoints the WS-8c reader.
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
| Open the app | Every member (D-EA-9, 2026-10-14). The pane is ungated, and each view gates itself |
| Use Connections, API keys, Plugins and Skills | `feature:integrations`, as before. A member without it sees only the AI agents view |
| See and revoke your own outside agents (AI agents view) | The member. WS-55 owns the routes (`external_agent_access.md` §15 EA-4b) |
| See and revoke every agent connection of the organization | `feature:integrations` and `integrations:manage`. WS-55 owns the routes |

### 3.6 MCP OAuth for an org connection (IN-8)

> **Decision:** D98 (`work_plan.md` §3), the owner, 2026-10-12. **Flag:**
> `MCP_OAUTH_ENABLED`, default off. **Verified against code** on 2026-10-12 at
> `origin/main` `713dc84ac`, and against the six providers of §3.6.2 on
> 2026-10-12 at 09:43 UTC. **Needs:** IN-4 merged, because the flow writes the
> registry row of the server.

#### 3.6.1 What it is

Many MCP servers take no static key. They ask for an OAuth consent, and they
issue a token for one user. An admin connects such a server for the whole
org. The connection then acts as that admin at the provider (D98).

IN-8 builds org connections only. The owner said on 2026-10-12: "get the
organization-wide connectors done, and later on we can start working on the
personal connectors." A member connection over OAuth is a later slice.

#### 3.6.2 The providers, measured 2026-10-12

**Method.** One plain HTTPS GET for each URL below, with no credential, at
09:43 UTC. "401 names" means the `resource_metadata` value of the
`WWW-Authenticate` header of the 401 answer. **CIMD** means
`client_id_metadata_document_supported: true` in the auth-server metadata.
**DCR** means that the auth-server metadata has a `registration_endpoint`.

| Provider | MCP endpoint, and its answer to a GET | Protected-resource metadata (RFC 9728) | Auth-server metadata (RFC 8414) | Registration | Notes |
|---|---|---|---|---|---|
| Notion | `https://mcp.notion.com/mcp` → 401. It names `https://mcp.notion.com/.well-known/oauth-protected-resource/mcp`, with `scope="default"` | `https://mcp.notion.com/.well-known/oauth-protected-resource/mcp` → 200. `resource` is `https://mcp.notion.com/mcp`. The auth server is `https://mcp.notion.com` | `https://mcp.notion.com/.well-known/oauth-authorization-server` → 200. `/authorize`, `/token`, `/register`, and revoke at `/token`. S256. `authorization_response_iss_parameter_supported` is true | **CIMD**. DCR also | Token auth: `none`, `client_secret_basic` or `client_secret_post` |
| Linear | `https://mcp.linear.app/mcp` → 401. It names `https://mcp.linear.app/.well-known/oauth-protected-resource/mcp`, with `scope="read write"` | `https://mcp.linear.app/.well-known/oauth-protected-resource/mcp` → 200. `resource` is `https://mcp.linear.app/mcp`. The auth server is `https://mcp.linear.app` | `https://mcp.linear.app/.well-known/oauth-authorization-server` → 200. `/authorize`, `/token`, `/register`, and revoke at `/token`. S256. The `iss` flag is true | **CIMD**. DCR also | The same token auth as Notion |
| Atlassian | `https://mcp.atlassian.com/v1/mcp` → 401, with **no** `resource_metadata` | **Not published.** `https://mcp.atlassian.com/.well-known/oauth-protected-resource/v1/mcp` → 404, and `https://mcp.atlassian.com/.well-known/oauth-protected-resource` → 404 | `https://mcp.atlassian.com/.well-known/oauth-authorization-server` → 200. `/v1/authorize`, `/v1/token`, `/v1/register`, and revoke at `/v1/token`. `plain` and S256. No `iss` flag | **DCR**. No CIMD flag | Discovery needs the fallback of §3.6.4, start step 2 |
| GitHub | `https://api.githubcopilot.com/mcp/` → 401. It names `https://api.githubcopilot.com/.well-known/oauth-protected-resource/mcp/` | That URL → 200. The auth server is `https://github.com/login/oauth`, which is **another origin** | `https://github.com/.well-known/oauth-authorization-server/login/oauth` → 200, by path insertion. `https://github.com/.well-known/oauth-authorization-server` → 404. `/login/oauth/authorize` and `/login/oauth/access_token`. S256. The `iss` flag is true. No revoke endpoint | **Pre-registered only** (IN-8d). No CIMD, no DCR | It also offers the device grant. **Not measured:** whether the IN-7 device token works at this server |
| Slack | `https://mcp.slack.com/mcp` → 401. It names `https://mcp.slack.com/.well-known/oauth-protected-resource` | That URL → 200. `https://mcp.slack.com/.well-known/oauth-protected-resource/mcp` → 404. `resource` is `https://mcp.slack.com`. The auth server is `https://mcp.slack.com` | `https://mcp.slack.com/.well-known/oauth-authorization-server` → 200. The issuer is `https://mcp.slack.com`, but authorize is at `https://slack.com/oauth/v2_user/authorize` and token at `https://slack.com/api/oauth.v2.user.access`. S256. Token auth: `client_secret_post` only. No revoke endpoint | **Pre-registered only** (IN-8d). No CIMD, no DCR | The endpoint names say a user token |
| HubSpot | `https://mcp.hubspot.com/` → 406 with `Accept: application/json`, and 401 with `Accept: application/json, text/event-stream`. The 401 names `https://mcp.hubspot.com/.well-known/oauth-protected-resource` | That URL → 200. `resource` is `https://mcp.hubspot.com`. The auth server is `https://mcp.hubspot.com`. `scopes_supported` is empty | `https://mcp.hubspot.com/.well-known/oauth-authorization-server` → 200. `/oauth/authorize/user` and `/oauth/v3/token`. S256. Token auth: `client_secret_post` only. No revoke endpoint | **Pre-registered only** (IN-8d). No CIMD, no DCR | Its docs: `https://developers.hubspot.com/mcp` |

**Not measured.** No probe sent a credential, so these facts stay open. The
owner's consent test (§3.6.10, act 1) and IN-8a measure them.

- The access-token lifetime, the refresh-token lifetime and the rotation rule
  of each provider.
- Whether each provider honours the `resource` parameter (RFC 8707).
- Whether Notion and Linear accept a CIMD URL on `app.metorite.com` in a real
  consent.
- What a revoke answers, at the three providers that publish one.
- Which provider account a token names. The acts-as label names the Metorite
  admin (§3.6.9), so it does not wait for this fact.

#### 3.6.3 Client registration order

The MCP text sets the order. On 2026-10-12, the client-registration page of
MCP 2026-07-28 and the authorization page of MCP 2025-11-25 gave one list:

1. **Pre-registered.** Metorite uses its own client for that auth server, when
   it holds one (IN-8d).
2. **CIMD**, when the auth server sets two flags:
   `client_id_metadata_document_supported` and
   `authorization_response_iss_parameter_supported`. Each CIMD flow shares
   one `client_id` and one redirect URI. So `iss` (RFC 9207) is the only
   defence against a mix-up of two auth servers. A server that does not send
   `iss` gets DCR, which gives a `client_id` for each auth server. Owner
   decision on the review of #931, 2026-10-13.
3. **DCR**, when the auth server has a `registration_endpoint`. MCP
   2026-07-28 marks DCR deprecated.
4. Else the connect fails with `no_registration`. Metorite never asks the
   admin for a client id.

⚠️ **The request for this section, and `external_agent_access.md` §5.3
(line 131), put CIMD first.** The MCP text puts pre-registration first, and
this spec follows the MCP text. For the six providers of §3.6.2 the result is
the same. Metorite holds a pre-registered client only where CIMD and DCR are
absent. The order changes the result only if the owner registers a client for
Notion or Linear. WS-55 owns the line in `external_agent_access.md`.

**The CIMD document.** Metorite publishes one JSON document at
`https://app.metorite.com/oauth/mcp-client.json`. The MCP text requires an
`https` URL with a path, and a `client_id` that equals that URL exactly. The
document holds:

- `client_id`: the URL of the document.
- `client_name`: "Metorite". `client_uri`: `https://app.metorite.com`.
- `redirect_uris`: the one callback of §3.6.4.
- `grant_types`: `authorization_code` and `refresh_token`.
  `response_types`: `code`. `token_endpoint_auth_method`: `none`.

The host comes from `_workbench_public_url()`
(`routes/email/transport/oauth.py:784`), so staging serves a document of its
own. A test pins the document against the callback path.

**A DCR client.** The registration answer can hold a `client_secret`. It goes
in the sealed blob only (§3.6.6).

#### 3.6.4 The flow

`POST /integrations/mcp/{name}/oauth/start` needs `integrations:manage`. The
callback is `GET /integrations/mcp/oauth/callback`, and it needs
`integrations:manage` too. Its public URL is
`https://app.metorite.com/api/integrations/mcp/oauth/callback`, on the proxy
path that the Zoho callback uses (`REDIRECT_PATH`,
`routes/crm/connection.py:89`). With `MCP_OAUTH_ENABLED` off, both routes
answer 404.

**The start.**

1. **Discovery.** Send a GET to the MCP URL with no token, and read
   `resource_metadata` from the 401. If no header names one, try the RFC 9728
   well-known URLs, the path-inserted form first.
   `build_protected_resource_metadata_discovery_urls` of `mcp` 1.30
   (`mcp/client/auth/utils.py:70`) gives that list.
2. **The fallback.** If no protected-resource metadata answers, use the
   origin of the MCP URL as the auth server. Atlassian needs this. MCP
   2025-03-26 had this rule, and the later texts dropped it. So the fallback
   applies only when the auth-server metadata is on the origin of the MCP URL.
3. **Checks.** The `resource` of the protected-resource metadata must equal
   the MCP URL, or its origin. The `issuer` of the auth-server metadata must
   equal the auth-server URL (`validate_metadata_issuer`, `utils.py:214`).
   Each endpoint must be `https` on port 443 (§3.6.5).
4. **Registration**, in the order of §3.6.3.
5. **PKCE.** Make a new verifier, and its S256 challenge with `s256`
   (`acb_auth/agent_oauth.py:137`). Send S256 only, also to an auth server
   that offers `plain`.
6. **State.** Call `sign_state(purpose="integrations.mcp.connect", org=…,
   member=…)` (`routes/email/transport/signing.py:233`). The state binds the
   org and the member, and it lives ten minutes. ⚠️ The state is signed, not
   encrypted. Its body is base64 JSON, so the auth server can read the admin
   email and the org UUID. The Zoho state is the same today.
7. **The pending flow.** Write it to Redis through `TenantRedis`
   (`acb_common/tenant_redis.py:420`), under the nonce of the state, for ten
   minutes. It holds the verifier, the issuer, the token endpoint, the client
   id, the server name, the resource and the scopes. The verifier never goes
   into the state or into a URL.
8. **The answer** is the consent URL. It carries `response_type=code`,
   `client_id`, `redirect_uri`, `state`, `code_challenge`,
   `code_challenge_method=S256`, `resource` (RFC 8707) and `scope`. The scope
   comes from the 401 header, else from `scopes_supported` of the
   protected-resource metadata, as the MCP text says.

**The callback**, in the order of the Zoho callback
(`routes/crm/connection.py:338`):

1. **The state, before any other work.** Call `verify_state(state,
   purpose="integrations.mcp.connect")` (`signing.py:261`). A `None`, another
   org or another member answers 400 `invalid_state`.
2. **Take the pending flow once.** Read it and delete it in one step. A
   missing record answers 400 `invalid_state`. So a replayed state fails,
   because its record is gone.
3. **`iss` (RFC 9207).** If the answer carries `iss`, or the metadata says
   that it will, it must equal the recorded issuer. Else answer 400
   `invalid_issuer`, before the code goes anywhere.
4. **An `error` from the provider** redirects to the console with a safe code.
5. **The code for tokens.** Send the code, the verifier, `redirect_uri` and
   `resource` to the token endpoint. The request goes through the guard
   (§3.6.5).
6. **Save** the credential on the registry row of the server, under the row
   lock. Set `connected_by` to the member (§3.6.6).
7. **A reconnect** revokes the old refresh token, best effort, after the
   commit. Only a provider that publishes a revoke endpoint gets the call.

**After the consent** (owner decisions on the review of #931, 2026-10-13):

- **A delete revokes.** `DELETE /integrations/mcp/{name}` and the console
  disconnect delete the row. After the commit, the gateway revokes the
  refresh token at the provider. The revoke is best effort, goes through
  `request_https`, and the log line `mcp.oauth_revoke` names the outcome
  only. A revoke that fails never fails the delete.
- **A register keeps the tokens.** `POST /integrations/mcp` with the name of
  an OAuth server merges the public keys: the label, the description, the
  agent scope, `enabled` and `always_confirm`. The tokens, `auth` and
  `connected_by` stay. A new URL answers 409 "disconnect first", because the
  token is for the old URL. The audit event still fires.
- **The Test button** of an OAuth server sends the stored bearer, the header
  that a run sends. A token that has ended answers `token_expired`. With the
  flag off, the test sends no bearer, as before.

#### 3.6.5 Every fetch goes through the guard

Every discovery, registration, token and revoke request goes through
`gateway/outbound_guard.py`, with `https` on port 443 only.

- **Do not use `mcp_client` (`outbound_guard.py:415`) for these.** Its
  transport refuses a request to another origin (`:392-393`), by design. The
  auth server often sits on another origin. GitHub sends the client to
  `github.com`, and Slack sends it to `slack.com`.
- **Do not use `request` (`:305`) as it is.** It calls `check_url`, which
  allows `http` (`_SCHEMES`, `:63`).
- **Use `check_mcp_url` (`:350`) and then `send` (`:268`)**, one pair for each
  hop. IN-8a adds one helper that does both, `request_https`, and every OAuth
  fetch calls it. A fence fails when an OAuth module calls `request`,
  `mcp_client` or `httpx` itself.
- `send` refuses a 3xx answer, and it caps the answer at 64 KiB and 10 s. A
  metadata answer and a token answer fit.
- The guard checks each URL that a metadata document names. A document can
  name a private address, and the guard refuses it, as it refuses a URL that a
  customer types.
- The consent URL goes to the admin's browser, and the gateway never fetches
  it. It must still be `https`.

#### 3.6.6 Storage

- **Tokens** go in the registry blob, as a `SourceCredential`
  (`crm_sources/base.py:60`), through `save_credential`
  (`connections/store.py:361`).
- ⚠️ **A DCR `client_secret` never goes in `provider_meta`.** `save_credential`
  and `update_tokens` copy `SourceCredential.provider_meta` into the plain
  `provider_meta` column (`store.py:380`, `:400`). So a secret there leaves
  the blob. IN-8a adds a `client_secret` field to `SourceCredential`, with
  `repr=False`. `_seal` (`store.py:232`) writes it, and no SQL parameter reads
  it. A sentinel fence plants a secret and reads the column.
- `provider_meta` holds public facts only:
  - the issuer, the token endpoint and the revoke endpoint.
  - the client id and the registration kind (`pre_registered`, `cimd` or
    `dcr`).
  - the resource.
- **Refresh** goes through `fresh_credential`
  (`connections/shared_token.py:41`). It takes the row with `FOR UPDATE`, so
  one refresh serves two workers. `invalid_grant` sets `needs_reconnect`
  (`_STOP_CODES`, `:38`), and nothing retries a dead token.
- The id and the secret of a pre-registered client are operator keys (D56).
  They live in the operator plane, never in a customer row (IN-8d).

#### 3.6.7 The runtime

- MAF calls `header_provider` synchronously (`agent_framework/_mcp.py:3744`,
  MAF 1.19). So the hook cannot refresh a token.
- Before `connect()`, the injection calls `fresh_credential`. When the token
  expires within `TOKEN_MARGIN_S`, the call refreshes it first.
  `TOKEN_MARGIN_S` is a new constant of 900 seconds. A token with a shorter
  life is due at half its life, and never later than 60 seconds before its end.
- The tool gets a `header_provider` that returns the cached `Authorization`
  value and nothing else. A changed header makes MAF connect the session again
  (the `header_provider` docstring, `_mcp.py:3841-3851`). So the value never
  changes inside a run.
- **A 401 on connect** drops the server from the run, and the run goes on. The
  log line `mcp.oauth_unauthorized` names the server only.
- If the injection refreshed the token in this run, that 401 also sets
  `needs_reconnect`. Else the 401 ends the stored token under the row lock,
  so the next run refreshes, and an `invalid_grant` then sets
  `needs_reconnect`. A token with no refresh token gets `needs_reconnect` at
  once. So one expired token never asks an admin to connect again.
  ⚠️ The request for this section said "a 401 marks needs_reconnect". This
  spec narrows that, for the reason above.
- **As built (task 3866, 2026-11-22):** a row added by URL has no tokens and no
  header. When its connect gets a 401 or 403 with a `WWW-Authenticate` header
  that names `resource_metadata`, the row becomes `needs_reconnect` with
  `last_error = needs_signin`. The console then offers Connect. A 401 with no
  challenge keeps the old path, because the server may only refuse a key. The mark needs `MCP_OAUTH_ENABLED` on, the same URL, and no header on the row. A later run still names the row in the chat (reason `reconnect`) and attaches none of its tools.
- ⚠️ The review of #1011 corrected the item above on 2026-10-14. It said
  that the next run refreshes. That was false. A token answer with no
  `expires_in` is stored for ten years. So the next run saw a live token and
  sent no refresh, and the server stayed dead for ever.
- A run that outlives the margin can get a 401 on a call. The server drops
  out of that run.
- `needs_reconnect` shows in the console, and in the "needs you" item of §5.
- With `MCP_OAUTH_ENABLED` off, the injection leaves out a row with an OAuth
  credential, and logs `mcp.oauth_disabled`.

#### 3.6.8 The library

`mcp` 1.30 (`uv.lock`) has `OAuthClientProvider`
(`mcp/client/auth/oauth2.py:228`). It waits for the code in the same process,
through `redirect_handler` and `callback_handler` (`:241-242`, `:367-370`). A
web flow gets the code in a later request, often on another worker. So the
flow does not use `OAuthClientProvider`.

The flow reuses only the pure helpers of `mcp/client/auth/utils.py` and the
pydantic models:

- `extract_resource_metadata_from_www_auth` (`:57`)
- `build_protected_resource_metadata_discovery_urls` (`:70`)
- `build_oauth_authorization_server_metadata_discovery_urls` (`:132`)
- `validate_metadata_issuer` (`:214`)
- `is_valid_client_metadata_url` (`:285`) and `should_use_client_metadata_url`
  (`:325`)

No helper in this list opens a socket. All I/O goes through §3.6.5. A
contract test pins these names, so an upgrade of `mcp` fails loud.

#### 3.6.9 Acts as the admin (D98)

- **The console** shows "acts as <admin email> at <provider>" on the
  connection row and in its side panel. The admin email is `connected_by`.
- **The Connect dialog** says, before the consent, that the agents of every
  member use this connection as the admin. It also says that each tool runs
  with no card, so every member can read and change what the admin can read
  and change at the provider (MCP-NC1, owner directive 2026-11-16).
- **The card** (`agent_architecture.md` §12.2 WS-8c, item 14) shows "acts as
  <admin email> at <provider>" for each tool of an OAuth server. The line sits
  under the hint line, and the card escapes it as it escapes the names (item
  19).
- **A tool of a member's run asks no card** (MCP-NC1, `agent_architecture.md`
  §12.2 WS-8c item 33, which amends H-299). So a member can read and change,
  through an agent, what the admin can read and change at that provider. The
  console says so, and this spec says so. WhatsApp still refuses a tool that
  does not say read-only. The H-300 setting `always_confirm`
  puts a card on every tool of one server, and a new server has it off
  (item 31 as item 33 amends it). D98.3 says "read" only, and item 33 widens
  it.
- The label names the Metorite admin, not the provider account. Metorite knows
  the admin. It does not know yet which provider account a token names
  (§3.6.2, not measured).
- A reconnect by another admin changes `connected_by`, and the label follows.

#### 3.6.10 Slices, gates and owner acts

| Slice | What | Gate |
|---|---|---|
| **IN-8a** | Discovery, CIMD and DCR, PKCE, the state, the callback and the store (§3.6.3 to §3.6.6). All dark behind `MCP_OAUTH_ENABLED` | 🟢 AGENT-SAFE |
| **IN-8b** | Refresh at run time, the 401, and `needs_reconnect` (§3.6.7) | 🟢 AGENT-SAFE |
| **IN-8c** | The console Connect button, the BFF route for `POST /integrations/mcp/{name}/oauth/start`, the dialog text, the acts-as label and the card line (§3.6.9) | 🟢 AGENT-SAFE. Visual review |
| **IN-8d** | Pre-registered providers: GitHub, Slack and HubSpot | 🔴 OWNER-GATE. The owner creates each OAuth app, and its secret goes in the operator plane (D56) |

**The order.** IN-4, then H-300, then IN-8a, IN-8b and IN-8c. IN-8d follows
IN-8a, when the owner has made the apps.

**The owner's acts.**

1. **A real consent test against a third-party account**, with Notion or
   Linear, in Fracktal. It reaches a real third party, so it is the owner's act
   (CLAUDE.md §3a rule 3). It measures the open facts of §3.6.2.
2. **For IN-8d**, create one OAuth app at GitHub, at Slack and at HubSpot,
   each with the redirect URI of §3.6.4. Drop each client id and secret into
   `~/.metorite/secrets`. An agent pushes them with `scripts/secrets.sh`.
3. **The flip of `MCP_OAUTH_ENABLED` on production** is gate
   `enforcement-flip`. An agent may do it inside the dev window (CLAUDE.md
   §3a), after act 1 passes.

#### 3.6.11 Acceptance

Each row has a fence test that fails first. All are new, and none exists yet.

| # | Done when | Fence |
|---|---|---|
| O1 | Each hop refuses before a socket opens: an `http` URL, a port that is not 443, `127.0.0.1`, `169.254.169.254`, an RFC 1918 address, and a 3xx answer. The hops are the two metadata fetches, a metadata endpoint that names a private address, registration, token and revoke. | `test_mcp_oauth_guard.py` |
| O2 | No OAuth module calls `outbound_guard.request`, `mcp_client` or `httpx` itself. | `test_mcp_oauth_guard.py` (an AST fence) |
| O3 | A replayed state answers 400 and sends no token request. A state of org A, used by a member of org B, answers 400. An expired state answers 400. A state signed for `crm.zoho.connect` answers 400. | `test_mcp_oauth_state.py` |
| O4 | A code with a wrong verifier fails the exchange and saves nothing. The consent URL carries `code_challenge_method=S256`, also when the server offers `plain`. | `test_mcp_oauth_flow.py` |
| O5 | An `iss` that differs from the recorded issuer answers 400 before the token request. | `test_mcp_oauth_flow.py` |
| O6 | A sentinel access token, refresh token and client secret show in no log line, no response body, no redirect URL and no `provider_meta` column. | `test_mcp_oauth_secrets.py` |
| O7 | Two workers find one token near its expiry. One refresh request goes out, and both workers get the new token. R8. | `test_mcp_oauth_refresh_r8.py` |
| O8 | A refresh that answers `invalid_grant` sets `needs_reconnect`. The next run sends no refresh, and the console shows the row as `needs_reconnect`. R8. | `test_mcp_oauth_refresh_r8.py` |
| O9 | Orgs A and B connect one server name. A run in A sends the token of A, and a run in B sends the token of B. The admin of A cannot see or disconnect the row of B. R8, two orgs. | `test_mcp_oauth_org_scope_r8.py` |
| O10 | The console row and its side panel show "acts as <email> at <provider>". The card for a tool of an OAuth server shows the same line. | A vitest under `src/app/integrations`, and `test_maf_mcp_confirm.py` |
| O11 | The `client_id` of the CIMD document equals its own URL, and its one redirect URI equals the callback. | `test_mcp_oauth_cimd.py` |
| O12 | With `MCP_OAUTH_ENABLED` off, start and callback answer 404, and a run leaves out an OAuth row. | `test_mcp_oauth_flow.py` |
| O13 | A contract test pins the `mcp` helpers of §3.6.8 and the signature of `header_provider`. | `test_mcp_oauth_contract.py` |
| O14 | A 401 on connect drops the server, and the run goes on. A 401 after a refresh in the same run sets `needs_reconnect`. | `test_mcp_oauth_refresh_r8.py` |

**Verify IN-8.**

```bash
bash scripts/dev_db.sh && eval "$(bash scripts/dev_db.sh --export)"
uv run pytest tests/unit/test_mcp_oauth_guard.py tests/unit/test_mcp_oauth_state.py \
  tests/unit/test_mcp_oauth_flow.py tests/unit/test_mcp_oauth_secrets.py \
  tests/unit/test_mcp_oauth_refresh_r8.py tests/unit/test_mcp_oauth_org_scope_r8.py \
  tests/unit/test_mcp_oauth_cimd.py tests/unit/test_mcp_oauth_contract.py \
  tests/unit/test_maf_mcp_confirm.py -v -rs
cd workbench/control_plane && npx tsc --noEmit && npx vitest run src/app/integrations
```

No R8 suite may skip.

### 3.7 After a connect, and before a disconnect (D220)

The owner asked on 2026-11-14 (task 3164) that AI Studio builds on connections. `ai_studio.md` §8.1 owns the Studio side. This section owns the console side. Each rule binds when its slice merges.

1. **The result card (IN-9).** After any connect, in any app or in the console, one card says what works now: the apps and features that use the connection, and what Mira can do with it. It then names the templates for it. Last, it offers "Build an app on <provider>", only for a provider whose data no built-in app shows. The card is never a modal, and it never starts a build. CE-10d (task 3069) is the model.
2. **Used by, and the disconnect preview (IN-10).** The sheet of a connection lists each app, workflow and skill that uses it. A disconnect shows that list first, and asks once.
3. **The connector list (IN-11).** One curated file lists each provider that a member can connect. Each row gives its sign-in kind, its owning app, what it unlocks, whether a built-in app shows its data, and where to find a key. The console, Studio, the result card and the chat read that file. It is not a marketplace (§1.3). **BUILT (2026-11-14, task 3174). Studio I8a (task 3168) is the first caller. It reads the label and the scope of a provider.** The file is `packages/acb_common/acb_common/connector_list.json`, with 16 rows. `acb_common.connector_list` reads it in Python. `src/lib/connectors.ts` reads it in TypeScript through `connectors.generated.ts`, which `scripts/gen_connectors.py` writes. Left: the Studio name check (I8, I1b), the console swap and the result card (IN-9).
4. **A connection outlives its person (IN-12).** An org connection over OAuth acts as the admin who connected it (D98). The member removal names each such connection of the leaver, and an admin connects it again as another person.

### 3.8 Local connectors: a program on the customer's machine (WS-LNK, task 3653, APPROVED)

> **Status: ✅ APPROVED, 2026-11-25 (D232).** The owner approved this section on task 3653 (answer `ccf82fd8`): "Approve §3.8, start TLY-2 first". It is the contract now. The epic is task 3654.

> TLY-2, the program's side, goes first, against a fake link. Each LNK slice of the Metorite side still asks the owner for its own "Approve the merge". The open points of §3.8.12 stay open.

> **Verified against code** on 2026-11-17 at `origin/main` `1128df4ed`. The owner's approval is D232 (`work_plan.md` §3).

#### 3.8.1 The ask and the rule

| Who | Words |
|---|---|
| The owner, 2026-11-17 | "this should be done in a way in which this particular connector, whatever we are building, can be set up inside any of our customers' Tally servers as well. They can connect their own Tally to Metorite. Basically, every customer sets up their own personal Tally MCP server by downloading this program and setting it up with their company domain" |
| The owner's standing rule, 2026-10-01 | A customer never does technical setup: no app registration, no keys, no Integrations plumbing |

So a customer does not run a tunnel, own a DNS zone, open a port or edit a file. The customer does three things. They install one program, type the company domain, and ask their Metorite admin to approve the code that the program shows. Metorite does the rest. LNK-Q1 (§10) settles what "company domain" means.

**The reading.** The program is an MCP server on the customer's machine, and it reads Tally there. Metorite cannot reach that machine, and the customer opens nothing. So the program opens an outbound HTTPS link to Metorite, and Metorite sends each MCP call down that link.

#### 3.8.2 What exists (code survey, 2026-11-17)

| Fact | Evidence |
|---|---|
| One registry. An MCP server is one org row with the provider `mcp:<name>`. Its plain fields sit in `provider_meta`: the url, the label, the transport, the agent scope and the two switches | `245_integration_connections.sql:40-68`, `connections/mcp.py:6-10` and `_meta` |
| A header value is Fernet ciphertext in `credentials_encrypted` | `connections/store.py:12-15`, `save_headers`, `_seal_headers` (`:565`) |
| The routes sit under `/integrations/mcp`. A register needs `integrations:manage` | `routes/integrations.py:2082-2312`, `:2112-2115` |
| A run uses a server only with `integrations:use:mcp-<name>`. Every seeded role except `guest` holds `integrations:use:*`. A per-member deny is an override row, and a write of one needs `admin:access:manage` | `acb_auth/permissions.py:28-46`, `:171`, `:241-247`, `decide` (`:423-442`). `PUT /admin/members/{email}/overrides` in `routes/admin/members.py`. The guest role: `200_provision_org_roles_tenancy.sql:142-144` |
| The transports are `http-sse` and `http` only, in three lists. The register refuses stdio (D85) | `routes/integrations.py:2141`, `connections/mcp.py:93`, `_tool_injection.py:4845` |
| The chat client is MAF `MCPStreamableHTTPTool` over an injected `httpx.AsyncClient`. It gives 10 s to connect and list, and 30 s to one call. It takes 128 tools and 8 servers at most. Above 12 tools or 1,500 tokens, the tools load on demand | `orchestrator/_maf_mcp.py:134-158`, `:674-676`, `:873` |
| `check_mcp_url` allows `https` on 443 to a global address only. It pins the DNS answer and refuses each 3xx. So an MCP row cannot point at an internal relay by URL | `gateway/outbound_guard.py:304-320`, `acb_common/public_address.py` (`is_local_address`, `:52`) |
| `_PinnedTransport` is a custom `httpx` transport. It is the precedent for a new transport | `outbound_guard.py:398` |
| Studio builds its own MCP client from the URL. It checks the grant in `_may_use` through `has_permission` | `connections/mcp_tools.py:349-350`, `:982` |
| Only native MAF agents get MCP servers, and only in an org on `MAF_MCP_ORGS` (`*` on production). An untrusted-input agent and a run that holds the email pack get none | `_tool_injection.py:4925-4942` |
| A batch, scheduled, webhook or headless run gets no MCP server at all, and a delegated run gets none either. A run that still reaches the injection with no member gets no per-member filter. `_integration_authorizer` returns `None`, and `_maf_servers` then keeps every server | `orchestrator/executor.py:6346-6351` (`_batch_thread`), `:7705-7711` (the stream path), `:5518`, `_maf_mcp.py:38-39`, `_tool_injection.py:5081` |
| The gateway runs one uvicorn process. A deploy drains runs for up to 60 s, then cuts open streams. D7 keeps the gateway SSE-only | `deploy/hostinger/acb-gateway.service:24`, `gateway-drain.sh:18`, `note_taker_app.md:254, 407` |
| The agent edge is the door for outside programs. It is its own unit, one uvicorn process. It checks a hashed bearer and takes the tenant from the token row only. It already uses the tenant Redis wrapper, and it calls the gateway with an HMAC signature | `acb-agent-edge.service:45`, `agent_edge/bearer.py:29-31, 128-164`, `agent_edge/idempotency.py:66`, `acb_auth/agent_signing.py` |
| The tenant Redis wrapper cannot build a key with no tenant, by design. A ratchet fails a new direct Redis client. The wrapper has `rpush`, `getdel`, `xadd`, `xread` with a block, `xreadgroup`, `publish` and `subscribe`, and no `blpop`. Its blocking pool holds 16 connections in each process | `acb_common/tenant_redis.py:178`, `:230-243`, `:777`, `tests/unit/test_tenant_redis.py:702` |
| Cross-worker routing exists for the chat stop. `dispatch_control` publishes, waits for an ack and tries once more. It uses raw keys, and the ratchet lists it as a follow-up | `orchestrator/stream_relay.py:991-992`, `:1111` |
| A box token is `mbt_` and 43 characters, 256 random bits. Metorite stores only its SHA-256. A member mints it for the tenant of their own session | `acb_auth/agent_tokens.py:206-212`, `:2131`, `external_agent_access.md` §10.9 |
| A token lookup runs with no tenant bound, and it returns the org. `agent_token` is RLS-exempt for that reason. It has a foreign key to `agent_connection`, which names a member, and a kind check of `access`, `refresh` or `code`. A box token is kind `access`. Its type sits in `agent_connection.credential`, which allows `oauth` and `box` only | `agent_tokens.py:1942`, `248_agent_access_tokens.sql:148-181`, `348_agent_connection_revoke_reason.sql:72` |
| A short code from before the tenant is known has a precedent. The sign-in code table is RLS-exempt. It stores a hash of the code with a server secret, and its rate limits count its own rows | migration 186 (`auth_email_otp_token`), `scripts/gen_tenant_migration.py:86`, `acb_auth/email_otp.py` |
| The phone link code is 10 characters, lives 15 minutes, and Metorite stores only its hash. No device flow (RFC 8628) exists | migration 235 (`whatsapp_member_links`), `external_agent_access.md:1678` |
| `organization.domain` is plain text that nothing verifies. More than one org can claim one domain | `acb_auth/access.py:344-370` |
| No rule covers customer accounting data that goes to a model vendor. D126 covers issue reports only. The router chain holds more than one vendor | `work_plan.md` §3 |
| Studio names a connection as `"mcp:tally"` | `ai_studio.md:695` (I1, I8) |
| MCP-NC1 (PR 2530) is merged. A new server has Always confirm off, and a tool in a member's run asks no card | §3.6.9, `agent_architecture.md` §12.2 WS-8c item 33 |

#### 3.8.3 The shape: one more MCP row

**A local connector is an `mcp:<name>` row of `integration_connections` with the transport `link`.** It has no URL. The chat, Studio (I1, I8), the grant `integrations:use:mcp-<name>`, the console, IN-10 and IN-12 then treat it as any MCP server. This section adds no registry, no loader and no grant word (`building_blocks.md` §6.1).

| Field | Value |
|---|---|
| `provider` | `mcp:<name>`, under the name rule `NAME_RE`. The admin names it at the approval, such as `tally` or `tally-pune` |
| `scope_kind`, `owning_app` | `org`, `integrations` |
| `provider_meta.transport` | `link`, a new value |
| `provider_meta.url` | None. A `link` row never has one |
| `provider_meta.connector` | The kind (`tally`), the machine name, the program version and the typed domain. Each is a display fact |
| The switches in `provider_meta` | `always_confirm`, `read_only_key`, `agent_scope` and `label`, as for any MCP server |
| `credentials_encrypted` | Empty. The link credential is a hash in the token store (§3.8.4) |
| The connector id | The id of the row. The program gets it at the pairing and logs it. It is not a secret |

**The one branch.** One function picks the client for a row. A URL row gets `outbound_guard.mcp_client`, as today. A `link` row gets an `httpx.AsyncClient` over a new link transport (§3.8.5). The chat (`_maf_mcp.py:873`) and Studio (`mcp_tools.py:350`) call that one function, so a third caller cannot skip the guard.

**What else changes.**

- One transport list accepts `link`: `_MAF_HTTP_TRANSPORTS` (`_tool_injection.py:4845`). Its three readers also test `srv.url`: `_mcp_may_attach` (`:4557`), `_oauth_wanted` (`:5029`) and `_maf_servers` (`:5078`). The first and the last accept a `link` row with no URL. `_oauth_wanted` keeps it out, because a `link` row has no OAuth.
- The other two lists stay as they are. So `POST /integrations/mcp` (`routes/integrations.py:2141`) and the store writer `save` (`connections/mcp.py:93`, `:908`) still refuse `link`.
- LNK-1 adds one store writer for a `link` row. Only an approval calls it.
- A name holds one row, and `save` replaces a row of the same name. So a URL register over a `link` row answers 409. An approval over a URL row answers 409 too.
- A run leaves out a `link` row whose program has not polled in the last 60 seconds. So a closed machine never costs a turn its 10-second connect budget.
- The program serves its MCP layer stateless, with JSON answers. So each request has one answer, and the link carries no stream. The link transport answers a GET for a server stream with 405 itself.

#### 3.8.4 Pairing (the shape of RFC 8628)

The customer types a code on another screen, so the pairing follows the device grant of RFC 8628.

1. The program sends `POST /connectors/pair/start` with the typed domain, the machine name, its version and the kind.
2. Metorite answers with a device code, a user code, an `expires_in` of 600 seconds and an `interval` of 5 seconds.
3. The program keeps the device code private. It shows the user code, and the customer sends it to their admin.
4. An admin with `integrations:manage` types the code in the console.
5. The approval screen shows the machine, the kind, the version, the typed domain, the source address and the start time.
6. The admin names the connector, picks who may use it (§3.8.8) and accepts the data notice. Then the admin approves.
7. The approval writes the registry row in the tenant of the admin's session.
8. The program polls `POST /connectors/pair/poll` with the device code. The first poll after the approval takes the pending record once.
9. That poll mints the link credential and saves its hash. It returns the credential, the connector id and the company name.
10. The program shows that company name on its setup page. It serves no call until the customer confirms the name.
11. If the customer refuses the name, the program calls `/connectors/link/revoke` and deletes the credential.
12. Before the approval, a poll gets `authorization_pending`. A poll too soon gets `slow_down`, and a poll after 10 minutes gets `expired_token`.

**Why the customer confirms.** The approving org gets read access to the books. Anybody with `integrations:manage` in an org on the flag can type a code that reached them by mistake or by a trick. The confirmation lets the owner of the books refuse a company that they do not know (rule j).

**The codes.**

| Code | Shape | Life |
|---|---|---|
| Device code | 256 random bits. Metorite stores only its SHA-256 | 10 minutes |
| User code | 8 characters of Crockford base 32 (no `I`, `L`, `O` or `U`), shown as `XXXX-XXXX`. That is 40 bits. The form ignores case and the dash, reads `O` as `0`, and reads `I` or `L` as `1`. Metorite stores a hash of it with a server secret, as the sign-in code does. A plain SHA-256 of a 40-bit code is easy to reverse by trying each code | 10 minutes |
| Link credential | `mlc_` and 43 characters, 256 random bits, the shape of a box token. Metorite stores only its SHA-256, and the program sees it once | It ends after 90 days with no poll. An admin revokes it at any time |

**The rules of the pairing.**

- **The org comes only from the approving admin's session.** It never comes from the domain. Nothing verifies `organization.domain`, and two orgs can claim one domain.
- **The domain grants nothing.** When it differs from `organization.domain`, the approval screen says so and asks the admin to confirm again.
- **The start names no org.** Its answer has one shape for every domain, so a stranger cannot learn who uses Metorite.
- **Before the approval, the pending record holds no tenant data.** It holds the two hashes, the display fields, the typed domain and the source address. The approval adds the org id and the connection id only. Path B below is still an exception to R5: a table that the EXEMPT map records with its reason. The reason of `agent_token` is the model: a lookup before the tenant is known.
- **Rate limits.** The start allows 10 calls an hour from one source address. The approve form allows 5 wrong codes in 15 minutes for one org, and then it locks for 15 minutes. Across all orgs, more than 200 wrong codes in one hour pause the form for 15 minutes, and the operator gets an alert. The LNK-1 audit sets the numbers.
- **Single use.** The approval and the poll each take the record with one conditional update. A second try gets the wrong-code answer or `expired_token` (rule k).
- **Remote phishing (RFC 8628 §5.4).** A stranger can start a pairing and ask a real admin to type the code. The screen shows the machine, the source address and the domain warning. The default access is "Only these people", and rung 2 reaches no write tool. Rule (d) treats each answer as outside text. The customer also confirms the company name before the program serves a call (step 10).

**Where the pending record lives.** The plan asked for Redis with a 10-minute TTL under a platform namespace. The wrapper cannot build that key today (§3.8.2), and the start limit needs the same kind of key. So LNK-1 takes one of two paths, and its audit picks.

| Path | Cost | Precedent |
|---|---|---|
| **A. Redis.** One narrow builder in the wrapper, for a fixed set of platform namespaces under `cc:_platform:`. No org id can match it, because `_ORG_ID_RE` needs a letter or a digit first | A change to a SAFETY seam. `test_tenant_redis.py` must pin the set, so a new namespace fails the build | `cc:_unattributed:cost:<day>` (`acb_common/activity.py:78-81`) |
| **B. Postgres.** One RLS-exempt table, listed in `gen_tenant_migration.EXEMPT` with its reason. Its rows also count the rate limits, and a sweep deletes the old ones | One migration (R1, R6) and a sweep | Migration 186 and `acb_auth/email_otp.py` |

**Recommendation: B.** It has the whole precedent, the limits included, and it changes no SAFETY seam. Path A is right only if the audit shows that the sweep costs more than the change to the wrapper.

**The credential store.** The link credential follows the rule of `agent_tokens.lookup_token`. A lookup by hash runs with no tenant bound, and it returns the org and the connection. The caller binds that tenant before it reads anything else. `integration_connections` has FORCE RLS, so it cannot serve that lookup. So the credential also needs a table outside RLS, as `agent_token` is.

- **Reused:** `agent_tokens.hash_token` for the two 256-bit secrets, the shape of the lookup, and the refusals of `bearer.authenticate`. Those are 401 for a missing, long, unknown or revoked token, and 503 for a database outage. The user code takes the keyed hash above, because 40 bits are too few for a plain hash.
- **Not reused:** the gates for outside agents in `bearer.authenticate`. Those are `agent_access_orgs`, the box flag and the agent audience. The link checks its own flag, `LINK_CONNECTOR_ORGS`, and its own audience.
- **The table.** A box token shows the way. It is kind `access`, and its type sits in `agent_connection.credential` (migration 348). **Recommendation:** a new value of that column, such as `connector`, and no second token table. The connection then names the approving admin. So the LNK-1 audit checks that a member removal names the connector (IN-12) and does not cut it in silence. It also checks that the AI agents view shows it as a connector. If this does not fit, LNK-1 adds one RLS-exempt lookup table, or stops and asks (`building_blocks.md` §6.1 step 4).

#### 3.8.5 The link: HTTPS long poll, not a WebSocket

| Reason | Evidence |
|---|---|
| The gateway is one process | `acb-gateway.service:24` |
| A deploy cuts a held stream after the drain, and Caddy does not retry it. A poll ends in 25 s anyway, and the program polls again | `gateway-drain.sh:18` |
| Office proxies often block a WebSocket. A plain POST passes them | The Tally build brief of task 3655, §6.8 |
| D7 keeps the gateway SSE-only | `note_taker_app.md:407` |
| A request that waits in Redis outlives a restart of the door | §3.8.2 |

**The three routes.**

| Route | Who calls | What it does |
|---|---|---|
| `POST /connectors/link/poll` | The program, with the link credential | It carries "Tally up" or "Tally down" and the version. Metorite holds it up to 25 s and answers with the waiting MCP requests, or with none |
| `POST /connectors/link/reply` | The program, with the link credential | It carries the answer to one request, by its request id |
| `POST /connectors/link/revoke` | The program, at uninstall | It revokes the credential of the caller. An unknown credential gets 200, as RFC 7009 says |

**One call, step by step.**

1. A run calls a tool of a `link` server. The link transport writes the HTTP request to the queue of the connection.
2. The queue item holds a request id, the method, the allowed headers, the body and a deadline.
3. The held poll takes the item and answers the program.
4. The program runs the request against its own MCP layer, in the same process. Then it posts the reply.
5. The door checks that the request id belongs to the connection of the credential. It writes the reply under that id.
6. The transport reads the reply and returns it to the MCP client as an HTTP answer.
7. With no reply in 30 s, the transport raises a timeout. The call then fails as any MCP timeout fails.

Each key goes through the tenant Redis wrapper. The door binds the org of the credential row, and the gateway binds the org of the run. The reply cap equals the answer budget of a URL server (`outbound_guard.AnswerBudget`). The door drops a reply that comes late, and an item whose deadline has passed. The link transport refuses a 3xx answer and an answer for another origin, as `_PinnedTransport` does.

The MCP client keeps speaking Streamable HTTP. Only its transport changes. So `MCPStreamableHTTPTool`, the card, the hints, Always confirm and the read-only key work as they work for a URL server.

**Capacity.** A held poll is one asyncio task and one open request on one process. A held `xread` with a block also holds one connection of the blocking pool for its whole wait. That pool holds 16 connections in each process (`tenant_redis.py:777`), so the plain design serves at most 16 connectors in a process. LNK-2 must not ship that limit. Its audit picks one of these:

- Short reads with no block, in a loop of about 250 ms.
- One blocking reader in each process for many connectors. It needs a wrapper change, because a key of one tenant fails while another tenant is bound (`TenantMismatch`).
- A separate pool for the link, with a measured ceiling.

The gateway side waits too. If its wait for a reply blocks in Redis, it takes a connection of the same pool of 16 that the live activity feed uses.

The audit measures each choice at 50 and at 500 connectors on one process. It records the Redis connections held on each side and the Redis commands each second. It records the memory of one held poll and the time that the link adds to a call, at p50 and p95. It times the three round trips of a connect (`initialize`, the initialized notice and `tools/list`) against the 10-second budget. It also records the open connections at Caddy, and how long the programs take to come back after a restart of the door.

**The door.** The recommendation is the agent edge. It is the door for outside programs, with the hashed-bearer check and the tenant-from-row rule. It already reaches the tenant Redis wrapper. It is its own unit, so held polls do not load the one gateway process, and a gateway deploy does not cut them. If the audit refuses the edge, a gateway route serves the link with the same lookup.

The LNK-2 audit decides with seven facts:

1. The public host, the Caddy route for `/connectors/*`, and each timeout on that path for a 25 s hold.
2. What a deploy does to the edge unit, and how soon the programs come back.
3. Whether the edge can write the health state with no import of the gateway. Its `pyproject.toml` forbids that import.
4. Whether held polls slow the MCP traffic of outside agents on the same process.
5. Whether the door sees the real source address behind Caddy. The start limit and the approval screen use it.
6. Where the reply cap lives. The edge cannot import `outbound_guard.AnswerBudget`, because its `pyproject.toml` forbids an import of the gateway.
7. How the link transport refuses a 3xx answer and another origin, with no URL to pin.

The pairing start and the pairing poll use the same door, because they come before the tenant is known. The approval is a console route of the gateway, in the admin's session.

#### 3.8.6 Health and revoke

The door writes the last poll time and the Tally state to a tenant Redis key on each poll. It writes the registry row (`last_sync_at`, `last_error`) only when the state changes. The read model reads the key, and a missing key reads as "no poll". This section adds no status string. The status check allows four values: the three of migration 245 (`245_integration_connections.sql:61-63`) and `needs_reset`, which migration 252 added.

| What the door saw | Stored status (`store.py:63-69`) | The read model | The state of `building_blocks.md` §4.4 | The admin sees |
|---|---|---|---|---|
| A poll in the last 60 seconds, and Tally up | `connected` | `connected` | connected | "Connected. Last call 2 minutes ago." |
| A poll in the last 60 seconds, and Tally down | `connected` | `error`, with the code `link_source_down` | failing | "TallyPrime is closed on OFFICE-PC." With a local copy (§3.8.13): "Reads use the copy of 14:05." |
| No poll for 60 seconds. A run then leaves the server out | `connected` | `error`, with the code `link_no_poll` | failing | "The program on OFFICE-PC has not called for 12 minutes." |
| An admin approved, and the program has not polled the link yet | `needs_reconnect` | `needs_reconnect` | needs consent | "Waiting for OFFICE-PC. The customer confirms there." With no credential taken after 10 minutes: "Pair this machine again." |
| The credential is revoked | `needs_reconnect` | `needs_reconnect` | needs consent | "Pair this machine again." |
| An admin pressed Disconnect | `disconnected` | `disconnected` | none | The row, with no tools |

"Quiet" in `company_context.md` §6.2 means a source with no new records. A link carries no records, so it never reads as quiet.

**Revoke.**

- Disconnect revokes the credential and sets `disconnected`. The next poll gets 401, and the program shows "Disconnected by your admin".
- A delete removes the row and revokes the credential.
- The door checks the revoke again before it answers a held poll. So a poll that waits during a Disconnect gets 401 and no request.
- Disconnect also deletes the queue of the connection.
- A request that waits when the credential ends gets no reply. It fails at its 30 s deadline.
- An uninstall calls `/connectors/link/revoke` while the link is up.

#### 3.8.7 Rules

Each rule names its fence, or says advisory. Each fence file is new, except where the row says "existing".

| # | Rule | Fence | Slice |
|---|---|---|---|
| a | A `link` row never takes a URL. A URL row never skips `check_mcp_url`. `POST /integrations/mcp` and the store writer `save` refuse `link`. Only the approval writes a `link` row. A URL register over a `link` row of the same name answers 409, and so does an approval over a URL row | `test_connector_link_shape.py` | LNK-1, LNK-3 |
| b | A link credential reaches only the queue of its own connection. A reply with the request id of another connection gets 404 | `test_connector_link_r8.py`: R8, two orgs, the app role with NOBYPASSRLS | LNK-2 |
| c | A run with no member gets no `link` tool. Today a batch, scheduled, webhook or headless run gets no MCP server at all, and a delegated run gets none either (`executor.py:6346-6351`). Rule c covers a run that still reaches the injection with no member, such as a `system:` principal on the stream path (`executor.py:7705-7711`). There `_integration_authorizer` returns `None` (`:5518`), and no grant filter runs. The LNK-3 audit lists each kind of run that reaches the injection, and its member. **This blocks rung 2.** Without it, "Only these people" is false for such a run | `test_connector_link_injection.py` | LNK-3 |
| d | LNK-3 frames each link answer and marks the session. **This blocks rung 2.** Today no MCP answer goes through `acb_skills.untrusted.frame_untrusted` (`untrusted.py:400`), and none sets the session mark. `mark_untrusted_answer` (`egress.py:341`) covers the answers of untrusted agents only. After MCP-NC1, text in a ledger narration could then drive a write of another server with no card. With the mark, each later MCP call of the session asks a card, a read of the same connector too. A URL server has the same gap, and that is a finding for the board | `test_connector_link_injection.py` | LNK-3 |
| e | A write connector (rung 3) has Always confirm on, and the approval sets it. Since MCP-NC1, a new server has it off. `PATCH /integrations/mcp/{name}` (`routes/integrations.py:2208`) cannot turn it off for a write connector | `test_connector_link_shape.py` | Rung 3, task 3664 |
| f | A run that holds the email pack gets no MCP server, so it gets no `link` tool either. This is a stated limit, and this section does not change it | `test_pack_guards.py` (existing) | none |
| g | "Only these people" also holds for a member who joins later (§3.8.8). It binds both grant checks: the chat (`is_authorized` in `_maf_servers`) and Studio (`mcp_tools._may_use`, `:982`, through `has_permission`, which `integrations:use:*` passes) | `test_connector_link_access_r8.py` | LNK-3 |
| h | Before the approval, the pending record holds no tenant data. The approval adds the org id and the connection id only. The start answer names no org and has one shape for every domain | `test_connector_pairing.py` | LNK-1 |
| i | The flag `LINK_CONNECTOR_ORGS` is empty by default. For an org off the flag, the approve route answers 404 and the door refuses the credential | `test_connector_pairing.py`, `test_connector_link_door.py` | LNK-1, LNK-2 |
| j | The program serves no call before the customer confirms the company name that the approval returned. A refusal calls `/connectors/link/revoke` | A test of the program in TLY-2 | TLY-2 |
| k | The approval and the poll each take the pending record with one conditional update. A second try gets the wrong-code answer or `expired_token` | `test_connector_pairing_r8.py` | LNK-1 |

#### 3.8.8 What the customer's admin decides

The approval screen asks three things. The program cannot decide them.

| Choice | Default | Notes |
|---|---|---|
| **Who may use it** | "Only these people", offered first, with the approving admin chosen | The grant stays `integrations:use:mcp-<name>`. A role holds `integrations:use:*`, so a deny for each other member leaves a later joiner open (rule g). **A candidate fix, not verified:** LNK-3 marks the row, and the injection accepts only an exact allow override for that server. A new member holds none. `decide()` already tells an exact override from a role grant: it returns `allow-override` or `role` (`permissions.py:423-442`). The open point is the shared session, which folds the access of each member into one yes or no. The candidate binds both grant checks of rule g. A write of an override needs `admin:access:manage`. An approver with `integrations:manage` only cannot pick people. Then the row stays closed to every member, and the screen names an admin who can open it. ⚠️ §3.9 (task 3985) replaces this candidate. LNK-3 uses the rule of §3.9 |
| **The data notice** | The admin must accept it to approve | The words are LNK-Q2 |
| **Read only** | Fixed in rung 2: `read_only_key` on, Always confirm off | Writes need rung 3, a second connector and Always confirm on |

**Where this sits in the console.** The build brief names "Admin → Integrations → Connectors". No view has that name. The console has the views Connections, API keys, Plugins, Skills and AI agents (§3.5, §7.2 IN-2). LNK-4 adds "Pair a local connector" to the Add menu of Connections, and the row joins the one list. It adds no view.

#### 3.8.9 The ladder (D211)

| Rung | The demo | Slices | Metorite code |
|---|---|---|---|
| 0 | None open | None | None |
| 1 | Our office, through a tunnel. A member asks the chat for a ledger balance, and the answer equals the Tally screen. Demo task 3655 | TLY-1 (task 3656) | None. The program registers as a URL server |
| 2 | Any customer, through the link. A second company installs the program and types its domain. Its admin approves the code, and its chat reads its own Tally. Demo task 3657 | LNK-0 (task 3653, this section), LNK-1 (task 3658), LNK-2 (task 3659), LNK-3 (task 3660), LNK-4 (task 3661), TLY-2 (task 3663). LNK-5 (task 3662) is off the demo path | Yes |
| 2a | The program keeps working when TallyPrime closes. A member asks for a ledger balance after TallyPrime closes, and the answer comes from the local copy with its age (§3.8.13). Demo task 3770 | TLY-4 (task 3769), TLY-3 (task 3768) | No Metorite code, except the age in the console |
| 3, Later | Writes. A member asks for a sales voucher, approves the card, and the voucher shows in Tally | Task 3664 | Yes |
| Later | Other local systems, such as Busy or a local SQL database, reuse the link and the setup screen. A sync of Tally data into Metorite needs a spec of its own | None yet | Yes |

Rung 1 already uses the product program. Only its transport differs from rung 2 (D211 rule 2). An unsigned or OV-signed test build serves the demo of rung 2. The signed build of LNK-5 gates the general release, not the demo.

#### 3.8.10 Changes to the draft contract of the build brief

The local build brief of task 3655 (version 2) holds a draft contract in its §6.8. The owner approved this section (D232), so it is the contract, and it wins where the two differ.

| The brief says | This section says | Why |
|---|---|---|
| A user code such as `KMTR-4821` | 8 characters of Crockford base 32, shown as `XXXX-XXXX` | `KMTR-4821` holds about 32 bits. The plan asks for 40 |
| §3.1: Always confirm is on for a new server | It is off since MCP-NC1 | PR 2530 is merged |
| §3.2: PR 2530 is open | It is merged | `d99180486` on `main` |
| "Admin → Integrations → Connectors" | The Add menu of Connections | No Connectors view exists (§3.8.8) |
| The poll returns a connector id and a credential | It also returns the company name, for step 4 of the setup screen. The credential shows once | The setup screen names the company only after the approval |
| Setup step 3 only shows the company name | The program serves no call until the customer confirms that name. A refusal revokes the credential | An admin of another org can approve a code (§3.8.4, rule j) |
| The Tally state goes up "each minute" | It goes up on each poll | A poll already runs at least every 25 s |
| The consent names "its AI model provider" | The words wait for LNK-Q2 | The router chain holds more than one vendor |
| `https://<metorite>/connectors/link` | The host waits for the LNK-2 audit. The paths stay. `/connectors/pair/start`, `/connectors/pair/poll` and `/connectors/link/revoke` are new | §3.8.5 |

#### 3.8.11 Decisions that stay closed

- **D85.** Metorite runs no stdio server and no customer command. The program runs on the customer's machine.
- **D7.** The gateway stays SSE-only. D7 binds the gateway, and the recommended door is the edge. On either door, the link uses plain HTTPS requests and no WebSocket.
- **§1.3.** A local connector is an MCP server of the org, which D96.2 puts in the console. It is not a marketplace, and it is not a member connection.

#### 3.8.12 What this section could not settle

| Open point | Who settles it | Recommendation |
|---|---|---|
| Where the pending pairing lives | The LNK-1 audit | Path B, Postgres (§3.8.4) |
| The credential store | The LNK-1 audit | A new value of `agent_connection.credential`, and no second token table (§3.8.4) |
| The door: the edge or a gateway route | The LNK-2 audit | The edge (§3.8.5) |
| The queue primitive and the capacity ceiling | The LNK-2 audit | Pick by the measures of §3.8.5 |
| Whether the frame and the session mark of rule d also bind a URL server | The board | A finding of its own. This section binds the link only |
| Whether "Only these people" works for a later joiner | Closed by §3.9 | The rule of §3.9 adds a deny at resolve time, and the shared session keeps a deny |
| The questions LNK-Q1 to LNK-Q9 | The owner | §10 |

#### 3.8.13 The program on the customer's machine (owner, 2026-11-18)

The owner asked two things on 2026-11-18, in chat. The words are the owner's, so they sit in a block that the linter does not read.

```text
We need to have some sort of Electron app or some sort of persistent app,
right, for the Tally connector?

Maybe this particular Metorite connector can keep a local copy of the Tally
backup even if Tally is off, and can specify the age of the Tally backups so
that we can sync periodically.
```

**The shape: a Windows service, not a browser tab and not Electron.**

- The installer of LNK-5 installs the program as a Windows service. The service starts with Windows, before anyone signs in, and restarts itself after a crash.
- The program has no window. A closed browser or a signed-out user does not stop it.
- The plan uses no Electron. Electron runs only while a user is signed in, and it carries a whole browser.
- The program runs on the same machine as TallyPrime. Port 9000 of Tally has no password, so a firewall rule keeps it to that machine (build brief §5.2).
- The program stores its link credential with Windows DPAPI at machine scope. No credential is in plain text on disk.
- The program only calls out (§3.8.5). The office opens no port and changes no router.

**The setup page.** The program serves it at `http://127.0.0.1:8765/setup`, and the installer opens it once. It has four steps: find Tally, choose the companies, pair (§3.8.4), and done.

- The page binds to `127.0.0.1` only.
- Each request carries a random secret that the installer made, and the `Origin` of the page. The page refuses every other request, because any web page in the browser can call `127.0.0.1`.
- The page never shows a stored secret again.

**Keep Tally reachable.** TallyPrime answers on port 9000 only while it runs with the company open. TallyPrime is a desktop program, and it cannot run as a service.

1. The setup page offers two settings, and the customer turns each one on. The first starts TallyPrime at sign-in with the chosen companies loaded. The second keeps the machine awake.
2. The program reports "Tally up" or "Tally down" on each poll (§3.8.6). The console then says "TallyPrime is closed on OFFICE-PC", and the chat says that Tally is closed. It never fails in silence.
3. An office that runs TallyPrime on an always-on server, or a hosted Tally, installs the program there.

**The tray icon (TLY-3).** A small icon in the Windows tray shows one of three states: Connected, Tally closed or Not connected. A click opens the setup page. The icon is a separate small program that reads the state of the service, and the service does not need it. The owner may drop this slice.

**The local copy (TLY-4).** The program does not read the backup file of Tally, because only a running TallyPrime reads that file. The program keeps its own readable copy.

1. **What it copies.** It copies the read data of the allowed companies only. That is the masters (groups, ledgers and stock items) and the vouchers of this financial year and the last one. It copies the fields that the read tools return, and no more.
2. **When.** While TallyPrime is open, at the interval that the customer's admin sets (LNK-Q9). After the first full copy, it copies only the records that changed, by the change number of each record (`AlterID`).
3. **How a read uses it.** With Tally up, a read goes to Tally. With Tally down, a read answers from the copy. Each answer from the copy names its age, for example "From the copy of 14:05 today. TallyPrime is closed." The chat repeats the age to the member.
4. **The maximum age.** The admin sets it (LNK-Q9). A copy older than that answers nothing, and the chat says how old the copy is.
5. **Writes.** A write never uses the copy. Writes are rung 3 (task 3664).
6. **Protection.** The program encrypts the copy with DPAPI at machine scope. It deletes the copy of a company that leaves the allow-list. It deletes the whole copy at Disconnect and at uninstall.
7. **Health.** Each poll also carries the time of the last good copy. The console then shows "TallyPrime is closed on OFFICE-PC. Reads use the copy of 14:05."
8. **Where it stays.** The copy stays on the customer's machine, so nothing answers while that machine is off. A copy inside Metorite would answer with the machine off, but the books then leave the customer's machine. LNK-Q8 asks the owner.

#### 3.8.14 The program's wire format (TLY-2a, for the LNK-2 audit to confirm)

TLY-2a builds the program's side first, against a fake link (`tests/unit/test_tally_bridge_link.py`). So the program fixes the bodies of the five routes. LNK-1 and LNK-2 build the Metorite side to these bodies, or they change both sides in one pull request. Each body is JSON. The program follows no redirect.

| Route | Auth | The program sends | Metorite answers |
|---|---|---|---|
| `POST /connectors/pair/start` | none | `domain`, `machine`, `version`, `kind` (`tally`) | 200: `device_code` (32 characters or more), `user_code` (8 characters of Crockford base 32, a dash allowed), `expires_in`, `interval` |
| `POST /connectors/pair/poll` | none | `device_code` | 200: `credential` (`mlc_` and 43 characters), `connector_id`, `company_name`. 400: `error` is `authorization_pending`, `slow_down`, `expired_token` or `access_denied` |
| `POST /connectors/link/poll` | `Bearer <credential>` | `tally` (`up` or `down`), `version`, `copy_at` (the time of the oldest last good copy of the allowed companies, ISO 8601, or `null`) | 200: `requests`, a list of at most 32 items. Each item has `id`, `method`, `headers`, `body` (text) and `deadline_s`, the seconds left at hand-out (at most 30). 401 with `{"error": "revoked"}` when the credential is revoked |
| `POST /connectors/link/reply` | `Bearer <credential>` | `id`, `status`, `headers`, `body` | 200. 404 for an id of another connection or a late reply. The program drops a 404 |
| `POST /connectors/link/revoke` | `Bearer <credential>` | nothing | 200, also for an unknown credential |

**401 means revoked, and nothing else.** Only a 401 with the body `{"error": "revoked"}` makes the program delete its credential. The door answers a pause of the org (rule i), a closed gate or an error of the lookup with 403 or 503. A 401 with another body gets the same answer. The program then keeps its credential and tries again later.

**What the program does with a request.**

- It runs the request in its own process, on `/mcp` only, with at most 8 requests at once. It polls again at once, so a slow request does not hold up a connect.
- Only `POST` passes. The MCP layer runs stateless with JSON answers, so it needs no `GET` stream and no `DELETE`.
- Only the MCP headers pass, in each direction: `content-type`, `accept`, `mcp-session-id`, `mcp-protocol-version` and `last-event-id`.
- A body over 1,000,000 bytes, a redirect or a failure of the MCP layer gets an error reply with the same id.
- A request with no time left gets no reply.

**The rest of the behaviour.**

- A poll fails when the network fails or Metorite answers 5xx. The program then waits 1 s, 2 s, 4 s and so on up to 60 s, with 20 % jitter. The first good poll resets the wait.
- A revoke deletes the credential on the machine and ends the loop. It deletes only a stored credential that is still the same, so a new pairing stays.
- The program reads its stored state before each poll. An `unlink` or a new pairing ends a running loop at once.
- An answer that comes back at once with no requests makes the program wait 1 s before the next poll.
- Each poll carries `copy_at` (TLY-2b). The console can then say "Reads use the copy of 14:05" (§3.8.13 point 7). It is `null` when the copy is off or empty.

### 3.9 Who can use an integration (task 3985, ACC-1 and ACC-2 MERGED, ACC-3 BUILT)

> **Status:** ✅ **ACC-1 MERGED** (task 3987, #2889), dark behind `INTEGRATION_ACCESS_ORGS`. The flag flip waits for the owner preview.

> ✅ **ACC-2 MERGED** (task 3988, #2900, and the follow-on #2909), the console control of §3.9.5, dark behind the same flag.

> 🔨 **ACC-3 BUILT** (2026-11-27, task 3989, branch `mt-fbef9dab-my-integrations`), the backend only: `GET /auth/me/integrations` (§3.9.4), dark behind the same flag. Another member builds the People tab. Review fix 1 (2026-11-27): an API row follows the grant of the call route (§3.9.4).

> ACC-4 is a 📝 PLAN. The owner approved teams on 2026-11-26 (ACC-Q2), and ACC-Q4 in §10 is open.

This section holds the one rule of "who may use a connection". §3.8.8 and rule g of §3.8.7 use it.

**Five facts of ACC-1 that the design did not state:**

1. **The write casts the service.** `jsonb_build_object` takes any type. So on asyncpg the SQL of §3.9.2 fails with "could not determine data type of parameter $1". So the write binds `CAST(:svc AS text)`. The bare removal passes on asyncpg, and it keeps the same cast.
2. **A stale rule.** A rule can outlive its MCP server. `GET` lists it with `listed: false`, and `PUT` with `everyone` clears it. Any other mode on it gets 422.
3. **The API route.** Each API connection holds `grant = admin` today, so only a holder of `integrations:manage` reaches it. Such a member passes each rule. So a rule changes the API route only when a member grant exists. The R8 suite opens one to prove the route reads the narrowed access.
4. **The admin floor.** Both routes also take the `/admin` floor `require_admin_user` (`admin:members:read`). The `admin` role holds all three rights.
5. **The preview lists.** `granted` and `denied` stay the member's own grants and overrides. A rule shows on its row of `integrations`, with the source `integration-rule`.

#### 3.9.1 What exists

| Fact | Evidence |
|---|---|
| An MCP server reaches a run, and an API connection answers, only with `integrations:use:<service>`. An MCP server is `mcp-<name>` and an API connection is `api-<template>`. A built-in tool reads its credential with no such check (§3.9.2) | `executor._integration_authorizer`, `_maf_mcp.mcp_service`, `routes/api_connections.py` |
| Each check ends in `EffectiveAccess.has`, so a deny pattern in the resolved access stops each of them | `acb_auth/permissions.py` (`decide`) |
| The roles `admin`, `manager` and `member` hold `integrations:use:*`. `guest` does not | migrations 131, 179 and 200 |
| The only control is one switch for all integrations on one member page | `settings/members/[email]/page.tsx` |
| A shared session folds each member's access. The fold keeps every deny | `EffectiveAccess.intersect`, `resolve_session_access` |
| A run with no member gets no integration filter | `_integration_authorizer` returns `None` |

#### 3.9.2 The model

**Rule.** An organization may hold one access rule for each service. The rule only narrows. It never grants.

**The services, a closed list.** Any other service gets 422.

| Kind | Service name | Source |
|---|---|---|
| MCP server of the organization | `mcp-<name>` | `connections/mcp.py` (`mcp:<name>`) |
| API connection | `api-<template>` | `connections/api/template.py` (`TEMPLATE_ID_PATTERN`) |

**Not in scope.** The app connections have no rule: the CRM Zoho sync, mailboxes, WhatsApp and Google. No check of `integrations:use` gates their app screens (`routes/crm/import_zoho.py`, `routes/crm/stage_metadata.py`). ACC-Q4 asks about the app connections.

**Not on the list: the built-in agent connectors** (the keys of `_REGISTRY` in `acb_skills/integrations.py`, ACC-1 fix round 2). A tool reads such a credential through `credential()`. That function reads the process environment first, and it checks no access (`web_tools._serpapi_search`, for example). So a rule on `serpapi`, `litellm` or `smtp` would stop nothing, and the console would show a control that does nothing. The PUT answers 422 for each key. ACC-Q4 asks about them too.

| Mode | Who may use the service |
|---|---|
| No rule (everyone) | Each member whose roles and overrides allow it. This is today, and it is the default |
| Chosen people | A member on the list of email addresses |
| Chosen teams (ACC-Q2, approved) | A member of a listed `group:<slug>`, or on the list of addresses |
| Admins only | A member who holds `integrations:manage`. It is a chosen rule with an empty list |

The word `org` is not stored, because it is the same as no rule.

1. A member who holds `integrations:manage` passes each rule. So an admin cannot lock out every admin.
2. A member who fails a rule gets a deny of the exact permission `integrations:use:<service>` in the resolved access. So no check changes, and no second check exists.
3. A rule never gives a permission. A `guest` on the list still has no integration. A per-member deny still wins.
4. An exact allow override of one member does not pass a rule. The list is the one place.
5. A member who joins later is not on a list. So the rule holds for a new member.
6. A group change takes effect at the next resolve.
7. A rule that the code cannot read means admins only for that service.

**Storage.** The rules sit in `organization.settings` under `integration_access`, one key for each service:

```json
{"integration_access": {"mcp-tally": {"subjects": ["a@x.com"], "set_by": "b@x.com", "set_at": "..."}}}
```

This needs no table and no migration. `organization` has no row security, so each statement names the organization id of the session (R5). No other code writes this key.

**The write.** One statement sets the key, and it creates the parent object when none exists. A plain `jsonb_set` on a missing parent changes nothing and reports success.

```sql
UPDATE organization SET settings = jsonb_set(settings, '{integration_access}',
       COALESCE(settings->'integration_access', '{}'::jsonb) || jsonb_build_object(:svc, CAST(:rule AS jsonb)))
 WHERE id = CAST(:org AS uuid)
```

The route checks that one row changed, and it reads the rule back. A removal deletes the key.

**The read.** The read is a separate statement, and never a part of `_ACCESS_SQL`. A failure in `_ACCESS_SQL` removes all access, and it can mark the access tables as missing until a restart. The statement returns the raw JSON and the member's group slugs. It joins `org_group` with `g.organization_id = u.organization_id`, because `org_group` has no row security. `org_group_member` has the same row policy as `user_role`, so the group read agrees with the role read.

- One pure function parses the rules and narrows the access. It never raises, because an exception in the run authorizer means "no filter".
- A failed read refuses `integrations:use:*` to each member who holds no `integrations:manage`. It changes no other permission.
- `resolve_access`, `resolve_access_in_org` and the access preview of one member use the same function. The preview shows the source `integration-rule`, and it lists each ruled service.

**Group writes.** A group member write needs only `admin:members:manage` today, and a chat tool calls the same route. So the team mode has two more rules (`groups_sessions_authority.md` §1 and §5):

1. A member write to a group that a rule names also needs `admin:access:manage`.
2. Nobody can delete a group that a rule names.

`org_group` rows scope and never authorize today. A team here decides who keeps a service. It never gives a permission. The owner accepted this on task 3985 (2026-11-26).

#### 3.9.3 The sites that obey it

Each site below reads the resolved access, so the rule reaches it with no change. The R8 suite proves each row.

| Site | Effect of a refusal |
|---|---|
| Chat MCP injection (`_maf_servers`, `is_authorized`) | The server is not attached |
| Integration credentials of a run (`acb_skills.integrations.build_integrations`). **Stored rules only** (ACC-Q4): it reads the keys of `_REGISTRY`, and the PUT accepts no rule on one | For a rule that a row already holds: no credential, and the agent gets a warning |
| API connection calls (`routes/api_connections.py`) | 404, as for an unknown name |
| MCP tool list refresh and live read (`connections/mcp_tools._may_use`) | Refused |
| A shared session | The fold denies when one member fails the rule. The second limit below names the exception |
| A delegated run | It acts as the member of its parent |

**Limits, stated.**

- A run with no member gets no filter, and every server attaches. The stream path adds MCP servers to each run, and the batch path adds them on a named chat thread. LNK-Q7 and rule c of §3.8.7 own this gap for link connectors.
- An org-visible room expands to every member. So one restriction removes the service from each such room.
- A room that expands past 200 members does not narrow. A failed read of the room members does not narrow either. In both cases, `resolve_session_access` falls back to the access of the actor, so the actor's own rule still applies. The access of the other members does not. This is the fold of `acb_auth/access.py` (`_MAX_PARTICIPANT_EXPANSION`), and ACC-1 did not change it.
- Replay redaction compares allowed sets only (`routes/chat.py` `_render_message`, `chat_fold._run_authority`). A rule adds a deny, and it removes no allow. So a member that a rule refuses later can still read the tool output of an earlier shared run. A member who was in the room at that run also reads it again.
- A key of `_REGISTRY`, such as `litellm` or `smtp`, takes no rule (§3.9.2). The resolver still narrows a rule that a row already holds, but a tool reads the credential with no check. `GET` shows such a rule with `listed: false`, and `PUT` with `everyone` clears it.
- A member purge takes the address off each rule, in the purge transaction (`members.purge_member_rows`, the admin door and the operator door). A rule that becomes empty stays, and it then means admins only, plus its teams. An org purge deletes the org row, and its rules go with it. No other path deletes an `app_user` row.
- A rule write, a member write to a group, a group delete and a member purge each lock the org row first. The rule write takes `FOR NO KEY UPDATE`, and the group writes take `FOR SHARE` (`_common.ORG_RULE_LOCKS`). So a rule check and its write never interleave with another of them.
- The room caps read allow overrides only (`routes/rooms.py`). So they do not name the rule as the cause.
- A rule controls use through the assistant and agents. It does not hide data that a sync already copied into Metorite, such as CRM records.
- A member-scoped connection (`scope_kind = 'member'`) has no rule. The `grant = admin` mark of an API connection (`api_connections.md` §2.3a) still applies on top.
- Tasks 2959 and 3351 are existing gaps, and this section does not close them.

**How fast.** The rule holds at the next run. The one gateway process holds the only access cache, and the orchestrator runs inside it. A write clears the cache. A run in flight keeps its access, and a retry keeps its credential names.

#### 3.9.4 Routes, audit and the flag

- `GET /admin/integration-access` lists each service of the closed list with its rule. `PUT /admin/integration-access/{service}` takes `{"mode": "everyone"|"chosen"|"admins", "groups": [], "people": []}`.
- Both need `integrations:manage` **and** `admin:access:manage`, because a rule is an access write.
- A group must exist in the organization, and a person must be a member of it. An unknown service, group or address, and the word `org`, get 422.
- Without either permission the answer is 403.
- Each write calls `record_admin_change` with the service, the mode and the subjects, and then `invalidate()` for all members.
- The flag `INTEGRATION_ACCESS_ORGS` is empty by default. For an organization off the flag, each route answers 404 and the console shows no control. The resolver applies a rule that exists, whatever the flag says. So a flag change never widens access.

**The self view (ACC-3).** `GET /auth/me/integrations` tells a member if they may use each integration, and why. `routes/admin/my_integrations.py` attaches it to `me_router`. It needs a signed-in member only, and no feature or admin right. It takes no parameter.

1. An organization off `INTEGRATION_ACCESS_ORGS` gets 404 before any session opens.
2. A caller with no address, or with access that is not active, gets 403.
3. `get_org_id` gives the organization. The member row is the caller's own `app_user` row, from `find_member` with the session's address. The route does not use `user.user_id`, because it is an identity id under `IDENTITY_CUTOVER`. No row gets 403.
4. A failed read of the closed list, of an API connection or of the rules gets 503. The route never sends a list with no reasons.

The answer is `{"services": [...], "source": "integration-rule"}`. Each row is exactly `{"service", "kind", "name", "allowed", "reason"}`, and the rows are in the order of `service`. `kind` is `mcp` or `api`, and `name` is the service without its prefix.

- The list starts as `closed_list` of `integration_access.py`, with no change. A stored rule of a service off the list does not show.
- An MCP row shows for each MCP server of the list. Its `allowed` is `user.access.has(integrations:use:<service>)`. It is the same resolver and the same cache that the chat's `_integration_authorizer` reads.
- An API row shows only when the call route can serve it. `API_CONNECTIONS_ORGS` must hold the organization, because each `/connections/api` route answers 404 when it does not. The organization must also hold the org-scope row of the template, with the status `connected` or `needs_reconnect`. The view reads the row with `api_connections.read_row` and `served`, the reads of the call route. So a template that the organization did not connect does not show.
- The `allowed` of an API row is `api_connections.granted` over the access of the session. It is the one grant of the call route: the use right, and then the grant mark of the row. The mark `grant = admin` asks for `integrations:manage` (D-API-3). So a plain member reads No on a connected API row today.
- The reason comes from `_common.member_rule_access`. It is the one step of roles, overrides and `narrow`, and the admin preview calls it too.
- When that step and `allowed` do not agree (the cache window of 60 seconds), `allowed` stays and the reason is `updating`. An API row compares `granted` over both accesses.
- A caller whose roles and allow overrides match no service of the closed list (a guest) gets `{"services": [], "source": ..., "blocked": "no-role"}`. The test reads the list before the API filter, so a guest is blocked also when no row is left. So a guest does not see the names of the MCP servers. A member with no row left gets `{"services": [], "source": ...}`.
- The answer never holds a mode, a team, a person, `set_by`, `set_at` or an address. That includes the caller's own team slugs.

| Allowed | Case | Reason |
|---|---|---|
| Yes | A rule names the service, and the member holds `integrations:manage` | `admin` |
| Yes | A rule names the service, and the member passes it | `on-rule` |
| Yes | No rule, and a role gives it | `role` |
| Yes | No rule, and an allow override gives it | `allowed-for-you` |
| No | The member row is not active | `inactive` |
| No | A rule refuses it, and the roles and overrides allow it | `rule` |
| No | The code cannot read the rules, and the roles and overrides allow it | `rules-unreadable` |
| No | A deny override | `denied-for-you` |
| No | No role gives it | `no-role` |
| No | An API row: the use right holds, and the grant mark of the row asks for `integrations:manage`, which the member does not hold | `admin-only` |
| Either | The cache and a fresh read do not agree yet | `updating` |

A No row follows the order of `members._explain_ruled`. When the code cannot read the rules at all, a holder of `integrations:manage` sees `admin` on each row.

`admin-only` comes after each other No. The call route reads the use right first and the grant mark second. So an inactive row, a rule, an unreadable rule, a deny override or a missing role keeps its own reason. A member who is on the list of a rule, and who holds no `integrations:manage`, reads `admin-only`.

**Limits of a row, stated.** An MCP row says the access of the member, and not the state of the server. A disabled MCP server (`disconnected`) still shows, and a `needs_reconnect` API row shows too. The chat does not attach a disabled server, and the call of a `needs_reconnect` row answers 409. The scope of an agent, `MAF_MCP_ORGS` and a changed API template are also outside the answer.

#### 3.9.5 The screens and the chat

- **Console (slice b).** Each row of Connections gets "Who can use", with the three modes, a group picker and a people picker. A row with no rule says "Everyone".
  - **Built (ACC-2).** The control sits under each MCP row and each API connection of the organization that the `GET` lists. No other row gets one. The screen calls a group a "team".
  - A `GET` that answers 404 or 403 draws no control and no error. Another failure draws one line with "Try again".
  - On a cold load, the list keeps its skeleton until the `GET` answers or fails, so no row grows under the pointer. This holds only when a row can take a rule. The wait ends 2 seconds after the list arrives (`ACCESS_WAIT_MS`). A later answer then adds the controls.
  - A stored rule that no row control covers sits under the list, in "Other stored rules". This holds when no filter is on. A rule with `listed: false` offers only "Set to Everyone", behind a confirm. A listed one offers "Change".
  - With `readable: false`, a banner says that only members with `integrations:manage` can use each integration. Each row then shows "Admins only" as text, with no control, because no save can repair the value.
  - "Chosen" with no team and no person warns before the save, and the button reads "Save as admins only".
  - A refused save keeps each choice and shows the words of the gateway.
  - The open editor never replaces a rule that the admin did not see. With no choice yet, it follows a newer rule. After a choice, it shows "This rule changed", and Save stays off until "Reload the rule".
  - Each save and each "Set to Everyone" reads the rule first, and writes nothing if it changed. The `PUT` has no version check, so a write between that read and the `PUT` is still replaced.
  - A stopped save moves the focus to "Reload the rule", or to the line that says why.
  - Fences: `app/integrations/lib/whoCanUse.test.ts`, `app/integrations/lib/accessSave.test.ts`, `app/integrations/components/WhoCanUse.test.tsx` and `app/integrations/components/accessWiring.test.ts`.
- **My access (slice c).** The People app tab "My access" lists the integrations of the organization for the member, each with "Yes" or "No" and the reason. It never shows another member's name or list.
  - **Backend built (ACC-3).** The tab reads `GET /auth/me/integrations` (§3.9.4). Another member builds the tab, and that work adds its own chat parity row.
  - The tab lists an API connection only when the organization connected it, and only with `API_CONNECTIONS_ORGS` on. A plain member reads "No" on it with the reason `admin-only`, because each API connection holds `grant = admin` today.
  - The answer is the member's solo access, and never the access of a room or a session. A shared room can still refuse a "Yes". A room of more than 200 members, or a failed read of a room, does not narrow. A delegated run acts as its parent, and a run with no member gets no filter. No field of the answer says these.
- **Chat (slice d).** An admin asks the assistant to change a rule, and a card confirms it. This waits for the owner decision of PAR-6b (task 3932).

---

## 4. Security and tenancy

1. Every table that holds a connection has FORCE RLS. If it does not, only a
   store that binds the tenant reads it (R5).
2. No tenant and no identity come from the request.
3. The gateway fetches a URL that a customer typed only through its one URL
   guard, `gateway/outbound_guard.py`. The guard refuses a private, loopback,
   link-local or metadata address, and it does not follow a redirect. Do not
   use `acb_skills/egress.py` here, because it classifies tools, not URLs.
   WS-8c adds the MCP client of the guard, which allows `https` on port 443
   only.
4. A write to a connection writes one audit row: who, what, when, which org.
5. The old routes that write the environment stop doing it in IN-0. They keep
   the per-org store write.

---

## 5. Shell manifest (R9)

| Field | Value |
|---|---|
| `href` | `/integrations` |
| `label` | Integrations |
| `feature` | none (each view gates itself), since D-EA-9 on 2026-10-14. `HREF_FEATURES` keeps `/integrations`, and `ALWAYS_ALLOWED` lists the exact path |
| `launch` | `live` since 2026-10-13, by owner decision |
| `team` | `admin` |
| `blurb` | "Every tool your company has connected" |
| `jobs` | `integrations.add_mcp`, `integrations.review` (a link to the list), and `connect-agent` of WS-55. The first two need `feature:integrations` |
| `needs` | `integrations`. A connection in `needs_reconnect` or `error`, for admins. After I8b-1b, the ask of a member for a connection, for admins (`ai_studio.md` §8.2) |
| `agent` | none |

---

## 6. Owner setup

| Act | Why it is the owner's | Slice |
|---|---|---|
| ✅ Confirm or overrule D96 | Product shape. The owner confirmed D96 on 2026-10-11. The owner chose "build the console after the CRM is in use". | Before IN-1 |
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
  ├─► IN-4 (needs IN-1 for the row, and WS-8c merged)
  └─► IN-6, IN-7 in any order
```

IN-0 comes first and alone, because it closes live holes.

### 7.2 Slices

| Id | What | Gate | Done when |
|---|---|---|---|
| **IN-0** ✅ merged 2026-10-11 (#859), (e) held behind H-244 | **Close the holes.** (a) Six routes stop writing `os.environ` and `.env`: configure, PUT and DELETE `/keys` keep the per-org store write. The GitHub device poll stores `github:token` for the org. connect-cli answers 410. The old OAuth authorize, callback and refresh are retired (410). A failed store write in configure answers 503. (b) Every writer requires `integrations:manage`. (c) The MCP list and delete filter on the caller's tenant, and the list returns key names only, never a `headers` or `env_vars` value. (d) `/mcp/test` and plugin install fetch only through `gateway/outbound_guard.py`. (e) **Held behind H-244 [OWNER]:** the `<<<SETUP:...>>>` path stops writing configure. After (a) it can write no env value, so holding it leaves no cross-tenant hole | 🟢 AGENT-SAFE for (a) to (d). ⚠️ It changes behaviour: a key set on the page stops reaching agents until IN-3. On prod the startup copy is already inert (9 orgs), so the cost is close to zero | A planted `os.environ` write in any of the six routes fails a fence. A member without `integrations:manage` gets 403 on each writer. Org B's MCP list shows none of org A's rows and no secret value. `/mcp/test` to `http://127.0.0.1` and to `http://169.254.169.254` is refused with no request sent. Each test red first |
| **IN-1** ✅ merged 2026-10-11 (#878) | **The read model.** `GET /integrations/connections` over the three sources of §3.2. Status per org, not per deployment. `_is_configured` answers False for an unknown service | 🟢 AGENT-SAFE. Needs CRM-Z2 for the table | Two orgs see only their own rows. No response holds a credential (a sentinel fence). A mailbox row shows owner and health and no message |
| **IN-2** ✅ merged 2026-10-11 (#880) | **The console.** It replaces the five tabs with one list of connections, filters by app and status, and opens a side panel with reconnect, disconnect, and a link to the owning app. MCP gets an "Add" form. The stale text goes (IN-D16). ⚠️ The MCP "Test" button sends `s.headers || {}`, and since IN-0 the list returns no headers. So a server that needs an auth header tests without it. The console must test with the stored headers | 🟢 AGENT-SAFE | Visual review passes. Every action reaches the store through the routes of IN-0 and IN-1. `AppTopBar` and the shared controls only ⚠️ **Built as:** `/integrations` opens with `AppTopBar` and a rail of four views: Connections, API keys, Plugins and Skills. The Email and MCP tabs are gone. The Add menu offers an MCP form and a link to API keys. Disconnect, Test and Add need `integrations:manage`, and the list needs `admin:members:read`. A 404 from the list route shows the API keys view with a notice. **Left:** the page still calls the MCP test by `detail.name`, so IN-1 must put the server name there. The `add-mcp` and `review-integrations` jobs are in the shell registry. The nav entry stays `preview` until the owner promotes it. The `stdio` transport is dropped on purpose, because a customer command must not run on our server (WS-8c allows https only). MCP edit is out of scope for v1. The stale text in `17_email_accounts.sql` and `mcp_plugin_integration.md` (IN-D16) is not touched. |
| **IN-3** (task 3176) | **Per-run credentials for the integration resolvers.** `acb_skills` resolvers read the per-run context, filled from the per-org store, and the environment stops winning | 🔴 §6 gate (f) | A run in org A gets org A's key and a run in org B gets org B's, on one process at one time (R8) |
| **IN-4** ✅ MERGED 2026-10-12 (#905), org scope only | **Move the org MCP servers into `integration_connections`, and repoint the WS-8c reader.** Owner direction, 2026-10-12: "get the organization-wide connectors done". **Non-goal:** a personal (member) MCP connection. That is a later ticket. **The key.** Each server is one org row: `provider = 'mcp:<name>'`, `owning_app = 'integrations'`, `scope_kind = 'org'`. The unique index `integration_connections_one_live_idx` (`245_integration_connections.sql:70`) includes the provider, so one org can hold many servers. The name rule of `POST /mcp` makes `mcp:<name>` unique, and no other provider starts with `mcp:`. **Encryption at rest.** The header map is a credential. `connections/store.py` seals it as `{"headers": {...}}` into `credentials_encrypted`, as Fernet ciphertext through `acb_llm.key_store` (`ACB_MASTER_KEY`). The url, label, description, transport and agent scope are not secret, so they go into `provider_meta`. `enabled` is the status, `connected` or `disconnected`. `env_vars` does not move, because Metorite runs no stdio server (D85). **One reader.** `connections/mcp.py` opens `tenant_session(org)` for each call. The org comes from the run binding or the caller's tenant, never from input (R5). The orchestrator and the routes read through it. A Copilot agent still gets no registry server. **Expand only (R6).** Writes go to the registry only. A read moves each old `mcp_servers` row of the org into the registry once, encrypted. A delete removes the old row too, so a removed server cannot come back. `mcp_servers` stays readable for one release. H-301 drops it. **H-300 (2026-10-12).** `provider_meta.always_confirm` holds the admin's "always confirm" switch. A new server gets `true`. A row with no key, and a moved row, read as `false` (`agent_architecture.md` §12.2 WS-8c, item 31). *Amended by MCP-NC1 (2026-11-16): a new server gets `false` (item 33).* No migration. **Paths at `713dc84ac`:** `connections/store.py` (`_seal` :232), `connections/mcp.py` (new), `_tool_injection._inject_mcp_servers` (:2139, `_MCP_ROWS_SQL` :2113), `routes/integrations.py` (:1808, :1821, :1862, :1885, :1959), `routes/integrations_connections.py` (`_registry_row` :137, `_mcp_rows` :224, the disconnect branch :496, the stored test :509-560) | 🟢 AGENT-SAFE | Rows A to H of §7.3 pass. The WS-8c fences pass against the new store |
| **IN-5** | **Retire the old writes.** Remove the env-write code, `_persist_tokens`, the dead OAuth route, and `configure_integrations`'s copy into the environment for customer keys. Move `COPILOT_CHAT_MODEL` out of the deployment `.env` (IN-D17) | 🔴 §6 gate (f) | `os.environ[` with a customer key appears nowhere outside the operator plane (fence) |
| **IN-6** | **Custom APIs and plugins.** Keep them with a reader and per-org keys, or retire them | 🔴 owner (Q2) | Per the answer |
| **IN-7** ✅ merged 2026-10-11 (#891) | **GitHub per org.** The device flow writes a member or org connection, not the deployment token. connect-cli goes. `GET /github/account` shows the org's own connection, never the operator's account (IN-D18). **Built as (2026-10-11):** The device poll saves an org connection (`github`, `integrations`) through `connections/store.py`. The login and the scopes sit in the row. `connections/github.py` is the one reader, `token_for_org(org)`. The agent config fetch reads it, and with no connection it sends no token. `GET /github/account` and `GET /test` read the org row only and run no `gh`. `DELETE /integrations/github` deletes the blob. GitHub cannot revoke a device-flow token without the client secret, so the answer says `revoked_at_github: false`. An old `provider_keys` `github:token` row moves into the store on the next read. That fallback read stays for one release. The slice adds no migration. The operator plane keeps `GITHUB_TOKEN`: the model list, the runtime check, the self-mutation PR, the LiteLLM route, the Copilot SDK runtime and the agent-repo clone in `loader.py`. A clone of a private customer repo waits for IN-3. A token that a member saves through configure or `PUT /keys` still lands in `provider_keys`, and the reader moves it. IN-5 retires that path. Fence: `tests/unit/test_github_per_org_r8.py`. | 🟢 AGENT-SAFE | Two orgs hold two tokens. The server's own `gh` token is never imported |
| **IN-8** 📝 PLAN 2026-10-12 (D98) | **MCP OAuth for an org connection.** An admin connects an MCP server that needs an OAuth consent. The connection acts as that admin at the provider, and the console and each card say so. Dark behind `MCP_OAUTH_ENABLED`, default off. The design, the provider table and the acceptance are in §3.6. It needs IN-4 merged. A member connection over OAuth is out of scope (§1.3) | See IN-8a to IN-8d | Rows O1 to O14 of §3.6.11 pass, each red first. No R8 suite skips |
| **IN-8a** 🔨 BUILT 2026-10-13, PR #931 | Discovery, CIMD and DCR, PKCE, the signed state, the callback and the store (§3.6.3 to §3.6.6), dark | 🟢 AGENT-SAFE | O1 to O6, O9 and O11 to O13 |
| **IN-8b** 🔨 BUILT 2026-10-14 | Refresh at run time, the 401 on connect, and `needs_reconnect` (§3.6.7), dark | 🟢 AGENT-SAFE | O7, O8 and O14 |
| **IN-8c** 🔨 BUILT 2026-10-15 | The console Connect button, the BFF route for `POST /integrations/mcp/{name}/oauth/start`, the dialog text, the acts-as label and the card line (§3.6.9), dark | 🟢 AGENT-SAFE | O10. Visual review passes |
| **IN-8d** | Pre-registered providers: GitHub, Slack and HubSpot (§3.6.2) | 🔴 OWNER-GATE. The owner creates each OAuth app, and its secret goes in the operator plane (D56) | The owner's consent with each provider stores an org connection in Fracktal |
| **GH-0 to GH-8** 📝 SPECIFIED 2026-10-18 | **GitHub through a Metorite-owned GitHub App.** `github_integration.md` owns the slices, the acceptance and the gates. The customer-made OAuth App client id path and its device flow retire (GH-7), and a pasted token stays as "Advanced". The GitHub OAuth app of IN-8d is a separate registration, for the GitHub MCP server | 🔴 OWNER-GATE: GH-0 (register the Apps) and the install in GH-8. The rest is 🟢 AGENT-SAFE | See `github_integration.md` §16 |
| **IN-9** (task 3172) | **The result card after a connect** (§3.7 rule 1). 🔨 **MODEL BUILT (2026-11-14, IN-9a): `acb_common/connector_result_card.py` makes the card and the chat line from the connector list. Left: the screen (IN-9b, UI gate) and the caller that passes the apps (IN-10) and the templates.** API-5 (task 3791, `api_connections.md` §2.12) draws the first screen of the card, for an API connection, dark: `ConnectResultCardView`. IN-9b reuses it for each other connect. What works now, then the templates, then "Build an app on <provider>" where it fits. In chat, Mira says the same after a connect | 🟢 AGENT-SAFE. After IN-11 | A mailbox connect shows no build offer. An MCP server that no app uses shows it. The card reads the connector list. Visual review in four contexts |
| **IN-10** (task 3173) | **Used by, and the disconnect preview** (§3.7 rule 2). The sheet lists the apps, workflows and skills that use a connection. A disconnect names them and asks once. The chat answers "what uses this connection?" The names of an app come from `package_host/resolve.connection_names` (`ai_studio.md` I1a). API-5 (task 3791) adds the used-by line and the disconnect words of an API connection, dark. Its list is empty, because no reader of an API connection exists before API-4 | 🟢 AGENT-SAFE. After `ai_studio.md` I1 | The sheet lists two apps and one workflow of a test connection. The disconnect dialog names them. A connection that nothing uses asks with no list |
| **IN-11** (task 3174) | **The connector list** (§3.7 rule 3). One curated file of the providers that a member can connect. Studio checks each name of `connections` against it, and `ai_studio.md` I1a checks the grammar only | 🟢 AGENT-SAFE. 🔨 **BUILT (2026-11-14). Studio I8a (task 3168) is the first caller.** Fence: `tests/unit/test_connector_list.py`. Readers: §3.7 rule 3. The manifest validator still checks the grammar only | A test holds a row for each provider with a connect flow, and an owning app or the console for each row. The console, Studio and the result card read the one file |
| **IN-12** (task 3175) | **A connection outlives its person** (§3.7 rule 4). The member removal names each org connection that acts as the leaver, with its apps | 🟢 AGENT-SAFE. HIGH tier | A removal of a member who owns one org connection lists it and its apps. After a reconnect, the row names the new person. R8: the list holds only the rows of the org |

### 7.3 IN-4 acceptance

Each test fails first, and then passes. The file is
`tests/unit/test_mcp_registry_r8.py`, unless the row names another file.

| Row | Acceptance |
|---|---|
| **A** | The WS-8c suites pass against the registry with no skip: `test_maf_mcp_injection`, `test_maf_mcp_org_scope_r8`, `test_mcp_outbound_pin`, `test_no_mcp_stdio`, `test_mcp_register_r8`, `test_mcp_servers_org_scope`, `test_maf_mcp_confirm` and `test_maf_mcp_lifecycle` |
| **B** | A run in org A gets only the servers of org A. The test is R8 with two orgs, and it runs as the app role, because the owner role bypasses RLS |
| **C** | Org A registers two MCP servers, and both reach the agent |
| **D** | A sentinel header value never shows in plain text. The test reads `credentials_encrypted`, `provider_meta`, each GET response, the read-model rows and the logs |
| **E** | Only `store.py` names the table. `test_crm_connection.py::test_only_the_store_names_the_connections_table` stays green |
| **F** | A read moves one old `mcp_servers` row into the registry once. A second read moves nothing, and the old row stays |
| **G** | `POST /integrations/mcp/{name}/test` sends the stored header, decrypted, through `outbound_guard` |
| **H** | The read model shows each MCP server once |

### 7.4 Local connector slices (WS-LNK)

The owner approved §3.8 on task 3653 (D232). A fence file is new, except where the row says "existing". TLY-2 ships in three parts:

- TLY-2a (task 3663): the link client, against a fake link (§3.8.14).
- TLY-2b (task 3968): the pair step of the setup page, and the copy time on each poll.
- TLY-2c (task 3969): the setup guide, `apps/connectors/tally_bridge/SETUP_GUIDE.md`.

```
LNK-0 ─► LNK-1 ─► LNK-2 ─┬─► LNK-3 ─┬─► TLY-2 ─► rung 2 demo (task 3657)
                         └─► LNK-4 ─┘
LNK-Q2 (owner) ─► LNK-4
TLY-1 (rung 1, no Metorite code)
LNK-5 (gates the general release, not the rung 2 demo)
TLY-2 ─► TLY-3 (the tray icon) and TLY-4 (the local copy, after LNK-Q8 and LNK-Q9)
```

LNK-2 needs LNK-1, because it checks the credential that LNK-1 mints. TLY-2 needs LNK-3 and LNK-4.

| Slice | Task | Scope | Done when | Fence | Tier | Needs |
|---|---|---|---|---|---|---|
| **LNK-0** | 3653 | This section, §3.8, the questions LNK-Q1 to LNK-Q9 and the INDEX row. Docs only | The owner approves §3.8 on task 3653 and answers LNK-Q1 and LNK-Q7 | `test_index_completeness.py`, `test_doc_names_resolve.py` (existing) | LOW | None |
| **LNK-1** | 3658 | Pairing (§3.8.4): start, poll and approve, the pending record, the codes, the rate limits and the lockout. The mint and the lookup of the credential. The registry row with the transport `link`, and the refusal of `link` on `POST /integrations/mcp`. The flag `LINK_CONNECTOR_ORGS` | A pairing that an admin of org A approves writes a row in A only. The start names no org. Five wrong codes lock the form. The credential shows once. A different domain shows the warning | `test_connector_pairing.py`, `test_connector_pairing_r8.py`, `test_connector_link_shape.py` | HIGH: identity, a credential, and maybe a migration | LNK-0 |
| **LNK-2** | 3659 | The door (§3.8.5, §3.8.6): poll, reply and revoke, the reused lookup, the queue keys, the health key and the row write on a change of state. The audit of the door, the queue primitive and the capacity (§3.8.5) | A credential of org A cannot take or answer a request of org B. A revoked credential gets 401 at its next poll. The door drops a reply after its deadline. The audit records the measures and the seven facts of §3.8.5 | `test_connector_link_r8.py`, `test_connector_link_door.py` | HIGH: a credential, the tenant boundary and a concurrency path | LNK-1 |
| **LNK-3** | 3660 | The client branch (§3.8.3): the one client function, the link transport, the transport lists, the row filters and the Studio client. Rules c, d and g, with the check of rule g that the audit picks (§3.8.8) | A member asks the chat, and a tool call goes through the link to a test program and back inside 30 seconds. A run with no member gets no `link` tool. A later joiner gets no tool of an "Only these people" row. A link answer reaches the model framed, and the session is marked | `test_connector_link_transport.py`, `test_connector_link_injection.py`, `test_connector_link_access_r8.py` | HIGH: three services and the untrusted frame | LNK-1, LNK-2 |
| **LNK-4** | 3661 | The console (§3.8.6, §3.8.8): "Pair a local connector" in the Add menu, the code form, and the approval screen with the machine, the domain warning, who may use it and the data notice. The row states and Disconnect | Visual review passes in four contexts. The owner sees the preview and approves it | A vitest under `src/app/integrations` | STANDARD: UI, with an owner preview | LNK-2, which needs LNK-1. The owner's answer to LNK-Q2 |
| **LNK-5** | 3662 | Distribution: the signed installer, the update file and its signature check, and the address where Metorite serves them | A clean Windows 11 machine installs the program with no SmartScreen block. The program refuses an update with a bad signature | The tests of the program | STANDARD. Owner gate: the code-signing certificate (LNK-Q3). It gates the general release, not the rung 2 demo | LNK-0, LNK-Q3, LNK-Q4 |
| **TLY-1** | 3656 | Rung 1: the Tally program in address mode, through our tunnel, with read tools only. No Metorite code | The owner sees the demo of task 3655. The rung 1 tests of the build brief pass | The tests of the program | STANDARD | None |
| **TLY-2** | 3663 | Rung 2: the Tally program in link mode, as a Windows service with the setup page and the Tally checks of §3.8.13 | A second test org pairs, approves and reads its own Tally. After a restart of Windows with no user signed in, the service serves again. The program serves no call before the customer confirms the company name (rule j). A cut link comes back with no manual step. Disconnect stops the tools. The program ships a one-page setup guide (task 3663). A person outside the team follows it on a clean Windows machine and reaches a correct chat answer with no help. The guide asks for no router change and no open port | The tests of the program, with the test of rule j. Also `test_connector_link_contract.py`, if LNK-Q6 puts the program in this repo | STANDARD | LNK-3, LNK-4 |
| **TLY-3** | 3768 | The tray icon (§3.8.13): three states, and a click that opens the setup page | The icon shows each state within 10 seconds of a change. With the user signed out, the service still serves | The tests of the program | LOW. The owner may drop it | TLY-2 |
| **TLY-4** | 3769 | The local copy (§3.8.13): the first copy, the copy of changes by `AlterID`, the read from the copy with its age, the maximum age, the encryption and the deletes. The time of the last good copy on each poll | With TallyPrime closed, a read answers from the copy and names its age. A copy older than the maximum age answers nothing. The copy on disk is not plain text. Disconnect deletes it | The tests of the program, on a synthetic company with a known set of changes | STANDARD: book data on disk | TLY-2, LNK-Q8, LNK-Q9 |

### 7.5 Access rule slices (§3.9, task 3985)

ACC-1 comes first. ACC-2 and ACC-3 need it.

| Id | Rung | What | Gate | Done when |
|---|---|---|---|---|
| **ACC-1** (task 3987) 🔨 BUILT 2026-11-26, dark | 1 | Backend: the storage, the resolver step, the two routes, the audit line and the flag (§3.9.2 to §3.9.4). Modes: everyone, chosen people, chosen teams and admins only, with the two group-write rules | 🟢 AGENT-SAFE to build dark. HIGH risk. The flag flip waits for the owner preview | Rows ACC-a to ACC-g below |
| **ACC-2** (task 3988) 🔨 BUILT 2026-11-26, dark | 1 | The "Who can use" control on each connection (§3.9.5) | 🟢 AGENT-SAFE to build. STANDARD: UI, with an owner preview | Visual review passes in four contexts |
| **ACC-3** (task 3989) 🔨 backend BUILT 2026-11-27, dark | 1 | The integrations list in My access, and `GET /auth/me/integrations`. This slice builds the route only. Another member builds the tab | 🟢 AGENT-SAFE to build. HIGH: it shows access | A member sees their own result only. Each reason of §3.9.4 maps to its case. The `allowed` of an MCP row equals the resolver and the chat authorizer. The `allowed` of an API row equals the `_resolve` of the call route, and only a connection of the organization shows, with `API_CONNECTIONS_ORGS` on. A member of the seeded role reads `admin-only` on a connected API row. No body names another member, a team or `set_by`. A guest sees no service name. Org B changes nothing in org A (`test_auth_me_integrations.py`, `test_auth_me_integrations_r8.py`, which is on `r8.must_run`) |
| **ACC-4** (task 3990) | 2 | The chat tool for admins, behind a card | 🔴 Waits for the owner decision of PAR-6b | Chat parity row |

**ACC-1 acceptance, R8** (`test_integration_access_r8.py`, two organizations, a real database):

- **ACC-a.** With no rule, the resolved access of each of the five roles is the same as today.
- **ACC-b.** For a refused member, the run authorizer refuses `mcp-x`, and `_maf_servers` drops the server. The `api-y` route answers 404. `_may_use` answers False. `build_integrations` drops a `_REGISTRY` key that a stored rule refuses, but the route takes no such rule (§3.9.2).
- **ACC-i (fix round 2).** A PUT on each `_REGISTRY` key answers 422. A stored rule of one shows with `listed: false`, and `everyone` clears it.
- **ACC-c.** A listed person passes. A new member is refused. A listed group member passes, and a removal refuses at the next resolve with no restart.
- **ACC-d.** A holder of `integrations:manage` passes. A member with an exact allow override is refused. A guest on the list is refused.
- **ACC-e.** A shared session with a refused participant denies, for an email, group or org subject. A delegated run also denies.
- **ACC-f.** Organization B does not change. The first write on empty settings persists, and a second key keeps the first.
- **ACC-g.** A malformed rule and a failed read refuse integrations only. Neither raises, and neither marks the access tables as missing.
- **ACC-h (fix round 1).** A purge takes the address off each rule, and the resolver refuses the same address when it joins again. A PUT waits for a member write that passed the gate, and a gate waits for a rule write, then reads it.

**ACC-1 acceptance, hermetic** (`test_integration_access_rules.py`):

- Both routes answer 404 for an organization off the flag, and 403 without either permission.
- An unknown service, group or address, and `org`, answer 422. So does each key of `_REGISTRY`.
- Each write records the audit line and clears the cache.
- Each service name equals the output of `mcp_service` in both modules, and of `api-<template>`.
- The preview shows the rule as the source.
- The group-write gate of §3.9.2 holds.

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
  tests/unit/test_integrations_mcp_tenant_r8.py \
  tests/unit/test_mcp_registry_r8.py
cd workbench/control_plane && npx tsc --noEmit && npx vitest run src/lib/missingIntegrations.test.ts src/app/integrations
```

Each slice adds its own test file to this list.

**Verify ACC-1 (§3.9).** The first two files are new. No R8 suite may skip.

```bash
bash scripts/dev_db.sh && eval "$(bash scripts/dev_db.sh --export)"
timeout 900 uv run pytest tests/unit/test_integration_access_rules.py tests/unit/test_integration_access_r8.py \
  tests/unit/test_org_access_credentials.py tests/unit/test_delegated_integration_filter.py \
  tests/unit/test_maf_mcp_injection.py tests/unit/test_maf_mcp_confirm.py tests/unit/test_api_connections_r8.py \
  tests/unit/test_admin_groups.py tests/unit/test_identity_pool_timeout.py tests/unit/test_member_of_org_read.py \
  tests/unit/test_session_authority.py tests/unit/test_org_access_control.py \
  tests/unit/test_org_access_enforcement.py tests/unit/test_studio_live_preview.py -v -rs
uv run ruff check packages/acb_auth apps/services/gateway/gateway/routes/admin
```

**Verify ACC-3 (§3.9.4).** The first two files are new. No R8 suite may skip.

```bash
bash scripts/dev_db.sh && eval "$(bash scripts/dev_db.sh --export)"
timeout 900 uv run pytest tests/unit/test_auth_me_integrations.py tests/unit/test_auth_me_integrations_r8.py \
  tests/unit/test_integration_access_rules.py tests/unit/test_integration_access_r8.py \
  tests/unit/test_org_access_enforcement.py tests/unit/test_auth_me_features.py \
  tests/unit/test_api_call_egress.py tests/unit/test_api_call_limits.py tests/unit/test_api_connections_r8.py -v -rs
uv run ruff check packages/acb_auth apps/services/gateway/gateway/routes/admin \
  apps/services/gateway/gateway/routes/api_connections.py
```

**Verify ACC-2 (§3.9.5).**

```bash
cd workbench/control_plane && npx tsc --noEmit && npx vitest run src/app/integrations
cd ../.. && uv run pytest tests/unit/test_chat_parity.py
```

**Verify WS-LNK (§3.8, APPROVED, D232).** Each test file below is new, except `test_pack_guards.py` and `test_tenant_redis.py`. LNK-1 to LNK-3 write the new files.

```bash
bash scripts/dev_db.sh && eval "$(bash scripts/dev_db.sh --export)"
uv run pytest tests/unit/test_connector_pairing.py tests/unit/test_connector_pairing_r8.py \
  tests/unit/test_connector_link_shape.py tests/unit/test_connector_link_r8.py \
  tests/unit/test_connector_link_door.py tests/unit/test_connector_link_transport.py \
  tests/unit/test_connector_link_injection.py tests/unit/test_connector_link_access_r8.py \
  tests/unit/test_pack_guards.py tests/unit/test_tenant_redis.py -v -rs
cd workbench/control_plane && npx tsc --noEmit && npx vitest run src/app/integrations
```

No R8 suite may skip.

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
| A local connector row never takes a URL, and a URL row never skips the guard | `test_connector_link_shape.py` (new) | LNK-1, LNK-3 |
| A link credential reaches only the queue of its own connection | R8, two orgs, `test_connector_link_r8.py` (new) | LNK-2 |
| A run with no member gets no link tool | `test_connector_link_injection.py` (new) | LNK-3 |
| "Only these people" holds for a member who joins later | R8, `test_connector_link_access_r8.py` (new) | LNK-3 |
| The pairing start names no org, and the pending record holds no tenant data | `test_connector_pairing.py` (new) | LNK-1 |
| Each link answer is framed, and it marks the session | `test_connector_link_injection.py` (new) | LNK-3 |
| The pending record serves one approval and one poll | R8, `test_connector_pairing_r8.py` (new) | LNK-1 |
| The self view names no other member | R8, `test_auth_me_integrations_r8.py` (new), and the sentinel of `test_auth_me_integrations.py` | ACC-3 |

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

### Local connector questions (WS-LNK, §3.8, 2026-11-17)

The owner answers these on task 3653. LNK-Q1 and LNK-Q7 gate LNK-1 and LNK-3. LNK-Q2 gates LNK-4. LNK-Q3 gates the general release, not the rung 2 demo. LNK-Q8 and LNK-Q9 gate TLY-4.

| Id | Question | Recommendation |
|---|---|---|
| **LNK-Q1** | What does "company domain" mean? | The domain routes the pairing request and drives the mismatch warning. It grants nothing, and the org comes from the approving admin. The other reading is each customer's own web address with a tunnel. That stays an advanced option for a customer with its own IT team |
| **LNK-Q2** | What does the data notice say? May the books go to every vendor of the router chain? | The notice names each vendor that can read the data, and the admin accepts it at the approval. No rule covers accounting data today, because D126 covers issue reports only. The owner decides whether a connector pins its calls to some vendors |
| **LNK-Q3** | Which code-signing certificate: EV or OV? | EV, for the general release. It stops the SmartScreen warning at once. An OV certificate builds trust over weeks of downloads. An unsigned or OV-signed test build serves the rung 2 demo. The owner buys the certificate |
| **LNK-Q4** | Where does Metorite serve the installer and the update file? | A path on the public host of the link door, so the program trusts one host. The update file carries the signature that LNK-5 checks |
| **LNK-Q5** | What product name does a customer see in the installer and in Metorite? | The owner picks it. The build uses a working name until then |
| **LNK-Q6** | Which repository holds the program? | This repository, so CI tests both sides of the link contract in one run |
| **LNK-Q7** | May a run with no member use a link tool? | No. Refuse it (rule c of §3.8.7). A batch, scheduled or webhook run gets no MCP server today. Rule c closes the rest: a run that reaches the injection with no member, such as a `system:` principal. A later slice can give such a run a named member |
| **LNK-Q8** | Does a copy of the books also live in Metorite, so that reads work while the customer's machine is off? | No, not now. The copy stays on the customer's machine (§3.8.13). A copy in Metorite moves the books into our database, and the data notice of LNK-Q2 must then say so. Ask again if customers' machines are off too often |
| **LNK-Q9** | The default interval of the local copy, and its default maximum age | Every 30 minutes while TallyPrime is open, and a maximum age of 7 days. The admin can change both. Each answer from the copy names its age, so the member judges it |

### Access rule questions (§3.9, task 3985)

The owner answers these on task 3985. The default is not open: the owner ask of 2026-11-26 names "everyone" as the default. The agent decides that an admin always keeps access, because an admin can open any rule again. The owner may overrule it.

| Id | Question | Recommendation |
|---|---|---|
| **ACC-Q2** | May a team decide who keeps an integration? A change of that team's members then also needs the access right | ✅ **Yes** (owner, task 3985, 2026-11-26, answer to "Yes to all three"). A team only narrows, and it gives no permission |
| **ACC-Q4** | Do the app connections (CRM Zoho, mailboxes, WhatsApp, Google) get a rule in a later slice? Do the built-in agent connectors (the keys of `_REGISTRY`) get one? | Later, one app at a time. The assistant and agents come first. A built-in connector first needs a check in `credential()` or in each tool that reads it. Today `credential()` reads the process environment first, so a rule would stop nothing (ACC-1 fix round 2) |
