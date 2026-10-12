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
> **Built later:** CRM-Q3, the new layout of §18.4 (2026-10-12). CRM-Q4, field definitions in the UI (2026-10-12). CRM-Q9, the Sync health card (2026-10-12). CRM-Q8, filters and saved views (2026-10-12, migration 250). The Deals board stages fix (2026-10-12, §18.5).
>
> **Built later:** CRM-Q5, the data the mirror loses (2026-10-12). Migration 249 adds `crm_deal_contacts.ext_source`.
>
> **Built later:** CRM-Q6a, the generic record store (2026-10-12, migration 251). See §18.7.
>
> **Built from §19:** CRM-X4, the record page v2 (2026-10-12, §19.8).

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
| Connect Fracktal's Zoho again, to give the new read scopes. CRM-Q5 adds Tasks, and CRM-Q6a adds the other modules. One consent after CRM-Q6a covers both | A third party's data (§3a rule 3) | CRM-Q5, CRM-Q6a (§18.7) |

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

# The adaptive mirror (§18.7): CRM-Q6a, CRM-Q6b, CRM-Q7a and CRM-Q7b
uv run pytest tests/unit/test_crm_module_records_r8.py tests/unit/test_crm_modules_routes_r8.py \
  tests/unit/test_crm_field_rules_r8.py tests/unit/test_crm_field_map_ai.py \
  tests/unit/test_crm_upsert_from_source_fence.py tests/unit/test_internal_ai_is_routed.py \
  tests/unit/test_crm_mirror_readonly.py tests/unit/test_tenancy_boundary.py \
  tests/unit/test_tenant_coverage.py tests/unit/test_org_purge_tenant.py \
  tests/unit/test_crm_sync_health_r8.py
uv run ruff check apps/services/gateway/gateway/routes/crm apps/services/gateway/gateway/crm_sync \
  apps/services/gateway/gateway/crm_sources

# The shell
uv run pytest tests/unit/test_shell_intent.py

# The daily-use UX (§19): CRM-X1, CRM-X2, CRM-X3 and CRM-X4
uv run pytest tests/unit/test_crm_find_and_page_r8.py tests/unit/test_crm_export.py \
  tests/unit/test_shell_search.py tests/unit/test_shell_search_crm_r8.py \
  tests/unit/test_shell_intent.py tests/unit/test_auth_me_shell.py \
  tests/unit/test_crm_next_activity_r8.py tests/unit/test_crm_mirror_readonly.py
# The vitest files of §19 (crmX1, runActivity, seams, nextActivity, crmX4,
# recordNav, timeline, urlState, format) run in the `npx vitest run` below.

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
| Only the sync writes a generic module record, and org B reads none of org A's | `test_crm_module_records_r8.py`, `test_crm_mirror_readonly.py`, `test_crm_upsert_from_source_fence.py` | New (CRM-Q6a), R8 |
| A field rule never overwrites a column of the fixed map, and only an admin decides one | `test_crm_field_rules_r8.py` | New (CRM-Q7a), R8 |
| The field-map model call reads no record value, and goes through the Router | `test_crm_field_map_ai.py`, `test_internal_ai_is_routed.py` | New (CRM-Q7b) |
| A list search matches the company of a deal and a phone number, pages with no duplicate, and stays in its tenant. The owner list holds every member | `test_crm_find_and_page_r8.py` | New (CRM-X1), R8 |
| The list and the CSV keep one column set, with Stage and Last touched | `test_crm_export.py` | Existing, widened (CRM-X1) |
| "Load more" adds no row twice, and the owner filter lists an owner with no row on screen | `crmX1.test.ts` | New (CRM-X1) |
| Cmd-K shows a CRM record only to a member that the CRM router admits, and only from that member's tenant | `test_shell_search.py`, `test_shell_search_crm_r8.py` | Existing, widened, and new (CRM-X2), R8 |
| The jobs `new-deal`, `log-call` and `find-record` agree on both sides | `test_shell_intent.py::TestOneJobList`, `test_auth_me_shell.py` | Existing (CRM-X2) |
| A run of `crm-assistant` counts on the CRM | `runActivity.test.ts` | Existing, widened (CRM-X2) |
| The CRM mounts no palette and no ⌘K listener | `seams.test.ts` | Existing (CRM-X2) |
| One statement finds the next activity of a whole board, and Zoho and native tasks count alike | `test_crm_next_activity_r8.py` | New (CRM-X3), R8 |
| The chip of a card reads overdue, today, later or none by the day rule | `nextActivity.test.ts` | New (CRM-X3) |
| No CRM file holds a raw `<input>`, `<textarea>` or `<select>`, and a phone link holds only digits | `crmX4.test.ts` | New (CRM-X4) |
| `j` and `k` step through the list behind the record, and stop at each end | `recordNav.test.ts` | New (CRM-X4) |

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
| **CRM-Q8** | ✅ built 2026-10-12. Built as: migration 250 adds `crm_views`. The list routes and `GET /crm/pipeline` take `owner` (or `me`), `source`, the created, updated and close date ranges, and `custom.<key>=<value>` or `custom.<key>.in=a,b`. A custom key that `crm_field_defs` does not list gives 422. The export takes the same parameters. `GET`, `POST`, `PATCH` and `DELETE /crm/views` save a view of the filters, sort, columns, layout and pipeline. A view is private to its owner, and only an admin shares it. A view works in Mirror mode. Saved views nest under each entity in the rail, `?view=` opens one, and the column choice moves into the view while one is active. Filters on a board and a list use one filter bar. A multiselect field matches when any chosen option is in its array. In Mirror mode the updated range uses the Zoho modified time, and the created range gives 422 `created_range_mirror`. Follow-up: a mirrored created column, so that a created range works in Mirror mode. This also builds CRM-S2 | P0 |
| **CRM-Q9** | ✅ built 2026-10-12. Sync health: the Zoho count against ours, skipped records, unsupported modules, last error | P0 |
| **CRM-Q10** | Layouts and sections on the record page. The move to API v8 with field batches of 50 | P1 |
| **CRM-Q11** | Currency conversion in totals and in the forecast | P1 |
| **CRM-Q12** | Per-owner visibility in the mirror | P1 |
| **CRM-Q13** | Attachments and email metadata. Tags. The Zoho user directory | P1 |

**Deals board stages built as (2026-10-12).** The owner reported the bug with a screenshot of the Fracktal mirror.

- The sync sets the type of a mirrored stage from its pipeline. A stage that the pipeline calls `won` or `lost` gets that type, also on a row that exists. A stage with no type keeps its row. Before this change, the insert kept an existing row, so Payment Complete and Re-Evaluate stayed `open`.
- Each run applies the types of the latest stored profile before its first Zoho call. So an org gets the fix on its first run after the deploy. The pass runs in Mirror mode only, and it changes no deal row.
- The board header counts the open and ongoing lanes only, as the owner leaderboard does. A won or a lost deal is not in the pipeline.
- `orderLanes` in `lib/board.ts` is the one column order, and it takes the mode. In both modes, open stages come first, then `on_hold` stages, then won stages, then lost stages. In Mirror mode, open stages sort by probability, then by their place in the pipeline, then by the global position. In Native mode, every group sorts by the admin's stage order, the global position. Probability does not change a Native board, because a new Native stage has probability 0 (owner decision, 2026-10-12).
- In Mirror mode the board hides a stage that holds no deal under the current filters. In Native mode it shows that stage, because it is a drop target. The "Empty stages" button in the title bar changes this. The browser keeps the choice for each member.
- A deal can sit in a stage that its pipeline does not list. `GET /crm/pipeline?pipeline_id=` returns that stage as a lane while it holds a deal under every CRM-Q8 filter of the request, `owner=me` included. Each lane carries `pipeline_position`. The board places a held lane by `orderLanes`, so it can sit between listed lanes. The lane counts then add up to the filtered deals of the pipeline.
- No migration and no flag. Fences: `tests/unit/test_crm_pipelines_r8.py`, `tests/unit/test_crm_discovery.py` and `src/app/crm/components/boardStages.test.ts`.

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
- Each list has a Columns control. A member picks up to 5 custom fields. The browser keeps the choice for each entity in local storage. While a saved view is active, the view holds the choice (CRM-Q8).

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

### 18.7 CRM-Q6 and CRM-Q7: the build contract (2026-10-12)

The §18.5 rows for CRM-Q6 and CRM-Q7 are one line each. The spec audit of
2026-10-12 found no data shape, no "done when", no fence and no gate. This
section is that contract. It splits the two tickets into four slices.

| Slice | Gate | Builds | Waits for |
|---|---|---|---|
| **CRM-Q6a** ✅ built 2026-10-12 | AGENT-SAFE | The generic record store, its door, discovery of the other modules, the new scopes, and the sync. No UI and no new route | CRM-Q5 merged |
| **CRM-Q6b** ✅ built 2026-10-12 | AGENT-SAFE | The read-only routes, the rail rows and the generic record page | CRM-Q6a merged. Start it after CRM-Q8 merges too, because both edit `CrmRail.tsx` and `page.tsx`. That is a merge order, not a contract |
| **CRM-Q7a** | AGENT-SAFE | The rule table, the Field map card, approve and reject, and the sync that applies approved rules. No model call | CRM-Q6a merged |
| **CRM-Q7b** | AGENT-SAFE | The proposer: one routed model call over metadata | CRM-Q7a merged |

The new Zoho consent is the owner's act (§12). The build is not. Each slice
takes the next free migration number at build time (R1).

#### CRM-Q6a: the generic record store (✅ built 2026-10-12)

**The table.** `crm_module_records` holds one row for each record of a module
that has no table of its own.

| Column | Type | Note |
|---|---|---|
| `id` | UUID PK | |
| `organization_id` | UUID NOT NULL | Inline `REFERENCES organization (id) ON DELETE CASCADE`, the shape of migration 247 |
| `module` | TEXT NOT NULL | The provider's module API name, for example `Products` or `Install_Base` |
| `ext_source` | TEXT NOT NULL | `zoho` |
| `ext_id` | TEXT NOT NULL | The provider's record id |
| `name` | TEXT | The value of the field that has `display_field: true` in the module's field definitions. With no such field, `name` is NULL |
| `owner_email` | TEXT | The owner, mapped the way `map_record` maps it for the known modules |
| `fields` | JSONB NOT NULL DEFAULT `'{}'` | Each field value, keyed the way `custom` is keyed |
| `ext_url` | TEXT | The deep link (CRM-Q1) |
| `ext_created_at`, `ext_modified_at` | TIMESTAMPTZ | The provider's times |
| `created_at`, `updated_at` | TIMESTAMPTZ NOT NULL DEFAULT `now()` | |

