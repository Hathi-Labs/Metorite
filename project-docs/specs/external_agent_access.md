# External agent access — Metorite as an MCP server

**Status: DECIDED (D97, 2026-10-10). Updated 2026-10-14. EA-1 and EA-2a are BUILT, dark, and merged in #884 (commit `898cfbc89`).**

**Verified against code on 2026-10-14 at 2295c9bf9.** The EA-6 audit read the anchors of §6.1 (EA3-28 to EA3-31), §9.2, §9.5, §9.6 and §15 at that commit.

**EA-6, 2026-10-14.** The spec audit of EA-6 gave NO-GO, and it narrowed the dispatch to EA-6a. The decisions EA3-28 to EA3-31 (§6.1) answer it. EA-6 now splits into EA-6a, EA-6b, EA-6c and EA-6d. **EA-6a is MERGED and dark (2026-10-14), after fix round 1.** It changes branch 0, and it passed an adversarial review and an independent verification (§10.1). The writes flag `agent_access_writes_enabled` stays off. EA-6b, EA-6c and EA-6d are not built.

**LIVE on 2026-10-14 for every organization, reads and writes.** The owner turned it on (`AGENT_ACCESS_ENABLED=true`, `AGENT_ACCESS_ORGS=*`, `AGENT_ACCESS_WRITES_ENABLED=true`) on `acb-gateway` and `acb-agent-edge`. The EA-10c smoke of §15.3 passed: the metadata answers 200, `/mcp` answers 401 with the §9.6 challenge, a false token answers `invalid_token`, and the box fetches the Claude Code document. A member connects with `claude mcp add --transport http metorite https://mcp.metorite.com/mcp`.

