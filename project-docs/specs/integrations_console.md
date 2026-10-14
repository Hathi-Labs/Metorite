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
>
> `APP_OWNED_SERVICES` in `routes/integrations.py` now maps the tile to the
> CRM's row (`zoho`, `crm`). The status reads that row in the tenant session
> and offers no key form. The test reads the CRM's own credential.
> `APP_OWNED_TILES` in `lib/connections.ts` is the client mirror, and the
> panel links to CRM settings. The fences are `test_integrations_zoho_tile.py`,
> `test_integrations_zoho_tile_r8.py` and `appOwnedTile.test.ts`.

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
  `TOKEN_MARGIN_S` is a new constant of 900 s.
- The tool gets a `header_provider` that returns the cached `Authorization`
  value and nothing else. A changed header makes MAF connect the session again
  (the `header_provider` docstring, `_mcp.py:3841-3851`). So the value never
  changes inside a run.
- **A 401 on connect** drops the server from the run, and the run goes on. The
  log line `mcp.oauth_unauthorized` names the server only.
- If the injection refreshed the token in this run, that 401 also sets
  `needs_reconnect`. Else the next run refreshes, and an `invalid_grant` then
  sets `needs_reconnect`. So one expired token never asks an admin to connect
  again. ⚠️ The request for this section said "a 401 marks needs_reconnect".
  This spec narrows that, for the reason above.
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
  member use this connection as the admin. It also says that a read-only tool
  runs with no card, so every member can read what the admin can read at the
  provider.
- **The card** (`agent_architecture.md` §12.2 WS-8c, item 14) shows "acts as
  <admin email> at <provider>" for each tool of an OAuth server. The line sits
  under the hint line, and the card escapes it as it escapes the names (item
  19).
