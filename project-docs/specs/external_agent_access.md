# External agent access — Metorite as an MCP server

**Status: DECIDED (D97, 2026-10-10). EA-1 and EA-2a are BUILT and dark (2026-10-11), and EA-2a has had fix round 1. EA-1 is the three tables, the token store and the `agent_access_enabled` flag, default OFF. EA-2a is the authorization server core: the edge unit `apps/services/agent_edge` (not deployed) and the gateway route `/agent-access`. EA-2b and EA-3 to EA-11 are not built.**

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

| Need | Existing seam | Anchor |
|---|---|---|
| Member identity at the gateway | `get_current_user`, then `resolve_access` and `resolve_identity` | `packages/acb_auth/acb_auth/deps.py:410`, `access.py:1087`, `access.py:1247` |
| Acting as a member from a service | `headersActingAs`, and the `skill_projects` client | `workbench/control_plane/src/lib/gateway.ts:142`, `apps/skills/skill-projects/skill_projects/client.py` |
| Tenant binding for row-level security | `bind_tenant`, then `tenant_session` | `acb_common/db.py:210`, `acb_common/db.py:248` |
| Member access ceiling | `EffectiveAccess.intersect` ("an agent acting for a member must never exceed them") | `acb_auth/permissions.py` near line 350 |
| Project and task visibility | `resolve_visibility`, `load_visible_project`, `task_visibility_clause` | `gateway/routes/projects/core.py:1198`, `:1334`, `:1573` |
| A tool allowlist with a coverage fence | the `skill_projects` manifest, classes A, B, C and X | `apps/skills/skill-projects/skill_projects/manifest.py`, `tests/unit/test_projects_chat_coverage.py` |
| MCP tool hints | `read_only`, `destructive`, `idempotent`, `open_world` | `packages/acb_skills/acb_skills/tool_annotations.py` |
| Provenance stamp | `X-Actor-Via`, stored in `pm_activities.meta.via` | `gateway/routes/projects/core.py:75-98` |
| Audit write | `acb_audit.record` | `packages/acb_audit/acb_audit/log.py` |
| Optimistic concurrency | `If-Match` on `PATCH /projects/tasks/{id}` | `gateway/routes/projects/tasks.py:479` |

**Gaps. Each one becomes a slice:**