- `UNIQUE (organization_id, ext_source, module, ext_id)`, and an index on
  `(organization_id, module, ext_modified_at DESC)`.
- FORCE ROW LEVEL SECURITY, with a policy that has USING and WITH CHECK, the
  shape of migration 243.
- The table joins `test_tenancy_boundary.py`, `test_tenant_coverage.py`, the
  org purge list, and the four generated tenancy files.
- No native writer exists. Only the sync writes this table.

**The field definitions.** Discovery writes the fields of each module into
`crm_field_defs` with `entity = 'module:<ApiName>'`, for example
`module:Products`. The four known entities keep `lead`, `contact`, `company`
and `deal`. `crm_field_defs.entity` is free text, so this needs no schema
change.

**Lookups.** A lookup value stays in `fields` as Zoho sends it, `{id, name}`.
The field definition names its target module. CRM-Q6b resolves the link when
it reads the record. CRM-Q6a adds no link table.

**Which modules.** Discovery already reads `GET /settings/modules`. The sync
mirrors a module when all of these are true:

- `api_supported` is true.
- `generated_type` is `default` or `custom`. A `linking` module and a subform
  are never mirrored.
- The module is not one of the known modules of §18.3 step 2.
- The module is one of the ten standard modules below, or its
  `generated_type` is `custom`. No other module is mirrored.
- The connection has the module's read scope.

Discovery sets `mirrored: true` on the row of each selected module in the
profile. Today `_module_rows` in `mapping.py` sets it from `MIRRORED_MODULES`
only, so CRM-Q6a widens that rule.

The order is `Products`, `Quotes`, `Sales_Orders`, `Purchase_Orders`,
`Invoices`, `Vendors`, `Price_Books`, `Cases`, `Solutions` and `Campaigns`. The custom modules
follow, in alphabetical order. The sync mirrors 25 modules at most. Sync health
lists each other module as "not mirrored".

**The scopes.** CRM-Q6a adds one read scope for each standard module above to
`ZOHO_READ_SCOPES` (`routes/crm/connection.py`). The name is
`ZohoCRM.modules.<api_name>.READ`, with the API name in lower case and with no
underscore. An example is `ZohoCRM.modules.salesorders.READ`. A custom module
uses `ZohoCRM.modules.custom.READ`.

⚠️ Before the build, check each scope name against Zoho's published scope
list. Zoho refuses the WHOLE consent for one unknown scope, and then no org can
connect.

**The door.** Add `upsert_module_records_from_source` and
`delete_module_records_from_source` to `routes/crm/core.py`, beside
`upsert_from_source`.

- Both names join `NAMES` in `tests/unit/test_crm_upsert_from_source_fence.py`.
- `crm_module_records` joins `_TABLES` in `tests/unit/test_crm_mirror_readonly.py`.
- The door sets no `zoho_id`, because the generic table has no such column.

**The sync.**

- Each module has its own cursor row, `zoho:module:<ApiName>`.
- The read is the same incremental read on `Modified_Time`, with the same
  deleted list.
- The modules run after the known entities and after the CRM-Q5 steps.
- The step stops with 20 credits left, as the roles step does. The next run
  continues, so a large first import spans runs.
- A scope error, a 400 or a 500 on one module marks that module unavailable,
  with the CRM-Q5 rule. A plain HTTP 401 still fails the run.
- Each module reports under its own key, `module:<ApiName>`, in
  `crm_sync_runs.counts`.
- The estimate also counts each mirrored module under `module:<ApiName>`,
  because Sync health reads `zoho_count` from the estimate.
- Sync health computes `ours` for each module key from `crm_module_records`.

**Done when.** One R8 suite, `tests/unit/test_crm_module_records_r8.py`, shows
each of these on a real database:

1. A fake Zoho has 2 Products and 1 record of a custom module `Install_Base`.
   After a sync, `crm_module_records` holds 3 rows, keyed by
   `(org, 'zoho', module, ext_id)`.
2. `crm_field_defs` holds the fields under `module:Products` and
   `module:Install_Base`.
3. A second sync with no change adds no row.
4. A changed Zoho record updates `fields` and `ext_modified_at`. A deleted
   record goes.
5. Org B reads none of org A's rows through the app role.
6. A scope error on one module marks only that module unavailable. The other
   modules sync, and the run status is `ok`.
7. A linking module and a subform never get a row.
8. A run that stops at the reserve continues in the next run, with no
   duplicate row.
9. `GET /crm/sync/health` lists each module with `zoho_count` and `ours`.

**Built as (2026-10-12).**

- Migration 251 adds `crm_module_records`, with the tenant key, FORCE row
  level security and one policy with USING and WITH CHECK.
- Discovery marks a generic module `mirrored` only when the connection holds
  its read scope. A module that waits for its scope keeps `scope` in the
  profile. The sync counts it as unavailable and makes no call for it.
- Discovery reads the fields of each mirrored module, at 1 credit each. It
  writes them to `crm_field_defs` as `module:<ApiName>`. A lookup keeps its
  target in `options` as `{"lookup_module": ...}`. The profile keeps the
  `display_field` of each module.
- The sync reads each module after the CRM-Q5 steps, with the cursor
  `zoho:module:<ApiName>` and the deleted list. The deleted list of a module
  starts before its first read.
- The reconcile never walks a generic module, because the table has no
  `ext_seen_at`. Its deletes come from the deleted list only.
- A module that hides `Modified_Time` is read again in full once in each
  `RECONCILE_EVERY`. The door skips an unchanged record, so that read writes
  nothing.
- After its first import, a module reads its changed list at most once an hour
  (`MODULE_READ_EVERY`), the same cadence as the deleted lists. During its first
  import it reads on each run, within the reserve.
- Discovery is due when the connection's scope set differs from
  `profile.scope_set`. The module step also checks the connection's scopes at
  run time. So the next run after a new consent mirrors the modules.
- Discovery stops reading module fields at the reserve. A module that still
  waits for its fields is not read, and discovery is due again after an hour.
- The sync reads the count of each module once in 24 hours. The cursor row
  `zoho:module_counts` keeps it, so an incremental run also reports
  `zoho_count`. A failed count keeps its time too.
- The U5b reset counts and deletes `crm_module_records`.
- The two doors are `upsert_module_records_from_source` and
  `delete_module_records_from_source`. A replay that changes nothing writes
  nothing.
- The profile now marks Tasks as a known module, so Sync health shows Tasks
  as mirrored.
- ⚠️ The eleven new scopes need a new consent (§12). Until the owner connects
  Zoho again, each module shows as unavailable and the run stays `ok`.
- Fences: `tests/unit/test_crm_module_records_r8.py`,
  `tests/unit/test_crm_module_records.py`,
  `tests/unit/test_crm_upsert_from_source_fence.py` and
  `tests/unit/test_crm_mirror_readonly.py`.

#### CRM-Q6b: the views (✅ built 2026-10-12)

- `GET /crm/modules` returns the mirrored generic modules of the latest
  profile: `api_name`, the plural label, and the row count. A Native org gets
  an empty list.
- `GET /crm/modules/{module}/records` takes `q` (on `name`), `sort` and the
  page params of the other lists. A module that the profile does not mirror
  gives 404.
- `GET /crm/modules/{module}/records/{id}` returns one record. Its fields use
  the CRM-Q4 rules from `crm_field_defs`.
- A lookup becomes a link:
  - to the record page of a known record, when the target is a known module.
  - to the generic record page, when the target module is mirrored.
  - otherwise, to plain text.
- "Open in Zoho" uses `ext_url`. Every route is read only, and the routes use
  the CRM access rule.
- The rail shows one row for each module, in the profile order, under the
  known modules (§18.4). The group does not show when the org has no module.
- The routes go in a new file, `routes/crm/modules.py`.

**Done when.**

- `tests/unit/test_crm_modules_routes_r8.py` shows the org isolation, the 404
  for an unknown module, the empty list for a Native org, and the lookup links.
- A vitest file shows that the rail rows follow the profile.
- The look gate of CLAUDE.md §4 passes: light mode, compact density, a changed
  accent, and the neighbouring Deals list.

**Built as (2026-10-12).**

- `routes/crm/modules.py` holds the three routes on the CRM router. They read
  only, and the slice adds no migration.
- `mirrored_modules` reads the latest profile in the bound tenant. It keeps
  each module with `generic` and `mirrored` true, in the order of
  `generic_modules`. Native mode or no profile gives an empty list.
- Each route checks the module name against that set before it reads a
  record. An unknown name gives 404 `unknown_module`. The name reaches SQL only
  as a bound parameter.
- The list uses `core.list_contract`, with `q` on `name`. The sorts are `name`,
  `owner_email`, `ext_modified_at` and `ext_created_at`. The list also returns
  `field_defs`, so the Columns control can offer the fields of the module.
- The record route returns the record, its field definitions and `links`. A
  lookup links to a known record or to a mirrored generic record, through
  `ext_id`. Any other lookup gets no link, and the page shows it as text.
- The rail shows one row for each module after Deals and before Reports, with
  its count. An org with no module gets no row.
- `?tab=module&module=<ApiName>` opens a list, and `?mrec=<ApiName>:<id>` opens
  a record with its `?rtab=`. A known record and a generic record never open
  together.
- The list is `RecordList` with `MODULE_COLUMNS`. Its search box and its "Load
  more" button are local to `ModuleList.tsx`, with the names of the CRM-X1
  store. They move onto the CRM-X1 parts after CRM-X1 merges.
- `RecordShell.tsx` holds the drawer, the header facts and the tab bar.
  `RecordSheet` and `ModuleRecordSheet` both use it, so the two record pages
  have one look.
