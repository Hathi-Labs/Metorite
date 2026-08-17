# Multi-tenancy — the leak paths a column-plus-predicate retrofit does not close

> **Minted 2026-08-08**, adversarial read of the tree at `ccb762a8`, alongside WS-29a/b.
> Companion to `multi_tenancy.md`. Everything here is read off code and off a live Postgres 16
> with the migration set applied; every claim carries a `file:line`.
>
> **Scope.** `multi_tenancy.md` costs the *database* half of the retrofit: a column on 17
> tables and a predicate in one query. This document is the other half — the places where a
> request, a job or a process reaches another tenant's data **without going through
> `_VISIBLE_PROJECTS_SQL` at all**. Two such places were already known and are assigned
> elsewhere (`pm_project_grants.subject='org'`; `data:org:read` → `unrestricted`); neither is
> restated below except where a third path makes one of them sharper.

---

## 0. The one-paragraph version

The Projects retrofit is contained, WS-29a/b does it correctly (§3, S2-8), and it is contained
for a reason that does not generalise: **Projects has one visibility seam and nothing else in
Metorite does.** So the remaining leak surface is precisely the set of paths that never
build a `Visibility` at all — and they are the ones that matter most. The admin plane
resolves its organization from a hard-coded slug; LLM and integration credentials are one row
per provider for the whole deployment; the event bus fans every tenant's events into every
tenant's workflows; a workflow can then patch any task by raw UUID with the visibility check
*deliberately* removed; and the identity an agent's tools act under is a process-global
environment variable. Ranked below by blast radius. The measured `organization_id` count is
also wrong — §5.

---

## 1. Findings, ranked by blast radius

### S1-1 — The entire admin plane resolves its tenant from a hard-coded slug

`apps/services/gateway/gateway/routes/admin/_common.py:102-118`

```
async def get_org_id(db) -> str:
    """Resolve the deployment's organization id, or 503 if unprovisioned."""
    ... text("SELECT id::text AS id FROM organization WHERE slug = :slug"),
        {"slug": DEFAULT_ORG_SLUG},          # _common.py:62 → "default"
```

The caller is never consulted. There are **27 call sites**, covering every write in the org
model:

| surface | file:line |
|---|---|
| member list / invite / suspend / remove / roles | `admin/members.py:113,169,209,281,588,652,801,887` |
| group create / rename / delete / add / remove member | `admin/groups.py:180,207,258,296,371,436` |
| role CRUD + permission grants | `admin/roles.py:112,157,234,300` |
| access-request queue | `admin/access_requests.py:419` |
| `GET /auth/me` | `admin/me.py:111` |

**What leaks.** The moment a second `organization` row exists, a tenant-B admin holding
`admin:members:*` lists **tenant `default`'s** roster, invites people **into** `default`,
creates groups **in** `default`, and grants roles **in** `default`. `GET /auth/me`
(`me.py:111-127`) reports the `default` organization's `slug`/`display_name` to every signed-in
member of every tenant, so the frontend's idea of "which org am I in" is wrong for all but one.

This is worse than a read leak: it is an unbounded **write** into another tenant's access
control, performed by a caller the permission system correctly authorised — for their *own*
org, which the query then discards.

**What closes it.** `get_org_id(db)` must become `org_of(user)` — a lookup keyed on
`UserContext.organization_id`, which `_with_resolved_access` already populates
(`packages/acb_auth/acb_auth/deps.py:272-275` via `resolve_identity`, `access.py:358-380`). The
27 call sites then inherit it. `DEFAULT_ORG_SLUG` should survive only as the *provisioning*
seed, never as a resolution. Until then this surface is single-tenant by construction and no
`pm_*` column changes that.

---

### S1-2 — One set of LLM and integration credentials for the whole deployment

`infra/postgres/08_provider_keys.sql:6-13` · `infra/postgres/11_integration_credentials.sql:17-27`
· `packages/acb_llm/acb_llm/key_store.py:57,120-137,431-438`

```sql
CREATE TABLE provider_keys (
    provider    TEXT PRIMARY KEY,   -- "openai" | "zoho-crm:refresh_token" | "clickup:…"
    encrypted   TEXT NOT NULL, ...
```

`provider` is the **primary key** — globally, for the deployment. Migration 11 extended the
same table to hold *integration* credentials (`credential_type='integration'`), so Zoho,
ClickUp, Gmail and Apollo tokens share the namespace. Reads go through a module-level singleton
(`key_store.py:431-438`) whose in-memory cache is keyed by provider alone
(`key_store.py:57`, hit at `:120-122`) — no tenant dimension exists to key on.

Writes are worse than shared, they are **process-global**:

* `routes/settings.py:203` — `os.environ[env_var] = value` mutates the running process.
* `routes/settings.py:207-227` — `_sync_key_to_store` overwrites the single `provider_keys` row.
* `routes/settings.py:797-802` (`_write_env_key`) writes the on-disk `.env`.

The gate is `require_permission("feature:models")` (`settings.py:592,719,756,794,947,997,1026,1047`),
which is a **per-user permission with a deployment-global effect**. `model_config`
(migration 35, `key TEXT PRIMARY KEY`) has the same shape for enabled/hidden models and tier
overrides.

**What leaks.** Every tenant's completions bill the same provider key, so cost attribution is
impossible and one tenant can exhaust another's quota. A tenant-B admin can *replace* the
OpenAI key (silent MITM of every tenant's prompts) or replace the Zoho refresh token (pointing
tenant A's CRM sync at tenant B's Zoho, or vice versa). `GET /settings/llm/*` surfaces enough
to confirm which providers are configured across the deployment.