1. No token store, no client registry, no consent page and no `/.well-known` metadata.
2. The gateway trusts the internal bearer plus `X-User-Email`. Any process that holds that bearer can act as anyone. See §10.
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
        │  2. load the connection, check member and org are live
        │  3. check scope, pins, rate limit
        │  4. call the gateway as that member
        ▼
  gateway  /projects/*  /memory/*  ...      (unchanged routes, unchanged RLS)
```

The consent page is part of the control plane, because only the control plane holds the member's NextAuth session.

```
agent ──authorize──▶ app.metorite.com/connect/authorize ──(NextAuth session)──▶ consent
agent ──token──────▶ authorization server endpoint ──▶ opaque token + refresh token
```

### 5.1 Where the edge runs (D-EA-2)

**Recommended: a separate small unit named for its job, with its own systemd sandbox** (box_hardening.md BH-2 shape). It holds the internal bearer, so a bug there is an impersonation bug. A narrow unit has a small blast radius, and it keeps `/mcp` traffic off the main gateway process.

**Alternative: a router inside the gateway.** It needs no second bearer holder. It also puts public, hostile traffic in the process that serves every member. The route would join `PUBLIC_ROUTES` (`main.py:625`) and authenticate itself.

The tool code is the same in both. Both call the existing routes with the same member headers that `skill_projects` uses. So the choice is a deploy choice, and it does not block EA-1 to EA-3.

### 5.2 The host (D-EA-1)

**Recommended:** the edge lives on its own host, `mcp.metorite.com`. No session cookie is ever sent there. The consent page stays on `app.metorite.com`. The canonical resource identifier is `https://mcp.metorite.com/mcp`. A DNS record is an owner gate (work_plan.md §6).

### 5.3 Protocol facts (as of the MCP specification dated 2026-07-28)

Re-read the spec at build time. It moves fast. These are the points that bind us.

- The server **must** publish OAuth 2.0 Protected Resource Metadata (RFC 9728). A 401 carries a `WWW-Authenticate` header with `resource_metadata`.
- The authorization server **must** publish RFC 8414 metadata or OpenID discovery.
- A client registers by one of three ways, in this order of preference: Client ID Metadata Document, pre-registration, or Dynamic Client Registration. The spec now marks Dynamic Client Registration as deprecated and kept for older servers.
- The client **must** send the `resource` parameter (RFC 8707) in the authorization request and in the token request. The server **must** accept only a token issued for its own resource.
- PKCE is required. The server must not accept a token in a query string. The server must never pass a token on to another service ("no token passthrough").
- The server returns **403** with `error="insufficient_scope"` and the needed `scope` when a call needs more. It sends all needed scopes in one challenge.
- The authorization response should carry `iss` (RFC 9207). We send it.
- The tool list **may** differ by authorization. It **must not** differ by connection state. The list is stateless. No MCP session holds state for us.
- A tool failure that the model can fix returns `isError: true` with text. A malformed request returns a protocol error.
- A tool may return `structuredContent` that matches an `outputSchema`. It should also return the same JSON as text.
- A server **must** validate inputs, check access, limit the rate and sanitize outputs.

### 5.4 Where members and admins manage it: the Integrations app (D-EA-9, recommended)

The owner suggested on 2026-10-11 that the Integrations app should list inbound and outbound connections together. This spec recommends that. It is not yet a decision.

**What exists today.** The Integrations app is `preview`, in the Admin section, at `/integrations`. It has the tabs APIs, Email, MCPs, Plugins and Skills. The MCPs tab is a registry of outside servers that Metorite reads, and it is empty. `crm_platform.md` §7.1 says WS-54 will make this app the admin console over one registry, `integration_connections`. That registry holds the connections that Metorite makes to a service.

**The recommendation: one app, two directions, two stores.**

- **Outbound** means Metorite uses a service. Examples are Zoho, Gmail and an outside MCP server. These rows live in `integration_connections`.
- **Inbound** means an outside agent uses Metorite. These rows are the `agent_connection` rows of §7.
- The app shows one list with a Direction filter. Each row shows the owner, the member, the scopes, the health, the last use and the date it began.
- Both kinds offer Disconnect. For an inbound row, Disconnect revokes the connection.
- The tables stay apart. `integration_connections` holds encrypted credentials that Metorite uses. `agent_connection` holds token hashes and scopes, and its token lookup runs before the tenant is known (§10.3). A merge would put those RLS-exempt lookup rows beside the encrypted credentials.
- One listing route joins the two stores for the screen. It is a read model and adds no third store. WS-54 owns the route, and this spec adds the inbound half.

**What the app adds for inbound.**

- A **Connect an agent** panel. It shows the URL `https://mcp.metorite.com/mcp`, a copy button, and one short setup note for each client.
- The member's own agents, with Revoke.
- For an admin: the organization switch, the client allow list, the audit view and a Revoke for any member's agent (D-EA-8).
- The existing MCPs tab keeps its job and gets a plain name, "Outside MCP servers". That removes the clash between "MCP we read" and "MCP we serve".

**Two constraints to settle in WS-54.**

1. The app sits in the Admin section today, so a member who is not an admin may not reach it. A member must reach their own agents. WS-54 must show the app to every member and hide the admin tabs from non-admins. `launch_surface.md` §2 and `nav.test.ts` own that change.
2. The app is `preview`. Promoting it to `live` is an owner decision (H-21).

**If WS-54 is late.** EA-4 builds the member pane as one component. The component first mounts in Settings and later mounts in the Integrations app. No code moves except the mount point.

### 5.5 Where the code lives, and the fixed addresses

- **Issuer:** `https://mcp.metorite.com`. RFC 8414 and RFC 9207 need one fixed value.
- **Public endpoints** run with the edge unit (D-EA-2): `/.well-known/oauth-protected-resource`, `/.well-known/oauth-authorization-server`, `/oauth/token` and `/oauth/revoke`. They are not in the gateway `PUBLIC_ROUTES` list.
- **Authorize page** at `https://app.metorite.com/connect/authorize`. EA-4 builds it.
- **Store:** `packages/acb_auth/acb_auth/agent_tokens.py`. It follows `acb_auth/email_otp.py`.
- **Authenticated half:** `apps/services/gateway/gateway/routes/agent_access.py`, prefix `/agent-access`, behind default-deny.
- **Edge unit:** a new uv member `apps/services/agent_edge/`. EA-3 adds `/mcp` to it.
- **Flag:** a `Settings` field `agent_access_enabled`, default false, which reads `AGENT_ACCESS_ENABLED`.
- **Do not touch:** `routes/oauth.py` (a different OAuth client, retired by WS-54), `routes/integrations.py`, `integration_connections`, `outbound_guard.py`, `nav.ts` and `launch_surface.md`. Reuse `outbound_guard.py` and never edit it. Do not name a new module `oauth.py`.
- **Lifetimes** are `Settings` defaults, so a test can assert them.
  - Access token: 3600 seconds.
  - Refresh token: 30 days.
  - Authorization code: 60 seconds.
  - Idle connection: 90 days.
  - Unused registered client: 1 day.
- **No grace window.** A client that sends two refreshes at once loses the connection. The client must serialize its refreshes.

## 6. Decisions D-EA-1 to D-EA-9 (D-EA-1 to D-EA-8 taken, D97)

The owner accepted every default below in chat on 2026-10-10, with the words "go ahead with your recommendations for all". Nothing here re-opens D1 to D94. D97 in work_plan.md §3 records them.

| # | Question | Accepted | Why |
|---|---|---|---|
| D-EA-1 | The edge host | `mcp.metorite.com` | No session cookie reaches it. §5.2 |
| D-EA-2 | Edge in its own unit, or a gateway router | Own unit | Smaller blast radius for the internal bearer. §5.1 |
| D-EA-3 | Authorization server: a vetted library inside our code, or a hosted OAuth server | Library inside our code | We already own the member session and the tenant tables. A second identity system is a second seam |
| D-EA-4 | May a connection reach the member's personal projects | **No by default.** The consent page offers an opt-in box, off | Personal projects are private to one member. A wrong default leaks private work |
| D-EA-5 | A personal access token for headless agents (CI, a server) | Not in version 1 | An interactive OAuth flow covers Claude Code and desktop clients. A token is a long-lived secret and needs its own review |
| D-EA-6 | What "company context" means | The organization name, domain, branding, and the `org:global` memory the member may read | No company profile route exists today. A richer profile is a separate spec. See H-15 in the org profile backlog |
| D-EA-7 | Price | No charge. The agent uses the member's seat | CLAUDE.md says pricing and commercial calls need owner input first. The tool calls cost no AI credits |
| D-EA-8 | Who may connect | An organization admin switches the feature on. Then any member may connect, unless the admin restricts clients to an allow list | Default OFF keeps a new tenant safe |
| D-EA-9 | Where members and admins manage connections | **Recommended, not yet decided.** The Integrations app (WS-54) lists inbound agents and outbound services in one list. The two stores stay apart. §5.4 | The owner suggested it on 2026-10-11. One place to see and revoke every connection |

## 7. The connection model

A **connection** is one agent client that one member has authorized in one organization.

| Field | Meaning |
|---|---|
| `id` | Opaque id of the connection |
| `organization_id`, `member_email` | Bound at consent. Never read from a request. The edge re-checks membership on every call |
| `client_id`, `client_name`, `client_origin` | Who the agent is. The origin comes from the verified metadata document, not from a self-reported name |
| `scopes` | The ceiling. See §8 |
| `pinned_projects` | Optional list of project ids. Empty means every project the member sees |
| `include_personal` | Opt-in flag from D-EA-4. Default false |
| `created_at`, `last_used_at`, `expires_at`, `revoked_at` | Lifecycle. A connection expires after 90 days of no use (proposed) |

### 7.1 The authority rule

On every call the edge computes the following, and it never caches the result for longer than the gateway's own 60 second access cache.

1. The token is valid, unexpired, unrevoked, and its audience is this edge.
2. The member is still in the organization and still holds a seat. If not, answer 401.
3. The tool's required scope is in `connection.scopes`. If not, answer 403 `insufficient_scope`.
4. The gateway route runs as the member. Row-level security and the visibility closure then decide what the member sees.
5. If the connection has pins, the edge checks that the target project is in the pins or below one. If not, answer with the same text as "not found".
6. If a record is not visible, the answer is "not found". It is never "forbidden". This is rule R5.

The result is `member's live access ∩ scopes ∩ pins`. A scope never adds access. A removed grant never waits for a token refresh.

### 7.2 What a connection cannot do

- It cannot create or widen another connection.
- It cannot write a grant, a role, a seat or a setting.
- It cannot see a sealed tree (D63). It cannot see a personal project unless `include_personal` is true.
- It cannot call a route that the manifest does not list.

## 8. Scopes

Scopes are coarse on purpose. A member must be able to read the consent page in ten seconds.

| Scope | Lets the agent | Tools |
|---|---|---|
| `projects:read` | Read projects, tasks, comments and the timeline the member can see | `list_projects`, `get_project`, `list_tasks`, `get_task`, `get_task_timeline`, `search_tasks` |
| `projects:write` | Create tasks, change fields and status, comment, assign to the member, and add references | `create_task`, `update_task`, `set_task_status`, `add_comment`, `claim_task`, `add_task_reference` |
| `context:read` | Read the company context of D-EA-6 | `get_company_context`, plus the matching resources |
| `offline_access` | Receive a refresh token | none. Per the MCP spec the server does not advertise this scope as a resource need |

`projects:write` includes `projects:read`. The server must treat a broader scope as covering a narrower one.

Every future scope needs a spec amendment. The MCP spec says a client must treat the scope in a challenge as authoritative. We therefore challenge with the exact scope a tool needs.

## 9. The tool surface

Tool names use letters, digits and underscores. Descriptions are written for a model: they say what the tool does, when to use it, and what it returns. The voice contract in `agent_writing_voice.md` binds the text a tool returns to a human, and not the schema.

### 9.1 Reads (class A, `readOnlyHint: true`)

| Tool | Input | Returns |
|---|---|---|
| `whoami` | none | The member, the organization, the connection scopes and pins, and the server time |
| `list_projects` | `parent_id?`, `text?`, `limit`, `cursor` | Projects the connection may see, with `id`, `name`, `path` and `task_counts` |
| `get_project` | `project_id` | Fields, statuses, task types, tags and custom field definitions |
| `list_tasks` | `project_id?`, `status?`, `type?`, `tag?`, `assignee?`, `text?`, `updated_since?`, `limit`, `cursor` | A page of tasks with `id`, `key`, `title`, `status`, `assignees`, `due_at`, `version` |
| `get_task` | `task_id` | The full task, its description, references, relations and a `version` |
| `get_task_timeline` | `task_id`, `kind?`, `limit`, `cursor` | Comments and events |
| `search_tasks` | `query`, `project_id?`, `limit` | Ranked hits across the visible tasks |
| `get_company_context` | `topics?` | The D-EA-6 context as structured text |

Each list tool pages with an opaque cursor, caps `limit` at 100, and returns `nextCursor`. A list never returns a task outside the member's visibility. The tool uses `task_visibility_clause`, as the REST route does.

### 9.2 Writes (class B, undoable, and class C, hard to undo)

| Tool | Class | Input | Effect |
|---|---|---|---|
| `create_task` | B | `project_id`, `title`, `description?`, `type?`, `status?`, `tags?`, `due_at?`, `idempotency_key?` | Creates one task. The same `idempotency_key` returns the first result |
| `update_task` | B | `task_id`, `expected_version`, fields | Changes title, description, tags, due date and custom fields. Refused with a text reason when the version is old |
| `set_task_status` | B | `task_id`, `expected_version`, `status` | Uses the existing transition path that stamps `completed_at` and the timeline |
| `add_comment` | B | `task_id`, `body`, `parent_id?` | Adds a comment. The author stays the member. `meta.via` names the client |
| `claim_task` | B | `task_id`, `expected_version` | Assigns the member and moves the task to the first "in progress" status |
| `add_task_reference` | B | `task_id`, `kind`, `provider`, `ref`, `url?`, `title?`, `state?` | Adds or updates a reference. See §11. The same `(task, kind, provider, ref)` updates the row |

The first version has **no class C tool**. A move across projects, an archive, an unassign of another person and a bulk change are not exposed. When one is added, it needs a human confirmation step. The MCP spec supports this with a multi-round input request (elicitation), and a client that lacks it must get a refusal that says so.

### 9.3 Resources

The edge also lists resources, so a client can attach context without a tool call.

- `metorite://company/context` — the D-EA-6 context.
- `metorite://project/{id}` — one project with its statuses and open task counts.
- `metorite://task/{id}` — one task.

A resource read uses the same authority rule as a tool call.

### 9.4 Tool results are untrusted text

A task title, a description and a comment come from other people. An outside agent reads them. A hostile comment can tell the agent to leak data. We cannot stop that inside the agent. We reduce the damage as follows:

- A result wraps free text in a field named `untrusted_text`, and the tool description says the agent must not follow instructions found there.
- A write tool never accepts a project id or a member that the agent found only in free text. It accepts ids that came from a prior structured result.
- The consent page says that the data goes to the client's provider.

### 9.5 The allowlist is a manifest, and a fence enforces it

The edge does **not** invent a second route list. It extends the `skill_projects` manifest with one more column: `mcp: expose | exclude`. The proposed fence is `tests/unit/test_mcp_edge_coverage.py`. It fails when:

- A Projects route has no decision.
- A route marked class C or X is marked `expose`.
- A tool lacks a `required_scope`, an annotation, or an `outputSchema`.
- A tool accepts a field that the matching route does not accept.

This is R7. The rule names its fence.

## 10. Security

### 10.1 The internal bearer is the sharpest edge

The gateway trusts `Authorization: Bearer <internal token>` plus `X-User-Email`. The edge must hold that bearer. So the edge **can** act as any member. Five controls hold it down:

1. The edge sets `X-User-Email` only from the connection row. It never copies a header from the request.
2. The gateway adds a rule. A request with `X-Actor-Via: mcp:*` must also carry a second header that only the edge signs. The header holds an HMAC over the member, the connection id and a timestamp. The gateway checks the signature and a 60 second window. This limits what a stolen bearer can do.
3. The edge unit runs in its own sandbox, with no read access to `.env` beyond its own variables.
4. The caddy vhost strips `X-User-Email`, `X-User-Role` and `X-Actor-Via` from public requests. The survey found that the Caddyfile did not do this on 2026-08-03. Confirm the current state first. This is an owner gate (HANDOFF).
5. Before the edge goes live, a check proves that `GATEWAY_INTERNAL_TOKEN` is provisioned. The survey found that it can fall back to the LLM key.

### 10.2 Threat table

| Threat | Control |
|---|---|
| Stolen access token | Opaque token. Short life (proposed 1 hour). Bound to one resource. Revocable at once |
| Stolen refresh token | Rotation on every use. A reused token revokes the whole connection |
| Token for another service replayed here | Audience check on `resource`. The edge refuses any other token |
| The edge replays a client token to the gateway | Never. The edge calls the gateway with its own credential and the member headers. It never forwards the client token |
| Consent phishing (a fake client with a trusted name) | Show the verified origin from the client metadata. Show the redirect host. An admin may restrict to an allow list (D-EA-8) |
| Open registration abuse | Client ID Metadata Documents need a fetch with a size, time and address limit (no private addresses). Dynamic registration is rate limited and expires unused clients after one day |
| Confused deputy | One consent per client and per member. State and PKCE are required. `iss` is checked by the client, and we send it |
| A write by a read-only connection | Scope check before the call, and the fence in §9.5 |
| Over-reading by a broad grant | Pins, the personal-project opt-in, a 100-row cap, and a per-connection quota |
| Prompt injection from a task text | §9.4 |
| Cross-tenant read | The edge never takes the organization from the request. The gateway binds the tenant from the member (R5, R11). Fence: EA-9 |
| Runaway agent loop | Rate limits, and an idempotency key on create |

### 10.3 Tenancy of the new tables

The token lookup happens **before** the tenant is known. A row-level-security table cannot serve it. The same problem exists for `user_identity` and `org_membership`, which are RLS-exempt.

- `agent_token` holds the access tokens, the refresh tokens and the authorization codes, told apart by a `kind` column. A SHA-256 hash is the lookup key. The table carries `organization_id` with a foreign key to `organization`. The lookup returns the connection id **and** the organization id, so the edge can bind the tenant before it reads anything else.
- `agent_token` sits in `gen_tenant_migration.EXEMPT` with that reason. It sits on the tenant plane as an exempt table, as `186_auth_email_otp_token.sql` does.
- `agent_connection` is tenant-scoped. After the lookup, the edge binds the tenant and then reads it under RLS.
- `agent_client` (a cache of client metadata) holds no tenant data. It is global and exempt.
- Two new exempt entries take the map from 13 to 15. `tests/unit/test_tenant_coverage.py` caps the map at 15, so this spec adds no third entry. A code in its own table would have made 16.
- Every table carries `organization_id` where it holds tenant data, and `tests/unit/test_tenant_coverage.py` must pass (R5).

## 11. Schema addition: task references

A pull request, a commit and a branch are structured facts. A comment hides them, and a custom field cannot hold several. This spec proposes one table.

`pm_task_refs`

| Column | Type | Note |
|---|---|---|
| `id` | uuid | |
| `organization_id` | uuid | Tenant scope |
| `task_id` | uuid | |
| `kind` | text | `pull_request`, `commit`, `branch`, `url` |
| `provider` | text | `github`, `gitlab`, `bitbucket`, `other` |
| `ref` | text | PR number, commit SHA or branch name |
| `url`, `title`, `state` | text | `state` is `open`, `merged`, `closed` for a pull request |
| `via` | text | The actor-via stamp |
| `created_by`, `created_at`, `updated_at` | | |

- A unique key on `(organization_id, task_id, kind, provider, ref)` makes the tool idempotent.
- Each add or state change writes a `link` activity. The `link` type already exists, so no change to the activity CHECK is needed.
- `pm_tasks.source` gains the value `mcp` in the same migration, with the `TASK_SOURCES` constant and its fence updated by hand. Without it a task made by an agent reads as `manual`.
- The migration is an expand-only change (R6). Take the number at build time (R1). The next free number is **not** named here.
- The task detail screen shows the references. That is a Projects UI slice. See EA-6.

**A human-readable task key.** A commit message or a pull request title should carry the task key, so a person can read the link. `get_task` returns `key`. The survey did not confirm the key format, so EA-6 must read `pm_tasks.task_number` and the project key rules first.

## 12. The coding-agent scenario, end to end

Project 7 holds the backlog. The member connects Claude Code once. The steps below show the calls.

1. **Connect.** The member runs the client's "add MCP server" command with `https://mcp.metorite.com/mcp`. A browser opens on `app.metorite.com/connect/authorize`. The member signs in, picks `projects:read`, `projects:write` and `offline_access`, pins project 7, and approves.
2. **Find work.** The agent calls `list_tasks` with `project_id=7`, `type=coding`, `status=todo`. It reads one task with `get_task` and notes the `version`.
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
| EA-3 | **The edge, read tools.** `whoami`, `list_projects`, `get_project`, `list_tasks`, `get_task`, `get_task_timeline`, `search_tasks`. The authority rule of §7.1. The signed header of §10.1, control 2, lands here, with the gateway check | A test shows a member sees the same tasks over MCP as in the REST route. A removed grant removes the task on the next call | AGENT-SAFE |
| EA-4 | **Consent and admin.** The consent page, the Connected agents list in the Integrations app (§5.4), the org switch, the client allow list, revoke. The consent page checks `Origin` and uses a `SameSite` session. It never answers approve with a 3xx. `check` mints a one-time consent ticket, and `approve` must present it, so an approval cannot skip the screen. The redirect URI rules move from a deny list to the forms of RFC 8252: `https`, loopback `http` with any port (§7.3) and a reverse-DNS private-use scheme. EA-4 or EA-2b makes that change | A revoked connection gets 401 on the next call. A suspended member gets 401. A cross-site approve and an approve with no ticket are refused | AGENT-SAFE |
| EA-5 | **Company context.** `get_company_context` and the three resources, after D-EA-6 is answered | The result holds no data that the member could not read in the app | AGENT-SAFE |
| EA-6 | **References and write tools.** The `pm_task_refs` migration, the `mcp` source, the write tools, the Projects UI for references | The coding scenario of §12 passes end to end on a real database. A stale version is refused | AGENT-SAFE |
| EA-7 | **Limits.** A per-connection and per-member limiter on the tenant-prefixed Redis wrapper. A 100-row cap | A test shows the 429 and the `Retry-After` value | AGENT-SAFE |
| EA-8 | **Audit.** One `acb_audit.record` row per call, with `organization_id` set on the event. An admin read route and view | A denied call writes a row. The admin view lists calls by connection | AGENT-SAFE |
| EA-9 | **Fences.** `test_mcp_edge_coverage.py`, a tenant fence, a "no client header reaches the gateway" fence, a "no token forwarded" fence | Each fence fails when a mutation breaks its rule (verified red first) | AGENT-SAFE |
| EA-10 | **Deploy.** The unit file, the Caddy block, the DNS record, the Caddy header strip, the proof that `GATEWAY_INTERNAL_TOKEN` is provisioned (§10.1, control 5), the flag | The flag turns ON for one test organization. The evidence is the deployed SHA and a real client round trip | OWNER-GATE |
| EA-11 | **Hardening review.** An adversarial review of §10, then a second pass with a real outside client | Both reviews are recorded in this file | AGENT-SAFE |

Order: EA-0, then EA-1 and EA-2, then EA-3, EA-4 and EA-5, then EA-6 to EA-9, then EA-10 and EA-11. EA-6 and EA-5 can run in parallel after EA-3.

### 15.1 Verification commands (proposed names, created by the slices)

- `uv run pytest tests/unit/test_agent_connection_tokens.py`
- `uv run pytest tests/unit/test_mcp_edge_coverage.py tests/unit/test_mcp_edge_authority.py`
- `uv run pytest tests/unit/test_tenant_coverage.py` (must stay green, with the exempt entries named)
- `bash scripts/dev_db.sh`, then `eval "$(bash scripts/dev_db.sh --export)"` before every R8 run
- `npx @modelcontextprotocol/inspector` against the edge, for the conformance run of EA-2

Name each test file in the PR that creates it. Name the files you mean when you run pytest. Do not run `tests/unit/` as a directory.

## 16. Shell manifest (R9)

This spec adds **no app**. Its screens join the Integrations app (§5.4, D-EA-9). That app owns the shell manifest entry. This spec adds two jobs to it. The jobs are "Connect an agent" and "Review agent activity".

Both jobs use `ManagerFrame` and the shared primitives. They add no palette, bell, assistant rail or command-bar listener. The consent page is a standalone screen on the control plane and uses the same design system.

## 17. Open risks

- The MCP specification moves. The 2026-07-28 text removed the session and added input-required results. Pin the version in code and re-read it at EA-2.
- A client may speak only an older revision, or only Dynamic Client Registration. EA-2 supports both, and its test list names the clients we test. The list is not written yet.
- Dynamic Client Registration and metadata fetches are open to abuse. The limits in §10.2 are proposals.
- The signed-header rule in §10.1 changes the gateway auth path. It needs `guard-write` grants and an adversarial review. It is the riskiest change in this spec.
- Data that an agent reads goes to its provider. That is the customer's choice, and it is not a Metorite sub-processor. The terms page must say so. This is an owner and legal point (work_plan.md WS-37).
- The company context is thin today (D-EA-6).
- A connection has no absolute maximum life. A thief who rotates a stolen refresh token at least once in 30 days keeps the connection alive until somebody revokes it. Proposal: an absolute cap, for example 365 days from `created_at`, as a `Settings` default. EA-4 decides it. EA-1 and EA-2a do not build it.
- The consent routes of EA-2a trust the session alone. A cross-site request could drive `approve` while a member is signed in. EA-4 closes this with an `Origin` check, a `SameSite` session, no 3xx from approve and a one-time consent ticket from `check` (§15 EA-4).
- The redirect URI rules of EA-2a are a deny list of dangerous schemes. A deny list is never complete. EA-4 or EA-2b moves them to the RFC 8252 forms.
- D-EA-2 says the edge unit has a smaller blast radius. That is true only after the EA-10 sandbox. The edge holds the database DSN, and EA-3 adds the internal bearer. Without the sandbox, a bug in the edge reaches as far as a bug in the gateway.

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