- Fences: `tests/unit/test_crm_modules_routes_r8.py` and
  `src/app/crm/components/crmQ6b.test.ts`, red first. A visual check passed
  in light mode, at compact density, under a violet accent and at 390 px,
  beside the Deals list.

#### CRM-Q7a: the rule table and the Field map

**The table.** `crm_field_rules` holds one row for each field of a known
entity that an admin decided, or that the assistant proposed.

| Column | Type | Note |
|---|---|---|
| `id` | UUID PK | |
| `organization_id` | UUID NOT NULL | Inline `REFERENCES organization (id) ON DELETE CASCADE` |
| `ext_source` | TEXT NOT NULL | `zoho` |
| `entity` | TEXT NOT NULL | `lead`, `contact`, `company` or `deal` |
| `field_api_name` | TEXT NOT NULL | The provider's field API name |
| `field_type` | TEXT NOT NULL | The `crm_field_defs.type` word when the rule was made, for example `select` |
| `profile_version` | INT NOT NULL | The `crm_source_profiles.version` that the rule was made from |
| `target_column` | TEXT | The column the value goes to |
| `status` | TEXT NOT NULL | `proposed`, `approved`, `rejected` or `stale` |
| `proposed_by` | TEXT NOT NULL | `ai` or `admin` |
| `confidence` | NUMERIC | 0 to 1. Only for `ai` |
| `reason` | TEXT | One sentence, 140 characters at most |
| `decided_by`, `decided_at` | TEXT, TIMESTAMPTZ | The admin who approved or rejected it |
| `created_at`, `updated_at` | TIMESTAMPTZ NOT NULL DEFAULT `now()` | |

`UNIQUE (organization_id, ext_source, entity, field_api_name)`. FORCE ROW LEVEL
SECURITY, as for `crm_module_records`. CRM-Q7 covers the four known entities
only. A generic module keeps every field in `fields`.

Only one rule for each `(organization_id, entity, target_column)` can be
`approved`. A partial unique index on `status = 'approved'` holds this. A
second approve gives 409.

**The legal targets.** `FIELD_RULE_TARGETS` in `crm_sources/zoho/mapping.py`
holds exactly the columns of the two tables below, and no other column.

The first kind is a **free column**, which the fixed map never writes:

| Entity | Column | Type |
|---|---|---|
| company | `linkedin_url` | TEXT |
| contact | `linkedin_url` | TEXT |
| lead | `lost_note` | TEXT |
| deal | `lost_note` | TEXT |

The second kind is a **fixed column that the org leaves empty**. The fixed map
fills each one from one standard Zoho field, shown in brackets. Zoho does not
let an org delete a standard field. An org that does not use one takes it off
every layout, so no user can fill it in.

- Discovery already reads the layouts of the four known modules. It stores
  `layout_fields` in the profile: for each entity, the field API names that
  are on at least one active layout.
- A rule may target a fixed column only when the column's standard field is
  not in `layout_fields`. `crm_field_defs` cannot answer this, because
  `map_field_def` writes no row for a standard field.
- The rule fills the column only where the fixed map gives NULL. So a rule
  never overwrites a value that came from the standard field, and no old value
  is lost.

| Entity | Column (standard Zoho field) | Type |
|---|---|---|
| company | `website` (Website), `industry` (Industry), `phone` (Phone), `email` (Email), `description` (Description) | TEXT |
| company | `no_of_employees` (Employees) | INT |
| company | `annual_revenue` (Annual_Revenue) | NUMERIC |
| contact | `email` (Email), `phone` (Phone), `mobile` (Mobile), `title` (Title), `description` (Description) | TEXT |
| lead | `website` (Website), `industry` (Industry), `email` (Email), `phone` (Phone), `mobile` (Mobile), `lead_source` (Lead_Source), `organization_name` (Company), `description` (Description) | TEXT |
| lead | `no_of_employees` (No_of_Employees) | INT |
| lead | `annual_revenue` (Annual_Revenue) | NUMERIC |
| deal | `next_step` (Next_Step), `description` (Description) | TEXT |
| deal | `expected_close_date` (Closing_Date) | DATE |
| deal | `probability` (Probability) | SMALLINT, coerced with `integer(value, _SMALLINT_CEILING)` |

A rule is legal only when the `field_type` agrees with the column type:

| `field_type` | Column type |
|---|---|
| `text`, `textarea`, `email`, `phone`, `url`, `select` | TEXT |
| `number`, `currency` | NUMERIC or INT |
| `date` | DATE |
| `datetime` | TIMESTAMPTZ |
| `multiselect`, `lookup` | None. A rule cannot target a column |

The routes refuse any other rule with 422.

**The Field map card.** It is in CRM settings, for an admin, in Mirror mode
only.

- It lists, for each entity, the fields that go to `custom` today.
- It shows each proposal with its target, its confidence and its reason.
- An admin can approve or reject a proposal. An admin can also pick a target
  by hand, which makes a rule with `proposed_by = 'admin'`.
- Routes: `GET /crm/field-rules?entity=`, `POST /crm/field-rules` and
  `PATCH /crm/field-rules/{id}`. Only an admin calls them.

**What an approved rule does.**

- The sync's `map_record` applies each approved rule after the fixed rules.
  The value also stays in `custom`, so a later reject loses nothing.
- The routes write no CRM record. Approve and reject only set `status`.
- The next sync run, in `gateway/crm_sync/`, does the backfill before it reads
  Zoho. It copies `custom ->> field_api_name` into the column, only on the
  mirrored rows where the column is NULL, in batches of 500. It reads no Zoho
  data, so it costs no credit. The backfill goes through a new door,
  `apply_field_rule_from_source`, in `routes/crm/core.py`. Its name joins
  `NAMES` in `tests/unit/test_crm_upsert_from_source_fence.py`.
- An approved rule can be rejected, or it can go `stale`. Then the next run
  reads that entity in full once, as CRM-Q5 does for an old lead cursor. Each
  row then gets the fixed map's value again. A full read costs about 1 credit
  for each 200 records. The read uses its own mark in the cursor state, and it
  stamps nothing and deletes nothing.
- A full read cannot restore a free column, because the fixed map never writes
  it. A reject or a stale rule on a free column has its own step. The next run
  sets that column to NULL on the mirrored rows, through
  `apply_field_rule_from_source`.
- `map_record` and the backfill coerce each value with the helpers of
  `mapping.py`: `text_value`, `number`, `integer`, `day` and `instant`. A value
  that does not coerce writes NULL.

**When the profile changes.** Discovery checks each approved rule against the
new profile version. A rule goes `stale` in three cases:

- its field is gone.
- the new `field_type` disagrees with the target.
- its target is a fixed column, and the standard field of that column is back
  in `layout_fields`.

The sync stops applying a stale rule. The Field map shows it, and an admin
decides again.

**Done when.** `tests/unit/test_crm_field_rules_r8.py` shows each of these:

1. An approved rule makes the next sync write the column.
2. The next run after the approval backfills the existing rows, with no Zoho
   call. A value that does not coerce gives NULL, and the run still succeeds.
3. A rule on a fixed column whose standard field is in `layout_fields` gives
   422. A type mismatch gives 422. A rule on the same column, with the standard
   field off every layout, is legal.
4. A member who is not an admin gets 403.
5. Org B sees none of org A's rules.
6. A type change in the profile makes the rule `stale`, and the sync stops
   applying it.
7. Two syncs of the same data give the same rows.
8. A record whose standard field holds a value keeps that value. The rule
   fills only the NULL rows.
9. A reject makes the next run read the entity in full once. Each row then
   holds the fixed map's value again, and the value stays in `custom`.
10. A reject of a rule on a free column sets that column to NULL.
11. A second approved rule for the same target column gives 409.

#### CRM-Q7b: the proposer

**When it runs.** Only when an admin presses "Suggest mappings" on the Field
map. Nothing calls the model in the background. One entity is one call. When
proposals already exist for the current profile version, the route returns
them and calls no model.

**What it reads.** The input is metadata only. For one entity it holds:

- the fields that go to `custom`, each with its API name, label and type.
- up to 20 picklist option labels for each field, each cut to 60 characters.
- the legal targets of `FIELD_RULE_TARGETS`, with their types.

It never holds a record value. The prompt builder takes `crm_field_defs` rows
and the target list as its only arguments.

**How it calls the model.**

- It uses `acb_llm.context.acompletion_with_fallback(model="tier-fast",
  feature="crm.field_map", ...)`. The pattern is at
  `routes/email/automation/actions.py:321`.
- `test_internal_ai_is_routed.py` already fences that route.
- Bind the run context (the org and the member) before the call. The H-171
  meter reads both from it.
- Do not use `tier-decide`, because its residency is open (D75).

**What it writes.**

- The answer is a JSON list of `{field_api_name, target_column, confidence,
  reason}`.
- The server drops an item when its field was not in the input, its target is
  not legal, or its types disagree.
- Each item that stays becomes a `proposed` row. The proposer never writes
  `approved`.
- A model error or a bad answer gives a short message, and writes no row.

**Done when.** `tests/unit/test_crm_field_map_ai.py` shows each of these:

1. A test seeds records that hold a canary value. It captures the model call,
   and the canary is not in the input.
2. An illegal target, a type mismatch and an unknown field are dropped.
3. No proposal is ever `approved`.
4. The call goes through `acompletion_with_fallback` with
   `feature="crm.field_map"`, and with the org bound.
5. A second press for the same profile version makes no model call.

---

## 19. The daily-use UX (2026-10-12)

**Bottom line.** The CRM reads its data correctly, but a sales rep cannot yet
work in it all day. A rep cannot search or page a list. The rest of Metorite
cannot find a CRM record. A card does not say what to do next. Most of the fix
works in Mirror mode, because it reads data or adds data that only Metorite
holds.

**Where this comes from.** A read-only UX audit of `origin/main` `068cbdc29`,
done on 2026-10-12. It compared the CRM with Zoho CRM, Pipedrive, Attio, Close,
HubSpot and folk. The audit note stays outside the repository, because it is a
working note. This section checked each anchor of the audit against the code
again, and §19.1 says where the audit was wrong.

**Paths.** A path that starts with `app/crm/` is under
`workbench/control_plane/src/`. A path that starts with `routes/` or `crm_` is
under `apps/services/gateway/gateway/`.