**What closes it.** `PRIMARY KEY (organization_id, provider)`, an `organization_id` on
`model_config`, and — the part that is not a migration — deleting the `os.environ` /`.env`
write-through, which cannot be tenant-scoped in a shared process. The `_cache` dict must key on
`(org, provider)`. Whether *some* keys stay deployment-global (a platform-supplied model key,
with the tenant billed by usage) is a product decision that should be **made explicitly**, per
provider, rather than inherited from a schema written for one company.

**LiteLLM.** The `LiteLLM_*` tables carry their own `organization_id`, and the two models are
**unrelated namespaces that happen to share a word**. There is no LiteLLM proxy in this
deployment — `settings.py:196-198` says so ("Since there's no separate LiteLLM proxy, keys are
set in the current process environment AND the encrypted Postgres key store"), the `LiteLLM_*`
tables appear only in `infra/postgres/schema.generated.sql` and are **absent from the live
database** (123 tables, none `LiteLLM_*`). Nothing connects the two org models and nothing
should; the ratchet is right to exclude the prefix (`tests/unit/test_tenancy_boundary.py:38`).

---

### S1-3 — The event bus is global, and the workflow that receives an event may write any task

Three files compose into one self-serve cross-tenant read **and write** chain.

**(a) Dispatch matches on `source` + `event_type` only.**
`apps/services/gateway/gateway/routes/workflows/triggers.py:52-64`

```sql
SELECT t.config, w.id AS workflow_id, ...
  FROM workflow_triggers t JOIN workflows w ON w.id = t.workflow_id
 WHERE t.kind = 'event' AND t.enabled
   AND w.status = 'published' AND w.latest_version IS NOT NULL
```

No tenant, no owner, no filter beyond "published". `event_trigger_matches`
(`triggers.py:32-37`) compares `config["source"]` and `config["event_type"]` and nothing else.
`workflow_triggers` and `workflows` carry no tenant key
(`tests/unit/test_tenancy_boundary.py`, `workflow_*` block).

**(b) Projects emits onto that same bus.** `routes/projects/core.py:1022-1044` (`emit` →
`ingestion.event_hooks.emit_event("projects", …)`), registered as a sink at
`gateway/main.py:1144-1147`. Sixteen emit sites, e.g. `projects/tasks.py:294,384,450,496,587`.
Payloads carry ids, not titles — that limits the *direct* exfiltration and is worth crediting.

**(c) The receiving workflow's `pm_task` node has no visibility check, by design.**
`routes/projects/automation.py:26-33`:

> *"**Who this acts as.** `system:workflow:<workflow_id>` … and **not** member-scoped: there
> is deliberately no visibility check here. A published workflow is an org-level artifact."*

`apply_task_patch` (`automation.py:110-180`) resolves the row with
`require_row(db, "pm_tasks", task_id, "Task")` at `automation.py:138` — a bare primary-key
lookup. `resolve_status` (`automation.py:90-96`) likewise reads `pm_task_statuses` by
`project_id` with no closure. The node is reached through `_pm_task_updater`
(`workflows/service.py:152-181`) and `_execute_pm_task` (`workflows/engine/handlers.py:234-262`).

**The chain.** Tenant B publishes a workflow with an event trigger `{"source": "projects"}` and
a `pm_task` node whose `task_id` is `{{trigger.task_id}}`. Tenant A edits any task → `emit` →
`dispatch_event` → tenant B's workflow starts, run row `started_by="event:projects"`
(`triggers.py:88`) → the node patches **tenant A's task**: title, description, importance,
due date, estimate, status (`PATCHABLE_FIELDS`, `automation.py:55-57`). The run's step output
returns `{changed, status, skipped}` to tenant B, and the trigger payload it captured
(`triggers.py:81-85`) is readable in the run detail.

That docstring is correct today and becomes the most dangerous sentence in the app the day a
second tenant onboards — the same shape as the `subject='org'` literal, one layer up.

**What closes it.** Three things, none of which is the `pm_*` column:
1. `dispatch_event` must filter triggers to the emitting tenant, which means the **event needs
   a tenant** — `emit` should carry `organization_id`, and `emit_event`'s sink signature
   (`event_hooks.py:26`) should carry it too.
2. `apply_task_patch` must take a tenant (not a member) and scope `require_row` to it. Keeping
   "not member-scoped" is right; "not tenant-scoped" is not.
3. The scheduler (`workflows/scheduler.py:106-120`) scans every enabled `schedule` trigger the
   same way, under `started_by="schedule"` — same fix, same reason.

---

### S1-4 — Agent tool identity is a process-global environment variable

`apps/services/orchestrator/orchestrator/executor.py:1711-1721` and `:2185-2195`

```python
if _mu:
    _set_memory_user_id(_mu)
    os.environ["ACB_AGENT_USER_EMAIL"] = _mu     # never cleared
```

The ContextVar is correct. The `os.environ` write is a single slot in a shared async process,
and it is what the agents actually fall back to:

* `apps/agents/agent-email-assistant/agents.py:62-75`
* `apps/agents/agent-crm/agents.py:76-87`
* `apps/agents/agent-whatsapp-assistant/agents.py:54-65`
* `apps/skills/skill-task-gtd/skill_task_gtd/core.py:84`

Each `_current_user_email()` tries the ContextVar and falls back to the env var, with the
docstring explaining exactly why the fallback is load-bearing ("the Copilot SDK runs tool
callbacks in a context that can drop ContextVars"). Under D-MT-1 that email **is** the tenant.

Two ways it goes wrong, and one is not hypothetical:

* **Concurrency.** Two runs in flight from two tenants; the second's assignment wins for
  whichever tool callback loses the ContextVar. The agent then reads the other tenant's mailbox
  or CRM through the gateway with that email in `X-User-Email`.
* **Callers that set nothing.** `projects/agent_dispatch.py:144` calls
  `run_agent(agent, message)` with a **string** payload, so the `isinstance(event_payload, dict)`
  guard at `executor.py:1716` skips the assignment entirely and the variable keeps whatever the
  previous run left. A WS-27f agent dispatch therefore acts as the last person to run an agent.

**What closes it.** Delete the env-var fallback and fix the ContextVar propagation, or pass the
acting identity explicitly into the tool surface. No schema change helps.

---

### S2-5 — `org` means "everybody in the deployment" in rooms and in session authority

Distinct from the assigned `pm_project_grants` finding: these are different modules with their
own copies of the same literal, and no one is working on them.

* `apps/services/gateway/gateway/rooms.py:368-402` — `SESSION_VISIBLE_SQL`. A room is visible
  when a `chat_session_participant` row says `'org'`, or when `s.visibility = 'org'`, and the
  only accompanying test is `EXISTS (SELECT 1 FROM app_user u WHERE u.email = :uid AND status
  = 'active')` (`:387-401`). Any active member of **any** organization passes. `chat_session`,
  `chat_message` and `chat_session_participant` carry no tenant key.
* `rooms.py:376-383` — the group branch joins `org_group g ON g.slug = substring(p.subject from 7)`
  with no organization filter.
* `packages/acb_auth/acb_auth/access.py:400-402` — `_ORG_MEMBER_SQL` is literally
  `SELECT email FROM app_user WHERE status = 'active'`, used at `:463` to expand an `org`
  participant subject.
* `packages/acb_auth/acb_auth/access.py:392-398` — `_GROUP_MEMBER_SQL` matches `g.slug = :slug`
  with no organization filter, used at `:466-470`.

**The group-slug detail matters.** `org_group` is `UNIQUE (organization_id, slug)` — verified on
the live DB — so `engineering` is a *legal* slug in every tenant simultaneously. Every consumer
that matches on the bare slug therefore spans tenants the moment two orgs pick the same
obvious name. That includes the Projects grant vocabulary itself: `_MY_GROUPS_SQL`
(`routes/projects/core.py:406-414`) emits bare `'group:' || g.slug`, matched against
`pm_project_grants.subject` at `core.py:445`. The WS-29b tenant predicate on the grant closure
closes the Projects instance; it closes none of the others.

**What closes it.** The `org`/`group:` expansion needs an organization argument in all four
places. Long term the subject vocabulary should carry the org (or the expansion should join
through `app_user.organization_id`), because "a bare slug identifies a group" stops being true
under multi-tenancy exactly as "a bare email identifies a person" would under D-MT-1(b).

---

### S2-6 — An org-visible Custom App is visible to every tenant, and carries its data with it

`apps/services/gateway/gateway/routes/apps/_common.py:270-293`

```python
org_live = (_field(app_row, "visibility") == "org"
            and _field(app_row, "status") == "live")
...
if org_live:
    return True          # any UserContext with an email
```

No organization check, and not even a `status='active'` check. `apps.visibility` is
`'private' | 'people' | 'org'` (`infra/postgres/114_custom_apps.sql:30-31`), and
`app_grants.subject` accepts `'org'` too (`114_custom_apps.sql:59-61`).

`can_view` gates `require_app_viewer`, which gates the storage bridge:
`routes/apps/runtime.py:142-166` (`GET /{slug}/data/{table}`) and `:250-290` (`PUT`/`DELETE`)
read and write `app_data` rows in the **shared** partition (`user_scope = ''`,
`114_custom_apps.sql:73`). So a cross-tenant viewer does not just see the app, it reads and
writes the app's shared data store.

Two smaller edges in the same table: `apps.slug` is `TEXT UNIQUE` globally
(`114_custom_apps.sql:25`), so tenant B can squat a slug tenant A wants and every app URL is a
global namespace; and workspace paths (`apps.workspace_path`) are a flat per-app directory with
no tenant segment (`routes/apps/files.py:87-96`).

---

### S2-7 — The Action Broker queue is global, and approving executes

`apps/services/gateway/gateway/routes/actions.py:47-55` → `action_broker/broker.py:246-263`

```sql
SELECT id, actor, action, target, payload, authority, destructive,
       disposition, status, created_at
FROM pending_actions WHERE status = 'pending' ORDER BY created_at DESC
```

`list_pending()` takes no argument and filters on nothing but status. `pending_actions`
(migration 66) has no tenant key. The route's own docstring notes the payloads carry
"outward-write bodies — CRM/email content".

`approve(action_id, reviewer)` (`broker.py:340-358`) loads the row by id and runs the
registered handler; there is no check that the approver has any relationship to the proposal.
Handlers are a **flat, process-wide registry** (`broker.py`'s `register_action_handler`, wired at
`main.py:1140-1142` and five other sites), and each acts on the payload's own identifiers —
e.g. `workflow.resume_run` resumes `payload["run_id"]` verbatim
(`routes/workflows/broker_handlers.py:18-33`).

**What leaks.** Anyone holding `feature:approvals` in any tenant reads every tenant's queued
outward writes (CRM record bodies, WhatsApp broadcasts, ClickUp comments) and can execute or
refuse them. Refusing is a denial-of-service on another tenant's automation; approving is a
write into another tenant's *external* system, which is the one place the platform cannot roll
back.

**What closes it.** `organization_id` on `pending_actions`, set at `propose`/`enqueue` time
from the proposing principal, and a tenant argument on `list_pending`, `approve` and `reject`.

---

### S2-8 — `pm_task_assignees` was a second door into a task — **CLOSED IN FLIGHT, verified**

Recorded because it is the finding most likely to be *thought* covered by a closure-only
predicate, and because the next reader should know it was checked rather than assumed.

`pm_task_assignees.assignee` is a bare email (D-PM-4), and `task_visibility_clause` /
`load_visible_task` grant access through it **without passing through
`_VISIBLE_PROJECTS_SQL`** — a deliberate escape hatch for cross-Center delegation. A tenant
predicate placed only inside the closure would not have reached it. Assignee writes are
unvalidated free text (`routes/projects/tasks.py:530-556` lowercases and inserts, with no check
that the address is a member of anything), so tenant A assigning `victim@tenant-b.example`
would have handed that person the task's title, description and full `pm_activities` timeline —
via `load_visible_task`, via `GET /projects/search` (`search.py:147`), and via the notification
bell, whose `deliverable()` (`notifications.py:143-175`) composes the same clause and whose row
snapshots an excerpt.

**Verified closed** in the uncommitted WS-29b working tree, correctly and for the stated
reason. `routes/projects/core.py` now:

* gives `Visibility` an `organization_id` that **fails closed on `None`** by construction
  (`column = NULL` is never true) rather than by a check;
* resolves the tenant **before** consulting `data:org:read`, so the permission cannot widen a
  caller out of their own organization;
* composes the tenant **above** the disjunction —
  `({alias}.organization_id = :vis_org AND (grant-closure OR assignee-exists))` — and says in
  its own docstring that the outer `AND` exists precisely to scope the assignee arm;
* deletes `load_visible_task`'s private copy of the two-armed predicate so the two cannot drift;
* replaces the `unrestricted → "TRUE"` short-circuit with the tenant in both clause helpers.

`infra/postgres/161_projects_tenancy.sql:63-79,322-338` carries all 17 `pm_*` columns to
`NOT NULL`, with a `pm_organization_from_parent()` trigger (`:117`) so descendants inherit
rather than each INSERT site remembering.

**What remains open here.** Assignee writes still accept any address. Nothing leaks now, but
`PUT /tasks/{id}/assignees` returns `not_notified` (`tasks.py:591-596`) — the list of addresses
that could not see the task. Post-retrofit, every out-of-tenant address lands in it, which
makes the field a cheap oracle for *whether a given email exists in this deployment*. Refusing
an out-of-tenant assignee outright is the honest fix and is cheap while the tables are empty.

---

### S2-9 — Shared agents have one workspace and one blob partition for the whole deployment

`packages/acb_skills/acb_skills/manifest.py:235-246`:

```python
def instance_key(self, actor=None) -> str:
    """'' (shared) · u:<email> (personal) · t:<team>"""
```

There is no `o:<org>`. Every agent that has not declared `sharing.instancing='personal'`
resolves to `''`, which `agent_paths.py:136-149` maps to the **shared clone directory** and
`acb_memory/blob_store.py:101-132` maps to `agent_blob (agent_name, instance='', path)`.
`rehydrate_workspace` (`blob_store.py:345-396`) restores that partition onto a single on-disk
workspace, and its own docstring names the hazard: *"restoring the wrong instance would put one
person's notes in front of another."*

The precedent is in the tree. `infra/postgres/137_quarantine_commingled_agent_data.sql:8-31`
exists because this exact failure already happened at the **user** level and had to be resolved
by quarantining data that could not be attributed. Multi-tenancy reintroduces it at the
organization level, for every agent that is not `personal`.

The `t:<team>` key does not help: `sharing.team` is a string in the agent's own repo
(`manifest.py:244`), so it is deployment-wide by construction.

`agent_run` (the trace table) is likewise unscoped and enumerable — see S3-13.

---

### S3-10 — Global tool/plugin registries reach every tenant's agents

* `infra/postgres/13_mcp_servers.sql:8-19` — `name TEXT PRIMARY KEY`, plus
  `agent_scope JSONB DEFAULT '["*"]'` and `headers JSONB` holding auth tokens. The executor
  injects matching servers at agent-run time (file header, `:3-5`).
* `infra/postgres/14_plugins.sql:8-25` — `name TEXT UNIQUE`, `auth_config JSONB`,
  `enabled BOOLEAN DEFAULT true`, tools auto-generated from the manifest and injected into the
  agent's tool list.

Both are single global namespaces with a default scope of "every agent". Registering an MCP
server or a plugin in tenant B makes it — and its credentials, and its egress — part of tenant
A's agent runs. Conversely a tenant's private MCP endpoint (with `headers` auth) is visible in
the registry to any tenant that can list it.

`custom_api_definitions` (migration 12) and `app_tool_grants` (116) are in the same family; I
did not trace their read paths (see §4).

---

### S3-11 — Public webhook receivers authenticate a *deployment*, not a tenant

`gateway/main.py:486-508` (`PUBLIC_ROUTES`) exempts `/webhooks/clickup`, `/webhooks/gmail`,
`/webhooks/zoho`, `/agent/webhook/{source}` and the OAuth callbacks from
`require_authenticated`. Each verifies its own signature against a **single deployment-wide
secret**:

* `ingestion/sources/clickup/webhook.py:23-29` — `get_settings().clickup_webhook_secret`
* `routes/agent.py:3433-3478` — `_webhook_secret(source)` / `AGENT_WEBHOOK_SECRET`

A valid signature proves "somebody holds the deployment's secret", never "this is tenant A".
Since `POST /agent/webhook/{source}` calls `dispatch_event` directly
(`routes/agent.py:3529-3531`), a holder of that one secret can inject an event that fires every
tenant's matching workflows — the remote-trigger end of S1-3.

---

### S3-12 — Jobs that run with no `X-User-Email`, and therefore no tenant

Under D-MT-1 the tenant is derived from the caller's email, so anything without one has no
tenant. What each such path touches:

| job | file:line | reaches |
|---|---|---|
| workflow schedule scanner | `workflows/scheduler.py:106-120` | every tenant's cron triggers; runs `started_by="schedule"` |
| workflow event dispatch | `workflows/triggers.py:52-64` | S1-3 |
| WS-27f agent dispatch sink | `projects/agent_dispatch.py:102-122` | `SELECT * FROM pm_tasks WHERE id = :tid` — no visibility, no tenant; then `run_agent` (S1-4) |
| ingestion consumer | `main.py:300-311` (`INGESTION_CONSUMER`, off by default) | drains `ingestion:{clickup,zoho,gmail}` into the same global sink registry |
| CRM ⟷ Zoho sync | `main.py:318-326` (`CRM_ZOHO_SYNC`, off by default) | writes the single Zoho tenant reached via the shared credentials of S1-2 |
| email sync scheduler | `email_ingestion/scheduler.py:1-13` | enumerates `email_accounts WHERE sync_enabled` globally, but writes only into each account's own rows — see §3 |
| WhatsApp enrichment | `whatsapp/scheduler.py:60` | `SELECT id FROM wa_accounts WHERE sync_status <> 'error'` — same shape, same verdict |
| GTD provider sync, calendar rollover | `main.py:258-278` | per-`user_id`; §3 |

The two ingestion loops are gated off by default, which is the only reason they are S3 rather
than S1. **Turning either on before a tenant key exists is the same mistake as running the
ClickUp import**, and `multi_tenancy.md` §2 should say so about them too.

Branch 1b of `get_current_user` (`packages/acb_auth/acb_auth/deps.py:373-384`) is the shape of
the problem: `UserContext(email="system:internal", role=AGENT, access=SERVICE_ACCESS)` — an
identity with every permission and, under D-MT-1, no organization. `resolve_identity` returns
`(None, None)` for it (`access.py:358-362`). **Whatever `resolve_visibility` does with a null
`organization_id` is the single most consequential line of WS-29b**: null-means-everything is a
silent global leak; null-means-nothing breaks every internal job until each is given a tenant.
Fail closed, and give the jobs an explicit tenant.

---

### S3-13 — Enumeration surfaces without a tenant

* `routes/debug.py:55-116` — `GET /debug/runs` selects from `agent_run` with only the filters
  the caller supplies; `_ADMIN = require_role(EXECUTIVE, AGENT)` (`debug.py:26`). An executive
  in any tenant enumerates every tenant's agent runs, with `user_id`, `agent_name`, `model`,
  token counts and `error_message`; `GET /debug/runs/{run_id}` (`:120`) returns the full trace.
* `routes/actions.py:47` — S2-7.
* `/health` is genuinely empty ("Deliberately says nothing beyond status + env name",
  `main.py:487-488`) — safe.

### S3-14 — One sign-in domain for the deployment

`packages/acb_auth/acb_auth/deps.py:204-230` — `allowed_email_domain()` reads a single
`ALLOWED_EMAIL_DOMAIN` (default `fracktal.in`) and `is_company_email` is the whole test on the
fail-open path (`deps.py:408`). `organization.domain` exists in migration 130 and **has no
reader anywhere in the tree**. A second tenant cannot express its own domain, so either the
check is disabled for everyone or the second tenant cannot sign in. Not a leak today; a
blocker the retrofit will hit on day one.

---

## 2. The `organization_id` count is wrong — three of the six are homonyms

Checked against the live database:

```
 crm_activities | crm_activities_organization_id_fkey | REFERENCES crm_organizations(id)
 crm_contacts   | crm_contacts_organization_id_fkey   | REFERENCES crm_organizations(id)
 crm_deals      | crm_deals_organization_id_fkey      | REFERENCES crm_organizations(id)
 app_user       | app_user_organization_id_fkey       | REFERENCES organization(id)
 org_group      | org_group_organization_id_fkey      | REFERENCES organization(id)
 org_role       | org_role_organization_id_fkey       | REFERENCES organization(id)
```

`crm_*.organization_id` points at **`crm_organizations`** — the *customer company* on a deal —
not at the tenant root (`infra/postgres/144_crm.sql:74,197,289`). The CRM is **not** tenant-scoped.

Consequences:

1. `multi_tenancy.md` §1's table should read **3 scoped / 140 unscoped** at the moment it was
   written, not 6 / 137, and §1's list of "the six that are scoped" should drop the three CRM
   entries. (With migration 161 applied the real figure becomes **20 scoped / 123 unscoped** —
   3 + the 17 `pm_*`. Both numbers should be restated together, or the correction will read as
   the retrofit's doing rather than as a miscount that predated it.)
2. ✅ **FIXED same day.** `tests/unit/test_tenancy_boundary.py` matched on the **column name only**
   (`re.search(r"\borganization_id\b", …)`), so any future table with an `organization_id`
   pointing anywhere at all passes the ratchet silently. The scan should resolve the FK target,
   or at minimum assert `REFERENCES organization` on the same line.
3. ✅ **FIXED same day.** `EXPECTED_SCOPED` asserted the three CRM tables were real tenant keys. They are not,
   so the file currently claims coverage it does not have — the exact failure mode its own
   docstring at `:150-155` warns about.
4. **The column name is taken.** Scoping `crm_contacts` to a tenant cannot reuse
   `organization_id`; it needs `tenant_id`, or the CRM's column has to be renamed to
   `account_id`/`company_id`. Decide this before WS-29d reaches the CRM family, not during.

### 2.1 ⚠️ It bit MT-1b's generated migration (found 2026-08-09, on the merge)

Consequence 4 stopped being advice when `main` merged PR #404. `scripts/gen_tenant_migration.py`
emits its four phases for every discovered table not in `EXEMPT`, and the three CRM tables were
not in `EXEMPT` — because nothing in that generator, or in `tests/unit/test_tenant_coverage.py`,
ever looks at what an existing `organization_id` **references**. What it generated for them:

```sql
-- phase 1  no-op: the column exists (pointing at crm_organizations)
ALTER TABLE crm_contacts ADD COLUMN IF NOT EXISTS organization_id UUID …;
-- phase 2  writes a TENANT id into the customer-company column
UPDATE crm_contacts SET organization_id = (SELECT id FROM organization WHERE slug = 'default')
 WHERE organization_id IS NULL;
-- phase 3  a second, contradictory FK on one column
ALTER TABLE crm_contacts ADD CONSTRAINT crm_contacts_org_fk
    FOREIGN KEY (organization_id) REFERENCES organization(id) ON DELETE CASCADE;
```

**Reproduced against a live Postgres 16** with the full ladder applied — two contacts, one at a
company and one without, which is the ordinary shape of that table:

```
--- MT-1b phase 2, as the UNFIXED generator emitted it ---
ERROR:  insert or update on table "crm_contacts" violates foreign key constraint
        "crm_contacts_organization_id_fkey"
DETAIL:  Key (organization_id)=(52eb2a2d-…-e40bbbc3e1ab) is not present in table
         "crm_organizations".
```

Note what makes it invisible until then: on an **empty** `crm_contacts` the same two statements
return `UPDATE 0` and `ALTER TABLE` and look completely healthy. It needs one real row to fail,
which is exactly the condition the production database has and a test fixture does not.

Phase 2 aborts on the existing `REFERENCES crm_organizations` FK; if it somehow did not, phase 3
fails on every pre-existing value. Either way the failure lands **inside the maintenance
window, after phase 1 has already run** — which is the worst moment to discover it, and the
generator's own docstring is explicit that promoting these files is a hand act in a window.

**Fixed on this branch, at generation time instead of apply time.** `discover_homonyms()`
derives the conflicting tables from the migrations; `HOMONYM_BLOCKED` is the human sign-off
carrying each reason; the generator **refuses to emit anything and exits 1** when the two
disagree, and lists the blocked tables in every generated file's header for whoever is holding
the psql prompt. `test_tenancy_boundary.py` asserts the same two invariants in CI, so a fourth
homonym added next month is a red build rather than a failed apply.

**What is NOT fixed, and is an owner call.** Those three tables now carry **no tenant
isolation at all** — under D15's pooled RLS an unpoliced table is readable by every tenant, and
these hold customer CRM records. Blocking them makes the gap visible and stops it corrupting a
business column; it does not close it. Closing it is consequence 4 above, and it is a rename
touching every CRM route and query. Deliberately **not** filed in `EXEMPT`: exempt means "needs
no isolation", and saying that about customer contact data would turn a hole into a decision
nobody revisits.

---

## 3. Paths checked and found SAFE — with the reason

Recorded so nobody re-checks them.

**Object storage — there is none, and that is the finding.**
There is no S3, no MinIO, no boto3 and no presigned URL anywhere in first-party code (`grep`
over `apps/`, `packages/`, `infra/` finds hits only under `.venv/`). Attachments are bytes on
local disk:

* `routes/tasks/attachments.py:33-36` — `_storage_dir()` is one flat directory
  (`GTD_ATTACHMENTS_DIR`, default `data/gtd_attachments`).
* `routes/tasks/attachments.py:64-67` and `routes/projects/attachments.py:113-116` — the
  filename is `uuid4() + sanitised suffix`. **Unguessable in practice**, and the suffix is
  allow-listed against `_BLOCKED_EXT`.
* Nothing is served by path. `routes/projects/attachments.py:182-222` serves only after a
  database join proving the file hangs off a task the caller can see, and
  `routes/tasks/attachments.py:91-108` serves only to `user_id = :uid`. `_safe_name`
  (`tasks/attachments.py:39-41`) strips traversal.
* `routes/projects/attachments.py:22-28` — there is deliberately **no attach-by-id endpoint**,
  so a caller cannot join somebody else's private capture onto their own task.

Verdict: **SAFE.** One caveat worth writing down rather than acting on now: the directory is a
single flat namespace, so any future directory-listing or traversal defect leaks every tenant at
once, and backup/restore/export is not tenant-separable — a per-tenant subdirectory is cheap
now and expensive later.

Two related points, both verified rather than assumed:

* The serve route's old `if not vis.unrestricted:` guard (which dropped the predicate for
  `data:org:read` holders and would have served every organization's bytes) is **already fixed**
  in the WS-29b working tree — `projects/attachments.py:199-208` now appends
  `vis.project_clause("t.root_project_id")` unconditionally.
* `gtd_attachments` — the row that holds the path — **did not get a tenant key** in migration
  158, and does not need one: the Projects serve route reaches it only by joining through
  `pm_task_attachments` → `pm_tasks`, both now scoped, and the personal route
  (`routes/tasks/attachments.py:99-102`) filters on `user_id = :uid`, which is per-tenant under
  D-MT-1. It is on the S3 list only in the sense that a future third reader of that table would
  have no key to filter by.

`agent_blob` and `app_files` are **Postgres BYTEA/text columns, not object storage**
(`infra/postgres/71_agent_blob_store.sql`, `115_app_files.sql`), reached only through
`blob_store.py` and `routes/apps/durability.py`. Their exposure is S2-6 and S2-9, not a key
namespace.

**`GET /projects/search` inherits the predicate.** `routes/projects/search.py:145-158` calls
`resolve_visibility` then composes `task_visibility_clause(vis)` into `_SEARCH_SQL`'s
`{visible}` slot (`:113,147`) — the same function `list_tasks` and `load_visible_task` use.
There is no second copy of the closure and no way to widen it from the query string: `q` is
`like_escape`d (`:141`), `limit` is clamped to `MAX_HITS` (`:140`), and `#123` parses to a
bounded bigint or `None` (`:66-76`). **SAFE by inheritance** — with the two inherited holes,
which are S2-8 (the assignee branch) and `data:org:read` (where the clause is literally `TRUE`,
`core.py:601-602`). Search is the highest-leverage way to exploit both, because it is the one
endpoint that returns ranked titles across everything at once; it should be re-tested against
both after WS-29b lands.

**`pm_task_counters` / `task_number`.** `PRIMARY KEY (project_id)` referencing
`pm_projects(id) ON DELETE CASCADE` (`infra/postgres/146_projects.sql:182-185`), incremented by
a single `INSERT … ON CONFLICT DO UPDATE … RETURNING` under the caller's transaction
(`routes/projects/core.py:832-846`) keyed on `root_project_id`. Numbers are **per root project**,
so they are per-tenant for free once projects are; a wrong-tenant counter row is not reachable
because the key is the project id, and cross-tenant collision is meaningless. **SAFE** — it
needed no key of its own, and migration 161 gives it one anyway
(`161_projects_tenancy.sql:67,326`), which is D-MT-3's uniformity argument and is the right
call: an unindexable exception in a set of 17 is how the exception gets forgotten.

**Email and WhatsApp.** Both scope on `user_id`, which is the email address —
`email_accounts` / `wa_accounts` and every read through them
(`routes/email/automation/replyzero.py:182`, `routes/whatsapp/core.py:170`,
`whatsapp/digest.py:88`, `whatsapp/pulse.py:99`, and ~20 more). Under **D-MT-1 email is
globally unique**, so per-user scoping is per-tenant scoping. **SAFE — but only because of
D-MT-1.** If D-MT-1 is ever revisited to (b), this entire family becomes unscoped in one step,
and that is a cost that belongs in the D-MT-1 write-up.

**Projects notifications.** `routes/projects/notifications.py:143-175` resolves each recipient's
own authority through `resolve_visibility_for` (`core.py:497-556`), which goes through the real
`build_access`, then tests the task with `task_visibility_clause`. It inherits the tenant
predicate correctly and does not re-derive the closure. **SAFE by inheritance** (subject to S2-8).

**Workflow webhook hooks.** `routes/workflows/hooks.py:60-79` looks the workflow up by an
unguessable per-workflow `hook_token` and verifies HMAC over the body against that workflow's
own secret (`:51-58`). One token, one workflow. **SAFE** — and the model the shared
deployment-wide secrets of S3-11 should be moved to.

**The access cache.** `packages/acb_auth/acb_auth/access.py:37,47,86-98` — 60s TTL keyed by
lowercased email, invalidated on every admin write (`:76-83`). Email is globally unique under
D-MT-1, so the key is already tenant-unique. **SAFE.** Same for `resolve_access`'s SQL
(`:180-194`), which joins `user_role → org_role_permission` and therefore inherits `org_role`'s
existing tenant key.

**Auth header trust.** `deps.py:296-412` — a bare `X-User-Email` is refused when an internal
token is configured (`:396-401`), and the LLM key can be refused as identity
(`:170-201`). The tenant is derived from an email that only the Next.js proxy can assert.
**SAFE as an identity seam**, which is what makes D-MT-1(a) cheap. Note the residual documented
at `deps.py:27-35`: the public vhost does not yet strip `X-User-*` (`deploy/hostinger/caddy/Caddyfile`),
so the whole tenant boundary rests on an owner action that has not been taken. That is a
pre-existing item, not a new finding, but multi-tenancy raises its severity from
"cross-account" to "cross-organization".

**LiteLLM.** Vendored, absent from the live database, no proxy in this deployment. Its
`organization_id` is unrelated to ours. **SAFE to ignore; do not connect the two.**

---

## 4. What I could not determine

Stated plainly, so nobody reads silence as clearance.

1. **~~The shape of the in-flight WS-29a/b change~~ — resolved.** I read the uncommitted
   working tree (`routes/projects/core.py`, `attachments.py`, `infra/postgres/161_projects_tenancy.sql`)
   and verified the predicate lands on `Visibility`, above the disjunction. S2-8 and the
   attachments caveat are rewritten accordingly. Everything else in §1 was read from files WS-29b
   does **not** touch: `automation.py`, `search.py`, `notifications.py`, `tasks.py`,
   `agent_dispatch.py`, and everything outside `routes/projects/`.

   **This sharpens the whole document.** With the tenant now living on `Visibility`, the leak
   surface is exactly *the paths that never build one*. Every S1 and S2 finding above is such a
   path: `get_org_id` builds its own answer from a literal; `apply_task_patch` and
   `agent_dispatch.on_event` take a raw task id; `list_pending` takes nothing; `can_view`,
   `SESSION_VISIBLE_SQL` and the key store never touch Projects at all. A useful review
   question for anything new is simply: *does this code path construct a `Visibility`, and if
   not, what is its tenant?*
2. **The ingestion consumer's drain semantics.** I read the receivers
   (`ingestion/sources/*/webhook.py`) and the sink registry, not `ingestion/consumer.py`'s
   full XACK/retry path. It is off by default (`INGESTION_CONSUMER`); I have not verified what a
   replayed or dead-lettered event does with respect to tenancy.
3. **Mem0 / graphiti memory partitioning.** `manifest.memory_scope` produces
   `agent:<slug>#<instance>` (`manifest.py:248-256`), which has the same missing-org dimension
   as S2-9 — but I did not trace the Mem0 client or `add_episode` (`main.py:1473-1474`) to
   confirm whether the scope string is actually honoured as a partition boundary, or whether
   there is a second key underneath it. Treat S2-9 as covering files, and memory as unverified.
4. **`custom_api_definitions` (migration 12), `app_tool_grants` (116) and the app tool bridge.**
   Named in S3-10's family by schema shape only; I did not read their enforcement paths.
5. **Meeting bot, Note Taker and `live_session`.** Not examined. `meeting*`, `notes_glossary`,
   `transcript_segment` and `live_session` are all unscoped; whether any of them has a global
   read surface is unknown.
6. **The frontend.** `workbench/control_plane` was out of scope; if any org identity is derived
   client-side it would compound S1-1's wrong `/auth/me` answer.

---

## 5. Proposed: split the ratchet baseline

`BASELINE_UNSCOPED` (`tests/unit/test_tenancy_boundary.py:53-153`) currently conflates "debt"
with "correct as is", which overstates the 137 and invites somebody to eventually "fix"
`organization` by giving it an `organization_id`. Proposing **three** sets rather than two,
because the third is a decision and not debt, and hiding it inside either of the others is how
it gets made by accident.

**`NEVER_SCOPED` — a tenant key here would be nonsense. Membership I can defend from code:**

| table | why |
|---|---|
| `organization` | the tenant root itself (migration 130) |
| `schema_migrations` | the migration ledger; `filename` PK, infrastructure (`153_schema_migrations.sql:24-36`) |
| `feature_catalog` | a catalog of *what features exist*; who gets them lives in `org_settings`, `org_role_permission` and `user_permission_override` (`140_center_features.sql:15-31`) |

I deliberately kept this set to three. Everything else I considered failed the test "would a
tenant key here be actively wrong?" — including `audit_event` and `access_request`, which read
like infrastructure but are not (below).

**`DEPLOYMENT_GLOBAL` — shared on purpose, and each entry needs a named owner decision:**

| table | the decision that has not been made |
|---|---|
| `provider_keys` | does the platform supply LLM keys and bill usage, or does each tenant BYOK? Today: shared, silently (S1-2) |
| `model_config` | is the enabled-model catalogue a platform choice or a tenant choice? |
| `mcp_servers`, `plugins` | is the tool registry curated by the platform, or self-serve per tenant? Today: self-serve *and* shared, which is the worst pair (S3-10) |
| `copilot_config` | not examined; grouped by shape |
| `access_request` | a knock from an address with no org yet — genuinely has no tenant at knock time, but *some* tenant's admin must see it. Needs a routing rule (domain? invite token?), not a column |

Being in this set must mean "we chose this", with the reason in the file. It must not mean
"nobody has looked". Every row above is a live finding in §1 or §2.

**`NOT_YET_SCOPED` — real debt; the remaining ~130.** Two members worth calling out as *not*
belonging in `NEVER_SCOPED` even though they look like it:

* `org_group_member`, `org_role_permission`, `user_role` — reachable through a scoped parent,
  which is exactly the derivation **D-MT-3 rejects** (`multi_tenancy.md` §3). They carry the
  key like everything else.
* `audit_event` — an audit trail is per-tenant evidence, not infrastructure. One tenant reading
  another's audit log is a leak in its own right.

And the three CRM homonyms (§2) must move **out** of `EXPECTED_SCOPED` and **into**
`NOT_YET_SCOPED`, with `test_the_expected_scoped_set_is_real_not_aspirational` tightened to
check the FK target rather than the column name.

---

## 6. Suggested sequencing against `multi_tenancy.md` §5

Nothing here changes WS-29a's urgency. What it changes is what "done" means.

| | | depends on |
|---|---|---|
| **WS-29a/b** | in flight and **verified correct** — 17 columns, the predicate on `Visibility` above the disjunction, `unrestricted` scoped, the assignee arm covered (S2-8) | — |
| **WS-29e (new, urgent)** | `get_org_id` → caller-derived (S1-1). 27 call sites, one function. Blocks any second tenant existing at all | — |
| **WS-29f (new, urgent)** | tenant on the event (S1-3): `emit` carries it, `dispatch_event` filters on it, `apply_task_patch` scopes `require_row` | WS-29a |
| **WS-29g (new)** | credentials: `PRIMARY KEY (organization_id, provider)`, drop the `os.environ`/`.env` write-through (S1-2) | product decision on BYOK |
| **WS-29h (new)** | delete the `ACB_AGENT_USER_EMAIL` fallback (S1-4) — no schema change, and it is a live cross-*user* bug today |  — |
| **WS-29c** | RLS. **The strongest argument for (a) in D-MT-2 is this document**: every finding above is an application path that forgot. RLS is the only option where forgetting fails closed — provided the jobs in S3-12 get a GUC | D-MT-2 |
| **WS-29d** | the remaining families, largest blast radius first: rooms/chat (S2-5), apps (S2-6), broker (S2-7), agent blobs (S2-9) | WS-29c |

**One addition to §2's red warning.** `POST /projects/import/clickup` is correctly gated. The
same gate belongs on **`INGESTION_CONSUMER=1`** and **`CRM_ZOHO_SYNC=1`** (`main.py:300-326`):
both write unscoped rows unattended, and both are one environment variable away from doing so.
