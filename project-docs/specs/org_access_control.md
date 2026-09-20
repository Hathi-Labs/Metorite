# Organization Access Control — implementation spec

> **Status:** 🟢 Phase 1 shipped. **Multi-tenant user management now hands off to the multiplayer agent collaboration workstream — see [§10](#10-handoff-multiplayer-agent-collaboration).** §10.4's requested spec exists: [`groups_sessions_authority.md`](groups_sessions_authority.md) decides the group primitive, `chat_session_participant`, and the authority rule (intersection). ⚠️ **Tenancy re-framed 2026-08-08 (D15, WS-29):** this spec's per-deployment framing ('the single-tenant deployment', 'the person who owns the deployment') predates row-tenancy. The intra-org model it documents is unchanged and correct; read `saas_multitenancy.md` for anything cross-tenant. MT-1a/H6 reopen the identity store (user_identity + org_membership); §10's 'complete, not being extended' claim is superseded for the tenancy axis only.
> **Created:** 2026-07-29 · **Handed off:** 2026-07-29 · **Verified against code:** 2026-08-03 (WS-14 doc remediation — §8 Phase 2 row and §9 Q2 only; the rest keeps its earlier stamps). That pass found **`email_account_member` does not exist** (0 hits repo-wide in `*.sql` and `*.py`) and that §9 Q2 gates `department_centers.md` Phase C4 — both annotated in place. **Repair round the same day (off `264f881e`):** the Q2 annotation's claim that `pending_actions.actor` "is the proposing *agent* … not the human behind it, and no group is derivable from any existing column" was **false** and is corrected in §9 Q2 — two of `actor`'s six writers embed the requesting human's email. Q2 remains OWNER-DECISION on the corrected evidence.
> **Scope:** Turning the single-tenant deployment *(wording predates D15 — one org per deployment is no longer the model)* into a real multi-user organization: named members, roles, per-user feature/agent access, and one enforcement path the whole platform shares.
> **Parent research:** [`multi_user_organization_research.md`](multi_user_organization_research.md) — the *why* and the long-horizon (SaaS, memory scoping, entity-graph RLS) design. This document is the *what we are building now*, and it deliberately implements a subset.
> **Companion:** [`permissions_sandbox_b6.md`](permissions_sandbox_b6.md) (tool-level risk gating — orthogonal: that answers "may this *tool call* proceed", this answers "may this *person* reach this feature at all").

---

## 1. What this spec covers

The research doc scopes a 22–32 week programme across identity, memory, credentials, the entity graph, and the Action Broker. That is the right destination, but nothing in it is enforceable until there is a **principal with a resolvable set of permissions**. Everything else hangs off that.

So Phase 1 is deliberately narrow and load-bearing:

**In scope**
- One organization row; every member belongs to it. Schema carries `organization_id` so a second org is a data change, not a migration.
- A member lifecycle: invited → active → suspended → removed.
- Roles as named permission bundles, seeded with five system roles, extendable with custom roles.
- **Per-user grants and denials on top of roles** — this is the specific ask: "some users don't get WhatsApp, don't get the app creator, but do get certain agents."
- One resolution algorithm (deny-wins) in one package, used by the gateway, the Next.js proxy layer, and the UI.
- Admin screens to do all of the above without SQL.

**Explicitly out of scope for Phase 1** (tracked in the research doc, not regressed by anything here)
- Memory scoping (`personal` / `team` / `organization`) — §7 of the research doc.
- Per-user integration credential scoping — §8.
- Entity-graph row visibility + Postgres RLS — §9, §16.5.
- Modules/teams as a first-class container — §5. Phase 1 uses flat org membership; the `module` concept is what Phase 2 adds when "the sales team" needs to mean something.
- Everything SaaS (§17): subdomains, billing, tenant isolation of agent runtimes. *(in scope since 2026-08-08 — WS-29, saas_multitenancy.md §0.9.3)*

**Why this order.** Modules, memory scoping, and credential scoping are all *consumers* of a resolved principal. Building them first means building three private half-implementations of the same permission check. Phase 1 is that check.

---

## 2. Current state (verified against code, 2026-07-29)

| Concern | Today | File |
|---|---|---|
| Sign-in | NextAuth v5 + Microsoft Entra ID, tenant-gated. Session carries `email` and `name` only. | `workbench/control_plane/src/auth.ts` |
| Role assignment | **An env var.** `EXECUTIVE_EMAILS` is parsed into a `Set` and compared against the session email. | ~50 copies of `buildGatewayHeaders()` across `src/app/api/**/route.ts` |
| Role transport | `X-User-Email` + `X-User-Role` headers alongside the internal bearer token. | `packages/acb_auth/acb_auth/deps.py` |
| Roles | `executive` \| `employee` \| `agent` (StrEnum, no DB backing). | `packages/acb_auth/acb_auth/roles.py` |
| Enforcement | `require_role(UserRole.EXECUTIVE)` — a binary gate. | `deps.py:189` |
| User table | `app_user(id, email, display_name, avatar_url, role, last_login_at, …)`. No org, no status, no invite trail. | `infra/postgres/09_app_user.sql` |
| Feature visibility | **None.** `NAV_SECTIONS` is a static array; every signed-in user sees every pane. | `workbench/control_plane/src/lib/nav.ts` |
| Agent visibility | **None.** Any registered agent is listable and runnable by anyone who can reach the gateway. | `agent_registry.json`, gateway `_AGENT_REGISTRY` |
| Nearest working precedent | Custom Apps: `apps.visibility ∈ (private, people, org)` + `app_grants(app_id, subject, role)` where subject is an email / `agent:<name>` / `agents:*`. | `infra/postgres/114_custom_apps.sql` |

Two observations drive the design:

1. **The app-grants model already works and users already understand it.** Phase 1 generalises its shape (a subject, a resource, a role) rather than inventing a second vocabulary. `app_grants` stays as-is; it is the per-app rung below org-level access.
2. **`EXECUTIVE_EMAILS` duplicated across ~50 route files is the actual blocker.** Adding a role today means editing an env var and redeploying, and any route that forgets the helper silently downgrades the caller to `employee`. Phase 1 collapses this to one DB-backed helper.

---

## 3. Model

### 3.1 Principals

A **principal** is whoever a request acts as. Three kinds:

| Kind | Identified by | Source |
|---|---|---|
| Member | `app_user.id`, addressed by email | Entra ID sign-in |
| Service | the internal bearer token | `GATEWAY_INTERNAL_TOKEN` |
| Agent | `agent:<name>` | agent runtime, acting **on behalf of** a member |

**Email stays the wire identity.** `app_user.id` is the FK target, but every existing table that scopes by user does so by email (`app_tool_grants.user_email`, `app_grants.subject`, `app_audit.user_email`, `apps.owner_email`). Re-keying them all to UUID is a large, risky, and — for Phase 1 — pointless migration. `UserContext` carries both.

**An agent never gains authority by existing.** An agent run inherits the invoking member's resolved permission set, intersected with the agent's own declared scope. It cannot exceed its caller. This is the same rule the research doc states in §11.1 (sub-agent inheritance) and it is why the permission set — not just the role name — has to travel with the request.

### 3.2 Roles

A role is a named bundle of permissions, scoped to an organization. Five are seeded as `is_system` (not deletable, not editable) and admins can create more.

| Role | Intent | Grants |
|---|---|---|
| `owner` | The person who owns the deployment. *(under D15: owner is per-organization, not per-deployment)* | `*` |
| `admin` | Runs the platform day to day. | `admin:*`, `feature:*`, `agents:*`, `apps:*`, `integrations:*`, `data:org:read` |
| `manager` | Reads the member directory. Cannot change platform config. | `feature:*` · `agents:run:*` · `apps:use:*` · `apps:create` · `data:org:read` · `admin:members:read` |
| `member` | Default for a new employee. | `feature:*` · `agents:run:*` · `apps:use:*` |
| `guest` | Contractor / external collaborator. | `feature:chat`, `apps:use:*` |

Plus one non-assignable service role, `agent_service`, for the internal bearer path.

A member may hold several roles, and grants are the **union**.

🔴 **EVERY APP IS OPEN TO EVERYBODY. A role narrows what you may DO, not what
you may see.** Owner directive, 2026-09-21, applied by migration 207. It
replaces the old posture, which was that access is added rather than taken
away.

**Why it changed.** Migration 130 wrote `member` and `manager` as explicit
lists, before Projects or People existed. A list does not grow, so every app
added afterwards defaulted to invisible. Measured 2026-09-21:
`feature:projects` and `feature:people` were held by nobody but admin and
owner. The company project board could not be opened by anyone in the
company.

**`feature:*` and not a longer list**, so the default is self-maintaining:
the next app to go live is open on the day it ships.

⚠️ **The wildcard reaches no verb.** `permission_matches` covers exactly the
permissions beginning `feature:`. A member holding every app still cannot
read the HR half of a person record (`admin:members:read`). They still cannot
edit one (`admin:members:manage`). They still cannot open Organisation, which
is gated on `is_admin` and which no feature confers. Fence:
`tests/unit/test_every_app_by_default.py`.

📌 **`guest` is unchanged.** "All the people" means the organization's
people. An external collaborator keeps chat and explicitly shared apps.

📌 **Hiding an app from a ROLE is not yet possible** — the owner asked for it
as a later capability. Per-person Deny works today. Per-role does not,
because `member` and `manager` are system roles and `PATCH
/admin/roles/{slug}` refuses to edit one. That gap is H-141.

> ⚠️ **This table is incomplete and one cell of it is stale — measured 2026-08-04, see `colleague_onboarding.md` §3.0.**
> **(a)** These are `130_org_access_control.sql`'s grants only. `131_integration_memory_permissions.sql` adds more to four of the six roles — notably `member` also gets `integrations:use:*` **and `memory:read_org`** (`131:70-78`), while `guest` gets nothing (`131:80`). Quoting this table alone gets `member` wrong.
> **(b)** `manager`'s former *"Sees org-wide data"* intent is **not true as written**: `data:org:read` has **zero consumers** — nothing in the repository ever checks it (`permissions.py:132` declares it, `130:205, 221, 258` grant it, `access.py:148` lists it, and no route, query or predicate reads it). What actually widens a `manager` is `admin:members:read`, which is the auth floor for the **whole** `/admin` package (`routes/admin/_common.py:77-91`), plus `feature:approvals`/`observability`/`whatsapp` and `memory:write_org`. Recorded as **D14** in `work_plan.md` §3 (`agent-proposed, owner may overrule`). Do not write acceptance criteria against `data:org:read` until it has a consumer.
>
> **(c)** `manager`'s **Grants** cell used to read *"all `feature:*` except `build.*`"*. That was wrong in five slugs, not one: the seeded list (`130:216-221`) omits `feature:workflows`, `feature:integrations`, `feature:models`, `feature:agents` **and every `center.*`**, on top of both Build panes. The cell above is now the literal array, and `manager`'s **Intent** was reworded for the same reason as (b) — what it actually has is `admin:members:read`, not org-wide data.
>
> The role × app capability matrix — what each role can *actually see*, per app, with the `file:line` that settles each cell — lives in [`colleague_onboarding.md`](colleague_onboarding.md) §3, not here.

### 3.3 Permission vocabulary

Colon-separated, with `*` legal **only as the final segment**, where it matches any non-empty suffix.

```text
# Feature / module access — drives nav, route guard, and API gating
feature:chat            feature:email           feature:whatsapp
feature:tasks           feature:notes           feature:memory
feature:dashboard       feature:observability   feature:artifacts
feature:agents          feature:approvals       feature:integrations
feature:models          feature:build.agents    feature:build.apps
feature:workflows

# Agents
agents:run:*            # run any agent
agents:run:<name>       # run one named agent
agents:manage           # register / update / remove

# Custom Apps (org level; app_grants remains the per-app rung)
apps:use:*              apps:create             apps:publish

# Workflows (drafting + Test runs need only feature:workflows; this is the
# right to ARM one — publish / rollback / disable). See specs/workflows_app.md Q3.
workflows:publish

# Administration
admin:members:read      admin:members:invite    admin:members:manage
admin:roles:manage      admin:access:manage     admin:settings:manage
admin:audit:read

# Data + platform
data:org:read           integrations:manage
integrations:use:*      # which services an agent run may get credentials for
memory:read_org         memory:write_org
```

`feature:*` matches `feature:whatsapp`; `agents:run:*` matches `agents:run:agent-sales`; a bare `*` matches everything. `*` in a non-final position is rejected at write time — wildcards that match *inward* (`feature:*:read`) make the grant set impossible to reason about, and nothing needs them.

The two examples from the original ask land like this:

- *"no WhatsApp"* → deny `feature:whatsapp`.
- *"no app creator"* → deny `feature:build.apps`.
- *"but these agents"* → the role's `agents:run:*` is denied, and `agents:run:agent-email-assistant` is allowed per-user.

### 3.4 Overrides and the resolution rule

Per-user overrides sit in `user_permission_override(user_id, permission, effect)` where effect is `allow` or `deny`.

Resolution is **two layers**. Roles are the baseline; overrides are exceptions layered on top, and only overrides compete with one another:

```
has(user, required):
    if membership is not active                      → False

    # Layer 1 — overrides. Most specific wins; a tie goes to deny.
    A := most specific override pattern matching required with effect=allow
    D := most specific override pattern matching required with effect=deny
    if A or D:
        return False if D and (no A or specificity(D) >= specificity(A)) else True

    # Layer 2 — the role baseline.
    if any role-granted pattern matches required     → True
    otherwise                                        → False      # default deny
```

Specificity: an exact match outranks every wildcard; among wildcards the longer literal prefix wins; bare `*` is the floor.

**Why specificity rather than flat deny-wins.** The product's actual request — "only these two agents" — is expressed as a blanket deny plus named allows:

```
role:     agents:run:*                        the baseline: every agent
override: agents:run:*                 deny   take the blanket away
override: agents:run:email-assistant   allow  hand two back
```

Under AWS IAM's flat "deny always wins" (research doc §3.3), the specific allow could never surface, and an admin would have to clone a role per person — the failure mode roles exist to prevent.

**Why roles stay out of that comparison.** An override of `feature:*` = deny must switch *everything* off. If a role's exact `feature:chat` could out-specify it, "revoke this person's access" would silently leave holes. An admin's explicit exception always outranks the role; specificity only orders exceptions against each other.

A suspended or removed membership resolves to the empty set regardless of roles held.

Every override row records `reason`, `set_by`, `set_at`. A permission model nobody can explain six months later gets switched off, so the *why* is a column, not tribal knowledge.

---

## 4. Schema

`infra/postgres/130_org_access_control.sql`, idempotent per `infra/postgres/README.md`.

```sql
organization(id, slug UNIQUE, display_name, domain, settings JSONB, created_at, updated_at)

-- app_user gains, without dropping the legacy `role` column (back-compat, §7):
app_user + organization_id, status ∈ (invited|active|suspended|removed),
           invited_by, invited_at, joined_at, last_active_at

org_role(id, organization_id, slug, display_name, description,
         is_system BOOL, rank INT, created_at, updated_at,
         UNIQUE(organization_id, slug))

org_role_permission(role_id, permission, granted_at, PRIMARY KEY(role_id, permission))

user_role(user_id, role_id, assigned_by, assigned_at, PRIMARY KEY(user_id, role_id))

user_permission_override(user_id, permission, effect ∈ (allow|deny),
                         reason, set_by, set_at, PRIMARY KEY(user_id, permission))

feature_catalog(slug PK, label, description, nav_href, category, sort_order, is_default)
```

`feature_catalog` exists so the admin UI can render a checklist of real features without importing `nav.ts` into the gateway, and so a new pane is one seeded row rather than a code change in three places.

The migration seeds the default organization from `ALLOWED_EMAIL_DOMAIN`, seeds the five system roles with their permission sets, backfills every existing `app_user` into the org as `active`, and maps the legacy column: `role='executive'` → `admin`, `role='employee'` → `member`. Existing users keep working; nobody is locked out by deploying this. *[⚠️ Trap under WS-29: this seeding is keyed slug='default' — org #2 gets no roles and no owner from it; see saas_multitenancy_implementation.md §7.1 and trap 5. Do not copy the pattern.]*

**Ownership bootstrap:** if no member holds `owner` after backfill, the first address in `EXECUTIVE_EMAILS` (else the oldest `app_user`) is promoted. A deployment with no owner is one where nobody can grant themselves access back. *(per-org under D15: _HAS_OWNER_SQL carries no org filter — a lockout RLS does not fix; fixed under MT-1i)*

---

## 5. Enforcement

Five seams. The first three are security, the last two are UX — a hidden nav item is not a permission check.

| # | Seam | Where | Enforces |
|---|---|---|---|
| 1 | `require_permission(...)` | gateway route dependencies | API access — **the boundary of record** |
| 2 | Agent-run gate | `/agent/run/stream` + `agents:run:<name>` | which agents a member may invoke |
| 3 | Agent context assembly | orchestrator | an agent run cannot exceed its caller's set |
| 4 | Route guard | Next.js middleware / layout | direct URL navigation |
| 5 | Nav filter | `NAV_SECTIONS` filtered by effective access | what the sidebar shows |

Phase 1 ships 1, 2, 4, 5 and the plumbing for 3. Seam 3's full form (per-user credential and memory scoping) is research-doc §7–§8 and stays queued.

### Where seam 1 is applied

Gating happens **at the router** wherever a prefix has one audience, so a new endpoint added later is covered by default — the failure mode of per-route gating is the route someone forgets.

| Surface | Gate |
|---|---|
| `/whatsapp` `/email` `/tasks` `/notes` `/chat` | `feature:<slug>`, router-level |
| `/actions` | `feature:approvals`, router-level |
| `/integrations` | `feature:integrations`, router-level |
| `/memory` | already `require_internal_auth` — internal callers only, left as is |
| `/settings` **writes** | `feature:models` per route; reads stay open (they feed the chat model picker, which every member needs) |
| `/integrations/oauth` | `authorize` + `refresh` gated; `callback` is not (see below) |
| `/agent` | run endpoints per-agent via `assert_can_run_agent`; registry writes on `agents:manage`; `/webhook/{source}` deliberately unauthenticated for external callers |
| `/apps` | `require_app_author` (`feature:build.apps`) for authoring, `require_app_viewer` (`apps:use:*`) for runtime — one prefix, two audiences |
| `/observability` `/debug` | left on their existing `EXECUTIVE` gate. Switching them to `feature:observability` would *widen* access to `manager`; a PR that tightens access should not quietly loosen one |

### Exemptions are load-bearing, not oversights

`require_feature_router(slug, exempt=[...])` takes a list of **route templates** that must stay reachable without a member:

| Exempt | Why | Its own auth |
|---|---|---|
| `/whatsapp/webhook` | Meta calls it | verify-token (GET), signature (POST) |
| `/whatsapp/bridge/*` | the Go bridge posts inbound messages | `X-Bridge-Secret`, constant-time compare |
| `/email/oauth/{provider}/callback` | browser redirect from the provider | HMAC-signed `state` |
| `/email/webhook/microsoft` | Graph change notification | `validationToken` echo + `clientState` |
| `/notes/meetings/{id}/live/segment` | meeting-bot worker's ASR callback | `MEETING_BOT_TOKEN` |
| `/notes/stt/bot-live-token` | bot token minting | `MEETING_BOT_TOKEN` |

Each arrives with no session and no internal token. Gating one does not restrict access — it **stops ingestion**, invisibly, until someone notices missing data. Exempting removes the *feature* check only; the endpoint's own scheme still runs.

Two properties keep the list honest, both enforced by `tests/unit/test_org_access_enforcement.py`:

- Matching is on the **route template**, never the concrete URL, so a path parameter cannot be crafted to spell an exempt path.
- Any route in a gated router that takes no `UserContext` must be either exempt or an explicitly-listed UI read. A new machine entrypoint added without an exemption fails the test rather than breaking ingestion in production.

`require_role()` is **kept and reimplemented** on top of the permission engine — `UserRole.EXECUTIVE` resolves as `admin:*`-equivalent — so no existing route changes behaviour on deploy. New routes use `require_permission()`.

### Resolution and caching

The gateway resolves `(roles, permissions)` from Postgres on first use per email and caches for 60s (in-process, invalidated on any admin write). Permissions are **not** put in the NextAuth JWT: a JWT outlives an access change, and "I revoked WhatsApp an hour ago and they still have it" is exactly the failure that makes people distrust the whole system. The session carries identity; access is resolved server-side per request.

---

## 6. API + UI

**Gateway** (`apps/services/gateway/gateway/routes/admin/`):

```
GET    /auth/me                             effective access for the caller (no admin perm needed)
GET    /admin/members                       list + roles + status
POST   /admin/members                       invite
PATCH  /admin/members/{email}               status, display name
DELETE /admin/members/{email}               remove (soft)
PUT    /admin/members/{email}/roles         set role assignment
GET    /admin/members/{email}/access        resolved set + overrides + provenance
PUT    /admin/members/{email}/overrides     set allow/deny overrides
GET    /admin/roles                         list with permissions
POST   /admin/roles                         create custom role
PATCH  /admin/roles/{slug}                  edit (system roles: 403)
DELETE /admin/roles/{slug}                  delete custom role
GET    /admin/features                      feature catalog
```

**Control Plane** — `/settings/members` (roster, invite, status, role), `/settings/members/[email]` (per-user access editor: feature toggles, agent list, effective-permission preview showing *why* each is on or off), `/settings/roles` (role editor).

The per-user editor shows the resolved outcome and its provenance — "WhatsApp: **off** — denied for this user, overriding role `member`". Admins should never have to simulate the resolution algorithm in their heads.

---

## 7. Back-compat and rollout

The change is additive and reversible:

1. `app_user.role` is retained and dual-written. Rollback is redeploying the previous build.
2. `require_role()` semantics are preserved exactly.
3. `EXECUTIVE_EMAILS` continues to work as the bootstrap path but stops being the source of truth once roles are assigned.
4. If the access tables are missing or unreachable, the resolver resolves to **no access** and logs an error naming the migration to run.

**That fallback used to fail open** — degrading to the legacy `executive`/`employee` mapping — because before authentication was enforced, refusing everyone would have been an outage with no way back in. BO-2 residual #1 (§8c) removed that reason, so the default flipped: quietly granting an *approximation* of access is the access model silently not being the access model. `ACCESS_LEGACY_FALLBACK=1` re-enables the old behaviour as a recovery hatch for a failed migration.

Deploy order makes fail-closed safe: `apply_migrations.sh` runs before the gateway restarts, so the tables exist by the time the new code serves traffic.

---

## 8. Phases

| Phase | Content | State |
|---|---|---|
| **1** | Schema, permission engine, admin API, member/role/access UI, nav + route gating, agent-run gate | 🔄 this spec |
| **2** | Modules/teams (research §5) — team-scoped visibility; shared mailboxes; ~~`email_account_member`~~ | 🔲 — in progress as Centers Phases B/C (`specs/department_centers.md` §3 + `work_plan.md` WS-13/WS-14); `org_group` shipped (mig 138), admin UI + scoping remain. ⚠️ **`email_account_member` is vapour — struck 2026-08-03.** It has **zero** occurrences repo-wide across `*.sql` and `*.py`; it was never built and this row's phrasing made it read as existing Phase-2 content. Do not cite it as a table. The settled shape for shared mailboxes is a grant on the `email_accounts` **row** (`17_email_accounts.sql:16` is the `user_id` it widens) using the `email \| group:<slug> \| org` vocabulary — `tenancy_and_visibility.md` §5. See also `department_centers.md` C2: the assigned owner (`email_app_master_plan.md`, per `work_plan.md` D5) contains **zero** occurrences of "shared mailbox", so this work has no dispatchable home until that spec gains a section or §4 reassigns it. |
| **3** | Memory + credential scoping (research §7–§8) — the real seam-3 work | 🔄 authorization done (below); isolation deferred to BO-7 |
| **4** | Entity-graph visibility + RLS safety net (research §9, §16.5) | 🔲 |
| **5** | Consent records, access reviews, audit completeness (research §11.3) | 🔲 |

---

## 8a. Integration credentials and org memory (seam 3, authorization half)

### What is enforced

An agent's `config.json` declares which integrations it *wants*. Which of those the platform actually resolves is now decided by the **acting member**, via `integrations:use:<service>`:

```
role:     integrations:use:*                     baseline
override: integrations:use:zoho-crm      deny    Priya is not on the sales team
```

`build_integrations(..., is_authorized=...)` filters before resolution, and the executor supplies the predicate from the member's resolved access. Two properties matter:

- **An agent cannot widen its own access.** Declaring more integrations gets more entries in `unavailable`, not more credentials.
- **Filtering happens before the env injection.** At the streaming call site the credentials are injected into the run's environment (B6 Tier 0, restored afterwards by token); filtering first means an unauthorized credential never enters that environment at all, rather than merely being absent from `state["integrations"]`.

Unauthorized services are reported through the existing `unavailable` map rather than raising, so a member missing one integration still gets a working agent that can explain what it cannot do.

Org memory writes are gated the same way, on `memory:write_org`. Personal and agent-scoped memory stay ungated — they are already keyed to the acting user and the running agent, so there is nothing to authorize. Org memory is the shared one, which is why writing to it is the act that needs a permission. Reads stay open to `member`; a recall that silently returns nothing is worse than one that works.

### What is *not* enforced — say this out loud

This is **authorization, not isolation.** During a run, resolved credentials still live in the process environment. An agent that reads `os.environ` directly, rather than `state["integrations"]`, can still see whatever else is there. Per-run scoping (B6 Tier 0) narrows the window and the restore token cleans up after, but the boundary is cooperative, not enforced.

Real isolation needs the sandbox work — **BO-7** in `FOUNDATION_BUILDOUT_CHECKLIST.md`, and research doc §17.12. Until that lands, the honest claim is: *the platform will not hand a member credentials they are not authorized for*, not *a hostile agent cannot obtain them*. Those are different sentences and only the first one is true today.

### Background runs are deliberately unfiltered

The filter engages only when a run is attributable to an **active member**. Cron, the reconciler, webhook-triggered runs, and any address that does not resolve to a provisioned member all run unfiltered, exactly as before.

This asymmetry is on purpose. The feature exists to restrict a known member's reach. If it also starved background runs whose payload happened to carry an unfamiliar address, the failure would be silent — no 403, no error, just work that quietly stops happening. Who may start a run at all is already enforced upstream by `assert_can_run_agent`.

---

## 8b. Two credentials that were one (BO-2 residuals #3 and #4)

Everything above assumes a caller's identity means something. Two holes made that assumption false, and both were adjacent enough to this work that closing them belongs here.

### The LLM key was the identity token

`_get_internal_token()` fell back to `LITELLM_MASTER_KEY`, and the orchestrator handed agents whichever value it resolved to — preferring `gateway_internal_token` when set. So **every agent held a credential granting `SERVICE_ACCESS`**: it could call `/admin/members/{me}/overrides` and grant itself anything, defeating the whole model.

Now two secrets with different jobs:

| Secret | Role | Checked by | Handed to agents |
|---|---|---|---|
| `GATEWAY_INTERNAL_TOKEN` | service identity → `SERVICE_ACCESS` | `require_internal_auth`, `get_current_user`'s Bearer branch | **never** |
| `LITELLM_MASTER_KEY` | the `/v1` API key | `require_llm_api_auth` only | yes — that is its purpose |

`/v1` accepts either, because the Next.js server and internal jobs route completions too. Everything else accepts identity only. Every `/v1` client now reads `settings.llm_api_key`; a test asserts none of them resolves `gateway_internal_token`, so the escalation cannot be reintroduced by a future edit.

When `GATEWAY_INTERNAL_TOKEN` is unset the identity token still falls back to the LLM key, so an un-migrated deployment does not 401 every internal call on upgrade — but it warns on every resolution, because a silent fallback here is the vulnerability wearing a fix's clothes.

**What this does not fix.** Agents run in-process (BO-7), so an agent that reads `get_settings()` directly can still obtain `gateway_internal_token`. What is closed is the *shared-secret* exposure: the value most likely to leak outside the process — an API key, by design present in BYOK configs, provider settings and logs — is no longer an identity credential. The in-process path needs the sandbox.

### The agent webhook was unauthenticated

`POST /agent/webhook/{source}` dispatches an agent run and had no authentication and no `assert_can_run_agent` — a fourth run path, internet-reachable through Caddy, that bypassed every gate in §5.

It now requires an HMAC-SHA256 signature over the raw body in `X-CC-Signature`, with per-source secrets (`AGENT_WEBHOOK_SECRET_<SOURCE>`) overriding a global one. A valid signature *is* the authorization: it proves the platform's own secret produced the request, which is the same trust level as the internal token. There is no member to resolve, which is precisely why the signature is mandatory.

**It fails closed when unconfigured** — 503, naming the variable to set — unlike the bridge and bot-token checks elsewhere, which allow when unset. Those receive data; this one starts an agent run. Safe to make strict because nothing calls this path today: the provider receivers live in `ingestion/sources/*/webhook.py` and dispatch no agents (`FOUNDATION_AUDIT_REPORT.md`), so there is no working sender to break.

---

## 8c. Default-deny authentication (BO-2 residual #1)

`get_current_user` never rejects — it *labels*. Every route was therefore open unless it remembered to add a guard, and the failure mode of opt-in security is the route nobody opted in. Two were found exactly that way:

- `GET /agent/workspace/{session_id}/history` — anonymous read of an agent's full file version history.
- `POST /agent/workspace/{session_id}/promote` — anonymous **write** to the workspace.

Both took no caller identity at all. Same IDOR family as the memory API that BO-2's first pass closed.

`require_authenticated(public=...)` is now attached once at `FastAPI(dependencies=[...])`, so **a route added tomorrow is covered without anyone remembering anything**. It asks only "who are you" — `require_permission` and the feature gates still answer "may you", and a member with an empty permission set passes authentication and then fails authorization, which is the correct sequence.

`gateway.main.PUBLIC_ROUTES` is the complete list of anonymous-reachable templates: `/health`, the three provider webhook receivers, the signed agent webhook, the two OAuth callbacks, the Graph notification, the Meta webhook, the four WhatsApp bridge endpoints, and the two meeting-bot callbacks. Every one authenticates itself by another means or is a liveness probe.

Two things keep the list honest, both tested: a `PUBLIC_ROUTES` entry matching no live route fails (a typo is dead text; a stale entry is a hole held open), and a coverage test asserts **every** route in the assembled app carries the guard — if a router is ever included in a way that bypasses the app-level dependency, that test fails rather than the endpoint quietly opening.

**Swagger/ReDoc are the one exception**, and structurally so: FastAPI mounts them as plain Starlette routes with no dependency chain, so they cannot carry the guard. They publish the entire API surface, so they are dev-only — outside `ACB_ENV=dev` the endpoints do not exist.

---

## 8d. The CONTENT apps have no write authority — documented 2026-09-19, deferred by the owner

🟡 **NOT BEING BUILT. The owner deferred it on 2026-09-19 and asked for a
record.** This section is that record. Nothing here is dispatchable until the
owner says so. Board row **WS-40**. Action entry **H-121**.

### 8d.1 The measurement

§1 says this spec builds "**one enforcement path the whole platform shares**".
It is built, `require_permission` is real, and **one content app already uses
it**. Here is every place the gateway uses it, counted on 2026-09-19:

| Permission | Where |
|---|---|
| `admin:members:invite` · `admin:members:manage` · `admin:roles:manage` · `admin:access:manage` · `admin:settings:manage` | the admin routes |
| `agents:manage` | `routes/agent.py` |
| `feature:integrations` · `feature:models` | `routes/oauth.py` · `routes/settings.py` |

**Almost every one is an ADMIN or a SETTINGS act — with one exception that
matters more than the rule.**

🟢 **`workflows/publish.py` gates three writes with `workflows:publish`.**
Publish, rollback and disable each carry
`dependencies=[require_permission(PUBLISH_PERMISSION)]`. That is a real
content-app write permission, in exactly the `<app>:<verb>` shape §8d.4 asks
about. **So the pattern is not hypothetical. It is built, and one app uses it.**

⚠️ Two near-misses, named so that a later reader does not count them as
precedent:

- `routes/crm/import_zoho.py`, `sync_zoho.py` and `stage_metadata.py` use
  `admin:access:manage`. Those are administrative acts that live inside a
  content package, not content writes.
- `routes/tasks/core.py` uses `admin:members:manage` under the local name
  `PEOPLE_WRITE_PERMISSION`. It also guards the **retired** `gtd_*` store, not
  the Tasks lens. An admin permission reused for a people write is not a
  content-write vocabulary either.

**What IS true of Projects:**
`grep -rn "require_permission" apps/services/gateway/gateway/routes/projects/`
returns nothing. Not one of its routes gates a write, and the same holds for
`email/` and `notes/`.

### 8d.2 What authorises a content write today

Visibility, and nothing else. A route resolves the caller, loads the row the
caller can SEE, and then writes it. So in the Projects app, anybody who can
open a project can also rename it, move it, archive it, change its run state,
and edit every task in it.

⚠️ **This is not a hole in one endpoint. It is the model.** A guard added to
one route would mint a second authority vocabulary beside the one this spec
already owns, which CLAUDE.md §5 forbids.

### 8d.3 Why it surfaced now

Until 2026-09-19 every content write a member could reach was reversible. A
rename can be renamed back. An archive has an unarchive. So visibility-only
authority cost nothing that anybody noticed.

**H-8 shipped Delete for a project (PR #298), and Delete is a cascade over the
subtree, every task and every grant.** The endpoint was always there. The act
was not reachable from the product. Now it is, so a member holding a
`group:<slug>` READ grant can destroy a space.

The delete dialog asks for the project's name and reads the real counts first.
That is a guard against a MISTAKE. It is not authority, and it must never be
described as authority.

### 8d.4 The four questions to answer before anybody builds this

1. **What is the permission vocabulary for a content app?** ✅ **Half answered
   already — follow `workflows:publish`.** The shape is `<app>:<verb>`, and
   copying it is what CLAUDE.md §5 requires. What is still open is the GRAIN:
   a per-verb permission across five apps is twenty new strings, and a role
   nobody can reason about is not a control. Workflows needed exactly one verb,
   which is why it got away without answering this.
2. **Does authority sit on the ORG or on the NODE?** §3's roles are org-wide.
   D12's grants are per-node. "Alice may delete in Marketing and nowhere else"
   needs the second, and `pm_project_grants` already carries a `subject`.
3. **Is delete a role, or is it ownership?** `pm_projects.created_by` exists.
   "The person who made it may remove it" needs no new vocabulary at all, and
   it answers the mistake case without answering the authority case.
4. **What happens to every existing member on the day it turns on?** A
   default-deny flip makes every content app read-only for anybody whose role
   does not carry the new permission. §8c took the same decision for
   authentication, and §7 is the rollout pattern to copy.

### 8d.5 What must be true of the answer

- **One vocabulary.** It extends `permissions.py`, and it does not start a
  second resolver. §5 is the enforcement path.
- **The two axes stay separate.** D12 says visibility is *who can see*.
  Authority is *who may act*. This adds the second axis. It does not redefine
  the first, and it does not merge them.
- **Ship dark.** Default OFF behind a flag, per CLAUDE.md §4.
- **R7.** Whatever rule lands names the test that makes breaking it fail.

## 9. Open questions

1. **Agent visibility vs. runnability.** Phase 1 gates *running* an agent. Should a member also be unable to *see* that an agent exists? Listing is currently a weaker signal than running, but agent names leak org structure.
2. **Approval routing.** 🔴 **OWNER-DECISION — still open, and it gates work.** When a member lacking `admin:*` triggers an action needing approval, who is asked? Phase 1 routes to anyone with `feature:approvals`; per-module approvers is a Phase 2 question.

   > **Annotated 2026-08-03 (WS-14).** `department_centers.md` Phase C4 ("per-Center approvals routing") depends on this question and cannot be dispatched until it is answered — that bullet is labelled **OWNER-DECISION** for this reason. Two facts an answerer should have:
   >
   > - **It is a policy call, not a UI call.** "Who may approve an outward write or a spend on another Center's behalf" is exactly the kind of thing an agent must not decide.
   > - **There is no column you can route on today.** `infra/postgres/66_pending_actions.sql:13-38` is the complete row — `id`, `actor`, `action`, `target`, `payload`, `authority`, `destructive`, `disposition`, `status`, `result`, `reviewed_by`, `reviewed_at`, `created_at`. **No requesting-member column, no group column, no Center column.** So the follow-on ticket is "answer this, then add a column" — at the next free migration number resolved at build time (R1) — not "add a filter".
   >
   >   ⚠️ **Corrected 2026-08-03.** This bullet used to add *"`actor` is the proposing agent … not the human behind it, and no group is derivable from any existing column."* **That absolute is false.** `pending_actions.actor` has six writers, and two of them carry the requesting human: `routes/apps/tools.py:393` and `routes/apps/actions.py:345` both write `actor=f"app:{slug}:{email}"` with `email = _uid(user)`, from which a group **is** derivable (parse the email, join `org_group_member`). The `"agent:sales"` in the column comment is an example no shipped call site produces. The five real shapes are `app:<slug>:<email>` · `app:<slug>` (`apps/publish.py:211`) · `workflow:<name-or-id>` (`workflows/service.py:668`, threaded into `workflows/tools.py:190`) · `tasks:<provider>` (`tasks/providers.py:127-130`) · `tasks:clickup:ws:<id>` (`providers.py:314-317`); `system:action_broker` is an *audit-event* actor (`action_broker/broker.py:151/161/172/225/233`), never a `pending_actions` row. **The conclusion is unchanged and better supported:** `actor` is free text with five shapes, no grammar, and a human in only two of six proposers — routing on it would silently show an empty Center inbox for every workflow-, publish- and provider-originated proposal. The new column must be *written by every proposer*, not parsed by the reader. Full evidence: `department_centers.md` C4.
3. **Departure.** A removed member's private apps, chat sessions, and agent workspaces currently persist unowned. Transfer-on-removal is unbuilt (research §13 Q4).
4. **Guest scope.** Does `guest` ever need email or tasks, or is chat-plus-shared-apps the whole product surface for externals?
5. **Custom role ceiling.** Clerk caps at 10. Unbounded custom roles tend to produce one role per person, which is what overrides are for.

---

## 10. Handoff: multiplayer agent collaboration

**Multi-tenant user management is complete as Phase 1 and is not being extended on its own track.** *(true for the intra-org model; the tenancy axis reopened 2026-08-08 as WS-29 MT-1a/H6)* Everything remaining — modules/teams, session sharing, memory and transcript scoping — is now owned by the multiplayer agent collaboration workstream, because those are the same primitives seen from two directions. Building them twice would produce two group models to reconcile later.

This section is the integration contract. Read it before designing multiplayer; it says what you can rely on, what will collide, and what is deliberately absent.

### 10.1 What is built and can be relied on

| Capability | Where | Notes for multiplayer |
|---|---|---|
| Permission model + resolution rule | `packages/acb_auth/acb_auth/permissions.py` | Pure, no I/O, fully unit-tested. Two layers: role grants, then per-user allow/deny overrides that compete **by specificity** (§3.4). |
| DB-backed resolution | `acb_auth/access.py` → `resolve_access(email)` | 60s cache, invalidated on every admin write. Never raises; unknown/suspended → no access. |
| Request identity | `acb_auth/deps.py` → `get_current_user` | Returns `UserContext` carrying `email`, `user_id`, `organization_id` and a resolved `EffectiveAccess`. |
| Authentication | `require_authenticated(public=...)`, app-wide (§8c) | Default-deny. A new route is covered without opting in. |
| Feature gating | `require_feature_router(slug, exempt=[...])` | Router-level, with a declared exemption list for self-authenticating machine entrypoints. |
| Agent-run gating | `assert_can_run_agent(user, name)` | On all three run endpoints; `/agent/webhook/{source}` is HMAC-signed instead. |
| Credential scoping | `build_integrations(..., is_authorized=)` + `executor._integration_authorizer` | Filters per acting member **before** creds enter the run env. |
| Org-memory gate | `acb_skills/memory_tools._may("memory:write_org")` | Run-scoped predicate contextvar, set by the executor. |
| Admin API + UI | gateway `routes/admin/`, workbench `/settings/members`, `/settings/roles` | Members, roles, per-user overrides, with provenance on every decision. |
| Schema | `infra/postgres/130_org_access_control.sql` | `organization`, membership on `app_user`, `org_role`, `org_role_permission`, `user_role`, `user_permission_override`, `feature_catalog`. |

### 10.2 The four collisions

**1. Groups — build once, not twice.** `module` / team is the deferred Phase 2 (research §5) and is *unbuilt*. Multiplayer needs "who is in this space"; access control needs "who is in this team". Same primitive. Whoever builds it should satisfy both requirement sets in one table. The visibility vocabulary to reuse is already established by `apps.visibility ∈ (private, people, org)` and generalised in §5 — do not invent a third.

**2. Session ownership is single-owner today.**

```sql
chat_session.user_id TEXT NOT NULL DEFAULT 'default'   -- comment still says "future multi-tenant"
```

No participants table, no sharing, no visibility column. Multiplayer needs `chat_session_participant`; access control needs session visibility. One change, not two.

> **Update 2026-08-01 (doc-truth pass):** shipped as migration 138 (`138_groups_and_session_participants.sql`) — `chat_session.visibility` + `chat_session_participant`, exactly the one change asked for. See `groups_sessions_authority.md` §2/§6.

**3. Whose authority runs the agent — decide before transcripts exist.**

Phase 1 resolved this implicitly for one user: `assert_can_run_agent` checks the caller, and `_integration_authorizer` keys off `event_payload["user_email"]`. With two people in a session the default answer becomes "whoever typed", and that leaks — a member denied Zoho sits in a session where another member triggers a Zoho-using agent, and the output lands in the shared transcript.

**`EffectiveAccess.intersect()` already exists and is tested** (`permissions.py`). It was written so a sub-agent cannot exceed its invoking member, but the semantics are exactly "a shared session grants the intersection of its participants' access". If intersection is the rule, the primitive is there. If the rule is actor-only, that needs to be a stated decision with attribution visible in the UI — not a default nobody chose.

> **Update 2026-08-01 (doc-truth pass):** decided and shipped — intersection, enforced at run start per `groups_sessions_authority.md` §3 (participant resolution + `intersect()` fold, feeding `_integration_authorizer` and `assert_can_run_agent`).

**4. The transcript is a second exposure boundary.** Phase 1 gates *reaching* a feature. A shared transcript re-exposes whatever any participant's agent produced to everyone in the room. Two concrete cases:

- **Personal memory.** Keyed to the acting user via `_memory_user_id`. Injecting it into a shared session surfaces one person's private context in another's view.
- **Tool output.** A member without `integrations:use:zoho-crm` reads Zoho records in the shared transcript because a permitted member asked for them.

Nothing in Phase 1 addresses this; every enforcement seam assumes one viewer per run. It is new surface that multiplayer introduces, and it is the item most likely to be discovered late.

> **Update 2026-08-01 (doc-truth pass):** addressed — clearance tags on tool output + replay redaction shipped in migration 139 (`139_room_authorship_and_agents.sql`; `groups_sessions_authority.md` §4), and personal memory is not injected in shared rooms. Still open: the `memory-clearance.md` 3b/3c items (subject binding, extraction classification, the memory-disclosure share sheet).

### 10.3 Deliberately not built

Not oversights — scoped out with reasons in the sections referenced.

- Modules/teams (§8 phases, research §5).
- Entity-graph row visibility + Postgres RLS (research §9, §16.5).
- Transfer-on-removal: a removed member's private apps, sessions and workspaces persist unowned (§9 Q3).
- Personal (BYO) integration credentials (research §8.3).
- Everything SaaS — subdomains, billing, per-tenant isolation (research §17). *(in scope since 2026-08-08 — WS-29, saas_multitenancy.md §0.9.3)*
- **True agent isolation.** Agents run in-process, so §8b's credential scoping is *authorization, not isolation*. That ceiling is **BO-7**, and it bounds what any access claim in this document can mean.
- ~50 Next.js proxy routes still carry their own `EXECUTIVE_EMAILS` copy. Cosmetic — permissions resolve server-side from the email regardless — but `workbench/control_plane/src/lib/gateway.ts` is the single replacement when someone sweeps them.

### 10.4 Suggested first move

One spec covering the group primitive, `chat_session_participant`, and the authority rule — then implementation can split cleanly. Sequencing matters: the authority and transcript-exposure decisions are cheap now and expensive once shared transcripts exist and have to be migrated.

### 10.5 Where the reasoning lives

Design rationale sits in the code, not only here. If a decision looks arbitrary, the docstring explains it:

- `permissions.py` module docstring — why specificity rather than flat deny-wins.
- `access.py::legacy_fallback_enabled` — why the resolver flipped to fail-closed.
- `deps.py::require_feature_router` — why exemptions match route *templates*.
- `deps.py::_get_internal_token` — why the identity token and the LLM key are separate.
- `executor.py::_integration_authorizer` — why background runs are deliberately unfiltered.