**Rules for every slice of this section.**

- Each UI slice passes the `visual-review` skill (§8.4): light mode, compact
  density, a changed accent, four widths, and next to Projects.
- Each slice lowers the `conformance.test.ts` baselines that it improves.
- A Projects component moves to `src/components/` before the CRM uses it. No
  app imports from another app.
- No CRM palette, no ⌘K listener, no bell and no assistant rail. AGENTS rule 10
  forbids them, and `src/lib/shell/seams.test.ts` fences it.
- Each new R8 suite sets `pytestmark = _DB_GATE`, as
  `tests/unit/test_crm_related_r8.py:29` does. Without the gate, the suite
  errors in `deploy.yml`, which has no ladder database.

### 19.1 Findings

**F1: No search box.** The URL grammar reads `?q=`
(`app/crm/lib/urlState.ts:175`), and `listQuery` sends it
(`app/crm/lib/filters.ts:93`). But `components/FilterBar.tsx` draws no text box,
so a member can search only through the URL. The gateway matches `?q=` with
ILIKE over `Entity.search` (`routes/crm/core.py:785-788`). A deal matches on
`name` and `next_step` only (`core.py:552`), so it does not match the name of
its company. No entity matches a phone number (`core.py:486`, `506`, `529`).

**F2: No paging.** `listQuery` asks for page 1 of 50 (`filters.ts:83-87`), and
`loadList` replaces the rows (`app/crm/lib/store.ts:334-338`). The footer then
reads "50 of 1500" and gives no way to the next page
(`components/RecordList.tsx:217`). The gateway already takes `page`, and
`page_size` up to 100 (`routes/crm/records.py:96-97`). A board lane stops at 50
cards and says "showing 50" (`components/KanbanBoard.tsx:154`).

**F3: The Deals list hides the stage.** The Deals columns are Deal, Company,
Amount, Closing and Owner (`app/crm/lib/columns.ts:46-61`). The Leads list has
no Status column. `SORTS` holds "Last touched" for each entity
(`filters.ts:205`, `212`, `218`, `223`). No column offers it, so no member can
reach that sort.

**F4: The filter of owners forgets people.** `ownerOptions` builds its list from
the rows on screen (`app/crm/lib/filterBar.ts:37-54`). An owner with no row on
page 1 is not in the list.

**F5: The rest of Metorite cannot see the CRM.** The search of the command bar
has three providers and none for the CRM (`routes/shell/search.py:120-132`).
"Needs you" has four sources and none for the CRM
(`routes/shell/needs.py:405-410`). The CRM has one of the four jobs that §11
names, `new-lead` (`src/lib/shell/registry.ts:130-138`,
`routes/shell/intent.py:118-119`). The `/crm` pane names no `agent`
(`src/lib/nav.ts:336-348`). That field feeds the run badge
(`src/lib/runActivity.ts:24-32`), so a run of `crm-assistant` counts on Chat.

**F6: A card does not say what to do next.** A deal card shows its name,
company, amount, stage age and owner (`components/KanbanBoard.tsx:210-288`). It
shows no next activity and no last touch. The rot colour needs
`max_dwell_days` (`app/crm/lib/board.ts:482-496`). The Zoho sync never writes
that column, and the stage grid is read-only in Mirror mode. So rot never shows
for Fracktal.

**F7: The record page is a list of fields.** The header holds the title, the
entity noun and "Open in Zoho" (`components/RecordSheet.tsx:106-133`). It has no
stage, amount, owner or close date, and no tabs. The timeline draws no due date
for a task, although each activity carries `due_at`
(`routes/crm/activities.py:69`). Three controls are hand-rolled. They are an
`<input>` at `components/FieldsPanel.tsx:346`, a `<textarea>` at
`components/Timeline.tsx:101` and an `<input>` at `Timeline.tsx:112`.

**F8: The raw controls have no fence.** AGENTS rule 3 forbids a hand-rolled
control. `conformance.test.ts` counts `<select>` tags and file inputs, but no
rule counts a plain text `<input>` or a `<textarea>`. So the three controls of
F7 pass today.

**F9: Phone and email are not links.** No CRM file writes a `tel:` link. A
custom email field becomes a `mailto:` link (`app/crm/lib/fields.ts:118-122`).
The fixed `email` field of a lead or a contact stays as plain text.

**F10: Nothing measures progress, and nothing celebrates.** Reports are four
static blocks over a fixed window of 90 days (`routes/crm/reports.py:93`). They
have no period picker, no drill-down and no chart. The CRM has no target, no
win moment and no streak.

**F11: The research changes the brief in three places.**

- **Zoho retired Gamescope on 2021-11-30.** Motivator replaced it, with KPIs,
  targets, games and trophies. So Zoho parity means targets and KPIs, not
  badges.
- **Attio's win celebration is opt-in, for each stage.** A Kanban setting turns
  confetti on for one stage at a time. It is the calmest win pattern that a
  vendor documents, and it is the precedent for CRM-X6.
- **No vendor has activity streaks.** The audit found no streak feature at
  Zoho, Pipedrive, Attio, Close, HubSpot or folk. A streak is our own idea, not
  parity.

**F12: The research behind CRM-X3.** Pipedrive draws the next activity on each
deal card. The colour is red when it is overdue, green for today, grey for
later and yellow for none. Pipedrive also tints a deal red after the idle days
of its stage. CRM-X3 copies the first rule with the colours of the house.
CRM-I1 builds the second rule in Mirror mode (UX-Q5).

**Where the audit was wrong.** The check against the code found five errors.
This section uses the corrected facts.

1. **The assistant dock does not exist.** The audit says that `agent` lets the
   shell dock offer the CRM assistant. The dock is NS-6 slice 6b, and it is not
   built (`src/lib/shell/dockFlag.ts`). Today `agent` drives the run badge only.
2. **A job id has no dot.** The audit names `crm.new_deal`, `crm.log_call` and
   `crm.find_record`. The code names jobs in kebab case (`new-lead`), and the
   CRM-L1 row names `new-deal`, `log-call` and `find-record`. §11 also writes
   `crm.new_lead`, and the code calls that job `new-lead`.
3. **A pane has no `keywords` field.** The audit adds keywords to the `/crm`
   pane. `NavPane` in `src/lib/nav.ts` has no such field. The command bar reads
   the `words` of each job in `src/lib/shell/registry.ts`.
4. **`mailto:` exists in part.** The audit says no CRM field is a mail link. A
   custom email field already is one (F9). Only the fixed fields and `tel:` are
   missing.
5. **CRM-Q5 is built.** The audit makes CRM-X3 wait for CRM-Q5. CRM-Q5 merged on
   2026-10-12. Only the owner's reconnect of Fracktal's Zoho (§12) is open.

### 19.2 The slices

The order is the impact on the day of a Fracktal rep in Mirror mode. One slice
is one PR. S is up to 1 day, M is 2 to 4 days, and L is a week. "Mirror? Y"
means that the slice works in Mirror mode with no write-back (CRM-Z9).

| Id | What the user gets | Gate | Size | Waits for | Mirror? |
|---|---|---|---|---|---|
| **CRM-X1** Find and page | A search box, "Load more", a Stage column, a "Last touched" column, and an owner filter that lists every member | AGENT-SAFE | S | Nothing | Y |
| **CRM-X2** The CRM joins the shell | Cmd-K finds a lead, a deal, a contact or a company. Three new jobs: New deal, Log a call and Find a record. The run badge counts the CRM assistant on the CRM | AGENT-SAFE | M | CRM-X1, because two jobs open its search box | Y |
| **CRM-X3** Next activity on each card | Each card shows its next activity: overdue, today, later or none. An overdue card has a red edge. A deal lane can sort by next activity | AGENT-SAFE | M | Nothing in code. Zoho tasks show after the reconnect of §12 | Y |
| **CRM-X4** Record page v2 | A summary header, four tabs, a timeline filter, task due dates, `j` and `k` through the list, and phone and email links | AGENT-SAFE | M | Nothing | Y |
| **CRM-S1** Follow-ups in the one task store | A follow-up is a `pm_tasks` row with a link to its record. It shows in My Tasks and Calendar, and one tick completes it everywhere | AGENT-SAFE (the §6 row) | L | A shape of the entity link that the owners of the task store agree | Y |
| **CRM-X5** Activities | A rail row with my overdue, today and upcoming activities, each with its record | AGENT-SAFE | M | CRM-S1, for Metorite tasks | Y |
| **CRM-I1** Pipeline health and "Needs you" | A health strip: stale deals, slipped close dates, no next step, no touch in 14 days, closing this month. My stale deals reach "Needs you". An admin sets idle days for each stage in Mirror mode too (UX-Q5) | AGENT-SAFE in the app. A push or a mail waits for UX-Q4 | M | CRM-X3 | Y |
| **CRM-X8** A board you can shape | Card fields, a choice of lane sum, "Load more" in a lane, a lane that folds, and one summary lane for won and lost deals | AGENT-SAFE | M | CRM-Q8 (built) | Y |
| **CRM-U3 and CRM-U4** Fast, and one look | A revisit paints at once. Skeletons, empty states with a way out, the shared lane colours, a flash where a card lands, and undo after a Native move | AGENT-SAFE | M | Nothing | Y |
| **CRM-X0** Setup, connection and start over (owner report, §19.11) | One setup hub. Reconnect keeps the records. A change that needs an empty CRM offers a download, then drops the records | AGENT-SAFE | M | CRM-Q6a | Y |
| **CRM-I2** Targets and the forecast by month | A target for each member, the forecast by month against it, "₹X to go", and a Home card | AGENT-SAFE (UX-Q2 answered) | M to L | Nothing | Y |
| **CRM-X6 and CRM-X7** Win moments and streaks | A calm toast and a flash when a deal is won. A private count of working days with a logged call, meeting or note | AGENT-SAFE (UX-Q1 answered) | S and M | CRM-I2 for "% of target". CRM-S1 or CRM-X5 for the counts | Y |
| **CRM-I3** Deal brief | A "Brief" button that writes 4 to 6 lines about one deal and suggests the next step | OWNER-GATE: UX-Q3 | M | The AI meter (H-171) | Y |
| **CRM-X9** Keyboard | A row cursor with `j` and `k`, Enter to open, keys that compose in the sheet, and a `?` sheet of shortcuts | AGENT-SAFE | S to M | CRM-X1, CRM-X4 | Y |
| **CRM-X10** Mobile | One lane at a time with a lane picker, lists as cards on a phone, "Log this call" after a tap to call, and a composer that stays in view | AGENT-SAFE | M | CRM-X4 | Y |
| **CRM-X11** Reports you can click | A period picker, a compare with the period before, charts on `LiveChart`, and a bar that opens the deals behind it | AGENT-SAFE. A scheduled send waits for UX-Q4 | M | CRM-X1 | Y |
| **CRM-Q13** Tags, and Follow | Zoho tags as chips and as a filter. Follow a record, and its changes reach "Needs you" | AGENT-SAFE. A new Zoho scope is an owner act (§12) | M | CRM-I1 | Y |
| **CRM-X12** The CRM in the Email app | A side card for a mail from a known contact: the person, the open deals and the next activity | AGENT-SAFE | M | CRM-X2 | Y to read. "Create lead" is Native only |
| **CRM-X13** Onboarding checklist | After the connect: set idle days, save a view, schedule a follow-up, set targets and try Cmd-K. A progress bar that a member can close | AGENT-SAFE | S | CRM-X3, CRM-S1, CRM-I2 | Y |
| **CRM-I6** Data quality | A score for each entity: no owner, no close date, no contact on a deal, and likely duplicates. Each count opens a list | AGENT-SAFE | S to M | CRM-X1 | Y. The fix happens in Zoho |
| **CRM-I4** Weekly digest | A Monday summary of the pipeline, by email or WhatsApp | OWNER-GATE: UX-Q3 and UX-Q4 | M | CRM-I1, CRM-I2 | Y |
| **CRM-Q6b** Products and Quotes | Rail rows and generic record pages for the mirrored modules | AGENT-SAFE. The new consent is an owner act (§12) | M | CRM-Q6a (§18.7) | Y |
| **CRM-X14** Win likelihood | A score from the stage, the age, the last activity and slipped dates, with its reasons. No model | AGENT-SAFE | M | CRM-X3, CRM-I1 | Y |
| **CRM-Z9** Zoho write-back | A move, an edit or a new record in Metorite writes to Zoho, for an org with the flag on | AGENT-SAFE to build, with the flag off. Turning it on for an org is the owner's act (UX-Q6, §12). Needs its own spec audit before dispatch | L | Its spec audit. The consent note is §19.9 | It is the write-back |

