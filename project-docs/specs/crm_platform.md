# CRM — the Metorite port, two modes, and the Zoho mirror

> **Board row:** WS-53 · **Created:** 2026-10-11 · **Owner directive:** 2026-10-11
> **Status:** ✅ **APPROVED 2026-10-11 — verified against code and production** at
> `origin/main` `7176fdf56` (#837). The owner approved the plan and told the
> agent to build it. D95 is decided (§16).

> **Built:** CRM-U1, the app bar and the rail of views (2026-10-11). CRM-U2, the
> dialogs on `Modal` and the `ui/` controls (2026-10-11). CRM-0, the
> kill switch, default OFF (2026-10-11). CRM-Z1, the `crm_sources` seam and the
> Zoho read adapter (2026-10-11). CRM-Z0, the Zoho facts (§5.2, §7.2) and the
> OAuth runbook `docs/ZOHO_OAUTH_SETUP.md` (2026-10-11). CRM-T1, the tenant port
> (2026-10-11). CRM-T3, narrowed (2026-10-11): the auto-lead step and the broker
> push handler bind their tenant. The Zoho sync stays off until CRM-Z5. CRM-T5,
> the access rule, the admin floor on stages and the seed when the mode becomes
> Native (migration 243, 2026-10-11). CRM-T4, the real-database tenancy suite and
> the CRM purge (2026-10-11).
> CRM-Z3, the mirror columns, `crm_field_defs` and `upsert_from_source`
> (2026-10-11, migration 244). CRM-Z2, the Zoho connection: migration 245, the
> connect routes behind `CRM_MIRROR`, and the shared token (2026-10-11). CRM-Z6,
> the mirror write guard (2026-10-11). CRM-Z4 and CRM-Z5, the Zoho sync engine:
> migration 246, `crm_sync_runs`, the import, the loop every 10 minutes, Sync
> now and the status route (2026-10-11). CRM-U5, the first-open choice, and
> CRM-Z7, narrowed (2026-10-11): the Zoho connect flow, the sync progress, the
> title bar mode and the mirror marks. CRM-L2, the promotion of `/crm` to live,
> with the per-org list `CRM_MIRROR_ORGS` (2026-10-11, owner decision in chat).
> CRM-Q1 and CRM-Q2, discovery, the source profile and the deal pipelines
> (2026-10-12, migration 247). The other slices are not built yet.
>
> **Built later:** CRM-Q3, the new layout of §18.4 (2026-10-12). CRM-Q4, field definitions in the UI (2026-10-12). CRM-Q9, the Sync health card (2026-10-12).
>
> **Built later:** CRM-Q5, the data the mirror loses (2026-10-12). Migration 249 adds `crm_deal_contacts.ext_source`.

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
| Native or Mirror | no mode | Yes, with 0 records and no live connection | Change CRM setup (CRM-U5b) |

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
   exists, else with the only org. The seed rows on prod already belong to
   `default`, and they stay there. An org gets its own seed when its mode
   becomes `native` (CRM-T5). A Mirror org gets its stages from the source.
3. Set `NOT NULL` on each table with no NULL row. *(Changed in the build,
   2026-10-11.)* With two or more orgs and no `fracktalworks`, the migration
   does not stop. That table keeps a nullable column, FORCE RLS hides its
   orphan rows, and a NOTICE names the table. Prod never reaches this, and a
   fresh install has one org. Only a shared dev database does. A stop there
   would break `scripts/dev_db.sh`. The migration looks up no `default`
   slug, because the D43-A ratchet refuses a new one.
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

CRM-Z0 read each fact in Zoho's own documentation on 2026-10-11. A row that
says **measured** comes from a live probe with `curl` on the same day, with no
credential. A row that says **not confirmed** has no Zoho source yet, and it
names the slice that measures it.

- **One OAuth client serves every data centre** when it has multi-DC on. The
  consent request goes to `accounts.zoho.com`. The redirect returns `location`
  and `accounts-server`. The token request goes to that server, and the token
  response returns `api_domain`. The data centres are US, EU, IN, AU, JP, CA,
  SA and UK. ⚠️ **CN is not one of them.** The first draft named CN, and that
  was wrong (row 9).
- **The API budget belongs to the customer.** Zoho counts credits in a
  rolling 24-hour window. The cap depends on the edition and the licence
  count. For example, Professional gets 50,000 plus 500 for each licence, up
  to 3,000,000. The first draft said 10,000 and 500,000, and that was wrong
  (row 6). A normal call costs 1 credit. Past the cap Zoho answers HTTP 429.

**The facts, CRM-Z0 (2026-10-11).**

| # | Fact | Answer | Source (URL, read 2026-10-11) |
|---|---|---|---|
| 1 | The read-only scopes | The form is `ZohoCRM.<scope>.<operation>`, with a comma between scopes. **Records:** `ZohoCRM.modules.leads.READ`, `ZohoCRM.modules.contacts.READ`, `ZohoCRM.modules.accounts.READ`, `ZohoCRM.modules.deals.READ`, `ZohoCRM.modules.notes.READ`, `ZohoCRM.modules.calls.READ` and `ZohoCRM.modules.events.READ`. `ZohoCRM.modules.tasks.READ`, which CRM-Q5 now asks for. **The deleted list has no scope of its own.** The READ scope of the module covers it. **Metadata:** `ZohoCRM.settings.fields.READ`, `ZohoCRM.settings.layouts.READ`, `ZohoCRM.settings.pipeline.READ` and `ZohoCRM.settings.modules.READ`. **Users:** `ZohoCRM.users.READ`. **Org:** `ZohoCRM.org.READ`. **Bulk Read** adds `ZohoCRM.bulk.READ`. The docs write a module name in lower case, and Zoho's own MCP server writes it with a capital. **Not confirmed:** whether the case matters | [scopes](https://www.zoho.com/crm/developer/docs/api/v8/scopes.html) · [records](https://www.zoho.com/crm/developer/docs/api/v8/get-records.html) · [deleted](https://www.zoho.com/crm/developer/docs/api/v8/get-deleted-records.html) · [fields](https://www.zoho.com/crm/developer/docs/api/v8/field-meta.html) · [layouts](https://www.zoho.com/crm/developer/docs/api/v8/layouts-meta.html) · [pipelines](https://www.zoho.com/crm/developer/docs/api/v8/get-pipelines.html) · [modules](https://www.zoho.com/crm/developer/docs/api/v8/modules-api.html) · [users](https://www.zoho.com/crm/developer/docs/api/v8/get-users.html) · [org](https://www.zoho.com/crm/developer/docs/api/v8/get-org-data.html) · [bulk](https://www.zoho.com/crm/developer/docs/api/v8/bulk-read/create-job.html) |
| 2 | Paging, and the 2000-row cap | **v2:** the docs show no `page_token`. `info` holds `per_page`, `count`, `page` and `more_records`, and no `next_page_token`. The v2 docs state no 2000-row cap. **v3 and later, v8 too:** `page` reaches the first 2000 rows only. Past them a call must send `page_token`, which comes from `info.next_page_token`. `info` also gives `page_token_expiry`. A third-party report names the error `DISCRETE_PAGINATION_LIMIT_EXCEEDED`, and no Zoho page does. **The keyset read of CRM-Z1 asks for page 1 of a new window each time.** So the cap reaches only the offset fallback and a tie of more than 2000 rows | [v2 records](https://www.zoho.com/crm/developer/docs/api/v2/get-records.html) · [v3 records](https://www.zoho.com/crm/developer/docs/api/v3/get-records.html) · [v8 records](https://www.zoho.com/crm/developer/docs/api/v8/get-records.html) |
| 3 | `If-Modified-Since` and `sort_by` | The header returns "recently modified records", so it filters on the modified time. The docs show ISO 8601 with seconds and an offset, `2019-07-25T15:26:49+05:30`, on v2 and v8. So the step is one second. The adapter sends RFC 1123, and a live v2 call honoured it on 2026-08-06 with a 304 (`crm_app.md` §7.1). A 304 means "not modified since the time in the header". **`sort_by`:** v2 takes any field API name. v3 and v8 take only `id`, `Created_Time` and `Modified_Time`, and the default is `id`. **v3 and v8 make `fields` mandatory** for a list read, with 50 field API names at most | [v2 records](https://www.zoho.com/crm/developer/docs/api/v2/get-records.html) · [v8 records](https://www.zoho.com/crm/developer/docs/api/v8/get-records.html) · [status codes](https://www.zoho.com/crm/developer/docs/api/v8/status-codes.html) |
| 4 | The deleted list | `GET /crm/{version}/{module}/deleted`, the same path on v2 and v8. `type` is `all` (default), `recycle` or `permanent`. It pages by `page` and `per_page` (200 at most). The docs name no `page_token` and no sort. **Not confirmed:** the order of the rows. A row holds `id`, `display_name`, `type`, `deleted_by`, `created_by` and `deleted_time`. **Zoho keeps a recycle-bin row for 60 days and a permanent delete for 120 days.** One call costs 2 credits | [v8 deleted](https://www.zoho.com/crm/developer/docs/api/v8/get-deleted-records.html) · [v2 deleted](https://www.zoho.com/crm/developer/docs/api/v2/get-deleted-records.html) · [limits](https://www.zoho.com/crm/developer/docs/api/v8/api-limits.html) |
| 5 | Bulk Read | `POST /crm/bulk/{version}/read`. Scopes `ZohoCRM.bulk.READ` and the READ scope of the module. One job exports 200,000 rows at most. The next batch comes through `page`, or `page_token` from `next_page_token`. A page token lives 24 hours at most. The file stays for one day. It reads every module except Notes, Attachments and Emails. A query takes 25 criteria at most. The answer is not immediate: a callback or a poll says when the file is ready. To start a job costs 50 credits. **Not confirmed:** a limit on jobs at one time, and the cost of the status and download calls | [bulk limits](https://www.zoho.com/crm/developer/docs/api/v8/bulk-read/limitations.html) · [create job](https://www.zoho.com/crm/developer/docs/api/v8/bulk-read/create-job.html) · [limits](https://www.zoho.com/crm/developer/docs/api/v8/api-limits.html) |
| 6 | Credits and concurrency | **Credits per edition, rolling 24 hours:** Free 5,000. Standard 50,000 + 250 per licence, up to 100,000. Professional 50,000 + 500 per licence, up to 3,000,000. Enterprise 50,000 + 1,000 per licence, up to 5,000,000. Ultimate 50,000 + 2,000 per licence, with no cap. **Cost:** a page of records 1, or 3 with `cvid`. The deleted list 2. Users, field and module metadata 1. A COQL query 1 to 3. A Bulk Read job 50. **Not confirmed:** whether a refused call (429) costs a credit. **Concurrency for one org and app:** Free 5, Standard 10, Professional 15, Enterprise 20, Ultimate 25. **A sub-limit of 10** covers a record read with `cvid` or `sort_by`, COQL and some writes. Past it Zoho answers `TOO_MANY_REQUESTS`. HTTP 429 means that the org spent its daily credits, or that a call passed the concurrency limit. **`X-API-CREDITS-REMAINING`** comes in the response only when half or more of the daily credits are spent | [v8 limits](https://www.zoho.com/crm/developer/docs/api/v8/api-limits.html) · [v2 limits](https://www.zoho.com/crm/developer/docs/api/v2/api-limits.html) · [status codes](https://www.zoho.com/crm/developer/docs/api/v8/status-codes.html) |
| 7 | The token endpoint | `POST {accounts-server}/oauth/v2/token`. An error comes as `{"error":"<code>"}`. **Measured:** a client id that does not exist gets HTTP 200 with `{"error":"invalid_client"}`, for the code grant and for the refresh grant. The codes are `invalid_client` (also a wrong secret for that data centre), `invalid_client_secret`, `invalid_redirect_uri` and `invalid_code`. `invalid_code` covers a code that expired or was used, and a revoked refresh token. Zoho does not document `invalid_grant`. **The throttle:** `{"error_description":"You have made too many requests continuously. Please try again after some time.","error":"Access Denied"}`, and Kaizen #43 says the status stays 200. **Limits:** 10 token requests in 10 minutes. 10 live access tokens for each refresh token, and the 11th removes the oldest. 20 live refresh tokens for each user and client, and the 21st removes the oldest. An access token lives 3600 seconds. A refresh token lives until someone revokes it. A code lives 2 minutes and works once | [token](https://www.zoho.com/accounts/protocol/oauth/web-apps/access-token.html) · [CRM tokens](https://www.zoho.com/crm/developer/docs/api/v8/access-refresh.html) · [refresh](https://www.zoho.com/crm/developer/docs/api/v8/refresh.html) · [token limits](https://www.zoho.com/accounts/protocol/oauth/token-limits.html) · [Kaizen #43](https://help.zoho.com/portal/en/community/topic/kaizen-43-tokens-and-limitations) |
| 8 | The deep link to a record | **Not confirmed by a Zoho page.** A community post gives `https://crm.zoho.<tld>/crm/<org>/tab/<Tab>/<record id>`, with `Potentials` as the tab of Deals. `<org>` is most likely `domain_name` from `GET /crm/{version}/org`, which looks like `org808232144` and needs `ZohoCRM.org.READ`. **Measured:** the web host answers at `crm.zoho.com`, `.eu`, `.in`, `.com.au`, `.jp`, `.sa`, `.uk` and at `crm.zohocloud.ca`. `crm.zoho.ca` does not resolve. So the web host is the accounts host with `accounts.` changed to `crm.`. CRM-Z6 measures the full link on Fracktal's tenant | [record link post](https://help.zoho.com/portal/en/community/topic/sending-a-link-to-a-record-in-an-email-template) · [org](https://www.zoho.com/crm/developer/docs/api/v8/get-org-data.html) |
| 9 | The data centres | `location` → accounts server → API host: `us` `accounts.zoho.com` `www.zohoapis.com`. `eu` `accounts.zoho.eu` `www.zohoapis.eu`. `in` `accounts.zoho.in` `www.zohoapis.in`. `au` `accounts.zoho.com.au` `www.zohoapis.com.au`. `jp` `accounts.zoho.jp` `www.zohoapis.jp`. `ca` `accounts.zohocloud.ca` `www.zohoapis.ca`. `sa` `accounts.zoho.sa` `www.zohoapis.sa`. `uk` `accounts.zoho.uk` `www.zohoapis.uk`. The token response gives the API host. **Measured:** `www.zohoapis.ca`, `.sa` and `.uk` answer. **CN is not on the multi-DC list.** The CRM page says a CN consent starts at `accounts.zoho.com.cn`, so one client on `accounts.zoho.com` cannot reach it. **Measured:** the live `oauth/serverinfo` also names `ae`, `sg` and `inec`, which no doc names. **By default each data centre gets its own client secret.** An option in the console gives one secret to all | [multi-DC](https://www.zoho.com/accounts/protocol/oauth/multi-dc.html) · [multi-DC (developer)](https://www.zoho.com/developer/oauth/multi-dc-support.html) · [CRM multi-DC](https://www.zoho.com/crm/developer/docs/api/v8/multi-dc.html) · [serverinfo](https://accounts.zoho.com/oauth/serverinfo) |
| 10 | Revoke for Disconnect | `POST {accounts-server}/oauth/v2/token/revoke?token=<refresh token>`. The revoke touches one org only. **Not confirmed:** the success body, and whether the access tokens of that refresh token die with it. **Measured:** a fake token gives HTTP 400 with an HTML page, not JSON | [CRM revoke](https://www.zoho.com/crm/developer/docs/api/v8/revoke-tokens.html) · [Analytics revoke](https://www.zoho.com/analytics/api/v2/authentication/revoke-token.html) |
| 11 | Stages and pipelines | **A stage is a value of the one Deals Stage picklist.** The standard pipeline holds every value of that picklist. Pipelines belong to a layout, and one layout can hold many pipelines. One stage can sit in many pipelines, and it keeps one probability in all of them. The v8 sample shows one stage `id` in two pipelines. So one stage name is one stage across the pipelines. **Not confirmed:** that Zoho refuses two picklist values with one name. The API is `GET settings/pipeline?layout_id=`, and `layout_id` is mandatory. The key is `pipeline`, and each stage in `maps` has `display_value`, `actual_value`, `id`, `sequence_number` and `forecast_category` | [pipelines API](https://www.zoho.com/crm/developer/docs/api/v8/get-pipelines.html) · [multiple pipelines](https://help.zoho.com/portal/en/kb/crm/customize-crm-account/pipelines/articles/multiple-sales-pipeline) · [overview](https://www.zoho.com/crm/tutorials/multiple-sales-pipeline/overview.html) |
| 12 | Zoho's CRM MCP servers | §7.2 holds the answer. In short: one click works, with no step in the customer's Zoho console | §7.2 |
| 13 | Register the client | `api-console.zoho.com` → **Server-based Applications** → Client Name, Homepage URL and Authorized Redirect URIs → Client ID and Client Secret. The redirect URI must match the one in the console. Multi-DC is a set of switches on the Settings tab. **The console sets no scopes.** The consent request asks for them. **Not confirmed:** any Zoho review before users of other organizations can consent. No Zoho page names one | [register](https://www.zoho.com/crm/developer/docs/api/v8/register-client.html) · [authorization](https://www.zoho.com/accounts/protocol/oauth/web-apps/authorization.html) · [multi-DC](https://www.zoho.com/accounts/protocol/oauth/multi-dc.html) |
| 14 | A Notification API channel | `channel_expiry` is one week at most. With no value, or with more than a week, Zoho closes the channel after one hour. So CRM-Z8 must renew each channel within a week | [notifications](https://www.zoho.com/crm/developer/docs/api/v8/notifications/enable.html) |

**What changes in the adapter.** These are the places where a fact above does
not match the merged CRM-Z1 code. Each item names the slice that owns it.

1. **Remove `cn` from `ZOHO_DATA_CENTRES`** in `crm_sources/zoho/auth.py`, and
   change its comment from nine centres to eight. A CN org cannot consent
   through `accounts.zoho.com`. Do not add `ae`, `sg` or `inec` until a Zoho
   page names them. Until then a consent from them fails closed with
   `UntrustedHost`. Slice: CRM-Z2.
2. **Ask for one client secret for all data centres.** `OAuthClientConfig`
   holds one secret. The owner sets "Use the same OAuth credentials for all
   data centers" (`docs/ZOHO_OAUTH_SETUP.md`). With a secret for each centre,
   every centre but one answers `invalid_client`. No code changes.
3. **Correct a comment in `auth.py`.** `_is_token_throttle` says the throttle
   comes "often with HTTP 400". Kaizen #43 says 200. The code reads the body
   and not the status, so it is correct.
4. **Add `revoke` to `auth.py`** (CRM-Z2 already plans it). It posts to
   `{accounts_server}/oauth/v2/token/revoke`. Zoho documents the token in the
   query string, and httpx logs the URL. So try the form body first, and
   measure it. If Zoho needs the query, keep the URL out of every log line.
5. **Count 2 credits for `list_deleted`** in `crm_sources/zoho/client.py`.
   `_spend_credit` counts 1 for every call. Keep 1 credit for a refused call,
   because Zoho does not say, and the high count is the safe one. Slice: CRM-Z5.
6. **Build `credits_remaining`** in `client.py` from the
   `X-API-CREDITS-REMAINING` header of the last response. Zoho sends it only
   after half of the daily credits are spent. So the protocol in
   `crm_sources/base.py` changes to `int | None`. `None` means "Zoho has not
   reported it, so more than half is left". Slice: CRM-Z5.
7. **Build `deep_link`** in `client.py` as
   `https://crm.<accounts host without "accounts.">/crm/<domain_name>/tab/<tab>/<id>`.
   It needs an `org()` read of `GET /crm/{version}/org` (scope
   `ZohoCRM.org.READ`) at connect time, and `domain_name` in `provider_meta`.
   The tab of each module comes from `GET settings/modules` (scope
   `ZohoCRM.settings.modules.READ`). CRM-Z6 measures the full link on Fracktal
   before it ships.
8. **Delete "CRM-Z0 confirms the exact name"** from the `FIELDS_SCOPE` comment.
   The three settings scopes in `client.py` are correct as written.
9. **Keep `RECORDS_API_VERSION = "v2"` until CRM-Z3.** Then move the record
   reads to v8. Version 2 still serves. No Zoho CRM page that CRM-Z0 found
   gives it an end date. Five things change with v8:
   - `fields` becomes mandatory, with 50 API names at most. CRM-Z3 builds the
     list from `list_field_defs`. A module with more than 50 fields needs a
     second read for each page, or a smaller set of custom fields.
   - `sort_by` takes `Modified_Time`, so the keyset read stays as it is.
   - The offset fallback must send `page_token` after page 10. A stored
     cursor with a token past `page_token_expiry` starts its window again.
   - `_with_modified_since` sends RFC 1123. The docs show ISO 8601. Measure the
     RFC 1123 form on v8 with the `crm_app.md` §7.1 `curl` before the move. If
     v8 ignores it, change to ISO 8601 in that one helper.
   - `list_users` and `list_deleted` keep their shape on v8.
10. **Keep a reconcile for deletes in CRM-Z5.** Zoho keeps a recycle-bin row
    for 60 days. A connection that stops for longer loses deletes, and only a
    full id compare finds them.
11. **Share one access token across workers in CRM-Z2.** Zoho allows 10 token
    requests in 10 minutes. The single-flight lock of `ZohoSource` covers one
    instance only. So CRM-Z2 stores each refreshed token in
    `integration_connections`, and every worker reads it from there.
12. **Revoke the old refresh token on a reconnect in CRM-Z2.** Each consent
    makes a new one, and Zoho keeps 20 for each user and client. The 21st
    removes the oldest. That can kill a live connection of the same Zoho user
    in another Metorite org.
13. **Hold the concurrency low in CRM-Z5.** A record read with `sort_by` falls
    under the sub-limit of 10 for the org, and other apps of the customer use
    it too. One read at a time for each org is the safe default. The backoff
    of CRM-Z1 already handles `TOO_MANY_REQUESTS`.
14. **Add `"task": "Tasks"` to `ZOHO_MODULES`** only if a slice decides to mirror
    Zoho Tasks. CRM-S1 keeps follow-ups in `pm_tasks`, so the minimal scope
    set leaves Tasks out.
15. **Use Bulk Read for a first import of a large module in CRM-Z4.** One job
    costs 50 credits, and 50 record pages read 10,000 rows. So a module with
    more than 10,000 rows is cheaper in Bulk Read. Notes are not in Bulk Read
    and stay on the record read.

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
| Incremental | Every 10 minutes by default, per org. `If-Modified-Since` on a cursor per entity, plus the deleted list. A tenant that hides `Modified_Time` (Fracktal's) gets offset paging, so CRM-Z5 needs a periodic reconcile for it. The deleted list pages by offset too, and needs the same reconcile |
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
  in the customer's Zoho console. CRM-Z0 checks it, and the answer follows.

**What CRM-Z0 found (2026-10-11).**

- **What they are.** Zoho hosts four ready-made MCP servers for Zoho CRM. Data
  Insights reads and is read-only. Data Operations creates, reads, updates and
  deletes records, in bulk too, and converts leads. Module Customization
  changes modules, fields and layouts. Workflow and Process Automation changes
  workflow rules. Sources:
  [overview](https://www.zoho.com/crm/developer/docs/mcp/overview.html),
  [product page](https://www.zoho.com/crm/developer/mcp.html),
  [announcement](https://help.zoho.com/portal/en/community/topic/zoho-crm-with-built-in-mcp-support).
- **How a client connects.** Each server has one fixed remote URL that is the
  same for every customer, for example
  `https://zoho-crm-data-operations-60065097786.zohomcp.in/mcp/<key>/message`.
  Zoho's Claude Desktop guide adds that URL as a custom connector and then
  clicks Connect. It names no step in a Zoho console
  ([Claude setup](https://www.zoho.com/crm/developer/docs/mcp/setup/claude.html)).
- **Measured: the servers follow the MCP authorization spec.** A call with no
  token gets HTTP 401 and a `WWW-Authenticate` header that names the resource
  metadata. The metadata names an authorization server with a
  `registration_endpoint` (dynamic client registration), PKCE `S256`, the
  grants `authorization_code` and `refresh_token`, and a `revocation_endpoint`.
  So an MCP client registers itself. **The customer creates nothing in Zoho,
  and Metorite registers nothing by hand.** The one-click check passes.
- **The OAuth is per member.** The first tool call opens a Zoho sign-in.
  Zoho's overview says that each action stays inside the permissions of the
  user, and that a user can do only what the CRM role allows. So Zoho applies
  the member's own role.
- **The scopes are wide.** The metadata of Data Operations lists READ,
  CREATE, UPDATE and DELETE on every module, plus `ZohoMCP.tool.execute`.
  Metorite must keep its own list of the tools an agent may call, and leave
  every delete tool out of v1.
- **The cost.** "API calls executed through MCP consume API credits in the
  same way as standard API calls." So an agent action spends the same budget
  as the mirror. Zoho states no other price.
- **Not confirmed.** (a) The tool names. The server lists them only after
  sign-in. (b) The reach outside India. The URLs and the authorization server
  are on `zohomcp.in` and `mcp.zoho.in`, and Zoho says it rolls the feature
  out "in phases" to every data centre. Fracktal is on the IN data centre. (c)
  Whether a server-side client may hold the refresh token for a member. The
  metadata allows it, and no Zoho page says so.
- **There is a second product, and CRM-Z9 must not use it.** "Zoho MCP" at
  `mcp.zoho.com` makes a custom server in a console, and a Super Admin
  creates it. That is a step in the customer's console, and it breaks one
  click ([Zoho MCP setup](https://www.zoho.com/mail/help/mcp/mcp-server-configuration.html)).

**The decision for CRM-Z9: use Zoho's ready-made Data Operations server.** It
passes the one-click check, and Zoho applies the member's role. Each member
connects it once, as a `member` row in `integration_connections` (D95.4).
Metorite's confirmation step wraps each call. Metorite's list of allowed tools
refuses every delete.

**Fall back to our `writer.py`** only if the dispatch check of CRM-Z9 fails.
Three failures count:

1. An org outside the IN data centre cannot sign in.
2. The tool list has no write that v1 needs.
3. The agent runtime of WS-54 cannot do the MCP authorization flow.

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
| `launch` | `live` since 2026-10-11 (CRM-L2) |
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
| Register Metorite's Zoho OAuth client, with multi-DC on and the redirect URL | A third-party account | CRM-Z0 runbook `docs/ZOHO_OAUTH_SETUP.md`, then the owner (H-297) |
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
| **CRM-T3** 🔨 built 2026-10-11, narrowed (not merged) | The three background paths bind their tenant (§4.3). The auto-lead step and the broker push handler bind it now. The sync loop fails closed: it starts no task, and `POST /crm/sync/zoho` answers 409. CRM-Z5 builds the sync binding and the advisory lock | 🟢 AGENT-SAFE. ⚠️ Touches the sync loop, which `work_plan.md` §6 WS-26 (a) gates while it runs. On prod it does not run (0 cursors) | `test_crm_sync_tenancy.py`: an auto-lead run for an org-B mailbox writes an org-B lead, and org A reads none of it. The broker handler bound to org A refuses an org-B row with no Zoho call. `test_db_engine_seam.py` exempts only `sync_zoho.py`, which no path reaches until CRM-Z5. H-201's CRM bullets point at CRM-Z5 |
| **CRM-T4** ✅ built 2026-10-11 | R8: a real-database suite for the CRM. Offboarding purges CRM rows | 🟢 AGENT-SAFE | `test_crm_tenancy_r8.py` on the dev DB: org B reads 0 of org A's rows on every list, `WITH CHECK` refuses a cross-org insert, an unbound session reads 0 rows. `test_org_purge_tenant.py` covers the CRM. Each case red first |
| **CRM-T5** ✅ built 2026-10-11 | Per-org seeds of stages and lost reasons, in the write that sets the mode to `native`. Stage and lost-reason writes need `admin:access:manage` (CR-10). `crm_settings` with `mode` and `access` (Q3). Groups resolve through the Projects grant model (D12) | 🟢 AGENT-SAFE | An admin sets a new org to Native, and the org gets its own 6 deal stages, 5 lead stages and 6 lost reasons. A member who is not an admin gets 403 on `POST /crm/statuses/deal`. With `access = admins`, a member gets 403 on every `/crm` route |

**For CRM-T4 (found in CRM-T1, 2026-10-11).** Intra-CRM FKs (`company_id`, `deal_id`, `status_id` and others) can point at another tenant's row, because referential checks bypass RLS. A composite `(organization_id, id)` key closes it. CRM-T4 measured the gap: `POST /crm/contacts` accepts another org's `company_id`. `test_crm_tenancy_r8.py::TestTheForeignKeyGap` is a strict xfail that fences it. The key itself is still open work.

**CRM-T4 result (2026-10-11).** The RESTRICT hazard on `status_id` did not reproduce with the bare cascade on Postgres 16, because the check runs after the cascade removes the records. `purge_tenant_organization` still deletes `crm_activities`, `crm_deals` and `crm_leads` for its own org first, so it does not rest on that order. Each delete names the org, because a role that bypasses RLS would delete every org's rows. The receipt `kept` list never named the CRM.

### 13.4 Phase 2 — the one look (§8)

| Id | What | Gate | Done when |
|---|---|---|---|
| **CRM-U1** ✅ built 2026-10-11 | `AppTopBar` and the rail of views. Tabs leave | 🟢 AGENT-SAFE | `app-title-bar.spec.ts` covers `/crm`. `railRows.test.ts` lists the CRM rail. Visual review passes |
| **CRM-U2** ✅ built 2026-10-11 | `Modal`, `ConfirmDialog` and the `ui/` controls replace the hand-rolled ones | 🟢 AGENT-SAFE | The 5 dialog files join `CONVERTED` in `conformance.test.ts`. The `PipelineSettings` rows in `SELECT_DEBT` and `CHECKBOX_DEBT` are deleted, not set to 0. `grep "fixed inset-0" src/app/crm` finds nothing |
| **CRM-U3** | Stage colour through `statusAccent`, categories through `categoricalAccent`, drops through `boardDrop.ts` | 🟢 AGENT-SAFE | `TONES` is gone. The `SCOPE` regex in `sharedTaskUi.test.ts:479` includes `app/crm/`, the comment at lines 467-477 is deleted, and the suite passes |
| **CRM-U4** | Reads through `useCachedResource`. Skeletons and `EmptyState` | 🟢 AGENT-SAFE | `dataCache.test.ts` patterns hold. No "Loading…" text remains |
| **CRM-U5** ✅ built 2026-10-11 | The first-open choice (§8.3). Built as: an admin sees two cards, and a member sees "An admin must set up the CRM first." With `CRM_MIRROR` off, only the Native card shows. `GET /crm/connection` now carries `mode`, because `GET /crm/settings` is for admins only | 🟢 AGENT-SAFE | An org with no mode sees the two cards. Native sets the mode and opens the pipeline |
| **CRM-U5b** ✅ built 2026-10-11 | Change CRM setup (§3.4). `PATCH /crm/settings {"mode": null}` returns the org to no mode. It needs 0 rows in the five record tables and no Zoho connection that is `connected`, else 409 `crm_has_records` or `crm_connected`. The same transaction clears `mode` and `connection_id` and deletes the stages and lost reasons. `access` stays. An admin sees a "CRM setup" card in Settings with a "Change CRM setup" button and a confirm | 🟢 AGENT-SAFE | `test_crm_u5b_r8.py` on a real database. Reset removes the 17 rows, and Native again seeds them. A lead or a connected Zoho gives 409 |
| **CRM-U6** | Settings → CRM. `PipelineSettings` moves there | 🟢 AGENT-SAFE | Only an admin sees the page. Stages, lost reasons and the mode work from it |

### 13.5 Phase 3 — the Zoho mirror (§5)

| Id | What | Gate | Done when |
|---|---|---|---|
| **CRM-Z0** ✅ docs built 2026-10-11 (the owner's registration is still open, H-297) | Verify the four open Zoho facts (§5.2). Write the runbook to register the client | 🟢 AGENT-SAFE (docs). 🔴 registration is the owner's | §5.2 lists each fact with its source. The runbook is in `docs/` |
| **CRM-Z1** ✅ built 2026-10-11 | `crm_sources/` with the protocol and the Zoho adapter: data centre, paging, rate limits, credits. No global credential | 🟢 AGENT-SAFE | `test_crm_sources_no_global_creds.py` passes. A 429 and a `TOO_MANY_REQUESTS` back off, measured with a fake server |
| **CRM-Z2** ✅ built 2026-10-11 | `integration_connections` and `crm_settings.connection_id`. The `purpose` argument on the signing seam. Revoke in the protocol. Connect, callback, refresh, disconnect, reconnect. Behind `CRM_MIRROR` (OFF). Built as: the callback route is `/crm/oauth/zoho/callback`, so the `/api/crm` proxy reaches it at the URI in the owner runbook. A dedicated BFF route for that redirect is still open (CRM-Z7). The fence on the blob names the table `integration_connections`, because the email and WhatsApp tables have a column with the same name. The review fixes: a blob that the key cannot open blocks neither a reconnect nor a disconnect, and the shared token marks it `needs_reconnect` with the code `undecryptable`. The callback revokes the old token only after the save commits. `PATCH /crm/settings` writes only if the mode is still the one it read, else it answers 409. ⚠️ **Before `CRM_MIRROR` goes on:** measure the revoke on a live Zoho. The form body is not measured, and a revoke that Zoho ignores fails with no sign (§5.2 item 4). The access log also records the consent `code` of the callback, so mask it | 🟢 AGENT-SAFE to build. 🔴 needs the owner's client | A tampered state is refused. A refresh failure sets `needs_reconnect`. The blob never appears in a log or response (fence) |
| **CRM-Z3** ✅ built 2026-10-11. `crm_pipelines` did not land: a Zoho pipeline is a subset of the one Stage picklist, so CRM-S7 adds it. The write guard moved to CRM-Z6 | The mirror columns, `crm_field_defs`, and pipelines if Zoho needs them | 🟢 AGENT-SAFE | R8: an upsert on `(organization_id, ext_source, ext_id)` is idempotent. Two orgs import one Zoho id with no collision |
| **CRM-Z4** ✅ built 2026-10-11. Built as: the first import skips the deleted list, because nothing is mirrored yet. It opens the deleted window at the start of the import instead. The estimate uses `actions/count` (v8), which is not measured on a live tenant. Bulk Read (§5.2 item 15) is not built. The import mirrors the Stage and Lead_Status picklists into the status tables first | The first import, with progress, estimate and resume. The owner map | 🟢 AGENT-SAFE | A paused import resumes with no duplicate. Counts per entity match the source |
| **CRM-Z5** ✅ built 2026-10-11. Built as: the interval (10 minutes) and the credit budget are constants, because `crm_settings` has no column for them. The budget assumes the Free edition (5,000 credits a day), so a sync may spend 500 in 24 hours. A run that spends credits spaces the next run, up to 6 hours. A module that hides `Modified_Time` is not listed by an incremental run. Only the reconcile reads it, once in 24 hours, and it resumes across runs. A reconcile deletes only a row that two complete walks in a row did not read (two strikes). It refuses to delete, with a `refused` count, when the Zoho count is unknown (`count_unknown`), when its walk read fewer records than that count, when it read nothing, or when it would delete more than 20 % of more than 10 local rows. A record that fails to write is read again for 5 runs, one page for each run, and then it expires. The status route counts it in `failed`. The old two-way engine in `sync_zoho.py` stays as unreachable code until CRM-Z11. A reconcile also runs after 50 days without a good run | The incremental loop per org, Sync now, the credit budget, `crm_sync_runs`. From CRM-T3: one `tenant_session(org)` per step, the advisory lock in place of `_cycle_lock`, and a working `POST /crm/sync/zoho` | 🟢 AGENT-SAFE to build. 🔴 first real customer connect is the owner's | Two orgs sync on one loop with no crossover. A run stops at its budget |
| **CRM-Z6** ✅ built 2026-10-11. Built as: deep links move to CRM-Z7 after the sync stores the org domain | Read-only enforcement in Mirror mode, "Only in Metorite" items, deep links | 🟢 AGENT-SAFE | `test_crm_mirror_readonly.py`: each write route returns 409 on a mirrored field, for each of the 4 entities. A Metorite-only note saves |
| **CRM-Z7** ✅ built 2026-10-11 (narrowed) | The mirror UI: connect flow, mirror marks, custom fields in the panel and the filters, sync health. Built as: the BFF route `api/crm/oauth/zoho/callback` passes the 302 on with a relative Location. The title bar names the mode. A banner asks for a new consent, and a bar shows the import. The Settings view holds the connection panel, with Sync now and Disconnect. A mirrored record shows locked fields, and its deal cannot be dragged. Notes and calls stay open and carry an "Only in Metorite" badge. A 409 from the write guard shows as a toast. Left: the "Open in Zoho" link, the custom-field filters and the field map | 🟢 AGENT-SAFE | Visual review passes. A custom field filters a list |

### 13.6 Phase 4 — launch

| Id | What | Gate | Done when |
|---|---|---|---|
| **CRM-L1** | The shell manifest (§11): jobs in both lists, the search provider, the needs provider | 🟢 AGENT-SAFE | `test_shell_intent.py::TestOneJobList` passes. `nav.test.ts` passes with `team` and `blurb` set |
| **CRM-L2** ✅ built 2026-10-11 (owner decision in chat: "Promote CRM to live now"). Built as: `/crm` is `live` in `nav.ts`, `launch_surface.md` §2, `LIVE_SET`, `APP_BAR_HOME` and the e2e `PAGES`. `CRM_ENABLED` turns on for each org, and the mirror stays Fracktal-only through the new list `CRM_MIRROR_ORGS`. It has the grammar of `CRM_ORGS`, and an empty list allows no org. `require_crm_mirror`, the sync loop and `engine.acquire` use `crm_mirror_allowed(org)`, so an org outside the list gets 404 on `/crm/connection*`, the callback and `/crm/sync/*`. A member without access sees "The CRM is not open to you yet". An admin sees the new Access panel in CRM settings. CRM-L1 is built in part: only the job `new-lead` (both lists) exists. The jobs `new-deal`, `log-call` and `find-record`, the search provider and the needs provider are not built | 🔴 OWNER-GATE (H-21) | `launch_surface.md` §2, `LIVE_SET`, `APP_BAR_HOME` and the e2e `PAGES` agree. `CRM_ENABLED` turns on for every org |

### 13.7 After launch, in any order

The Native slices **CRM-S1 to S12** (§6). The insights **CRM-I1 to I6** (§9).
And four mirror slices:

| Id | What | Gate |
|---|---|---|
| **CRM-Z8** | Near-real-time: Zoho's Notification API starts a targeted pull | 🟢 AGENT-SAFE. A signed URL per org (§10.7) |
| **CRM-Z9** | Agent actions in Zoho through Zoho's MCP (§7.2), behind Metorite's confirmation step. Our `writer.py` stays as the fallback if Zoho's MCP fails the CRM-Z0 check | 🔴 OWNER-GATE to turn on for any org |
| **CRM-Z10** | "Make Metorite your CRM": Mirror to Native (§3.4). Z10 must rewind `processed_watermark`, because Mirror mode skips auto-lead mail | 🟢 AGENT-SAFE to build. 🔴 Fracktal's run is the owner's |
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
| Only the members that the admin picks can open the CRM. Only an admin manages stages, lost reasons and the mode | `test_crm_access.py`, `test_crm_access_r8.py` | New (CRM-T5), R8 |
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

---

## 18. Zoho parity and the adaptive mirror (2026-10-12)

**Bottom line.** The mirror must work for every Zoho edition, from Free to
Enterprise and Ultimate, and for every customer's own modules and fields. So
the mirror reads the shape of the customer's Zoho first, then maps what it
knows by fixed rules. A generic store holds the rest. An AI assistant proposes
a mapping where the rules do not know, and an admin approves it. The sync
applies saved rules only.

**The layout.** The CRM uses the words that Zoho users know: one **Deals**
view with a board or list switch and a pipeline picker. A separate "Pipeline"
row does not exist.

**Why.** On 2026-10-12 the owner reported three things. Pipeline settings did
not open. "Pipeline" read as a third object beside Leads and Deals. The CRM
did not have the views and settings that a Zoho mirror needs.

The owner then asked for comprehensive research. They also asked for a mirror
that adapts to each customer's Zoho tier, with AI help where the mapping is not
clear.

### 18.1 What the research found

The research was done on 2026-10-12. It read the documentation of Zoho CRM, the
Zoho API v8, HubSpot, Salesforce, Pipedrive, Attio, Close and Freshsales, and
buyer guides. The full note stays outside the repository, because it is a
working note.

1. **A pipeline is not an object in Zoho.** It is a property of a Deal. A Deal
   has a `Pipeline` field and a `Stage` field. A pipeline is an ordered subset
   of the one Stage picklist, attached to a layout. Leads have no pipeline.
   They have one flat `Lead_Status` picklist.
2. **The layout of our CRM shows one entity twice.** The rail row "Pipeline"
   is the deal board, and the rail row "Deals" is the deal list.
3. **The mirror flattens the customer's Zoho.** We keep one list of deal
   stages, with no pipeline. `crm_field_defs` (migration 244) is written and
   never read. Tasks, converted leads, deal contact roles, Products, Quotes,
   attachments and custom modules are not mirrored. A record shows no related
   lists. The list views have no saved views, and no filter on a custom field.
4. **Zoho differs by edition.** The modules, the number of pipelines, the
   custom modules, the API credits and the concurrency all change with the
   edition. A mirror that assumes one edition breaks on another.

### 18.2 Owner answers (2026-10-12, in chat)

| Question | Answer | Effect |
|---|---|---|
| Fracktal's Zoho edition | Professional | Products and Quotes exist to mirror. Pipelines stay P0, because the mirror must not assume one edition |
| Does Fracktal rely on Zoho sharing rules? | No. Everyone sees all | Per-owner visibility in the mirror stays P1 |
| Deal currencies | INR only | Currency conversion in totals moves to P1 |
| "Accounts" or "Companies" in Mirror mode | "Companies" everywhere | No label alias. D95.2 holds |

### 18.3 The adaptive mirror

**Step 1: discovery.** At connect, and once a day after that, the sync reads
the shape of the customer's Zoho. It reads metadata only, never a record:

| Read | Zoho API | What it gives |
|---|---|---|
| Org | `GET /org` | Edition, home currency, multi-currency, time zone, `domain_name` (for the deep link) |
| Modules | `GET /settings/modules` | Each module, standard or custom, with its labels, `api_supported` and visibility |
| Layouts | `GET /settings/layouts?module=` | Sections and the fields in each section |
| Fields | `GET /settings/fields?module=` | Type, picklist values, lookup target, custom or standard |
| Pipelines | `GET /settings/pipeline?layout_id=` | Each deal pipeline and its stages |
| Users | `GET /users` | The owner map |

The result is one **source profile** for each connection. It is versioned, so
a change in the customer's Zoho shows as a new version.

**Step 2: three mapping layers, in this order.**

1. **Known modules go to our tables, by fixed rules.** Leads, Contacts,
   Accounts, Deals, Notes, Calls, Events and Tasks.
2. **The other modules go to one generic record store.** It holds the
   standard modules that we do not model yet. They are Products, Quotes, Sales
   Orders, Invoices, Vendors, Price Books, Campaigns and Cases. It also covers each
   custom module. A generic record keeps its fields in JSON, described by
   `crm_field_defs`. Its lookups become links. The UI shows a generic list and
   a generic record page for each module. A later slice can give a module its
   own table. The generic store then hands its rows over.
3. **Fields.** A field that maps to one of our columns goes there, by a fixed
   rule. Every other field goes to `custom`, described by `crm_field_defs`.

**Step 3: the AI mapping assistant, for unclear cases only.** It proposes a
mapping where the fixed rules do not decide. Two examples: a custom deal field
that is a product reference, or a custom module that is an install base linked
to Accounts.

- It reads metadata only: module and field names, types, labels and picklist
  values. It never reads a customer record.
- It goes through the Router, so the AI spend is metered.
- Each proposal has a target, a confidence and one sentence of reason.
- A proposal becomes a **mapping rule** only when an admin approves it in
  **CRM settings → Field map**. The sync applies rules and never calls the
  model for a record. So each record maps the same way, and a re-sync gives
  the same result.
- With no approved rule, the data stays in the generic store or in `custom`.
  Nothing is lost while the admin decides.

**Step 4: the app follows the profile.**

- The rail shows the modules that the customer has. A module that the edition
  does not have does not show.
- A deal pipeline picker shows only when the org has more than one pipeline.
- The default sync budget follows the edition's daily API credits, read from
  the profile. The 500-credit default of CRM-Z5 stays as the floor for an
  unknown edition.
- The CRM settings page shows what the mirror leaves out. Examples: a module
  that the API does not support, records skipped, and the Notes that a
  non-admin Zoho user cannot read.

### 18.4 The layout of the CRM

| Rail row | Opens |
|---|---|
| Leads | List, with a list or board switch. The board columns are the Lead Status values |
| Contacts | List |
| Companies | List |
| Deals | List, with a list or board switch. A pipeline picker shows when there are two or more pipelines |
| Activities | Tasks, calls and meetings |
| *A row for each mirrored generic module* | Generic list and record page, for example Products and Quotes |
| Reports | Forecast, funnel, win and loss, owners |
| CRM settings | Setup, Access, Connection, Pipelines and stages, Lead Status, Lost reasons, Fields, Field map, Sync health |

The row "Pipeline" goes. The record page gets **related lists**. A company
shows its contacts and deals. A contact shows its deals. A deal shows its
contacts with their roles.

### 18.5 Tickets

Each migration takes the next free number at build time (R1). Each new table
has the tenant column and FORCE RLS (R5), and an R8 test on a real database.

| Id | What | Priority |
|---|---|---|
| **CRM-Q1** ✅ built 2026-10-12. Built as: migration 247 adds `crm_source_profiles`. A discovery step runs at the first import and once in 24 hours. It reads `GET org`, the modules, the layouts and fields of four modules, and the Deals pipelines. That costs about 11 credits and reads no record. A new profile version is kept only when the hash changes. The org domain, the edition and the tab of each module go into `provider_meta`. The default budget is 10 % of the edition credits, and 500 credits for an unknown edition. `deep_link` is built, and every mirrored record carries `ext_url`. `GET /crm/source-profile` returns no record and no token | Discovery and the source profile. The edition-aware budget. `domain_name` and the deep link ("Open in Zoho") | P0 |
| **CRM-Q2** ✅ built 2026-10-12. Built as: migration 247 adds `crm_pipelines`, `crm_pipeline_stages`, `crm_deals.pipeline_id` and `crm_deal_statuses.forecast_category`. Discovery writes the Zoho pipelines through the mirror bypass. A deal finds its pipeline by id, by name, or the default. A Native org gets one "Sales pipeline" from the seed, and the migration backfills the same for existing orgs. The U5b reset deletes the pipelines. `GET /crm/pipelines` and `?pipeline_id=` serve the UI. An admin renames a pipeline and adds or removes its stages in Native mode only | Deal pipelines: `crm_pipelines`, the stages of each pipeline, `crm_deals.pipeline_id`, and the forecast category | P0 |
| **CRM-Q3** | ✅ built 2026-10-12. The new layout of §18.4: no "Pipeline" row, the list or board switch, the pipeline picker, the Leads board, "CRM settings", and related lists | P0 |
| **CRM-Q4** | ✅ built 2026-10-12. Field definitions in the UI: `GET /crm/fields/{entity}`, custom fields with label, type and order on the record page | P0 |
| **CRM-Q5** ✅ built 2026-10-12. Built as: the Leads read sends `converted=both`. A converted lead keeps its Zoho ids in `custom._converted`, and fills `converted_contact_id`, `converted_organization_id`, `converted_deal_id` and `converted_at`. An id with no local row stays NULL, and `_relink_leads` fills it at the end of each run. A lead cursor from before this slice is read in full once, because an incremental read never returns a lead that did not change. Tasks are the entity `task` (Zoho `Tasks`), read after the notes, calls and meetings. They land in `crm_activities` with `type='task'`, `due_at` from `Due_Date`, `completed_at` for a Completed task, and `Status` and `Priority` in `custom`. A task with no parent record is skipped, because `crm_activities` needs one. The deleted list covers them. Roles: each deal that changed is queued in the cursor row `zoho:deal_roles`, and the step reads `GET /Deals/{id}/Contact_Roles` for each one, at 1 credit. `set_deal_contacts_from_source` writes `(deal_id, contact_id, role)` and removes the rest, except the main contact. The fence names it. The step stops with 20 credits left and keeps the rest queued. Each step reports under its own key of `crm_sync_runs.counts`: `converted_leads`, `tasks` and `deal_contacts`, with `seen`, `upserted`, `deleted`, `unlinked`, `skipped` and `unavailable`. ⚠️ **The new scope `ZohoCRM.modules.tasks.READ` needs a reconnect.** A connection made before this slice must be connected again, and an admin does that in CRM settings. Until then Zoho answers a scope error on Tasks. The engine skips the module, counts it in `tasks.unavailable`, asks again once an hour, and never fails the run. A scope error is not a dead token, so it never sets `needs_reconnect`. Not measured on a live tenant: the exact shape of `Converted_*` on a v2 lead, and `Contact_Role` in the roles answer. Both are read in more than one form Review fixes: migration 249 adds `crm_deal_contacts.ext_source`, and the roles door deletes only rows with `ext_source='zoho'`, so a native link stays. The Leads list, board and export hide a mirrored lead that has `converted_at`, and any lead that has `converted_deal_id`. `include_converted=true` shows them. Any failure to read Tasks skips the module, except a dead token, a spent budget, a throttle and a plain HTTP 401. A plain 401 is a stale token, so it fails the run. A failed roles read is queued again, and dropped after 3 attempts. | Data that the mirror loses: converted leads (`converted=both`), Zoho Tasks, and deal contact roles | P0 |
| **CRM-Q6** | The generic record store and its views. Products and Quotes first, then each custom module | P0 |
| **CRM-Q7** | The AI mapping assistant and CRM settings → Field map | P0 |
| **CRM-Q8** | Filters on pipeline, stage, owner, source, dates and custom picklists. Saved views (`crm_views`) | P0 |
| **CRM-Q9** | ✅ built 2026-10-12. Sync health: the Zoho count against ours, skipped records, unsupported modules, last error | P0 |
| **CRM-Q10** | Layouts and sections on the record page. The move to API v8 with field batches of 50 | P1 |
| **CRM-Q11** | Currency conversion in totals and in the forecast | P1 |
| **CRM-Q12** | Per-owner visibility in the mirror | P1 |
| **CRM-Q13** | Attachments and email metadata. Tags. The Zoho user directory | P1 |

**CRM-Q9 built as (2026-10-12).**

- `GET /crm/sync/health` is read only and needs an admin. It sits behind `CRM_MIRROR` and adds no migration. It returns `last_run`, `last_success_at`, `next_run_at`, `entities`, `modules`, `history` (10 runs) and `credits`. The error is a short code. It holds no token and no record.
- Each entity row holds `zoho_count` (the last estimate), `ours` (rows with `ext_source = zoho`) and the count keys of the last run. A key in the counts that the route does not know passes through, so a new entity needs no change here. `ours` is null for a key with no local table.
- `credits.budget_24h` comes from `engine.daily_budget`, and `used_24h` from `runs.credits_used_24h`. The engine is not changed. `runs.recent` is new.
- The Sync health card shows in Mirror mode for an admin. It shows the status line, a table by entity with a difference badge, an Unavailable chip with "Connect Zoho again to allow this module", the modules in the Zoho account, a credits bar, the last 10 runs and Sync now. It polls every 3 s while a run is running.
- Fences: `tests/unit/test_crm_sync_health_r8.py` and `src/app/crm/components/crmQ9.test.ts`.

**CRM-Q4 built as (2026-10-12).**

- `GET /crm/fields/{entity}` serves leads, contacts, organizations and deals. It maps them to the `crm_field_defs.entity` values `lead`, `contact`, `company` and `deal`. Any member that the CRM access rule admits may read it. An unknown entity gives 422. It returns `key`, `label`, `type`, `options`, `position`, `visible`, `source` and `api_name`, in the order of `position`. It adds no write route and no migration. Fences: `tests/unit/test_crm_fields.py` and `tests/unit/test_crm_fields_r8.py`.
- The record page draws each custom field from its definition: the label, the order, and only visible fields. The rules for each type are in `src/app/crm/lib/fields.ts`. A key with no definition keeps its humanised key. In Mirror mode the fields stay read only.
- A URL becomes a link only for an http or https address, with `rel="noopener noreferrer"`. Any other value stays text.
- The Fields card on CRM settings lists the fields of each entity. It says "Fields come from Zoho." in Mirror mode and "Field editing comes later." in Native mode. It has no editor. CRM-S6 builds the editor.
- Each list has a Columns control. A member picks up to 5 custom fields. The browser keeps the choice for each entity in local storage. Saved views on the server stay with CRM-Q8.

**CRM-Q3 built as (2026-10-12).**

- The rail reads Leads, Contacts, Companies, Deals, Reports and CRM settings. It has no Activities row, because no Activities view exists yet.
- `?tab=pipeline` and `?tab=board` open Deals as a board. `?tab=settings` opens CRM settings. Deals opens as a board, and `?layout=list` shows the list.
- Deals and Leads have a list or board switch in the title bar. The Leads board has one column for each Lead Status. A card does not drag in Mirror mode or with an `ext_source`.
- In Mirror mode no card drags, including a native card (§18.4).
- The pipeline picker shows for two pipelines or more. It needs `GET /crm/pipelines` from CRM-Q2. A 404 gives one implicit pipeline and no picker. The browser keeps the last choice.
- The record page shows related lists. The list routes take `?company_id=` for contacts and deals, and `?contact_id=` for deals. A filter on another entity gives 422. Fence: `tests/unit/test_crm_related_r8.py`.
- CRM settings is one page of cards in this order: Setup, Access, Connection, Pipelines and stages, Lead Status, Lost reasons, Fields, Sync health. Fields and Sync health no longer say "Coming soon" (CRM-Q4 and CRM-Q9).
- §18.6: an error boundary wraps the CRM views. It shows "Something went wrong on this page. Reload to get the newest version." The Access card reads the group list only for the rule "groups".
- The UI does not yet call the CRM-Q2 routes that rename a pipeline or add and remove its stages. So a second pipeline shows its stages read-only.

The earlier native gaps of §6 (CRM-S1 to CRM-S12) and the insights of §9 keep
their own rows. CRM-Q8 builds the saved views of CRM-S2.

### 18.6 The settings page that did not open

The owner saw the CRM stop when they opened Pipeline settings on 2026-10-11. A
local run of the same code, in a production build, did not reproduce it. The
gateway logged no error and no request for the groups list. The most likely
cause is a browser tab that stayed open across several deploys and held old
script files. CRM-Q3 replaces that view. If it happens again, capture the
browser console and the network tab.