**EA-3a is MERGED and dark (#946, `d5d8244f5`, 2026-10-13), after fix rounds 1 to 5. EA-2c is MERGED and dark (#953, 2026-10-13). EA-3b-1 is MERGED and dark (#954, `355c18bcf`, 2026-10-13), after fix rounds 1 and 2. EA-3b-2 is MERGED and dark (2026-10-13), after fix rounds 1 and 2. EA-4a is MERGED and dark (2026-10-13), after fix rounds 1 and 2. EA-10p, the organization allowlist (EA3-27), is MERGED and dark (2026-10-13), after fix round 1. EA-10a and EA-10b are MERGED and dark (2026-10-13), after fix rounds 1 and 2: the unit is installed and not enabled (§15.3). EA-2b, EA-3c, EA-4b, EA-4c, EA-5 to EA-9, EA-10c and EA-11 are not built.**

**EA-3b, 2026-10-13.** The spec audit of EA-3b gave NO-GO. The decisions EA3-9 to EA3-23 (§6.1) answer it. EA-3b now splits into EA-3b-1 and EA-3b-2. A new slice, EA-2c, accepts a loopback redirect on any port, and it comes before EA-4.

**EA-3, 2026-10-11.** The spec audit of EA-3 gave NO-GO. The engineering decisions EA3-1 to EA3-8 (§6.1) answer it. EA-3 now splits into EA-3a (the gateway) and EA-3b (the edge), with EA-3c as a later follow-up.

**EA-4, 2026-10-13.** The spec audit of EA-4 gave NO-GO. The decisions EA3-25 and EA3-26 (§6.1) answer it. EA-4 now splits into EA-4a, EA-4b and EA-4c, and §10.5 holds the rules of the consent page. **EA-4a is MERGED and dark (2026-10-13), after fix rounds 1 and 2.** EA-4b and EA-4c are not built.

**What is built.** EA-1 is the three tables, the token store and the `agent_access_enabled` flag, default OFF. EA-2a is the authorization server core: the edge unit `apps/services/agent_edge` (not deployed) and the gateway route `/agent-access`. EA-3a is the gateway half of the read tools: branch 0 of `get_current_user`, the signing module and the agent scope in the Projects visibility seam. EA-3b-1 is the edge transport `/mcp` with two tools, `list_tasks` and `search_tasks`. It is merged (#954, `355c18bcf`). EA-3b-2 adds the other five read tools and the fence edit of EA3-9. It is merged (#957, `97ab64e96`).

Written 2026-10-10 from a read-only survey of the tree at commit `f62f2bb16`. Every anchor below came from that survey. Re-verify each one at dispatch (work_plan.md §1, item 4).

**Board row:** **WS-55**, in `work_plan.md` §2. **Decision:** D97, in `work_plan.md` §3. The owner accepted all eight defaults of §6 in chat on 2026-10-10.

**Origin:** D91.5 says an MCP facade for external clients is "a later, separate item" with its own spec and an owner gate. This is that spec.

**Owner of the topic:** this file. `mcp_plugin_integration.md` owns the other direction, where Metorite is an MCP **client** that reads outside servers. Do not merge the two.

---

## 1. The answer in one screen

A member connects an outside agent to Metorite with one URL. The agent signs in through a consent page. The member picks what the agent may do. The agent then calls a small set of typed tools. Every call runs as that member, and it can never see or change more than the member can.

- **Protocol.** Standard MCP over Streamable HTTP, with OAuth 2.1. No custom protocol.
- **Identity.** The agent is a **connection**. A connection belongs to one member in one organization. It holds a scope list, and that list is a ceiling.
- **Authority.** The rule is `effective = member's live access ∩ the connection's scopes`. The member's access is read again on every call. A grant that shrinks takes effect at once.
- **Surface.** About 14 tools for Projects and company context. No email, no WhatsApp, no CRM, no delete, no grant writes in version 1.
- **Provenance.** Every write carries `mcp:<client>` as its actor-via stamp. Every call writes one audit row.
- **Ship dark.** One flag, default OFF. Then one organization-level switch, default OFF.

The two use cases that started this spec both fit:

1. **A coding agent works project 7.** It lists the coding backlog and claims a task. It moves the status. It records the branch, the commit IDs and the pull request on the task.
2. **Claude designs a landing page from company context.** It reads the company profile and brand. It reads no project data, because the member did not grant it.

## 2. What we learned from the Personal Agent Proxy

Source: `github.com/personalagentproxy/proxy` (MIT, early). It is a self-hosted hub. A person connects mail, Notion and Linear once, then grants each agent a set of actions. The summary below comes from its README and AGENTS.md only. We did not read its code.

| Idea | Proxy | Metorite decision |
|---|---|---|
| Door | One MCP URL, OAuth 2.1, PKCE S256 only | **Copy.** Add Client ID Metadata Documents first, because the 2026-07-28 MCP spec now prefers them |
| Tool surface | Four generic tools: `list_connections`, `list_tools`, `read`, `run` | **Do not copy.** We know our schema. Typed tools carry MCP annotations, so a client can ask a human before a write |
| Grants | Connection defaults, then per-agent overrides | **Do not copy.** We already have a grant vocabulary. The connection holds a ceiling and the member's own access does the rest |
| Data | No cache. Every call goes live to the provider | **Do not copy.** We own the data. The call goes through our own routes under row-level security |
| Tokens | SHA-256 at rest, refresh rotation, revoke per agent | **Copy.** Add reuse detection |
| Audit | Every call logged, denials too | **Copy** |
| Identity | One person owns many agents | **Change.** Our member belongs to an organization, and an admin must see and revoke every connection |

## 3. Scope and non-goals

**In scope (this spec describes, and a later PR builds):**

- The MCP edge, the authorization server endpoints, and the consent page.
- The connection model, the tool allowlist, and the audit and limit seams.
- One schema addition: structured task references for a pull request, a commit or a branch.
- The member list of connected agents, and the admin policy and audit view. They live in the Integrations app (§5.4).

**Non-goals (version 1):**

- Email, WhatsApp, CRM, meetings and the member's personal stores. D91.4 says a personal store never gets a direct read tool for a shared agent. This edge is an outside agent, so the rule binds harder.
- A hard delete, a grant write, a role write, billing, and any `admin:*` route. D-PM-35 and D-PM-40 already refuse these for the in-product chat.
- A personal access token and a device flow. See D-EA-5.
- Metorite's own agents calling this edge. They keep native tools (D91.1).
- A local stdio server. Outside agents reach us over HTTPS only.
- Automatic pull request linking from a GitHub webhook. See §14.

## 4. The seams we reuse, and the gaps

The survey found **no** inbound MCP server, no token issuer and no member-level access token in the tree. Every `oauth` and `mcp` hit is a client or an internal secret.

The anchors below were read again on 2026-10-11, at commit `0bd098f18`, for the EA-3 audit. The EA-3b audit read them again on 2026-10-13, on the branch `ws55-ea3b` at commit `0cdb0a6db`. Re-read them at dispatch.

| Need | Existing seam | Anchor |
|---|---|---|
| Member identity at the gateway | `get_current_user`, then `resolve_access` and `resolve_identity` | `packages/acb_auth/acb_auth/deps.py:669`, `access.py:1108`, `access.py:1275` |
| The matched route template | `_route_template`, and `_matched_template` for branch 0 | `acb_auth/deps.py:936`, `deps.py:959` |
| Acting as a member from a service | `headersActingAs`, and the `skill_projects` client | `workbench/control_plane/src/lib/gateway.ts:142`, `apps/skills/skill-projects/skill_projects/client.py:135` |
| Tenant binding for row-level security | `bind_tenant`, then `tenant_session` | `acb_common/db.py:210`, `acb_common/db.py:248` |
| Member access ceiling | `EffectiveAccess.intersect` ("an agent acting for a member must never exceed them") | `acb_auth/permissions.py:375` |
| Project and task visibility | `Visibility`, `resolve_visibility`, `resolve_visibility_for`, `load_visible_project`, `load_visible_task`, `task_visibility_clause` | `gateway/routes/projects/core.py:1190`, `:1264`, `:1313`, `:1405`, `:1426`, `:1644` |
| A tool allowlist with a coverage fence | the `skill_projects` manifest, classes A, B, C and X | `apps/skills/skill-projects/skill_projects/manifest.py`, `tests/unit/test_projects_chat_coverage.py` |
| MCP tool hints | `read_only`, `destructive`, `idempotent`, `open_world` | `packages/acb_skills/acb_skills/tool_annotations.py`. The edge tools do **not** go into its `TOOL_ANNOTATIONS`. They carry their own annotations (§9.5) |
| Provenance stamp | `X-Actor-Via`, stored in `pm_activities.meta.via` | `gateway/routes/projects/core.py:75-98`. `acb_auth/agent_oauth.py:388` defines `ACTOR_VIA_PATTERN`, and `core.py:47` imports it |
| Audit write | `acb_audit.record` | `packages/acb_audit/acb_audit/log.py` |
| Optimistic concurrency | `If-Match` on `PATCH /projects/tasks/{id}` | `gateway/routes/projects/tasks.py:483`. The token is `updated_at` (`core.py:1809-1852`) |
| The public routes of the gateway | `PUBLIC_ROUTES` | `gateway/main.py:644` |

**Gaps. Each one becomes a slice:**

1. No token store, no client registry, no consent page and no `/.well-known` metadata.
2. The gateway trusts the internal bearer plus `X-User-Email`. Any process that holds that bearer can act as anyone. So the edge holds no bearer, and it signs each call instead. See §10.1.
3. No per-project write role exists. Anyone who can see a project can write its tasks. A "read-only agent" must come from the connection ceiling, not from the grants.
4. `POST /projects/nodes/{id}/grants` needs only visibility of the project (`tree.py:1309`). That is a hazard for an outside agent, so the edge never exposes it.
5. `pm_tasks.source` has no `mcp` value, and `pm_tasks` has no pull request or commit field. `pm_task_links` links a task to a task only.
6. No shared rate limiter exists. The edge needs its own.
7. `admin:audit:read` is declared, and the survey found no route that reads `audit_event`. Do not promise an audit API before EA-8 builds one.
8. There is no company profile route. `organization` and `org_settings` hold a name, a domain and branding only. EA-5 must decide what "company context" is (D-EA-6).

## 5. Architecture

```
external agent (Claude, ChatGPT, a coding agent)
        │  HTTPS, Streamable HTTP, Authorization: Bearer <opaque token>
        ▼
  mcp.metorite.com        the MCP edge  (resource server)
        │  1. validate the token, audience and expiry
        │  2. load the connection, compute the effective scopes
        │  3. check the scope of the tool, the rate limit
        │  4. call the gateway on loopback with a signed request (§10.1)
        ▼
  gateway  branch 0 of get_current_user: check the signature, read the
        │  connection, resolve the member, attach the agent scope
        ▼
  /projects/* and /auth/me GET routes   (unchanged routes, RLS, and a
                                          narrower Visibility for an agent)
```

The consent page is part of the control plane, because only the control plane holds the member's NextAuth session.

```
agent ──authorize──▶ app.metorite.com/connect/authorize ──(NextAuth session)──▶ consent
agent ──token──────▶ authorization server endpoint ──▶ opaque token + refresh token
```

### 5.1 Where the edge runs (D-EA-2)

**Recommended: a separate small unit named for its job, with its own systemd sandbox** (box_hardening.md BH-2 shape). It holds the database DSN and the signing key of §10.1. It holds **no** internal bearer (EA3-1). A narrow unit has a small blast radius, and it keeps `/mcp` traffic off the main gateway process.

**Alternative: a router inside the gateway.** It needs no signing key. It also puts public, hostile traffic in the process that serves every member. The route would join `PUBLIC_ROUTES` (`main.py:644`) and authenticate itself.

D-EA-2 chose the separate unit. The edge calls the gateway over HTTP on loopback, at the Setting `agent_edge_gateway_url` (default `http://127.0.0.1:8080`). That address is the one the Caddy block of `api.metorite.com` proxies to (`deploy/hostinger/caddy/Caddyfile:51`).

### 5.2 The host (D-EA-1)

**Recommended:** the edge lives on its own host, `mcp.metorite.com`. No session cookie is ever sent there. The consent page stays on `app.metorite.com`. The canonical resource identifier is `https://mcp.metorite.com/mcp`. The wildcard record `*.metorite.com` already sends `mcp.metorite.com` to the box (`marketing_site.md` §2). So EA-10 needs no new DNS record.

### 5.3 Protocol facts, and the revision each one came from

Re-read the pinned text at build time. It moves fast. EA-3 accepts the revisions `2025-06-18` and `2025-11-25`. It assumes `2025-03-26` only when a request after `initialize` has no `MCP-Protocol-Version` header.

The table names the first revision that carries each rule. The tests of EA-3b pin the behaviour, not the citation. §9.6 is the full wire contract.

**What EA-3 builds.**

| Rule | First revision |
|---|---|
| One endpoint that answers a POST with `application/json`. The server may send no `Mcp-Session-Id` | 2025-03-26 (Streamable HTTP) |
| The tool annotations `readOnlyHint`, `destructiveHint`, `idempotentHint` and `openWorldHint` | 2025-03-26 |
| The server validates `Origin`, and answers 403 to an origin that it does not allow | 2025-03-26. The 2025-11-25 text adds the 403 |
| No JSON-RPC batch | 2025-06-18 removed batches |
| The `MCP-Protocol-Version` header on each request after `initialize`. No header means `2025-03-26`. A value the server does not support gets 400 | 2025-06-18 |
| A tool `title`, an `outputSchema`, and `structuredContent` with the same JSON in a text block | 2025-06-18 |
| RFC 9728 metadata, and a 401 whose `WWW-Authenticate` carries `resource_metadata` | 2025-06-18 |
| The RFC 8707 `resource`, an audience check, and no token passthrough | 2025-06-18 |
| A 403 with `error="insufficient_scope"` and the needed `scope` in the challenge | 2025-11-25 |
| A tool input that fails validation is a tool error with `isError: true`, not a protocol error | 2025-11-25 |

**What EA-2a already built.** PKCE with S256 only, no token in a query string, RFC 8414 metadata, `iss` in the redirect (RFC 9207), and Client ID Metadata Documents. Its fence is `tests/unit/test_agent_oauth_server.py`.

**What came from the 2026-07-28 text only.** The owner-side research of 2026-10-10 read it at `modelcontextprotocol.io/specification/latest`. No copy of that text is in the tree. EA-3 builds no rule that exists only in it.

- The 2026-07-28 revision is real, and its wire shape differs. A request is stateless and carries required `_meta` fields, and a result carries a `resultType`. EA-3c accepts that revision after a copy of its text is in the tree (§15).
- It prefers Client ID Metadata Documents, and it marks Dynamic Client Registration as deprecated.
- It says that the tool list may differ by authorization and must not differ by connection state. EA-3 keeps this, because the older revisions allow it. The list varies only by scope.
- A server must validate inputs, check access, limit the rate and sanitize outputs. EA-3, EA-7 and §9.4 cover these.

### 5.4 Where members and admins manage it: the Integrations app (D-EA-9, decided 2026-10-14)

The owner suggested on 2026-10-11 that the Integrations app should list inbound and outbound connections together. The owner decided it in chat on 2026-10-14.

**The decision.** Integrations opens to all members. Each member sees an "AI agents" view. The view holds the connect card and the member's own agents, with Revoke. A member with `integrations:manage` also sees the org views and every agent connection of the organization, with Revoke. EA-4b builds this.

**What exists today.** The Integrations app is `live` since 2026-10-13, in the Admin section, at `/integrations`. It has a rail of views: Connections, API keys, Plugins and Skills (WS-54, `integrations_console.md` §7.2). Connections is the admin console over one registry, `integration_connections`. That registry holds the connections that Metorite makes to a service.

**One app, two directions, two stores.**

- **Outbound** means Metorite uses a service. Examples are Zoho, Gmail and an outside MCP server. These rows live in `integration_connections`.
- **Inbound** means an outside agent uses Metorite. These rows are the `agent_connection` rows of §7.
- The tables stay apart. `integration_connections` holds encrypted credentials that Metorite uses. `agent_connection` holds token hashes and scopes, and its token lookup runs before the tenant is known (§10.3). A merge would put those RLS-exempt lookup rows beside the encrypted credentials.
- The routes stay apart too. WS-54 owns the outbound routes. WS-55 owns the four agent routes of §15 EA-4b, and the AI agents view reads only them. No route joins the two stores.

**What the app adds for inbound (EA-4b).**

- A **Connect an agent** card. It shows the URL `https://mcp.metorite.com/mcp`, a copy button, and one short setup note for each client of §17.
- The member's own agents, with Revoke.
- For a member with `integrations:manage`: every agent connection of the organization, with Revoke.

**Later slices, not EA-4b.**

- The organization switch and the client allow list (D-EA-8) are EA-4c.
- The audit view of agent calls is EA-8.
- The name "Outside MCP servers" for the MCP rows is a WS-54 choice. EA-4b does not change it.

**The gate (EA-4b).** The pane has no `feature` in `nav.ts`, and `/integrations` is an exact entry of `ALWAYS_ALLOWED` in `access.ts`. So each member reaches the app. Each view then gates itself. API keys, Plugins, Skills and Connections need `feature:integrations`, as before. A member without it opens on `?view=agents` and sees only that view. The pane stays in the Admin section. `launch_surface.md` §2 and `nav.test.ts` hold the change.

### 5.5 Where the code lives, and the fixed addresses

- **Issuer:** `https://mcp.metorite.com`. RFC 8414 and RFC 9207 need one fixed value.
- **Public endpoints** run with the edge unit (D-EA-2): `/.well-known/oauth-protected-resource`, `/.well-known/oauth-authorization-server`, `/oauth/token` and `/oauth/revoke`. They are not in the gateway `PUBLIC_ROUTES` list.
- **Authorize page** at `https://app.metorite.com/connect/authorize`. EA-4a builds it (§10.5).
- **Store:** `packages/acb_auth/acb_auth/agent_tokens.py`. It follows `acb_auth/email_otp.py`.
- **Authenticated half:** `apps/services/gateway/gateway/routes/agent_access.py`, prefix `/agent-access`, behind default-deny.
- **Edge unit:** a new uv member `apps/services/agent_edge/`. EA-3b adds `/mcp` to it, in `agent_edge/mcp.py`, `tools.py`, `bearer.py`, `gateway_client.py` and `cursor.py`. EA-3b adds `httpx` to the dependencies in `apps/services/agent_edge/pyproject.toml`, because `gateway_client.py` calls the gateway with it. Today the edge gets `httpx` only through `acb-auth`.
- **Signing:** `packages/acb_auth/acb_auth/agent_signing.py` (EA-3a). It holds the one canonical string and the one HMAC of §10.1. The edge signs with it, and the gateway checks with it.
- **Allowlist:** `MCP_ROUTES` in `packages/acb_auth/acb_auth/agent_oauth.py` (EA-3a, §9.5). The edge client and branch 0 of the gateway both read it.
- **Flag:** a `Settings` field `agent_access_enabled`, default false, which reads `AGENT_ACCESS_ENABLED`. The edge and the gateway read the same flag through `agent_oauth.enabled()`.
- **EA-3 Settings:** `agent_edge_signing_key` and `agent_edge_signing_key_previous`, both default empty. `agent_edge_gateway_url`, default `http://127.0.0.1:8080`. §10.1 gives their rules.
- **Do not touch:** `routes/oauth.py` (a different OAuth client, retired by WS-54), `routes/integrations.py`, `integration_connections`, `outbound_guard.py`, `nav.ts` and `launch_surface.md`. Two exceptions. `CHROMELESS_ROUTES` in `nav.ts` gains `/connect` (EA-4a). The Integrations pane loses its `feature` in `nav.ts`, and its row in `launch_surface.md` §2 changes to match (EA-4b, D-EA-9). `/connect/authorize` gets no `PUBLIC_PAGES` entry in `proxy.ts`, because the page needs a signed-in member. Reuse `outbound_guard.py` and never edit it. Do not name a new module `oauth.py`.
- **Lifetimes** are `Settings` defaults, so a test can assert them.
  - Access token: 3600 seconds.
  - Refresh token: 30 days.
  - Authorization code: 60 seconds.
  - Idle connection: 90 days.
  - Unused registered client: 1 day.
- **No grace window.** A client that sends two refreshes at once loses the connection. The client must serialize its refreshes.

## 6. Decisions D-EA-1 to D-EA-9 (D-EA-1 to D-EA-8 taken, D97. D-EA-9 taken 2026-10-14)

The owner accepted every default below in chat on 2026-10-10, with the words "go ahead with your recommendations for all". Nothing here re-opens D1 to D94. D97 in work_plan.md §3 records them.

| # | Question | Accepted | Why |
|---|---|---|---|
| D-EA-1 | The edge host | `mcp.metorite.com` | No session cookie reaches it. §5.2 |
| D-EA-2 | Edge in its own unit, or a gateway router | Own unit | Smaller blast radius for the internal bearer. §5.1. Since EA3-1 the edge holds a signing key, and no bearer |
| D-EA-3 | Authorization server: a vetted library inside our code, or a hosted OAuth server | Library inside our code | We already own the member session and the tenant tables. A second identity system is a second seam |
| D-EA-4 | May a connection reach the member's personal projects | **No by default.** The consent page offers an opt-in box, off | Personal projects are private to one member. A wrong default leaks private work |
| D-EA-5 | A personal access token for headless agents (CI, a server) | Not in version 1 | An interactive OAuth flow covers Claude Code and desktop clients. A token is a long-lived secret and needs its own review |
| D-EA-6 | What "company context" means | The organization name, domain, branding, and the `org:global` memory the member may read | No company profile route exists today. A richer profile is a separate spec. See H-15 in the org profile backlog |
| D-EA-7 | Price | No charge. The agent uses the member's seat | CLAUDE.md says pricing and commercial calls need owner input first. The tool calls cost no AI credits |
| D-EA-8 | Who may connect | An organization admin switches the feature on. Then any member may connect, unless the admin restricts clients to an allow list | Default OFF keeps a new tenant safe |
| D-EA-9 | Where members and admins manage connections | **Decided: owner, 2026-10-14, in chat.** Integrations opens to all members. Each member sees an AI agents view. `integrations:manage` sees all agent connections. The two stores stay apart. §5.4 | The owner suggested it on 2026-10-11. One place to see and revoke every connection |

### 6.1 The engineering decisions of the EA-3 audit (EA3-1 to EA3-8, 2026-10-11)

A spec audit of EA-3 on 2026-10-11 gave NO-GO. The spec had no testable done-when for the authority rule or the signed header, and it left design choices open. The orchestrator took the eight engineering decisions below on the same day. None of them is a product or pricing call, and none re-opens D-EA-1 to D-EA-9. This spec writes each one into the section that it names, as a rule.

| # | Decision | Where |
|---|---|---|
| EA3-1 | The signed header, design B. The edge holds no internal bearer and sends no member header. An HMAC is its only credential, and the gateway checks it in a new branch 0 of `get_current_user` | §10.1 |
| EA3-2 | A stateless `/mcp` endpoint that answers in JSON only, written by hand in the edge. The `mcp` SDK server is not used. Revisions `2025-06-18` and `2025-11-25`. EA-3c adds `2026-07-28` | §5.3, §9.6 |
| EA3-3 | The gateway checks the seat on every call. The edge checks the audience and computes the effective scopes | §7.1 |
| EA3-4 | Pins, the personal opt-out and the seal narrow the Projects visibility seam itself, never an edge filter | §7.3 |
| EA3-5 | The seven read tools, each with its route, its seam and its output shape | §9.1, §9.4 |
| EA3-6 | `MCP_ROUTES` in `agent_oauth.py` is the allowlist, with the fence `test_mcp_edge_coverage.py`. The `skill_projects` manifest stays untouched | §9.5 |
| EA3-7 | EA-3 splits into EA-3a, EA-3b and EA-3c. No migration. No stamp of `last_used_at` | §15 |
| EA3-8 | The acceptance criteria D1 to D16 and the verification commands | §15.2, §15.1 |

**The second audit, 2026-10-13.** A spec audit of EA-3b gave NO-GO. It found two blockers in the identity fence and the version rule, two more in outage handling and untrusted text, and six gaps. The orchestrator took the decisions EA3-9 to EA3-23 on the same day. None of them is a product or pricing call. None of them re-opens D-EA-1 to D-EA-9 or EA3-1 to EA3-8.

| # | Decision | Where |
|---|---|---|
| EA3-9 | `agent_edge/agent_edge/tools.py` is the ONE response-mapping module. It is the only edge file that may hold an identity literal, such as `"email"`, and only as a key that it reads from a gateway RESPONSE. `tools.py` may not read a request header, query or form at all, and an AST check proves it. The fence keeps its refusal in every other edge module | §9.4, §15 (EA-3b-2) |
| EA3-10 | A database outage in the token lookup answers HTTP 503 with `Retry-After: 2`, never 401. `acb_common.db_busy.is_db_outage` decides | §7.1, §9.6 |
| EA3-11 | The version rule. The edge serves a request after `initialize` with no `MCP-Protocol-Version` as `2025-03-26`, with the same result shape. An explicit header must be `2025-06-18` or `2025-11-25`, or it gets 400. `initialize` never needs the header | §9.6 |
| EA3-12 | The order of the checks: flag 404, method 405, `Origin` 403, body 400, token 401 or 503, version 400, scope 403. The signing key check of EA3-18 comes right after the flag | §9.6 |
| EA3-13 | A 400, 401 or 403 body is a JSON-RPC error with `id: null`. The 401 and the 403 carry the exact `WWW-Authenticate` of §9.6 | §9.6 |
| EA3-14 | An unknown method, `server/discover` included, gets the JSON-RPC error `-32601` at once | §9.6 |
| EA3-15 | Each tool returns a pinned field list. `clickup_id`, `clickup_kind` and `lead` never leave the edge. Each string value of a custom field is `untrusted_text`. The timeline `meta` is a typed allowlist of keys | §9.1, §9.4 |
| EA3-16 | A gateway 403 is a tool error with the fixed text "This member cannot read that.". It is never the MCP `insufficient_scope` 403. A gateway 5xx or a timeout is a tool error with "Metorite is busy. Try again." | §9.6 |
| EA3-17 | 10 seconds for each gateway request, and 25 seconds for the whole tool call. `list_tasks` makes at most 11 gateway calls | §9.1, §9.6 |
| EA3-18 | When `agent_signing.signing_key()` is None, `/mcp` answers HTTP 503 with `Retry-After: 30` at the transport | §9.6 |
| EA3-19 | `list_projects` never shows a personal project, whatever `include_personal` says. No tool takes an `include_personal` input | §9.1 |
| EA3-20 | The test seam is `httpx.ASGITransport` on the gateway app, through `agent_edge_gateway_url`. The new file is `tests/unit/test_mcp_edge_protocol.py`. The edge declares `httpx` | §15.1, §15.2 |
| EA3-21 | The conformance check is an exact Inspector CLI command with an expected output | §15.1 |
| EA3-22 | EA-3b splits into EA-3b-1 and EA-3b-2 | §15 |
| EA3-23 | A registered loopback redirect URI with no port accepts any port (RFC 8252 §7.3). The new slice EA-2c builds it, before EA-4 | §10.4, §15 (EA-2c) |
| EA3-24 | `list_projects` drops each archived project. `get_project` still returns an archived project, and its pinned `archived_at` key says so. The orchestrator took this decision on 2026-10-13, at the audit of EA-3b-2 | §9.1 |

**The third audit, 2026-10-13.** A spec audit of EA-4 gave NO-GO. It narrowed the dispatch to EA-4a, the consent page, after this doc change. The orchestrator took the two decisions below on the same day. Neither is a product or pricing call.

| # | Decision | Where |
|---|---|---|
| EA3-25 | EA-4 splits into EA-4a, EA-4b and EA-4c. EA-4b and EA-4c are NOT go-live gates for a pilot. Until EA-4c, approve binds the organization of the member's current session, and the consent page names that organization. Until EA-4b, a member revokes through the client (`/oauth/revoke`), or asks an admin. The go-live note of EA-10 must list both limits | §15 |
| EA3-26 | The consent ticket lives in Redis, through the tenant-prefixed wrapper `acb_common.tenant_redis`. It adds no raw Redis client. `check` gives the ticket, and `approve` spends it with one `GETDEL` | §10.5 |
| EA3-27 | **Owner decision, 2026-10-13.** Go-live is a pilot behind an organization allowlist, Fracktal first. `AGENT_ACCESS_ORGS` holds organization ids with a comma between them, and `*` allows each organization. An empty list allows none, so the default is fail-closed. Agent access is live for an organization only when the flag is on AND the list allows it. Four doors check the list: the consent routes, the token endpoint, the bearer check of `/mcp` and branch 0 | §10.6, §15 (EA-10p) |

**The fourth audit, 2026-10-14.** A spec audit of EA-6 gave NO-GO, because the spec had no nonce, no write scope check at the gateway and no cap. It narrowed the dispatch to EA-6a. The orchestrator took the four decisions below on the same day. None of them is a product or pricing call.

| # | Decision | Where |
|---|---|---|
| EA3-28 | **The nonce.** A write carries the header `X-Agent-Nonce`, 16 random bytes in base64url with no padding (22 characters). It is a 9th line of the signed string on a request that is not GET, and only there. So the signed string of a GET stays byte-identical. The gateway runs `SET NX EX 120` on `tenant_redis.key("agent_nonce", <connection>, <nonce>)` inside `organization_scope` of the connection row's organization. A nonce that the key already holds is a replay, and it gets 401. A Redis outage gets 503 with `Retry-After: 2`. Branch 0 reads the body of a write to hash it. So the two fence layers of §10.1 each gain one name: the header `x-agent-nonce`, and one `await request.body()` in `_agent_user` for a listed write pair. A GET that reads the body stays a violation | §10.1, §15 (EA-6a) |
| EA3-29 | **The write scope and the flag.** Branch 0 admits a method other than GET only for a pair in `MCP_WRITE_ROUTES`, and only when all three hold: the connection's scopes include `projects:write`, the nonce is fresh, and the Setting `agent_access_writes_enabled` (default false) is true. Otherwise the answer is 401, with one exception since fix round 1: a SIGNED write while the flag is off gets 403 with the detail `agent_writes_disabled`, which the edge maps to a tool error. Both units read the flag through `agent_oauth.writes_enabled()`. While it is off, the edge leaves the write tools out of `tools/list`, and a call of one answers "Changes are turned off for agents in this organization." with no gateway request. While it is on, the edge refuses a write tool without `projects:write`, as the existing `insufficient_scope` 403 of check 8 | §7.1, §9.5, §9.6, §10.1, §15.3 |
| EA3-30 | **The cap.** After the nonce, the gateway runs `INCR` on the tenant key `agent_writes:<connection>`, with a life of 600 seconds from the first write. A connection makes at most 120 writes in that window. The 121st write gets 429 with `Retry-After`. The edge maps a 429 to the tool error "Too many changes. Wait a few minutes." | §9.6, §10.1 |
| EA3-31 | **The split.** EA-6a builds `set_task_status`, `add_task_reference` and the references in `get_task`. EA-6b builds `claim_task`, which needs a new add-only route `POST /projects/tasks/{task_id}/assignees/self`, because the PUT of the assignees replaces the set. EA-6c builds `add_comment`, with a required `idempotency_key`. EA-6d builds `create_task`, `update_task` and the migration for the task source `mcp` | §9.2, §15 |

## 7. The connection model

A **connection** is one agent client that one member has authorized in one organization.

| Field | Meaning |
|---|---|
| `id` | Opaque id of the connection |
| `organization_id`, `member_email` | Bound at consent. Never read from a request. The gateway re-checks the membership on every call (§7.1) |
| `client_id`, `client_name`, `client_origin` | Who the agent is. The origin comes from the verified metadata document, not from a self-reported name |
| `scopes` | The ceiling. See §8 |
| `pinned_projects` | Optional list of project ids. Empty means every project the member sees |
| `include_personal` | Opt-in flag from D-EA-4. Default false |
| `created_at`, `last_used_at`, `expires_at`, `revoked_at` | Lifecycle. A connection expires after 90 days of no use. `last_used_at` records the last token issue. EA-3 does not stamp it on a tool call (EA3-7) |

### 7.1 The authority rule

The rule is `member's live access ∩ effective scopes ∩ agent scope`. A scope never adds access. A removed grant never waits for a token refresh. The edge does the token steps and the gateway does the member steps, on every call. Neither one keeps a result for longer than the gateway's own 60 second access cache.

**At the edge (EA-3b), for each `/mcp` request:**

1. Read the bearer token. Call `agent_tokens.lookup_token` with the kind `access`. No live token gives 401.
   - **An outage is a 503 (EA3-10).** `lookup_token` raises when the database fails (`agent_tokens._run` rolls back and raises again). When `acb_common.db_busy.is_db_outage` answers yes for that error, the edge answers HTTP 503 with `Retry-After: 2`. It never answers 401 for an outage. A 401 makes an MCP client drop a good token and sign in again.
   - Any other error of the lookup gives 401, as in branch 0 (§10.1).
2. Check that `lookup.resource` equals `agent_oauth.resource()`. `lookup_token` does not check the audience, so the edge must. Another audience gives 401.
3. Read the connection row in the same transaction as the lookup. EA-3a extended `_CONNECTION_LIVE_SQL` (`agent_tokens.py:393`). `lookup_token` (`agent_tokens.py:1044`) now returns `scopes`, `pinned_projects`, `include_personal` and `client_origin`.
4. Compute the effective scopes: the token scope intersected with the connection scopes. `projects:write` covers `projects:read`.
5. Check that the effective scopes hold the scope of the tool. If not, answer 403 `insufficient_scope` (§9.6).
6. Call the gateway with a signed request (§10.1). A gateway 401 becomes an MCP 401. §9.6 maps every other gateway answer (EA3-16).

**At the gateway (EA-3a), in branch 0 of `get_current_user`:** the seven steps of §10.1. Its steps 6 and 7 are the seat check. "Seat" means that `resolve_access` returns `is_active` true, and that the member holds an active membership in the organization of the connection.

**Why the gateway checks the seat, and not the edge.** The edge may not import the Console seat reader (`tests/unit/test_console_dependency_boundary.py`). The edge may not bind a tenant (`tests/unit/test_agent_oauth_server.py:766`). The gateway already resolves both on every request.

**Not found is the one answer.** If a record is not visible, the answer is "not found", and never "forbidden". A record that a pin hides gets the same text as a record that does not exist (§9.1). This is rule R5.

### 7.2 What a connection cannot do

- It cannot create or widen another connection.
- It cannot write a grant, a role, a seat or a setting.
- It cannot see a sealed tree (D63). It cannot see a personal project unless `include_personal` is true.
- It cannot call a route that `MCP_ROUTES` does not list (§9.5).

### 7.3 The agent scope narrows the visibility seam itself (EA3-4)

The Projects visibility seam itself enforces the pins, the personal opt-out and the seal. The edge never filters rows for them. An edge filter would miss the second arm of `task_visibility_clause` (`core.py:1644`). That arm matches a task by its assignee and never reads the project. So without this rule a list would return three kinds of task that the agent must not see:

- an assigned task in a project outside the pins,
- an assigned task in a personal tree, when `include_personal` is false,
- a D63 hand-over task, which stays visible to its assignee in a sealed tree (`seal.py:25-35`).

**The shape.**

1. `UserContext` (`acb_auth/roles.py:68`) gains `agent: AgentScope | None = None`. `AgentScope` is a frozen dataclass in `roles.py:44`. It holds `connection_id`, `pinned_projects` (a tuple of ids), `include_personal` and `actor_via`.
2. Branch 0 attaches the scope last, with `dataclasses.replace`, after `_with_resolved_access` returns. No other constructor of `UserContext` has to carry it.
3. `Visibility` (`core.py:1190`) gains `agent: AgentScope | None = None`. `resolve_visibility` (`core.py:1264`) copies `user.agent` into it. `resolve_visibility_for` (`core.py:1313`) does not, because it resolves another person.
4. `Visibility.project_clause` and `task_visibility_clause` add one `AND` over both arms, and in the `unrestricted` form too. The project column must be in this set:

```
SELECT id FROM pm_projects
 WHERE organization_id = CAST(:vis_org AS uuid)
   AND sealed_at IS NULL
   [AND personal_owner IS NULL]           -- when include_personal is false
   [AND id IN (<recursive pin closure>)]  -- when pinned_projects is not empty
```

5. The pin closure starts at the pinned ids in `:vis_org` and walks `parent_project_id` down. `Visibility.params` adds `vis_pins` only when the scope has pins.
6. Use a positive `IN`. Never use `NOT IN`, because one NULL row turns `NOT IN` into "no rows match".

**Facts this rests on.** `personal_owner` is set at every depth of a personal tree (`personal.py:538-547`). A seal stamps every node of the tree (`seal.py:18-23`, `:134-156`). So a check on the task's own project is enough, and the clause needs no walk up the tree.

**When `agent` is None, the SQL is byte-identical to today.** A golden test fences it (D3 of §15.2). The hand-over tasks of D63 stay HIDDEN from an agent, even when its member is the assignee.

**What the edge never sends.** `include_triage`, `include_archived`, `archived_only` and `watching`. So an agent sees no triage-parked task and no archived task.

## 8. Scopes

Scopes are coarse on purpose. A member must be able to read the consent page in ten seconds.

| Scope | Lets the agent | Tools |
|---|---|---|
| `projects:read` | Read projects, tasks, comments and the timeline the member can see | `list_projects`, `get_project`, `list_tasks`, `get_task`, `get_task_timeline`, `search_tasks` |
| `projects:write` | Create tasks, change fields and status, comment, assign to the member, and add references | `create_task`, `update_task`, `set_task_status`, `add_comment`, `claim_task`, `add_task_reference` |
| `context:read` | Read the company context of D-EA-6 | `get_company_context`, plus the matching resources |
| `offline_access` | Receive a refresh token | none. Per the MCP spec the server does not advertise this scope as a resource need |

`projects:write` includes `projects:read`. The server must treat a broader scope as covering a narrower one.

`whoami` needs a live token and no resource scope. Its declared scope is `None`, which means "any live token" (§9.5).

Every future scope needs a spec amendment. The MCP spec says a client must treat the scope in a challenge as authoritative. We therefore challenge with the exact scope a tool needs.

## 9. The tool surface

Tool names use letters, digits and underscores. Descriptions are written for a model: they say what the tool does, when to use it, and what it returns. The voice contract in `agent_writing_voice.md` binds the text a tool returns to a human, and not the schema.

### 9.1 Reads (class A, `readOnlyHint: true`) — the EA-3 inventory (EA3-5)

EA-3 builds exactly these seven tools. Each one calls only GET routes in `MCP_ROUTES`. Each route reads through the visibility seam that the row names, so the agent scope of §7.3 applies.

| Tool | Scope | Input | Gateway route, and its visibility seam | Returns |
|---|---|---|---|---|
| `whoami` | `None` (any live token) | none | `GET /auth/me` (`routes/admin/me.py:157`, router at `:26`), plus the connection row of §7.1 step 3 | Exactly six keys. `email` comes from `/auth/me`. `organization_id`, `scopes` (the effective scopes, sorted), `pinned_projects` and `include_personal` come from the token lookup (`Caller` and `TokenLookup`, `agent_tokens.py:126`). `server_time` is ISO-8601 UTC. The edge never reads `organization.id` from `/auth/me`, because that object can be `{}` (`me.py:199`) |
| `list_projects` | `projects:read` | `parent_id?`, `text?`, `limit`, `cursor` | `GET /projects/tree` (`tree.py:253`), through `Visibility.project_clause`. The route takes no parameter. The edge flattens the tree, filters by `parent_id` and `text`, and pages it, after the gateway narrowed the rows. The route never returns a personal project (EA3-19). The edge drops each archived project (EA3-24). `parent_id`, `text`, `limit` and `cursor` are edge inputs (§9.5, rule 4) | `id`, `name`, `path` (the names from the root down), `parent_id` and `task_counts` (`tasks`, `done`, `cancelled`), plus `total` and `nextCursor`. The rules below the table say how the edge filters and orders the rows |
| `get_project` | `projects:read` | `project_id` | Five reads, each through `load_visible_project`: `GET /projects/nodes/{project_id}` (`tree.py:395`), and its `/statuses` (`admin.py:155`), `/types` (`admin.py:914`), `/fields` (`custom_fields.py:385`) and `/tags` (`tags.py:316`) | The pinned project fields, its statuses, task types, custom field definitions and tags. "Pinned output fields" below lists each key |
| `list_tasks` | `projects:read` | `project_id?`, `status_id?`, `tag?`, `assignee?`, `text?`, `limit` (1 to 100, default 50), `cursor` | `GET /projects/tasks` (`tasks.py:180`), through `task_visibility_clause`. `tag` maps to `tags`, `text` maps to `q`, and `limit` and `cursor` map to `page_size` and `page` | A page of tasks with `id`, `task_number`, `title`, `project_id`, `status`, `assignees`, `due_at` and `version`, plus `total` and `nextCursor` |
| `get_task` | `projects:read` | `task_id` | `GET /projects/tasks/{task_id}` (`tasks.py:335`), `GET /projects/tasks/{task_id}/relations` (`relations.py:193`) and, since EA-6a, `GET /projects/tasks/{task_id}/refs?refresh=false` (`refs.py:336`), each through `load_visible_task` | The pinned task fields, with its `description`, `status`, `assignees`, subtasks, links, references and `version` |
| `get_task_timeline` | `projects:read` | `task_id`, `kind?` (`all`, `comments` or `events`, default `all`), `limit` (1 to 100, default 50), `cursor` | `GET /projects/tasks/{task_id}/timeline` (`activities.py:158`), through `load_visible_task`. `kind` and the `Page` parameters pass through | Comments and events, newest first, with the pinned row fields and the `meta` allowlist, plus `total` and `nextCursor` |
| `search_tasks` | `projects:read` | `query`, `limit` (1 to 50, default 50) | `GET /projects/search` (`search.py:353`), through `task_visibility_clause`. `query` maps to `q`. The route caps the hits at `MAX_HITS`, 50 (`search.py:111`), and has no pages | Ranked hits with `id`, `task_number`, `title`, `project_id`, `project_name`, `status` and `due_at`, plus `truncated` |

**Rules for every tool.**

- **No `type`, no `updated_since`, no `project_id` on search.** No route accepts them, so EA-3 drops them.
- **`task_number`, not `key`.** A task returns its `task_number` until EA-6 decides the key format (§11).
- **`version` is `updated_at`.** It is the token that `If-Match` compares (`core.py:1809-1852`). EA-6 sends it back.
- **`status` is an object with `id` and `name`.** For `list_tasks` and `get_task`, the edge reads `/statuses` once for each distinct project on the page, inside one tool call, and keeps no cache. A task that reaches the member only as its assignee has a project the member cannot open. Its status `name` is then null.
- **References.** `task_references.md` §5 owns the table `pm_task_refs` (WS-57, migration 255). EA-6a adds the references to `get_task`, and the edge sends `refresh=false`, so no tool call reads GitHub. The rule for a task key stays in §11.
- **The edge drops the `task_count` of each tag in `get_project`.** That count covers every task of the root (`tags.py:339-344`), and the visibility seam does not narrow it.
- **One not-found text.** Any gateway 404 becomes `isError: true` with the text `Project not found.` or `Task not found.`. The text is the same whether the record does not exist, a pin hides it, or the member cannot see it.

**`list_projects` (EA3-24).** `GET /projects/tree` takes no parameter, so the edge applies these rules to the tree that the gateway already narrowed.

- **Order.** The edge flattens the tree depth-first, in the order of the route. A parent comes before its children.
- **Archived.** The edge drops each project that has an `archived_at`. It keeps a live child of an archived parent. `get_project` still returns an archived project, and its `archived_at` key says so.
- **`parent_id` input.** The edge keeps the direct children of that project in the tree.
- **`text` input.** The edge keeps a project whose name holds the text, case-insensitive.
- **`parent_id` in a row.** It is the id of the parent node in the tree. It is null when the parent is not visible, because the route then shows the node as a root (`tree.py:269-272`).
- **`task_counts`.** The counts cover the whole subtree of the project (`tree.py:375-381`), as the route gives them.
- **`total`.** The count of rows after the archived drop and the two filters.

**Pages.** Each list tool caps `limit` at 100 and returns `nextCursor` while more rows exist. Three tools have pages: `list_tasks`, `list_projects` and `get_task_timeline`. `search_tasks` caps at 50 and returns `truncated` in its place.

**The opaque cursor.** A cursor is `<payload>.<tag>`. `<payload>` is the base64url form of a JSON object with five keys: `tool`, `page`, `size`, `filter` and `connection`. `filter` is the hex SHA-256 of the sorted-key JSON of every input except `limit` and `cursor`. `<tag>` is the base64url HMAC-SHA256 of `<payload>`. Its key is the HMAC-SHA256 of `agent_edge_signing_key` over the label `cursor-v1`.

**A bad cursor.** A cursor fixes the page size, so the edge ignores a `limit` sent with a cursor. A cursor with a bad tag, another tool, another filter or another connection gives `isError: true`. A key rotation ends every open cursor in the same way, and the agent lists again. EA-3b-2 binds the tool name into the payload, so a cursor of `list_tasks` never pages `list_projects`.

**Offset pages can skip or repeat a row.** The routes page by offset. A task that changes between two calls can move across a page boundary. Each list tool description says so.

**Pinned output fields (EA3-15).** Each tool copies only the keys below from a gateway response, and drops every other key. A new column on a gateway model never reaches an agent until this list names it. In the lists, (U) marks a value that the edge wraps as `untrusted_text` (§9.4). The source models are in `gateway/routes/projects/core.py`.

- **`whoami`.** `email`, `organization_id`, `scopes`, `pinned_projects`, `include_personal` and `server_time`, and no other key. The table row of `whoami` gives the source of each key.
- **`get_project`, the project.** From `ProjectModel` (`core.py:596`): `id`, `name` (U), `description` (U), `parent_project_id` as `parent_id`, `kind`, `task_prefix` (S), `status`, `timezone` (S), `created_at`, `updated_at` and `archived_at`.
- **(S), a shape check (fix round 1).** A member writes the value, and the gateway does not shape-check it. The edge sends it as a plain string only when it fullmatches its shape. Any other value is `untrusted_text`. `task_prefix` takes `[A-Za-z0-9_-]{1,16}`, because `ProjectIn.task_prefix` takes any text (`core.py:645`). `timezone` takes `[A-Za-z0-9_+/-]{1,64}`, because only the API checks it with `ZoneInfo` and the column has no CHECK. An assignee is a plain string only for an address or for `agent:<name>`, where the name passes `acb_skills.agent_paths.AGENT_NAME_RE` (64 characters at most). A plain address is at most 64 characters in total, its local part is at most 64 characters, and its domain has at most 4 labels (fix round 2). `AssigneesIn` takes any string.
- **`get_project`, the parts.** Statuses, from `StatusModel` (`core.py:743`): `id`, `name` (U), `category`, `position` and `is_default`. Task types, from `TypeModel` (`core.py:753`): `id`, `name` (U) and `is_epic`. Custom field definitions (`custom_fields.py:385`): `id`, `field_key`, `name` (U), `description` (U), `field_type`, `options` (each item U) and `required`. Tags (`tags.py:301`): `id` and `name` (U).
- **`list_tasks` and `get_task`, the task.** From `TaskModel` (`core.py:656`): `id`, `task_number`, `project_id`, `root_project_id`, `parent_task_id`, `type_id`, `title` (U), `importance`, `leveraged`, `estimate_mins`, `start_date`, `due_at`, `completed_at`, `tags` (each item U), `created_by`, `created_at` and `archived_at`. `version` is `updated_at`. `status` is the object of the rule above. `assignees` is the list that the route adds, and each item is (S). `list_tasks` returns only the keys of its row in the table.
- **`get_task` only.** `description` (U) and `custom_fields`. `custom_fields` is a map from `field_key` to a value. A string value is U. A list keeps each number and wraps each string as U. A number, a boolean and null stay as they are. Any other shape becomes U, as its compact JSON text.
- **`get_task`, the relations** (`relations.py:193`). A subtask keeps `id`, `task_number`, `title` (U), `status_id`, `status_name` (U), `category`, `due_at` and `completed_at`. A link (`relations.py:172`) keeps `id`, `link_type`, `direction`, `title` (U), `task_number`, `status_name` (U), `category` and `completed_at`. The `id` of a link is the id of the other task (`relations.py:174`). The edge drops `link_id`.
- **`get_task`, the references (EA-6a)** (`refs.py:129`). A reference keeps `id`, `kind`, `provider`, `ref` (S), `url` (S), `label` (S), `title` (U) and `state`. A database CHECK shapes `kind`, `provider` and `state`. The shapes of (S) are these. A `url` is a plain string only when it is at most 160 characters and fullmatches `https?://[a-z0-9.-]{1,253}(:[0-9]{1,5})?([/?#][A-Za-z0-9._~%/:@!?&+=#-]*)?`. A `ref` is plain only at most 160 characters of `[A-Za-z0-9._~%/:@!?&+=#-]`. A `label` is plain only at most 64 characters, as one token of `[A-Za-z0-9._~%/:@!+=#-]` and at most two more words of letters. Any other value is `untrusted_text`. The edge drops `task_id`, `fetched_at`, `via`, `created_by`, `created_at` and `updated_at`.
- **`get_task_timeline`, a row.** From `ActivityModel` (`core.py:772`): `id`, `type`, `parent_id`, `body` (U), `created_by`, `created_at`, `updated_at` and `meta`. `meta` is the allowlist below.
- **`search_tasks`, a hit.** The keys of its row in the table, with `title` (U) and `project_name` (U).

**Dropped on purpose.** `clickup_id`, `clickup_kind`, `clickup_synced_at` and `lead` leave the edge in no tool, because no tool needs them. The edge also drops `source`, `position`, `icon`, `icon_slot`, `view_position`, `time_spent_mins`, the `merged_*` keys and the lifecycle keys of a project.

**`leveraged` stays.** `TaskModel.leveraged` (`core.py:671`) is a plain boolean about the task, and the D78 priority matrix reads it. A NULL reads as false. So `get_task` keeps it. `list_tasks` does not, because its row keeps only the keys of its table row.

**The timeline `meta` allowlist.** `core.record_activity` (`core.py:3020`) writes most rows of `pm_activities`, and `core.record_field_change` rewrites `meta` in place when it folds an edit (`core.py:3308`). Callers write more keys than these. Examples are `intake`, `source`, `source_ref`, `status_id`, `duplicate_of_task_id`, `until`, `from_project`, `from_number`, `recurrence_id`, `successor_id`, `cascade_from`, `cleared_type_id`, `dropped_custom_fields`, `import`, `reverted_activity_id`, `attachment_id`, `name`, `mime`, `size`, `removed`, `agent`, `agents` and `state`. The edge keeps only the keys below. It drops any other key, and it sends a `meta` with no kept key as null.

| Activity `type` | Kept keys, and the shape of each value |
|---|---|
| any type | `via`, a string that matches `ACTOR_VIA_PATTERN`. The edge drops any other `via`. `automation`, a boolean |
| `status_change` | `from` (U) and `to` (U), the status names. `from_category` and `to_category`, the category strings |
| `field_change` | `changes`, a list. Each item keeps `field`, a string key, and `old`, `new`, `old_label` and `new_label`, each with the custom field value rule above. For a foreign-key field, `resolve_fk_labels` (`core.py:3183-3190`) writes `old_id` and `new_id` in place of `old` and `new`. The edge keeps them as id strings, and it sends any other value as null |
| `assignment` | `added` and `removed`, lists of assignees, each item (S). `bulk`, a boolean |
| `link` | `target`, a task id. `type`, the link type |
| `merge` | `merged_into`, a task id. `merged_from`, a list of task ids |
| `mention` | `mentioned`, a list of addresses, each item (S) |
| `comment` | `author_name` (U), from a file import |

On `attachment`, `system`, `agent_run` and `sync` rows the edge keeps only `via` and `automation`. Their other keys carry file names, import text and agent state. A free-text value never passes as a bare string.

**Personal projects (EA3-19).** `list_projects` never shows a personal project. `GET /projects/tree` reads `_visible_projects`, which adds `personal_owner IS NULL` (`tree.py:246`) for every caller. So `include_personal` has no effect on `list_projects`. No tool takes an `include_personal` input. The connection field still narrows the other read tools, through the seam (§7.3).

**Gateway calls and time (EA3-17).** Each gateway request has a 10 second limit. Each tool call has a 25 second limit for all its requests.

- `whoami`, `list_projects`, `get_task_timeline` and `search_tasks` make one request each.
- `get_task` makes four: the task, its relations, its references, and the `/statuses` of its project. It made three before EA-6a.
- `get_project` makes five.
- `list_tasks` makes at most 1 + N. N is the count of distinct projects on the page, and the edge caps N at 10. The 10 are the first 10 distinct `project_id` values, in page order. The edge reads those statuses at most four at a time. A task in a project past the cap gets a status `name` of null, as a task in a project that the member cannot open does.

`get_company_context` is not in EA-3. EA-5 adds it, with the `context:read` scope (§13).

### 9.2 Writes (class B, undoable, and class C, hard to undo)

EA-6a builds two write tools (EA3-31). Each needs `projects:write`, and each write passes the nonce, the scope check, the flag and the cap of branch 0 (EA3-28 to EA3-30).

| Tool | Class | Input | Gateway route | Returns |
|---|---|---|---|---|
| `set_task_status` | B | `task_id`, `status_id`, `expected_version` | First `GET /projects/tasks/{task_id}` and `GET /projects/nodes/{project_id}/statuses`, to find the category of the target status. Then `PATCH /projects/tasks/{task_id}` (`tasks.py:484`) with the body `{status_id}` only and `If-Match: <expected_version>`. It never sends `include_subtasks` | `id`, `status` (an object with `id` and `name` (U)) and `version` |
| `add_task_reference` | B | `task_id`, `url`, `title?` | `POST /projects/tasks/{task_id}/refs?refresh=false` (`refs.py:418`) with the body `{url, title?}` | `id`, `kind`, `provider`, `ref` (S), `url` (S), `label` (S), `title` (U), `state` and `created` |

**Rules of `set_task_status`.**

- The edge refuses a target status whose category is `done` or `cancelled` (`core.py`, `CLOSING_CATEGORIES`), with the text "Moving a task to done or cancelled stays a human step." The refusal comes before the PATCH, so nothing changes.
- A target status that is not in the status set of the task's project gets the bad-request text of §9.6, before the PATCH.
- When the member cannot open the task's project, the edge cannot read the category. It then refuses with "This member cannot change that.", before the PATCH.
- `expected_version` is the `version` that `get_task` returned. The gateway compares it with `If-Match` (`core.py:1860`). A stale version gets 412, and the edge maps it to "The task changed. Read it again."
- The transition path of the PATCH stamps `completed_at` and the timeline row, and `record_activity` stamps `meta.via` with `mcp:<slug>`.

**Rules of `add_task_reference`.**

- The route is WS-57's (`task_references.md` §6 TR-1). The edge builds no table and no second store.
- An add is idempotent. A new row answers 201, and the edge returns `created: true`. A row that already holds the reference answers 200, and the edge returns `created: false`. The second add writes no activity row.
- `refresh=false` keeps the call off GitHub, so the tool keeps `openWorldHint: false`.
- `_via()` of `refs.py` keeps `mcp:<slug>` in the column `via`. The router dependency `capture_actor_via` binds it from the signed `X-Actor-Via` of the write, so branch 0 binds no second stamp.
- A 409 (a remove crossed the add) gives "The link changed while Metorite added it. Add it again." It is never the text of a 412.
- A 422 (the classifier refused the link) gives "The input 'url' is not a link that Metorite can add."
- EA-6a does not expose `DELETE /projects/tasks/{task_id}/refs/{ref_id}`.

**Annotations of a write tool.** `readOnlyHint: false`, `destructiveHint: false`, `idempotentHint: true` and `openWorldHint: false`. Both tools are idempotent: a second call with the same input changes nothing more.

⚠️ **A write can reach an outside site through a workflow (fix round 1).** The tool call itself calls no outside site, and that is all `openWorldHint: false` says. But `patch_task` emits `pm.task.updated` and `pm.task.status_changed`. Through `workflows.triggers.dispatch_event`, such an event can start a published workflow of the organization, and that workflow can send mail or call a webhook. This is the same as for a member's own change in the app. A member who connects an agent with `projects:write` lets it start those workflows.

**The writes flag (fix round 1).** While `agent_access_writes_enabled` is off, `tools/list` leaves out both write tools. A call of one answers `isError: true` with "Changes are turned off for agents in this organization." and the edge makes no gateway request. Check 8 skips such a tool, so a client never steps up for it. The gateway stays the second guard: a signed write gets 403 with the detail `agent_writes_disabled`, and the edge maps that code to the same text. A gateway 401 on a write would make a client drop a good token and lose its reads too.

**Later rows (EA3-31).**

| Slice | Tool | Class | Input | Note |
|---|---|---|---|---|
| EA-6b | `claim_task` | B | `task_id`, `expected_version` | Assigns the member and moves the task to the first "in progress" status. It needs a new add-only route `POST /projects/tasks/{task_id}/assignees/self`, because the PUT of the assignees replaces the set |
| EA-6c | `add_comment` | B | `task_id`, `body`, `parent_id?`, `idempotency_key` | Adds a comment. The author stays the member. `meta.via` names the client. The tool needs the key |
| EA-6d | `create_task` | B | `project_id`, `title`, `description?`, `type?`, `status?`, `tags?`, `due_at?`, `idempotency_key?` | Creates one task. The same `idempotency_key` returns the first result. It comes with the migration for the task source `mcp` |
| EA-6d | `update_task` | B | `task_id`, `expected_version`, fields | Changes title, description, tags, due date and custom fields. Refused with a text reason when the version is old |

The first version has **no class C tool**. A move across projects, an archive, an unassign of another person and a bulk change are not exposed. When one is added, it needs a human confirmation step. The MCP spec supports this with a multi-round input request (elicitation), and a client that lacks it must get a refusal that says so.

### 9.3 Resources

The edge also lists resources, so a client can attach context without a tool call.

- `metorite://company/context` — the D-EA-6 context.
- `metorite://project/{id}` — one project with its statuses and open task counts.
- `metorite://task/{id}` — one task.

A resource read uses the same authority rule as a tool call.

### 9.4 Tool results are untrusted text

A task title, a description and a comment come from other people. An outside agent reads them. A hostile comment can tell the agent to leak data. We cannot stop that inside the agent. We reduce the damage as follows:

- **The shape (EA-3).** Every free-text field comes back as an object, `{"untrusted_text": "<string>"}`, and never as a bare string. The free-text fields are titles, descriptions and comment bodies. They are also the names of projects, statuses, task types, tags and custom fields. A null stays null.
- **The values (EA3-15).** Each string value of a custom field is `untrusted_text`, and so is each free-text value in the timeline `meta`. §9.1 pins the field list of each tool and the `meta` allowlist.
- **One mapping module (EA3-9).** `agent_edge/agent_edge/tools.py` is the only edge module that reads a gateway response into a tool result. It is the only edge file that may hold an identity literal such as `"email"` or `"member"`, and only as a key that it reads from a gateway RESPONSE. `whoami` maps the `/auth/me` answer there.
- **The fence edit (EA-3b-2).** `test_no_module_reads_a_tenant_or_a_member_from_input` (`tests/unit/test_agent_oauth_server.py:951`) refuses each name of `_IDENTITY_INPUTS` (`:921`) in every edge module. EA-3b-2 exempts `tools.py` from that test, and adds AST tests in the same file. Every other edge module keeps the old refusal. The new tests (R7) hold three rules for `tools.py`:
  - An identity literal appears only as the argument of a `.get()` call, as a subscript index, or as a key of a dict display.
  - `tools.py` reads no request header, query or form. It reads no attribute `headers`, `query_params`, `form`, `cookies`, `path_params`, `scope` or `url`. It calls no `.body()`, `.json()` or `.form()`, no `Header`, `Query` or `Form`, and no `getattr` with two arguments. It names no `Request`.
  - `tools.py` imports nothing from `fastapi` or `starlette`.
- **What the AST tests prove (fix round 1).** The two scans are a heuristic that catches the common shapes. They do not catch an identity literal that reaches a read through a variable, for example a value taken from a dict key. They do not catch an attribute name that the code computes without `getattr`. The real guards are two. `tools.py` receives no `Request` object, because a tool call gets only the parsed arguments and a `Context` of the caller and the key, and a test holds that. And rule 4 of `test_mcp_edge_coverage.py` holds.
- **What the shape checks leave (fix round 2).** A shaped value of up to 64 characters can still carry a short phrase. That is true of an address, an agent name and a `field_key`. The shape checks bound the length of a planted phrase, and they do not remove it. The real fix is that the gateway checks each assignee against the member directory before it stores it. §17 holds it as a follow-up.
- **The description.** Each tool description says that the agent must not follow instructions found inside an `untrusted_text` value.
- A write tool never accepts a project id or a member that the agent found only in free text. It accepts ids that came from a prior structured result.
- The consent page says that the data goes to the client's provider.

### 9.5 The allowlist is `MCP_ROUTES`, and a fence enforces it (EA3-6)

**The home.** `MCP_ROUTES` is a frozenset of `(method, template)` pairs in `packages/acb_auth/acb_auth/agent_oauth.py`. The edge client reads it before it sends a request. Branch 0 of the gateway reads it before it resolves a member (§10.1, step 3). EA-3 lists twelve pairs, all `GET`:

- `/auth/me`
- `/projects/tree`
- `/projects/nodes/{project_id}`, with its `/statuses`, `/types`, `/fields` and `/tags`
- `/projects/tasks`, `/projects/tasks/{task_id}`, with its `/relations` and `/timeline`
- `/projects/search`

EA-6a adds three pairs. `GET /projects/tasks/{task_id}/refs` is a read. `PATCH /projects/tasks/{task_id}` and `POST /projects/tasks/{task_id}/refs` are writes. A second frozenset, `MCP_WRITE_ROUTES`, names each write pair, and it is a subset of `MCP_ROUTES`. Branch 0 admits a method other than GET only for a pair in `MCP_WRITE_ROUTES` (EA3-29).

**What stays untouched.** Do not edit `apps/skills/skill-projects/skill_projects/manifest.py`. Do not edit `tests/unit/test_projects_chat_coverage.py` or `tests/unit/test_projects_field_parity.py`. Do not put the edge tools into `TOOL_ANNOTATIONS` of `acb_skills`. Exclusion is the default: branch 0 refuses every route that `MCP_ROUTES` does not list.

**The tool record.** Each tool in `agent_edge/tools.py` declares `name`, `title`, `required_scope`, `inputSchema`, `outputSchema`, `annotations` and its routes. `required_scope` is a scope string, or `None` for "any live token". A read has the annotations `readOnlyHint: true`, `destructiveHint: false`, `idempotentHint: true` and `openWorldHint: false`. A write (EA-6a) declares its `method`, and it has `readOnlyHint: false` with the other three the same.

**The fence.** `tests/unit/test_mcp_edge_coverage.py` fails when any of these rules breaks:

1. Each pair in `MCP_ROUTES` is a route that the gateway serves. Its method is `GET`, or it is `PATCH` or `POST` for a pair in `MCP_WRITE_ROUTES`. `MCP_WRITE_ROUTES` holds no GET pair.
2. Each pair is class A in `MANIFEST`, or it is a named exception. EA-3 has one exception, `GET /auth/me`. A pair in `MCP_WRITE_ROUTES` may be class B. No class C or X route appears.
3. Each tool declares a `required_scope` key, the four annotations, an `inputSchema` and an `outputSchema`. A write tool has `readOnlyHint: false` and needs `projects:write`.
4. Each tool input maps to a parameter of its route. The fence reads the parameters from `route.dependant`, as `tests/unit/test_projects_field_parity.py` does: the path, query and header parameters, and the fields of each model in `dependant.body_params`. The tool record declares each map, such as `text` to `q` and `expected_version` to `If-Match`. An input that the edge applies itself is a named exception, and the tool record lists it in `edge_inputs`. EA-3 has four: `parent_id`, `text`, `limit` and `cursor` of `list_projects`, because `GET /projects/tree` takes no parameter. A new edge input needs a spec amendment.

This is R7. The rule names its fence.

### 9.6 The `/mcp` wire contract (EA3-2)

**Written by hand.** The edge holds a stateless `/mcp` endpoint that answers in JSON only, about 300 lines. It handles `initialize`, `ping`, `tools/list` and `tools/call`.

**Not the SDK server.** Do not use the server of the `mcp` SDK. The package `mcp` 1.30.0 is in `uv.lock` only as a dependency of the orchestrator. Its server refuses any `MCP-Protocol-Version` later than `2025-11-25`. The SDK may serve as a client in tests.

**The order of the checks (EA3-12, EA3-18).** The edge runs the checks of each `/mcp` request in this order. The first check that fails gives the answer, and no later check runs.

1. **Flag.** With `agent_access_enabled` off, every path and method answers 404. The ASGI middleware of EA-2a already does this.
2. **Signing key.** When `agent_signing.signing_key()` is None, `/mcp` answers HTTP 503 with `Retry-After: 30` (EA3-18). Without a key the edge cannot call the gateway, so no later check can help. This check comes after the flag and before the method, because it is a fact of the transport and not of the request.
3. **Method.** `GET /mcp` and `DELETE /mcp` answer 405.
4. **Origin.** When `Origin` is present, it must equal the origin of `agent_oauth.issuer()`. Any other origin gets 403.
5. **Body.** A body that is not JSON gets HTTP 400 with `-32700`. A JSON-RPC batch, or a body that is not a JSON-RPC message, gets HTTP 400 with `-32600`.
6. **Token.** §7.1 steps 1 and 2. A failure gets 401. A database outage in the lookup gets 503 with `Retry-After: 2` (EA3-10).
7. **Version.** The rule below. A value that the edge does not accept gets 400 with `-32600`.
8. **Scope.** A `tools/call` of a known tool that the effective scopes do not cover gets 403. A write tool while the writes flag is off skips this check, and its call answers the tool error of §9.2 (EA-6a fix round 1).

After the eight checks, the edge dispatches the method. A notification, or a JSON-RPC response that a client posts, gets 202 with no body.

**The transport.**

1. One `POST /mcp`. Each answer is `application/json`. The edge ignores the `Accept` header.
2. The edge never sends `Mcp-Session-Id`.

**The error body (EA3-13).** A 400, 401 or 403 answer carries a JSON-RPC error with `id: null`. The `code` is `-32700` for a body that is not JSON, and `-32600` for every other case. A 503 carries the same shape with the code `-32603`. A 404 from the flag and a 405 carry no rule for the body.

**The version (EA3-11).**

1. `initialize` never needs the `MCP-Protocol-Version` header. It answers with the client's `protocolVersion` when that is `2025-06-18` or `2025-11-25`. For any other value it answers `2025-11-25`.
2. A request after `initialize` with no header is a `2025-03-26` request. The edge serves it with the same result shape as the two later revisions.
3. An explicit header must be `2025-06-18` or `2025-11-25`. Any other value gets 400 with the code `-32600`. This check skips `initialize`. It covers every other method, and that includes `server/discover`.
4. `2026-07-28` gets 400 in EA-3. EA-3c adds it.
5. D8 sends `server/discover` with no header, and expects `-32601`.

**The body size.** A request body over 1 MiB gets 413 before the edge parses it. A declared `Content-Length` over 1 MiB gets the 413 before the edge reads the body. Otherwise the edge reads the stream until the total passes 1 MiB, so it holds at most 1 MiB and one more ASGI chunk.

**Authorization errors.**

1. A request with no token, or with a token that fails §7.1 steps 1 or 2, gets 401. The header is `WWW-Authenticate: Bearer resource_metadata="<issuer>/.well-known/oauth-protected-resource/mcp"`. When the request sent a token, the header also carries `error="invalid_token"`.
2. A gateway 401 gets the same MCP 401, with `error="invalid_token"`.
3. A `tools/call` of a known tool that the effective scopes do not cover gets 403. The header is `WWW-Authenticate: Bearer error="insufficient_scope", scope="projects:read", resource_metadata="..."`.
4. Only check 8 sends `insufficient_scope`. A client such as Claude Code answers that 403 with an OAuth step-up, which signs the member in again for the wider scope.

**Methods (EA3-14).** The edge serves `initialize`, `ping`, `tools/list` and `tools/call`. Any other method gets the JSON-RPC error `-32601` at once, with HTTP 200. That includes `server/discover`, the probe of the `2026-07-28` revision. The edge never holds a request open for a method it does not serve.

**Tools.**

1. `tools/list` returns the tools whose `required_scope` the effective scopes cover. The list varies only by scope.
2. A result carries `structuredContent` that matches the `outputSchema`, and one text block with the same JSON.
3. An unknown tool gets the JSON-RPC error `-32602`, with HTTP 200.
4. An input that fails the `inputSchema` returns `isError: true` with a text that names the input. A bad cursor returns `isError: true` too (§9.1).

**Gateway answers (EA3-16).** In a `tools/call`, the edge maps each gateway answer to one result. It never copies the text of a gateway error into a result.

| Gateway answer | Tool result |
|---|---|
| 2xx | The mapped result of §9.1 or §9.2 |
| 401 | The MCP 401 above. Not a tool result. A gateway 401 on a write stays the MCP 401 too |
| 403 on a read | `isError: true` with the text "This member cannot read that." |
| 403 on a write with the detail `agent_writes_disabled` (EA-6a fix round 1) | `isError: true` with the text "Changes are turned off for agents in this organization." Never the MCP 401 |
| Any other 403 on a write (EA-6a) | `isError: true` with the text "This member cannot change that." |
| No answer: a write tool while the writes flag is off at the edge (fix round 1) | `isError: true` with the text "Changes are turned off for agents in this organization." The edge sends no request |
| 404 | `isError: true` with `Project not found.` or `Task not found.` (§9.1) |
| 412 on a write (EA-6a) | `isError: true` with the text "The task changed. Read it again." |
| 429 on a write (EA-6a, EA3-30) | `isError: true` with the text "Too many changes. Wait a few minutes." |
| Any other 4xx | `isError: true` with the text "Metorite could not answer that request. Check the input." |
| 5xx, or no answer within the limit | `isError: true` with the text "Metorite is busy. Try again." |

A gateway 403 is NEVER the MCP `insufficient_scope` 403. That 403 makes Claude Code start an OAuth step-up. A step-up gives the same member the same access, so the next call fails again, and the client loops.

**Time (EA3-17).** Each gateway request has a 10 second limit, and each tool call has a 25 second limit for all its requests. When either limit runs out, the result is the busy text above. §9.1 gives the count of requests for each tool.

**What Claude Code does (2.1.296, read on 2026-10-13).**

- It registers by a Client ID Metadata Document, not by Dynamic Client Registration. So EA-2b is not on its path.
- It first posts a `server/discover` probe, the shape of the `2026-07-28` revision. When it gets the JSON-RPC error `-32601`, or an HTTP error with a JSON-RPC body, it sends a legacy `initialize` that asks for `2025-11-25`. A probe that gets no answer before the client timeout is a hard failure. So the edge answers `-32601` at once (EA3-14), and every 4xx carries a JSON-RPC body (EA3-13).
- It answers a 403 with `error="insufficient_scope"` with an OAuth step-up (EA3-16).
- In its metadata document, each redirect URI is a loopback URI with no port. The client then uses a random port. EA-2c makes the authorize check accept that (§10.4).

## 10. Security

### 10.1 The signed request replaces the internal bearer (EA3-1)

**The sharpest edge.** The gateway trusts `Authorization: Bearer <internal token>` plus `X-User-Email` (branch 1a, `deps.py:732`). Any holder of that bearer can act as any member.

**Why the edge holds no bearer.** The first draft gave the edge that bearer plus an HMAC header for `mcp:*` calls. That control did not limit a stolen bearer. A thief leaves out `X-Actor-Via` and uses branch 1a. So the edge holds **no** bearer, and an HMAC is its only credential.

**What the edge sends.** The edge calls the gateway at `agent_edge_gateway_url` (default `http://127.0.0.1:8080`). It sends no `Authorization` header and no `X-User-Email`. It sends four headers:

| Header | Value |
|---|---|
| `X-Agent-Connection` | `<organization uuid>.<connection uuid>`, from the token lookup |
| `X-Agent-Timestamp` | The Unix time in whole seconds |
| `X-Actor-Via` | `mcp:<slug>`, the value of `agent_oauth.actor_via_for(client_origin)` |
| `X-Agent-Signature` | `v1,<kid>,<hex>` |

**The signed string.** `agent_signing.py` builds it, once, for both sides. The signature is the lower-case hex HMAC-SHA256 of these lines, joined by `\n`, with the UTF-8 bytes of the key:

```
v1
<timestamp>
<organization uuid>
<connection uuid>
<X-Actor-Via value>
<METHOD, upper case>
<path>?<sorted query>
<hex SHA-256 of the body>
```

- The path is the URL path as sent. The gateway reads it from the ASGI scope, `scope["path"]`.
- The query is its pairs, sorted by key and then by value, and encoded with `urllib.parse.urlencode`. The gateway parses `scope["query_string"]` with `keep_blank_values=True` and encodes it again the same way. With no pairs, the line is the path alone, with no `?`.
- The gateway never reads `request.url`, because Starlette builds it with the `Host` header. Branch 0 reads no host input. The fence is a source scan in `tests/unit/test_tenant_request_binding.py`.
- The gateway refuses a query with a repeated key. Starlette keeps the last value of a key, and the signed string sorts every pair. So a replay with the pairs in another order would read another value under the same signature. The edge cannot sign such a query either.
- The gateway refuses a query that is not ASCII, or whose `%XX` sequences do not decode as UTF-8. Then `%FF` and `%FE` cannot share one signature.
- A GET has an empty body, so its hash is the SHA-256 of zero bytes.
- A write (EA-6a, EA3-28) adds a 9th line, the value of `X-Agent-Nonce`, after the body hash. A GET has no 9th line, so its signed string is the eight lines above, byte for byte. The body line of a write is the SHA-256 of the body bytes as sent.

**The keys.**

- Two Settings hold them: `agent_edge_signing_key` and `agent_edge_signing_key_previous`. Each one must be at least 32 bytes.
- The gateway refuses a key equal to `GATEWAY_INTERNAL_TOKEN` or to `LITELLM_MASTER_KEY`. It treats a refused or a short key as unset.
- The `kid` is the first 8 hex characters of the SHA-256 of the key. The gateway picks the key whose `kid` matches. The edge signs with `agent_edge_signing_key` only.

**Rotation.**

1. On the gateway, put the old key in `agent_edge_signing_key_previous` and the new key in `agent_edge_signing_key`.
2. Restart the gateway.
3. Set the new key on the edge, and restart the edge.
4. Clear `agent_edge_signing_key_previous` on the gateway, and restart the gateway.

**Provisioning.** To set the key on both units is an `env-write` act. D97 makes it an owner gate, and it is part of EA-10 (HANDOFF H-303). Until then the feature is inert, because branch 0 refuses every request while no key is set.

**Branch 0 of `get_current_user` (EA-3a).** `get_current_user` (`deps.py:669`) gains a keyword parameter `request: Request = None`. FastAPI injects it.

**The callers that send no request.** The ten direct callers in three test files pass no request, and they keep working. They are in `test_service_identity_and_webhook_auth.py`, `test_tenant_request_binding.py` and `test_usage_attribution.py`. When no request arrives, branch 0 does not run. That covers those callers, and a WebSocket route, where FastAPI injects no `Request`.

**The trigger.** Branch 0 runs before branch 1 when the request carries any header whose name starts with `X-Agent-`. It also runs when `X-Actor-Via`, after strip and lower case, starts with `mcp:`.

**The seven steps, in the order the code runs them.** Any failure returns the anonymous context with `NO_ACCESS`, so default-deny answers 401.

1. The flag `agent_access_enabled` is on, and a usable key is set.
2. The `kid` names a usable key, and `hmac.compare_digest` matches the signature. The timestamp is within 60 seconds of the gateway clock, before or after.
3. The pair of the method and the matched route template (`deps._matched_template` at `deps.py:959`, which has no URL fallback) is in `MCP_ROUTES`. Until EA-6a the method had to be `GET`. Since EA-6a a method other than GET needs a pair in `MCP_WRITE_ROUTES`, a nonce and the read of the body before the signature check. After this step, a signed write while the writes flag is off gets 403 with the detail `agent_writes_disabled` (fix round 1). After the stamp check it needs `projects:write` on the connection, a fresh nonce and room under the cap (EA3-28 to EA3-30).
4. A function in `agent_tokens.py`, `read_live_connection`, reads the connection by its id. It reuses `_session` and `_BIND_SQL`, and it binds the signed organization. The connection must be live: not revoked, and not past `expires_at`. The read returns the organization of the ROW, and that organization must equal the signed one.
5. `X-Actor-Via` must equal `agent_oauth.actor_via_for(client_origin)` for that row, and match `ACTOR_VIA_PATTERN`.
6. The member email comes from that row, never from a header. Branch 0 then calls `_with_resolved_access` with that email.
7. The resolved organization must equal the organization of the row, and `access.is_active` must be true.

**Why this order.** The first draft checked the stamp last. The code checks the stamp and the two organization checks before the member resolve, because the resolve binds a tenant. So a request that fails on a stamp or an organization never binds one. A refusal after the resolve also clears the tenant it bound.

**An outage is a 503.** A database outage answers the 503 of `_with_resolved_access`, with `Retry-After: 2`. It never falls through to another branch, whatever bearer or `X-User-Email` the request also carries. A 401 there would make an MCP client drop a good token and sign in again.

- **Step 4, the connection read.** `acb_common.db_busy.is_db_outage` decides. It is the one list, and the role read uses it too. It answers yes for `acb_common.db_busy.is_db_unavailable`. That covers the pooler's `EMAXCONNSESSION`, `TooManyConnectionsError`, `CannotConnectNowError` and `ConnectionDoesNotExistError`. It also covers the "connection refused" family of messages, and SQLSTATE class 08, 53300 and 57P01 to 57P03. It also answers yes for `db_busy.is_db_timeout`, a pool or connect timeout. It also answers yes for an `OSError` anywhere in the cause chain, `ConnectionRefusedError` included, because asyncpg raises the bare socket error when a connect fails. Every other failed read is a refusal, a 401.
- **Step 6, the member resolve.** A pool or connect timeout raises `IdentityUnavailable` in `acb_auth.access`, and `_with_resolved_access` answers the 503. The identity read records any other failure in `identity_read_failed()`, the flag that `gateway.main` reads to answer 503 for an unbound tenant. When the seat check would refuse and that flag is set, branch 0 answers the 503.
- **Step 6, the role read.** When the role read alone hits an outage (`db_busy.is_db_outage`) while the identity read works, `resolve_access` still answers no access. It also records the outage in `role_read_failed()`, and only branch 0 reads that flag. When the seat check would refuse and the flag is set, branch 0 answers the 503. `gateway.main` and the WhatsApp channel read `identity_read_failed()` only, so their answers do not change.
- **What stays a 401.** A role read or a connection read that fails with an error that is not an outage, such as a `ValueError`. A cache hit reads nothing and records nothing.

**Process control. The automatic checks are a backstop, not a proof.** Neither of them stops deliberate code. Any change to branch 0 needs an adversarial review (diff-reviewer) AND an independent verification before it merges.

**The digest test.** `test_branch_zero_digest_is_pinned` pins a SHA-256 of the normalised source of each scanned function, `agent_signing.py` and `_branch_zero_tripwire.py`. It holds one pin for each Python minor version, because `ast.dump` changes between versions. A version with no pin fails with its own message. So any change fails the test, whatever its shape. The commit that updates the digest carries the review and the verification.

**What branch 0 is.** Branch 0 is these functions of `deps.py`: `get_current_user`, `_is_agent_request`, `_one_header`, `_verify_signed_request`, `_agent_user`, `_matched_template` and `_effective_template`. The same rule binds `agent_signing.py`, the `Visibility` narrowing in `core.py`, and the fence files `test_tenant_request_binding.py`, `_branch_zero_tripwire.py` and `test_mcp_edge_signing.py`.

- The static scan refuses a list of construct classes in each scanned function. They are a nested function or class, a lambda, `match`, `global`, `nonlocal` and a chained comparison. They are also a dunder, frame, traceback, generator or coroutine attribute, and a closure cell. They are also any name that starts with `__`, and any attribute named `format`, `format_map`, `vformat`, `get_field` or `format_field`, which closes `str.format` and `string.Formatter`. They are also the introspection names `sys`, `inspect`, `gc`, `traceback`, `operator`, `functools`, `builtins`, `importlib`, `ctypes` and `string`. The last group is `object`, `type`, `vars`, `globals`, `locals`, `setattr`, `delattr`, `getattr`, `__import__`, `eval`, `exec`, `compile` and `dict`. Ten `getattr` calls of the real code are on an allow-list, each with its reason. The scan also refuses every use of `request` outside a few exact shapes, and every rebind of a trusted name.
- Module checks pin each scanned function to one plain def in its own file, with no wrapper. They pin `agent_signing` to its one import in `deps.py`.
- The runtime tripwire records the reads that go through the wrapped request, on the paths that the tests run. A violation that the code catches is still recorded, and the teardown fails on it.
- **Known gaps.** A walk up the caller frames on a path outside the refused constructs, for example from a helper outside the scan. A closure cell reached through a helper that the scan does not cover. `gc`. The digest test and the review rule are the guard against these, not the scan or the tripwire.

On success, branch 0 attaches the `AgentScope` of §7.3. It does not call `_bind_session_member`, because no route in `MCP_ROUTES` calls a model.

**`actor_via_for`.** EA-3a adds it to `agent_oauth.py`. It returns `mcp:` plus the lower-case ASCII host of the origin, with no scheme and no port. When that value does not match `ACTOR_VIA_PATTERN`, it returns `mcp:` plus the first 16 hex characters of the SHA-256 of the origin. An empty `client_origin` fails step 5.

**One copy of the pattern.** `acb_auth` may not import the gateway. So `ACTOR_VIA_PATTERN` moves to `agent_oauth.py:388`, and `core.py:47` imports it from there. One copy stays.

**Never a fall-through.** Branch 0 never falls through to branch 1a, 1b or 2. These all fail with 401:

- an `mcp:*` request that carries a valid internal bearer and `X-User-Email`,
- the same request with `GATEWAY_TRUST_UNVERIFIED_SSO_HEADERS=1`,
- the same request on a box where the internal token falls back to the LLM key.

**Replay.** A replay inside the 60 second window repeats an identical GET, and it changes nothing. A write needs more, so EA-6a adds the nonce of EA3-28. The gateway keeps each nonce for 120 seconds, twice the skew of the timestamp. So a replay of a write inside its window always finds its nonce, and it gets 401.

**The HMAC is the boundary.** The Caddy block of `api.metorite.com` proxies every request to the gateway with no header strip (`deploy/hostinger/caddy/Caddyfile:47-51`). So branch 0 is reachable from the internet. Only the HMAC stands between a stranger and the identity of an agent.

**No `guard-write`.** `plan-guard.mjs` protects no file in `acb_auth`. Its protected paths are `.env` files, `deploy/`, the guard itself and `settings.json`. So branch 0 needs no `guard-write` grant. It needs the adversarial review of EA-11.

**The other controls.**

1. The edge never copies a header from the client request into a gateway request. The gateway reads the member from the connection row.
2. The edge unit runs in its own sandbox, with no read access to `.env` beyond its own variables. EA-10 builds the sandbox.
3. The Caddy block of `api.metorite.com` strips `X-User-Email`, `X-User-Role` and `X-Actor-Via` from public requests. On 2026-10-11 it does not. The change edits `deploy/`, so it is an owner gate, and H-303 carries it.
4. Before the edge goes live, a check proves that the box holds a value for `GATEWAY_INTERNAL_TOKEN`. The edge no longer holds the bearer, and EA-10 still needs this proof. The gateway itself falls back to the LLM key when that token is unset (`deps.py:104-149`).

### 10.2 Threat table

| Threat | Control |
|---|---|
| Stolen access token | Opaque token. Short life (proposed 1 hour). Bound to one resource. Revocable at once |
| Stolen refresh token | Rotation on every use. A reused token revokes the whole connection |
| Token for another service replayed here | Audience check on `resource`. The edge refuses any other token |
| The edge replays a client token to the gateway | Never. The edge calls the gateway with the signed headers of §10.1 only. It never forwards the client token, and it sends no member header |
| A stolen signing key | The thief can act only as a member who holds a live connection, because the gateway reads the member from that row. It reaches the GET routes of `MCP_ROUTES`. Since EA-6a it also reaches the write pairs, for a connection that holds `projects:write`, while the writes flag is on. A nonce stops a replay, not a forgery, and the cap of EA3-30 bounds the damage. A rotation (§10.1) ends it |
| A holder of the internal bearer forges an `mcp:*` stamp | Branch 0 refuses the request, and never falls through to branch 1a (§10.1) |
| Consent phishing (a fake client with a trusted name) | Show the verified origin from the client metadata. Show the redirect host. An admin may restrict to an allow list (D-EA-8) |
| Open registration abuse | Client ID Metadata Documents need a fetch with a size, time and address limit (no private addresses). Dynamic registration is rate limited and expires unused clients after one day |
| Confused deputy | One consent per client and per member. State and PKCE are required. `iss` is checked by the client, and we send it |
| A write by a read-only connection | Scope check before the call, and the fence in §9.5 |
| Over-reading by a broad grant | Pins and the personal-project opt-in, inside the visibility seam (§7.3). A 100-row cap, and a per-connection quota |
| Prompt injection from a task text | §9.4 |
| Cross-tenant read | The edge never takes the organization from the request. The gateway binds the tenant from the member (R5, R11), and branch 0 checks it against the signed organization. Fence: EA-9 |
| Runaway agent loop | Rate limits, and an idempotency key on create |

### 10.3 Tenancy of the new tables

The token lookup happens **before** the tenant is known. A row-level-security table cannot serve it. The same problem exists for `user_identity` and `org_membership`, which are RLS-exempt.

- `agent_token` holds the access tokens, the refresh tokens and the authorization codes, told apart by a `kind` column. A SHA-256 hash is the lookup key. The table carries `organization_id` with a foreign key to `organization`. The lookup returns the connection id **and** the organization id, so the edge can bind the tenant before it reads anything else.
- `agent_token` sits in `gen_tenant_migration.EXEMPT` with that reason. It sits on the tenant plane as an exempt table, as `186_auth_email_otp_token.sql` does.
- `agent_connection` is tenant-scoped. After the lookup, the edge binds the tenant and then reads it under RLS.
- `agent_client` (a cache of client metadata) holds no tenant data. It is global and exempt.
- Two new exempt entries take the map from 13 to 15. `tests/unit/test_tenant_coverage.py` caps the map at 15, so this spec adds no third entry. A code in its own table would have made 16.
- Every table carries `organization_id` where it holds tenant data, and `tests/unit/test_tenant_coverage.py` must pass (R5).

### 10.4 A loopback redirect on any port (EA3-23, slice EA-2c)

**The problem.** The orchestrator fetched the Claude Code metadata document on 2026-10-13, at `https://claude.ai/oauth/claude-code-client-metadata`. Its `redirect_uris` are `http://localhost/callback` and `http://127.0.0.1/callback`, with NO port. The client really sends `http://localhost:<random port>/callback`. Before EA-2c, the authorize check of EA-2a matched the redirect URI exactly, as a string, so it refused Claude Code. EA-2c replaces that test with `agent_oauth.redirect_uri_matches` (`gateway/routes/agent_access.py:349`).

**The rule.** RFC 8252 §7.3 says that a server must allow any port on a loopback redirect. The authorize check accepts a requested URI against a registered URI when ALL of these hold:

1. The registered URI has the scheme `http`, a host of exactly `localhost`, `127.0.0.1` or `[::1]`, and no port.
2. The requested URI has the same scheme, the same host and the same path.
3. The requested URI has a port from 1 to 65535. The match compares RAW strings: the requested `netloc` must equal the registered `netloc`, a colon, and `str(port)`. So the host keeps its case and its form, and a port has no leading zero. It has no user name and no password. The exact string match runs first, so a request with no port against a registration with no port still passes.
4. The query of the two URIs is identical, as a string. The requested URI has no fragment.

Every other registered URI keeps the exact string match. An `https` loopback URI keeps the exact match too. `agent_oauth.is_allowed_redirect_uri` (`agent_oauth.py:239`) still runs on the requested URI.

**The code binds the exact URI.** `mint_code` stores the redirect URI that the request used, with its port. The code spend matches that string exactly (`agent_tokens.py:293`). So the token request must present the same string, port included. The rule widens the authorize check only.

**Where it lives.** One function in `agent_oauth.py`, beside `is_allowed_redirect_uri`, decides the match. `agent_access._validate` calls it in place of the `in` test. No other module compares redirect URIs.

### 10.5 The consent page (EA-4a)

The consent page is `/connect/authorize` on the control plane. A member sees it when an outside agent asks to connect. EA-4a builds it, dark, behind the flag of §5.5.

**The flow.** Each hop is a rule.

1. The agent reads the authorization server metadata. Its `authorization_endpoint` is `https://app.metorite.com/connect/authorize` (`agent_edge/metadata.py`).
2. `proxy.ts` sends a signed-out member to `/signin?callbackUrl=<path and query>`. After the sign-in, the browser comes back to the same URL, query included.
3. The page sends the query to the BFF route `POST /api/agent-access/authorize/check`. The BFF calls the gateway route `POST /agent-access/authorize/check` as the member.
4. On Approve, the page sends the request, the member's choices and the consent ticket to `POST /api/agent-access/authorize/approve`. The gateway answers JSON `redirect_to` with HTTP 200, never a 3xx.
5. The page navigates with `window.location.assign(redirect_to)`. On Deny, it navigates to `deny_redirect_to` from the check answer.

**The BFF routes and the CSRF rules.**

- The BFF has two routes, `POST /api/agent-access/authorize/check` and `POST /api/agent-access/authorize/approve`. The gateway has no deny route, so the BFF has none.
- Each BFF route refuses with 403 a request whose `Sec-Fetch-Site` is not `same-origin`. A request with no such header gets 403 too. The check is `sameOrigin` in `lib/accountsServer.ts`.
- The gateway does not check `Origin`, because it sees only the server call of the BFF.
- The session cookie stays `SameSite=Lax`, the Auth.js default. A test asserts it.
- No BFF route answers with a 3xx. The BFF does not follow a redirect from the gateway, and it turns a gateway 3xx into a 502.
- Each response of `/connect/*` carries `Content-Security-Policy: frame-ancestors 'none'` and `X-Frame-Options: DENY`. So no other site can put the page in a frame.

**The consent ticket (EA3-26).** The ticket proves that the member saw the screen before the approval.

- `check` returns `consent_ticket`, 32 random bytes in base64url.
- The gateway keeps only the SHA-256 of the ticket, as a Redis key, with a life of 300 seconds. The key goes through `acb_common.tenant_redis`, so it carries the organization. The wrapper gains the command `getdel`. EA-4a adds no raw Redis client.
- The ticket binds the member, the organization, `client_id`, `redirect_uri`, `code_challenge`, `state`, `resource` and `scope`. `scope` is the requested scope string, not the member's choice.
- `approve` spends the ticket with one `GETDEL`, before every other check of the request. So a ticket that `approve` reads is gone, whatever the result.
- A missing, spent, expired or unmatched ticket gives 400 `invalid_request`, with no `redirect_to`.
- A Redis outage gives 503 with `Retry-After`, never 400. This holds for `check` too, because `check` cannot give a ticket without Redis.
- Redis can spend a ticket and then lose the reply. So after a gateway 503 on `approve`, the page calls `check` again before the retry. It uses the fresh ticket only when the fresh screen is the same (`sameScreen` in `consent.ts`). A 503 from that `check` allows one more try.
- ⚠️ The retry applies to a JSON 503 from `approve` ONLY. Every such 503 comes before `create_connection`. After a thrown fetch, an answer that is not JSON, or a 2xx with no `redirect_to`, the gateway may have made the connection. So the page ends the flow, and a retry cannot make a second connection.

**What the screen shows of the redirect.** `AuthorizeView.redirect_display` replaces `redirect_host`.

- For `http` and `https`, it is `scheme://host:port`. The port comes from the full redirect URI of the request, not from the registered URI. A URI with no port shows `scheme://host`.
- For any other scheme, it is `scheme://authority`, or `scheme:` when the URI has no authority. Then `redirect_app` is true, and the page adds the label "an app on this computer".
- So the screen shows `x-evil://claude.ai/cb` as `x-evil://claude.ai`, and never as `claude.ai`.
- The page names the organization of the session. It reads the name from `GET /api/auth/me`, through `useAccess`. Approve binds the tenant of the same session (EA3-25), so the page keeps Approve off until it can name the organization.

**The redirect forms (RFC 8252).** `is_allowed_redirect_uri` changes from a deny list to an allow list. It accepts three forms, and no other:

1. `https`, with a host.
2. Loopback `http`, to `localhost`, `127.0.0.1` or `[::1]`. EA-2c (§10.4) keeps its match on any port.
3. A private-use scheme that holds a dot, in the form `scheme:/path` (RFC 8252 §7.1). An example is `com.example.app:/oauth/callback`. The scheme is lower case, the path starts with one slash, and the URI has no authority.

`redirect_uri_matches` stays the one comparison, and EA-4a does not change it. The check refuses a scheme with no dot, and a private-use URI with an authority.

**What the page shows and takes.**

- The headline names the VERIFIED host, for example "claude.ai wants to connect to Metorite". The `client_name` of the document is only a claim. The page shows it second, as `It calls itself: "<name>"`, or not at all.
- `_clean_name` refuses a name with no visible letter or digit, such as a name of Hangul fillers (U+3164, U+115F). The view then carries no name.
- It also shows the verified origin, `redirect_display`, the organization name and each requested scope.
- It takes a checkbox for each scope. A member can clear a box and never add a scope. The member cannot clear `offline_access`, so it shows with no box.
- It takes a project pin picker. The picker lists up to 50 visible projects that are not personal. No pin means every project the member sees (§7).
- It takes `include_personal`, a checkbox that is off by default (D-EA-4).
- It has an Approve button and a Deny button. Deny navigates to `deny_redirect_to`.
- ⚠️ **The approve gate (double-clickjacking).** An attacker's page can move its opener to this page between the two clicks of a double-click. So Approve stays off until the page is visible and has focus, and then for 500 ms more. A `blur` or a change to hidden starts the wait again. The click checks the gate again. Fences: `consent.test.ts` and `e2e/connect-authorize.spec.ts`.

### 10.6 The organization allowlist (EA3-27, slice EA-10p)

The flag of §5.5 is global, so when it is on, each organization can connect an agent. The owner wants a pilot. A second setting limits agent access to the organizations that it names.

**The setting.** `AGENT_ACCESS_ORGS` is the Setting `agent_access_orgs`, a string with the default `""`.

- It holds organization ids with a comma between them. The parser removes the spaces around each id and compares the ids in lower case.
- `*` allows each organization.
- An empty value allows no organization. This is fail-closed: with the flag on and no list, no organization can connect.
- The parser ignores an entry that is not a UUID and not `*`. Such an entry allows nothing.
- `acb_auth.agent_oauth.org_allowed(organization_id)` is the one reader. It does not read the flag. Agent access is live for an organization only when `enabled()` and `org_allowed()` are both true.

**The four doors.** Each door takes the organization from the session or from a token row, never from request input.

1. **The consent routes.** `check` and `approve` refuse a session whose organization is not on the list. The refusal comes before the client fetch, the consent ticket and the code. The answer is 403 with the error `org_not_enabled`. The consent page shows "Your organization has not turned on agent connections yet."
2. **The token endpoint.** The store refuses a code exchange and a refresh for an organization that is not on the list, and the edge answers `invalid_grant`. The refresh check comes before the spend, so a refused refresh spends nothing.
3. **The bearer check of `/mcp`.** A token whose organization is not on the list counts as no live token. The answer is 401 with `invalid_token`.
4. **Branch 0 of the gateway.** `agent_tokens.read_live_connection` finds no connection for an organization that is not on the list. Branch 0 then refuses the request as it refuses a dead connection. The code of branch 0 does not change.

**What the list does not stop.** `/oauth/revoke` still revokes a token of an organization that is not on the list, because a revoke only removes access.

**Both units read it.** The gateway checks doors 1 and 4, and the edge checks doors 2 and 3. So the gateway unit and the edge unit must hold the same value. A change of the value is gate `enforcement-flip`. When an id leaves the list, the next call of that organization gets 401 and its next refresh gets `invalid_grant`.

⚠️ **A unit reads the list once, at process start.** `get_settings` is `lru_cache`d, so an edit of the env file changes nothing in a running unit. To change the list:

1. Set the same `AGENT_ACCESS_ORGS` in the env of the gateway and of the edge.
2. Restart the gateway (`acb-gateway`).
3. Restart the edge (`acb-agent-edge`).

Until both units restart, the old list stays in force on the unit that did not restart.

**A replay still revokes.** The refresh path runs the reuse check before the list check (fix round 1). So a replay of a spent refresh token revokes its connection, also when the organization is off the list.

## 11. Schema addition: task references

**The table is WS-57's.** `task_references.md` §5 owns `pm_task_refs` (migration 255), its route and the task card. This spec proposed the table, and WS-57 built it on 2026-10-13. EA-6a calls its route and builds no second store (§9.2). The task source `mcp` moves to EA-6d (EA3-31).

**A human-readable task key.** A commit message or a pull request title should carry the task key, so a person can read the link. Until EA-6, `get_task` returns `task_number` and no `key` (§9.1). The survey did not confirm the key format, so EA-6 must read `pm_tasks.task_number` and the project key rules first.

## 12. The coding-agent scenario, end to end

Project 7 holds the backlog. The member connects Claude Code once. The steps below show the calls.

1. **Connect.** The member runs the client's "add MCP server" command with `https://mcp.metorite.com/mcp`. A browser opens on `app.metorite.com/connect/authorize`. The member signs in, picks `projects:read`, `projects:write` and `offline_access`, pins project 7, and approves.
2. **Find work.** The agent calls `list_tasks` with `project_id=7` and the `status_id` of the "To do" lane. It reads one task with `get_task` and notes the `version`.
3. **Start.** The agent calls `claim_task`. The member becomes the assignee. The status moves to "in progress". The timeline reads "moved by <member> through mcp:claude-code".
4. **Work.** The agent works in its own checkout. Metorite sees none of it.
5. **Report.** The agent calls `add_task_reference` for the branch, then for each pull request. It calls `add_comment` with a short summary.
6. **Finish.** The agent calls `set_task_status` with the "in review" status. A person merges. In version 1 the move to "done" stays a human step.
7. **Revoke.** The member removes the connection in Settings. The next call gets 401.

If the version changed in between, `update_task` and `set_task_status` refuse with a text that says "the task changed, read it again". The agent reads and retries.

## 13. The landing-page scenario

The member connects Claude with `context:read` only. Claude calls `get_company_context` and reads the resource `metorite://company/context`. Claude writes the page in its own window. Metorite stores nothing and holds no Claude key. A second connection with `projects:read` is needed to let Claude read the product backlog. The consent page makes that second grant a separate choice.

D-EA-6 limits what this returns today. The tree has no company profile route. Until a profile exists, Claude gets the name, the domain, the logo and the shared memory. The org profile backlog (H-15) is the real fix.

## 14. Later work, named so it is not built by accident

- A GitHub App that links pull requests to a task key by webhook, with no agent call.
- An outbound webhook when a task changes, so an agent can wait for work.
- A personal access token and a device flow (D-EA-5).
- A class C tool with a human confirmation step.
- Email and CRM tools through each module's own agent (D91.4), and never as a direct read.
- Metering a connection against a quota that an admin sets.
- The outbound twin: Metorite starts a Copilot, Jules or GitHub-based session for a task. `external_agent_dispatch.md` (WS-56) owns it. A run can report back through a run-scoped connection of this spec (DX-10).

## 15. Slices (every slice ships dark)

Each slice is **AGENT-SAFE** unless it says **OWNER-GATE**. An agent builds the code. The owner does the act that touches DNS, `.env`, a deploy credential or a live organization.

| # | Slice | Done when | Gate |
|---|---|---|---|
| EA-0 | **Owner decisions.** The owner answers D-EA-1 to D-EA-8 | **DONE 2026-10-10.** The answers are in §6, with D97 and the WS-55 row in work_plan.md | OWNER-GATE |
| EA-1 | **BUILT 2026-10-11, dark.** **Tables and token store.** One expand-only migration: `agent_connection` (tenant-scoped), `agent_token` (RLS-exempt, with a `kind` of `access`, `refresh` or `code`) and `agent_client` (RLS-exempt). A store module in `packages/acb_auth/acb_auth/agent_tokens.py`. The module keeps only the SHA-256 of a random opaque token. Rotation uses one `UPDATE ... WHERE used_at IS NULL RETURNING`. No grace window | `tests/unit/test_agent_connection_tokens.py` runs on a real database **as the non-privileged role, on the promoted catalog**. It proves: a lookup by hash works with no tenant bound and returns the connection id and the organization id. An unbound read of `agent_connection` returns zero rows. A reused refresh token revokes the connection and every token of it. A code works once. Two concurrent refreshes revoke the connection | AGENT-SAFE |
| EA-2a | **BUILT 2026-10-11, dark.** **Authorization server core, backend only.** The public half runs in the edge unit `apps/services/agent_edge/`: the metadata documents, `token` and `revoke`. The authenticated half is `apps/services/gateway/gateway/routes/agent_access.py` under `/agent-access`: validate an authorize request, then approve it and mint a code. It also holds the CIMD fetch, through `outbound_guard.py` | `tests/unit/test_agent_oauth_server.py` runs in process and proves each of these. The two metadata documents carry `issuer`, `authorization_endpoint`, `token_endpoint`, `revocation_endpoint`, `code_challenge_methods_supported` of `["S256"]`, `client_id_metadata_document_supported`, `resource` and `authorization_servers`. The server refuses an authorize request with no PKCE or with `plain`. It refuses a token request with a wrong verifier, resource, redirect URI or client. That refusal spends nothing, because the code spend matches all four values (fix round 1). It refuses a code used twice and a token in a query string. The redirect carries `iss`. A CIMD URL that resolves to a private address, a body over the cap or a slow host is refused. `revoke` answers 200 for an unknown token. With the flag OFF every new route answers 404. `tests/unit/test_default_deny_auth.py` stays green | AGENT-SAFE |
| EA-2b | **Dynamic client registration.** A fallback for clients with no CIMD support. A Postgres counter on the row limits it, as `186_auth_email_otp_token.sql` does for the one-time code. Unused clients expire after one day | The test shows the limit, the expiry and a 404 with the flag OFF | AGENT-SAFE |
| EA-2c | **MERGED 2026-10-13, dark.** **A loopback redirect on any port (EA3-23).** The match rule of §10.4, in `agent_oauth.py`, called from `agent_access._validate`. It changes a security path, so it needs the full review loop: an independent verification and an adversarial review before it merges. Claude Code needs it, because its metadata document lists loopback URIs with no port | New tests in `tests/unit/test_agent_oauth_server.py`, each red first under a named mutation. These pass: a request with a port against a registration with no port, for `localhost`, `127.0.0.1` and `[::1]`. These fail: another host, path or scheme. `http://localhost.evil.com/callback`. The user-name form `http://localhost@evil.com/callback`. An `https` loopback request against an `http` registration. A query that differs, and a fragment. A token request whose redirect URI has another port than the authorize request. A registration that is not loopback still needs the exact string. These fail too: the host `LOCALHOST`, `localhost.`, `0.0.0.0`, `127.0.0.2`, `[0:0:0:0:0:0:0:1]`, `[::ffff:127.0.0.1]`, a host with a percent escape, the ports `0`, `65536` and `05555`, and an empty port (`localhost:`). This passes: a request with no port against a registration with no port. A test fixture holds the Claude Code metadata document, as JSON fetched on 2026-10-13 | AGENT-SAFE |
| EA-3a | **MERGED 2026-10-13 (#946, `d5d8244f5`), dark, after fix rounds 1 to 5.** **The gateway half of the read tools.** Branch 0 in `acb_auth/deps.py` (§10.1). `UserContext.agent` and `AgentScope` in `roles.py`, and the `Visibility` narrowing in `gateway/routes/projects/core.py` (§7.3). The new `packages/acb_auth/acb_auth/agent_signing.py`. `MCP_ROUTES`, `actor_via_for` and `ACTOR_VIA_PATTERN` in `agent_oauth.py` (§9.5). The connection read in `agent_tokens.py`, and the wider `_CONNECTION_LIVE_SQL` (§7.1). The three Settings of §5.5. No migration: migration 248 already holds `scopes`, `pinned_projects`, `include_personal` and `client_origin`. It ships dark. Its merge deploys it live but inert, because no current caller sends `X-Agent-*` or `mcp:*`. The audit checked `skill_projects` (`client.py:135`) and the workbench | D1, D2, D3, D4 and D14 (the gateway 401) of §15.2. D15 rules 1 and 2 | AGENT-SAFE |
| EA-3b-1 | **MERGED 2026-10-13 (#954, `355c18bcf`), dark, after fix rounds 1 and 2.** **The edge transport and two task tools (EA3-22).** `agent_edge/mcp.py`, `bearer.py`, `gateway_client.py`, `cursor.py`, and `tools.py` with `list_tasks` and `search_tasks` only. The wire contract of §9.6, with the order of checks. The `httpx` dependency of the edge (§5.5). It ships dark, and it does not stamp `last_used_at` | D5, D6, D7, D8, D10, D11, D16, D17, D18 and D19. The MCP half of D14. D15 rules 3 and 4. D9, D12 and D13 for `list_tasks` and `search_tasks` | AGENT-SAFE |
| EA-3b-2 | **MERGED 2026-10-13, dark, after fix rounds 1 and 2.** **The other five read tools, and the fence edit (EA3-9).** `whoami`, `list_projects`, `get_project`, `get_task` and `get_task_timeline`, with the field lists and the `meta` allowlist of §9.1. The edit of `test_agent_oauth_server.py` of §9.4 | D9, D12 and D13 for the five tools. D20 | AGENT-SAFE |
| EA-3c | **Accept the 2026-07-28 revision, after a copy of its text is in the tree.** Its requests are stateless with required `_meta` fields, and its results carry a `resultType` (§5.3) | A test with the pinned text shows a `2026-07-28` client can list and call each tool. EA-11 repeats it with a real client | AGENT-SAFE |
| EA-4a | **MERGED 2026-10-13, dark, after fix rounds 1 and 2** (branch `ws55-ea4-consent`). **The consent page (§10.5).** The page `/connect/authorize`, the two BFF routes, the consent ticket, the CSRF and frame rules, `redirect_display` and the RFC 8252 redirect forms. It ships dark | `test_agent_oauth_server.py`: approve with no ticket, a spent ticket or a tampered ticket gives 400. A test checks each binding field. A Redis outage gives 503. The display cases hold, `x-evil://claude.ai/cb` included. Each RFC 8252 form passes or fails as §10.5 says. vitest: each BFF route refuses `Sec-Fetch-Site: cross-site` and a missing header with 403, and never answers a 3xx. A test asserts the `SameSite=Lax` session cookie. Playwright `e2e/connect-authorize.spec.ts`, with the BFF mocked through `page.route`: the page renders, narrowing works, approve navigates, deny navigates, and the frame headers are present | AGENT-SAFE |
| EA-4b | **Connections and revoke, in the Integrations app (§5.4, D-EA-9).** It is not a go-live gate for a pilot (EA3-25). **The store** (`agent_tokens.py`): `list_connections(org, *, member_email=None)` gives the live rows with `id`, `client_name`, `client_origin`, `scopes`, `pinned_projects`, `include_personal`, `created_at`, `last_used_at`, `expires_at` and `member_email`. `revoke_connection_scoped(org, connection_id, *, member_email=None)` revokes with `AND organization_id`, and with `AND member_email` for a member, and it revokes the tokens only when the `UPDATE` returns a row. `revoke_connection` and branch 0 do not change. **The four routes.** `GET /agent-access/connections` and `DELETE /agent-access/connections/{connection_id}` for the member, in `routes/agent_access.py`. The member is `_member(user)` and the organization is `current_tenant()`. They do not check the organization allowlist, so an organization off the list can still list and revoke. `GET /integrations/agent-connections` and `DELETE /integrations/agent-connections/{connection_id}` for an admin, in the new `routes/integrations_agents.py`, behind `feature:integrations` and `integrations:manage`. Each route answers 404 while `agent_access_enabled` is off. Each revoke writes one `acb_audit` row with the action `agent_connection.revoke`. **The BFF** relays the four routes, and each DELETE refuses a `Sec-Fetch-Site` that is not `same-origin`. **The view** is "AI agents" in the Integrations rail: the connect card, the member's agents and, for `integrations:manage`, every agent connection of the organization. `last_used_at` is stamped on a token issue and a refresh only, so the view labels it "Last token refresh" | **A1** a member lists only their own live rows (R8, two members, two organizations). **A2** a member's DELETE of the id of another member or of another organization gives 404 and changes nothing. **A3** an admin lists each row of the organization and no row of organization B. **A4** a member gets 403 on the admin routes, and so does a holder of `feature:integrations` without `integrations:manage`. **A5** after a revoke, the next `/mcp` call gets 401 and a refresh gets `invalid_grant`, through `ASGITransport` on the real gateway and edge apps. **A6** a suspended member gets 401. **A7** no answer holds a token or a hash (a sentinel test). The flag off gives 404. An organization off the allowlist can still list and revoke. **A8** each revoke writes one audit row. **A9** vitest: a member with no grants sees `/integrations` and the agents view only, the org views stay hidden, and a cross-site BFF DELETE gets 403. **A10** Playwright `e2e/integrations-agents.spec.ts`, with `page.route` mocks: the card renders, copy writes the URL, the tabs switch, revoke confirms and removes the row, an admin sees the org table and a member does not. Each item goes red first under a named mutation | AGENT-SAFE |
| EA-4c | **The organization switch and the client allow list (D-EA-8).** It is not a go-live gate for a pilot (EA3-25) | With the switch off, `check` and `approve` refuse. They also refuse a client outside the allow list | AGENT-SAFE |
| EA-5 | **Company context.** `get_company_context` and the three resources, after D-EA-6 is answered | The result holds no data that the member could not read in the app | AGENT-SAFE |
| EA-6a | **MERGED 2026-10-14, dark, after fix round 1.** **The first write tools (EA3-28 to EA3-31).** The nonce line in `agent_signing.py`. In branch 0: the write method for a pair in `MCP_WRITE_ROUTES`, the `projects:write` check, the nonce `SET NX` through the tenant Redis wrapper, the flag `agent_access_writes_enabled` (default false) and the cap. Three pairs join `MCP_ROUTES`. The edge client sends a write with the nonce, `If-Match` and a JSON body. The tools `set_task_status` and `add_task_reference`, and the references in `get_task` (§9.2). Coverage rules 1 to 4 accept a listed write pair. No migration. It ships dark | `tests/unit/test_mcp_edge_writes.py`, each check red first under a named mutation, on a real database (R8) through the real gateway app: **W1** a `projects:read` connection gets 401 at the gateway on the PATCH. **W2** a replayed nonce gets 401. **W3** (fix round 1) with the writes flag off, `tools/list` leaves out both write tools, a call of one gives "Changes are turned off for agents in this organization." with no gateway request, a signed write gets 403 with the detail `agent_writes_disabled`, an unsigned one still gets 401, the edge maps the coded 403 to the same tool error and never to the MCP 401, and the token keeps its reads. **W4** a stale `expected_version` gets 412, and the edge gives "The task changed. Read it again." **W5** a task that a pin hides gets 404, and the edge gives `Task not found.` **W6** a second add of one reference returns `created: false`, and the task holds one `link` activity row for it. **W7** `meta.via` of the status change and `via` of the reference equal `mcp:<slug>`. **W8** the signed string of a GET is byte-identical to the string before EA-6a, by a golden test. Also: the edge refuses a move into `done` or `cancelled`, the 121st write in the window gets 429, and a Redis outage or a Redis client that cannot be built gets 503 | AGENT-SAFE |
| EA-6b | **`claim_task`.** A new add-only route `POST /projects/tasks/{task_id}/assignees/self`, because the PUT of the assignees replaces the set (EA3-31) | The member becomes an assignee, and no other assignee leaves. The task moves to the first "in progress" status | AGENT-SAFE |
| EA-6c | **`add_comment`**, with a required `idempotency_key` (EA3-31) | The same key returns the first comment, and writes one row | AGENT-SAFE |
| EA-6d | **`create_task`, `update_task` and the task source `mcp`.** One expand-only migration adds `mcp` to `pm_tasks.source`, with the `TASK_SOURCES` constant and its fence. Take the number at build time (R1) | A task that an agent makes reads `mcp`, not `manual`. The coding scenario of §12 passes end to end on a real database | AGENT-SAFE |
| EA-7 | **Limits.** A per-connection and per-member limiter on the tenant-prefixed Redis wrapper. A 100-row cap | A test shows the 429 and the `Retry-After` value | AGENT-SAFE |
| EA-8 | **Audit.** One `acb_audit.record` row per call, with `organization_id` set on the event. An admin read route and view | A denied call writes a row. The admin view lists calls by connection | AGENT-SAFE |
| EA-9 | **Fences.** A tenant fence, a "no client header reaches the gateway" fence, a "no token forwarded" fence. `test_mcp_edge_coverage.py` lands earlier, in EA-3 (D15) | Each fence fails when a mutation breaks its rule (verified red first) | AGENT-SAFE |
| EA-10p | **MERGED 2026-10-13, dark, after fix round 1.** **The organization allowlist (EA3-27, §10.6).** The Setting `agent_access_orgs`, the predicate `org_allowed` in `agent_oauth.py`, and a check at each of the four doors of §10.6. It ships dark, and the default allows no organization. EA-10 turns the flag on with `AGENT_ACCESS_ORGS=<the organization id of Fracktal>` on both units. Then it restarts `acb-gateway` and `acb-agent-edge`, because each unit reads the list only at process start | The parser: an empty value allows no organization, `*` allows each, a list works, the spaces go, and a bad entry allows nothing. Each door refuses an organization off the list and accepts one on it, and `*` accepts any. With the flag off, each door still refuses. R8, end to end: an allowed organization connects and lists tasks. Then it leaves the list, the next `tools/call` gets 401, and a refresh gets `invalid_grant`. Each door and the fail-closed rule go red under a named mutation | AGENT-SAFE |
| EA-10 | **EA-10a and EA-10b MERGED 2026-10-13, dark, after fix rounds 1 and 2.** **Deploy, as a pilot.** §15.3 pins each part. EA-10a is the unit, its sandbox and its deploy step. EA-10b is the Caddy site `mcp.metorite.com` and the header strip on `api.metorite.com` (§10.1, other control 3). EA-10c is the smoke. A check proves that the box holds `GATEWAY_INTERNAL_TOKEN` before the flag turns on (§10.1, other control 4). The signing key goes on both units (H-303). The wildcard DNS record already covers the host (§5.2). **Go-live limits (EA3-25).** Until EA-4b, no member screen revokes a connection, so a member revokes through the client or asks an admin. Until EA-4c, approve binds the organization of the member's session. **What gates the pilot.** EA-10p, the organization allowlist, gates it. EA-5 to EA-9 and EA-11 do not gate it | The flag turns on for the organizations in `AGENT_ACCESS_ORGS` (EA-10p), Fracktal first. The evidence is the deployed SHA, the smoke of §15.3 and a real client round trip | EA-10a: AGENT-SAFE (`deploy-write`). EA-10b: OWNER-GATE, gate (a). The env writes, the unit enable and the flag: OWNER-GATE |
| EA-11 | **Hardening review.** An adversarial review of §10, then a second pass with a real outside client | Both reviews are recorded in this file | AGENT-SAFE |

Order: EA-0, then EA-1 and EA-2, then EA-3a, then EA-3b-1 and EA-3b-2, then EA-2c, then EA-4a and EA-5, then EA-6 to EA-9, then EA-10 and EA-11. EA-4b and EA-4c come later, and neither gates a pilot (EA3-25). EA-2c touches no file of EA-3b, so it can run in parallel with EA-3b. It must merge before EA-4. EA-6 and EA-5 can run in parallel after EA-3b-2. EA-3c runs when a copy of the 2026-07-28 text is in the tree.

### 15.1 Verification commands

The EA-3 slices create four test files: `tests/unit/test_mcp_edge_protocol.py`, `test_mcp_edge_signing.py`, `test_mcp_edge_coverage.py` and `test_mcp_edge_authority.py`.

**The R8 database.** Run `eval "$(bash scripts/dev_db.sh --export)"`, or export the DSN of a private container. The shared scratch database holds rows from other branches. Use one private pgvector container for each R8 run, and never run two R8 runs at once.

**The new suites of EA-3a, with the EA-1 and EA-2a fences.** Each file in this line exists after EA-3a:

```
uv run pytest tests/unit/test_mcp_edge_signing.py tests/unit/test_mcp_edge_coverage.py tests/unit/test_mcp_edge_authority.py tests/unit/test_agent_oauth_server.py tests/unit/test_agent_connection_tokens.py -v -rs
```

**The same line after EA-3b.** EA-3b-1 creates `tests/unit/test_mcp_edge_protocol.py`, so this line adds it.

**The test seam of EA-3b (EA3-20).** The protocol tests call the edge app in process. The edge reaches the gateway through `agent_edge_gateway_url`, and the tests point that URL at the real gateway app with `httpx.ASGITransport`. So a test runs the real branch 0, the real `MCP_ROUTES` check and the real routes, with no network. An R8 suite in the file starts with `pytestmark = _DB_GATE`, as `test_agent_oauth_server.py:88` does. Without the gate, `deploy.yml` has no ladder database and errors.

```
uv run pytest tests/unit/test_mcp_edge_protocol.py tests/unit/test_mcp_edge_signing.py tests/unit/test_mcp_edge_coverage.py tests/unit/test_mcp_edge_authority.py tests/unit/test_agent_oauth_server.py tests/unit/test_agent_connection_tokens.py -v -rs
```

**The suites that must stay green unchanged (D3):**

```
uv run pytest tests/unit/test_default_deny_auth.py tests/unit/test_service_identity_and_webhook_auth.py tests/unit/test_tenant_request_binding.py tests/unit/test_usage_attribution.py tests/unit/test_identity_pool_timeout.py tests/unit/test_route_template_prefixes.py tests/unit/test_projects_actor_via.py tests/unit/test_projects_chat_coverage.py tests/unit/test_projects_field_parity.py tests/unit/test_projects_routes.py tests/unit/test_projects_search_r8.py tests/unit/test_projects_agent_grants.py tests/unit/test_db_engine_seam.py tests/unit/test_console_dependency_boundary.py tests/unit/test_tenant_coverage.py -rs
```

**Lint and types:**

```
uv run ruff check apps/services/agent_edge packages/acb_auth packages/acb_common apps/services/gateway/gateway/routes/projects/core.py tests/unit/test_mcp_edge_*.py
uv run mypy --ignore-missing-imports apps/services/agent_edge packages/acb_auth/acb_auth/agent_signing.py
```

The mypy line needs `--ignore-missing-imports`, as `deploy.yml` does. The workspace packages ship no `py.typed` marker, so mypy refuses to read `acb_common` and `acb_auth` without the flag.

**Conformance (EA3-21).** Start the edge on loopback with the flag on and a signing key set. Mint an access token with `projects:read` on a test connection. Then run this command:

```
npx @modelcontextprotocol/inspector --cli http://127.0.0.1:<edge port>/mcp --transport http --method tools/list --header "Authorization: Bearer <token>"
```

The command must print seven tools, and no other tool. The names are `whoami`, `list_projects`, `get_project`, `list_tasks`, `get_task`, `get_task_timeline` and `search_tasks`. Each tool shows its `inputSchema`. After EA-3b-1 alone, the list holds `list_tasks` and `search_tasks` only. This shell has no `jq`, so check each name with `grep`.

**What we know about the flags.** The repo holds no copy of the Inspector and pins no version of it. The flags `--cli`, `--transport http`, `--method tools/list` and `--header` come from the README of the Inspector CLI client (`clients/cli/README.md` on `main` of `modelcontextprotocol/inspector`, read on 2026-10-13). That README also names `--format json`, and an older release may not have it. So the builder confirms the flags with the installed version first. If the installed version does not have a flag, this check is MANUAL: run the Inspector in its browser mode, connect with the token, list the tools, and record the result in the pull request.

**EA-4a (§10.5).** Run these two lines. The first runs in `workbench/control_plane`:

```
npx tsc --noEmit && npx vitest run && npx playwright test e2e/connect-authorize.spec.ts
uv run pytest tests/unit/test_agent_oauth_server.py -v -rs
```

Six vitest failures are known and come from CRLF line ends only: `layoutBoundary` (two), `railSessions`, `emailLink` and `reportsLiveOverview` (two). Any other failure is real.

**EA-4b (§5.4, D-EA-9).** Run the first line on the R8 database. Run the second line in `workbench/control_plane`. The six CRLF vitest failures above are allowed:

```
uv run pytest tests/unit/test_agent_connections_r8.py tests/unit/test_agent_connection_tokens.py tests/unit/test_agent_oauth_server.py tests/unit/test_mcp_edge_protocol.py tests/unit/test_org_access_enforcement.py -v -rs
npx tsc --noEmit && npx vitest run && npx playwright test e2e/integrations-agents.spec.ts e2e/app-title-bar.spec.ts
```

**EA-6a (§9.2, EA3-28 to EA3-31).** `tests/unit/test_task_refs_route.py` is the WS-57 fence of the route that `add_task_reference` calls. Run it on the R8 database:

```
uv run pytest tests/unit/test_mcp_edge_writes.py tests/unit/test_task_refs_route.py -v -rs
```

The R8 classes of `test_mcp_edge_writes.py` need `TENANT_LADDER_DATABASE_URL`, and they run the nonce and the cap on a fake under the real `TenantRedis`. One more class runs them on a real Redis 7 when `AGENT_WRITES_REDIS_URL` names one. CI has no Redis service, so that class skips there. Also run the D3 line, `tests/unit/test_tenant_request_binding.py` with the digest, and the lint and type lines above.

Every file in these pytest lines existed on 2026-10-11, except the four new ones. With `-rs`, a skipped R8 test shows its reason. A skip is not a pass. Name the files you mean when you run pytest. Do not run `tests/unit/` as a directory.

### 15.2 EA-3 acceptance (EA3-8)

Each line is one test, or one set of tests. The parentheses name the slice that proves it.

- **D1** (EA-3a). A valid signature, a live connection and an active member resolve to that member, with the agent scope attached.
- **D2** (EA-3a). Each of these gets 401:
  - the flag off, the key unset, a key under 32 bytes,
  - a bad signature, a wrong `kid`, a timestamp more than 60 seconds off,
  - a revoked or expired connection, a connection of another organization,
  - a suspended member, a member who moved to another organization,
  - a method other than GET, a route outside `MCP_ROUTES`,
  - `mcp:*` with no signature, `mcp:*` with a bearer and `X-User-Email`, `GATEWAY_TRUST_UNVERIFIED_SSO_HEADERS=1`, the LLM-key fallback.
- **D3** (EA-3a). The existing auth suites of §15.1 pass unchanged. When `agent` is None, the clause strings are byte-identical to the strings of today, by a golden test.
- **D4** (EA-3a). A pin, the personal opt-out and the seal each hide their target on both arms of `task_visibility_clause`. A get answers 404, and the `total` of a list leaves the target out. An R8 test proves it on a real database.
- **D5** (EA-3b-1). With the flag off, `/mcp` answers 404.
- **D6** (EA-3b-1). The 401 headers are exactly as §9.6 says.
- **D7** (EA-3b-1). The 403 headers are exactly as §9.6 says, and `tools/list` changes with the scope.
- **D8** (EA-3b-1). The version, session, 405, 202, `-32600`, `-32601` and `-32602` rules of §9.6 hold. A request after `initialize` with no version header gets the same result shape as one with `2025-11-25`. An `initialize` with no header passes. `server/discover` gets `-32601` with HTTP 200.
- **D9** (EA-3b-1 for two tools, EA-3b-2 for five). `structuredContent` validates against its `outputSchema`, and a text block holds the same JSON. The keys of each result are exactly the pinned keys of §9.1, so no `clickup_id`, `clickup_kind` or `lead` appears. The not-found text is the same for every cause. `whoami` returns exactly its six keys of §9.1.
- **D10** (EA-3b-1 for `list_tasks`, EA-3b-2 for the other two). `limit` is at most 100, and at most 50 for `search_tasks`. A list returns `nextCursor` while more rows exist. A tampered cursor returns `isError: true`. `list_projects` and `get_task_timeline` page with the cursor. A cursor of another tool returns `isError: true`.
- **D11** (EA-3b-1). No request of the edge carries `include_triage`, `include_archived`, `archived_only` or `watching`.
- **D12** (EA-3b-1 for two tools, EA-3b-2 for five). Every free-text field arrives in the `untrusted_text` shape of §9.4. So does each string value of a custom field, and each free-text value of the timeline `meta`. A `meta` key outside the allowlist of §9.1 never arrives.
- **D13** (EA-3b-1 for two tools, EA-3b-2 for five, R8). A member sees the same tasks over MCP as over `GET /projects/tasks`. The test runs once with `include_personal` true and no pins, then once with each narrowing. A second R8 test removes a grant, and the task is gone on the next call. The tests use the seam of §15.1, `httpx.ASGITransport` on the gateway app. For EA-3b-2, the R8 tests also prove four facts:
  - `list_projects` equals `GET /projects/tree` for the same member, flattened, with the archived projects dropped.
  - A target that a pin, the personal opt-out or a removed grant hides makes `get_project`, `get_task` and `get_task_timeline` return the not-found text.
  - A visible target returns the data of the route, through the pinned keys.
  - `whoami` returns the pins and the `include_personal` of the connection.
- **D14** (EA-3a and EA-3b-1). A revoked connection and a suspended member each get 401 on the next call. EA-3a proves the gateway 401, and EA-3b-1 proves the MCP 401. The admin member routes clear the 60 second access cache (`invalidate_for` in `routes/admin/members.py`), so a suspension takes effect on the next call. A suspension by another path takes effect within 60 seconds.
- **D15** (EA-3a rules 1 and 2, EA-3b-1 rules 3 and 4). `test_mcp_edge_coverage.py` passes. Each of its four rules was red first under a named mutation.
- **D16** (EA-3b-1). No request of the edge carries a bearer or `X-User-Email`.
- **D17** (EA-3b-1). The order of the checks of §9.6 holds. A request that fails two checks gets the answer of the earlier one. Each 400, 401 and 403 body is a JSON-RPC error with `id: null`. With no signing key, `/mcp` answers 503 with `Retry-After: 30`.
- **D18** (EA-3b-1). A database outage in the token lookup answers 503 with `Retry-After: 2`, never 401. The test raises an error that `db_busy.is_db_outage` accepts, such as `ConnectionRefusedError`. A second test raises a `ValueError` and gets 401.
- **D19** (EA-3b-1). Each row of the gateway answer table of §9.6 holds. A gateway 403 gives `isError: true` with "This member cannot read that.", and never a `WWW-Authenticate` header. A gateway request past 10 seconds, and a tool call past 25 seconds, give the busy text. `list_tasks` makes at most 11 gateway requests. `get_task` makes three gateway requests, and `get_project` makes five (EA-3b-2).
- **D20** (EA-3b-2). The fence edit of §9.4 holds. `tools.py` may hold an identity literal in the three positions of §9.4. The AST tests are a heuristic that catches the common shapes, and §9.4 names what they miss. A mutation that reads a request header, query or form in a common shape in `tools.py` turns a new AST test red, and so does `getattr(request, "head" + "ers")`. A mutation that adds an identity literal to another edge module turns the old test red. A test holds that a tool call receives no request object.

### 15.3 EA-10 pins

EA-10 puts the edge on the box as a pilot. The flag turns on for the organizations in `AGENT_ACCESS_ORGS` (EA-10p), Fracktal first. A separate slice, EA-10p, builds that allowlist. This section pins each file and each command of EA-10. The tests of EA-10a and EA-10b hold each pin.

**EA-10a. The unit, its sandbox and its deploy step (AGENT-SAFE, gate `deploy-write`).**

The unit file is `deploy/hostinger/acb-agent-edge.service`. It holds these lines:

```
DynamicUser=yes
User=acb-agent-edge
EnvironmentFile=/opt/acb/app/apps/services/agent_edge/.env
RuntimeDirectory=acb-agent-edge
WorkingDirectory=/run/acb-agent-edge
ExecStart=/opt/acb/app/.venv/bin/uvicorn agent_edge.app:app --host 127.0.0.1 --port 8091
```

- Port 8091 is free. On 2026-10-13 no unit, compose file or Caddy site in the repo uses it. The Customer Console uses 8090.
- The edge listens on loopback only. Caddy is its only public door.
- `uv sync` already installs the edge into the shared venv, because the root `pyproject.toml` lists `agent-edge`. So the import needs no working directory.

**The identity (fix round 1).** The edge does not run as `acb`. As `acb`, it could read every 0600 file of `acb`. These include the root `.env`, each `.env.bak-<stamp>` copy that `scripts/secrets.sh` keeps, and each `.env-edit.*` file. `InaccessiblePaths` takes no glob, so a path sandbox cannot close those copies. A different identity closes them with plain file permissions.

- **Why `DynamicUser`, and not a fixed user.** The edge only READS the code and the venv, and it writes no file. `DynamicUser=yes` gives the unit a fresh uid at each start, with no home and no file on the box. So nothing must create a user, and no file can ever belong to the edge.
- **What it must read.** The code under `/opt/acb/app` and the venv at `/opt/acb/app/.venv`. A git checkout and `uv sync` write them as `acb` under umask 022, so they are 0755 and 0644. The repo cannot prove the modes of the box, so the pre-enable check below proves them.
- **The interpreter.** The venv uses the system Python. `/home/acb/.local/share/uv` does not exist on the box (`box_hardening.md`, the home of `acb`). So `ProtectHome=yes` is safe, and the check below proves it too.
- **The env file.** systemd reads `EnvironmentFile` as root, before it drops to the dynamic user. So the edge file stays `acb`-owned at 0600, and the edge process cannot open it.
- **Why the working directory is `/run/acb-agent-edge`.** `Settings` reads `env_file=".env"` from the working directory, and pydantic raises on a `.env` that it cannot open. The runtime directory is empty, so `Settings` reads only the process env that systemd filled. It is never the checkout root, where `.env` holds `GATEWAY_INTERNAL_TOKEN`.

**The sandbox** is `deploy/hostinger/acb-agent-edge.service.d/50-hardening.conf`. It has the BH-2 shape of `acb-gateway`, with `ProtectHome=yes`. It is the second layer, for the paths that are not 0600.

- `InaccessiblePaths=/opt/acb/app/.env` has no `-`, because the root `.env` is always on the box.
- It also hides the workbench tree, the Console env file, the Git checkout, `/opt/acb/app/data`, `/var/lib/acb-gateway`, `/var/cache/acb-gateway`, the Docker socket and the user manager. The workbench tree holds `.env.local` and the `.env.local.bak.<epoch>` copies of `vps_apply.sh`, whose mode the repo does not fix.
- It sets no `ReadWritePaths`, because the edge writes no file.
- **What the sandbox does not do.** The edge holds the DSN of the gateway's database role. Code execution in the edge reaches each table that this role reaches. A narrower role is a follow-up before GA (§17).
- `bh2_strict_check` in `vps_apply.sh` reads the gateway only. It does not check the sandbox of the edge. The tests of EA-10a pin each line instead.

**The pre-enable check (owner or operator, gate `deploy`).** Run it on the box after the deploy has installed the unit, and before `systemctl enable`. Stop at the first line that differs.

1. The new identity can read the code and the venv, with no home. The line prints `code and venv: readable`:

```
sudo systemd-run --wait --pipe --quiet -p DynamicUser=yes -p ProtectHome=yes -p WorkingDirectory=/ /opt/acb/app/.venv/bin/python -c "import agent_edge.app; print('code and venv: readable')"
```

2. The new identity cannot read the root `.env` or any copy of it. Each line prints `closed`. A `READABLE` line means that file has a mode other than 0600, so fix the mode first:

```
sudo systemd-run --wait --pipe --quiet -p DynamicUser=yes sh -c 'for p in /opt/acb/app/.env /opt/acb/app/.env.* /opt/acb/app/.env-edit.*; do [ -e "$p" ] || continue; if [ -r "$p" ]; then echo "READABLE $p"; else echo "closed $p"; fi; done'
```

2b. The sandbox hides the backups. The nightly dumps under `/opt/acb/backups` are 0664, because `backup_db.sh` sets no umask, so a bare `DynamicUser` could read them. The drop-in hides `/opt/acb/backups` and `/opt/acb/data`. This check applies those two lines, and the line prints `backups: closed`:

```
sudo systemd-run --wait --pipe --quiet -p DynamicUser=yes -p InaccessiblePaths=-/opt/acb/backups -p InaccessiblePaths=-/opt/acb/data sh -c 'ls /opt/acb/backups >/dev/null 2>&1 && echo "backups: READABLE" || echo "backups: closed"'
```

3. The unit hides the gateway data. The output lists `/opt/acb/app/.env`, `/opt/acb/app/data` and `/var/lib/acb-gateway`:

```
sudo systemctl show acb-agent-edge -p DynamicUser -p User -p InaccessiblePaths
```

4. The edge env file holds the pool pin of the pooler budget below, and nothing more about the pool. The line prints exactly `DB_MAX_OVERFLOW=0` and `DB_POOL_SIZE=1`, in that order:

```
sudo grep -E '^DB_(POOL_SIZE|MAX_OVERFLOW|SYNC_[A-Z_]+)=' /opt/acb/app/apps/services/agent_edge/.env | sort
```

**The deploy step** is in `scripts/vps_apply.sh`, after the Customer Console step, in the same pattern:

1. When `acb-agent-edge` is enabled, the step installs the unit file if it changed. Then it restarts the unit.
2. It waits for an HTTP answer on `http://127.0.0.1:8091/.well-known/oauth-authorization-server`. A 404 counts, because the flag is off by default.
3. An enabled unit that does not answer fails the deploy.
4. When the unit is not enabled, the step does nothing and says so. A unit that is not enabled stays off.

- **The probe reads no database.** With the flag off, no route of the edge reads the database, and the edge has no readiness route. Step 3 of the EA-10c smoke is the database check.

The BO-23 loop installs the unit file, and the BH-7 step installs the drop-in. Neither one enables a service. So the first deploy puts the files on the box and starts nothing.

**The env file of the edge.** The path is `/opt/acb/app/apps/services/agent_edge/.env`, owner `acb`, mode 0600.

| Name | Value |
|---|---|
| `AGENT_ACCESS_ENABLED` | `true` at the pilot. While it is not set, every route answers 404 |
| `AGENT_EDGE_SIGNING_KEY` | The same key as the gateway. At least 32 bytes, and not equal to `GATEWAY_INTERNAL_TOKEN` or `LITELLM_MASTER_KEY` |
| `DATABASE_URL` | The DSN and the role of the gateway's `DATABASE_URL`, on the SESSION pooler. `lookup_token` and the other token acts read it through `acb_common.db` |
| `DB_POOL_SIZE` | `1`. The edge has ONE database connection (the pooler budget below) |
| `DB_MAX_OVERFLOW` | `0` |
| `DB_SYNC_POOL_SIZE`, `DB_SYNC_MAX_OVERFLOW` | Not set. The edge never builds the sync `acb_graph` engine, so these values open no connection. Never set them to `0`: SQLAlchemy reads a `pool_size` of 0 as no limit |
| `DB_STATEMENT_CACHE_SIZE` | The value of the root `.env`, when it sets one |
| `AGENT_EDGE_GATEWAY_URL` | Not set. The default `http://127.0.0.1:8080` is the gateway on loopback |
| `AGENT_ISSUER`, `AGENT_RESOURCE` | Not set. The defaults are `https://mcp.metorite.com` and `https://mcp.metorite.com/mcp` |
| `AGENT_ACCESS_WRITES_ENABLED` | Not set at the pilot, so the write tools stay off. Set it to `true` only when writes turn on, and set the same value in the root `.env` (EA-6a) |

- The edge file must not hold `GATEWAY_INTERNAL_TOKEN`, `LITELLM_MASTER_KEY` or a provider key.
- ⚠️ **The writes flag lives in both files (EA-6a fix round 1).** The edge reads it to list the write tools and to refuse a call of one. The gateway reads it in branch 0. To turn writes on, set `AGENT_ACCESS_WRITES_ENABLED=true` in the edge file AND the root `.env`, then restart `acb-gateway` and `acb-agent-edge`. While only one unit has restarted, the edge maps the gateway's coded 403 to a tool error, never to the MCP 401. A change of the value is gate `enforcement-flip`.
- EA-10p decides if the edge reads `AGENT_ACCESS_ORGS`. If it does, the edge file holds the value of the root `.env`.

**The pooler budget (orchestrator decision, 2026-10-13).** The session pooler in front of the database allows 15 clients for the whole database. The gateway and the edge share it.

- The gateway opens 7 + 2 async and 2 + 1 sync, so 12 (`settings.py`, `db_pool_size` and the note above it). This slice does not change it.
- The edge opens ONE: `DB_POOL_SIZE=1` and `DB_MAX_OVERFLOW=0`. Its work is short: the token lookup, and the token issue and rotation. With `db_pool_timeout` at 10 seconds, one connection serves a pilot of one organization.
- The edge never builds the sync engine. No edge module, and none of `agent_tokens`, `agent_oauth` and `agent_signing`, imports `acb_graph`. A test also imports the edge app and proves that it built no sync engine.
- So the database sees 12 + 1 = 13 of 15. The headroom is 2: a migration and the backup job can run together. An ad-hoc `psql` waits while both run.
- The fence is `TestThePoolCeilingFitsThePoolerInFront` in `tests/unit/test_db_engine_seam.py`. It reads the two pool rows of the table above, so the spec and the test hold one value.
- The transaction pooler (port 6543) is not proven safe for the edge, and a narrower database role is not built. Both are for GA (§17).

**The root `.env` of the gateway** gets three names. A writer of that file keeps its inode (`deploy/AGENTS.md`).

| Name | Value |
|---|---|
| `AGENT_EDGE_SIGNING_KEY` | The same key as the edge |
| `AGENT_ACCESS_ENABLED` | `true` at the pilot |
| `AGENT_ACCESS_ORGS` | The organization ids of the pilot, Fracktal first (EA-10p) |
| `AGENT_ACCESS_WRITES_ENABLED` | Not set at the pilot. `true` only when writes turn on, with the same value in the edge file (EA-6a) |

**EA-10b. The Caddy site and the header strip (OWNER-GATE, gate (a)).**

The Caddyfile gets the site `mcp.metorite.com`:

```
mcp.metorite.com {
	request_body {
		max_size 1MiB
	}
	reverse_proxy 127.0.0.1:8091 {
		lb_try_duration 30s
		lb_try_interval 250ms
	}
}
```

- `1MiB` is 1048576 bytes, the value of `mcp.MAX_BODY_BYTES`. Caddy reads `1MB` as 1000000 bytes, so `1MB` refuses a body that the edge accepts.
- TLS works as on the other sites. Caddy gets the certificate by itself.
- The `lb_try_*` lines follow the H-60 rule of the file. A restart of the edge then holds a request, and it does not answer 502.

The `reverse_proxy` of `api.metorite.com` gets six lines:

```
header_up -X-User-Email
header_up -X-User-Role
header_up -X-Actor-Via
header_up -X-Agent-Connection
header_up -X-Agent-Timestamp
header_up -X-Agent-Signature
```

- A public request then names no member and no agent connection. Only the edge reaches branch 0, and it calls the gateway on loopback.
- **No internal caller uses the public host.** On 2026-10-13 the repo shows that each caller of these headers calls the gateway on loopback. These are the workbench BFF (`GATEWAY_BASE_URL`), the agents and skills (`GATEWAY_URL`), the WhatsApp bridge and the edge. `GATEWAY_PUBLIC_URL` builds callback links only. The CI and health probes send no identity header.
- The repo cannot see the env files of the box. Before the merge, the owner checks that no `GATEWAY_URL` or `GATEWAY_BASE_URL` on the box names `api.metorite.com`.
- **Done when** `tests/unit/test_caddy_auth_gate.py` passes after the owner adds `CADDY-AUTH-APPROVED <sha>` to `.claude/OWNER_GRANTS.md`. The H-303 grep also counts 3 or more: `grep -cE "header_up -X-(User-Email|User-Role|Actor-Via)" deploy/hostinger/caddy/Caddyfile`.

**EA-10c. The smoke (OWNER-GATE, gate `deploy`).** Run it after the flag turns on.

1. `curl -s https://mcp.metorite.com/.well-known/oauth-authorization-server` prints the RFC 8414 document with HTTP 200. Its `issuer` is `https://mcp.metorite.com`. A 404 prints `{"detail":"Not Found"}`, and that means the flag is off.
2. `curl -si -X POST https://mcp.metorite.com/mcp -H "Content-Type: application/json" -d '{"jsonrpc":"2.0","id":1,"method":"ping"}'` answers HTTP 401. Its header is `WWW-Authenticate: Bearer resource_metadata="https://mcp.metorite.com/.well-known/oauth-protected-resource/mcp"` (§9.6).
3. **The database check.** Send step 2 again, with `-H "Authorization: Bearer smoke-not-a-token"`. The edge looks the token up in the database (§7.1 step 1). HTTP 401 with `error="invalid_token"` proves that the database answered. HTTP 503 with `Retry-After: 2` means that the edge cannot reach the database.
4. Run this line on the box. It fetches the metadata document of Claude Code through `_fetch_document` in `agent_access.py` (about line 235). It writes nothing to the database, and it prints `Claude Code`:

```
cd /opt/acb/app && sudo -u acb .venv/bin/python -c "import asyncio; from gateway.routes import agent_access as a; u='https://claude.ai/oauth/claude-code-client-metadata'; print(a._validated_metadata(u, asyncio.run(a._fetch_document(u)))['client_name'])"
```

⚠️ Step 2 sends a JSON-RPC body on purpose. A POST with no body fails check 5 of §9.6 (the body) before check 6 (the token), so it answers 400 and not 401.

## 16. Shell manifest (R9)

This spec adds **no app**. Its screens join the Integrations app (§5.4, D-EA-9). That app owns the shell manifest entry. This spec adds two jobs to it. The jobs are "Connect an agent" and "Review agent activity".

Both jobs use `ManagerFrame` and the shared primitives. They add no palette, bell, assistant rail or command-bar listener. The consent page is a standalone screen on the control plane and uses the same design system.

**The consent page (EA-4a).** The page follows rules 1 to 3 and rule 8 of `workbench/control_plane/AGENTS.md`. It writes no colour, it uses `<Icon>`, it uses the primitives of `src/components/ui/`, and it imports no headless library. The builder checks it by eye in light mode, at compact density and with a changed accent.

## 17. Open risks

- The MCP specification moves. EA-3 accepts only `2025-06-18` and `2025-11-25` (§5.3). A client that speaks only `2026-07-28` cannot connect until EA-3c. EA-11 must test with real clients.
- A client may speak only an older revision, or only Dynamic Client Registration. EA-2 supports both, and its test list names the clients we test. §17.1 holds the list.
- **Claude Code 2.1.296, read on 2026-10-13.** §9.6 records what it does. It uses a Client ID Metadata Document, not Dynamic Client Registration, so EA-2b is not on its path. It probes with `server/discover` and falls back to `initialize` with `2025-11-25`. A probe with no answer is a hard failure, so the edge answers `-32601` at once. It answers `insufficient_scope` with an OAuth step-up, so a gateway 403 must never become that 403. It needs a loopback redirect on any port, so EA-2c must merge before a real Claude Code client can connect. A later release of Claude Code can change each of these facts. EA-11 checks them again.
- Dynamic Client Registration and metadata fetches are open to abuse. The limits in §10.2 are proposals.
- Branch 0 of §10.1 changes the gateway auth path. It is the riskiest change in this spec. It needs the adversarial review of EA-11. It needs no `guard-write` grant, because `plan-guard.mjs` protects no file in `acb_auth`.
- Branch 0 is reachable from the internet, because the Caddy block of `api.metorite.com` strips no header. The HMAC is the boundary. The edge calls the gateway on loopback, so EA-10 can also strip `X-Agent-*` at Caddy. EA-10 decides that.
- The gateway does not check the scopes of a connection. EA3-3 puts that check at the edge. So a thief with the signing key can read Projects as any member who holds a live connection, whatever its scopes. The key is the boundary. Rotate it at once on any suspicion (§10.1, HANDOFF H-303).
- A replay inside the 60 second window repeats an identical GET and changes nothing. A write carries the nonce of EA3-28, so its replay gets 401.
- **The category check of `set_task_status` is at the edge (EA-6a).** The edge reads the category of the target status, and then the gateway applies the PATCH. An admin who changes the category of that status between the two calls can let one move into `done` pass. The gateway does not repeat the check for an agent. A later slice can move it into the route.
- **A write of an agent can start a workflow (EA-6a fix round 1).** `patch_task` emits `pm.task.status_changed`. Through `workflows.triggers.dispatch_event`, a published workflow of the organization can act on it, and send mail or call a webhook, as it does for a member's own change. So `openWorldHint: false` is true of the tool call and not of what follows it (§9.2). The organization's workflows decide that, and EA-6a does not change them.
- **Follow-up, not for EA-6a: narrow an agent's PATCH at the gateway.** Today the edge alone sends the PATCH body `{status_id}` only, and never `include_subtasks`. A thief with the signing key can sign a PATCH with any field of `TaskIn`, or with `include_subtasks=true`, and skip the edge's rules: the done refusal, the body limit and the cascade. The gateway should refuse, for a request with `UserContext.agent` set, a PATCH body with any key but `status_id`, the query `include_subtasks`, and a move into `done` or `cancelled`. No slice owns it yet. EA-6d (`update_task`) must decide the field list first.
- The edge allows only the origin of the issuer in `Origin` (§9.6). A client that runs in a browser on another origin gets 403. EA-11 checks the real clients.
- Data that an agent reads goes to its provider. That is the customer's choice, and it is not a Metorite sub-processor. The terms page must say so. This is an owner and legal point (work_plan.md WS-37).
- The company context is thin today (D-EA-6).
- **Follow-up: the backup dumps are 0664.** `backup_db.sh` sets no umask, so each nightly dump under `/opt/acb/backups` is readable by any local user. The edge sandbox hides the folder. The backup job must still write its dumps at 0600, and fix the old ones. This belongs to WS-49 (box hardening), and no slice owns it yet.
- **Follow-up: check assignees at the gateway.** `AssigneesIn` takes any string, so a member can store a short phrase as an assignee, and the edge can only bound it (§9.4). The gateway must check each new assignee against the member directory, or against the agent registry for `agent:<name>`, before it stores the value. This belongs to the Projects app (WS-27), not to the edge. No slice owns it yet.
- A connection has no absolute maximum life. A thief who rotates a stolen refresh token at least once in 30 days keeps the connection alive until somebody revokes it. Proposal: an absolute cap, for example 365 days from `created_at`, as a `Settings` default. A later slice decides it, not EA-4. EA-1, EA-2a and EA-4a do not build it.
- The consent routes of EA-2a trust the session alone. A cross-site request could drive `approve` while a member is signed in. EA-4a closes this with a `Sec-Fetch-Site` check, a `SameSite` session, no 3xx from approve and a one-time consent ticket from `check` (§10.5).
- For a private-use scheme, `redirect_host` shows the URI authority as if it were a web host. A client can list `x-evil://claude.ai/cb`, and the consent screen then shows `claude.ai`, although the browser gives the code to the native app that claims `x-evil`. EA-4a shows the scheme with the authority for every scheme other than http and https (`redirect_display`, §10.5).
- The redirect URI rules of EA-2a are a deny list of dangerous schemes. A deny list is never complete. EA-4a moves them to the RFC 8252 forms (§10.5).
- The allow list of EA-4a refuses a private-use scheme with no dot. A client that registers such a scheme cannot connect, until it registers a loopback or `https` URI. EA-11 checks the real clients.
- **The Cursor decision (EA-4a fix round 1).** The allow list refuses Cursor's `cursor://anysphere.cursor-retrieval/oauth/callback`, so Cursor cannot connect today.
  - RFC 8252 §8.4 says that a server SHOULD refuse a private-use scheme with no dot. It does not say MUST.
  - The refusal of the authority form (`scheme://authority/path`) is stricter than the RFC.
  - Cursor registers through Dynamic Client Registration. So look at this decision again when EA-2b is built.
- **Follow-up before GA, not for the pilot: the transaction pooler for the edge.** At GA one connection may be too few for the edge. The transaction pooler (port 6543) has more client slots, and the tenant bind of the edge is transaction-local (`acb_common/db.py:298`, `agent_tokens.py:239`). But no config in the repo uses that port. The seam sends `idle_in_transaction_session_timeout` in the startup packet (`db.py:132`). And `settings.py` makes the move its own decision. A real-database test of the edge's token acts through that pooler comes first.
- **Follow-up before GA, not for the pilot: a narrower database role for the edge.** The edge connects with the DSN and the role of the gateway (§15.3). So code execution in the edge reaches each table that the gateway reaches, whatever the sandbox hides. The edge needs `agent_token`, `agent_client` and `agent_connection` only. The first two are RLS-exempt (§10.3). A role with grants on those three tables alone takes the database out of the blast radius. No slice owns it yet.
- D-EA-2 says the edge unit has a smaller blast radius. That is true only after the EA-10 sandbox. The edge holds the database DSN, and EA-3 adds the signing key, not the internal bearer. A thief with the key can act as any member who holds a live connection, on the GET routes of `MCP_ROUTES` (§10.2). A thief with the DSN reaches the database directly.

### 17.1 The clients (2026-10-14)

The connect card of EA-4b and `docs/external_agents_connect.md` show this table. Change all three together.

| Client | State | Why |
|---|---|---|
| Claude Code | Tested, version 2.1.296 | It uses a Client ID Metadata Document and a loopback redirect (EA-2c) |
| claude.ai | Not tested yet | EA-11 tests it |
| Claude Desktop | Not tested yet | EA-11 tests it |
| VS Code | Not tested yet | It may need Dynamic Client Registration. That is EA-2b, and EA-2b is not built |
| Cursor | Not supported yet | The redirect rules refuse its `cursor://` redirect (§17, the Cursor decision) |

## 18. Changelog

- 2026-10-10 — First draft. Research record, design and slices. No code.
- 2026-10-10 — The owner accepted all eight defaults in chat. D97 and the WS-55 row are now in work_plan.md. EA-0 is done.
- 2026-10-11 — The audit of EA-1 and EA-2 (verdict GO-NARROWED) changed the spec. The decision id moved from D96 to D97, because the open WS-54 pull request takes D96. `agent_authz_code` folded into `agent_token`. EA-2 split into EA-2a and EA-2b. §5.5 fixed the issuer, the layout and the lifetimes. The anchors in §4 use short paths. The full path of a gateway file is `apps/services/gateway/gateway/...`. The line numbers drifted by up to 25 lines, so every builder re-reads them.
- 2026-10-11 — EA-1 is built. One migration adds `agent_connection`, `agent_token` and `agent_client`. `packages/acb_auth/acb_auth/agent_tokens.py` is the store. `tests/unit/test_agent_connection_tokens.py` is the fence, and it runs on a phase-4-promoted catalog as a non-privileged role. The `generated/` phase files carry `agent_connection`, and the `EXEMPT` map holds 15 entries. Nothing reads the flag yet.
- 2026-10-11 — Fix round 1 of EA-1, after an adversarial review. A revoke that overlapped a refresh missed the new pair, so a revoked connection kept live tokens. Now each act that issues tokens locks the connection row first, and a revoke takes the same lock. Each issue slides the idle window of 90 days. The token lookup refuses a token of a revoked or expired connection. A refresh token that another client presents spends nothing and revokes nothing. Each refusal maps to the OAuth error `invalid_grant`, and only a true reuse revokes. Revoking the tokens issued from a replayed code (an OAuth 2.1 SHOULD) is left for EA-2a.
- 2026-10-11 — Fix round 2 of EA-1. A refusal for a reuse, a wrong client or a dead connection names the connection, so EA-2 can write an audit row. A refusal for an unknown, revoked or expired token names nothing. A refresh request with no `resource` matches the stored resource (RFC 8707). A test proves that the store's tenant bind ends with its transaction.
- 2026-10-11 — `agent_connection.last_used_at` records the last token issue, not the last use of an access token. EA-3 decides whether each call stamps it.
- 2026-10-11 — `issue_tokens` takes the `CodeGrant` that `consume_code` returns. EA-2a must call it exactly once for each spent code.
- 2026-10-11 — EA-2a is built, and it ships dark. The fence is `tests/unit/test_agent_oauth_server.py`.
  - **The edge.** `apps/services/agent_edge` is a new uv member. It serves the RFC 8414 and RFC 9728 documents, `POST /oauth/token` and `POST /oauth/revoke`. The RFC 9728 document also answers at `/.well-known/oauth-protected-resource/mcp`. EA-10 deploys the unit.
  - **The gateway.** `POST /agent-access/authorize/check` and `POST /agent-access/authorize/approve` sit behind default-deny. The member comes from the session, and the organization comes from the bound tenant. A body that names either one gets a 422.
  - **Shared rules.** `packages/acb_auth/acb_auth/agent_oauth.py` holds the scopes, the fixed addresses, the PKCE check and the redirect rules. The addresses are `Settings` fields with the defaults of §5.5.
  - **CIMD.** The gateway fetches the metadata document through `outbound_guard.py`. The fetch has a 5 KiB cap and a 5 second limit. The cache in `agent_client` lasts one hour.
  - **Code replay.** A spent code that comes again revokes its connection (OAuth 2.1 §4.1.2). The store function is `revoke_replayed_code`. A failed check after the spend also revokes the connection, because that connection can never get a token.
  - **Omitted scope.** An authorize request with no `scope` asks for the three resource scopes of §8, and the member can narrow them.
  - **Revoke.** A token that its own client revokes ends the whole connection. A token of another client changes nothing, and the answer is 200 in both cases.
  - **Not built.** The absolute cap on the life of a connection (§17) is open for EA-4. EA-7 owns the rate limits of `token` and `revoke`, and EA-8 owns the audit row.
- 2026-10-11 — Fix round 1 of EA-2a, after an adversarial review. Each change has a test that a mutation turned red.
  - **The spend proves the code.** The code spend matches the client, the redirect URI, the S256 of the verifier and the resource in its WHERE clause. A wrong presentation spends nothing and revokes nothing, so a thief with the code but not the verifier cannot burn it. This replaces the rule that a failed check after the spend revokes the connection.
  - **A replay must prove the code too.** `revoke_replayed_code` revokes only when the presentation proves the same four values. It acts only within `agent_code_replay_window_s` (600 seconds) after the code expired. So a code that leaked long ago cannot end a live connection.
  - **The fetch asks for `identity`.** The guard reads raw bytes and never decompresses. The fetch sends `Accept-Encoding: identity` and refuses a gzip or deflate body. The guard returns no headers, and WS-55 does not edit it, so the check reads the first bytes of the body.
  - **ASCII only.** A client id and a redirect URI must be ASCII. A look-alike letter of another script cannot then show as a verified origin. A punycode host is fine and shows as itself.
  - **The flag answers before routing.** On the edge, an ASGI middleware answers 404 to every path and method while the flag is off. A wrong method no longer leaks a 405 with an `Allow` header.
  - **No control character.** The edge answers `invalid_request` to a control character in a form field, and the gateway answers 422. Before, a NUL reached Postgres and gave a 500.
  - **Recorded for later.** §15 EA-4 and §17 now carry the consent page rules, the RFC 8252 redirect forms and the limit of the D-EA-2 claim.
- 2026-10-11 — The branch moved onto the current `main`. The CRM work of WS-53 took the migration number that EA-1 had used. So the EA-1 migration took the next free number at build time (R1), and this spec still names no number. The four `generated/` phase files are the files of `main` plus the `agent_connection` block, and each now counts 152 tables. The `EXEMPT` map still holds 15 entries, and `main` added none.
- 2026-10-11 — EA-1 and EA-2a merged in #884 (commit `898cfbc89`).
- 2026-10-11 — The spec audit of EA-3 gave NO-GO. The spec had no testable done-when for the authority rule or the signed header, and it left design choices open. The orchestrator took eight engineering decisions, EA3-1 to EA3-8 (§6.1). This change writes them in. No code changed.
  - **The signed request (§10.1).** The edge holds no internal bearer and sends no member header. A new branch 0 of `get_current_user` checks an HMAC, reads the connection and resolves the member. It never falls through to branch 1. The old design did not limit a stolen bearer. The claim that this needs `guard-write` was wrong, and is gone.
  - **The authority rule (§7.1) and the agent scope (§7.3).** The gateway checks the seat on every call. Pins, the personal opt-out and the seal narrow the visibility seam itself, on both arms of `task_visibility_clause`.
  - **The tools (§9.1, §9.4, §9.6).** Exactly seven read tools, each with its route and seam. `type`, `updated_since`, `key` and references leave EA-3. The `untrusted_text` shape and the wire contract are now rules.
  - **The allowlist (§9.5).** `MCP_ROUTES` in `agent_oauth.py`, with the fence `test_mcp_edge_coverage.py`. The `skill_projects` manifest stays untouched.
  - **The slices (§15).** EA-3 splits into EA-3a, EA-3b and EA-3c, with the acceptance D1 to D16 (§15.2) and the commands of §15.1. EA-3 needs no migration and does not stamp `last_used_at`.
  - **Other fixes.** §5.3 names the revision of each protocol rule. §4 carries anchors read again at `0bd098f18`. §5.1, §10.2 and §17 drop the internal bearer from the edge.
- 2026-10-12 — EA-3a is built, and it ships dark. It is not merged. No migration.
  - **The signed request.** `packages/acb_auth/acb_auth/agent_signing.py` holds the one canonical string, the HMAC, the `kid` and the key rules. Branch 0 of `get_current_user` (`acb_auth/deps.py`) runs the seven steps of §10.1, and it never falls through to branch 1. It checks the stamp before the member resolve, so a bad stamp binds no tenant. It also refuses a connection row whose organization differs from the signed one, and a GET that announces a body.
  - **No host input.** Branch 0 reads the path and the query from the ASGI scope, not from `request.url` (§10.1). `test_tenant_request_binding.py` used to ban a `request` parameter on `get_current_user`. Its parameter set now holds `request`, and a source scan of branch 0 carries the rule instead. The orchestrator decided this edit of the fence on 2026-10-12.
  - **The allowlist and the stamp.** `MCP_ROUTES`, `actor_via_for` and `ACTOR_VIA_PATTERN` are in `agent_oauth.py`. `gateway/routes/projects/core.py` imports the pattern, so one copy stays.
  - **The store.** `agent_tokens.read_live_connection` reads a live connection in the signed organization. `lookup_token` now returns `scopes`, `pinned_projects`, `include_personal` and `client_origin`.
  - **The agent scope.** `AgentScope` holds `connection_id`, `scopes`, `pinned_projects`, `include_personal`, `client_origin` and `actor_via`. That is two fields more than §7.3 names, both from the connection row. `Visibility` narrows both arms of `task_visibility_clause` and the project clause, and with `agent` None every string is byte-identical to before.
  - **The Settings.** `agent_edge_signing_key`, `agent_edge_signing_key_previous` and `agent_edge_gateway_url`.
  - **The fences.** `tests/unit/test_mcp_edge_signing.py` holds D1, D2 and the gateway half of D14. `tests/unit/test_mcp_edge_authority.py` holds D3 and D4, and it holds D14 on a real database as a non-privileged role. `tests/unit/test_mcp_edge_coverage.py` holds rules 1 and 2 of §9.5. EA-3b adds rules 3 and 4.
- 2026-10-12 — Fix round 1 of EA-3a, after the independent verification and an adversarial review.
  - **Stand-ins.** The Projects clauses read `agent` with `getattr`, so a Visibility-like stand-in with no `agent` field gives the old string. Two older unit tests pass such a stand-in. Fix round 2 reversed this.
  - **The row's organization.** `read_live_connection` now returns the organization of the ROW, not its argument, so the comparison in branch 0 can refuse. An R8 test removes the read's predicate and runs as a role that bypasses RLS, and branch 0 still refuses.
  - **An outage is a 503.** A pool or connect timeout in the connection read answers the same 503 as the member resolve, with `Retry-After`. Every other failed read stays a refusal. Fix round 2 widened this to every database outage.
  - **The query.** Branch 0 refuses a repeated query key and a query that is not strict ASCII and UTF-8 (§10.1). The header checks use `fullmatch`, so a trailing newline never passes.
  - **The fence scan.** The source scan of branch 0 follows aliases, keywords, bytes, built strings and every function that receives the request. Fourteen bypasses each turn it red. The review of fix round 2 found that this claim was too wide: the scan read only plain assignments, so other static shapes passed it.
- 2026-10-12 — Fix round 2 of EA-3a, after a second adversarial review and a second verification.
  - **Every database outage is the 503.** Round 1 mapped only a timeout. The pooler's `EMAXCONNSESSION`, the other refused connects and a bare socket error now answer the 503 in the connection read. A failed identity read answers the 503 at the seat check. §10.1 names each error.
  - **The static scan is inverted.** It flags every use of the name `request` in the branch-0 functions, except a short list of exact shapes. 34 bypass shapes each give a finding on the line they add. The scan cannot see dynamic access such as `eval`. Fix round 3 found that it trusted names it never checked.
  - **A runtime tripwire.** Every branch-0 test in `test_mcp_edge_signing.py` runs a second time on a wrapped request. Any read outside the allowlist raises: `url`, `client`, a header or scope key outside the list, and the whole-scope or whole-header walks. It proves nothing about a path that no test runs.
  - **Strict `agent`.** The Projects clauses read `vis.agent` again, so an object with no `agent` field raises and never reads as "no narrowing". The two older stand-ins carry `agent=None`.
  - **Other fixes.** `agent_oauth.py` checks each shape with `fullmatch`. §15.1 splits the pytest line, because `test_mcp_edge_protocol.py` arrives with EA-3b, and the mypy line takes `--ignore-missing-imports`. An R8 test isolates the row's organization check.
- 2026-10-12 — Fix round 3 of EA-3a, the last round on the fence. The production behavior was right. The fence and its claims were not.
  - **A role-read outage is the 503.** When the role read alone hits an outage, branch 0 answers the 503, not "no seat". `role_read_failed()` records it, and only branch 0 reads it. `db_busy.is_db_outage` is now the one list for both reads.
  - **The scan refuses more shapes.** It refuses a rebind, an import alias, a `def` or `class` of a trusted name, `global`, `del` and a decorator. It also refuses `setattr`, `eval` and the other dynamic calls. It pins the parameter of `_one_header` and the loop variable of the trigger. 53 bypass shapes each give a finding on the line they add. The verification of fix round 4 showed that a caller-frame walk still passed it, so this entry claimed too much.
  - **The tripwire keeps what it sees.** Its error is not an `Exception`, and every violation is recorded and checked at teardown. The wrapper holds the real request only in a closure, and its scope is not a `dict`. It also covers the body of `get_current_user`, and a route that two prefixes serve. It records only the reads that go through the wrapper.
  - **Process control.** §10.1 now says that any change to branch 0 needs an adversarial review and an independent verification before it merges.
- 2026-10-12 — Fix round 4 of EA-3a, the final round on the fence. Production code did not change. The fence closes classes of construct, not single shapes, and its claims now say only what is true.
  - **Construct classes.** The scan reads each branch-0 function and each function of `agent_signing.py`. In them it refuses nested functions and classes, lambdas, `match`, `global` and `nonlocal`. It also refuses chained comparisons, frame and dunder attributes, closure cells and the introspection names of §10.1. Ten `getattr` calls are on an allow-list, each with its reason. 74 bypass shapes each give a finding on the line they add.
  - **Module checks.** Each scanned function is one plain def in its own file, with no wrapper, and the scan reads its source from the file. `agent_signing` is bound once in `deps.py`. The stores of `contexts`, `cached` and `context` are pinned to their lines.
  - **Tripwire.** A header name must be an exact `str`. A `TypeError` on the wrapper is recorded even when the code catches it. A `matches` call on a receiver outside the three route classes is a violation. The R8 suite and the two tenant-binding tests run branch 0 under the tripwire.
  - **The digest test.** `test_branch_zero_digest_is_pinned` makes any edit to the scanned code fail until the digest changes, in the commit that carries the review.
  - **Known gaps.** A caller-frame walk outside the refused constructs, a closure cell through a helper outside the scan, and `gc`. Neither automatic layer stops deliberate code. The digest test and the review rule are the guard.
- 2026-10-13 — Fix round 5 of EA-3a, and the merge of `origin/main` into the branch. Production code did not change.
  - **The HANDOFF entry is H-303.** `origin/main` took H-301 and then H-302 for other entries, so the entry for the signing key and the Caddy strip is now H-303.
  - **Format strings.** A format string reaches attributes by name, past the attribute rules. The scan now refuses any attribute named `format`, `format_map`, `vformat`, `get_field` or `format_field`, the module `string`, and any name that starts with `__`. This closes `str.format` and `string.Formatter`. 79 bypass shapes each give a finding on the line they add.
  - **The digest per version.** The pin is a map from the Python minor version to its digest, with 3.12 and 3.13. Any other version fails with its own message.
  - **Test isolation.** The role-read tests pin `access._tables_missing` to False, so an earlier suite cannot change them. The RAISE hook uses tool id 3 or 4 only, because Python reserves 5 for the optimizer.
- 2026-10-13 — The spec audit of EA-3b gave NO-GO. The orchestrator took the decisions EA3-9 to EA3-23 (§6.1), and this change writes them in. No code changed.
  - **The identity fence (§9.4).** `agent_edge/tools.py` is the one response-mapping module. It may hold an identity literal as a response key, and an AST test proves that it reads no request input. EA-3b-2 edits the fence.
  - **The wire contract (§9.6).** The order of the checks, the JSON-RPC error body, the version rule with no header, `-32601` for `server/discover`, a 503 for an outage in the token lookup and for a missing signing key, and the map of each gateway answer.
  - **The tools (§9.1).** A pinned field list for each tool, an allowlist of timeline `meta` keys from `core.record_activity`, the limits of 10 and 25 seconds, and the bound of 11 requests for `list_tasks`. `list_projects` never shows a personal project.
  - **The tests (§15.1, §15.2).** The seam is `httpx.ASGITransport` on the gateway app. The Inspector check is an exact CLI command, with a manual fallback. D17 to D20 are new.
  - **The slices (§15).** EA-3b splits into EA-3b-1 and EA-3b-2. The new slice EA-2c accepts a loopback redirect on any port (§10.4), because the metadata document of Claude Code lists loopback URIs with no port.
  - **Anchors.** §4, §7.1, §7.3, §9.1 and §10.1 carry line numbers read again at `0cdb0a6db`.
- 2026-10-13 — EA-2c is BUILT, dark, and not merged, on the branch `ws55-ea2c-loopback`. `agent_oauth.redirect_uri_matches` holds the rule of §10.4, and `agent_access._validate` calls it. The token step keeps its exact match. `test_agent_oauth_server.py` holds the new tests, and each rule went red under a named mutation first.
- 2026-10-13 — EA-3b-1 is built, and it ships dark. It is not merged. No migration, and no stamp of `last_used_at`.
  - **The transport.** `agent_edge/mcp.py` serves `POST /mcp` with the eight checks of §9.6 in their order. `bearer.py` does §7.1 steps 1 to 4. A database outage in the token lookup answers 503 with `Retry-After: 2`.
  - **The gateway client.** `gateway_client.py` sends the four signed headers and no other header that it sets. It turns off `trust_env`, so no proxy or `.netrc` setting can add a credential.
  - **The tools.** `tools.py` holds `list_tasks` and `search_tasks`, with the pinned keys, the `untrusted_text` shape and the gateway answer table. `cursor.py` signs the page cursor.
  - **Choices this slice made.** `nextCursor` is null on the last page. A status read that answers 403, 404 or another 4xx gives a null status name, and a 5xx or a timeout ends the call with the busy text. A status name comes only from the read of the task's own project. A 413 and a 405 carry the JSON-RPC error body too.
  - **The fences.** `tests/unit/test_mcp_edge_protocol.py` holds D5 to D19, and its R8 classes hold D13 and the MCP half of D14 through the real gateway app. `test_mcp_edge_coverage.py` holds rules 3 and 4. Each rule was red first under a named mutation.
- 2026-10-13 — Fix round 1 of EA-3b-1. A test now holds the origin check above the body read. A deeply nested body answers 400 `-32700`, and a lone surrogate in an input is a tool error that names the input. The input patterns use `fullmatch`, so a trailing newline fails. A gateway request that the edge cannot sign or encode gives the busy text, never a 500.
- 2026-10-13 — Fix round 2 of EA-3b-1. A test now holds the wide guard of the gateway request: a transport error outside httpx gives the busy text, and the log names only its type. A JSON-RPC `id` with a lone surrogate answers 400 `-32600` with `id: null`, never a 500.
- 2026-10-13 — EA-3b-1 merged in #954 (`355c18bcf`). The spec audit of EA-3b-2 gave NO-GO for doc gaps only, and this change closes them. No code changed.
  - **`whoami` (§9.1).** Exactly six keys. `email` comes from `/auth/me`, and the other keys come from the token lookup, never from the `organization` object of `/auth/me`.
  - **`list_projects` (§9.1).** The order, the two filters, the null `parent_id` of a node whose parent is not visible, and the subtree counts. EA3-24 drops each archived project.
  - **The cursor.** It binds the tool name, so it has five keys. Three tools have pages.
  - **The timeline `meta`.** The list of the keys that callers write beyond the allowlist, `old_id` and `new_id` of a foreign-key change, and one rule for the four other row types.
  - **Fences.** Rule 4 of §9.5 names `edge_inputs`. §9.4 states the three rules of the new AST tests, at the anchors `:921` and `:951`. D9, D10, D13, D19 and D20 name what EA-3b-2 proves.
  - **Small fixes.** A link `id` is the id of the other task, and the edge drops `link_id`. `get_task` keeps `leveraged`, a plain boolean.
- 2026-10-13 — EA-3b-2 is built, and it ships dark. It is not merged. No migration, and no stamp of `last_used_at`.
  - **The tools.** `tools.py` adds `whoami`, `list_projects`, `get_project`, `get_task` and `get_task_timeline`, with the pinned keys of §9.1, `untrusted_text` on the free-text values that §9.1 marks (U), the `meta` allowlist and the answer table of EA3-16. `tools/list` now shows seven tools. ⚠️ This line first said "on each free-text value". That was false: `task_prefix`, `timezone` and the assignees left as bare strings. Fix round 1 below closes it.
  - **The cursor.** `cursor.py` binds the tool name into the payload. ⚠️ A `list_tasks` cursor made before this change has four keys, so the edge now refuses it, and the agent lists again. The feature is dark, so no client holds such a cursor.
  - **The fences.** `test_agent_oauth_server.py` exempts `tools.py` from the old identity refusal and holds it with three AST tests. Rule 4 of `test_mcp_edge_coverage.py` reads `edge_inputs`, and a new test pins them to the four named exceptions. `test_mcp_edge_protocol.py` holds D9, D10, D12 and D19 for the five tools, and its R8 class holds D13 through the real gateway app. Each rule went red under a named mutation first.
  - **Result shapes.** `list_projects` returns `projects`, `total` and `nextCursor`. `get_project` returns `project`, `statuses`, `types`, `fields` and `tags`. `get_task` returns `task`, `subtasks` and `links`. `get_task_timeline` returns `activities`, `total` and `nextCursor`.
  - **Choices this slice made.** The edge reads `GET /projects/tree` again for each page of `list_projects`. A live child of an archived parent keeps the parent's id in `parent_id`, because that parent is visible. `get_project` and `get_task` read their record first and then the other reads at the same time. A 404 or an empty `email` from `/auth/me` gives the busy text, because `whoami` reads no record.
  - **Value rules.** In `meta`, an id that is not a UUID becomes null, and a category, link type or field name must be key-shaped (`[A-Za-z0-9_.:-]{1,64}`). Fix round 1 replaced the rule for a list of member addresses. The edge drops a custom field key that is not key-shaped.
- 2026-10-13 — Fix round 1 of EA-3b-2, after the review and the verification. Each change went red under a named mutation first.
  - **Shape checks (P1).** A member wrote `task_prefix`, `timezone` and each assignee, and the gateway does not shape-check them, so they left as bare strings. Now each one is a plain string only in its shape, and any other value is `untrusted_text` (§9.1, mark (S)). The assignee rule also covers `added`, `removed` and `mentioned` in the timeline `meta`. A test pins the copy of the agent-name rule to `acb_skills.agent_paths.AGENT_NAME_RE`.
  - **The other plain keys.** The review read each key that the seven tools send as a plain string. The database types or CHECKs shape the ids, dates, `status`, `kind`, `category`, `link_type`, `field_type` and `field_key`. The authenticated context or the importer code writes `created_by`, `email` and `via`, and no member writes them as text.
  - **The AST tests (P2).** The request-read scan also refuses `getattr` with two arguments, `.scope`, `.url`, `.body()`, `.json()` and `.form()`. §9.4 and D20 now call the scans a heuristic and name what they miss. A new test holds that a tool call receives no request object.
  - **Pages.** The `list_projects` description warns that offset pages can skip or repeat a row. A test pins the warning in the three paged tools. The timeline puts the default `kind` into the filter hash, so a cursor made with no `kind` pages a call that sends `kind: "all"`.
- 2026-10-13 — Fix round 2 of EA-3b-2. The address shape let about 120 characters of dot-joined words leave as a plain string. Now a plain address is at most 64 characters in total, and its domain has at most 4 labels. Anything longer is `untrusted_text`. A test went red when each cap was reverted. A residual stays: a shaped value of up to 64 characters (an address, an agent name or a `field_key`) can still carry a short phrase (§9.4). The real fix is a gateway check of each assignee against the member directory, and §17 holds it as a follow-up.
- 2026-10-13 — The spec audit of EA-4 gave NO-GO, and it narrowed the dispatch to EA-4a. This change writes the decisions EA3-25 and EA3-26 (§6.1) in. No code changed.
  - **The slices (§15).** EA-4 splits into EA-4a (the consent page), EA-4b (the list of connections and revoke) and EA-4c (the organization switch and the client allow list). EA-4b and EA-4c do not gate a pilot.
  - **The consent page (§10.5).** The flow, the two BFF routes, the `Sec-Fetch-Site` check, the `SameSite=Lax` session, the frame headers, the consent ticket in Redis, `redirect_display` and the RFC 8252 allow list.
  - **Other edits.** §5.5 lets `CHROMELESS_ROUTES` gain `/connect`. §16 names the design rules of the page. §17 moves the absolute cap on the life of a connection out of EA-4. §15.1 holds the EA-4a commands.
- 2026-10-13 — EA-4a is built, and it ships dark. It is not merged. No migration.
  - **The gateway.** `check` gives the consent ticket, and `approve` spends it with one `GETDEL` before every other check. `TenantRedis` gains `getdel` and `OUTAGE_ERRORS`, so the route imports no Redis client. `redirect_display` and `redirect_app` replace `redirect_host`. `is_allowed_redirect_uri` is now the allow list of §10.5.
  - **The control plane.** The page `/connect/authorize`, the BFF routes `check` and `approve` with the `Sec-Fetch-Site` check, the frame headers in `next.config.ts`, and `/connect` in `CHROMELESS_ROUTES`. The page reads the organization name from `GET /api/auth/me`, so `AuthorizeView` carries no organization field.
  - **The fences.** `test_agent_oauth_server.py` holds the ticket, the display and the redirect forms. `relay.test.ts` holds the BFF rules, `sessionCookie.test.ts` holds `SameSite=Lax`, and `e2e/connect-authorize.spec.ts` holds the page and the frame headers. Each rule was red first under a named mutation.
  - **Choices this slice made.** A URI with no port shows `scheme://host`, with no port. The pin picker lists the nodes of `GET /projects/tree` in tree order, folders and spaces too, because a pin covers its subtree. `test_mcp_edge_signing.py` reads `_PRIVATE_SCHEME` in place of the removed `_SCHEME`.
- 2026-10-13 — Fix round 1 of EA-4a, after the adversarial review. Approve waits for 500 ms of focus. The headline names the verified host, and the client name is a claim. A port 0 redirect is refused. A lost session is a 401. A retry after a 503 gets a fresh ticket. §17 records the Cursor decision.
- 2026-10-13 — Fix round 2 of EA-4a. Only a JSON 503 from `approve` gets a fresh ticket and a retry. A lost answer or an answer that is not JSON ends the flow, so a retry never makes a second connection.
- 2026-10-13 — The owner took EA3-27 (§6.1): go-live is a pilot behind an organization allowlist, Fracktal first. §10.6 holds the rules, and §15 adds the slice EA-10p. No code changed.
- 2026-10-13 — EA-10p is built, and it ships dark. It is not merged. No migration. The default of `agent_access_orgs` allows no organization.
  - **The rule.** `agent_oauth.org_allowed` is the one reader of the list. The parser keeps `*` and canonical lower-case UUIDs, and it drops every other entry.
  - **The doors.** `agent_access.py` refuses `check` and `approve` with 403 `org_not_enabled` before the client fetch and the ticket. `agent_tokens` refuses a code exchange and a refresh with `ORG_NOT_ALLOWED`, and the refresh check comes before the spend. `bearer.py` answers 401. `read_live_connection` checks the organization of the row, so branch 0 refuses. Branch 0 and its digest do not change. The consent page shows the text of `org_not_enabled`.
  - **The fences.** `test_agent_oauth_server.py` holds the parser and doors 1 and 2. `test_mcp_edge_protocol.py` holds door 3 and the R8 round trip: connect, list, leave the list, get 401 and `invalid_grant`, come back. `test_mcp_edge_authority.py` holds door 4. `consent.test.ts` holds the page text. Each door and the fail-closed rule went red under a named mutation.
  - **Choices this slice made.** A code exchange for an organization that left the list spends the code. The refresh check comes before the spend, so the client can refresh again when the organization comes back. `lookup_token` does not check the list, so `/oauth/revoke` still works.
- 2026-10-13 — Fix round 1 of EA-10p, after the review. The refresh path now runs the reuse check before the list check. So a replay of a spent refresh token revokes the connection, also when the organization is off the list. §10.6 and the EA-10p row now say that each unit reads the list at process start, so a change needs a restart of `acb-gateway` and `acb-agent-edge`.
- 2026-10-13 — The spec audit of EA-10 gave NO-GO, and the owner answered it. The pilot runs behind the allowlist `AGENT_ACCESS_ORGS` (EA-10p), Fracktal first. §15.3 now pins EA-10a, EA-10b and EA-10c. The wildcard DNS record already covers `mcp.metorite.com`, so EA-10 needs no record.
- 2026-10-13 — EA-10a and EA-10b are built, and they ship dark. They are not merged.
  - **The unit.** `deploy/hostinger/acb-agent-edge.service` runs the edge on `127.0.0.1:8091` as `acb`, with its own env file. Its drop-in `50-hardening.conf` has the BH-2 shape and hides the root `.env`.
  - **The deploy step.** `scripts/vps_apply.sh` restarts the edge only when the unit is enabled. A box where nobody enabled it keeps the edge off.
  - **The Caddyfile.** The site `mcp.metorite.com` caps a body at `1MiB`, the cap of the edge. The `reverse_proxy` of `api.metorite.com` strips the three identity headers and the three `X-Agent-*` headers.
  - **The fences.** `tests/unit/test_agent_edge_deploy.py` holds each pin, and it runs the deploy step against a stub `systemctl`. `test_caddy_auth_gate.py` stays red until the owner adds the line `CADDY-AUTH-APPROVED <sha>`.
  - **The smoke.** A POST to `/mcp` with no body answers 400, because §9.6 checks the body before the token. So the 401 smoke of EA-10c sends a `ping` body.
- 2026-10-13 — Fix round 1 of EA-10, after the review. Each change went red under a named mutation first.
  - **The identity (P1).** The edge runs as a `DynamicUser`, not as `acb`. As `acb`, it could read each 0600 copy of the root `.env` (`.env.bak-<stamp>`, `.env-edit.*`), and `InaccessiblePaths` takes no glob. Its working directory is now its own empty runtime directory, so `Settings` opens no `.env`. The sandbox adds `ProtectHome=yes` and hides the workbench tree and the gateway data. §15.3 adds a pre-enable check for the box.
  - **The pooler budget (P1), OPEN.** The gateway already takes all 12 slots of the budget. The edge at `1` and `1` makes 14, which leaves 1 slot of the reserve of 3. The transaction pooler is not proven safe. The orchestrator decides, and the edge stays off until then.
  - **The claim (P2).** The sandbox does not narrow the database. §17 holds a narrower database role for the edge as a follow-up before GA.
  - **Notes.** The deploy probe reads no database, so step 3 of the EA-10c smoke is the database check. `bh2_strict_check` does not cover the edge.
- 2026-10-13 — The orchestrator decided the pooler budget of EA-10. The edge gets ONE connection, `DB_POOL_SIZE=1` and `DB_MAX_OVERFLOW=0`, and the gateway pool stays as it is. So the database sees 13 of 15, with a headroom of 2 for a migration and the backup job.
  - **The fence.** `TestThePoolCeilingFitsThePoolerInFront` now sums the gateway and the edge. It reads the edge pin from the table of §15.3, and an edge pool of 2 turns it red.
  - **The sync engine.** The edge never builds it. A test proves that no edge module imports `acb_graph`, and that the edge app builds no sync engine.
  - **The box check.** Step 4 of the pre-enable check reads the two pool lines of the edge env file. §17 holds the transaction pooler and the narrower database role for GA.
- 2026-10-13 — Fix round 2 of the EA-10 deploy. The drop-in also hides `/opt/acb/backups` and `/opt/acb/data`. The nightly dumps there are 0664, so the dynamic uid could read the data of every tenant. Pre-enable check 2b proves the line on the box. A follow-up in §17 asks the backup job to write its dumps at 0600.
- 2026-10-14 — The spec audit of EA-6 gave NO-GO, and it narrowed the dispatch to EA-6a. This change writes the decisions EA3-28 to EA3-31 (§6.1) in. No code changed.
  - **The nonce, the scope, the flag and the cap.** A write carries a nonce as a 9th signed line, and a GET keeps its eight lines. Branch 0 admits a write only for a listed pair, with `projects:write`, a fresh nonce and `agent_access_writes_enabled` on. A connection makes at most 120 writes in 600 seconds.
  - **The tools (§9.2).** `set_task_status` and `add_task_reference`, each with its route, and the references in `get_task`. EA-6b, EA-6c and EA-6d are later rows.
  - **The fences (§9.5, §9.6, §15).** Coverage rules 1 to 4 accept a listed write pair. The answer table gains the write rows. §15 splits EA-6 into four rows, and the acceptance of EA-6a is W1 to W8. §15.1 adds its line.
  - **WS-57.** §9.1 and §11 point to `task_references.md` §5, which owns `pm_task_refs`.
- 2026-10-14 — EA-6a is built, and it ships dark. It is not merged. No migration. The writes flag `agent_access_writes_enabled` defaults off.
  - **The gateway.** `agent_signing.py` signs the nonce as a 9th line on a write. Branch 0 reads the body of a write, and `deps._claim_write` holds the scope check, the nonce and the cap in `TenantRedis`. `agent_oauth.MCP_WRITE_ROUTES` names the two write pairs, and `MCP_ROUTES` adds them and the references read.
  - **The edge.** `gateway_client.write` sends the body, the nonce and `If-Match`. `tools.py` adds `set_task_status`, `add_task_reference` and `refs` in `get_task`. A 409 of the references route asks for a retry, and a 422 names `url` (a request of the WS-57 session).
  - **The fences.** The scan and the tripwire each gain the header `x-agent-nonce` and one `await request.body()` in `_agent_user`. Five new bypass shapes (84 in all) and one tripwire mutant hold the body read to that one shape. The digest also covers `_read_connection` and `_claim_write` now. The pins moved from 3.12 `1eded904…` and 3.13 `2ac62c16…` to 3.12 `8de38247…` and 3.13 `0cc35491…`. Coverage rules 1 to 4 accept a listed write pair.
  - **Fix round 1, after the review.** While `agent_access_writes_enabled` was off, the edge still listed the write tools, and a call met branch 0's 401, which the edge turned into the MCP 401. A client then dropped a good token and lost its reads. Now both units read the flag through `agent_oauth.writes_enabled()`. While it is off, the edge leaves the write tools out of `tools/list`, check 8 skips them, and a call answers a tool error with no gateway request. Branch 0 checks the flag after the signature and answers a signed write 403 with the detail `agent_writes_disabled`, which the edge maps to the same tool error. `_claim_write` builds the Redis client inside its `try`, so a client that cannot be built is the 503. §9.2 and §17 say that a write can start a workflow, and §17 holds a follow-up to narrow an agent's PATCH at the gateway. §15.3 puts the flag in both env files. The digest pins moved to 3.12 `6e8ea012…` and 3.13 `be0315c7…`. Six mutations each turned their test red: the list filter, the call refusal, the map of the coded 403, the coded 403 itself, the check 8 skip and the client build inside the `try`.
  - **The proof.** `tests/unit/test_mcp_edge_writes.py` holds W1 to W8 on R8, through the real gateway app. Each of eight mutations turned its test red: the scope check, the nonce, the flag, the cap, `If-Match`, the done refusal, the pin and the GET golden.
- 2026-10-14 — The owner decided D-EA-9 in chat. Integrations opens to all members, and each member sees an AI agents view. `integrations:manage` also sees every agent connection of the organization. The spec audit of EA-4b gave GO-NARROWED. §5.4 holds the decision and the gate. §15 EA-4b holds the store functions, the four routes and the acceptance A1 to A10. §15.1 adds its line. §17.1 adds the client table. The organization switch and the client allow list move to EA-4c, and the audit view moves to EA-8. `docs/external_agents_connect.md` now says only what is built. No code changed.