The first 22 rows are the slices of the audit. The owner's answer to UX-Q6
added CRM-Z9.

CRM-X2 builds the job half and the search half of CRM-L1 (§13.6). The needs
half of CRM-L1, with stale deals in "Needs you", belongs to CRM-I1.

### 19.3 The slices that need write-back or Native mode

These slices change records. For Fracktal they wait for CRM-Z9 and its flag
(UX-Q6). For a Native
customer they are the next build after §19.2.

| Slice | What the user gets | Waits for |
|---|---|---|
| Board drag in Mirror mode | Drag a card, and Metorite writes the stage to Zoho | CRM-Z9 |
| Inline edit in lists | Edit a cell, with Tab and Enter to move | CRM-X9, and CRM-Z9 or Native mode |
| Bulk bar (CRM-S4) | Select rows, then change the owner, stage, close date or tag, or delete, with undo | CRM-X9. Move `app/projects/components/BulkBar.tsx` to `src/components/` first |
| Quick add in a lane | "+ Add" at the foot of a lane | Native mode |
| Quick create with lookups | Company and contact pickers on a new deal, "Create another", and a duplicate warning | CRM-S5, for the warning |
| Delete with undo | Delete in the record menu, then an Undo toast | Native mode |
| CSV import (CRM-S3), merge (CRM-S5), field editor (CRM-S6), web forms (CRM-S8), field history (CRM-S10) | The §6 rows | Native mode |
| Stage transition forms | The move dialog gains a checklist and optional fields for each transition | Native mode |
| Cadences | Sequences of mail, calls and tasks that stop on a reply | UX-Q4, because a cadence sends mail |

### 19.4 Owner answers and open questions

**Owner answers (2026-10-12, in chat).** These four answers bind. Each one
names the question as the audit asked it.

| Question | Answer | Effect |
|---|---|---|
| **UX-Q1.** Win moments and streaks. Is a win moment on by default? Does the team see a win, or only the owner of the deal? Is a streak private? | **Calm.** The deal owner sees a short toast and a card flash. The team sees a "wins this week" line. A streak is private to its member. No confetti by default | CRM-X6 and CRM-X7 become AGENT-SAFE. The gateway never sends one member's streak to another member |
| **UX-Q2.** Targets. Monthly or quarterly? For each member or each team? Who sets a target, and who sees the progress of the others? | **Monthly, for each member.** An admin sets a monthly ₹ target for each rep. Everyone sees the team total. Each rep sees their own progress | CRM-I2 becomes AGENT-SAFE. Its `crm_targets` row holds one member and one month. The report route gives a rep the team total and their own row only |
| **UX-Q5.** Idle days for each stage in Mirror mode. May an admin set `max_dwell_days` while Zoho owns the stages? | **Yes, as a Metorite-only setting.** The limit lives only in Metorite and never writes to Zoho. A stale deal shows its colour and reaches "Needs you" | CRM-I1 builds it. It unlocks that one column in `components/PipelineSettings.tsx` and in the mirror write guard, and `test_crm_mirror_readonly.py` names the column. No Zoho write path reads it |
| **UX-Q6.** Zoho write-back for Fracktal (CRM-Z9, §12) | **Build it, flag off.** The flag defaults to off. The owner checks write-back on a test deal, then turns it on for Fracktal | CRM-Z9 is AGENT-SAFE to build. Turning the flag on for an org stays the owner's act (§12). The consent note is §19.9. CRM-Z9 needs its own spec audit before dispatch |

**Still open.** An agent builds no OWNER-GATE part of §19.2 before the owner
answers. Each recommendation is the audit's.

1. **UX-Q3. AI credits.** The deal brief (CRM-I3) and the digest (CRM-I4)
   spend credits. Which plan pays for them? *Recommendation:* the audit makes
   none. Q7 (§16) already puts AI insights on credits. The meter (H-171) must
   count each call before either slice ships (§9).
2. **UX-Q4. Anything that reaches a person outside the app.** A mail or a
   WhatsApp digest, a push for a stale deal, a scheduled report, or a cadence.
   *Recommendation:* the "Needs you" feed in the app is passive, and it fits
   CRM-S11, so it needs no decision. Each outward channel waits for the owner.

The reconnect of Fracktal's Zoho is an act, not a question. §12 already holds
it. Zoho tasks reach CRM-X3 and CRM-X5 only after it.

### 19.5 CRM-X1: Find and page

**Scope.**

1. A search box at the start of the filter bar. It writes `?q=` 300 ms after
   the last key.
2. "Load more" under each list. It adds the next page of the same view.
3. A Stage column on the Deals list, and a Status column on the Leads list.
4. A "Last touched" column on all four lists, with its sort.
5. An owner filter that lists every active member, and each owner that a
   record names.
6. The gateway search matches the company name of a deal, and a phone number.

**Non-goals.**

- "Load more" in a board lane (CRM-X8).
- A filter with more than one value of a picklist (CRM-X8).
- An infinite scroll or a virtual list.
- A search over custom fields.
- A phone search that ignores spaces and dashes. A number matches as the record
  holds it.
- A row cursor (CRM-X9).

**The gateway.**

- `routes/crm/core.py`, `Entity`: add `search_sql: tuple[str, ...] = ()`.
  `list_contract` adds each item to the OR group of the `search` terms
  (`core.py:785-788`). Each item is a constant of this module, never caller
  text.
- DEALS gets one item:
  `company_id IN (SELECT id FROM crm_companies WHERE name ILIKE :q)`. FORCE RLS
  keeps the inner read in the tenant of the session.
- `search` gets wider. CONTACTS adds `phone` and `mobile`. ORGANIZATIONS adds
  `phone`. LEADS adds `phone` and `mobile`.
- DEALS and LEADS project `status_name`, the name of the stage or status.
  DEALS adds `("status_name", "st.name")` to `joined_columns`, and
  `LEFT JOIN crm_deal_statuses st ON st.id = base.status_id` to `join_sql`.
  LEADS gets the same pair on `crm_lead_statuses`.
- `DealModel` and `LeadModel` gain `status_name: str | None = None`. The list,
  the board (`routes/crm/pipeline.py:494-509`) and the CSV
  (`routes/crm/export.py:326`) all read through `project_joined`. So all three
  carry the field.
- `routes/crm/export.py`, `COLUMNS`: the same new columns as `columns.ts`, in
  the same order.

**The route `GET /crm/owners`.** It goes in `routes/crm/records.py`, on the
CRM router, so the kill switch, `feature:crm` and the access rule hold.

```json
{ "owners": [ { "email": "priya@fracktal.in", "name": "Priya", "member": true } ] }
```

- It returns each active member of the tenant from `app_user`, with the
  display name.
- It adds each distinct `lower(owner_email)` of the four CRM tables that no
  member holds, with `name: null` and `member: false`.
- It sorts by name, then by email, and it stops at 500. It runs in one
  `_tenant_session()`.
- **Why a CRM route.** The member list of Chat (`routes/rooms.py:897`) needs
  `feature:chat`. It also cannot list a Zoho owner who is not a member.

**The UI.**

- `components/FilterBar.tsx`: the box is `Input` from
  `src/components/ui/Input.tsx`. Its placeholder word comes from `filterWord()`
  (`src/lib/shell/registry.ts`).
- The box carries `data-page-filter="<entity label>"`. Then `/` focuses it, and
  "Show all in …" in the command bar fills it (`src/lib/shell/pageFilter.ts`).
- `app/crm/lib/store.ts`: `loadList` sets `page: 1`. A new `loadMore` asks for
  `page + 1` with the same `listQuery`. It adds only the rows whose `id` is not
  on screen, through a pure `appendPage` in `app/crm/lib/filters.ts`.
- A change of the view sets the page to 1. An answer for an old view is
  dropped, with the guard that `loadRelated` uses (`store.ts:378-406`).
