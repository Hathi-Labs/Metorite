# CRM — the Metorite port, two modes, and the Zoho mirror

> **Board row:** WS-53 · **Created:** 2026-10-11 · **Owner directive:** 2026-10-11
> **Status:** ✅ **APPROVED 2026-10-11 — verified against code and production** at
> `origin/main` `7176fdf56` (#837). The owner approved the plan and told the
> agent to build it. D95 is decided (§16).
> **Built:** CRM-U1, the app bar and the rail of views (2026-10-11). CRM-0, the
> kill switch, default OFF (2026-10-11). CRM-T1, the tenant port (2026-10-11).
> The other slices are not built yet.

> **Supersedes for new work:** `crm_app.md` (WS-26). That file stays the as-built
> record of the CRM that exists. It keeps the data model (§3), the API (§4) and
> the sync design (§7.1) that this plan reuses. Do not re-derive them here.


---

## 0. The answer in one paragraph

The CRM already exists, and most of it works. WS-26 built the `crm_*` tables,
a 8,889-line gateway package, the `/crm` app, a CRM agent and a two-way Zoho
sync. Three things stop it from being a Metorite app:

1. Three of its tables are not tenant-isolated, so a member of any organization can read every contact, deal and activity.
2. Its Zoho link is one global credential in `.env`, so only one company can connect.
3. Its UI is older than the house look.

This plan fixes those three in that order. Then it adds the new product shape.
Each organization picks **Native** (Metorite is the CRM) or **Mirror**
(Metorite copies an external CRM, Zoho first).

---

## 1. Scope and non-goals

### 1.1 What the owner asked for (2026-10-11)

1. Port the CRM from the Command Center era into Metorite, with its database.
2. Find what is missing, and bring the UI up to the other Metorite apps.
3. Support two modes. **Native** is a standalone CRM. **Mirror** copies the
   data of an external CRM, runs insights on it, and syncs on a schedule.
4. Mirror Zoho CRM first. Add the other popular CRMs later.

### 1.2 In scope

- The tenant port of every `crm_*` table and every CRM code path (§4).
- One connector seam for external CRMs, and the Zoho adapter on it (§5).
- The parts a Native CRM still lacks (§6).
- The joins to Projects, My Tasks, Calendar, Email, WhatsApp, People and chat (§7).
- The UI uplift to the one look (§8), and insights (§9).
- The shell manifest (§11) and the path from `preview` to `live` (§13).

### 1.3 Non-goals

- **A second CRM spec for the same tables.** This file owns new work. The
  as-built record stays in `crm_app.md`.
- **Two-way sync in Mirror v1.** Mirror v1 reads only (§3.2, D95.3). Write-back
  is a later, opt-in slice (CRM-Z9).
- **A provider other than Zoho in this plan.** §5.1 defines the seam that the
  next provider uses. Each new provider gets its own slice list when the owner
  picks it (Q5).
- **Marketing automation, sequences, telephony and invoicing.** These stay out,
  as `crm_app.md` §1 already says.
- **Products, price books and proposals.** D22 puts them in CRM scope. They
  need their own section before any ticket exists (CRM-S9 is a spec slice only).

---

## 2. Measured state (2026-10-11)

### 2.1 What exists and works

| Part | Where | State |
|---|---|---|
| Schema | `infra/postgres/144_crm.sql`, `145_crm_zoho_sync.sql`, `163_crm_auto_lead_cursor.sql`, `169_crm_stage_discipline.sql` | 13 tables, applied on prod |
| API | `apps/services/gateway/gateway/routes/crm/` (13 files) | Behind `require_feature_router("crm")` |
| Records | `records.py` | CRUD for leads, deals, contacts and organizations |
| Pipeline | `pipeline.py` | Board, stage moves with entry rules, lead conversion |
| Timeline | `activities.py` | Notes, tasks, status changes, and the caller's email threads |
| Reports | `reports.py` | Forecast, funnel, win and loss, owner leaderboard |
| Export | `export.py`, `gateway/csv_export.py` | CSV, complete or refused |
| Settings | `admin.py` | Stages and lost reasons |
| Zoho import | `import_zoho.py`, `stage_metadata.py` | Backfill and stage repair |
| Zoho sync | `sync_zoho.py`, `ingestion/sources/zoho/{client,writer}.py` | Two-way, 600 s loop, `CRM_ZOHO_SYNC` |
| Auto-lead | `auto_lead.py` | Unknown inbound sender becomes a lead. `CRM_AUTO_LEAD` is OFF |
| Agent | `apps/agents/agent-crm/` | 4 read tools, 4 write tools that ask first |
| UI | `workbench/control_plane/src/app/crm/` | 7 tabs, kanban, record sheet, reports, settings |
| Chat card | `src/components/crm/CrmEvidence.tsx` | Renders the read tools |

### 2.2 Production, measured 2026-10-11

- **The CRM tables are empty.** Each entity table holds 0 rows.
  `crm_deal_statuses` holds the 6 seed rows. `crm_sync_cursors` holds 0 rows,
  so no Zoho cycle has completed against this database.
- The 2026-08-06 backfill (737 organizations, 551 deals) was on the old box.
  It did not move to Supabase. **No customer data needs a migration.**
- **9 organizations exist**, and 8 of them are not Fracktal. Their members
  hold `feature:*`, so they hold `feature:crm` (migration 207).
- **10 of the 13 `crm_*` tables are tenant-scoped on prod.** The generated
  phases gave them `organization_id NOT NULL` and FORCE RLS on 2026-08-23.
  `crm_contacts`, `crm_deals` and `crm_activities` have no tenant column, no
  policy and no FORCE RLS.
- **The seed rows belong to the org `default`.** That is 6 deal stages, 5
  lead stages and 6 lost reasons. So under RLS, `fracktalworks` and every
  other org see no stages today.
- **On the box, `CRM_ZOHO_SYNC=false`**, and the box `.env` holds no Zoho
  refresh token. The old sync loop does not run.
- `/crm` is `preview` (`launch_surface.md` §2). The nav hides it. The routes
  answer.

### 2.3 Defects

Each one is real at `7176fdf56`. The last column names the slice that fixes it.

| Id | Defect | Evidence | Slice |
|---|---|---|---|
| **CR-1** | 🔴 **Any member of any organization reads and writes the shared contacts, deals and activities.** These 3 tables have no tenant column, so `tenant_session()` filters nothing. The other 10 tables are scoped on prod. The data is empty today. The first contact, deal or activity that any org writes is visible to all 9. | `144_crm.sql`, `core.py:44-68`, `207_every_app_by_default.sql:85-112` | CRM-0, then CRM-T1 |
| **CR-2** | **The tenant column name is taken.** `crm_contacts`, `crm_deals` and `crm_activities` hold `organization_id`, but it points at `crm_organizations`, the customer company. | `scripts/gen_tenant_migration.py:138-141`, `144_crm.sql:74,197,289` | CRM-T1 |
| **CR-3** | **Unique keys are global, not per tenant.** `zoho_id UNIQUE` on 5 tables, status `name UNIQUE`, lost reason `label UNIQUE`, and `crm_sync_cursors` keyed on `module` alone. A second org collides on its first import. | `144_crm.sql:49,80,108,118,133,177,223,293`, `145_crm_zoho_sync.sql:149-158` | CRM-T1 |
| **CR-4** | **Three background paths open an unbound session.** The sync loop, the auto-lead hook and the broker push handler use `_get_db()`. Under FORCE RLS they see zero rows. Today they see every org. | `sync_zoho.py:1152-1163`, `auto_lead.py:279-284,656-690`, `broker_handlers.py:191-200`, HANDOFF H-201 | CRM-T3 |
| **CR-5** | **The Zoho token is one global value in `.env`.** `_persist_tokens` writes `ZOHO_REFRESH_TOKEN` to the box file. The client caches the access token in `.zoho_token_cache.json` in the working directory. | `routes/oauth.py:62-70,226-260`, `ingestion/sources/zoho/client.py:17-75` | CRM-Z2 |
| **CR-6** | **Per-org keys leak into a process global.** `configure_integrations` copies `provider_keys` into `os.environ`, for every integration and not only Zoho. CRM-Z1 does not touch it, because the adapter takes its credential as an argument. | `acb_llm/key_store.py:416-530`, `routes/integrations.py:55-80` | CRM-Z11 for Zoho. The other integrations need their own item |
| **CR-7** | **The Zoho data centre is one operator setting.** `ZOHO_API_DOMAIN` and `ZOHO_ACCOUNTS_URL` default to `.com`. Only `acb_skills/integrations.py:78` reads `ZOHO_REGION`, and the client does not. | `routes/integrations.py:106-123,430-470` | CRM-Z1 |
| **CR-8** | **No rate-limit handling.** Neither the client nor the writer handles a 429 or Zoho's `TOO_MANY_REQUESTS`. | `ingestion/sources/zoho/client.py`, `writer.py` | CRM-Z1 |
| **CR-9** | **One sync for the whole process.** `_cycle_lock` is a process-global `asyncio.Lock`, and the loop syncs one Zoho tenant. | `sync_zoho.py:1116` | CRM-Z5 |
| **CR-10** | **Any member edits the stages and lost reasons.** `admin.py` checks `feature:crm` only. | `admin.py:278-425` | CRM-T5 |
| **CR-11** | **A second task store.** CRM follow-ups are `crm_activities type='task'`. D52 and D53 say `pm_tasks` holds every task in the product. | `144_crm.sql:276-301`, `crm_app.md` §6 | CRM-S1 |
| **CR-12** | **The UI breaks the house rules.** No `AppTopBar`, 6 hand-rolled overlays, raw `<select>`, a third colour map (`board.ts:60-87`), no read cache, no `EmptyState`, no shell manifest. | The old header (`page.tsx` at `ba25a686c`, lines 199-293, closed by CRM-U1), H-148, `sharedTaskUi.test.ts:467-479` | CRM-U1 to U6 |
| **CR-13** | **The kanban has its own drop logic.** It uses HTML5 drag events and not `lib/boardDrop.ts`. | `KanbanBoard.tsx:74,185` | CRM-U3 |
| **CR-14** | **Offboarding skips the CRM.** An org purge leaves its CRM rows. | `acb_auth/offboard.py:17-26,67-73,134` | CRM-T4 |
| **CR-15** | **No test runs CRM SQL against a real database (R8).** All 14 CRM suites use `_crm_fakes.py`. | `tests/unit/test_crm_*.py` | CRM-T4 |
| **CR-16** | **Only one pipeline.** `stage_metadata.py` stops when Zoho has more than one. A real Zoho org often has two or more. | `stage_metadata.py`, D-CRM-11 | CRM-S7 |
| **CR-17** | **No custom fields.** Fields live in migrations. A Zoho org has many custom fields, and a mirror without them loses data. | `crm_app.md` §1 non-goals | CRM-Z3 |

---

## 3. The two modes

### 3.1 What a mode is

A **mode** is a property of the organization, not of a member. It lives in
one row of a new `crm_settings` table, keyed on `organization_id`.

| Mode | Who owns the records | What Metorite does |
|---|---|---|
| **Native** | Metorite | Every CRM feature, read and write |
| **Mirror** | The external CRM | Copy, show, search, report, and give insights. No write to a mirrored field |

A new organization starts with **no mode**. The first open of `/crm` shows a
choice of two cards: "Use Metorite as your CRM" and "Connect your CRM". This
follows the one-click rule (memory: customer setup is one click).

### 3.2 Mirror v1 reads only

D95.3 decides this. The reasons:

1. **The customer's CRM is their system of record.** Root `AGENTS.md`
   constraint 8 holds. A read-only mirror cannot corrupt it.
2. **The consent screen asks for less.** Read scopes are an easier "yes" for
   a customer admin than full write scopes.
3. **D52.5 named five costs of a connector.** They are a token, a webhook
   receiver, a poll scheduler, a rate limiter and a three-way merge. A
   read-only mirror needs the first four and not the merge.
4. **The write path exists.** WS-26b built it. CRM-Z9 can turn it on for one
   org later, behind its own flag.

### 3.3 What a member can do in Mirror mode

- Read every mirrored record, with its custom fields.
- Filter, sort, save views, export and search.
- See reports and insights, and ask the CRM assistant.
- Add **Metorite-only** items to a mirrored record: a task in the one task
  store, a note, or a logged call. A badge marks each one as "Only in
  Metorite". CRM-Z9 can push them upstream later.
- Open the record in the external CRM through a deep link.

A member cannot edit a mirrored field. The gateway refuses it with a 409 that
names the source system. The UI shows the field as locked. The gateway is the
boundary, and the UI is a courtesy.

### 3.4 Moving between modes

| From | To | Allowed | How |
|---|---|---|---|
| no mode | Native or Mirror | Yes | The setup choice |
| Mirror | Native | Yes | "Make Metorite your CRM": a final sync, then the mode flips and the connection closes. This is WS-26e's cutover, made a per-org action (CRM-Z10) |
| Native | Mirror | Only while the org has 0 native records | A mirror over native data is a merge, and v1 has no merge |

### 3.5 The architecture: one store, two writers

**Both modes use one store.** The `crm_*` tables hold the records in Native
mode and in Mirror mode. There is no second schema for mirrored data, and no
cache beside the tables. The mode decides only **who may write a record
field**.

```
            Native mode                          Mirror mode
            ───────────                          ───────────
  member ─► /crm routes ─┐            Zoho ─► adapter ─► sync engine ─┐
  agent  ─► /crm routes ─┤                                            │
                         ▼                                            ▼
                 core.insert_row / core.update_row          core.upsert_from_source
                   (the write guard runs here)              (the only bypass of the guard)
                         │                                            │
                         └──────────────► crm_* tables ◄──────────────┘
                                   (organization_id + FORCE RLS)
                                               │
          ┌──────────────┬──────────────┬──────┴───────┬──────────────┬──────────────┐
          ▼              ▼              ▼              ▼              ▼              ▼
       /crm UI      crm-assistant    reports       insights       shell search    exports
```

Four rules make this hold:

1. **Every reader is blind to the mode.** The UI, the agent, the reports, the
   insights, the search provider and the export read the same tables through
   the same routes. So an AI agent reasons over Zoho data exactly as it
   reasons over native data. No reader has a Zoho branch.
2. **One write guard.** `core.insert_row` and `core.update_row` call
   `assert_writable(org, entity, fields)`. In Mirror mode it refuses a write
   to a field that came from the source, with a 409 that names the source.
   A Metorite-only item (§3.3) passes. The fence is
   `test_crm_mirror_readonly.py`.
3. **One bypass.** The sync engine writes through
   `core.upsert_from_source` only. No route, agent tool or skill imports it.
   An AST fence holds that, in the shape of the WS-26h reachability fences.
4. **The provider stays behind the adapter.** The sync engine calls the
   `CrmSource` protocol (§5.1) and never a provider client. A second provider
   adds one adapter, and the engine, the guard and every reader stay as they
   are.

**The sync engine is generic.** `sync_zoho.py` becomes `crm_sync/engine.py`.
It loops over the orgs with an active connection, binds each tenant, takes an
advisory lock for that connection, and calls the adapter. The push half that
WS-26b built stays in the engine. It runs only for a connection with
`write_back = true`, and every connection starts with `false` (D95.3).

**What the AI agents read.** `crm-assistant` reads `/crm` routes as the
member, so it sees the member's tenant and nothing else. Its read tools work
in both modes. In Mirror mode it offers no write tool for a mirrored field.
The insights of §9 run on the same rows. So a question such as "which Zoho
deals went quiet this month" needs no Zoho call. It reads the mirror.

---

## 4. The tenant port (milestone M1)

### 4.1 The rename that frees the name

`organization` means the tenant everywhere else in Metorite. In the CRM it
also means the customer company. That breaks W3 (one term for one thing), and
it blocks the tenant column (CR-2).

**D95.2 (decided 2026-10-11):**

| Today | After |
|---|---|
| table `crm_organizations` | `crm_companies` |
| column `organization_id` on `crm_contacts`, `crm_deals`, `crm_activities` | `company_id` |
| UI label "Organizations" | "Companies" |
| tenant column | `organization_id` on every `crm_*` table, `REFERENCES organization(id)` |

"Company" is the word HubSpot uses. Zoho and Salesforce say "Account", but
`email_accounts` already holds that word in Metorite.

**How.** Use the pattern the owner approved for the `gtd_*` rename on
2026-09-21. The rename lives inside the migration that creates each table,
behind a guard, so one file answers a fresh install, an upgrade and a replay.
Index, constraint and policy names keep the old spelling.

⚠️ **Sweep the tree first, and add the guarded prologue second.** If you do it
the other way, the sweep rewrites the prologue into a no-op. The fence is the
shape of `tests/unit/test_gtd_rename_upgrade.py`.

⚠️ **R6 says "never rename in place".** The case for an exception is narrow and
measured. The tables hold 0 rows on prod, the app is `preview`, and the old
code can only fail during the restart window of one deploy. The owner accepted
this exception (Q2). CRM-0 merges first, with the switch OFF, so that window
serves no CRM traffic.

⚠️ **A changed migration file runs again on the next deploy**
(`scripts/apply_migrations.sh:281-290`). So guard each seed INSERT in
`144_crm.sql` to run only when its table has no `organization_id` column. Else
the INSERT meets NOT NULL and FORCE RLS with no tenant bound, and the deploy
stops.

### 4.2 The tenant column and RLS

One migration, at the next free number at build time (R1). Number 240 is free
on 2026-10-11. Use `223_email_accounts_unique_per_tenant.sql` as the template,
with the `$rls$` block of `238_whatsapp_bot_messages.sql:85-103`.

**It must give one result from two starting shapes.** On prod, 10 tables
already carry the column and the policy. On a fresh install, none does. So
each step is `IF NOT EXISTS` or guarded.

1. Add `organization_id UUID REFERENCES organization(id) ON DELETE CASCADE`
   to each of the 13 `crm_*` tables where it is missing. Declare the foreign
   key inline, because generated phase 3 adds `<t>_org_fk` by name.
2. Fill a NULL `organization_id` with the id of `fracktalworks` if it
   exists, else with the only org. With two or more orgs and no
   `fracktalworks`, stop and name the table. The seed rows on prod already
   belong to `default`, and they stay there. An org gets its own seed when
   its mode becomes `native` (CRM-T5). A Mirror org gets its stages from
   the source.
3. Set `NOT NULL`.
4. `ENABLE` and `FORCE ROW LEVEL SECURITY`, with one `USING` and `WITH CHECK`
   policy on `app.tenant_id`.
5. Replace each global unique key with a per-tenant one (CR-3).
   `ON CONFLICT (zoho_id)` in `core.py:1091` changes in the same PR.
6. Remove the three tables from `HOMONYM_BLOCKED`. Regenerate
   `infra/postgres/generated/`.

### 4.3 The background paths (CR-4)

Each path binds the tenant it serves, and never the ambient one (H4).

| Path | Binds |
|---|---|
| The sync loop | Each org with an active connection, one `tenant_session(org)` per step. No session stays open across a provider call. **CRM-Z5 builds this**, because it is the engine rewrite. Until then CRM-T3 makes the old loop fail closed |
| The auto-lead hook | The tenant that the email hook already bound (`current_tenant()`), for the whole hook, also for the unknown-sender read |
| The broker push handler | The tenant that the `approve` call bound. `action_broker/broker.py:214-218` forbids a tenant taken from a proposal, a payload or a request field. RLS on `pending_actions` makes it equal to the proposal's org |

Copy `apps/services/email_ingestion/email_ingestion/scheduler.py` and its fence
`tests/unit/test_email_scheduler_tenancy.py`. CRM-Z5 replaces the process
lock with `pg_advisory_xact_lock`, keyed on the connection. Close H-201's
CRM half in CRM-T3, and point its sync bullet at CRM-Z5.

---

## 5. The connector seam and the Zoho mirror

### 5.1 One seam for every external CRM

Do not build a Zoho mirror. Build a mirror with a Zoho adapter. The next
provider then adds one adapter and no new seam.

```
gateway/crm_sources/
  base.py          # CrmSource protocol + canonical record shapes
  registry.py      # provider slug -> adapter; one list
  zoho/
    auth.py        # OAuth, data centre, refresh
    client.py      # read calls, paging, rate limits, credits
    mapping.py     # Zoho module and field -> canonical entity and field
    writer.py      # moved from ingestion/; used only by CRM-Z9
```

The protocol, in words. An adapter must:

1. Start the consent flow and finish it, and return a credential blob.
2. Refresh the blob, and report `needs_reconnect` when the refresh fails.
3. List the field definitions and the pipelines of each entity.
4. List the records changed since a cursor, one page at a time.
5. List the records deleted since a cursor.
6. List the users, so the sync can map an owner to a member by email.
7. Build the deep link to one record.
8. Report the API budget it used and what is left.
9. Revoke the credential at the provider (CRM-Z2 adds it).

**The canonical entities** are the four that exist: lead, deal, contact and
company. Plus activities (notes, calls, meetings) and users. A field that has
no canonical column goes to `custom JSONB`, described by `crm_field_defs`.

**The rule that stops a second seam (R7):** no file under `crm_sources/` reads
`settings.zoho_*`, `os.environ` or a file on disk for a credential. The
credential comes from `integration_connections` only. The fence is
`tests/unit/test_crm_sources_no_global_creds.py` (§15).

### 5.2 Zoho facts this design rests on

Confirmed 2026-10-11 against Zoho's developer documentation:

- **One OAuth client serves every data centre** when it has multi-DC on. The
  consent request goes to `accounts.zoho.com`. The redirect returns `location`
  and `accounts-server`. The token request goes to that server, and the token
  response returns `api_domain`. The data centres are US, EU, IN, AU, JP, CA
  and CN.
- **The API budget belongs to the customer.** Zoho counts credits in a 24-hour
  window. The cap depends on the edition and the licence count. For example,
  Professional gets 10,000 plus 500 per licence, up to 500,000. A normal call
  costs 1 credit. Past the cap Zoho answers `TOO_MANY_REQUESTS`.

**To verify in CRM-Z0, before any build:**

- the exact names of the read-only scopes
- the limits of the Bulk Read API
- the lifetime of a Notification API channel
- the shape of a deep link to a record, for each data centre

**What follows from the budget.** The mirror spends the customer's own credits,
so it must be a polite guest. The default sync budget is **10 % of the daily
credits**, which an org admin can change. The sync reads the remaining credits
and backs off before Zoho refuses it. Settings → CRM shows the credits used.

### 5.3 New tables

All tenant-scoped, with FORCE RLS, in the migrations of the slice that needs them.

| Table | Holds | Slice |
|---|---|---|
| `crm_settings` | One row per org: `mode`, sync interval, credit budget, the auto-lead switch | CRM-Z2 (mode can land in CRM-T5) |
| `integration_connections` | **General, not CRM-only** (D95.4, amended 2026-10-11). `provider`, `owning_app`, `scope_kind` (`org` or `member`), `member_email`, `status`, `provider_meta` (location, `accounts_server`, `api_domain`), `credentials_encrypted` (Fernet, through `key_store`), granted scopes, `connected_by`, `last_sync_at`, `last_error`. One store module is its only reader of the blob. The Integrations app (WS-54) reads this one registry. `crm_settings.connection_id` points at the row | CRM-Z2 |
| `crm_field_defs` | Entity, key, label, type, options, `source`, the provider's API name, position, visible | CRM-Z3 |
| `crm_pipelines` | Name, position, `ext_id`. `crm_deal_statuses` gains `pipeline_id` | CRM-S7, or CRM-Z3 if Zoho needs it first |
| `crm_sync_runs` | Started, finished, counts per entity, credits used, errors | CRM-Z5 |
| `crm_views` | Saved views, the `pm_views` shape | CRM-S2 |

Columns added to the five entity tables (expand only, nullable, R6):
`ext_source TEXT`, `ext_id TEXT`, `ext_modified_at TIMESTAMPTZ`,
`custom JSONB NOT NULL DEFAULT '{}'`, `currency` and `exchange_rate` on deals,
and `UNIQUE (organization_id, ext_source, ext_id)`. `zoho_id` stays until a
later contract release removes it.

### 5.4 The sync

| Step | What happens |
|---|---|
| Connect | An org admin clicks Connect, consents at Zoho, and returns. The callback checks the signed state (the email seam, `transport/signing.py`), stores the blob and sets the mode to Mirror |
| Import | The first import reads fields, pipelines and users, then every record. It shows a progress bar with an estimate and resumes after a pause (the D-EM-16 rule from Email) |
| Owner map | A Zoho user maps to a member by email. An unmatched owner keeps the name and shows "Not in Metorite" |
| Incremental | Every 10 minutes by default, per org. `If-Modified-Since` on a cursor per entity, plus the deleted list |
| Sync now | A button for an org admin. It refuses while a cycle runs for that org |
| Health | Each cycle writes one `crm_sync_runs` row. Settings → CRM reads it |
| Failure | A refresh failure sets `needs_reconnect`, stops the loop for that org and puts a need in the admin's bell. It never retries a dead token |
| Disconnect | Revoke the token at Zoho, delete the blob, keep the mirrored data read-only, and show "Last synced on <date>" |

Reuse from `sync_zoho.py`: the cursor rules, the SAVEPOINT for each record, and
the backoff with its retry count. Drop the push half from the Mirror v1 path.

### 5.5 What retires

When CRM-Z5 is live and Fracktal has chosen its mode, retire the global path
(CRM-Z11):

- `settings.zoho_*`, `.env.example`'s Zoho block, and `_persist_tokens` in `routes/oauth.py`
- `.zoho_token_cache.json`
- the read client `ingestion/sources/zoho/client.py`, which CRM-Z1 copies into `crm_sources/zoho/`
- the copy of each integration key into `os.environ` for Zoho (CR-6)
- the unwired nightly job `ingestion/scheduler.py::_run_zoho` and `scripts/zoho_sync.py`
- the Phase-0 graph normaliser in `ingestion/sources/zoho/normaliser.py`
- the shared-secret `/webhooks/zoho`
- the `zoho-crm` env form on Integrations

`crm_app.md` §7.4 lists the exact paths. Re-check each one at dispatch.

---

## 6. Native mode — what is missing

| Id | Missing | Why it matters | Notes |
|---|---|---|---|
| CRM-S1 | **Follow-ups in the one task store** | CR-11. A follow-up must show in My Tasks and Calendar with no sync | A task links to its CRM record through an entity ref. The timeline reads it from `pm_tasks` |
| CRM-S2 | **Saved views** | Every other list app has them | `pm_views` shape. WS-26i blocked this on tenancy, and CRM-T1 clears it |
| CRM-S3 | **CSV import** | The first thing a new customer does | The D80 file-importer rule. Reuse `project_import.md`'s seam. Dedupe by email and by domain |
| CRM-S4 | **Bulk edit** | Moving 50 deals one by one is not a product | The WS-26i-bulk contract in `crm_app.md` §9 exists. Re-audit it first |
| CRM-S5 | **Duplicate detection and merge** | Imports make duplicates | Native only. In Mirror the merge happens in the source CRM |
| CRM-S6 | **Custom fields editor** | Every company has its own fields | Native only. It writes `crm_field_defs` from CRM-Z3 |
| CRM-S7 | **More than one pipeline** | CR-16. Sales and service often differ | Reopens D-CRM-11, which was agent-proposed |
| CRM-S8 | **Lead capture** | Leads must arrive without a person typing them | A public form endpoint per org, with a signed key and a rate limit. WhatsApp inbound. Auto-lead as a per-org setting, not a global flag |
| CRM-S9 | **Products, line items and quotes** | D22 scope | **Spec slice only.** No ticket until it has a section |
| CRM-S10 | **Field history** | "Who changed the amount?" | Reuse the Projects field-change pattern (H-88) |
| CRM-S11 | **Notifications** | A deal assigned to you, a deal gone stale, a task due | Through the shell's needs provider, never an app bell |
| CRM-S12 | **Currency** | A mirror brings deals in many currencies | Store the currency and the rate. Reports convert to the org's home currency |

---

## 7. How the CRM joins Metorite

The rule from `crm_app.md` §6 holds: **bind, do not rebuild.** The CRM keeps
its records. Every other concern belongs to the app that owns it.

| Metorite part | The join | Seam |
|---|---|---|
| **Projects, My Tasks, Calendar** | A CRM follow-up is a `pm_tasks` row with an entity ref to the deal, lead, contact or company. Done in one app is done in all three | The one task store (D52 to D54) |
| **Email** | The record timeline shows the caller's own threads with that address. "Create lead from this email". Auto-lead per org | `activities.py` email join. A mailbox stays private (D-EM-4) |
| **WhatsApp** | Link a WhatsApp contact to a CRM contact. An inbound chat from an unknown number can become a lead | `_KNOWN_SYSTEMS` already parses `crm:` refs |
| **People** | A record owner is a member. A Zoho owner maps to a member by email | `app_user` |
| **Chat** | `crm-assistant` is the app's `agent`. Read tools work in both modes. Write tools hide in Mirror mode | `apps/agents/agent-crm/`, `CrmEvidence.tsx` |
| **Shell** | Jobs, a search provider and a needs provider (§11) | `navigation_shell.md` §5 |
| **Reports** | CRM reports join the one report seam, so they can be saved, scheduled and sent | `projects_reports.md` (WS-27bn). Do not build a second builder |
| **Notes and meetings** | A meeting note can link to a deal, and the deal timeline shows it | Entity ref |
| **Admin** | Settings → CRM: mode, connection, pipelines, stages, fields, lost reasons, who sees the CRM | The Settings shell |
| **Billing** | The CRM is inside the ₹500 seat. AI insights spend credits | `launch_surface.md` §4 |
| **Export** | One CSV seam | `gateway/csv_export.py`, `@/lib/export` |
| **Integrations** | The Zoho connection is one row in the general registry. The Integrations app lists it beside every other connection | `integration_connections`, WS-54 |

### 7.1 Where a connection lives (orchestrator decision, 2026-10-11, owner told)

**Connect in the app, manage in one place.**

- **The connect flow lives in the app that needs it.** The CRM offers
  "Connect Zoho" on its first open and in Settings → CRM. The app knows its
  mode, its scopes and its import. Email and WhatsApp work the same way
  (D-EM-1).
- **The Integrations app (WS-54, specified next) is the admin console over one registry.** It
  lists every connection with its owner app, the member who connected it,
  the scopes, the health and the last sync. It offers reconnect and
  disconnect, and it links to the owning app for setup. It is also the home
  of a connection with no app, such as a general MCP server.
- **No customer key goes into `os.environ` or `.env`.** The Integrations page
  of today does that (O-GM-5, CR-6). WS-54 retires it for customer keys.

### 7.2 The REST API for the mirror, Zoho's MCP for agent actions

- **The mirror uses Zoho's REST API.** MCP is a tool call for an agent. It has
  no change feed, no deleted list and no bulk read. The insights, reports and
  search of §9 need the rows in Metorite. An MCP call for each question costs
  the customer's credits and time, and it fails when Zoho is down.
- **Agent actions in Zoho use Zoho's MCP (CRM-Z9).** Zoho runs MCP servers for
  Zoho CRM. A member signs in with OAuth, and Zoho applies that member's own
  roles. So Zoho decides who may change what, and Zoho keeps the tools. This
  replaces the plan to turn on our own writer for agent writes. Metorite's
  confirmation step still wraps each action.
- **Two checks come before CRM-Z9.** First, MCP injection reaches only the
  Copilot-SDK agents today, and D92 moves every agent to MAF (WS-8c). WS-54
  owns that fix. Second, Zoho's MCP must connect with one click, with no step
  in the customer's Zoho console. CRM-Z0 checks it.

---

## 8. UI and UX — one look

The CRM becomes a projection of the house look, not a surface with its own.
`workbench/control_plane/DESIGN_SYSTEM.md` and `AGENTS.md` beside it are the
contract.

### 8.1 Layout

| Region | Content | Component |
|---|---|---|
| Title bar | Rail toggle, "CRM", a subtitle with the mode ("Native", or "Synced from Zoho · 4 min ago"), New, and the tools | `AppTopBar` |
| Rail | Pipeline, Deals, Leads, Contacts, Companies, Activities, Reports. Saved views nest under each. Pipelines list when there are two or more | `RailRow`, `OverflowTip` |
| Main | Board, list or table for deals. List or table for the others. A filter bar with chips and saved views | `ModeSwitch`, the Projects `FilterBar` shape, `FilterPills` |
| Record | A docked side panel on desktop, a sheet on mobile. Header: name, stage, amount, owner. Tabs: Overview, Timeline, Related, Tasks | The `TaskPanel` pattern, `Modal` with `end` and `sheet` |
| States | Skeletons while loading, three empty states, toasts | `Skeleton*`, `EmptyState`, `useToast` |

### 8.2 The changes, by rule

| Rule | Today | After | Slice |
|---|---|---|---|
| 11 `AppTopBar` | Old Settings header | `AppTopBar` with the rail toggle | CRM-U1 |
| 12 `RailRow` | Tabs across the top | A rail of views | CRM-U1 |
| 8 primitives | 6 hand-rolled overlays | `Modal` and `ConfirmDialog` | CRM-U2 |
| 3 controls | Raw `<select>` and checkboxes | `Select`, `SelectButton`, `Checkbox` | CRM-U2 |
| 4 one vocabulary | `TONES` in `board.ts` | `statusAccent`. Delete the `sharedTaskUi.test.ts` exemption | CRM-U3 |
| 7 categorical | No rule | Sources and tags through `categoricalAccent` | CRM-U3 |
| shared drop | Own drag code | `lib/boardDrop.ts` | CRM-U3 |
| 9 one read cache | Fetch in a zustand store | `useCachedResource`, `invalidate()` after a write | CRM-U4 |
| 10 shell | No manifest | §11 | CRM-L1 |

### 8.3 New surfaces

- **The first open (CRM-U5).** Two cards: "Use Metorite as your CRM" and
  "Connect your CRM". Under the second, Zoho is live and the others show
  "Coming soon", which is the Gmail precedent (D-EM-35).
- **The connect flow (CRM-Z7).** Copy WhatsApp's guided `/whatsapp/connect` and
  Email's `ConnectEmptyState`. The steps are consent, import with progress, and
  done.
- **Mirror marks (CRM-Z7).** A lock on each mirrored field, an "Open in Zoho"
  link, an "Only in Metorite" badge, and the last sync time in the subtitle.
- **Settings → CRM (CRM-U6).** `PipelineSettings` moves here from the tab
  strip. Add the mode, the connection health (last sync, next sync, credits
  used, errors) and the field map.

### 8.4 The gate

The conformance suite reads 8 regexes, and nothing tests layout. So before
each UI PR, run the `visual-review` skill. Look at the CRM in light mode, at
compact density, under a changed accent, at four widths down to mobile, and
beside Projects. Every baseline in `conformance.test.ts` for `app/crm/` must
go down, and never up.

---

## 9. Insights and AI

Every insight reads the same tables, so it works in both modes. That is the
reason a Mirror customer pays.

| Id | Insight | What it shows | Cost |
|---|---|---|---|
| CRM-I1 | **Pipeline health** | Stale deals, slipped close dates, deals with no next step, coverage against target | No model |
| CRM-I2 | **Forecast and targets** | Weighted forecast by month and owner, against a target per member | No model. Needs a `crm_targets` table |
| CRM-I3 | **Deal brief** | A short summary of one record from its timeline, emails and tasks, with the next step | Credits, on demand |
| CRM-I4 | **Weekly digest** | Pipeline change, wins, losses, risks. Sent by email or WhatsApp | The report send seam (H-111). Credits |
| CRM-I5 | **Ask your CRM** | Questions in chat. "Which deals over ₹5 lakh have had no contact in 3 weeks?" | `crm-assistant`. A big question goes through WS-48's narrowing |
| CRM-I6 | **Data quality** | Missing fields, duplicates and dead contacts, with a score | No model |

⚠️ **The product's own AI is not metered today** (H-171). Each AI insight above
must go through the meter before it ships, or it is free forever.

---

## 10. Security and tenancy

1. **Tenancy is a row** (D15). Every `crm_*` table has `organization_id` and
   FORCE RLS. Each read and write runs in `tenant_session()`.
2. **No tenant and no identity come from the request** (R5).
3. **A credential stays in one row.** It is Fernet-encrypted through
   `key_store`. It never reaches `os.environ`, `.env`, a disk cache, a log or
   the browser.
4. **The OAuth state is signed** with the org, the member, the provider and a
   10-minute expiry. Reuse `routes/email/transport/signing.py`, and give it a
   keyword-only `purpose` argument. The email purpose stays the default. The
   CRM uses `crm-oauth-state:v1`, so an email state never passes the CRM
   check. `test_email_oauth_state.py` must stay green.
5. **Only an org admin connects, disconnects or changes the mode.** The floor
   is `admin:access:manage`, as the Zoho routes use today.
6. **Who sees the CRM is an admin choice** (Q3). A mirror copies everything
   the connecting Zoho user sees, and Zoho's own roles do not cross over. So
   the default must not be "every member".
7. **A webhook finds its tenant from a signed URL,** never from the body. This
   is the Email lesson (O-GM-4). It applies to CRM-Z8 only.
8. **The CRM agent never queries the database or the provider.** It calls the
   `/crm` routes as the member.

---

## 11. Shell manifest (R9)

| Field | Value |
|---|---|
| `href` | `/crm` |
| `label` | CRM |
| `feature` | `crm` |
| `launch` | `preview` until CRM-L2 |
| `team` | `across` |
| `blurb` | "Track leads and deals, or mirror your CRM" |
| `keywords` | crm, sales, deal, lead, pipeline, contact, company, zoho |
| `jobs` | `crm.new_lead`, `crm.new_deal`, `crm.log_call`, `crm.find_record`. Each opens a form and never saves |
| `search` | `crm`. A server provider that searches the four entities in the caller's tenant |
| `needs` | `crm`. Stale deals, deals assigned to me, a connection that needs a reconnect (admins only) |
| `cards` | Pipeline summary for Home (later) |
| `agent` | `crm-assistant` |

The jobs go into both lists, `src/lib/shell/registry.ts` and
`gateway/routes/shell/intent.py`, in one PR. `test_shell_intent.py` holds them
equal. The CRM never joins `SEAM_DEBT`.

---

## 12. Owner setup (OWNER-GATE)

These acts are the owner's. An agent writes the runbook and stops.

| Act | Why it is the owner's | Slice |
|---|---|---|
| ✅ Confirm D95 and answer Q1 to Q7 | Product shape. **Done 2026-10-11** | Before CRM-T1 |
| Register Metorite's Zoho OAuth client, with multi-DC on and the redirect URL | A third-party account | CRM-Z0 runbook, then the owner |
| Drop the client id and secret into `~/.metorite/secrets` | Credentials. An agent pushes them with `scripts/secrets.sh` | CRM-Z2 |
| Connect a real customer's Zoho for the first time | A third party's data (§3a rule 3) | CRM-Z5 |
| Pick Fracktal's mode | Fracktal is customer zero (D36) | Q4 |
| Promote `/crm` from `preview` to `live` | §6.0 C3, H-21 | CRM-L2 |
| Turn on write-back for any org | An outward write to a customer's system of record | CRM-Z9 |

---

## 13. Tickets

### 13.1 The order

```
CRM-0 ─► CRM-T1 ─► CRM-T2 ─► CRM-T3 ─► CRM-T4 ─► CRM-T5
                                                   │
              ┌────────────────────────────────────┼──────────────────┐
              ▼                                    ▼                  ▼
     UI: U1 ► U2 ► U3 ► U4 ► U5 ► U6     Mirror: Z0 ► Z1 ► Z2 ► Z3 ► Z4 ► Z5 ► Z6 ► Z7
              │                                                       │
              └──────────────────────► CRM-L1 ◄───────────────────────┘
                                          │
                                       CRM-L2 (owner)
                                          │
                      Native S1–S12, insights I1–I6, Z8–Z11 in any order
```

CRM-0 and the T slices are milestone **M1** (a second org can exist safely).
The U, Z and L slices are milestone **M4** (the apps we sell).

### 13.2 Phase 0 — close the leak now

| Id | What | Gate | Done when |
|---|---|---|---|
| **CRM-0** ✅ built 2026-10-11 | A kill switch. `CRM_ENABLED` (default OFF) makes every `/crm` route answer 404, the nav hide CRM, and `crm-assistant` refuse. An allowlist of organization ids, `CRM_ORGS` (or `*`), turns it on for named orgs only. This is the repo idiom (`whatsapp_assistant_orgs`) | 🟢 AGENT-SAFE to build. The flag stays OFF for every org until CRM-T4 merges | A member of an org outside `CRM_ORGS` gets 404 on each of the 4 entity lists, a create and the agent's `search_crm`. Fence: `tests/unit/test_crm_kill_switch.py`, measured red first |

### 13.3 Phase 1 — the tenant port (§4)

| Id | What | Gate | Done when |
|---|---|---|---|
| **CRM-T1** ✅ built 2026-10-11 | The rename (§4.1) and the tenant column, RLS and per-tenant keys (§4.2), in one migration. The code sweep for the rename in the same PR: routes, agent, UI types, fakes, tests | 🟢 AGENT-SAFE. After CRM-0 is merged and deployed | `HOMONYM_BLOCKED` is empty. `test_tenancy_boundary.py` and `test_tenant_coverage.py` pass with the CRM tables scoped. A fresh install, an upgrade and a replay give the same schema (the `test_gtd_rename_upgrade.py` shape) |
| ~~**CRM-T2**~~ | ✅ **Already true** (audit, 2026-10-11). Every request handler uses `_tenant_session` (`test_db_engine_seam.py:359-361`). `_get_db` stays only in the three background paths, and CRM-T3 removes them | — | Moved into CRM-T3 |
| **CRM-T3** | The three background paths bind their tenant (§4.3). The advisory lock replaces `_cycle_lock` | 🟢 AGENT-SAFE. ⚠️ Touches the sync loop, which `work_plan.md` §6 WS-26 (a) gates while it runs. On prod it does not run (0 cursors) | `test_crm_sync_tenancy.py`: two orgs, two cycles, each sees only its own rows. `test_db_engine_seam.py` exempts no CRM file. H-201's CRM half is deleted |
| **CRM-T4** | R8: a real-database suite for the CRM. Offboarding purges CRM rows | 🟢 AGENT-SAFE | `test_crm_tenancy_r8.py` on the dev DB: org B reads 0 of org A's rows on every list, `WITH CHECK` refuses a cross-org insert, an unbound session reads 0 rows. `test_org_purge_tenant.py` covers the CRM. Each case red first |
| **CRM-T5** | Per-org seeds of stages and lost reasons, in the write that sets the mode to `native`. Stage and lost-reason writes need `admin:access:manage` (CR-10). `crm_settings` with `mode` and `access` (Q3). Groups resolve through the Projects grant model (D12) | 🟢 AGENT-SAFE | An admin sets a new org to Native, and the org gets its own 6 deal stages, 5 lead stages and 6 lost reasons. A member who is not an admin gets 403 on `POST /crm/statuses/deal`. With `access = admins`, a member gets 403 on every `/crm` route |

**For CRM-T4 (found in CRM-T1, 2026-10-11).** Intra-CRM FKs (`company_id`, `deal_id`, `status_id` and others) can point at another tenant's row, because referential checks bypass RLS. A composite `(organization_id, id)` key closes it.

### 13.4 Phase 2 — the one look (§8)

| Id | What | Gate | Done when |
|---|---|---|---|
| **CRM-U1** ✅ built 2026-10-11 | `AppTopBar` and the rail of views. Tabs leave | 🟢 AGENT-SAFE | `app-title-bar.spec.ts` covers `/crm`. `railRows.test.ts` lists the CRM rail. Visual review passes |
| **CRM-U2** | `Modal`, `ConfirmDialog` and the `ui/` controls replace the hand-rolled ones | 🟢 AGENT-SAFE | The 5 dialog files join `CONVERTED` in `conformance.test.ts`. The `PipelineSettings` rows in `SELECT_DEBT` and `CHECKBOX_DEBT` are deleted, not set to 0. `grep "fixed inset-0" src/app/crm` finds nothing |
| **CRM-U3** | Stage colour through `statusAccent`, categories through `categoricalAccent`, drops through `boardDrop.ts` | 🟢 AGENT-SAFE | `TONES` is gone. The `SCOPE` regex in `sharedTaskUi.test.ts:479` includes `app/crm/`, the comment at lines 467-477 is deleted, and the suite passes |
| **CRM-U4** | Reads through `useCachedResource`. Skeletons and `EmptyState` | 🟢 AGENT-SAFE | `dataCache.test.ts` patterns hold. No "Loading…" text remains |
| **CRM-U5** | The first-open choice (§8.3) | 🟢 AGENT-SAFE | An org with no mode sees the two cards. Native sets the mode and opens the pipeline |
| **CRM-U6** | Settings → CRM. `PipelineSettings` moves there | 🟢 AGENT-SAFE | Only an admin sees the page. Stages, lost reasons and the mode work from it |

### 13.5 Phase 3 — the Zoho mirror (§5)

| Id | What | Gate | Done when |
|---|---|---|---|
| **CRM-Z0** | Verify the four open Zoho facts (§5.2). Write the runbook to register the client | 🟢 AGENT-SAFE (docs). 🔴 registration is the owner's | §5.2 lists each fact with its source. The runbook is in `docs/` |
| **CRM-Z1** | `crm_sources/` with the protocol and the Zoho adapter: data centre, paging, rate limits, credits. No global credential | 🟢 AGENT-SAFE | `test_crm_sources_no_global_creds.py` passes. A 429 and a `TOO_MANY_REQUESTS` back off, measured with a fake server |
| **CRM-Z2** | `integration_connections` and `crm_settings.connection_id`. The `purpose` argument on the signing seam. Revoke in the protocol. Connect, callback, refresh, disconnect, reconnect. Behind `CRM_MIRROR` (OFF) | 🟢 AGENT-SAFE to build. 🔴 needs the owner's client | A tampered state is refused. A refresh failure sets `needs_reconnect`. The blob never appears in a log or response (fence) |
| **CRM-Z3** | The mirror columns, `crm_field_defs`, and pipelines if Zoho needs them | 🟢 AGENT-SAFE | R8: an upsert on `(organization_id, ext_source, ext_id)` is idempotent. Two orgs import one Zoho id with no collision |
| **CRM-Z4** | The first import, with progress, estimate and resume. The owner map | 🟢 AGENT-SAFE | A paused import resumes with no duplicate. Counts per entity match the source |
| **CRM-Z5** | The incremental loop per org, Sync now, the credit budget, `crm_sync_runs` | 🟢 AGENT-SAFE to build. 🔴 first real customer connect is the owner's | Two orgs sync on one loop with no crossover. A run stops at its budget |
| **CRM-Z6** | Read-only enforcement in Mirror mode, "Only in Metorite" items, deep links | 🟢 AGENT-SAFE | `test_crm_mirror_readonly.py`: each write route returns 409 on a mirrored field, for each of the 4 entities. A Metorite-only note saves |
| **CRM-Z7** | The mirror UI: connect flow, mirror marks, custom fields in the panel and the filters, sync health | 🟢 AGENT-SAFE | Visual review passes. A custom field filters a list |

### 13.6 Phase 4 — launch

| Id | What | Gate | Done when |
|---|---|---|---|
| **CRM-L1** | The shell manifest (§11): jobs in both lists, the search provider, the needs provider | 🟢 AGENT-SAFE | `test_shell_intent.py::TestOneJobList` passes. `nav.test.ts` passes with `team` and `blurb` set |
| **CRM-L2** | Promote `/crm` to `live` | 🔴 OWNER-GATE (H-21) | `launch_surface.md` §2, `LIVE_SET`, `APP_BAR_HOME` and the e2e `PAGES` agree. `CRM_ENABLED` turns on for every org |

### 13.7 After launch, in any order

The Native slices **CRM-S1 to S12** (§6). The insights **CRM-I1 to I6** (§9).
And four mirror slices:

| Id | What | Gate |
|---|---|---|
| **CRM-Z8** | Near-real-time: Zoho's Notification API starts a targeted pull | 🟢 AGENT-SAFE. A signed URL per org (§10.7) |
| **CRM-Z9** | Agent actions in Zoho through Zoho's MCP (§7.2), behind Metorite's confirmation step. Our `writer.py` stays as the fallback if Zoho's MCP fails the CRM-Z0 check | 🔴 OWNER-GATE to turn on for any org |
| **CRM-Z10** | "Make Metorite your CRM": Mirror to Native (§3.4) | 🟢 AGENT-SAFE to build. 🔴 Fracktal's run is the owner's |
| **CRM-Z11** | Retire the global Zoho path (§5.5) | 🟢 AGENT-SAFE, after CRM-Z5 is live |

The second provider (Q5) gets its own slice list, on the seam of §5.1.

---

## 14. Verification commands

Run these from the worktree root. Start the dev DB first, or the R8 suites skip
and the run still reads green.

```bash
bash scripts/dev_db.sh && eval "$(bash scripts/dev_db.sh --export)"

# Tenancy and the CRM backend
uv run pytest tests/unit/test_crm_kill_switch.py tests/unit/test_crm_tenancy_r8.py \
  tests/unit/test_crm_sync_tenancy.py tests/unit/test_tenancy_boundary.py \
  tests/unit/test_tenant_coverage.py tests/unit/test_org_purge_tenant.py
uv run pytest tests/unit/test_crm_routes.py tests/unit/test_crm_pipeline.py \
  tests/unit/test_crm_zoho_import.py tests/unit/test_crm_zoho_sync.py

# The mirror
uv run pytest tests/unit/test_crm_sources_no_global_creds.py tests/unit/test_crm_mirror_readonly.py

# The shell
uv run pytest tests/unit/test_shell_intent.py

# The UI
cd workbench/control_plane && npx tsc --noEmit && npx vitest run
npx playwright test e2e/app-title-bar.spec.ts
```

Then run the `visual-review` skill on `/crm` (§8.4).

---

## 15. Fences (R7)

| Rule | Fence | New or existing |
|---|---|---|
| An org outside `CRM_ORGS` cannot reach the CRM | `test_crm_kill_switch.py` | New (CRM-0) |
| Every `crm_*` table is tenant-scoped | `test_tenancy_boundary.py`, `test_tenant_coverage.py` | Existing, widened |
| Org B reads none of org A's CRM rows | `test_crm_tenancy_r8.py` | New (CRM-T4), R8 |
| The rename answers install, upgrade and replay | the `test_gtd_rename_upgrade.py` shape | New (CRM-T1) |
| A background CRM path binds its own tenant | `test_crm_sync_tenancy.py` | New (CRM-T3) |
| An adapter reads no global credential | `test_crm_sources_no_global_creds.py` | New (CRM-Z1) |
| Mirror mode refuses a write to a mirrored field | `test_crm_mirror_readonly.py` | New (CRM-Z6) |
| The CRM uses the shared stage colours | `sharedTaskUi.test.ts`, exemption deleted | Existing (CRM-U3) |
| The CRM adds no hand-rolled control | `conformance.test.ts` baselines | Existing (CRM-U2) |
| The CRM jobs agree on both sides | `test_shell_intent.py::TestOneJobList` | Existing (CRM-L1) |
| The live list agrees with the spec | `nav.test.ts` | Existing (CRM-L2) |

---

## 16. Proposed decisions and owner questions

### D95 — The CRM has two modes, and a mirror is a product (2026-10-11)

*Owner directive 2026-10-11. The agent proposed the details. The owner read the
plan and wrote: "if you are clear about the CRM implementation, then you can go
ahead and start implementing it". The owner added two requirements. AI agents
must run their inference over the Zoho data, and that data must sync into the
Metorite CRM. §3.5 meets both. So the owner took the recommendations below,
and D95 is in `work_plan.md` §3.*

- **D95.1 Two modes per org.** Native or Mirror (§3). This replaces
  `crm_app.md` §1's end state, "Zoho is retired", as the product's end state.
  A company can keep its CRM. For Fracktal, retirement is still an option, as
  CRM-Z10.
- **D95.2 The rename.** `crm_organizations` becomes `crm_companies` and the
  business column becomes `company_id`. `organization` then means the tenant
  only (§4.1).
- **D95.3 Mirror v1 reads only** (§3.2). This narrows D-CRM-7. The write path
  that D-CRM-7 built stays, and it is off for every org until CRM-Z9.
- **D95.4 One connector seam** (§5.1). A provider is an adapter, and the
  credential lives in `integration_connections` only (amended 2026-10-11: the store is general, so the Integrations app reads one registry).
- **D95.5 CRM follow-ups live in `pm_tasks`** (CRM-S1). This applies D52 and
  D53 to the CRM. It needs no new rule.

**Why D52 does not forbid this.** D52 retired ClickUp because Metorite is the
PM system of record. A CRM mirror is a different case. The customer keeps
their CRM, and Metorite reads it. D52.4 already allows a read-mostly mirror of
Zoho and Gmail. D95.3 stays inside that.

### Owner questions

| Id | Question | Answer (2026-10-11) |
|---|---|---|
| **Q1** | Mirror v1: read only, or two-way from the start? | **Read only** (D95.3). Write-back is CRM-Z9 |
| **Q2** | Accept the in-migration rename (§4.1) under R6, because the tables are empty and the app is `preview`? | **Yes** |
| **Q3** | Who sees the CRM in an org: every member, or the members an admin picks? | **The admin picks, and the default is admins only.** `crm_settings.access` holds `admins`, `everyone` or a list of group slugs. One router dependency beside `feature:crm` reads it (CRM-T5) |
| **Q4** | Fracktal: Native, or Mirror of its Zoho? | **Open.** Fracktal picks on its first open. The recommendation is Mirror first |
| **Q5** | Which CRM comes after Zoho? | **Open.** Not needed until the Zoho mirror is live |
| **Q6** | The default sync budget: 10 % of the customer's daily Zoho credits? | **Yes**, and an admin can change it |
| **Q7** | Is the mirror inside the ₹500 seat, with AI insights on credits? | **Yes**. No new SKU |

---

## 17. What this plan did not do

- **It did not run the HANDOFF checks.** 102 entries are open. This session
  read H-201 only, because CRM-T3 closes part of it.
- **It did not re-audit the WS-26i-bulk contract** in `crm_app.md` §9. CRM-S4
  must do that first.
- **It read one key in the box `.env`.** `CRM_ZOHO_SYNC=false`, and no Zoho
  refresh token is set. It printed no secret.