- **A read-only tool runs with no card** (H-299, WS-8c item 13). So a member
  can read, through an agent, what the admin can read at that provider. The
  console says so, and this spec says so. The H-300 setting `always_confirm`
  puts a card on every tool of one server, and a new server has it on
  (`agent_architecture.md` §12.2 WS-8c, item 31).
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
| **IN-3** | **Per-run credentials for the integration resolvers.** `acb_skills` resolvers read the per-run context, filled from the per-org store, and the environment stops winning | 🔴 §6 gate (f) | A run in org A gets org A's key and a run in org B gets org B's, on one process at one time (R8) |
| **IN-4** ✅ MERGED 2026-10-12 (#905), org scope only | **Move the org MCP servers into `integration_connections`, and repoint the WS-8c reader.** Owner direction, 2026-10-12: "get the organization-wide connectors done". **Non-goal:** a personal (member) MCP connection. That is a later ticket. **The key.** Each server is one org row: `provider = 'mcp:<name>'`, `owning_app = 'integrations'`, `scope_kind = 'org'`. The unique index `integration_connections_one_live_idx` (`245_integration_connections.sql:70`) includes the provider, so one org can hold many servers. The name rule of `POST /mcp` makes `mcp:<name>` unique, and no other provider starts with `mcp:`. **Encryption at rest.** The header map is a credential. `connections/store.py` seals it as `{"headers": {...}}` into `credentials_encrypted`, as Fernet ciphertext through `acb_llm.key_store` (`ACB_MASTER_KEY`). The url, label, description, transport and agent scope are not secret, so they go into `provider_meta`. `enabled` is the status, `connected` or `disconnected`. `env_vars` does not move, because Metorite runs no stdio server (D85). **One reader.** `connections/mcp.py` opens `tenant_session(org)` for each call. The org comes from the run binding or the caller's tenant, never from input (R5). The orchestrator and the routes read through it. A Copilot agent still gets no registry server. **Expand only (R6).** Writes go to the registry only. A read moves each old `mcp_servers` row of the org into the registry once, encrypted. A delete removes the old row too, so a removed server cannot come back. `mcp_servers` stays readable for one release. H-301 drops it. **H-300 (2026-10-12).** `provider_meta.always_confirm` holds the admin's "always confirm" switch. A new server gets `true`. A row with no key, and a moved row, read as `false` (`agent_architecture.md` §12.2 WS-8c, item 31). No migration. **Paths at `713dc84ac`:** `connections/store.py` (`_seal` :232), `connections/mcp.py` (new), `_tool_injection._inject_mcp_servers` (:2139, `_MCP_ROWS_SQL` :2113), `routes/integrations.py` (:1808, :1821, :1862, :1885, :1959), `routes/integrations_connections.py` (`_registry_row` :137, `_mcp_rows` :224, the disconnect branch :496, the stored test :509-560) | 🟢 AGENT-SAFE | Rows A to H of §7.3 pass. The WS-8c fences pass against the new store |
| **IN-5** | **Retire the old writes.** Remove the env-write code, `_persist_tokens`, the dead OAuth route, and `configure_integrations`'s copy into the environment for customer keys. Move `COPILOT_CHAT_MODEL` out of the deployment `.env` (IN-D17) | 🔴 §6 gate (f) | `os.environ[` with a customer key appears nowhere outside the operator plane (fence) |
| **IN-6** | **Custom APIs and plugins.** Keep them with a reader and per-org keys, or retire them | 🔴 owner (Q2) | Per the answer |
| **IN-7** ✅ merged 2026-10-11 (#891) | **GitHub per org.** The device flow writes a member or org connection, not the deployment token. connect-cli goes. `GET /github/account` shows the org's own connection, never the operator's account (IN-D18). **Built as (2026-10-11):** The device poll saves an org connection (`github`, `integrations`) through `connections/store.py`. The login and the scopes sit in the row. `connections/github.py` is the one reader, `token_for_org(org)`. The agent config fetch reads it, and with no connection it sends no token. `GET /github/account` and `GET /test` read the org row only and run no `gh`. `DELETE /integrations/github` deletes the blob. GitHub cannot revoke a device-flow token without the client secret, so the answer says `revoked_at_github: false`. An old `provider_keys` `github:token` row moves into the store on the next read. That fallback read stays for one release. The slice adds no migration. The operator plane keeps `GITHUB_TOKEN`: the model list, the runtime check, the self-mutation PR, the LiteLLM route, the Copilot SDK runtime and the agent-repo clone in `loader.py`. A clone of a private customer repo waits for IN-3. A token that a member saves through configure or `PUT /keys` still lands in `provider_keys`, and the reader moves it. IN-5 retires that path. Fence: `tests/unit/test_github_per_org_r8.py`. | 🟢 AGENT-SAFE | Two orgs hold two tokens. The server's own `gh` token is never imported |
| **IN-8** 📝 PLAN 2026-10-12 (D98) | **MCP OAuth for an org connection.** An admin connects an MCP server that needs an OAuth consent. The connection acts as that admin at the provider, and the console and each card say so. Dark behind `MCP_OAUTH_ENABLED`, default off. The design, the provider table and the acceptance are in §3.6. It needs IN-4 merged. A member connection over OAuth is out of scope (§1.3) | See IN-8a to IN-8d | Rows O1 to O14 of §3.6.11 pass, each red first. No R8 suite skips |
| **IN-8a** 🔨 BUILT 2026-10-13, PR #931 | Discovery, CIMD and DCR, PKCE, the signed state, the callback and the store (§3.6.3 to §3.6.6), dark | 🟢 AGENT-SAFE | O1 to O6, O9 and O11 to O13 |
| **IN-8b** | Refresh at run time, the 401 on connect, and `needs_reconnect` (§3.6.7) | 🟢 AGENT-SAFE | O7, O8 and O14 |
| **IN-8c** | The console Connect button, the BFF route for `POST /integrations/mcp/{name}/oauth/start`, the dialog text, the acts-as label and the card line (§3.6.9) | 🟢 AGENT-SAFE | O10. Visual review passes |
| **IN-8d** | Pre-registered providers: GitHub, Slack and HubSpot (§3.6.2) | 🔴 OWNER-GATE. The owner creates each OAuth app, and its secret goes in the operator plane (D56) | The owner's consent with each provider stores an org connection in Fracktal |

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