- `components/RecordList.tsx`: the footer reads "<shown> of <total>". A
  `Button` "Load more" shows while fewer rows show than `total`.
- `app/crm/lib/columns.ts`: Deals add `status_name` ("Stage") after Deal, and
  `last_activity_at` ("Last touched", sort `last_activity_at`) at the end.
  Leads add `status_name` ("Status"). Contacts and Companies add "Last
  touched".
- `RecordList.tsx` draws the stage name with the dot of `statusTone`
  (`app/crm/lib/board.ts:67`). That is the one stage tone of the CRM until
  CRM-U3. It draws "Last touched" with `relativeTime`
  (`src/lib/taskCard.ts:247`).
- `app/crm/lib/filterBar.ts`: `ownerOptions` reads the `GET /crm/owners` list,
  not the rows. It keeps "Anyone", "Me", and a chosen owner that the list does
  not hold. `app/crm/lib/api.ts` adds `listOwners()` through the existing
  proxy `app/api/crm/[...path]`.

**Done when.**

1. `GET /crm/deals?q=<part of a company name>` returns the deals of that
   company.
2. `GET /crm/contacts?q=<digits of a phone>` returns that contact.
3. A search in org B returns none of org A's rows, also through the company
   subquery.
4. Page 2 at `page_size=50` holds rows 51 to 100 in the same order, and no id
   of page 1.
5. `GET /crm/owners` holds each active member and each owner who is not a
   member, for the caller's tenant only. A member that the access rule refuses
   gets 403.
6. `status_name` holds the stage name on deals and leads, in the list, on the
   board and in the CSV.
7. `appendPage` drops a row whose `id` is already on screen.
8. `ownerOptions` lists an owner who holds no row on page 1.
9. The search box writes `?q=` once after a pause of 300 ms, and Back gives the
   last query back. The `visual-review` skill checks it.

**Fences.**

- `tests/unit/test_crm_find_and_page_r8.py`, new, R8: items 1 to 6.
- `tests/unit/test_crm_export.py`, existing: the list and the CSV keep one
  column set, and each exported key is a field of its model.
- `app/crm/components/crmX1.test.ts`, new: items 7 and 8, and the page that
  `listQuery` sends.

### 19.6 CRM-X2: The CRM joins the shell

**Scope.**

1. A `crm` provider for the search of the command bar, in
   `routes/shell/search.py`.
2. Three jobs in both job lists: `new-deal`, `log-call` and `find-record`.
3. `agent: "crm-assistant"` on the `/crm` pane of `src/lib/nav.ts`.
4. An icon for each CRM kind in `FIND_ICONS` (`src/lib/shell/CommandBar.tsx:51`).

**Non-goals.**

- A palette or a ⌘K listener inside the CRM. AGENTS rule 10 forbids it, so
  Cmd-K means the shell's command bar.
- A new key in `SEAM_DEBT`.
- The `crm` provider of "Needs you". Stale deals, my deals and a lapsed consent
  belong to CRM-I1.
- The assistant dock, which is not built (§19.1).
- A search over activities or mail. A job that saves a record.

**The search provider.**

- `PROVIDERS["crm"] = ("crm", "CRM", _crm)`, after the three providers of
  today. `SCOPE_APPS["/crm"] = "crm"`, so the CRM group comes first inside the
  CRM.
- The loop already tests `feature:crm`. `_crm` then applies the two other gates
  of the CRM router, in the order of the router:
  - `crm_org_allowed(user.organization_id)` (`routes/crm/flags.py:76`). When it
    is false, `_crm` returns `[]`.
  - `await require_crm_access(user)` (`routes/crm/access.py:98`). Its 403 is an
    `HTTPException`, and the loop drops the group (`search.py:168-171`).
- `_crm` writes no SQL. It calls a new `search_records(user, q, limit)` in
  `routes/crm/records.py`.
- `search_records` runs `list_contract` and `run_list` for each of the four
  entities, in one `_tenant_session()`. It reads `limit` rows of each, with the
  converted-lead rule of `_list` (`records.py:153-164`).
- It merges the rows by `last_activity_at`, newest first, and returns the
  first `limit` (`PER_APP`, 5).
- ⚠️ `search_records` does not build a `ListParams`. The class declares its
  defaults with `Query(...)`, so a direct call that leaves out an argument
  passes the `Query` object (the same trap as `search.py:79-81`).

| Kind | Title | Hint | Link |
|---|---|---|---|
| `lead` | `lead_name` | "Lead · " and the company, or else the email | `/crm?tab=leads&lead=<id>` |
| `deal` | `name` | "Deal · " and the company | `/crm?tab=deals&deal=<id>` |
| `contact` | first and last name | "Contact · " and the email | `/crm?tab=contacts&contact=<id>` |
| `company` | `name` | "Company · " and the industry | `/crm?tab=organizations&organization=<id>` |

No id reaches the eye of the member. The id travels only inside the link, as
the module note of `search.py` says.

**The jobs.** Each goes into `JOBS` of `src/lib/shell/registry.ts` and of
`routes/shell/intent.py`, with `feature="crm"` and `admin=False`.

| Id | Label | Meaning for the coordinator | Link | Mirror mode |
|---|---|---|---|---|
| `new-deal` | New deal | add a new deal or sales opportunity to the CRM | `/crm?do=new-deal` | The refusal toast of `new-lead` (`app/crm/page.tsx:860-867`) |
| `log-call` | Log a call | log a phone call with a lead, a contact or a customer in the CRM | `/crm?do=log-call` | Works, because a call is Metorite-only data |
| `find-record` | Find a record | find a lead, a deal, a contact or a company in the CRM | `/crm?do=find-record` | Works |

- `new-deal` opens `QuickCreateModal` for a deal, as `new-lead` does for a
  lead.
- `log-call` with an open record opens the Timeline of that record, with the
  composer set to Call.
- `log-call` with no open record opens the Deals list. It focuses the search
  box of CRM-X1, and it shows the hint "Open the record to log the call on".
- `find-record` opens the list of the current entity and focuses the search
  box.
- `app/crm/page.tsx` mounts one `ShellJob` for each job, beside the two
  `new-lead` mounts (`page.tsx:860`, `page.tsx:1045`). A job saves nothing.

**Done when.**

1. A member with the CRM types part of a deal name. The answer holds a CRM
   group with that deal, and its link opens the record sheet.
2. A member without `feature:crm` gets no CRM group, and `search_records` is
   not called.
3. A member of an org outside `CRM_ORGS` gets no CRM group.
4. A member that the access rule refuses gets no CRM group.
5. A member of org B never gets a record of org A.
6. With `scope=/crm`, the CRM group comes first.
7. A converted lead does not show, as on the Leads list.
8. When the CRM provider fails or runs out of time, the other groups still
   answer.
9. The three jobs are in both lists. `TestOneJobList` and
   `test_auth_me_shell.py` pass.
10. `appForAgent("crm-assistant")` returns `/crm`.
11. `seams.test.ts` passes, and `SEAM_DEBT` has no new key.

**Fences.**

- `tests/unit/test_shell_search.py`, existing, wider: items 2, 3, 4, 6 and 8,
  with the fakes of the file.
- `tests/unit/test_shell_search_crm_r8.py`, new, R8: items 1, 5 and 7, on the
  pattern of `test_shell_search_email_r8.py`.
- `tests/unit/test_shell_intent.py::TestOneJobList` and
  `tests/unit/test_auth_me_shell.py`, existing: item 9.
- `src/lib/runActivity.test.ts`, existing: the table gains
  `["crm-assistant", "/crm"]` (item 10).
- `src/lib/shell/seams.test.ts`, existing: item 11.

### 19.7 CRM-X3: Next activity on each card (✅ built 2026-10-12)

**Scope.**

1. Each deal and each lead that a list or a board reads carries
   `next_activity`.
2. Each deal card and each lead card shows one chip: overdue, today, later or
   none.
3. An overdue card has a red left edge, from the shared status colours.
4. A deal lane sorts by stage age, as today, or by next activity.

**Non-goals.** Idle days in Mirror mode (CRM-I1 builds them, UX-Q5). A card tint for rot, and the
move to `TaskCardShell` (CRM-U3). Follow-ups in `pm_tasks` (CRM-S1 extends the
same function). A sort of a lead lane (CRM-X8). A "Next activity" column in a
list.

**What counts as the next activity.** One rule, held in one SQL constant,
`NEXT_ACTIVITY_SQL`, in `routes/crm/core.py`:

- A row of `crm_activities` on the record, through the column that
  `Entity.activity_column` names (`deal_id` or `lead_id`).
- `completed_at IS NULL`.
- Either `type = 'task' AND due_at IS NOT NULL`, or
  `type IN ('call', 'meeting') AND occurred_at > now()`.
- The next activity is the row with the smallest
  `COALESCE(due_at, occurred_at)`. A tie goes to the smaller `id`.

An open task with no due date does not count. A past call or meeting is
history, not a next step.

**Zoho tasks and native tasks count alike.** CRM-Q5 writes a Zoho task as a
`crm_activities` row with `type='task'` and `due_at` from `Due_Date`
(`crm_sources/zoho/mapping.py:388-399`). The composer writes a native task with
the same two columns. So the rule has no branch on `ext_source`. A Zoho task
that Zoho marks Completed has `completed_at`, so it drops out. A Zoho meeting
counts when its `Start_DateTime` is in the future (`mapping.py:402-406`).

**One query, not one for each card.** `attach_next_activity(db, entity, rows)`
goes in `routes/crm/core.py`, beside `attach_ext_urls`. For deals and leads it
runs this one statement for all the rows it gets:

```sql
SELECT DISTINCT ON (a.deal_id)
       a.deal_id AS record_id, a.id, a.type, a.subject,
       COALESCE(a.due_at, a.occurred_at) AS at,
       (a.type = 'task') AS all_day
  FROM crm_activities a
 WHERE a.deal_id = ANY(CAST(:ids AS uuid[]))
   AND <NEXT_ACTIVITY_SQL>
 ORDER BY a.deal_id, COALESCE(a.due_at, a.occurred_at), a.id
```

- The column comes from `Entity.activity_column`, never from caller text. The
  index `idx_crm_activities_deal_id_created_at` serves `deal_id = ANY`
  (`infra/postgres/144_crm.sql:442-445`). The lead index is the next one.
- Each row gets `next_activity`: `{id, type, subject, at, all_day}`, or `null`.
  Contacts and companies get nothing.
- `run_list` (`core.py:1091-1106`) calls it for deals and leads, after
  `attach_ext_urls`. The Leads board reads one list for each status
  (`store.ts:262-284`), so it costs one more statement for each lane, never
  for each card.
- `get_pipeline` (`routes/crm/pipeline.py:409`) collects the card ids of every
  lane and calls it once, after the lane loop. So a read of the board adds one
  statement, whatever the count of lanes and cards.
- The CSV export does not call it.

**The lane sort.** `GET /crm/pipeline` takes `card_sort`:

| Value | Order of the cards in a lane |
|---|---|
| `stage_age` (the default) | `status_changed_at DESC NULLS LAST, id DESC`, as today |
| `next_activity` | The soonest next activity first, then the cards with none |

- Any other value gives 422.
- For `next_activity`, the lane read selects the smallest `at` of the rule as
  `next_at`, and orders by `next_at ASC NULLS LAST, id DESC`. The outer order
  of `project_joined` uses `base.next_at`.
- The count, the amount and the weighted sum of a lane do not change.
- The UI keeps the choice for each member in local storage, with the key shape
  of `app/crm/lib/emptyStages.ts`.

**The chip.** A pure `nextActivityChip(next, laneType, now)` in a new
`app/crm/lib/nextActivity.ts` returns a `MetaChip` (`src/lib/taskCard.ts:50`).

| State | When | `MetaTone` | Text |
|---|---|---|---|
| Overdue | A task whose due day is before today | `danger` | "Overdue" and the days late |
| Today | A task due today, or a call or meeting today | `accent` | "Today" and the subject |
| Later | After today | `muted` | The short date and the subject |
| None | `next_activity` is null | `warning` | "No next step" |

- A card in a `won` or `lost` lane shows no chip.
- **The day rule.** A task's `due_at` holds a date at 00:00 UTC. The Zoho sync
  (`mapping.py:397`) and the composer (`Timeline.tsx:75`) both write it so. So
  for `all_day`, the rule compares the UTC date of `at` with the local date of
  the member. For a call or a meeting, it compares local dates.
- The day rule is one function, `dueDay(at, allDay, now)`, in
  `app/crm/lib/format.ts`. The first of CRM-X3 and CRM-X4 to merge adds it,
  and the other imports it.
- `components/KanbanBoard.tsx` draws the chip with `TaskMeta`
  (`src/components/TaskMeta.tsx:128`).
- An overdue card adds `border-l-2` and `accentForHue("red").bar`
  (`src/lib/statusAccent.ts:291`). The CRM adds no class map of its own
  (`sharedTaskUi.test.ts`).

**Done when.**

1. A deal has an open task due yesterday, one due tomorrow and a completed
   task. It gets the task due yesterday.
2. A future meeting before every task due date wins. A past meeting does not
   count.
3. A Zoho task (`ext_source='zoho'`) and a native task both count, and the code
   has no branch on the source.
4. A deal with only an undated task gets `null`.
5. One `GET /crm/pipeline` over 3 lanes and 40 cards issues one statement that
   reads `crm_activities`. A statement listener on the test engine counts it.
6. `card_sort=next_activity` orders a lane soonest first, then the cards with
   none. An unknown `card_sort` gives 422. The lane totals do not change.
7. The rows of `GET /crm/leads` carry `next_activity`, through `lead_id`.
8. An activity of org B never sets the `next_activity` of an org A record.
9. `nextActivityChip` gives each of the four states. A task due today reads
   "Today", not "Overdue", for a member in UTC+05:30. A card in a won lane gets
   no chip.

**Fences.**

- `tests/unit/test_crm_next_activity_r8.py`, new, R8: items 1 to 8.
- `app/crm/lib/nextActivity.test.ts`, new: item 9.
- `src/lib/sharedTaskUi.test.ts`, existing: no new tone map in `app/crm/`.
- `tests/unit/test_crm_mirror_readonly.py`, existing: the slice writes
  nothing.

### 19.8 CRM-X4: Record page v2 (✅ built 2026-10-12)

**Scope.**

1. **A summary header** under the title, from the record that the sheet holds:
   - Deal: the stage (`StatusPill`), the amount, the owner and the close date.
   - Lead: the status (`StatusPill`), the company and the owner.
   - Contact: the title, the company, the email and the phone.
   - Company: the industry, the phone and the owner.
2. **Four tabs**, drawn with `Tabs` (`src/components/Tabs.tsx:84`,
   `chrome="inline"`):
   - **Overview**: the fixed fields of `FieldsPanel`, the status pill, the
     lead's Convert, and the next 3 open activities from the timeline.
   - **Timeline**: the composer and the stream.
   - **Related**: the contacts of a deal with their roles, and the lists of
     `RelatedLists`. A lead has no Related tab.
   - **Fields**: the custom fields of `crm_field_defs`.
   - `?rtab=` holds the tab. An unknown value opens Overview.
3. **A timeline filter**: All, Notes, Calls, Meetings, Tasks, Emails and Stage
   changes. A pure `filterTimeline(entries, kind)` in `app/crm/lib/timeline.ts`
   works on the entries that the sheet holds, through `timelineBranch`.
4. **Task due dates.** A task shows its due date. An open task due before
   today shows an "Overdue" chip with the tone `danger`, through `dueDay`
   (§19.7).
5. **`j` and `k`.** `j` opens the next record of the list that the member came
   from, and `k` the one before.
   - The order is the rows of the list on screen, or the cards of the lane on
     a board.
   - A pure `neighbour(ids, id, step)` in a new `app/crm/lib/recordNav.ts`
     finds the id. At either end it gives `null`, and the key does nothing.
   - The keys do nothing while `isTypingTarget` is true
     (`src/lib/keyboard.ts:24`).
   - The step replaces the sheet parameter (`router.replace`), so Back closes
     the sheet.
   - The header shows "3 of 50" and two arrow `Button`s for a mouse and a
     touch.
   - A record opened from a link, from Cmd-K or from a related list has no list
     behind it. Then the keys do nothing, and the arrows do not show.
6. **`tel:` and `mailto:` links.**
   - `formatField` in `app/crm/lib/fields.ts` gains a `tel` kind for the type
     `phone`.
   - The `href` keeps only digits and a leading `+`. A value with fewer than 6
     digits stays text.
   - The fixed `email`, `phone` and `mobile` fields of `FieldsPanel` use the
     same rules, with the `isEmail` guard for `mailto:`.
   - `components/FieldValue.tsx` draws the new kind.
7. **No raw control.** These three controls go onto the primitives of
   `src/components/ui/Input.tsx` (design rule 3):
   - `FieldsPanel.tsx:346` becomes `Input` (`Input.tsx:96`).
   - `Timeline.tsx:101` becomes `Textarea` (`Input.tsx:152`).
   - `Timeline.tsx:112` becomes `Input` with `type="date"`.

**Non-goals.**

- A docked panel in place of the drawer (§8.1). The sheet stays a `Modal` with
  `end`, and `sheet` on a phone.
- The Tasks tab of §8.1. This slice ships a Fields tab in its place. The Tasks
  tab comes with CRM-S1 and CRM-X5, when tasks live in `pm_tasks`.
- Ctrl+Enter in the composer, the edit or delete of a note, and the keys that
  compose (CRM-X9).
- Days in stage and the next activity in the header (later, on the data of
  CRM-X3).
- Field history (CRM-S10), the deal brief (CRM-I3), and `j` and `k` in a list
  (CRM-X9).

**Files and seams.** No gateway change, so no R8 suite.

- `app/crm/components/RecordSheet.tsx`, `FieldsPanel.tsx`, `Timeline.tsx`,
  `RelatedLists.tsx` and `FieldValue.tsx`.
- `app/crm/lib/timeline.ts` (`filterTimeline`), `fields.ts` (`tel`),
  `urlState.ts` (`rtab`), `format.ts` (`dueDay`), and the new `recordNav.ts`.
- `app/crm/page.tsx` passes the order of the list and the step handler to the
  sheet.
- Shared parts it uses and does not change: `src/components/Tabs.tsx`,
  `src/components/ui/Input.tsx`, `src/components/ui/Button.tsx` and
  `src/lib/keyboard.ts`.

**Done when.**

1. No file under `app/crm/` holds a raw `<input`, `<textarea` or `<select`
   tag, except a hidden file input.
2. `?rtab=timeline` opens the Timeline tab, and an unknown value opens
   Overview. A lead shows no Related tab.
3. `filterTimeline` keeps only the chosen kind. "All" keeps every entry, an
   entry of an unknown kind too.
4. A task shows its due date. An open task due before today shows "Overdue". A
   task due today does not, for a member in UTC+05:30.
5. `neighbour` gives the next id and the id before. It gives `null` at each
   end and for an id that the list does not hold.
6. `formatField` gives `tel:` for a phone with 6 digits or more, and the
   `href` holds only digits and `+`. A shorter value stays as text. A fixed
   email field gives `mailto:` only when `isEmail` passes.
7. In Mirror mode the header and the tabs show, and the fields stay locked.
8. The `visual-review` skill passes (§8.4), also next to the task panel of
   Projects.

**Fences.**

- `app/crm/components/crmX4.test.ts`, new: items 1 and 6.
- `app/crm/lib/recordNav.test.ts`, new: item 5.
- `app/crm/lib/timeline.test.ts`, existing, wider: item 3.
- `app/crm/lib/urlState.test.ts`, existing, wider: item 2.
- `app/crm/lib/format.test.ts`, existing, wider: item 4.
- `src/lib/theme/conformance.test.ts`, existing: the CRM baselines go down only.

### 19.9 CRM-Z9: the write consent (a contract note)

This is a note, not the contract. CRM-Z9 needs its own spec audit before
dispatch. That audit also decides the write path: Zoho's MCP (§7.2), or the
old writer in `apps/services/ingestion/ingestion/sources/zoho/writer.py`
(§13.7).

**The problem.** Write-back needs Zoho WRITE scopes, in the form
`ZohoCRM.modules.<module>.UPDATE` and `ZohoCRM.modules.<module>.CREATE`. One
consent serves every org today. If CRM-Z9 adds the write scopes to it, Zoho
asks every org for write access, also with the flag off. That is wrong.

**The seam today.** `ZOHO_READ_SCOPES` (`routes/crm/connection.py:98-115`) is
the one scope list. It reaches Zoho at three points:

- `start_zoho_connection` passes it to `source.consent_url`
  (`connection.py:312`, `crm_sources/zoho/auth.py:171`).
- `zoho_callback` passes it to `source.finish_consent` (`connection.py:354`).
- `zoho_callback` stores it as the granted list through `store.save_credential`
  (`connection.py:365`).

The signed state has the purpose `crm.zoho.connect` (`connection.py:85`), and
it names no scope.

**The rule for CRM-Z9.**

1. A new flag in `routes/crm/flags.py` names the orgs with write-back on. It
   has the grammar of `CRM_MIRROR_ORGS`, and an empty list allows no org. It
   ships empty.
2. The read consent does not change. `ZOHO_READ_SCOPES` gets no WRITE scope.
3. A second consent step asks for the write scopes. It is a separate admin
   route beside `start_zoho_connection`. It gives 404 for an org that the flag
   does not allow.
4. The second step asks for `ZOHO_READ_SCOPES` and the write scopes together.
   A Zoho token holds only the scopes of its own consent.
5. The step signs its state with a purpose of its own. The callback picks the
   scope list from that purpose, never from the request.
6. `store.save_credential` records the scopes that the consent granted. The
   writer refuses a write when the stored scopes do not hold the WRITE scope of
   that module.
7. The flag gates every write. With the flag off, the stored write scopes do
   nothing.

⚠️ Before the build, check each WRITE scope name against Zoho's published
scope list. Zoho refuses the whole consent for one unknown scope (§18.7).

### 19.10 Audit notes for CRM-X1 to CRM-X4 (2026-10-12)

The spec audit of 2026-10-12 gave GO-NARROWED to the four slices. These notes
bind. Where a note and §19.5 to §19.8 disagree, the note wins.

**CRM-X1.**

- The search box shows on the list layout only. `get_pipeline` takes no `q`,
  so a board would ignore the box.
- Done-when 2 reads: `GET /crm/contacts?q=<part of the phone as the record
  holds it>` returns that contact.
- The deals search adds `company_id IN (SELECT c.id FROM crm_companies c WHERE
  c.name ILIKE :q)`.
- `GET /crm/owners` reads `app_user` where `COALESCE(status, 'active') =
  'active'`, as `_directory` in `rooms.py` does. It returns `email`, `name` and
  `member` only.

**CRM-X2.** Build it after CRM-X1 merges.

- Each read of `search_records` passes `sort="last_activity_at"`. The default
  sort of leads is `created_at`.
- `search_records` does not call `attach_next_activity`, because the search
  has a budget of 1.5 seconds.
- `log-call` with no open record opens `/crm?tab=deals&layout=list`. Deals
  open as a board, and a board has no search box.

**CRM-X3.** The scope is items 1 to 4. Idle days and the tint for rot belong
to CRM-I1.

- The slice needs no migration. `idx_crm_activities_deal_id_created_at` and
  `idx_crm_activities_lead_id_created_at` (`144_crm.sql`) serve the query.
- Done-when 5 reads: one `GET /crm/pipeline` with the default `card_sort`, over
  3 lanes and 40 cards, issues one statement for the next activities.
- With `card_sort=next_activity`, each lane read also reads `crm_activities`.
  Done-when 5 does not count that read.

**CRM-X4.** The scope is items 1 to 7.

- `fields.ts` exports `isEmail`. Today it is private.
- The `j` and `k` handler checks that no modifier is down (`!e.metaKey &&
  !e.ctrlKey`). `seams.test.ts` is a fence of this slice, because its `cmdk`
  pattern can match a bare `k` handler.
- `crmX4.test.ts` also covers item 7.
- Exactly three raw controls are in `app/crm/` today: `FieldsPanel.tsx` (one)
  and `Timeline.tsx` (two). The slice replaces all three.

**Order.** CRM-X1, CRM-X3 and CRM-X4 run in parallel. CRM-X2 follows CRM-X1.
None of the four needs a migration.

### 19.11 CRM-X0: Setup, connection and start over (owner report, 2026-10-12)

**The report.** The owner connected Zoho, disconnected it, and then tried to
sync again. The CRM showed "This CRM has records, so its setup cannot change.
Remove the records first." The owner found no way forward. The owner asked
for three things:

1. A change of setup that needs an empty CRM drops all the CRM records
   automatically.
2. Before the drop, the CRM offers to save a copy of the records.
3. The setup, connect, disconnect and reconnect workflows are easy to find
   and easy to follow.

**What production showed (Fracktal, 2026-10-12 09:40 UTC).** `crm_settings.mode`
was still `mirror`. The Zoho connection was `disconnected`. The CRM held 547
deals, and every record came from Zoho (`ext_source = 'zoho'`). A plain
reconnect needs no reset, because the sync matches each record by its Zoho
id. But the page put "Change setup" next to the disconnected connection, and
that route (U5b) refuses a CRM with records.

**Gate.** AGENT-SAFE. The owner asked for the drop in chat. The admin does
each drop in the UI. An agent never drops the records of a live org.

**Waits for.** CRM-Q6a (PR #900) merged, because both edit
`routes/crm/settings.py` (`_RESET_DELETES`).

**Scope.**

1. **One setup hub.** The first card of CRM settings is "Setup". It shows
   the mode, the connection status, the last sync, and the record counts.
   It offers only the actions that fit the state:

   | State | Main action | Other actions |
   |---|---|---|
   | No mode | Choose: Connect Zoho, or Use Metorite | None |
   | Mirror, connected | Sync now | Disconnect. Switch to Metorite. Start over |
   | Mirror, disconnected or needs a reconnect | Reconnect Zoho (keeps the records) | Switch to Metorite. Start over |
   | Native | None | Connect Zoho. Start over |

   The mirror banner and the title-bar line link to this card. No other
   card offers "Change setup".

2. **Reconnect keeps the records.** A reconnect to the SAME Zoho org resumes
   the sync with the records in place. The callback compares the Zoho org id
   of the new token with the one in the latest source profile.

3. **A different Zoho org needs an empty CRM.** If the org id differs, the
   callback stores the connection as `needs_reset` and does not sync. The
   hub then opens the start-over dialog of item 5 for that change. Records of
   two Zoho accounts never mix.

4. **Download a copy.** `GET /crm/export/all.zip` returns one ZIP. It holds
   one CSV for each entity. The entities are leads, contacts, companies,
   deals, activities, and the generic module records of CRM-Q6a. It reuses the CSV writer of
   `routes/crm/export.py`. It has no row cap. An admin calls it, and the CRM
   access rule applies. It streams, so a large CRM does not fill memory.

5. **The start-over dialog.** Each change that needs an empty CRM opens it.
   These changes are Switch to Metorite, Connect Zoho from Native mode,
   Start over, and a different Zoho org.
   - Step 1 shows the counts for each entity, and says that the change
     removes them.
   - Step 2 offers "Download a copy" (item 4). The dialog marks it as done
     when the download starts.
   - Step 3 asks the admin to type `DELETE` to confirm. The button reads
     "Remove N records and continue".
   - The dialog uses `Modal` and the shared primitives.

6. **The reset route.** `POST /crm/reset` with
   `{"confirm": "DELETE", "then": "none" | "native" | "mirror"}`.
   - Only an admin calls it. Any other `confirm` gives 422.
   - In one transaction in the tenant session, it deletes every CRM record
     of the org. That covers the record tables, `crm_deal_contacts`,
     `crm_module_records`, `crm_field_defs`, the sync cursors, the pipelines
     and their stages, the deal statuses, the lead statuses and the lost
     reasons. It keeps `crm_settings` (the access rule), `crm_views`, and
     `crm_sync_runs` (the history).
   - It disconnects Zoho through the ONE disconnect logic
     (`connection.disconnect_zoho`), so a revoke at Zoho also happens.
   - It sets `mode` from `then`. `then = "native"` seeds the Native stages,
     as the first-open choice does today. `then = "mirror"` leaves the mode
     empty and starts the Zoho consent.
   - The reset writes through a new fenced door,
     `reset_crm_from_admin`, in `routes/crm/core.py`. Its name joins `NAMES`
     in `tests/unit/test_crm_upsert_from_source_fence.py`, because it
     bypasses the Mirror write guard. Only `routes/crm/settings.py` may call
     it.
   - It writes one audit line with the admin, the time and the counts.
   - The sync of the org stops before the delete, and it does not start
     again until a new connection.

7. **Words.** The hub and the dialog say what each action does in one
   sentence. "Disconnect" says that the records stay, and that they stop
   updating. "Start over" says that every record goes.

**Non-goals.**

- Keeping Zoho records as Metorite records on a switch to Native mode. That
  is a later slice, because it converts mirrored rows to native rows.
- Undo of a reset. The download is the copy.

**Done when.**

1. An R8 test starts with a Mirror org that holds records. An admin calls
   `POST /crm/reset`. The call removes every listed row of that org. It
   leaves org B unchanged. It keeps `crm_settings` and the run history.
2. A member who is not an admin gets 403. A wrong `confirm` gives 422.
3. A reconnect to the same Zoho org keeps the 547-shaped record set and
   resumes the sync. A reconnect to a different Zoho org gives
   `needs_reset`, and writes no record.
4. `GET /crm/export/all.zip` holds one CSV for each entity, with every row.
   Org B's rows never appear.
5. A vitest shows the hub's actions for each state in the table of item 1.
   It also shows that only the hub offers a change of setup.
6. A vitest walks the dialog: counts, download, typed confirm, then the
   call.
7. The fence test refuses a call to `reset_crm_from_admin` from any file
   other than `routes/crm/settings.py`.

**Fences.** `tests/unit/test_crm_reset_r8.py` (R8, `_DB_GATE`),
`tests/unit/test_crm_upsert_from_source_fence.py`, and
`src/app/crm/components/crmX0.test.ts`.
