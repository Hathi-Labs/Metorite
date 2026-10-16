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
> **Built later:** CRM-Q3, the new layout of §18.4 (2026-10-12). CRM-Q4, field definitions in the UI (2026-10-12). CRM-Q9, the Sync health card (2026-10-12). CRM-Q8, filters and saved views (2026-10-12, migration 250). The Deals board stages fix (2026-10-12, §18.5). CRM-Q10, a reconnect never freezes the mirror (2026-10-13, §18, a production hotfix). CRM-Q15, a Zoho module that the account turned off no longer stops the sync (2026-10-15, §18, a production hotfix). CRM-Q16, hold a module and sync one module on its own (2026-10-15, §18.8, migration 269). CRM-Q17a-1, the backend of the fill rank (2026-10-16, §18.9.3, migration 274). It is dark behind `CRM_FILL_RANK_ORGS`. CRM-Q23b, a dropped activity comes back once its module record is in (2026-10-16, §18.10, no migration).
>
> **Specified, not built:** CRM-Q17a to CRM-Q22, a custom CRM that reads well, and a mapping that the admin only approves (2026-10-16, §18.9). CRM-Q18-2 and CRM-Q18-3 are not built.
>
> **Built later:** CRM-Q18-1, the backend of one plan (2026-10-16, §18.9.6, migration 277). The versions of the Field map live in `crm_mapping_plans`, and `crm_field_rules` stays the live set.
>
> **Built later:** CRM-Q5, the data the mirror loses (2026-10-12). Migration 249 adds `crm_deal_contacts.ext_source`.
>
> **Built later:** CRM-Q6a, the generic record store (2026-10-12, migration 251). See §18.7.
>
> **Built later:** CRM-Q7a, the rule table and the Field map (2026-10-12, migration 253). See §18.7.
>
> **Built later:** CRM-Q7b, the proposer of field rules (2026-10-14). It adds no migration. See §18.7.
>
> **Built later:** CRM-Q26, labels on CRM notes on the decide tier, dark
> (2026-10-16, migration 278). See §18.11.
>
> **Built from §19:** CRM-X4, the record page v2 (2026-10-12, §19.8).
>
> **Built from §20:** CRM-C2, one board (2026-10-13, §20.5). CRM-C3, stages
> and statuses you can shape, in both modes (2026-10-13, migration 257).
> CRM-C4a, the backend of the field studio, dark behind
> `CRM_FIELD_STUDIO_ORGS` (2026-10-13, migration 260, §20.7). CRM-C4p, the
> shared client of the field studio: the layout read, `FieldInput` and the
> client check (2026-10-14, §20.7). It draws no surface and adds no flag.
> CRM-C4b, the record UI of the field studio: the create form, the Fields
> tab, the Columns control and the filter bar read the layout when `studio`
> is on (2026-10-14, §20.7). No migration and no flag.
> CRM-C4c, the studio UI in the `fields` settings section, for an admin of a
> Native org with the flag on (2026-10-14, §20.7). It adds no migration and no
> flag.
> CRM-C6a, the Native lead-conversion mapping in the `field-mapping` section,
> dark behind `CRM_FIELD_STUDIO_ORGS` (2026-10-14, migration 261, §20.9).

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
| UI label "Organizations" | "Companies". *In Mirror mode, the source's own label (amended 2026-10-15, §18.9.1)* |
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
| 6 | Credits and concurrency | **Credits per edition, rolling 24 hours:** Free 5,000. Standard 50,000 + 250 per licence, up to 100,000. Professional 50,000 + 500 per licence, up to 3,000,000. Enterprise 50,000 + 1,000 per licence, up to 5,000,000. Ultimate 50,000 + 2,000 per licence, with no cap. **A suite plan** counts as the CRM edition it holds. Zoho One Enterprise (`paid_type` `zohooneenterprise`) counts as Enterprise, through `BUNDLE_EDITIONS` in the Zoho mapping (2026-10-16, found on DEWiN). Any other suite plan keeps the 500-credit floor. **Cost:** a page of records 1, or 3 with `cvid`. The deleted list 2. Users, field and module metadata 1. A COQL query 1 to 3. A Bulk Read job 50. **Not confirmed:** whether a refused call (429) costs a credit. **Concurrency for one org and app:** Free 5, Standard 10, Professional 15, Enterprise 20, Ultimate 25. **A sub-limit of 10** covers a record read with `cvid` or `sort_by`, COQL and some writes. Past it Zoho answers `TOO_MANY_REQUESTS`. HTTP 429 means that the org spent its daily credits, or that a call passed the concurrency limit. **`X-API-CREDITS-REMAINING`** comes in the response only when half or more of the daily credits are spent | [v8 limits](https://www.zoho.com/crm/developer/docs/api/v8/api-limits.html) · [v2 limits](https://www.zoho.com/crm/developer/docs/api/v2/api-limits.html) · [status codes](https://www.zoho.com/crm/developer/docs/api/v8/status-codes.html) |
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
| CRM-S6 | **Custom fields editor** | Every company has its own fields | **Superseded by CRM-C4 (§20.7), 2026-10-13.** Do not dispatch it |
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
| "Accounts" or "Companies" in Mirror mode | "Companies" everywhere | No label alias. D95.2 holds. *Amended 2026-10-15: Mirror mode shows the source's own labels (§18.9.1). Native keeps "Companies"* |

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
| **CRM-Q14** (was CRM-Q10 until 2026-10-13, when the hotfix "a reconnect never freezes the mirror" took that id) | Layouts and sections on the record page. The move to API v8 with field batches of 50 | P1 |
| **CRM-Q11** | Currency conversion in totals and in the forecast | P1 |
| **CRM-Q12** | Per-owner visibility in the mirror | P1 |
| **CRM-Q13** | Attachments and email metadata. Tags. The Zoho user directory | P1 |
| **CRM-Q15** | ✅ built 2026-10-15 (hotfix). A Zoho account can turn off a core module, such as Accounts or Deals, and keep its data in custom modules. Zoho then answers each record read of that module with HTTP 400 `INVALID_MODULE` (v2) or `NO_PERMISSION` (v8). Measured on a live tenant on 2026-10-15. Before this fix, the run read Accounts first, failed, and mirrored nothing. Now the client raises `SourceModuleUnavailable` for those two codes. The engine skips ANY module that raises it, core or optional, and records the reason `hidden` in the cursor. The run is `ok`, the other modules and the generic modules land, and the connection stays `connected`. The count key of the module gets `unavailable` and `hidden`. The Sync health card then says "Turned off in your Zoho", and not "Connect Zoho again". A skip deletes nothing, and no reconcile runs for the module while it is off. The engine asks again after `UNAVAILABLE_RETRY`, so a module turned on again heals. When a core module comes back, each entity in `DEPENDENTS` is read in full once, through the re-read list of the field rules, so a child that landed with no parent gets its link. That read stamps nothing and deletes nothing. While Deals is off, the contact roles step reads nothing and keeps its queue. A refused deleted list is counted as `hidden`. The cursor keeps `failed`, `retry_from` and `converted_both` while the module is off. Any other failure of a core module still fails the run. Fence: `tests/unit/test_crm_hidden_module_r8.py` (R8) and two client tests in `test_crm_sources_zoho.py` | P0 |
| **CRM-Q16** | ✅ built 2026-10-15. Hold a module, and sync one module on its own. The first import holds a module above 5,000 records and suggests a sync of it on its own. §18.8 | P0 |
| **CRM-Q17a** | Lists and records by fill rank: default columns, empty columns hidden, owner names, Overview and Fields, Zoho labels in Mirror mode. No AI. §18.9 | P0 |
| **CRM-Q17b** | Getting around: the rail group, the first screen, filters, the Columns picker, phone cards, and "Needs you" in Sync health. No AI. §18.9 | P0 |
| **CRM-Q18** | One plan: the versions of the Field map in `crm_mapping_plans`, and `crm_field_rules` stays the live set. CRM-Q18-1 is built. §18.9 | P1 |
| **CRM-Q19** | The executor and the 200-record preview. §18.9 | P1 |
| **CRM-Q20** | The mapping pipeline on the decide tier, the suggestion in an empty slot, the panel and Undo. Replaces the CRM-Q7b proposer. §18.9 | P1 |
| **CRM-Q21** | Promote a module into a core entity. §18.9 | P1 |
| **CRM-Q22** | Source changes as suggestions, then a CSV source, then HubSpot. §18.9 | P2 |
| **CRM-Q23** | Link a Zoho activity to a custom-module record, and show it there, in three slices (Q23a, Q23b, Q23c). §18.10 | P0 |
| **CRM-Q24** | A short read shows on the Sync health card. §18.10 | P1 |
| **CRM-Q25** | The typed aggregate over every module and field, and its AI tool. §18.11 | P0 |
| **CRM-Q26** | Labels on notes, on the decide tier, in shadow. §18.11 | P1 |
| **CRM-Q27** | Search of note text by meaning. §18.11 | P2 |
| **CRM-Q28** | A weekly digest on the powerful tier. §18.11 | P2 |

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
- The Fields card on CRM settings lists the fields of each entity. It says "Fields come from Zoho. Edit fields in Zoho." in Mirror mode and "Field editing comes later." in Native mode. CRM-C4 (§20.7) builds the editor. CRM-C4c draws the studio in its place for an admin of a Native org with the flag on. It supersedes CRM-S6.
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
| **CRM-Q7a** ✅ built 2026-10-12 | AGENT-SAFE | The rule table, the Field map card, approve and reject, and the sync that applies approved rules. No model call | CRM-Q6a merged |
| **CRM-Q7b** ✅ built 2026-10-14 | AGENT-SAFE | The proposer: one routed model call over metadata | CRM-Q7a merged |

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
- The list is `RecordList` with `MODULE_COLUMNS`, the CRM-X1 search box
  (`FilterBar.tsx` exports `SearchBox`) and the CRM-X1 "Load more". The rows
  of a module are not in the CRM store, so `ModuleList.tsx` holds the paging
  state. It keeps the rules of the store: `appendPage`, a short page ends the
  list, and the newest request wins.
- `RecordShell.tsx` holds the drawer, the header facts and the tab bar.
  `RecordSheet` and `ModuleRecordSheet` both use it, so the two record pages
  have one look.
- Fences: `tests/unit/test_crm_modules_routes_r8.py` and
  `src/app/crm/components/crmQ6b.test.ts`, red first. A visual check passed
  in light mode, at compact density, under a violet accent and at 390 px,
  beside the Deals list.

#### CRM-Q7a: the rule table and the Field map (✅ built 2026-10-12)

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

**Built as (2026-10-12).**

- Migration 253 adds `crm_field_rules`, with the tenant key, FORCE row level
  security and one policy with USING and WITH CHECK. A partial unique index
  keeps one approved rule for each target column.
- `FIELD_RULE_TARGETS` in `mapping.py` holds the two tables above and no other
  column. `rule_problem` is the one check of a rule. The routes and the sync
  both use it.
- The profile gains two keys from the reads that discovery already makes.
  `layout_fields` holds the fields on an active layout. `custom_field_types`
  holds the type of each field that goes to `custom`. A profile with no
  `layout_fields` makes discovery due at once.
- Discovery makes an approved rule stale from those two keys. An entity that
  discovery could not read keeps the status of its rules.
- `POST /crm/field-rules` is the pick by hand. It writes an approved rule with
  `proposed_by = 'admin'`. It refuses a field that has an approved rule.
- An approve checks the rule again against the latest profile. So an admin
  cannot approve a rule that the next discovery makes stale again.
- `GET /crm/field-rules` names the columns that each field can fill now. The
  Field map card offers only those columns, and it keeps no copy of the rules.
- The sync keeps the rules that it applied in the cursor row
  `zoho:field_rules`. Before its first provider call, it undoes each rule that
  is not approved now. Then it backfills each new rule.
- A rule enters the applied state in the transaction of its first backfill
  batch, with the mark `backfilling`. The last batch clears the mark. So a
  stop between two batches, then a reject, still undoes the rule.
- `map_record` applies only the rules that the backfill step handled, minus
  the rules that discovery made stale. A rule that an admin approves during
  a run waits for the next run.
- A full read for a rule keeps the mark `rule_reread` in the cursor state of
  the entity while it runs. It stamps nothing and deletes nothing.
- The door `apply_field_rule_from_source` writes only a NULL column of a
  mirrored row, or clears a free column. It refuses to clear a fixed column.
  Each statement names the organization.
- A rule on `probability` gives NULL for a value outside 0 to 100.
- The start over of CRM-X0 and the change of setup of CRM-U5b delete the
  rules. An R8 fence fails when a `crm_` table is in neither `RESET_TABLES`
  nor `RESET_KEEPS`.
- Fences: `tests/unit/test_crm_field_rules_r8.py`,
  `tests/unit/test_crm_field_rules.py`,
  `tests/unit/test_crm_upsert_from_source_fence.py`,
  `tests/unit/test_crm_mirror_readonly.py`,
  `tests/unit/test_crm_reset_r8.py`, `tests/unit/test_crm_u5b_r8.py` and
  `src/app/crm/components/crmQ7a.test.ts`.

#### CRM-Q7b: the proposer (✅ built 2026-10-14)

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

**Built as (2026-10-14).**

- The route is `POST /crm/field-rules/suggest {"entity"}` in
  `routes/crm/field_rules.py`. It has the guards of CRM-Q7a: `CRM_MIRROR`,
  `admin:access:manage` and Mirror mode. The body forbids other keys, so a
  body that names an organization gets 422.
- `build_field_map_prompt(fields, targets)` builds the input. Each field
  holds its API name, label, type, option labels and `may_fill`. The targets
  are the open columns that no approved rule fills.
- The model gets only the fields with no decided rule, and only the fields
  that an open column can take. A stale rule counts as decided.
- `_ask_model` binds the admin with `run_context_scope` and
  `bind_run_context(member_verified=True)`. It wraps the leaf call in
  `llm_slot()`, as `actions.py` does. The spec did not name that slot. Outside
  a mailbox job it takes no permit and counts nothing.
- The call has a bound of 25 seconds, because the `/api/crm` proxy cuts a
  request at 30 seconds. A failure or a bad answer gives 502 with the code
  `suggest_failed` or `suggest_bad_answer`, and writes no row.
- The route sends the fields in batches of 40, one call for each, and the
  calls run at the same time. So "one entity is one call" is now one call
  for each batch. The route reads each answer on its own and keeps what it
  can read. `unread` counts the fields of a batch that failed.
- One press for each organization and entity in 60 seconds reaches the
  model. The route sets a tenant Redis key with `NX` and a TTL of 60 seconds
  before the call, through `acb_common.tenant_redis`. A second press gets
  429 "Wait a minute before you ask again." A Redis outage gets 503.
- The insert writes the literal `'proposed'`. Its update replaces only an
  older proposal of the assistant, so a decided rule keeps its row.
- An approve or a reject updates a rule only when its status and target are
  still the ones the route read. Else the admin gets 409 "This proposal
  changed. Reload to see it."
- `mapping.legal_columns` is the one list of the columns that a field may
  fill, and the GET route calls it. The proposer uses `mapping.rule_problem`
  and `mapping.types_agree` for the same rule.
- The Field map card has a "Suggest mappings" button for each entity. It
  shows a spinner while the call runs, and an error under the entity.
- ⚠️ The second-press rule needs an open proposal of the assistant at the
  current profile version. A decided row does not count. An answer that
  keeps no item writes no row, so the next press after the cool-down calls
  the model again.
- Fences: `tests/unit/test_crm_field_map_ai.py` (R8, red first, eleven
  mutations) and `src/app/crm/components/crmQ7b.test.ts` (red first, three
  mutations).

#### CRM-Q10: a reconnect never freezes the mirror

**Gate.** AGENT-SAFE. This is a production hotfix (2026-10-13). It adds no
migration, no route and no flag. `CRM_MIRROR` already gates the sync.

**The id.** The §18.5 P1 row "Layouts and sections" had the id CRM-Q10 until
2026-10-13. It is now CRM-Q14, and this hotfix keeps CRM-Q10.

**The incident (Fracktal, Mirror mode, measured on prod 2026-10-12).**

1. At 15:10 UTC the owner used "Reconnect Zoho (keeps the records)" to grant
   the module scopes of CRM-Q6a. It was the same Zoho account and the same
   connection id.
2. Each consent ran `_DROP_PROFILES_SQL` in `store.save_credential`. So
   `crm_source_profiles` held 0 rows.
3. With no profile, `profiles.budget_for` returns `None`, and the run budget
   falls to the floor `DAILY_CREDIT_BUDGET` (500 credits).
4. The org had spent about 950 credits in 24 hours. So the run budget,
   `max(500 - used_24h, 0)`, was 0.
5. From 15:00 UTC each run stopped at once with the code `budget` and 0
   credits. Discovery restores the profile and the edition budget, but
   discovery needs credits. So the mirror froze until the 24-hour window fell
   below 500 credits.

**The root cause in the code.** A new consent always dropped the profile, also
for the same account. And a run with no profile had no credit for the one step
that makes a profile.

**Scope.**

1. **A consent to the same account keeps the profile.**
   - `store.save_credential` takes `keep_profile`. The default is `False`, and
     it drops the profile as before.
   - The callback (`routes/crm/connection.py`) passes `True` only when
     `_same_account` confirms the account (step 1, 2 or 3 of CRM-X0).
   - The save marks the profile stale: its `fetched_at` moves 7 days back. So
     `_discovery_due` is true at the next run, and discovery reads the new
     scopes.
   - An account that the callback cannot confirm still drops the profile. A
     different account still gets `needs_reset` and saves nothing (CRM-X0).
2. **With the edition unknown, discovery can run.**
   - `engine.DISCOVERY_ALLOWANCE` is 25 credits. The allowance holds when the
     budget is the floor (no profile, or an edition that Zoho did not name),
     the profile is absent or 24 hours old, and the run uses the default
     budget. Then discovery may spend up to 25 credits, also when the default
     budget is spent. A known edition never gets it (review, 2026-10-13).
   - The age rule holds it to once in 24 hours. `_discovery_due` alone does
     not, because pending module fields make discovery due after one hour
     (re-review, 2026-10-13).
   - When less than the allowance is left, discovery reads first, before the
     estimate and the field definitions. It stores the profile. The pipelines
     wait for the normal discovery slot, after the Stage picklist, so a start
     over keeps the picklist order and probabilities (review P1). A run that
     stops before that slot leaves them to the next run, which writes them
     from the stored profile while the org mirrors no Zoho pipeline.
   - When discovery has read the account, the run budget becomes the edition
     budget minus `used_24h`, as before. The credits of this run, discovery
     included, count against it. The module fields and the rest of the run
     obey that budget.
   - A discovery that fails sets the budget back.
   - ⚠️ **Advisory, not fenced: the provider floor.** The check of `ctx.call`
     against `CREDITS_FLOOR` runs only on a count that Zoho reported earlier
     in the same run. The early discovery is the first call of its run, so no
     count exists yet. The 25-credit cap of the allowance bounds it, and the
     floor does not. A fence needs the last reported count kept across runs.
     That is not built.
3. **Sync health names the pause.**
   - `GET /crm/sync/health` returns `credits.resumes_at`. It is the time when
     the credits of the 24-hour window fall below the budget. It is null when
     the window is already below the budget.
   - When the last run stopped with `budget` and `resumes_at` is set, the card
     says "Paused: the daily Zoho API budget is spent. It resumes at HH:MM."
4. **Not changed.** `BUDGET_FRACTION`, `ASSUMED_DAILY_CREDITS` and the runs that
   count toward `used_24h` stay as they are.

**The cost of one discovery (measured).** `ZohoSource.discover` makes 2 + 2 × 4
calls, plus one for each Deals layout. That is 11 credits with one Deals layout
(`test_crm_discovery.py` asserts 11). The allowance of 25 covers 15 Deals
layouts, or 14 retries.

**Done when.**

1. An R8 test reconnects the same account through the real callback. The
   profile row stays, and it is stale. A save with `keep_profile=False` drops
   it. A different account keeps it and gets `needs_reset`.
2. An R8 test starts with no profile and 950 credits spent in 24 hours. An
   incremental run and a reconcile run each discover, store the profile, use
   the edition budget, and end `ok`.
3. That test fails without the fix: the run stops with `budget` and 0 credits.
4. A hermetic test shows that a reported credit count under `CREDITS_FLOOR`
   still stops discovery before its call. This guards the order in
   `ctx.call` only. Production does not reach that state (see the advisory
   note above).
5. An R8 test shows `credits.resumes_at` for a spent window, and null for a
   window under the budget.
6. A vitest shows the pause sentence with its time.
7. Review, 2026-10-13. An R8 test does a start over, spends the window, then
   imports. The picklist order and probabilities hold. A second test shows
   that a run that stopped early leaves the pipelines to the next run.
8. Review, 2026-10-13. An R8 test shows that an unknown edition gets the
   allowance once in 24 hours. Another shows that a known edition with a
   spent window gets none, and stops on `budget`.

**Fences.** `tests/unit/test_crm_q10_reconnect_r8.py` (R8, `_DB_GATE`),
`tests/unit/test_crm_reset_r8.py`, `tests/unit/test_crm_discovery.py`,
`tests/unit/test_crm_sync_health_r8.py` and
`src/app/crm/components/crmQ9.test.ts`.

**Verification.**

```bash
uv run pytest tests/unit/test_crm_q10_reconnect_r8.py tests/unit/test_crm_reset_r8.py \
  tests/unit/test_crm_discovery.py tests/unit/test_crm_sync_health_r8.py -q
cd workbench/control_plane && npx vitest run src/app/crm/components/crmQ9.test.ts
```

**Built as (2026-10-13).** ✅ Built. No migration and no flag.

- `store.save_credential(keep_profile=...)`. `_STALE_PROFILES_SQL` moves each
  `fetched_at` of the connection 7 days back. The callback passes
  `keep_profile=verdict is True`.
- `engine.DISCOVERY_ALLOWANCE = 25`. `_discovery_allowance_due` reads the
  four conditions, and the profile age is one of them. `_execute` runs `_discover` first when they hold, with
  `ctx.pipelines_deferred` set. `_discover` reads at most once in a run, and
  it sets the edition budget before the module fields.
  `_mirror_deferred_pipelines` writes the pipelines at the normal slot.
- `runs.resumes_at` reads the window with one window function, on the filter
  of `credits_used_24h`. `GET /crm/sync/health` returns it as
  `credits.resumes_at`, and `statusLines` in `lib/syncHealth.ts` writes the
  pause sentence.
- `test_crm_reset_r8.py` now expects the first same-account consent to keep the
  profile.

### 18.8 CRM-Q16: hold a module, and sync one module on its own (2026-10-15)

**Why.** On 2026-10-15 the first import of DEWiN Solutions read 805 contacts
and 3,848 of 18,674 notes in five minutes. In the same window the gateway pool
ran dry. A member's sign-in failed twice, and the email reads hit the statement
timeout.

The owner paused the org and asked for two things. An admin must be able to
sync one module at a time. The product must suggest that when one module
holds a lot of data.

**Owner decisions (2026-10-15, in chat).**

| Question | Answer |
|---|---|
| What does the first import do with a large module? | It holds the module and suggests a sync of it on its own. The rest syncs |
| When is a module large? | Above 5,000 records in the provider count (`LARGE_MODULE_RECORDS`) |

**The keys.** A hold names a count key of `crm_sync_runs.counts`:
`organizations`, `contacts`, `leads`, `deals`, `activities` (notes, calls and
meetings), `tasks`, or `module:<ApiName>`. The card already lists these keys.

**The store.** Migration 269 adds `crm_settings.sync_holds`, a JSONB map from
a key to `{"held": bool, "by": "auto" | "admin", "count": int | null, "at": iso}`.
An empty map holds nothing. An entry with `held: false` records that an admin
turned the module on, so the next import does not hold it again.
`gateway/crm_sync/holds.py` is the one reader and the one writer.

**The engine.**
1. A held key is not read. Its changed pass, its deleted pass and its generic
   module step return at once. The run counts `held: 1` under the key.
2. A held core entity does not keep the plan at `import`. `plan()` skips it,
   as it skips an optional entity. So the deleted lists of the other entities
   still run.
3. **Auto-hold.** An `import` run reads the counts with `_estimate` before the
   first record read. Then it holds each key whose count is above
   `LARGE_MODULE_RECORDS`, with `by: "auto"`. That applies only when the key has no entry yet and
   its first import is not done. A generic module does the same after
   `_module_estimate`.
4. **One module on its own.** `POST /crm/sync/zoho` takes an optional body
   `{"only": "<key>"}`. The run then reads only that key, also when it is held.
   It does not read the other keys, and it counts nothing under them. A held
   key stays held. The admin clicks again until the module is in, or turns it on.
5. A hold deletes nothing. The rows that a held module mirrored stay.

**The routes.**
- `PUT /crm/sync/modules/{key}` with `{"sync": bool}`. It needs
  `admin:access:manage` and `CRM_MIRROR`. It writes `held: !sync, by: "admin"`.
  An unknown key answers 422. The key is checked against the fixed list, or
  against the modules of the source profile for `module:<ApiName>`.
- `GET /crm/sync/health` adds `holds`, the map of the store.

**The card.** Each entity row of the Sync health card shows a "Sync" switch.
A held row shows a "Held" badge and the sentence: "<Label> has <n> records in
Zoho. Sync it on its own when Metorite is quiet, so the rest stays fast". It
also shows a "Sync it on its own" button, which posts `only`.

**Fences.** `tests/unit/test_crm_sync_holds_r8.py` (R8) and `syncHealth` tests
in `crmQ9.test.ts`.

**Not in this slice.** Pacing inside one module, and a cheaper write of an
activity whose parent cannot exist. The owner moved the activities of DEWiN
to "later".

**Review round (2026-10-15).** The adversarial review found two P1 and four
P2 defects. The build fixed each one:

- A held parent (`organizations`, `contacts`, `leads`, `deals`) makes its
  children land with no link. The engine marks the parent `held_skipped` in
  its cursor. Its next read, released or on its own, re-reads the entities in
  `DEPENDENTS` once, through the CRM-Q15 `recovering` path.
- The estimate sums tasks into `activities`. The hold decision counts by
  COUNT KEY (`ctx.key_counts`), so `tasks` is held on its own. A resumed
  import holds nothing again: its first try did.
- An import run reads the deleted list of each entity whose first import was
  done when the run began. A released hold no longer stops every delete.
- `plan()` asks a full compare of an entity when its deleted cursor and its
  last compare are both older than `GAP_RECONCILE` (50 days). A held key and
  a hidden module are never due. One completed compare then answers for 50
  days. So a deleted list that never moves cannot ask on every run. It is one
  strike, as the org-level gap is today.
- A start over clears `sync_holds` (`holds.clear`), in both reset paths.
- `PUT /crm/sync/modules/{key}` accepts `module:<ApiName>` only for a generic
  module of the profile. The auto-hold writes only a key with no entry, so an
  admin's choice made while the run planned stays.
- The card suggests a hold only for a key with no entry that still has
  records to sync. A held row shows a neutral badge and no difference badge.

**Round two (2026-10-15).** Three rules came from the second review. A resumed
import derives its hold counts from the saved estimate, which now carries
`tasks` on its own key. An estimate from before this slice counts once more.
An "on its own" run also reads each unheld parent of its keys, incrementally.
So a child of a new parent keeps its link.

**Round three (2026-10-15).** A re-read of the children bounded to the hold
lost records in two cases. One is a child whose import never ran. The other
is a child made between the parent's last read and the hold. So the re-read
of a held parent's children is a FULL read, on purpose.

An "on its own" run reads an unheld parent for its changes only. It does no
walk, no first import, no re-read, no deleted list and no deal roles for that
parent. A parent that is not in yet, is mid-walk, or still carries a hold or
refusal mark is left to the next normal run. So no relink is lost.

Two items stay open in `HANDOFF.md`. H-320 is the edge of the shared
checkbox. H-321 is a generic module held past the 60 days of the deleted list.

### 18.9 A custom CRM that reads well, and a mapping that the admin only approves (CRM-Q17 to CRM-Q22, 2026-10-16)

**Status (2026-10-16).** Specified, not built. Verified against code at
`origin/main` `00fd30230`, after CRM-Q16 merged (#1053).

**Design record.** The owner's design page is "Source Mapping Studio" v4.
Its link is https://claude.ai/artifact/QcBY3s2ATxo3Kb9QCpR3Zr. This section
is the build contract, and it wins where the two differ.

v4 replaces v3 of the same page. Three reviews made v4, at the owner's request. They checked
the token cost of the engine and the admin's path. They also rendered the
CRM app with DEWiN's data shape.
They used the repo skills `impeccable`, `apple-design` and `visual-review`.

#### 18.9.1 Owner decisions (2026-10-15 and 2026-10-16, in chat)

| Question | Answer |
|---|---|
| Who maps a source into Metorite? | AI does all the mapping work. The admin gives only the final approval |
| Which AI? | The decide tier, as far as possible (`tier-decide`, `acb_llm.decide()`) |
| May CRM data go to the decide tier? | Yes. D100 (2026-10-13) covers every app. The owner confirmed it for the CRM on 2026-10-16 |
| Who pays? | The customer, in AI credits (D100 item 3) |
| What arrives before a mapping exists? | Everything. Each module and each custom field lands from the first sync |
| Labels in Mirror mode? | The org's own source labels. This amends D95.2 for Mirror mode only. A Native CRM keeps "Companies" |
| How far does a custom module stand in for a core one? | Overlay first (CRM-Q20), promote later (CRM-Q21) |

**Open, with a recommendation (design page, "Your calls").**

1. May a choice that changes only the view apply with no approval? It moves
   no record, and it has Undo. Recommended: yes. Until the owner answers,
   CRM-Q17 applies view choices as display defaults, as today. CRM-Q20 asks
   for every other choice.
2. May Metorite read Zoho's own list views? It needs the scope
   `ZohoCRM.settings.custom_views.READ`, so each connected org must
   reconnect once. Recommended: ask for it on new connections only.
3. May one org's approved choices help another org? Recommended: no.

**Superseded.** CRM-Q7b keeps `tier-fast` because "residency is open (D75)".
D100 closed that question. CRM-Q20 replaces the CRM-Q7b proposer.

#### 18.9.2 What the reviews found

**The app (visual-review, DEWiN's shape, invented values).** The CRM renders
a custom Zoho with no error. But each screen is laid out for a standard Zoho
org. Title, Email and Phone are empty on all 805 contacts, and they are the
default columns. 43 of 81 contact fields are under 20% filled.

A record page shows 76 rows (an Invoice shows 236), mostly empty. Leads draws
22 filter controls. The CRM opens on the Deals board, which DEWiN turned off
in Zoho. A keyboard cannot open a record from any list (P0, all orgs). PR
#1066 fixes that P0, outside this section.

**The engine (token review).** v3 asked about each field of each module.
For DEWiN, that is about 1,650 decide questions and 560k input tokens. v3
also used a larger model for new options. The admin then approved about 1,847 decisions as one
plan. Most of those questions decide nothing:

- A module that stays its own module needs no mapping. The generic store
  (CRM-Q6a) already shows each of its fields. For DEWiN that is 18 of 22
  modules and about 1,300 fields.
- Only an empty core slot needs a role. DEWiN has two: Companies and Deals.
- A target needs one question ("which field fills Email?"), not one question
  for each field. The store already allows one approved rule for each
  target column (`routes/crm/field_rules.py`).
- Code answers most targets: fill rate, value patterns, a unique type, the
  Zoho display field, and the lookup target.
- No choice needs a larger text model. A parameter, such as the first month
  of a fiscal year, is a choice question.

**Real data, after DEWiN's import (2026-10-15, counts only).** The first
run after CRM-Q16 took 7 minutes and 155 credits, and the pool stayed
healthy. CRM-Q16 held Invoices (31,924 records) and Payment Received (8,681)
by itself. Commercials, Quotes and Sales Orders hold 0 records in Zoho.

So the design page's example, "Commercials as Deals", does not fit DEWiN. No
module is a sure candidate for Deals, so the pipeline makes no suggestion,
and CRM-Q17b hides the empty Deals row. The custom modules fill better than
Contacts. Operator Associates has 71 of 127 fields over 80% filled, and 35
under 20%.

The run also found two gaps, H-326 and H-327. Payment Made holds 3,003 of
Zoho's 4,947 records. 1,145 Zoho tasks were skipped, because their parent is
a custom-module record.

**The admin's path (impeccable, apple-design).** The admin must see useful
records at once, and approve a result, not a table of rules. A suggestion
shows where the gap is. No confidence number and no engine word reaches the
screen ("value_map", "plan v1", "0.68").

#### 18.9.3 The fill rank

The fill rank is the one input of CRM-Q17. It needs no AI.

1. After a run changes an entity or a module, the sync computes `filled`,
   `total` and `distinct_count` for each key. One SQL
   statement per entity does it. A key of an entity that did not change keeps
   its last numbers.
2. The keys are the real columns behind the list columns. They also include
   each standard field of the four core entities, each `custom` key, and each
   key of a module's `fields`.
3. Only the sync writes them, in its own tenant session after the module step
   (R5). They live in one new tenant table with FORCE RLS. Not on
   `crm_field_defs`, because a standard column has no row there. Not on the
   source profile, because the profile is versioned and hashed.
4. **Cost bound.** The sync computes an entity again at most once in 24
   hours. An entity whose first import finished in the same run is the
   exception. An R8 test measures the
   statement on 30,000 module rows.
5. `GET /crm/fields/{entity}` and the module list and record routes serve
   them, on each field definition. No request computes them.
6. **Ship dark.** The list `CRM_FILL_RANK_ORGS` names the orgs that get the
   fill rank and the source labels. It ships empty, and `*` means every org.
   An org outside the list gets no fill work and sees the CRM as before. A
   flip is gate `enforcement-flip`.
7. A Native CRM is not in this slice. It never runs a sync, so it needs its
   own writer. A later ticket adds one.

#### 18.9.4 The engine of the mapping: a fixed pipeline

The mapping is one pass with no loop and no planning. It is a pipeline that
calls the decide tier, not an agent. A scripted fake of `decide` tests each
branch.

| Step | What | Tokens |
|---|---|---|
| 1. Shape | The fill rank, plus a pattern class for each field (for example "94% look like an email") | 0 |
| 2. Slots | Find each empty core slot. A slot is empty when Zoho turned the module off, or it holds no records | 0 |
| 3. Code | Answer what code can. A module named like the slot fills it. One field over 90% email-shaped fills `email`. One currency field fills `amount`. The Zoho display field is the name. A lookup names its parent. "Won" and "Lost" are known stage names. A field with 0% fill is never offered | 0 |
| 4. Decide | One request for each entity with an open target. The state holds the candidate fields as named objects. Each question asks which field fills one target, or `none` | about 5 requests |
| 5. Re-ask | A question under 0.75, or with its top two within 0.15, goes again with 5 sample values | about 2 requests |
| 6. Draft | The draft runs on 200 sampled records in memory, for the preview. Nothing changes until the admin approves | 0 |

**Rules for token use.**

1. At most 16 questions share one `state`. One entity fits in one request.
2. The state names each field by path (`` `fields.f7` ``). A question never
   copies a value.
3. A value that looks like a person's email, phone or name sends its pattern
   class only, also in step 5.
4. Confidence comes from the answer's `probabilities` (`acb_llm/decide.py`).
   No question asks for certainty.
5. Each module keeps a hash of its schema. An unchanged schema makes no
   decide call.
6. A run has a token cap. Past it, the run stops, keeps a draft, and the sync
   keeps the last approved plan.
7. A result that stays unsure makes no suggestion. The admin can still pick a
   module by hand.

**The estimate for DEWiN.** About 35 to 45 questions in 5 to 7 requests,
about 25k input tokens, about USD 0.001. The decide client does not return
`usage` today, so the run logs its request ids, and a join to `usage_event`
gives the measured count. One probe sends the same state with 1 question and
with 16 questions, to learn whether the vendor bills the state once.

**What applies with no approval, and what waits.**

| Kind of choice | Example | Rule |
|---|---|---|
| Changes only the view | Columns, hidden empty fields, folded modules, labels | Applies, with Undo (owner call 1) |
| Changes nothing | A new field on a module that stays its own module | Applies. It shows with its module |
| Changes what records mean | Commercials plays Deals. A new picklist value is a Lost stage | Waits for the admin, always |
| Moves records | Promote Commercials into the core tables | Waits for the admin, after a download |

#### 18.9.5 Seams

- **The decide call** goes through `gateway/decide_features.py` (`ask`), and
  never calls `acb_llm.decide` directly. The mapping adds three features:
  `crm.map_role`, `crm.map_target` and `crm.map_stage`. Each starts `off`.
  Each must join `FEATURES`, `REGISTERED` and `ON_FEATURES`, or `on` reads
  as `off`.
- **One `ask()` for each entity**, not one for the run. One failed request
  voids each request of the same call. A 402 starts the cool-down of the
  whole org, so the pipeline checks `cooling()` first and resumes later.
- **Rollout** is `off`, then `shadow`, then `on`, for each feature (D100
  item 2). In `shadow` the pipeline asks "which field fills Email?" on orgs
  where the standard Email is filled. That field is the answer key, and the
  log line holds the agreement count only, never a field name.
- **No text model.** CRM-Q20 has no L3 rung.
- **The source seam** stays `crm_sources/registry.py` (D95.4).
- **Tenancy (R5).** A new table carries `organization_id` with FORCE RLS. The
  organization comes from the server, never from a request.
- **The screens** use the shared parts only. The design page names the part
  of each screen.

#### 18.9.6 Tickets

| Ticket | Slice | Gate | Size | Waits for |
|---|---|---|---|---|
| CRM-Q17a | Lists and records by fill rank, in two slices (Q17a-1, Q17a-2). No AI | AGENT-SAFE | M | — |
| CRM-Q17b | Getting around: rail, first screen, filters, Columns, phone, Sync health. No AI | AGENT-SAFE | M | CRM-Q17a |
| CRM-Q18 | One plan: a table of versions, and the live rule set | AGENT-SAFE | M | — |
| CRM-Q19 | The executor and the 200-record preview | AGENT-SAFE | M | CRM-Q18 |
| CRM-Q20 | The pipeline, the suggestion, the panel, Undo, and Settings → Suggestions | AGENT-SAFE | M + M | CRM-Q19 |
| CRM-Q21 | Promote a module into a core entity | AGENT-SAFE | L | CRM-Q20 |
| CRM-Q22 | Zoho changes as suggestions, then a CSV source, then HubSpot | AGENT-SAFE. The HubSpot app is OWNER-GATE | M + M | CRM-Q20 |

A flag flip to `shadow` or `on` for an org is gate `enforcement-flip`.

**CRM-Q17a · Lists and records by fill rank.** It ships in two slices. The
spec audit of 2026-10-16 narrowed it.

**CRM-Q17a-1 · The backend.** Done when:

1. A migration (next free number at build time) adds the tenant table of the
   fill numbers, with FORCE RLS. It is expand-only and passes
   `test_tenant_coverage.py`.
2. The sync writes the numbers of §18.9.3 for each org in
   `CRM_FILL_RANK_ORGS`, under its cost bound. An R8 test proves each case.
3. `GET /crm/fields/{entity}` (also with `?layout=1`) and the module list and
   record routes serve `fill` and `distinct` on each definition.
4. `GET /crm/fields/{entity}` serves `empty_columns`: the standard keys of
   the entity with `total > 0` and `filled = 0`. The name column and
   `last_activity_at` never show in it. One gateway function gives the set.
5. The CSV export leaves out each key of `empty_columns`, through the same
   function. `test_crm_export.py` stays green, and its static parity test
   does not change.
6. In Mirror mode, one route serves the source's plural and singular labels
   of the four core entities. In Native mode it serves none.
7. An org outside `CRM_FILL_RANK_ORGS` gets none of items 2 to 6.

**Built as** (2026-10-16, migration 274):

- `crm_field_fill` holds one row for each `(organization_id, entity, key)`.
  A custom field of a core entity has the key `custom.<key>`.
- `crm_sync/fill.py` writes the numbers after the module step. The cursor
  row `zoho:fill_rank` keeps the time of each entity, and the changes that
  wait for the 24-hour bound. A field rule that a run applies or undoes also
  changes its entity.
- A value counts as filled unless it is NULL, empty, blank, `[]`, `{}` or
  `false`. A Zoho checkbox sends `false` when nobody ticks it. The number 0
  counts as filled.
- `fill` is the share `filled / total`, from 0 to 1. Both numbers are `null`
  outside the list.
- `distinct` counts the different filled values in a sample of 2,000
  records, the ones with the lowest ids. It is exact up to 2,000 records.
  Above that it is a lower bound, and it never passes 2,000.
- The layout carries `empty_columns`. The plain read is a bare list, so it
  carries `fill` and `distinct` on each row, and no `empty_columns`. A joined
  list column shows in both words, for example `company_id` and
  `organization_name`.
- Stale numbers give `empty_columns: []`. The numbers of an entity are stale
  in four cases. It has no stamp. It is pending. One of its field rules
  changed. A run that started after its stamp changed or re-linked one of
  its rows. The run counts show that last case. They also show a run that
  failed, and a run while the flag was off.
- A run that finds no change moves the `checked` mark of the entity to its
  own start, and the next scan starts there. So an entity that changes in
  most runs often serves `[]`.
- The CSV export drops a key of `empty_columns` only when each row of the
  file has an empty cell for it.
- `GET /crm/mode` carries `labels`, by API slug, in Mirror mode for a listed
  org. In every other case the body has no `labels` key. The route reads only
  `profile -> 'modules'`.
- Cost: `filled` groups by key only. On 30,000 module rows with 236 fields of
  unique values, the statement took 5.4 s in the R8 suite (35.5 s in the
  first build). Each statement runs under a 30 s `statement_timeout`.
- A run computes at most one entity above 5,000 records, and the others wait.
  It commits the stamp first, then reads with no lock on `crm_settings`,
  then writes the numbers in a short page. An entity that fails waits 24
  hours, and the run goes on.

**CRM-Q17a-2 · The screens.** It waits for CRM-Q17a-1. Done when:

1. The list hides each key of `empty_columns`.
2. With no saved choice, the list shows the custom fields with the highest
   fill, up to the column limit. A saved view or a member's choice wins. The
   code tells "nothing saved" from "saved empty".
3. A list cell does not wrap. Long text ends in an ellipsis, and the full
   text is in its title.
4. A record whose owner is not a member shows `custom._owner_name`, with
   "Zoho only". The key `_owner_name` leaves the Fields tab.
5. Overview shows the filled fields first, by fill rank. The Fields tab keeps
   the sections it draws today and has a filter box. It folds the empty
   fields behind "Show n empty fields".
6. The record page leaves out the Zoho system keys `Layout`, `Record_Image`
   and `Tag`. One named constant holds the list.
7. In Mirror mode a core entity shows the source's plural label. For
   example, Contacts shows as "Existing Sites". The rail, the list heading
   and the record show it. A Native CRM shows "Companies" as before.

Backend files: `crm_sync/engine.py` (or a module beside it),
`routes/crm/fields.py`, `routes/crm/modules.py`, `routes/crm/export.py`,
`routes/crm/core.py` and `tests/unit/test_crm_export.py`.

Frontend files, under `src/app/crm/`: `lib/columns.ts`, `lib/fields.ts`,
`lib/views.ts`, `RecordList.tsx`, `FieldsPanel.tsx`, `RecordSheet.tsx`,
`ModuleRecordSheet.tsx` and `CrmRail.tsx`.

**CRM-Q17b · Getting around.** Done when:

1. The custom modules fold into one rail group, with the modules that hold
   records first. Reports and CRM settings stay in view at 1440 × 900.
2. A core row that Zoho turned off and that holds no records leaves the rail.
3. `/crm` opens on the first row that holds records.
4. An entity with no records says why: "Deals is turned off in your Zoho", or
   "No records yet". "Nothing matches this view" shows only with a filter.
5. The filter bar shows Owner and the 3 fullest picklist filters. The rest
   sit behind "Filters (n)".
6. The Columns picker has a search box, shows the chosen fields first, and
   shows "% filled" beside each field. A disabled box looks disabled.
7. At phone width, each list row is a two-line card.
8. Sync health opens with a "Needs you" block: held, large and turned-off
   modules, each with one action. The Sync switch of a turned-off module is
   disabled. The detail table folds.

Each item is checked with the `visual-review` skill in light mode, compact
density, a changed accent and 375 px.

**CRM-Q18 · One plan.** The spec audit of 2026-10-16 found that rows of
versions do not fit the unique indexes of `crm_field_rules` under R6. So the
versions live in a new table, and `crm_field_rules` stays the live set. It
ships in three slices.

**Design (2026-10-16).**

- A new tenant table `crm_mapping_plans` holds the versions of the plan of
  each org. Each row has a version number, a state, a JSON snapshot of the
  rules, and who made and approved it, and when.
- The states are `draft`, `approved` and `superseded`. One version at most
  is `approved` for each org.
- `crm_field_rules` stays the live set, and its two indexes do not change.
  A draft never writes a row with `status = 'approved'`.
- An approve writes the snapshot into `crm_field_rules` in one transaction.
  A rule that exists keeps its row and its id. A rule that leaves the plan
  gets `status = 'rejected'`, so the sync undoes it through the path it has
  today.
- "Go back" rolls forward. It approves a new version N+1 with the content of
  version N-1.
- In this ticket each rule of a snapshot has the kind `field`. CRM-Q20 adds
  the other kinds of §18.9.3 to the snapshot only.
- The first read of the plan of an org builds version 1 from its approved
  rules. The migration writes no data.
- The rows with the status `proposed`, `rejected` or `stale` stay as they
  are. They are not in a snapshot.
- The approve and reject of one rule (`PATCH /crm/field-rules/{id}`) stay.
  Each one also writes a new approved version, so the history stays whole.
  The CRM-Q7b proposer still writes `proposed` rows.

**CRM-Q18-1 · The backend.** Done when:

1. A migration (276 on 2026-10-16, next free at build time) adds
   `crm_mapping_plans`, with FORCE RLS. It is expand-only, and it passes
   `test_tenant_coverage.py`. A partial unique index allows one `approved`
   version for each org.
2. `GET /crm/mapping/plans` lists the versions. The first call for an org
   builds version 1 from its approved rules.
3. `POST /crm/mapping/plans` makes a draft from the approved version and a
   list of changes. A draft changes no row of `crm_field_rules`.
4. `POST /crm/mapping/plans/{version}/approve` approves a draft, in one
   transaction, as the design says.
5. `POST /crm/mapping/plans/{version}/restore` approves a new version with
   the content of an older one.
6. A per-rule PATCH writes a new approved version.
7. Each route is for an admin in Mirror mode. The org comes from the server.
8. R8 tests prove each item. They prove that a draft changes nothing in the
   sync. They prove that a restore undoes a rule through the existing path.
   They
   prove that the ids of kept rules do not change, and that orgs stay apart.

Built as (2026-10-16): migration 277 adds `crm_mapping_plans`.
`routes/crm/mapping_plans.py` holds the four routes, and its `commit` is the
one approve. `POST /crm/field-rules` and the PATCH call it too. A reject of a
rule that is not approved writes no version. A draft approves only while it
is built on the approved version, else 409 `plan_moved`. When the sync makes a
rule stale, the next plan read or change writes a new approved version by
`system`. Fence: `tests/unit/test_crm_map_plan_r8.py`.

Fix round (2026-10-16): `GET /crm/mapping/plans` lists the versions with
`rule_count` and no snapshot. `GET /crm/mapping/plans/{version}` returns one
snapshot, and CRM-Q18-2 reads it. The approve and the sync's `mark_stale` lock
the rule rows through `core.FIELD_RULE_LOCK_SQL`, in one order. A PATCH takes
that lock before it holds one row. `commit` locks the rows before it reads the
profile. A deadlock is a 409 `plan_busy`, and a retry is safe.

**CRM-Q18-2 · The Field map screen.** It waits for CRM-Q18-1. The Field map
card shows the approved version and a draft. It approves a draft with one
action, and it goes back to the version before.

**CRM-Q18-3 · A column that is empty opens.** It waits for migration 274
(CRM-Q17a-1). Done when:

1. At approve time, a fixed column also opens to a rule when its fill is 0.
   The fill comes from `crm_field_fill`, with `total` above 0. No fill row keeps
   the column closed.
2. A rule that this opened never goes stale only because its standard field
   is on a layout. The value of the fixed map still wins where Zoho fills
   it.

Files: `infra/postgres/`, `routes/crm/field_rules.py`, a new
`routes/crm/mapping_plans.py`, `crm_sync/field_rules.py`,
`crm_sources/zoho/mapping.py` (CRM-Q18-3 only), and
`FieldMapCard.tsx` (CRM-Q18-2 only).

Verify CRM-Q18 with `uv run pytest tests/unit/test_crm_field_rules.py tests/unit/test_crm_field_rules_r8.py tests/unit/test_crm_field_map_ai.py tests/unit/test_crm_upsert_from_source_fence.py tests/unit/test_crm_map_plan*.py -q`
on the R8 database, with no test skipped.

**CRM-Q19 · Executor.** Done when:

1. Each transform (`copy`, `cast`, `value_map`, `normalize`, `coalesce`,
   `link`, `owner`) is a pure function with its own unit test.
2. The sync applies the approved plan through the executor, and no other
   path maps a value.
3. `POST /crm/mapping/preview` applies a draft to up to 200 sampled records
   in memory. It returns the records as the board would draw them, and the
   count of values that did not map. It writes nothing.
4. A value that no rule maps is counted on the Sync health card.

**CRM-Q20 · The pipeline and the suggestion.** Done when:

1. The pipeline runs the six steps of §18.9.4. A scripted fake of `decide`
   tests each step.
2. An unchanged schema makes no decide call. A run stops at its token cap and
   keeps a draft.
3. In `shadow`, the agreement count is logged, with no tenant text.
4. `evals/crm_mapping/` holds synthetic CRM schemas with expected answers,
   and passes in `--scripted` mode in CI.
5. An empty core slot with a sure candidate shows the suggestion to an
   admin, in its empty state. An example is "Preview Commercials as Deals". A
   member sees why the slot is empty, and no suggestion.
6. The panel shows three facts and the preview of CRM-Q19 on the real board
   card. It also shows at most 3 checks, and "What changes". One action
   approves it.
7. An approved overlay has Undo in a toast and in Settings → Suggestions.
   Undo is exact, because no record moved.
8. No confidence number and no engine word shows on a screen.
9. The CRM-Q7b route and its proposer are removed.

**CRM-Q21 · Promote.** Done when:

1. An approved overlay can move that module's rows from `crm_module_records`
   into the core table, through the executor.
2. It runs one module at a time, under a CRM-Q16 hold.
3. A download comes first. A promote is undone only by a start-over of that
   module.

**CRM-Q22 · Changes and a second source.** Done when:

1. A new field, a new picklist value or a failing rule makes a suggestion
   under the rules of §18.9.4.
2. A record with a new stage value that waits shows in a "Not sorted" lane.
3. A CSV source adapter maps a file through the same plan and pipeline.
4. A HubSpot adapter follows. Its OAuth app is OWNER-GATE.

#### 18.9.7 Verification

- Backend: `uv run pytest tests/unit/test_crm_map_*.py tests/unit/test_crm_fill_rank*.py -q`,
  with the R8 database (`bash scripts/dev_db.sh`), and confirm that no test
  is skipped.
- Registry: `uv run pytest tests/unit/test_email_decide_shadow.py tests/unit/test_feed_decide.py -q`.
  The email and My Day features must behave as before.
- Evals: `uv run python -m evals.crm_mapping --scripted`.
- Frontend: `npx tsc --noEmit && npx vitest run src/app/crm` in
  `workbench/control_plane`.
- Screens: the `visual-review` skill on each changed surface.

### 18.10 Activities on a module record, and a short read that shows (CRM-Q23, CRM-Q24, 2026-10-16)

**Status (2026-10-16).** CRM-Q23a: built, on the branch `crm-q23`. CRM-Q23b:
built, on the branch `crm-q23b`, with no migration. CRM-Q23c and CRM-Q24:
specified, not built. Verified against code at
`origin/main` `0a14f7552`. Found on DEWiN's import (H-326, H-327).

#### 18.10.1 What the import found

**Activities.** `mapping._activity_links` links a task, a note, a call or a
meeting through `Who_Id` and `What_Id`. A `What_Id` resolves only through
`_MODULE_ENTITY`, which holds Accounts, Contacts, Leads and Deals. An activity
whose parent is any other module finds no target. `_write_record` then counts
it `unlinked` and does not store it. The CHECK `crm_activities_target_required`
(`144_crm.sql`) enforces the same rule.

DEWiN's import read 1,145 tasks and linked none. It read 3,848 notes and
linked 1. DEWiN's notes total 18,674, and other activities add more. They
would be lost the same way, so the `activities` hold stays until CRM-Q23
ships.

**Probe of 2026-10-16 (2 credits).** One page of DEWiN's Zoho Notes showed
`$se_module` set to `Operator_Associates` on all 200 rows. So a note names
its custom module by its API name, as CRM-Q23a assumes. One page of Tasks
showed no `What_Id`, no `Who_Id` and no `$se_module` on all 200 rows. All
200 had one owner and the status "Not Started".

**Owner decision (2026-10-16, in chat).** A Zoho task with no parent record
and no person stays out of Metorite. Metorite never writes to the customer's
Zoho to clean them up. CRM-Q24 shows their count.

**Counts.** Zoho counted 4,947 Payment Made records, and the list returned
3,003. A probe on 2026-10-16 found the cause. The list of v2 and of v8 both
end at the same newest record, and no record waits for approval. Products
gives 9 records to the list and 13 to the count. So the connected Zoho user
cannot see the rest, and Zoho's count ignores that. No code can read a record
that the user cannot see.

#### 18.10.2 Tickets

| Ticket | Slice | Gate | Size | Waits for |
|---|---|---|---|---|
| CRM-Q23a | Link an activity to a module record, and serve it. Backend | AGENT-SAFE | M | — |
| CRM-Q23b | Recover an activity dropped before its module record landed | AGENT-SAFE | M | CRM-Q23a |
| CRM-Q23c | The timeline on a module record page. Frontend | AGENT-SAFE | S | CRM-Q23a |
| CRM-Q24 | A short read shows on the Sync health card | AGENT-SAFE | S | — |

**Not in scope of CRM-Q23.** A composer or any write on a module record. A
push of a module activity to Zoho. A next-activity chip on a module card. The
release of DEWiN's `activities` hold, which stays an admin act. Native mode.

**CRM-Q23a · Activities on a module record, backend.** The spec audit of
2026-10-16 narrowed it. Done when:

1. A migration takes the next free number at build time (275 on
   2026-10-16). It adds `crm_activities.module_record_id`, shown below, and a
   partial index where it is not null. It is expand-only (R6).
   `module_record_id UUID NULL REFERENCES crm_module_records (id) ON DELETE CASCADE`
2. One `DO` block drops `crm_activities_target_required`, adds it again with
   `module_record_id` as `NOT VALID`, and validates it. The block runs only
   when `pg_get_constraintdef` lacks `module_record_id`. A second run changes
   nothing.
3. `_activity_links` adds a module candidate when `$se_module` is not one of
   the four core modules. The engine resolves it in `crm_module_records` by
   module and `ext_id`, in the tenant session. A module that is not mirrored,
   or a record not read yet, stays unlinked. A custom `$se_module` never falls
   back to the core candidates. A module parent gets no `_BUMP_SQL`.
4. Each run counts the dropped activities by `$se_module`, as numbers only,
   under the counts key `activity_drops`. Sync health does not show it as an
   entity row. A test proves that `GET /crm/sync/health` still serves.
5. `GET /crm/modules/{module}/records/{record_id}/timeline` serves the
   activities of a module record. It is read only. An unknown module or
   record gives 404. It keeps tenant isolation. `ActivityModel` gains
   `module_record_id`.
6. Each reader that assumes a core parent still works, and a test covers
   each. The readers are the activity edit and delete routes, the
   next-activity statements, the export and `sync_zoho.push_activities`.

**Built as (2026-10-16).** Migration 275 holds the column, the index and the
CHECK. `mapping._activity_links` gives the candidate `module_record_id` with
the entity `module:<ApiName>`, and `Mapped.parent_module` keeps the name.
`engine._local_id` resolves it only for a module that the profile mirrors.
The drop key is `(none)` when an activity names no module. `activity_drops`
is in `NOT_ENTITY_KEYS` of `routes/crm/sync.py`, so the `failed` sum skips it
too. The route reuses `activities.activity_entries_by` and `merge_page`. The
readers of done-when 6 needed no change. A module-only row never matches
their column. The mirror stamps `zoho_id`, so the push skips the row.
`schema.generated.sql` stays stale on purpose. Fences:
`test_crm_module_activities_r8.py`, `test_crm_migration.py` and
`test_crm_sync_engine.py`.

**CRM-Q23b · Recover a dropped activity.** The spec audit of 2026-10-16
narrowed it. Done when:

1. A dropped activity whose `$se_module` names a mirrored module is kept in
   the cursor state of its entity. The entry is
   `{id: [module, parent_ext_id, first_seen]}`, at most 5,000 for each entity.
   The final-page state, the mid-read state and
   `_KEPT_WHEN_UNAVAILABLE` keep it. A unit test proves each of the three.
2. After `_module_records`, one tenant-session query selects the kept entries
   whose parent now exists. Only those are read again, 100 ids in each call
   (`GET /crm/v2/{Module}?ids=a,b,…`). The step skips a held key
   (`_skip_held`) and stops at `MODULES_RESERVE`. A linked id leaves the list.
   An id older than 7 days leaves it, and a later re-add keeps its first
   `first_seen`. A test proves that 0 recoverable entries make 0 provider calls.
3. An overflow queues one full read through the re-read list. It does so only
   when the first import of the activity entity is done. Every mirrored module
   named in the list must also have `initial_done_at`. It queues at most
   one time in 7 days for each entity. A test proves that a second overflow
   inside 7 days queues nothing.
4. An R8 test proves a new module record and a new note in one run. The note
   links in the same run's recovery step.
5. Take an org with no `zoho:activity_backfill` cursor row, whose profile
   mirrors at least one module. The sync queues one full read of note, call,
   meeting and task. It writes that row in the same transaction. A second run queues
   nothing. An R8 test proves that a hold on `activities` keeps the entry until
   release.

Files: `crm_sources/base.py`, `crm_sources/zoho/client.py`,
`crm_sync/engine.py`, a new `crm_sync/recover.py`, and the tests
`test_crm_activity_recover.py`, `test_crm_activity_recover_r8.py` and
`test_crm_sources_zoho.py`. No migration: the cursor state is `jsonb`.

**Built as (2026-10-16).** The kept list is the key `recover` of the activity
cursor, and `recover_overflow` marks an overflow. `crm_sync/recover.py` holds
the list rules. `engine._recover_activities` runs right after
`_module_records`. `zoho:activity_backfill` holds `queued_at` and
`overflow_at`.

The one full read runs before the activity passes, so on a new org it is the
first import and costs no second read. When an answer by ids lacks an id, the
sync asks for it one more time, and then the id leaves the list. The probe of
2026-10-16 proved the `ids=` read on 3 ids. The cap of 100 comes from Zoho's
documents, and no probe tested it.

Fix round 1 (review of 2026-10-16) stops three loops that spent credits:

- An overflow queues no full read while a module that its list names is
  unavailable. `_mark_unavailable` stamps `initial_done_at` too.
- A refused read by ids pauses that entity for 24 hours
  (`recover_paused_at`). A plain HTTP 401 or an untrusted host fails the run.
- A kept id that a read returns, and that stays on the list, spends one try
  (`recover_tries`). It leaves the list at `RETRY_LIMIT` tries.

A recovery read takes the drop of the same run back out of
`activity_drops`, so the count shows only what the run lost. The step takes
an id that a read by ids cannot send off the list before the call. A source
test fences the `organization_id` predicate of the recovery query. A kept
entry on an unavailable module is still read when its parent row is here.
That read goes to the activity module, not to the refused module, and the
activity then links and leaves.

Fix round 2 (review of fix round 1) closes one more loop. The kept list can
expire in the run that the 7 days of the last overflow read lapse. An empty
list names no module, so the guard let the full read through. Now
`recover_overflow_modules` keeps the modules of the drops that overflowed,
beside `recover_overflow`. The guard checks those modules too. A held module
now holds the guard, as an unavailable module does.

Fix round 3 (review of fix round 2): the guard also waits while a module
that it checks is mid-read. A module that comes back can stop at
`MODULES_RESERVE` before all its records are in.

**CRM-Q23c · The timeline on a module record.** Done when:

1. `ModuleRecordSheet.tsx` shows the timeline of CRM-Q23a, read only.
2. `Timeline.tsx` gets a read-only mode with no composer. `lib/api.ts` and
   `lib/modules.ts` get the fetch.
3. The `visual-review` skill checks the record page in light mode, compact
   density and 375 px.

**Verify CRM-Q23 with:**

- `bash scripts/dev_db.sh && eval "$(bash scripts/dev_db.sh --export)"`
- `uv run pytest tests/unit/test_crm_migration.py tests/unit/test_crm_sync_engine.py tests/unit/test_crm_sync_engine_r8.py tests/unit/test_crm_sync_holds_r8.py tests/unit/test_crm_hidden_module_r8.py tests/unit/test_crm_module_records_r8.py tests/unit/test_crm_modules_routes_r8.py tests/unit/test_crm_next_activity_r8.py tests/unit/test_crm_export.py tests/unit/test_crm_reset_r8.py tests/unit/test_tenant_coverage.py -q`,
  with the new R8 suite. Confirm that no test is skipped.
- `uv run ruff check` on each changed file.
- For CRM-Q23c: `npx tsc --noEmit && npx vitest run src/app/crm` in
  `workbench/control_plane`.

**CRM-Q24 · A short read shows.** Done when:

1. When a full read of a module ends with fewer records than Zoho's count, the
   cursor records `short_by`.
2. The Sync health card shows it: "Zoho counts 4,947. The connected Zoho user
   can read 3,003. Connect with a Zoho admin to read all of them."
3. A read of more than 2,000 records inside one second, with no
   `next_page_token`, raises a named error. It never ends the walk as done.
   A unit test fences it.
4. Sync health shows the activities that have no parent: "1,145 Zoho tasks
   belong to no record. Metorite leaves them out." It reads the `(none)`
   count of `activity_drops`.
### 18.11 Insights on the mirrored data (CRM-Q25 to CRM-Q28, 2026-10-16)

**Status (2026-10-16).** CRM-Q26: built on the branch `crm-q26`, dark.
CRM-Q27 and CRM-Q28: specified, not built. CRM-Q25: not built on this
branch. Verified against code at `origin/main` `02223e3e2`.

**Owner direction (2026-10-16, in chat).** Use the decide, balanced and
powerful tiers to run inference on the CRM data and produce insights. Build
as much as possible the same day.

#### 18.11.1 The rule: numbers come from SQL

A model never adds up records, and it never writes SQL. A number that a
member reads comes from one typed aggregate route. The model chooses a
module, a measure, a field to group by and filters, by name. The route
checks each name against the field definitions of the org, and it binds
each value. Embeddings help the model find text. They never make a number.

| Need | Mechanism | Tier |
|---|---|---|
| Totals, trends, aging and top lists | CRM-Q25, the typed aggregate over every module and field | No model in the query. The chat agent calls it |
| Labels on note text | CRM-Q26, decide questions when a note arrives | Decide |
| Search of note text by meaning | CRM-Q27, embeddings through `kb_index.embed` | Embedding model |
| A weekly digest | CRM-Q28, a scheduled run over CRM-Q25 numbers | Powerful |
| Questions in chat | The CRM agent with its tools (CRM-C7) | Balanced |

#### 18.11.2 Tickets

| Ticket | Slice | Gate | Size | Waits for |
|---|---|---|---|---|
| CRM-Q25 | The typed aggregate over every module and field, and its AI tool | AGENT-SAFE | M | — |
| CRM-Q26 | Labels on notes, on the decide tier, in shadow | AGENT-SAFE | M | CRM-Q23a |
| CRM-Q27 | Search of note text by meaning | AGENT-SAFE | M | CRM-Q26 |
| CRM-Q28 | A weekly digest on the powerful tier | AGENT-SAFE | M | CRM-Q25 |

CRM-Q27 and CRM-Q28 get their done-when lists in a later edit of this
section. To build each ticket is AGENT-SAFE. For CRM-Q26, a mode other than
`off` for `crm.note_labels` is an owner flip, because each request spends the
org's credits (CLAUDE.md §3a rule 3). So is a name in `CRM_DECIDE_ORGS`. The
grant `enforcement-flip` does not cover them. `work_plan.md` §6 registers both.

**Not in scope.** A write to the source. A model that writes SQL. A number
that comes from a model. A per-member record scope (§18.5, CRM-Q12).

**CRM-Q25 · The typed aggregate.** Done when:

1. `GET /crm/aggregate` also takes a generic module (`module:<ApiName>`) as
   the entity. A module that the profile does not mirror gives 404.
2. The measure can be `count`, or `sum`, `avg`, `min` or `max` of one field.
   A field measure takes only a field of type number, decimal, currency or
   percent.
3. `group_by` can also name one field. A picklist, a boolean, a lookup and
   the owner group by value. A date or a datetime groups by `month`,
   `quarter` or `year`, given as `bucket`. Any other type gives 422.
4. A filter can name a field with one value, or a date field with a range.
   The core entities keep the list filters they have today.
5. Each field name is checked against the field definitions of the org, or
   of the module. A name never reaches the SQL text unchecked. Each value is
   bound. A test sends names that try to inject SQL, and each one gives 422.
6. An answer holds at most 50 groups, largest first, and one `other` total.
   The statement runs under a LOCAL `statement_timeout` of 10 s.
7. The AI tool `crm_aggregate` takes the new arguments.
   `crm_list_fields` and the module record tools tell the model the legal
   names and types.
8. R8 tests cover a sum and a group on a module, a date bucket and a
   filter. They also cover tenant isolation, and the time on 30,000 module
   rows.

Files: `routes/crm/aggregate.py`, `routes/crm/modules.py`,
`routes/crm/fields.py`, the CRM tools module (`crm_tools.py`) and
`tests/unit/`.

**CRM-Q26 · Labels on notes, in shadow.** Done when:

1. The decide registry (`gateway/decide_features.py`) gains the feature
   `crm.note_labels`, in a new tuple `CRM_FEATURES`. It joins `REGISTERED`
   and `ON_FEATURES`, and it starts `off` for every org. It does not join
   `FEATURES`. That tuple is the email set: two email suites pin it, and the
   email start-up check `configured_on()` reads it.
2. A new setting `crm_decide_orgs` (`CRM_DECIDE_ORGS`) lists the orgs that may
   run a `crm.` feature. It is empty by default. For a `crm.` feature,
   `mode_for` reads it and not `DECIDE_FEATURE_ORGS`. So a change of mode
   never starts every CRM org at once.
3. A migration (next free number at build time) adds the tenant table
   `crm_note_labels`. It has FORCE RLS and an inline foreign key to the org. Its
   `activity_id` is unique, and it references `crm_activities (id)` with ON
   DELETE CASCADE. A row holds the kind, the probability of "needs action",
   the urgency, and the confidence and probabilities of each. It also holds
   `body_md5` of the labeled text, `labeled_at` and `request_id`.
4. The CRM start over deletes the table (`core.RESET_TABLES` and
   `settings._RESET_DELETES`). The generated phases carry it.
5. After a sync run, a background step labels the new or changed notes. It
   runs only for an org whose feature is `shadow` or `on`. It runs outside
   the run lock: after `engine.run_org` in `scheduler._run_one`, and after the
   run that `scheduler.spawn` starts for a request. It binds
   `tenant_session(org)`.
6. A note is new when it has no label row. It has changed when the md5 of its
   subject and body differs from `body_md5`. The step takes the newest first
   (`COALESCE(ext_modified_at, created_at)`). It takes at most 200 notes in one
   step. Each org has a cap of requests for each UTC day, counted before each
   request goes out, so a billed request that fails still counts. The cap is
   1,000 notes over 5 notes a request (`DAILY_NOTES`). While more than 1,000
   notes wait (`BACKLOG_THRESHOLD`), the cap is 5,000 notes
   (`BACKLOG_DAILY_NOTES`, owner decision of 2026-10-16).
6a. A batch that fails for a cause of its own counts one failure for each of
   its notes. Such a cause is an invalid request, an answer that the step
   cannot read, or a timeout. A failed note goes to the back of the queue.
   After 3 failures it waits 7 days, or until its text changes, so one bad
   note cannot block the queue.
7. In `shadow` and in `on`, the step calls `decide_features.ask()`, because
   no old path exists. It names `module_slug="crm"`. It names the
   `connected_by` of the Zoho connection as a proven member, as email names
   the mailbox owner. With no member, the step skips and logs a count. The
   member must still be an active member of this org
   (`my_day_loop.departed`). When not, the step skips, logs a count, and
   spends nothing, because the Console bills the org of the member.
8. One decide request holds up to 5 notes, and 3 questions for each note.
   The first question is a choice of kind: payment, site issue, commercial,
   follow-up, internal or other. The second is a boolean, "needs action". The
   third is a score of urgency with 3 levels. The step clips the subject and
   the body with `clip_fact`, and the body to 2,000 characters. A question
   names its note by its id in `state.notes`.
9. In `shadow` and in `on`, no route returns the labels, and a test proves
   it. A later ticket reads them on the timeline and in CRM-Q25.
10. Each step logs counts and request ids only, never note text. For a `crm.`
    feature, only a 402 starts the cool-down of the registry, and the step
    stops. A 403 starts no cool-down, so it never pauses email `on` for the
    org.
11. `evals/crm_note_labels/` holds synthetic notes with expected labels. It
    passes with `uv run python -m evals.crm_note_labels.run --scripted`.
12. R8 tests cover the table and its RLS, and a step that labels notes
    through a fake decide. They also cover the 200 cap, the daily cap, a
    changed note, the feature `off` path and tenant isolation.

Files: `gateway/decide_features.py`, `acb_common/settings.py`, a new
`gateway/crm_sync/note_labels.py`, `gateway/crm_sync/scheduler.py`,
`routes/crm/core.py`, `routes/crm/settings.py`, the migration and
`infra/postgres/generated/`, `evals/crm_note_labels/` and `tests/unit/`.

**Built as (2026-10-16, branch `crm-q26`, not merged).** Migration 278 adds
the table. `gateway/crm_sync/note_labels.py` holds the step, and
`scheduler.start_labels` starts it. The mode ships `off`, and
`CRM_DECIDE_ORGS` ships empty. A source fence and an R8 read of a company
timeline prove item 9. The model sweep of the eval is not built.

Fix round (2026-10-16). The cap counts requests in Redis, as My Day counts
its calls, under the key of the UTC day, with a life of 26 hours. The
constants are `DAILY_NOTES` 1,000, `BACKLOG_DAILY_NOTES` 5,000 and
`BACKLOG_THRESHOLD` 1,000. With them DEWiN's 18,674 notes take about 4 to 5
days, not 19. A failing batch counts `failures` on its rows, and
`MAX_FAILURES` 3 and `PARK_DAYS` 7 park it. `decide_features.last_no_answer`
names the cause of a failure. The step checks the member with
`my_day_loop.departed`, and a failed read spends nothing.

Round 2 (2026-10-16). A note that failed before goes out in a request of
its own, so only a poison note parks and its batch-mates land. A failure
row clears every label column and the request id, so it never pairs a new
md5 with the label of an older text.

#### 18.11.3 Verification

- `bash scripts/dev_db.sh && eval "$(bash scripts/dev_db.sh --export)"`
- `uv run pytest tests/unit/test_crm_aggregate*.py tests/unit/test_crm_note_labels*.py tests/unit/test_crm_mirror_run_tools.py -q`,
  with no test skipped.
- `uv run pytest tests/unit/test_email_decide_shadow.py tests/unit/test_email_decide_questions.py tests/unit/test_feed_decide.py -q`.
  The email and My Day features must behave as before.
- `uv run python -m evals.crm_note_labels.run --scripted`
- `uv run python scripts/affected_tests.py --run`

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
| CSV import (CRM-S3), merge (CRM-S5), field editor (CRM-S6, superseded by CRM-C4), web forms (CRM-S8), field history (CRM-S10) | The §6 rows | Native mode |
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

### 19.5 CRM-X1: Find and page (✅ built 2026-10-12)

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

**Built as.** The scope and the nine items are built, and each fence went red
first. The review added three rules. A short or empty page ends "Load more"
(`hasMore`), because a sync can move a row between two reads. A write re-reads
as many rows as the list showed, up to 100. A query with 4 digits or more also
matches a phone on its digits alone, through a second bound parameter. The
visual review found one defect: a pause after a space removed the space. The
pure rule is now in `lib/filterBar.ts` (`searchBoxSync`). On Leads, "Last
touched" sits before the converted badge. Two findings stay open. The search
box makes the Closing range wrap at 1440, and a `blue` stage follows the accent
until CRM-U3.

### 19.6 CRM-X2: The CRM joins the shell (✅ built 2026-10-12)

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

**Built as.** The scope and the eleven items are built. Each new fence went
red first, or a mutation of the code made it red. The R8 suite also proves
item 4 on a real database, and the sort and the merge of the §19.10 notes.
`app/crm/components/crmX2.test.ts` holds the browser half. The slice adds no
migration.

Four rules came from the build.

- `core.run_list` takes `rows_only`. It skips the count, the provider link and
  the next activity, and `search_records` uses it.
- The bar puts `/crm?do=<job>` in place of the whole query. So the page keeps
  the view from before the job link, and `log-call` reads the open record
  from it.
- The jobs change the address with `history.replaceState`, as
  `shell/doJob.tsx` does. No new `router.push` is in `app/crm/`.
- The command bar gives focus back to `<main>` when it closes. So the page
  waits until no dialog is open, and then it focuses the search box
  (`lib/shellJobs.ts`, `afterDialogsClose`).

The hint of `log-call` is one line above the Deals list, because the toast
has no neutral kind. The gate mounts the four jobs from one list, so each job
says why the CRM does not open. `agent: "crm-assistant"` feeds the run badge
only, because the assistant dock (NS-6 slice 6b) is not built. One finding
stays open: "Show all in …" of the shell loses its focus to `<main>` in the
same way.

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

### 19.11 CRM-X0: Setup, connection and start over (owner report, 2026-10-12) (✅ built 2026-10-12)

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

---

## 20. CRM configuration and AI reach (2026-10-12)

**Bottom line.** Seven slices answer the owner's eight points of 2026-10-12.
They are CRM-C1 to CRM-C7. C4 has four parts: C4a, C4p, C4b and C4c. Dispatch
C7, C2 and C3 first, in parallel. §20.3 gives the order of the rest. Only
C5 writes to a third party, and only the owner turns it on for an org.

| Owner's point (2026-10-12) | The slice that answers it |
|---|---|
| 1. Settings is one wall of cards | **CRM-C1** Settings sections in the rail |
| 2. Stage order and probability, and the lead sequence | **CRM-C3** Stages and statuses you can shape |
| 3. Fields for Deals and Leads, synced to Zoho | **CRM-C4** Field studio, and **CRM-C5** the Zoho write |
| 4. A sound system of fields and mappings | **CRM-C4** and **CRM-C6** Field mapping |
| 5. One Kanban board, with a stage colour that shows properly | **CRM-C2** One board |
| 6. Any agent can work on the CRM data | **CRM-C7** AI reach |
| 7. Remove `agent-sales-assistant` | **CRM-C7** item 7 |
| 8. What the CRM still lacks | §20.11. No contract |

The ids CRM-C1 to CRM-C7 were free. No `CRM-C<n>` id was in this spec or in
`work_plan.md` on 2026-10-12. The owner answers of §19.4 bind here too.
"Build it, flag off" covers the Zoho write of C5. "Metorite-only setting
(idle days)" sets the pattern of the C3 overlay.

### 20.1 Measured state (2026-10-12, at `144dc0d1f`)

Frontend paths are relative to `workbench/control_plane/src/`. Gateway paths
are relative to `apps/services/gateway/gateway/`.

**Settings.** `?tab=settings` is in `NON_ENTITY_TABS` (`app/crm/lib/urlState.ts:82`).
The URL has no `section` parameter.
`app/crm/components/PipelineSettings.tsx:150` (measured 2026-10-13) draws
every card on one page: Setup, Access, the stage cards, Fields and Sync
health. `CrmRail.tsx:172` draws the settings row last. Saved views already
nest at depth 1 through `RailRow`.

**Stages.** No status table has a column that hides a row.
`core.assert_writable` (`routes/crm/core.py:1723`) refuses every Mirror write
to `MIRROR_SETTINGS_TABLES` (`core.py:1663`). `orderLanes`
(`app/crm/lib/board.ts:115`) sorts the open group of a Mirror board by
probability.

**Fields.** `crm_field_defs` (`244_crm_mirror_columns.sql:92`) describes custom
fields only, and only the sync writes it. `options` is a list of
`{label, value}`, and a record stores the `value`. `GET /crm/fields/{entity}`
(`routes/crm/fields.py:53`) is read only. `POST /crm/leads/{id}/convert`
(`routes/crm/pipeline.py:608`) copies the fixed columns only.

**Boards.** `app/projects/components/TaskBoard.tsx:949` and
`app/tasks/components/TaskBoard.tsx:282` draw a column with `overflow-hidden
rounded-lg`. A `h-1 w-full` stripe sits on top (`:955` and `:294`). The
rounded corner clips the stripe into a sliver at each end. That is the
owner's complaint. `app/crm/components/KanbanBoard.tsx:124` draws a third
column shape, and its card at `:257` is not `TaskCardShell`.

**Agents.** `crm-assistant` (`apps/agents/agent-crm/agents.py:1003`) has 4
read tools and 4 write tools. `search_crm` caps a page at 25 (`agents.py:417`).
The gateway allows 100 (`core.MAX_PAGE_SIZE`). No tool reads the modules, the
reports, the field labels, the pipelines, the deal contacts, the owners or a
total. `acb_skills/skill_families.py` has no `crm` family.

**Nine facts that differ from the audit notes.** The code is the fact.

1. `ZOHO_READ_SCOPES` is at `routes/crm/connection.py:143`. Its uses are at
   `:422`, `:511` and `:547`. §19.9 cites `:98-115`, `:312`, `:354` and
   `:365`, which are old.
2. **H-171 is closed.** #439 serves the product's own AI through the Router.
   So `acompletion_with_fallback` bills, and the warning in §9 is old.
3. `agent-sales-assistant` is named in 23 files outside the docs, not 3.
   It also has a row in `dynamic_agents`. CRM-C7 item 7 lists them all.
4. **A Mirror stage keeps its first order.** `_STATUS_INSERT_SQL`
   (`crm_sync/engine.py:740`) ends in `ON CONFLICT ... DO NOTHING`. A later
   Zoho reorder or probability change never reaches the mirror. Only
   `_FORECAST_SQL` (`:748`) and `_STAGE_TYPE_SQL` (`:756`) update a row
   that exists, and they write `forecast_category` and `type` only.
5. **A stage probability does not move a mirrored forecast.** A Zoho deal
   carries its own `Probability` (`mapping.py:307`). `weightedDeal`
   (`board.ts:243`) reads the deal value first. CRM-C3 item 10 keeps that
   rule.
6. **Every mirrored stage is `gray`.** The sync never writes `color`, and the
   column default is `gray`. So the type fallback never colours a Mirror lane.
7. **The sync resets field order and visibility.** `_FIELD_DEF_SQL`
   (`engine.py:1712`, measured 2026-10-13) overwrites `position` and
   `visible` on each run.
8. **A member cannot read the mode.** `GET /crm/settings`
   (`routes/crm/settings.py:277`) needs an admin.
9. **The palette exemption is written down.** `src/lib/sharedTaskUi.test.ts:471`
   names the CRM map in `board.ts:26` and asks for the exemption to go.

### 20.2 The slices

| Id | What the user gets | Gate | Size | Migration | Waits for |
|---|---|---|---|---|---|
| **CRM-C1** Settings sections | Child rows under the settings row. One section on each page | AGENT-SAFE | S | None | CRM-C3, because both edit `PipelineSettings.tsx` |
| **CRM-C2** One board | One column primitive for Projects, Tasks and the CRM. A stage colour that does not fight the corner | AGENT-SAFE | M | None | Nothing |
| **CRM-C3** Stages you can shape | Hide, order, colour and probability in both modes. A Mirror overlay that the sync never writes | AGENT-SAFE | M | 257 | CRM-C2 merged, because both edit three files |
| **CRM-C4a** Field studio, the backend | The field routes, the `custom` write path, the layout read and the reader filters (Native) | AGENT-SAFE. Flag `CRM_FIELD_STUDIO_ORGS`, shipped empty | L | 260 | Nothing |
| **CRM-C4p** Field studio, the shared client | The layout read and its hook, one `FieldInput` for each type, the empty value and the Mirror lock | AGENT-SAFE. The same flag | S | None | CRM-C4a merged |
| **CRM-C4b** Field studio, the record UI | Custom fields in the forms, the Fields tab by layout, the Columns control and the filter bar | AGENT-SAFE. The same flag | M | None | CRM-C4p merged |
| **CRM-C4c** Field studio, the studio UI | Create, rename, order, hide, sections, picklists, required and defaults in the `fields` section | AGENT-SAFE. The same flag | M | None | CRM-C1 and CRM-C4p merged |
| **CRM-C5** Field studio to Zoho | The C4 studio and the C3 stage order write to Zoho (Mirror) | AGENT-SAFE to build. 🔴 OWNER-GATE to turn on, and for the first live write | L | None | CRM-C4a and CRM-C4c, the §19.9 consent, its own spec audit |
| **CRM-C6** Field mapping | Lead conversion mapping, the CRM-Q7a Field map and lookup targets | AGENT-SAFE | M | The next free number at build. WS-58 MD-2d took 259 | CRM-C4a, CRM-Q7a merged |
| **CRM-C7** AI reach | `crm-assistant` reads what a member can read. Every agent can reach the CRM. `agent-sales-assistant` goes | AGENT-SAFE | M | 256 (254 and 255 were taken) | Nothing |

**Migration numbers (R1).** Measured again on 2026-10-13. 253 to 256 are
merged, and 256 is CRM-C7 (`256_remove_sales_assistant.sql`). 257 is
CRM-C3 (`257_crm_stage_overlay.sql`). This section reserves **260** for
CRM-C4a. A reservation is advisory. Each slice takes the next free number at
build time and checks it again at merge.

**WS-58 MD-2d took 259 (measured 2026-10-14).** It merged
`259_user_settings_my_day_prefs.sql`. So CRM-C6 has no reservation. It takes
the next free number at build.

### 20.3 Dispatch order and parallel work

1. **Wave 1, in parallel: C7, C2 and C3.**
   - C7 touches the agent, `acb_skills`, the orchestrator and new gateway
     files. It edits no file of C2 or C3.
   - C2 owns the colour half of `app/crm/lib/board.ts` and `KanbanBoard.tsx`.
     The colour half is `TONES`, `TYPE_TONES`, `TONE_COLORS` and `statusTone`.
   - C3 owns `orderLanes` and `weightedDeal` in `board.ts`, and the grids in
     `PipelineSettings.tsx`. C3 takes its colour list from `ACCENT_HUES`,
     never from `TONE_COLORS`, which C2 deletes.
   - C2 and C3 both edit `board.ts`, in hunks that do not overlap. The one
     that merges second rebases.
   - **C2 merges before C3 for `PipelineSettings.tsx`.** C2 changes only its
     colour imports and three colour lines (§20.5 item 9). C3 rebases on C2
     for that file.
2. **Wave 2: C1.** It splits `PipelineSettings.tsx`, so it starts after C3
   merges. It does not touch `KanbanBoard.tsx`, so it may run beside C2.
3. **C4 is four slices (§20.7), with this order.**
   - **C4a starts now.** It is the backend, and it may run beside C1. It
     edits no file of C1.
   - **C4p follows C4a.** It is the shared client: the layout read, its
     hook and `FieldInput`. C4b and C4c both use it.
   - **C4b and C4c follow C4p, in parallel.** C4b draws the custom fields on
     the record page and the lists. C4c fills the `fields` section that C1
     makes.
   - **The split.** C4b owns the record and list files (§20.7, C4b Files).
     C4c puts its code in a new `lib/fieldStudio.ts` and in new
     `components/FieldStudio*.tsx` files. Beyond those, C4c edits only
     `FieldsCard.tsx`. So no file is in both slices.
   - **Branching.** Cut C4p from the trunk after C4a merges. Cut C4b and C4c
     from the trunk after C4p merges. No C4 branch starts from another
     unmerged C4 branch.
4. **Wave 4: C6.** It needs the C4a columns and the merged CRM-Q7a card.
5. **Wave 5: C5.** It needs the C4c studio and the §19.9 consent.

`app/crm/page.tsx` is the hot file. C2 changes only the `KanbanBoard` props.
C3 changes only the quick create dialog (`<QuickCreateModal>`, `page.tsx:1123`)
and the move and lost dialog (`<MoveModal>`, `:1148`). C1 starts after C3 merges. So no two slices edit `page.tsx`
at the same time.

C1 may edit these hunks of `page.tsx` and no other. The line numbers come
from a measurement at `be8a5db7e`, which holds C3.

- `viewLine`, the settings text at `:600-601`.
- One new section handler beside `chooseView` (`:622`).
- The two `CrmRail` call sites: the phone drawer at `:773` and the desktop
  column at `:802`.
- The banner `onOpenSetup` at `:812`.
- The settings branch at `:925-956`.
- One new effect that sends a missing, unknown or hidden section to the
  default through `replaceView`.

### 20.4 CRM-C1: Settings sections

**Status.** 🔨 Built 2026-10-13 on branch `crm-c1`. No migration and no
flag. See "Built as" at the end of this section.

**Gate.** AGENT-SAFE. Frontend only. No migration and no flag, because the
same cards show in a new place.

**Audit (2026-10-13).** The spec auditor gave GO-NARROWED. The owner decided
each finding, and this section holds the result. The anchors below come from
a measurement at `be8a5db7e`, which holds C3.

**Scope.**

1. While `?tab=settings` is open, the rail row "CRM settings" shows child
   rows. Each child is a `RailRow` with `depth={1}`, as a saved view is.
   While a child row is open, the rail does not highlight the parent row.
2. The sections:

   | Id | Label | Shows for | Mode |
   |---|---|---|---|
   | `setup` | Setup and sync | Admin | Both |
   | `access` | Access | Admin | Both |
   | `stages` | Deal stages | Each member with CRM access | Both |
   | `lead-statuses` | Lead statuses | Each member with CRM access | Both |
   | `lost-reasons` | Lost reasons | Each member with CRM access | Both |
   | `fields` | Fields | Admin | Both |
   | `field-mapping` | Field mapping | Admin | Mirror. Native too after CRM-C6 |
   | `sync-health` | Sync health | Admin | Mirror |

   A row that the caller or the mode cannot use does not show. One list in
   the new `app/crm/lib/settingsSections.ts` holds this table. The rail and
   the page both read it.
3. **A `null` mode reads as Native.** `CrmMode` can be `null`
   (`lib/connect.ts:15`), and `useCrmMode` returns it
   (`lib/useCrmMode.ts:107`). The page treats only `"mirror"` as Mirror
   (`page.tsx:949`). So `settingsSections.ts` treats `null` as `native`.
4. **The cards of each section.**

   | Section | The cards it draws |
   |---|---|
   | `setup` | `SetupHub`, for an admin |
   | `access` | `AccessPanel`, for an admin |
   | `stages` | `PipelineSections` and the deal grid |
   | `lead-statuses` | The lead grid |
   | `lost-reasons` | The reason grid, or the reason card |
   | `fields` | `FieldsCard` |
   | `field-mapping` | `FieldMapCard`, for an admin in Mirror mode |
   | `sync-health` | `SyncHealthCard`, for an admin in Mirror mode |

   No section is empty for an admin or a member, in Mirror, Native or `null`
   mode. The page hides a section that the caller or the mode cannot use.
5. **The URL** is `?tab=settings&section=<id>`. `urlState.ts` parses
   `section` only when the tab is `settings`, as it does `module`
   (`urlState.ts:231` and `:256`). `selectTab` (`:434`) clears `section`.
   An unknown value parses as `null`.
6. **The default section** is `setup` for an admin and `stages` for a
   member. A missing, unknown or hidden section opens the default through
   `replaceView`. One new effect in `page.tsx` does this. So Back does not
   return to the bad URL.
7. **A click on a child row** calls `pushView`. No code calls `router.push`
   or `router.replace` (#914). On a phone, the click also closes the drawer,
   as `chooseView` does (`page.tsx:622`).
8. **`section` is an optional prop of `PipelineSettings`.** With a value,
   the component draws the cards of that section only. With no value, it
   draws every card, as it does today. So `crmQ9.test.ts`,
   `crmQ7a.test.ts`, `crmC3.test.ts` and `mirror.test.ts` keep working.
9. **The title.** The page draws one section, and the cards do not change.
   The section label shows in the `AppTopBar` subtitle, for example
   "Settings · Deal stages". `viewLine` (`page.tsx:600-601`) gives this text
   in place of "Setup, access, pipelines and statuses".
10. **The error boundary.** `boundaryKey` (`lib/boundary.ts:25`) adds
    `section` to the key that it returns at `:30-31`. So an error clears
    when the member opens another section.
11. **The setup link.** The mirror banner and the title-bar line open
    `?tab=settings&section=setup`. Only the hub offers "Change setup"
    (CRM-X0 item 1).
    - `SETUP_HREF` (`lib/setup.ts:18`) becomes
      `/crm?tab=settings&section=setup`.
    - `SetupLink` (`components/MirrorBanner.tsx:80`) draws a `next/link`
      `<Link>`. That is the server round trip that #914 removed. C1 makes
      its click call `pushView` with `SETUP_HREF`.
    - The banner `onOpenSetup` (`page.tsx:812`) opens the `setup` section.
      It does not call `chooseView("settings")`.
    - `crmX0.test.ts:243` looks for `chooseView("settings")` at
      `page.tsx:812`. C1 changes that line, so C1 changes the fence in the
      same slice. If the `href` of `SetupLink` goes, C1 also changes the
      check at `crmX0.test.ts:239`.
12. On a phone, the child rows show in the phone rail, as saved views do.

**Non-goals.** No change to a card. No new route. No search in settings.

**Files.** Paths are relative to `workbench/control_plane/src/`.

- `app/crm/components/CrmRail.tsx`.
- `app/crm/components/PipelineSettings.tsx`. The cards start at `:150`.
  The docstring at `:6-10` names a Connection card, and it says that
  Fields and Sync health wait. The Setup hub holds the connection, and both
  cards exist. C1 corrects the docstring.
- `app/crm/page.tsx`, in the hunks that §20.3 names and no other.
- `app/crm/lib/urlState.ts`.
- `app/crm/lib/setup.ts:18`.
- `app/crm/components/MirrorBanner.tsx:80`.
- `app/crm/lib/boundary.ts:25`.
- `app/crm/components/crmX0.test.ts:243`.
- `app/crm/lib/history.test.ts`, for the `next/link` fence.
- `app/crm/lib/settingsSections.ts` (new) and
  `app/crm/components/crmC1.test.ts` (new).

**Done when.**

1. A vitest shows the visible sections for an admin and a member, in
   Mirror, Native and `null` mode. Each visible section draws at least one
   card.
2. A vitest shows that a member in Native or `null` mode sees `stages`,
   `lead-statuses` and `lost-reasons` only. Each of the three draws its grid
   or its card.
3. A vitest shows that `section=sync-health` in Native mode resolves to the
   default through `replaceView`.
4. A vitest shows that a child-row click calls `pushView` with the section.
   On a phone, the click also closes the drawer.
5. A vitest shows that `selectTab` clears `section`, and that `section` on
   another tab parses as `null`.
6. A vitest shows that `PipelineSettings` with no `section` draws every card
   of today.
7. A vitest shows that `boundaryKey` changes when only the section changes.
8. A vitest renders `CrmRail` with the settings tab open. It finds the
   depth-1 child rows, and each row is a `RailRow`.
9. A fence refuses a `next/link` `<Link>` under `src/app/crm`. It lives in
   `history.test.ts` or in a new test. `SetupLink` calls `pushView`.
10. `railRows.test.ts` passes.
11. The `visual-review` skill shows the rail and one section. Check light
    mode, dark mode, compact density, a changed accent and phone width.

**Fences.** `app/crm/components/crmC1.test.ts`,
`app/crm/components/crmX0.test.ts`, `app/crm/lib/history.test.ts` and
`src/lib/railRows.test.ts`.

**Verification.**

```bash
cd workbench/control_plane && npx tsc --noEmit && npx vitest run src/app/crm src/lib/railRows.test.ts
```

**Built as (2026-10-13, branch `crm-c1`).**

- `app/crm/lib/settingsSections.ts` holds the table of item 2. It also
  holds `visibleSections`, `defaultSection`, `shownSection` and
  `sectionRedirect`. It imports only a type from `connect.ts`, because
  `urlState.ts` imports it to parse `?section=`.
- `urlState.ts` adds `section` to `CrmView` as an optional field, and the
  new `selectSection(view, id)`. That function opens the settings tab and
  keeps an open record.
- `CrmRail.tsx` exports `SettingsSectionRows`. It has no hook, so the test
  reads its element tree. The page passes `settings={railSettings}` at both
  rail call sites.
- **The redirect changed in review, and this changes item 6 and done-when 3.**
  The effect rewrites only a missing or unknown section. A known section
  that the caller cannot open draws the default, and its URL stays. So
  `section=sync-health` in Native mode shows Setup and sync and keeps its
  URL. No rewrite runs while the access read loads or is stale, or while the
  mode read failed (`readFailed` from `useCrmMode`). In those states the
  caller is a guess, and a rewrite would lose an admin's link.
- `boundaryKey` reads the section on screen (`withShownSection`). So a
  settings URL with no section mounts its subtree once.
- The unsaved edits of the settings rows live in the CRM store
  (`settingsDrafts`, `draftOf`). A section change keeps them. A re-read of a
  row drops its draft, as the local state did before.
- `SetupLink` is an `<a>` with `href={SETUP_HREF}`. Its click calls
  `pushOnPlainClick` in `lib/history.ts`. A plain left click calls
  `pushView`. A middle click or a click with a modifier key stays the
  browser's. The `href` check at `crmX0.test.ts:239` did not change.
- `history.test.ts` refuses a `next/link` import under `src/app/crm`, and it
  skips its own file, because its fixtures name `next/link`. It also refuses
  an in-app `<a href>` whose click does not call `pushView`, `replaceView`
  or `pushOnPlainClick`. A link may pass for three other reasons:
  - It opens a new tab.
  - Its href starts with `#`, `mailto:`, `tel:` or `http`.
  - A `crm-link-external:` comment with a reason stands just before it.
- A click on the section or the view on screen adds no history entry.
- Each card heading is the label of its rail row. "Pipelines and stages"
  is now "Deal stages", "Lead Status" is "Lead statuses", the hub heading
  is "Setup and sync", and `FIELD_MAP_TITLE` is "Field mapping". In Mirror
  mode an admin's pipelines list reads "Pipelines", because the editor
  below it is "Deal stages".
- page.tsx also changes its import lines, `MirrorContext` and the
  `CrmGate` call. §20.3 does not name them, and the review fixes need them.
- Fences: `components/crmC1.test.ts` (31 tests) and `lib/history.test.ts`
  (14 tests). The first build proved 11 mutations, and the review fixes
  proved 14 more. Each turned a fence red.
- The verifier checked the rail and one section in a browser.

### 20.5 CRM-C2: One board

**Gate.** AGENT-SAFE. Frontend only. No migration and no flag. The change is
the look of three boards, and the `visual-review` skill is its gate.

**The decision: an inset stage chip, and no stripe.** The column header holds
one chip inside the column's own padding. The chip holds a dot, the stage
name and the count. The column frame stays neutral.

**Why this design.**

- **A top stripe** is the defect. A bar under `overflow-hidden rounded-lg`
  meets the curve of the corner, and the clip leaves a sliver at each end.
  No radius fixes it, because a cap must touch the edge.
- **A coloured left edge on the column** collides with CRM-X3. A red left
  edge already marks an overdue card.
- **A tinted block across the top of the column** fixes the clip. But twelve
  lanes then read as twelve colour blocks, and the cards lose the eye.
- **The inset chip** never touches the column edge, so no corner clips it.
  Its radius is `rounded-md`, one step under the column's `rounded-lg`, so
  the two curves nest. Colour stays in one small area of each lane.

**The chip tones.** A coloured hue uses `soft` for the fill and `text` for
the name. The gray hue uses its `chip` tone (`statusAccent.ts:93`). That
tone is `bg-secondary text-muted-foreground`. The gray `soft` is
`bg-muted/40` (`:90`), and that fill is too faint to read as a chip. The dot
uses `dot` in every hue.

**How it meets `DESIGN_SYSTEM.md`.**

- Rule 4 of `AGENTS.md`: the hue comes from `statusAccent()` only.
- §4: every radius is a named utility, never `rounded-[..]`.
- §7: colour is never alone. The name is always text. A won, a lost and an
  on-hold lane also show an icon that `Icon` already serves.
- Light and dark mode: `soft` is a 10 % tint of a status token.
  `src/lib/theme/contrast.test.ts` measures `text` on that tint in both
  modes.
- Compact density: the chip uses `px-2 py-1` and the root font size, and no
  fixed height. So it scales with the density.
- A changed accent: a lane hue never follows the member accent, because
  `blue` maps to `info`. The drop-target ring and the focus ring use
  `primary`, so they do follow the accent.

**Three colours change on the CRM board.** These are the visible results of
the move from `TONES` to `statusAccent`. They are intended.

| Stored colour | Before (`TONES`) | After (`statusAccent`) | What a member sees |
|---|---|---|---|
| `blue`, `sky`, `cyan` | `bg-primary` | `info` | A blue lane stops following the member accent |
| `violet`, `purple`, `pink` | `bg-accent`, which is orange | `violet` | The seeded "Proposal" lane changes from orange to violet |
| `orange` | `bg-accent` | `amber`, the `warning` token | An orange lane reads as amber |

**Scope.**

1. **The primitive.** New `src/components/board/Board.tsx`, the horizontal
   scroller, and `src/components/board/BoardColumn.tsx`, one lane. It is a
   shared component, not a headless primitive. So it lives beside
   `TaskCardShell`, not in `src/components/ui/`.
2. **The props of `BoardColumn`.**
   - `header`: an object with `accent` (a `StatusAccent`), `title`, `count`
     and an optional `icon`. `header={null}` draws a column with no header.
   - `totals`, an optional slot under the chip, for the CRM money line.
   - `empty`, the text of an empty column.
   - `dropTarget`, a boolean.
   - `onAdd`, which draws "+ Add" at the foot when it is set.
   - `headerActions` and `children`.
3. **The frame.** `w-72` in all three apps, `rounded-lg border border-border
   bg-muted/30` and `p-2`. The body scrolls down, and the board scrolls
   across.
4. **The states.** Empty shows a dashed `rounded-md` box with the `empty`
   text. A drop target adds `ring-2 ring-primary/40`. The frame keeps its
   size during a drag.
5. **Projects swimlanes.** A swimlane cell (`projects/.../TaskBoard.tsx:1052`)
   is a column with no header. It renders `BoardColumn` with `header={null}`.
   The swimlane header row (`:1009`) draws the column headers once, above
   all lanes. It stays a row of its own. It drops its `border-l-2` bar, and
   each header uses the same chip as `BoardColumn`. New
   `src/components/board/StageChip.tsx` holds the chip, and both read it.
6. **The cards.** Every card renders through `TaskCardShell`. The CRM keeps
   its card content. If the shell lacks a prop that a CRM card needs,
   extend the shell. The overdue edge of CRM-X3 is one such prop. The CRM
   card passes these two props, which `sharedTaskUi.test.ts:574` requires:
   - `completed={false}`, as a literal with a comment. A won or a lost deal
     is closed, not done. The lane chip says so, and the card is not struck
     through.
   - `atCursor`, true for the card whose record the sheet shows. CRM-X4
     `j` and `k` move it. Else false.
7. **The CRM colours.** Delete `TONES`, `TYPE_TONES`, `TONE_COLORS` and
   `statusTone` from `app/crm/lib/board.ts`. A new `stageAccent(status,
   mode)` in `board.ts` calls `statusAccent()` and holds no class string.
   Extend `statusAccent.ts`:
   - Add the aliases that `TONES` offered: `slate` to gray, `sky` and
     `cyan` to blue, `emerald` and `teal` to green, `rose` to red, `pink`
     to violet.
   - Add an input for the CRM stage type: `won` green, `lost` red,
     `on_hold` amber, `ongoing` blue. It comes after the stored colour and
     before the name. An `open` stage falls through to the name and the
     position.
   - In Mirror mode, `stageAccent` passes no stored colour, because every
     mirrored row is `gray` (§20.1 fact 6). CRM-C3 owns the Metorite colour
     of a Mirror stage. C2 adds no column.
8. **Every caller of the deleted names.** `npx tsc --noEmit` fails until
   each one changes. Each caller moves to `stageAccent(...).dot`:
   - `app/crm/components/PipelineSettings.tsx:40-41`, `:65` and `:407`. The
     colour list at `:65` reads `ACCENT_HUES`.
   - `app/crm/components/RecordList.tsx:36` and `:77`.
   - `app/crm/components/Reports.tsx:28`, `:146` and `:203`.
   - `app/crm/components/StatusPill.tsx:24`, `:120` and `:173`.
   - `app/crm/components/mirror.test.ts:195`, and the `statusTone` tests
     at `app/crm/lib/board.test.ts:15` and `:136-148`.
9. **`PipelineSettings.tsx` has two owners.** C2 owns its colour imports
   and the three colour lines of item 8 only. C3 owns the grids. **C2
   merges first.** C3 rebases on C2 for that file. There is no re-export of
   `statusTone`, because a kept alias is the second palette that rule 4
   forbids.
10. **The palette fence.** In `sharedTaskUi.test.ts`, widen `SCOPE` to
    `app/(projects|tasks|crm)/` and delete the exemption at `:471-477`.
11. **The registry fence.** New `src/lib/boards.test.ts`, in the shape of
    `railRows.test.ts`. `BOARDS` lists the three board files:
    `app/projects/components/TaskBoard.tsx`,
    `app/tasks/components/TaskBoard.tsx` and
    `app/crm/components/KanbanBoard.tsx`. The test reads each class list as
    a set of tokens, so the order of the classes does not matter.
    - Each board file imports `BoardColumn` from
      `@/components/board/BoardColumn`.
    - No class list in a board file holds `shrink-0`, `flex-col` and a
      width token together. A width token is `w-<n>` or `w-[...]`.
    - Each board file renders `<TaskCardShell`, or imports one component
      file that renders it. One level of indirection is allowed, because
      Tasks reaches the shell through `TaskCard.tsx:364`.
    - **The stripe.** No class list in a board file, or in
      `src/components/board/`, holds `w-full` together with `h-0.5`, `h-1`
      or `h-1.5`. The scope is these files only. So the progress bars at
      `email/components/automation/AISettingsView.tsx:151` and
      `projects/components/RelationsBlock.tsx:212` stay legal.
    - The radius test at `sharedTaskUi.test.ts:633` moves here, over the
      three boards.

**Non-goals.** No change to the drag logic, `DropGap` or `lib/boardDrop.ts`.
No CRM-X8 feature, such as card fields or a folded lane. The CRM keeps
native HTML5 drag.

**Files.**

- New: `src/components/board/Board.tsx`, `BoardColumn.tsx` and
  `StageChip.tsx`, and `src/lib/boards.test.ts`.
- Shared: `src/components/TaskCardShell.tsx`, `src/lib/statusAccent.ts`,
  `src/lib/statusAccent.test.ts` and `src/lib/sharedTaskUi.test.ts`.
- Boards: the two `TaskBoard.tsx` files and
  `app/crm/components/KanbanBoard.tsx`.
- CRM colour callers: `app/crm/lib/board.ts`, `board.test.ts`,
  `PipelineSettings.tsx` (item 9), `RecordList.tsx`, `Reports.tsx`,
  `StatusPill.tsx` and `mirror.test.ts`.

**Done when.**

1. `boards.test.ts` passes. It fails when a board draws its own column, in
   any class order. The PR shows it red first.
2. `sharedTaskUi.test.ts` passes with `app/crm` in `SCOPE`, and it fails
   while `TONES` exists.
3. `statusAccent.test.ts` shows each new alias and each stage type. The
   test "category %s and the name %j resolve to the same hue"
   (`statusAccent.test.ts:117-124`) still passes.
4. A Mirror board with every stage `gray` shows won green, lost red and
   on-hold amber.
5. `npx tsc --noEmit` passes, and no file names `statusTone` from
   `app/crm/lib/board`.
6. The `visual-review` skill shows the three boards side by side, and the
   Projects swimlanes. Check light mode, dark mode, compact density, a
   changed accent and phone width. No chip has a clipped corner. The
   "Proposal" lane is violet.

**Fences.** `src/lib/boards.test.ts`, `src/lib/sharedTaskUi.test.ts`,
`src/lib/statusAccent.test.ts`, `src/lib/theme/contrast.test.ts` and
`app/crm/lib/board.test.ts`.

**Verification.**

```bash
cd workbench/control_plane && npx tsc --noEmit && npx vitest run src/lib src/app/crm src/app/projects src/app/tasks
```

**Built as (2026-10-13, branch `crm-c2`).**

- `src/components/board/` holds `Board.tsx`, `BoardColumn.tsx` and
  `StageChip.tsx`. `Board` takes `scroll="own"` (My Tasks, the CRM) or
  `scroll="parent"` (Projects, where the canvas scrolls).
- `BoardColumn` has the props of item 2. It also has five more:
  `addLabel`, `overlay` (the refused-drop layer), `as` (`ul` for Projects),
  the drag handlers, and `hidden`. CRM-C3 wires `hidden`, which draws a
  muted "Hidden" word beside the chip.
- `BoardHeaderCell` in `BoardColumn.tsx` draws the Projects swimlane head
  row. Each head has the lane width and padding, so its chip stands over
  the chip of a lane.
- The chip uses the `chip` slot of the hue. For a coloured hue that slot is
  `soft` plus `text`. For gray it is `bg-secondary text-secondary-foreground`.
  The review changed the gray ink, because `text-muted-foreground` on
  `bg-secondary` measured 4.33 : 1 in light mode. `contrast.test.ts` now
  fails a chip that draws a pair it does not measure at AA.
- A lane is `role="group"` with the lane name. It is not a `<section>`
  landmark.
- The CRM card is a `role="button"`, so `cardLabel` (`lib/cardLabel.ts`)
  gives it a full name. That name holds the money or the company, the next
  step, "Overdue" and the lost-reason warning.
- The settings colour picker shows a stored name that it does not offer
  (`orange`, `teal`, `sky`) first, marked "custom", with the hue it draws
  (`lib/colorOptions.ts`).
- The radius check in `boards.test.ts` is advisory. `--radius-lg` and
  `--radius-xl` are one value, so it cannot catch a change that shows.
- A lane with an icon (won, lost, on hold) shows the icon in the place of
  the dot. `stageIcon` in `board.ts` holds the three names.
- `stageAccent` passes no index. So an open stage with no keyword in its
  name is gray on every surface. A lane that the board hides changes the
  colour of no other lane.
- `RecordList`, `Reports`, `StatusPill` and the settings dot read the mode
  from `CrmModeContext` (`lib/modeContext.ts`). `page.tsx` provides it from
  `ctx.mode`. So a mirrored won stage is green on each of them and on the
  board. Fence: `components/crmC2.test.ts`.
- `TaskCardShell` has a new `overdue` prop, for the red edge of CRM-X3.

### 20.6 CRM-C3: Stages and statuses you can shape, in both modes

**Status.** 🔨 Built 2026-10-13 on branch `crm-c3`, migration 257. The two
items that waited for CRM-C2 are built after its merge. See "Built as" at
the end of this section.

**Gate.** AGENT-SAFE. No flag. With no overlay value set, each board, list
and report shows what it shows today. Item 7 is a fix of the sync.

**Waits for.** CRM-C2 merged. C2 and C3 share `PipelineSettings.tsx`,
`StatusPill.tsx` and `app/crm/lib/board.ts`. C2 merges first, and C3
rebases on it.

**The model.** Each stage, status and lost reason has two sets of values.
The **source values** come from Zoho in Mirror mode, and from the admin in
Native mode. The **overlay values** are Metorite-only. The sync never writes
them, and no Zoho write reads them. The **effective value** is the overlay
value when it is set, else the source value.

**Migration 257 (expand only, R6).** Each new column is nullable or has a
default. The three tables already have the tenant key and FORCE RLS (241).

| Table | New columns |
|---|---|
| `crm_deal_statuses` | `hidden BOOLEAN NOT NULL DEFAULT false`, `ui_position INT NULL`, `ui_probability SMALLINT NULL` (0 to 100), `ui_color TEXT NULL`, `ext_id TEXT NULL`, `source_gone_at TIMESTAMPTZ NULL` |
| `crm_lead_statuses` | `hidden`, `ui_position`, `ui_color`, `ext_id`, `source_gone_at` |
| `crm_lost_reasons` | `hidden` |

The migration also adds one partial unique index on each status table:
`(organization_id, ext_id) WHERE ext_id IS NOT NULL`. `ext_id` holds the id
of the Zoho picklist value. `ui_color` is the Metorite colour of a Mirror
stage, which CRM-C2 leaves to this slice.

**Scope.**

1. **The overlay door.** New `core.MIRROR_OVERLAY_COLUMNS` maps each of the
   three tables to its overlay columns. The deal set is `hidden`,
   `ui_position`, `ui_probability`, `ui_color` and `max_dwell_days`.
   `max_dwell_days` is in it because UX-Q5 allows it (§19.4). The lead set
   is `hidden`, `ui_position` and `ui_color`. The lost reason set is
   `hidden`.
2. **The guard.** In Mirror mode, `assert_writable` lets a settings write
   pass only when each written column is in `MIRROR_OVERLAY_COLUMNS`. Any
   other column still gives 409 `mirror_mode_settings`.
3. **The edit routes.** `StatusIn` (`routes/crm/admin.py:63`) and
   `LostReasonIn` take the new fields.
   - Mirror mode accepts the overlay fields only.
   - Native mode accepts `hidden` and every source field. It refuses a
     `ui_*` field with 422, because a Native admin edits the source columns
     directly.
4. **The order route.** New `PUT /crm/statuses/{kind}/order`. Only an admin
   calls it.
   - The body is `{"ids": [uuid, ...]}`, the full ordered list of that
     kind.
   - It writes `position` in Native mode. In Mirror mode it writes
     `ui_position` on every row of the kind. All in one transaction.
   - It gives 422 when the list is not exactly the org's ids of that kind.
   - CRM-C5 (§20.8 item 2) sends this route to Zoho when its flag is on.
5. **The wire contract.** `StatusModel` keeps its fields `position`,
   `probability` and `color`. They now carry the **effective** value. So
   each reader of today gets the effective value with no change. New
   fields serve the settings UI: `source_position`, `source_probability`,
   `source_color`, `hidden`, `source_gone_at`, `ext_id` and `admin_order`.
   `admin_order` is true when a row of that kind has `ui_position`.
6. **One SQL helper.** New `core.EFFECTIVE_STATUS_COLUMNS` is the one SELECT
   list of the effective and the source columns. No route computes an
   effective value on its own. These reads use it:
   - the lanes of `GET /crm/pipeline` (`routes/crm/pipeline.py:417`, `:422`
     and `:432`).
   - the stage list of the reports (`routes/crm/reports.py:230`).
   - `GET /crm/pipelines` (`routes/crm/pipelines.py:108`), which reads raw
     `s.probability` today. CRM-C7 reads this route.
   - `GET /crm/statuses/{kind}` (`admin.py:289`).
   The probability binds at `pipeline.py:530` and `reports.py:263` then
   carry the effective value with no change.
7. **The sync keeps the source values current.** `_ensure_status`
   (`crm_sync/engine.py:790`) has three callers. Only the picklist call
   (`:859`) updates a row that exists.
   - The picklist call writes `position` each time. It writes `probability`
     only when Zoho names one.
   - The pipeline stages call (`:1871`) and the record call through
     `_status_id` (`:825`) keep `DO NOTHING`. So a record pull never
     changes the order or the probability.
   - The picklist call matches a row by `ext_id` first, then by name. A
     Zoho rename then changes `name` in place. The row keeps its overlay
     and its deals.
   - No SQL of the sync names an overlay column or `color`.
     `_STAGE_TYPE_SQL` (`:756`) and `_FORECAST_SQL` (`:748`) keep their
     work.
8. **A rename that collides.** A record pull can run before the picklist
   read. It then inserts a row under the new name with a NULL `ext_id`.
   The picklist call finds the `ext_id` row with the old name, and a rename
   would break `uq_crm_deal_statuses_org_name`. The rule:
   - The `ext_id` row survives, because it holds the overlay.
   - A new fenced door, `core.merge_status_from_source`, does the merge in
     one transaction. Only `gateway/crm_sync/` may call it.
   - It repoints each reference from the name row to the `ext_id` row:
     `crm_deals.status_id` or `crm_leads.status_id`, and
     `crm_pipeline_stages.status_id`. A pipeline that holds both rows keeps
     one membership, at the place of the `ext_id` row.
   - `crm_status_changes` holds names as text and has no foreign key. The
     merge needs no repoint there, because the new name stays the name.
   - A saved view (`crm_views`) whose `status` filter holds the id of the
     name row gets the id of the `ext_id` row.
   - It then deletes the name row and renames the `ext_id` row.
   - The savepoint never swallows a failed merge. A failure fails the
     picklist step, and the sync logs it.
9. **A stage that leaves Zoho.** The gone rule runs only in a run that read
   the Stage or Lead_Status picklist definition (`engine.py:1566-1575`). A
   run that could not read it marks nothing.
   - It applies only to a row with a non-null `ext_id`. A stage that only a
     pipeline lists (`engine.py:1854`) and `UNKNOWN_STATUS` never get
     `source_gone_at`.
   - A row with an `ext_id` that the picklist no longer lists gets
     `source_gone_at = now()`. The sync never deletes it.
   - A value that comes back clears `source_gone_at`.
10. **Weighted totals.** `WEIGHTED_SQL` (`core.py:130`) keeps its text. Its
    `:stage_probability` now binds the effective stage probability. A deal
    with its own probability keeps it. The stage value applies only to a
    deal with none, which is the rule of today.
    - In Mirror mode most deals carry a Zoho `Probability` (§20.1 fact 5).
      So a `ui_probability` moves the forecast of the deals with no value
      only. The settings row says so.
    - A `ui_probability` on a won or a lost stage moves no forecast,
      because `WEIGHTED_TYPES` leaves those stages out.
    - `weightedDeal` in `board.ts` reads the effective value from the wire,
      with no code change.
11. **The parity fixture.** `tests/fixtures/crm_weighted_parity.json` gets a
    `ui_probability` column. Two new kinds of case hold:
    - A deal with no probability of its own, in a stage with
      `ui_probability`. The result follows `ui_probability`.
    - A deal with its own probability, in the same stage. The result does
      not change.
12. **The lane order.** `orderLanes` keeps its four groups: open, on hold,
    won and lost.
    - In Mirror mode with `admin_order`, each group sorts by the key
      `(ui_position IS NULL, ui_position, source position)`. A row with no
      `ui_position` sorts after each ordered row, in Zoho order.
    - The order route writes `ui_position` on every row of the kind. So
      after an admin order, only a new Zoho stage has none, and it goes to
      the end of its group.
    - In Mirror mode without `admin_order`, open stages sort by the
      effective probability, as today. Native mode sorts by `position`.
    - The Leads board takes the same key.
13. **Hidden.** These rules hold for a row with `hidden = true`, and for a
    row with `source_gone_at` set.
    - No picker offers it, except as the current value of a record. The
      pickers are the move dialog (`MoveModal.tsx`), quick create
      (`QuickCreateModal.tsx`), the filter bar (`FilterBar.tsx`), the lost
      dialog and `StatusPill.tsx`.
    - A board shows its lane only when the lane holds records in the
      current view. The lane sits at the end of its group
      (`lib/emptyStages.ts`, `visibleLanes`). `KanbanBoard.tsx` passes a
      `hidden` prop, and `BoardColumn.tsx` (CRM-C2) draws the "Hidden" mark.
    - Reports count the deals in it, because data stays data.
    - The server does not refuse a move into it, because Zoho can move a
      deal there.
    - Settings shows a row with `source_gone_at` as "Removed in Zoho".
14. **Mirror mode gets an editor.** For an admin, the Deal stages, Lead
    statuses and Lost reasons sections become editable in Mirror mode. Each
    row shows the Zoho value beside the Metorite value. A drag calls the
    order route.
    - "Reset to Zoho" on a row sets `ui_probability` and `ui_color` to
      NULL. It does not change `hidden` or `max_dwell_days`.
    - "Reset order to Zoho" on a section sets `ui_position` to NULL on every
      row of that kind.
15. **Native mode adds hide.** A hide of the default stage, or of the only
    `won` or `lost` stage, gives 422. The same holds for the default lead
    status.
16. **Colour.** The picker offers `ACCENT_HUES`. Mirror writes `ui_color`.
    Native writes `color`. C3 changes `stageAccent` in `board.ts`: in Mirror
    mode a set `ui_color` wins, else the C2 type rule applies.

**Non-goals.** No write to Zoho (CRM-C5). No order for each pipeline: a
pipeline is an ordered subset of the one stage list (§18.1). No overlay of
the stage type or the name.

**Files.**

- New: `infra/postgres/257_crm_stage_overlay.sql`, and the new tests below.
- Gateway: `routes/crm/core.py` (`MIRROR_OVERLAY_COLUMNS`,
  `EFFECTIVE_STATUS_COLUMNS`, `merge_status_from_source`), `admin.py`,
  `pipeline.py`, `pipelines.py`, `reports.py` and `crm_sync/engine.py`.
- Frontend, after C2: `app/crm/components/PipelineSettings.tsx` (the grids),
  `StatusPill.tsx`, `app/crm/lib/board.ts` (`orderLanes` and
  `stageAccent`), and `src/components/board/BoardColumn.tsx` (the `hidden`
  prop and its mark).
- Frontend: `MoveModal.tsx`, `QuickCreateModal.tsx`, `FilterBar.tsx`,
  `KanbanBoard.tsx`, `app/crm/lib/emptyStages.ts`, `types.ts`, `api.ts`, and
  `app/crm/page.tsx` at `:1025` and `:1054` only.
- Fixture: `tests/fixtures/crm_weighted_parity.json`.
- Fence: `tests/unit/test_crm_upsert_from_source_fence.py`.

**Done when.**

1. An R8 test starts a Mirror org. An admin sets each overlay column, and
   each call gives 200. A PATCH of `name` or `probability` still gives 409.
   In Native mode a PATCH of a `ui_*` field gives 422.
2. The same test runs a full sync. The overlay columns keep their values.
   `position` and `probability` follow the Zoho picklist fixture. A second
   org does not change.
3. **Record pulls do not reorder.** A sync run that pulls records after the
   picklist leaves each stage's `position` and `probability` as the
   picklist gave them.
4. **Rename.** A Zoho fixture renames a stage and keeps its id. After the
   sync, the row has the new name, the same `id`, its overlay, and its
   deals.
5. **A rename that collides.** A record pull first inserts a row under the
   new name, and a deal points to it. The next picklist read merges it. One
   row remains, with the `ext_id`, the overlay and the new name. Each deal
   and pipeline membership of the name row now points to it. No error is
   swallowed.
6. **Removal.** A Zoho fixture drops a stage. After the sync, the row
   exists with `source_gone_at` set, and its deals keep it. Settings shows
   "Removed in Zoho". A fixture that brings it back clears `source_gone_at`.
7. **No picklist read.** A run whose picklist read fails marks no row. A
   pipeline-only stage and `UNKNOWN_STATUS` never get `source_gone_at`.
8. The order route writes `ui_position` on every row in Mirror mode, and
   `position` in Native mode. A list with a missing, extra or foreign id
   gives 422.
9. **The order key.** After an admin order, a new Zoho stage arrives. It
   sorts at the end of its group, and the other rows keep the admin order.
   `board.test.ts` shows the key in both modes.
10. **Reset.** "Reset to Zoho" on a row clears `ui_probability` and
    `ui_color`, and keeps `hidden` and `max_dwell_days`. "Reset order to
    Zoho" clears `ui_position` on every row of the kind.
11. `GET /crm/pipelines`, the lanes and the reports return the effective
    probability. The parity fixture passes on both sides, with the new
    cases of item 11.
12. A Native org hides a stage. A hide of the default stage gives 422.
13. **Hidden.** A vitest shows each picker without a hidden stage, and with
    it when it is the current value. A vitest shows a hidden lane with
    records at the end of its group with the "Hidden" mark. An R8 test
    counts a deal in a hidden stage in the pipeline report. An R8 test
    moves a deal into a hidden stage with 200.
14. **Colour.** A vitest shows a Mirror lane with `ui_color` set in that
    colour, and a Mirror won lane with no `ui_color` in green.
15. **The SQL scan.** `test_crm_mirror_readonly.py` names
    `MIRROR_OVERLAY_COLUMNS` exactly. An AST scan of `routes/crm/` and
    `crm_sync/` reads each module-level `str` constant and each literal
    argument of `text()`. In `crm_sync/` and `crm_sources/`, no such string
    names an overlay column. Python names are out of scope, because
    `engine.py:1192-1289` uses `hidden` as a local name.
16. **The scan proves itself.** A self-test plants a literal that names
    `ui_position` in a temporary module, and the scan finds it.
17. `test_crm_upsert_from_source_fence.py` puts `merge_status_from_source`
    in `NAMES`. Its allowlist names `gateway/crm_sync/` only, and a planted
    call from a route fails.
18. `crmC3.test.ts` shows the Mirror editor and both reset actions.

**Fences.** `tests/unit/test_crm_stage_overlay_r8.py` (new, R8, `_DB_GATE`),
`tests/unit/test_crm_mirror_readonly.py`,
`tests/unit/test_crm_upsert_from_source_fence.py`,
`tests/fixtures/crm_weighted_parity.json`, `app/crm/lib/board.test.ts` and
`app/crm/components/crmC3.test.ts` (new).

**Built as (2026-10-13).** Migration 257 holds the columns and the two
partial unique indexes of the table above.

- **The wire.** `StatusModel` also carries `ui_position`, `ui_probability`
  and `ui_color`, so the lane order and the colour rule read the overlay as
  stored. The effective `position` folds the admin order key into one
  number. An ordered row gives its `ui_position`. Once a kind has an admin
  order, a row with none gives `1000000 + position`.
  `core.ADMIN_ORDER_TAIL` holds that number.
- **The reads.** `core.read_statuses` and `core.read_status` run the one
  SELECT list. `LostReasonModel` carries `hidden`.
- **The reset of the order.** "Reset order to Zoho" is `POST
  /crm/statuses/{kind}/order/reset`. Mirror mode only, and Native mode gets
  422. A `DELETE` on `/order` would meet `DELETE /statuses/{kind}/{status_id}`.
- **The colour.** `ui_color` must be a short colour name, else 422. The
  Native `color` keeps its rules.
- **Native hide.** The rule of item 15 binds in Native mode only. It judges
  the state after the write, on a create and on a PATCH: the payload over the
  stored row. A hidden row cannot be the default, for a stage or a lead
  status. A write that hides or retypes the only visible `won` or `lost`
  stage gets 422.
- **Two values that trade names.** The spec names the merge for a name row
  with no `ext_id`. When the name row holds the `ext_id` of another value,
  the sync renames it to `<name> [<ext_id>]`. Its own value names it again
  in the same run. A value that Zoho no longer lists keeps that name, and
  the gone rule marks it.
- **A picklist with no Zoho id.** The gone rule marks nothing when no value
  of the read has an id.
- **The pickers.** `lib/emptyStages.ts` holds `pickable` and `isRetired`.
  The filter chips are `chipsFor` in `lib/filters.ts`, because `FilterBar.tsx`
  draws chips that it does not build. `QuickCreateModal.tsx` has no status
  picker, so it needs no change, and `page.tsx` is not changed.
- **The order of a hidden lane.** `orderLanes` puts it last in its group.
  `visibleLanes` shows it only with records.
- **The Leads board has no groups by type.** It sorts by the effective
  `position`, with a hidden or a gone status last. So a lost lead status
  stays where Zoho puts it.
- **The Mirror editor.** `PipelineSettings.tsx` reads `orderStatuses` and
  `resetStatusOrder` from the store, because the page wiring is CRM-C1's.
  The Native drag keeps its PATCH of each moved row.
- **After CRM-C2 merged.** `KanbanBoard.tsx` passes `hidden` to
  `BoardColumn`, which draws the "Hidden" mark. In Mirror mode a set
  `ui_color` wins in `stageAccent`, else the C2 type rule applies (item 16).
  The Mirror colour picker takes `lib/colorOptions.ts`, with "by type" first.
- **When the sync reads the picklists.** An import and a reconcile read them
  with the field definitions. Every other run reads the Stage and Lead_Status
  picklists when discovery is due (`_discovery_due`, once in 24 hours). So a
  Zoho reorder, a Zoho probability and the Zoho id of each value reach the
  mirror within a day, with no wait for a reconcile.

**Verification.**

```bash
bash scripts/dev_db.sh && eval "$(bash scripts/dev_db.sh --export)"
uv run pytest tests/unit/test_crm_stage_overlay_r8.py tests/unit/test_crm_mirror_readonly.py tests/unit/test_crm_upsert_from_source_fence.py tests/unit/test_crm_pipelines_r8.py tests/unit/test_crm_discovery.py tests/unit/test_crm_migration.py tests/unit/test_crm_reports.py -q
cd workbench/control_plane && npx tsc --noEmit && npx vitest run src/app/crm
```

### 20.7 CRM-C4: Field studio, Native mode

**Audit (2026-10-13).** The spec auditor gave NO-GO. The owner decided each
finding, and this section holds the result. C4 became three slices, and each
has its own done-whens: CRM-C4a (the backend), CRM-C4b (the record UI) and
CRM-C4c (the studio UI). §20.3 gives the order. The anchors below come from a
measurement at `be8a5db7e`.

**Audit (2026-10-14).** The spec auditor gave NO-GO to C4b and to C4c. The
owner decided each finding. A new prelude slice, CRM-C4p, now holds the
client parts that both slices need. So C4 is four slices. The C4p, C4b and
C4c anchors come from the trunk at `3fe3349f3` and from `crm-c4a` at
`cf4d6a9c4`.

**Supersedes CRM-S6.** CRM-C4 supersedes CRM-S6, "Custom fields editor"
(§6 and §18.5). Do not dispatch CRM-S6.

**Gate.** AGENT-SAFE. The write routes sit behind the new flag
`CRM_FIELD_STUDIO_ORGS` in `routes/crm/flags.py`. It has the grammar of
`CRM_MIRROR_ORGS` and ships empty. To turn it on is the gate
`enforcement-flip`. Name the flag and the box in the same message.

**Which entities.** Leads, Contacts, Companies and Deals. Native mode has no
generic module, because `crm_module_records` has no native writer (§18.7).

**Migration 260 (expand only, R6).** 256 is CRM-C7, merged, and 257 is
CRM-C3.

- New table `crm_field_sections`: `id`, `organization_id`, `entity`,
  `label`, `position`, `created_at` and `updated_at`. It takes the tenant
  shape of `251_crm_module_records.sql`: an inline `REFERENCES` before
  `DEFAULT`, FORCE RLS, and one policy with USING and WITH CHECK.
- Seven new columns on `crm_field_defs`:
  - `section_id UUID NULL`, with `ON DELETE SET NULL`.
  - `required BOOLEAN NOT NULL DEFAULT false`.
  - `help_text TEXT NULL` and `default_value JSONB NULL`.
  - `lookup_target TEXT NULL`.
  - `is_system BOOLEAN NOT NULL DEFAULT false`.
  - `retired_at TIMESTAMPTZ NULL`.
- A field that the studio writes has `source = 'metorite'`. The sync writes
  `'zoho'`. The `source` column exists, so this needs no DDL.
- `_FIELD_DEF_SQL` of the sync (`crm_sync/engine.py:1712`) names none of the
  seven columns, so the sync leaves them alone. Item 13 adds a guard to its
  conflict update.

**Scope.**

1. **The types.** The record stores a value in `custom`, under the field
   `key`.

   | Studio label | Type word |
   |---|---|
   | Text, Long text | `text`, `textarea` |
   | Whole number, Decimal, Percent | `number`, `decimal`, `percent` |
   | Currency | `currency` |
   | Date, Date and time | `date`, `datetime` |
   | Checkbox | `boolean` |
   | Email, Phone, URL | `email`, `phone`, `url` |
   | Picklist, Multi-select | `select`, `multiselect` |
   | Lookup | `lookup`, with `lookup_target` (item 8) |

   `decimal` and `percent` are new words. In `mapping._FIELD_TYPES`
   (`crm_sources/zoho/mapping.py:786`), Zoho `double` becomes `decimal` and
   `percent` becomes `percent`. The next sync updates the mirrored rows.
   `app/crm/lib/fields.ts` draws both words.

   **The same change extends `_RULE_COLUMN_TYPES`** (`mapping.py:574-582`).
   `decimal` and `percent` get the column types of `number`. Without this,
   an approved CRM-Q7a rule on a `double` or `percent` field goes
   `type_mismatch` after the next sync.
2. **The key.** At create, the gateway makes `key` from the label. It uses
   `a-z`, `0-9` and `_`, at most 40 characters, with `_2` on a clash. A key
   never equals a fixed column. The validator also refuses `order`, `layout`
   and `restore`, because they are route words (item 4). The key never
   changes, and a rename changes `label` only.
3. **System fields.** `core.SYSTEM_FIELDS` lists the fixed columns of each
   entity, with a label and a type. The studio shows them beside the custom
   fields. An admin may change their label, order, section and `visible`.
   The first such edit writes a row with `is_system = true`. The studio
   cannot delete or retype a system field. A field that the gateway needs stays
   required, such as a deal name.
4. **The routes.** A new `routes/crm/field_studio.py`. Only an admin calls
   them.
   - `POST /crm/fields/{entity}` creates a field.
   - `PATCH /crm/fields/{entity}/{key}` changes `label`, `section_id`,
     `required`, `help_text`, `default_value`, `visible`, the options,
     `type` and `lookup_target`. A `type` change follows the matrix of item
     9. A `lookup_target` follows item 8.
   - `PUT /crm/field-layout/{entity}` sets the order of the sections and the
     order of the fields in each. One body holds both, and one transaction
     writes it. A `PATCH /crm/fields/{entity}/order` route would collide
     with `/{key}`, so it does not exist.
   - `POST`, `PATCH` and `DELETE /crm/field-sections/{entity}` manage the
     sections. A delete moves its fields to no section.
   - `DELETE /crm/fields/{entity}/{key}` retires the field. It sets
     `retired_at`, and it keeps each value in `custom`. It keeps `visible`
     as it was (review round 1, 2026-10-13). Each reader hides a retired
     field by `retired_at`.
   - `POST /crm/fields/{entity}/{key}/restore` brings a retired field back.
     It clears `retired_at`. The field shows again with the visibility
     that it had before the retire.
5. **The custom write path.** It is new. `LeadIn` (`routes/crm/core.py:289`),
   `DealIn` (`:345`), `ContactIn` (`:242`) and `OrganizationIn` (`:208`)
   have no `custom`. `update_row` (`core.py:1798`) writes each column whole,
   so a `custom` value replaces the whole object today.
   - A create and a PATCH take `custom` as a partial map of key to value.
   - The write merges per key, with jsonb `||`. A JSON `null` removes that
     key.
   - A write never touches a key that it does not name. So the stored value
     of a retired field stays.
   - A write that names a retired key gives 422: "<label> is retired.
     Restore it to write a value." The stored value does not change.
   - The gateway checks each value against the type of its field. A bad
     value gives 422, and the detail names the field label.
   - A create enforces "required". A PATCH enforces it only when it sets the
     key to `null` or to an empty value.
   - On create, a field with a `default_value` fills an absent key.
   - The same rules apply to leads, deals, contacts and organizations.
6. **Picklist options.** Add, rename, reorder and retire.
   - A new option takes a `value` that is a slug of its label. The `value`
     is unique in the field, and a suffix `-2`, `-3` resolves a clash.
   - The `value` never changes after create. A rename changes `label` only.
   - A retire adds `"retired": true`. A record keeps a retired value and
     shows it. The pickers hide a retired option.
7. **Multi-select.** The stored value is a JSON array of option `value`
   strings. The custom filter (`core.py:1181`) already reads this shape.
8. **Lookup.**
   - In Native mode, `lookup_target` is one of the entity slugs `leads`,
     `contacts`, `organizations` or `deals`. A `module:<ApiName>` target is
     for Mirror mode only, and it waits for CRM-C5.
   - The stored value is `{"id": "<uuid>", "name": "<label at write time>"}`.
     The readers find the live name by `id`. `lookupName`
     (`app/crm/lib/fields.ts:120` on `crm-c4a`) already reads `{name, id}`.
   - An `id` of a record in another org gives 422.
9. **The type change matrix.** The gateway allows a change only when each
   stored value stays valid. Any other change gives 422 `field_type_change`.

   | From | Allowed to |
   |---|---|
   | `text` | `textarea` |
   | `number` | `decimal`, `currency`, `percent` |
   | `decimal`, `percent`, `currency` | Each other |
   | `select` | `multiselect`. A value becomes a list of one, in one transaction |
   | `email`, `phone` | `text` |
   | `url` | `textarea`, because a URL can hold more than 255 characters (review round 1, 2026-10-13) |
   | Any other | None |

10. **Mode guard.** `crm_field_sections` and `crm_field_defs` join
    `MIRROR_SETTINGS_TABLES`. So each studio route gives 409 in Mirror mode
    until CRM-C5. The sync writes with raw SQL and does not call the guard.
11. **System rows and retired fields in the readers.** Five readers list the
    custom keys. Each one leaves out the rows with `is_system = true`:
    - `_FIELDS_SQL` (`routes/crm/fields.py:31`), except under `?layout=1`.
    - the custom filter (`routes/crm/core.py:1161`).
    - `_FIELD_SQL` (`routes/crm/field_rules.py:81`).
    - `crm_list_fields` (`packages/acb_skills/acb_skills/crm_tools.py:460`).
    - `fieldRows` (`app/crm/lib/fields.ts:286` on `crm-c4a`).

    A retired key has these rules:
    - The custom filter (`core.py:1161`) refuses it with 422.
    - The CSV export leaves it out of its columns.
    - The saved-view column picker (`pickable`, `lib/fields.ts:370` on
      `crm-c4a`) leaves it out.
    - `crm_list_fields` marks it "retired".
12. **One layout, read everywhere.** `GET /crm/fields/{entity}?layout=1`
    returns the sections, the system fields and the custom fields in order.
    Five places read the layout:
    - the create and edit forms.
    - the Fields tab of the record page (CRM-X4).
    - the Columns control (CRM-Q4) and the filter bar (CRM-Q8).
    - the CSV export.

    **The Fields tab draws the layout.** Today the `fields` part of
    `FieldsPanel.tsx` (`:242`) lists the custom keys that the record holds.
    - It draws each section in order, with the system and custom fields in
      it.
    - It shows each visible field, also a field with no value. So a member
      can edit a new field on the record page. Today `fieldRows`
      (`lib/fields.ts:296` on `crm-c4a`) shows only the keys that the record
      holds.
    - The Overview tab keeps its summary facts.
13. **The mode switch.** A Native field and a Zoho field can have the same
    key.
    - The conflict update of `_FIELD_DEF_SQL` (`engine.py:1712`) gets
      `WHERE crm_field_defs.source IS DISTINCT FROM 'metorite'`. So the
      sync never updates a Metorite field row.
    - Start over (`routes/crm/settings.py:241`, CRM-X0) deletes the
      `source = 'zoho'` rows today. It also deletes the `source = 'metorite'`
      rows, because start over deletes the records too.
14. **The Fields section** (CRM-C1) is the studio for an admin of a Native
    org with the flag on. The section is for an admin only
    (`settingsSections.ts:46`, `PipelineSettings.tsx:296`), so a member
    never sees it. An admin in every
    other case sees the read-only `FieldsCard` of today. That includes an
    admin with the flag off, and an admin of a Mirror org (owner,
    2026-10-14).

**Non-goals.** Formula, rollup, auto-number, file and user-lookup fields.
Field permissions for each role. Field history (CRM-S10). A layout for each
pipeline. A Zoho write (CRM-C5).

#### CRM-C4a: the backend

**Starts now.** It edits no file of CRM-C1.

**Scope.**

- Migration 260 and the flag `CRM_FIELD_STUDIO_ORGS`.
- `field_studio.py` and `core.SYSTEM_FIELDS` (items 3 and 4).
- The custom write path (item 5), with the rules of items 6, 7 and 8.
- The `?layout=1` read (item 12) and the five reader filters (item 11).
- The `_FIELD_TYPES` and `_RULE_COLUMN_TYPES` change (item 1).
- Restore, the field layout route and the mode-switch guard (items 4
  and 13).

**Files.** `infra/postgres/260_crm_field_studio.sql` (new),
`routes/crm/field_studio.py` (new), `routes/crm/fields.py`, `core.py`,
`records.py`, `flags.py`, `export.py`, `field_rules.py`, `settings.py`,
`crm_sync/engine.py`, `crm_sources/zoho/mapping.py`,
`packages/acb_skills/acb_skills/crm_tools.py`, `app/crm/lib/fields.ts` (the
reader filter only) and the fences below.

**Done when.**

1. An R8 test creates a field of each type. It renames, orders, sections,
   hides, retires and restores each one. The values in `custom` survive
   each step, and org B sees none of it.
2. The same test walks the type matrix. A refused change gives 422 and
   changes no row.
3. A required custom field makes `POST /crm/deals` give 422 with its label.
   A PATCH that sets it to `null` gives 422. A PATCH that does not name it
   passes. A default value fills a new deal.
4. An R8 test PATCHes one key of `custom`. The other keys stay, and the
   value of a retired field stays. A JSON `null` removes its key. The test
   runs for leads, deals, contacts and organizations.
5. A bad value for a type gives 422 that names the field label.
6. A write that names a retired key gives 422 with "<label> is retired.
   Restore it to write a value." The stored value stays the same.
7. A lookup write stores `{"id", "name"}`. An `id` from org B gives 422. A
   `module:<ApiName>` target in Native mode gives 422.
8. A multi-select write stores a JSON array of option values. The custom
   filter finds the record by one of them.
9. A new option takes a slug `value`, with `-2` on a clash. A rename keeps
   the `value`.
10. `PUT /crm/field-layout/{entity}` sets the section order and the field
    order in one transaction. The key validator refuses `order`, `layout`
    and `restore`.
11. Each of the five readers of item 11 leaves out an `is_system` row. The
    custom filter refuses a retired key with 422. The export leaves it out.
    `crm_list_fields` marks it "retired".
12. An R8 case: an approved CRM-Q7a rule on a Zoho `double` field survives
    the `_FIELD_TYPES` change. Its status is not `type_mismatch`.
13. An R8 case: a sync over a key that a `source = 'metorite'` row holds
    leaves that row unchanged. Start over deletes the `metorite` rows.
14. Each route gives 403 for a member who is not an admin. It gives 404 for
    an org that the flag does not allow, and 409 in Mirror mode.
15. A sync over a Mirror org leaves the seven new columns unchanged.

**Fences.** `tests/unit/test_crm_field_studio_r8.py` (new, R8, `_DB_GATE`),
`tests/unit/test_crm_field_studio.py` (new, the key rule and the matrix),
`tests/unit/test_crm_field_rules.py`, `tests/unit/test_crm_field_rules_r8.py`,
`tests/unit/test_crm_mirror_readonly.py` and
`tests/unit/test_tenant_coverage.py`.

**Verification.**

```bash
bash scripts/dev_db.sh && eval "$(bash scripts/dev_db.sh --export)"
uv run pytest tests/unit/test_crm_field_studio_r8.py tests/unit/test_crm_field_studio.py tests/unit/test_crm_fields_r8.py tests/unit/test_crm_field_rules.py tests/unit/test_crm_field_rules_r8.py tests/unit/test_crm_export.py tests/unit/test_crm_mirror_readonly.py tests/unit/test_tenant_coverage.py -q
cd workbench/control_plane && npx tsc --noEmit && npx vitest run src/app/crm
```

**Built as (2026-10-13, branch `crm-c4a`).** 🔨 BUILT, not merged. Migration
260 holds `crm_field_sections` and the seven columns. The flag
`CRM_FIELD_STUDIO_ORGS` ships empty.

- **The section routes.** A `PATCH` or a `DELETE` of a section names its id:
  `/crm/field-sections/{entity}/{section_id}`. A delete clears `section_id`
  on its fields first, in the same transaction.
- **The key.** A label that gives a route word, a fixed column or a taken key
  gets a number. So the label "Order" gets the key `order_2`. A taken key in
  another case counts as a clash.
- **System fields.** `core.SYSTEM_FIELDS` lists 17 lead, 14 deal, 10 contact
  and 10 company columns. A system field takes a new label, section, order or
  `visible` only. A system field that the gateway needs stays visible.
- **The layout body.** It names each section once. It names each system field
  and each live custom field once. A retired field can stay out, and it keeps
  its place. Any other body gives 422 and writes nothing.
- **A retired key.** A record write that names a retired key gives 422 with
  the message "<label> is retired. Restore it to write a value." The stored
  value does not change. A studio `PATCH` of a retired field also gives 422.
- **An empty value.** A `PATCH` that sends `null`, a blank string or an empty
  list removes the key. A required field refuses each of them.
- **Options and targets.** An option leaves the list only with
  `retired: true`. A list that leaves out an option gives 422
  `option_removed`. A change of `lookup_target` gives 422 while a record holds
  a value for the key.
- **The default value.** Each read casts `default_value` to text. Psycopg
  gives a scalar jsonb as the value, and asyncpg gives it as JSON text. Without
  the cast, the text default "007" reads as a number under one of them.
- **The export.** The CSV adds a column for each custom field that is not
  retired, in the order of the layout, after the fixed columns. A lookup
  gives its name. A list of options joins with `; `.
- **The field definitions in a sync.** Only an import or a reconcile writes
  them. So "the next sync" in item 1 means the next import or reconcile.
- **Two anchors moved.** Item 13 names `settings.py:241` as the start over.
  That line is the change of setup (CRM-U5b). Now it also deletes the
  `metorite` fields and the sections. The start over (`core.RESET_TABLES`)
  already deleted every field row, and now it also deletes the sections.
- **Not in this slice.** The field map of a conversion is CRM-C6.

**Review round 1 (2026-10-13).** The owner decided item 1 below. The
coordinator decided the rest.

1. **"Required" binds a member write only.** A system writer fills the
   defaults and skips "required". `create_record` and `custom_for_create`
   take `system=True`, and the auto-lead step passes it.
2. **A conversion fills the defaults.** The new contact, company and deal
   each get the custom defaults, and "required" does not stop a conversion.
3. **Numbers.** A value of 10^15 or more gives 422. A JSON integer that is
   too big for a float also gives 422, not 500.
4. **Dates.** A date takes `YYYY-MM-DD` only, and a datetime takes the plain
   ISO form with `Z` or an offset. The record stores `isoformat()`.
5. **The matrix.** A `url` changes to `textarea`, not to `text` (item 9).
6. **RLS fence.** `test_crm_field_studio.py` reads migration 260. It
   requires ENABLE, FORCE and one policy with USING and WITH CHECK on
   `crm_field_sections`. The R8 fixture applies the generated policies, so
   the database alone cannot see a lost block.
7. **The export.** A picklist value exports as its option label. A hidden
   field is left out. A Mirror org keeps its Zoho custom columns.
8. **Restore.** A retire keeps `visible`, so a restore gives the field back
   with the visibility that it had.
9. **Saved views.** `GET /crm/views` leaves out a custom filter on a retired
   key and names the key in `skipped_filters`. The stored view keeps the
   key, so a restore brings the filter back. A list request that names a
   retired key still gives 422.

**The C4b and C4c audit (2026-10-13).** Two gaps moved into C4a.

- **The flag on the layout.** `GET /crm/fields/{entity}?layout=1` carries
  `studio` and `studio_admin`. `studio` is true when the flag allows the
  org. `studio_admin` also needs `admin:access:manage` and Native mode.
- **The live name of a lookup** (item 8). A record read and a list read give
  each lookup value the current name of its target. One tenant-bound query
  runs for each target entity. A target that is gone, or in another org,
  keeps the stored name and gets `"missing": true`.

**Fences.** `test_crm_field_studio_r8.py` (27 cases, R8),
`test_crm_field_studio.py`, `test_crm_mirror_readonly.py` (the studio
tables and the scan of the seven columns), `test_crm_fields.py`,
`test_crm_kill_switch.py` and `crmQ4.test.ts`.

#### CRM-C4p: the shared client

**Follows CRM-C4a.** Cut the branch from the trunk after C4a merges. C4b and
C4c both use what C4p builds. So C4p merges before either of them starts.

**What C4p reads from C4a.** The C4a fix round adds two facts (owner,
2026-10-14). The `crm-c4a` worktree holds them, but commit `cf4d6a9c4` does
not. At dispatch, read the merged `routes/crm/fields.py` and
`field_studio.py`. If one fact is absent, C4p is NO-GO.

- `GET /crm/fields/{entity}?layout=1` returns two booleans. `studio` is true
  when `CRM_FIELD_STUDIO_ORGS` allows the org. `studio_admin` is true only
  when the flag allows the org, the caller is an admin, and the mode is
  Native (`studio_flags` in `fields.py`).
- A record read gives each lookup value the live name of its target
  (`attach_lookup_names` in `field_studio.py`). A target that is gone, or
  that row-level security hides, keeps its stored name and gets
  `"missing": true`.

**Scope.**

1. **The layout type.** A new `lib/fieldLayout.ts` holds `FieldLayout` and
   `FieldSection`. They have the shape of `FieldLayout` in `fields.py`
   (`:110` on `crm-c4a`) and the two booleans. `FieldDef` in `lib/fields.ts`
   gets `section_id`, `required`, `help_text`, `default_value` and
   `lookup_target`.
2. **The read.** `api.getFieldLayout(entity)` calls
   `GET /crm/fields/{entity}?layout=1`.
3. **The hook.** `useFieldLayout(entity)` goes in `lib/useCrmMode.ts`,
   beside `useFieldDefs` (`:259`). It returns the layout, `loading` and
   `refresh`. A failed read gives `null`, and the caller then draws the view
   of today.
4. **The field of a refusal.** `CrmError` (`api.ts:47`) gets `field`. It is
   the `field` key of an object `detail`. C4a sends it with each refused
   value (`_unprocessable`, `field_studio.py:155` on `crm-c4a`).
5. **One way to call the gateway.** C4p exports `call` from `api.ts`
   (`:62`). Before C4p it is a private function. C4c calls the studio routes
   through it. A second `fetch` path is a defect.
6. **`FieldInput`.** A new `components/FieldInput.tsx`. It takes a
   `FieldDef`, a value, `onChange`, `error` and `locked`. It draws the input
   of the type from the table below, and it shows `error` under the input.
7. **The client check.** `checkValue` in `lib/fieldLayout.ts` runs before
   each save. It mirrors the server limits, so the gateway seldom gets a bad
   value. The gateway stays the authority.
8. **The empty value.** A field with no value reads "—". Precedent:
   `FieldsPanel.tsx:348` and `fieldText` in `lib/fields.ts`.
9. **The Mirror lock.** With `locked` set, `FieldInput` draws the value read
   only, with `LOCK_TIP`. Precedent: `FieldsPanel.tsx:359`. A Mirror record
   stays locked until CRM-C5.
10. **The read of a value.** `formatField` in `lib/fields.ts` changes for
    three types. A picklist and a multi-select show the option label, also
    for a retired option. Today they show the stored `value`. A lookup with
    `"missing": true` shows its stored name and the note "missing".
11. **The primitives.** `FieldInput` uses `Input`, `Textarea` and `Select`
    (`components/ui/Input.tsx`), `SelectButton` and `Checkbox`. It draws no
    raw control. The `crmX4.test.ts` fence (`:51`) refuses a raw `<input>`,
    `<textarea>` or `<select>` under `app/crm`.

**The input of each type.** The client check mirrors `field_studio.py` on
`crm-c4a` (the limits at `:127-134`, the patterns at `:137-143`, the checks
at `:374-467`). The Read column is what `formatField` shows.

| Type | Primitive | Client check | Read |
|---|---|---|---|
| `text` | `Input` | At most 255 characters (`TEXT_MAX`) | The text |
| `textarea` | `Textarea` | At most 32 000 characters (`TEXTAREA_MAX`) | The text, with its line breaks |
| `number` | `Input`, `inputMode="text"` | A whole number, and its size is less than 10^15 (`NUMBER_LIMIT`) | The number, with `en-IN` groups |
| `decimal` | `Input`, `inputMode="text"` | A finite number, and its size is less than 10^15 | The number, with `en-IN` groups |
| `currency` | `Input`, `inputMode="text"` | The check of `decimal` | `money()` |
| `percent` | `Input`, `inputMode="text"` | The check of `decimal` | The number and "%" |
| `date` | `Input`, `type="date"` | `YYYY-MM-DD`, and a real day | `shortDate()` |
| `datetime` | `Input`, `type="datetime-local"` | A real date and time. The client sends `toISOString()`, which has the `Z` that the server needs | `dateTime()`, in local time |
| `boolean` | `Checkbox` | `true` or `false` | "Yes" or "No" |
| `email` | `Input`, `type="email"` | At most 254 characters (`EMAIL_MAX`), and `isEmail` | A `mailto:` link |
| `phone` | `Input`, `type="tel"` | At most 40 characters (`PHONE_MAX`), 4 digits or more, and the pattern of `_PHONE_RE` | A `tel:` link from 6 digits (`TEL_MIN_DIGITS`). A shorter value is text |
| `url` | `Input`, `type="url"` | At most 2048 characters (`URL_MAX`), and `safeHref` | A link |
| `select` | `SelectButton` | One active option, or the option that the record holds | The option label |
| `multiselect` | One `Checkbox` for each option | A list of active options and held options, with no repeat | One chip for each option label |
| `lookup` | `Input` for the search, and `SelectButton` for the hits | A record of `lookup_target`, from the search | The live name. A missing target shows the stored name and "missing" |

- **A number is a JSON number.** The client sends a number, never text.
  `_is_number` (`field_studio.py:353`) refuses a string.
- **The lookup search.** The picker calls
  `api.listRecords(lookup_target, {q, page_size: 10})`. That is the `?q=`
  list search of CRM-X1 (`records.py:99`). The picker stores `{id, name}`
  of the chosen record, and the gateway checks the `id` again.
- **A retired option.** No picker offers it. A record that holds one shows
  it, and its input keeps it as a choice. The gateway allows a held value
  (`_select`, `field_studio.py:449`).

**Files.** `app/crm/lib/fieldLayout.ts` (new),
`app/crm/components/FieldInput.tsx` (new), `app/crm/lib/api.ts`
(`getFieldLayout`, `CrmError.field` and the `call` export only),
`app/crm/lib/useCrmMode.ts` (`useFieldLayout` only), `app/crm/lib/fields.ts`
(`FieldDef` and `formatField` only), `app/crm/components/crmC4p.test.ts`
(new) and `tests/unit/test_crm_field_input_parity.py` (new).

**Done when.**

1. `crmC4p.test.ts` draws `FieldInput` for each of the 15 types. Each one
   draws the primitive of its row in the table.
2. For each type, the client check passes a good value and refuses a bad
   one. The test hits each limit: 255, 32 000, 10^15, 254, 40 and 2048.
3. A `datetime` input sends a value with `Z`. A `number` input sends a JSON
   number.
4. An empty value reads "—". A `locked` input draws no control, and it
   shows `LOCK_TIP`.
5. `formatField` shows the option label of a picklist and a multi-select,
   also for a retired option. A missing lookup shows its stored name and
   "missing".
6. `useFieldLayout` returns `studio`, `studio_admin`, the sections and the
   fields. A failed read gives `null`.
7. A 422 with an object `detail` gives a `CrmError` with its `field`.
8. `test_crm_field_input_parity.py` reads the limits and the type list from
   `lib/fieldLayout.ts`. It compares them with `TEXT_MAX`, `TEXTAREA_MAX`,
   `NUMBER_LIMIT`, `EMAIL_MAX`, `PHONE_MAX`, `URL_MAX` and `FIELD_TYPES` of
   `field_studio.py`. A change on one side only turns it red.
9. `crmX4.test.ts` stays green, so `FieldInput.tsx` holds no raw control.

**Fences.** `app/crm/components/crmC4p.test.ts` (new),
`tests/unit/test_crm_field_input_parity.py` (new, the limits and the type
list), `app/crm/components/crmX4.test.ts` and
`app/crm/components/crmQ4.test.ts`.

**Verification.**

```bash
uv run pytest tests/unit/test_crm_field_input_parity.py -q
cd workbench/control_plane && npx tsc --noEmit && npx vitest run src/app/crm
```

**Built as (2026-10-14, branch `crm-c4p`).** 🔨 BUILT, not merged. No
migration, no flag and no new surface.

- **The files.** `lib/fieldLayout.ts` holds the layout type, `checkValue`,
  `toWire` and `fromWire`. `components/FieldInput.tsx` draws the input.
  `api.ts` exports `call` and adds `getFieldLayout`. `useCrmMode.ts` adds
  `useFieldLayout` and its read, `readFieldLayout`.
- **The length.** `checkValue` counts code points (`[...s].length`), as
  Python `len` does. JS `.length` counts UTF-16 units.
- **The year.** A date and a datetime keep the year in 1..9999.
  `toISOString()` can give `+010000-…` or `0000-…`, and the gateway refuses
  both.
- **The phone.** `PHONE_RE` is the text of `_PHONE_RE`, with its extension
  group. `ONE_NUMBER` in `fields.ts` stays the rule of a `tel:` link.
- **The value of an edit.** `toWire` changes the text of an input into the
  JSON value. A blank text gives `null`. A number type gives a JSON number,
  and a text that is not a number stays text, so the check refuses it. A
  `datetime` gives `toISOString()`.
- **The options.** `fieldOptions` in `fields.ts` reads the options with the
  retired flag. `formatField` and `FieldInput` use it. `optionsOf` in
  `filterBar.ts` does not change, because C4b owns that file.
- **A missing lookup** reads "<name> (missing)". `LOOKUP_MISSING` holds the
  word.
- **A multi-select.** The client check refuses a repeat, and the gateway
  removes a repeat. A cleared multi-select sends `null`.
- **The tests.** The node runner cannot run a hook. So `crmC4p.test.ts` tests
  `readFieldLayout` against a stubbed `fetch`. It tests an edit through
  `toWire`, and a source check proves that each text edit calls it.
- **The parity fence.** `test_crm_field_input_parity.py` reads the six
  limits, `FIELD_TYPES` and `LOOKUP_TARGETS` as literals. It runs the five
  patterns of both sides on one table of samples. It compiles the TypeScript
  patterns with `re.ASCII`, because JS `\d` matches only ASCII digits.

**Review round 1 (2026-10-14).** The coordinator decided each item.

1. **Commas (P1).** `toWire` removes the commas of a Western grouping
   (`1,234.5`) or an Indian grouping (`12,34,567`) only. Any other comma
   stays in the text, so "1,5" never saves as 15. `checkValue` then gives
   "Use a dot for decimals."
2. **A failed re-read.** `readFieldLayout` throws on a failure.
   `useCachedResource` then keeps the last good layout, and
   `useFieldLayout` gives the message in `error`.
3. **The error belongs to its control.** Each control names the help text
   and the error in `aria-describedby`, and it sets `aria-invalid`.
   `SelectButton` takes an optional `id`, `describedBy` and `invalid`. The
   multi-select is a `role="group"` with `aria-labelledby`.
4. **The keyboard.** The four number types use `inputMode="text"`, because
   the numeric keypad of iOS has no minus key.
5. **A boolean.** A boolean has no empty state in a form. `formValue` gives
   the stored value, else the default, else `false`. C4b sends that value,
   so a required boolean that nobody ticks saves as `false`.
6. **What a picker sends.** `toggleOption` gives an array of option values,
   and `pickLookup` gives the `{id, name}` of the hit. A failed search shows
   "Search failed".
7. **The shared cases.** `tests/fixtures/crm_field_check_cases.json` holds
   date, datetime and phone cases. The parity test runs `field_studio` on
   it, and `crmC4p.test.ts` runs `checkValue` on it. It found one drift: Python
   takes an offset such as `+00:99`, so the client now takes an offset
   strictly inside one day. The parity test also fences `PHONE_MIN_DIGITS`.
8. **A missing lookup is a C4b duty.** A form sends only the keys that
   changed. So a lookup with `"missing": true` that the member does not
   touch never reaches a save, and the gateway never checks its old id again.

#### CRM-C4b: the record UI

**Follows CRM-C4p.** Cut the branch from the trunk after C4p merges. It may
run beside C4c. It reads the layout through `useFieldLayout`, and it draws
each custom value with `FieldInput`. It writes through the C4a custom write
path.

**Dark by default.** Each C4b change draws only when the layout says
`studio`. With `studio` false, each surface stays as it is today. The 422
rule (Scope item 4) is the one exception, and it holds in every case.

**Scope.**

1. The create and edit forms (items 5 to 8).
2. The Fields tab by layout (item 12).
3. The Columns control, and the filter bar for custom fields.
4. **The 422 rule.** A form stays open after a refused save. It shows the
   server `message` beside the field that `detail.field` names. A refusal
   with no `field` shows at the top of the form. Today the create modal
   closes on each result: `page.tsx:1185` calls `setCreating(null)` after
   `store.createRecord`. C4b closes it only when the save returns an id.
5. **The filter bar** (owner, 2026-10-14). Today it offers picklist custom
   fields only (`picklistDefs`, `filterBar.ts:151`). C4b adds more types.
   It uses only the operators of the CRM-Q8 custom filter (`extra_filters`,
   `core.py:1208` on `crm-c4a`). There are two operators, and neither is a
   range:
   - `custom.<key>=<value>`. The text of the stored value equals `<value>`.
     For an array, the array holds `<value>`.
   - `custom.<key>.in=a,b`. The value is one of the list. For an array, the
     array holds one of the list.

   | Type | The filter | The query |
   |---|---|---|
   | `select`, `multiselect` | Any of the active options, as today | `custom.<key>.in` |
   | `boolean` | Yes or No | `custom.<key>.in=true` or `.in=false` |
   | `number`, `decimal`, `currency` | Equal to one number | `custom.<key>.in=` and `String(Number(text))` |
   | `date` | Equal to one day | `custom.<key>.in=YYYY-MM-DD` |

   The re-audit (2026-10-14, fix B) chose `custom.<key>.in` with one value
   for each row. The existing `extraQuery` (`lib/filters.ts`) sends it, and
   the gateway matches one value of `.in` as it matches `=`.

   - The number query matches the text that Postgres gives for a JSON
     number that the client wrote.
   - "No" does not find a record that holds no value for the key. The
     control says so in a hint.
   - A range on a number or a date needs a new gateway operator. It is not
     in C4b. It is a follow-up.

**Files.**

- `app/crm/components/QuickCreateModal.tsx`, `FieldsPanel.tsx`,
  `RecordSheet.tsx` and `FilterBar.tsx`.
- `app/crm/components/RecordList.tsx:155-317`, the Columns control. The
  state and the button are at `:155-185`, and `ColumnChooser` is at
  `:275-317`.
- `app/crm/lib/fields.ts` (`fieldRows` and `pickable`) and
  `app/crm/lib/filterBar.ts`.
- `app/crm/lib/api.ts` and `app/crm/lib/useCrmMode.ts` (owner,
  2026-10-14). C4c edits neither of them.
- `app/crm/lib/store.ts`, in `createRecord` (`:602`) and `patchRecord`
  (`:576`) only. Today they keep only the message of a refusal. The form
  needs the `field` of the `CrmError` too.
- `app/crm/page.tsx`, the create modal hunk at `:1177-1188` only.
- `app/crm/components/crmC4b.test.ts` (new).

**Not `lib/columns.ts`.** It is the list of fixed columns that
`test_crm_export.py` parses. The custom columns of a list come from
`pickable` (`RecordList.tsx:164`). The CSV custom columns are C4a's
(`export.py`). So C4b does not change `columns.ts`.

**Done when.**

1. With `studio` true, the create form shows each visible custom field of
   the layout through `FieldInput`, in section order. A required field with
   no value blocks the save, except a boolean. A default fills its input.
2. An edit sends `custom` as a partial map that holds only the changed key.
   A cleared value sends a JSON `null`.
3. The lookup input searches the `lookup_target` entity only, through
   `?q=`. It writes `{"id", "name"}`, and the record shows the live name.
4. The multi-select input writes a JSON array of option values. A retired
   option shows on a record that holds it, and no picker offers it.
5. With `studio` true, the Fields tab draws each section in order. It shows
   a visible field with no value as "—", and a member can edit it there. A
   Mirror record shows each field locked.
6. **Already true, so keep it true.** `pickable` leaves out a retired field
   and a system row (`pickable` in `lib/fields.ts`). `picklistDefs` reads
   `pickable` (`filterBar.ts:151`). So the Columns control and the filter
   bar offer no retired field today. A C4b test proves that each new filter
   type keeps this rule.
7. The filter bar offers the filters of Scope item 5. A test reads the query
   string of each row.
8. A refused create or edit keeps the form open. The server message shows
   beside the field that `detail.field` names, and the typed values stay.
9. With `studio` false, the forms, the Fields tab, the Columns control and
   the filter bar draw as today. A test proves it for each one.
10. The `visual-review` skill shows the form, the Fields tab and the filter
    bar. Check light mode, dark mode, compact density, a changed accent and
    phone width.
11. A record holds a lookup with `"missing": true`. An edit of another key
    sends no lookup key (re-audit, 2026-10-14).

**Fences.** `app/crm/components/crmC4b.test.ts` (new),
`app/crm/components/crmC4p.test.ts`, `app/crm/components/crmX4.test.ts` and
`tests/unit/test_crm_export.py`. The last one shows that `columns.ts` did not
change.

**Verification.**

```bash
uv run pytest tests/unit/test_crm_export.py -q
cd workbench/control_plane && npx tsc --noEmit && npx vitest run src/app/crm
```

**Built as (2026-10-14, branch `crm-c4b`).** 🔨 BUILT, not merged. No
migration, no flag and no new route. The re-audit of 2026-10-14 gave four
binding fixes, and the items below record each one.

- **The files.** `lib/recordForm.ts` is new. It holds the rules of the two
  forms, because the spec named no home for them and `lib/` holds each pure
  rule of the CRM. `fields.ts` adds `layoutGroups`, `columnGroups` and
  `undefinedRows`. `filterBar.ts` adds `customFilters` and the filter
  values. The store keeps `refusal`, the message and the field of a refused
  create or edit. `api.ts` and `useCrmMode.ts` needed no change.
- **The sections.** The fields of no section come first, with no heading.
  Each section follows in the order of `position`. A `section_id` that the
  layout does not list counts as no section. A section with no field to show
  is left out.
- **The create form.** The fixed fields of today stay first, and the custom
  fields follow through `FieldInput`. A required field with no value keeps
  the Create button disabled. A boolean never blocks, because `formValue`
  gives it `false` (fix 3). A default that the member clears goes as `null`,
  because the gateway fills only an absent key.
- **The Fields tab.** Each field has an inline edit, as the Overview has. A
  save sends only its own key (fix 4). A system field that names another
  row reads only: the stage, the pipeline, the company, the lead and the
  lost reason. The tab shows the name of the stage and the company. It
  leaves out the other three, because an id means nothing to a member. A
  custom key with no definition follows the sections, read only.
- **The filters.** Each filter sends `custom.<key>.in` with one value through
  `extraQuery` (fix 2), and `lib/filters.ts` did not change. A number filter
  reads its text through `toWire`, so `1,20,000` gives `120000`. The "No"
  choice has the hint "A record with no value is not found." `optionsOf` now
  reads `fieldOptions` and leaves out a retired option (the C4p follow-up).
- **The 422 rule.** `page.tsx` closes the create modal only when the save
  returns an id. A refusal shows beside the field that it names, or at the
  top of the form. A form shows a refusal only after it sent a save.
- **The fences.** `crmC4b.test.ts` has 42 cases. Seven mutations turned it
  red: a boolean with no `formValue`, a lookup compared whole, a close
  before the guard in `page.tsx`, a retired option in `optionsOf`, a filter
  list without `pickable`, a filter bar that ignores `studio`, and a store
  that drops the field. One mutation stayed green. It removed the boolean
  clause of `missingRequired`, which no case reached, so the slice removed
  that clause.
- **The visual review.** A temporary page under `src/app` drew the Fields
  tab, the create form with each of the 15 types, and a list with three
  custom columns. Playwright took each one in dark mode, light mode, compact
  density, a changed accent and at 390 px. The builder then deleted the page.
- **Not in this slice.** A range on a number or a date needs a new gateway
  operator. The number filter matches a JSON number that the client wrote.
  A Zoho value such as `1500.0` gives other text, so it does not match
  `1500`.

**Review round 1 (2026-10-14).** The coordinator decided each item.

1. **The one deliberate flag-off change is the 422 rule.** A refused create
   keeps the form open also with `studio` off, because a form that closes
   loses what the member typed. Each other flag-off surface draws as the
   parent did. Fence: "the one deliberate flag-off change" in
   `crmC4b.test.ts`.
2. **The currency (P1).** A system `currency` field reads in
   `record.currency`, in the edit view and under the Mirror lock
   (`layoutValue`). A deal with no probability of its own shows "40% (from
   Proposal)", as the Overview does.
3. **An unset boolean.** A save of No or Yes on a record that holds no
   boolean sends the value. When nobody edits it, the field still reads "—".
4. **The keyboard.** The inline edit takes the focus. Enter saves, except in
   a long text and on a button, and Escape cancels (`editKey`). After a save
   or a cancel, the focus goes back to the Pencil button.
5. **Golden files.** `__fixtures__/c4b-flag-off/` holds the markup of the
   create form, the Fields tab, the list with the Columns chooser open and
   the filter bar. The parent commit `33b16848d` made each one.
   `crmC4bGolden.test.ts` draws the new code with `studio` false and with no
   layout, and compares. The mutation `layout ? layout : null` in
   `RecordList.tsx` turns it red.
6. **Two more fences.** The create form calls `onClose` only from Cancel and
   the Modal. A Mirror create draws each custom field locked and sends none.
7. **Notes.** Each section of the Columns control is a `role="group"` with
   its label. A multi-select value that no option has shows in the editor as
   a chip with "not an option" and a remove button.

The fences now hold 58 cases, and seven more mutations turned them red.

#### CRM-C4c: the studio UI

**Follows CRM-C1 and CRM-C4p.** Cut the branch from the trunk after C4p
merges. It may run beside C4b. It fills the `fields` settings section that
C1 makes.

**Where the code goes.** Put the new code in a new `lib/fieldStudio.ts` and
in new `components/FieldStudio*.tsx` files. Beyond those, C4c edits only
`FieldsCard.tsx`. `fieldStudio.ts` calls the studio routes through `call`
of `api.ts`. After each write, it calls `refresh` of `useFieldLayout`.

**Who sees the studio.** The `fields` section is for an admin only
(`settingsSections.ts:46`, `PipelineSettings.tsx:296`). So a member never
sees the studio. `FieldsCard` draws the studio when `studio_admin` is true.
In each other case, the admin sees the read-only `FieldsCard` of today.

**Scope.**

1. The studio of items 2, 3, 4, 6, 9 and 14.
2. **The type menu.** At create, it offers each of the 15 types. On a field
   that exists, it offers only the changes of the matrix (item 9). The
   client keeps the matrix as `TYPE_CHANGES` in `fieldStudio.ts`. A copy by
   hand goes stale, so a parity fence holds it to `TYPE_CHANGES` in
   `field_studio.py` (`:103` on the trunk after C4p).
3. **The lookup target.** A lookup create picks one of the four entity
   slugs: `leads`, `contacts`, `organizations` or `deals`
   (`NATIVE_LOOKUP_TARGETS`, `field_studio.py:119`). The studio offers no
   `module:<ApiName>` target. The gateway refuses a target change while a
   record holds a value, and the studio shows that message.
4. **Order by drag.** A section and a field move by HTML5 `draggable`.
   Precedent: the stage grid at `PipelineSettings.tsx:511`. A drop calls
   `PUT /crm/field-layout/{entity}` with the whole order in one body.
5. **The default editor.** It uses `FieldInput` from C4p. So a default takes
   the client check of its type. A lookup has no default
   (`field_studio.py:491`).
6. **The docstring.** `FieldsCard.tsx:9` names CRM-S6. C4c changes it to
   name CRM-C4.

**Files.** `app/crm/lib/fieldStudio.ts` (new),
`app/crm/components/FieldStudio*.tsx` (new), `app/crm/components/FieldsCard.tsx`,
`app/crm/components/crmC4.test.ts` (new) and
`tests/unit/test_crm_field_type_change_parity.py` (new).

**Done when.** Each case is in `crmC4.test.ts`, except where a case names
another file.

1. **Create.** The studio creates a field of each of the 15 types, with the
   type word of item 1. A lookup create sends one of the four slugs as
   `lookup_target`.
2. **Rename.** A rename sends `label` only, and the key stays.
3. **Reorder.** A drag of a field, also into another section, calls
   `PUT /crm/field-layout/{entity}`. The body holds the section order and
   the field order of each section.
4. **Sections.** The studio creates, renames, reorders and deletes a
   section. Before a delete, it says that the fields move to no section.
5. **Picklist options.** The option editor adds, renames, reorders and
   retires an option. A rename keeps the `value`. A retire sends
   `"retired": true` and keeps the option in the list. The gateway refuses
   a missing option with 422 `option_removed`.
6. **Retire and restore.** A retire calls `DELETE /crm/fields/{entity}/{key}`.
   A retired field shows a Restore control, which calls
   `POST /crm/fields/{entity}/{key}/restore`.
7. **Required.** A toggle sends `required`.
8. **Default.** The default editor is `FieldInput`. It refuses a bad value
   before the save. It shows a 422 beside the field.
9. **System fields.** For a system field, the studio offers label, hide
   and order only. It has no type menu, no retire, no required and no default. A system field
   that the gateway needs offers no hide.
10. **The type menu.** On a field that exists, it offers the matrix changes
    only.
11. **The parity fence.** `test_crm_field_type_change_parity.py` reads
    `TYPE_CHANGES` from `lib/fieldStudio.ts`. It compares it with
    `TYPE_CHANGES` in `field_studio.py`, as
    `test_crm_stage_discipline_parity.py` does for `REQUIREABLE_FIELDS`. A
    change on one side only turns it red.
12. **Who sees it.** A member never sees the `fields` section. An admin
    with the flag off sees the read-only `FieldsCard` of today. An admin of
    a Mirror org sees it too.
13. The `visual-review` skill shows the studio. Check light mode, dark mode,
    compact density, a changed accent and phone width.

**Fences.** `app/crm/components/crmC4.test.ts` (new),
`tests/unit/test_crm_field_type_change_parity.py` (new),
`app/crm/components/crmC1.test.ts` and `app/crm/components/crmC4p.test.ts`.

**Verification.**

```bash
uv run pytest tests/unit/test_crm_field_type_change_parity.py -q
cd workbench/control_plane && npx tsc --noEmit && npx vitest run src/app/crm
```

**Built as (2026-10-14, branch `crm-c4c`).** 🔨 BUILT, not merged. No
migration, no flag and no gateway change. The re-audit gave GO, and the three
anchors above now carry the measured lines.

- **The files.** `lib/fieldStudio.ts` holds the rules and the eight studio
  calls, each through `call` of `api.ts`. `components/FieldStudio.tsx` draws
  the studio. `FieldStudioFieldDialog.tsx`, `FieldStudioOptions.tsx` and
  `FieldStudioSectionDialog.tsx` draw the forms. `FieldsCard.tsx` switches to
  the studio.
- **Who sees it.** `fieldsCardShape` gives the studio only when the layout
  says `studio_admin`. A failed read gives an error with Retry, and a first
  read gives a skeleton. Mirror mode reads no layout. Its note now ends "Edit
  fields in Zoho." (owner brief, 2026-10-14).
- **The layout on screen.** One tab for each record type. The fields in no
  section come first, then each section, then the retired fields.
  `layoutBody` numbers the fields in that order, so the order on screen is the
  order of `position`. A drop takes the place of the row under it, as the
  stage grid does. A drop on a section header puts the field first in it.
- **A system field.** The studio reads "the CRM needs it" from `required:
  true` on a system row of the layout (`field_wire`, `fields.py`). It does
  not copy a list. Its form offers the label and a hide only. A drag moves
  it, also into a section.
- **The options.** A saved option offers Retire and Restore. Only a new
  option, which has no `value` yet, offers Remove. A new option cannot be a
  default until the save gives it a `value`.
- **A refusal.** `errorPart` puts a 422 beside the control that its `code`
  names. A refusal with `CrmError.field` goes beside the default editor,
  because the default is the only value that the studio sends. A refusal of
  a drag, a restore, a retire or a section delete shows at the top of the
  card. Each write calls `refresh` of `useFieldLayout`, also after a
  refusal, and it drops the cached field definitions of the entity.
- **The parity fence** also holds `LABEL_MAX`, `OPTION_LABEL_MAX` and
  `HELP_MAX` to the Python, because the forms check them before the save.
- **The visual check.** Playwright stubbed the layout on a temporary preview
  page. It showed the studio for deals, the create form, the option editor, a
  retired field and a system field. Each was in dark, light, compact, the Rose
  accent and at 390 px. A real HTML5 drag of a field and of a section sent one
  `PUT` each. At 390 px the header note was too narrow, and the fix is in. The
  implementer deleted the page.
- **Not in this slice.** The `visual-review` skill did not run, and the
  Playwright check stood in for it.

**Review round 1 (2026-10-14).** The coordinator decided each item.

1. **A required field that no member can fill (P1).** The gateway gives 422
   `required_unfillable` for a required field that is hidden with no default.
   It gives the same 422 for a required picklist or multi-select with no
   active option. `check_fillable` in `field_studio.py` runs at a create and
   at a PATCH. So it also refuses a hide, a cleared default, a new "required"
   and a retire of the last active option. `checkDraft` refuses the same
   shapes before the save. It also refuses text in "New option" that the
   admin did not add: "Add or clear the new option." R8 case:
   `test_a_required_field_that_no_member_can_fill_gives_422`.
2. **A failed drop after a tab switch.** The tabs are off while a write runs.
   `revertedOrder` goes back to the served layout only on the tab of the drop.
3. **A close during a save.** Each dialog ignores Escape and the scrim while
   its save runs (`guardClose`). A refusal that comes after the form is gone
   shows in the notice of the studio.
4. **Move up and Move down.** Each field, section and option has the two
   buttons, so a keyboard and a phone can change the order. A move sends the
   same full order as a drag (`stepField`, `stepSection`, `moveOption`).
5. **The options of a PATCH.** The save reads the options again
   (`mergeOptions`). It applies only the changes of this dialog. Those are a
   rename and a retire by `value`, each new option, and the order. An option that another admin also
   changed gives "Someone else changed the options. Reopen to see them."
6. **A failed layout read** shows "The fields could not be read." and Retry.
   It never says "Field editing comes later."
7. **The notes.** `typeChoices` of a system field has a test. A section
   delete counts its retired fields too (`sectionFieldCount`).
   `src/components/ui/` has no shared Card, so the studio keeps the frame of
   the settings cards.

### 20.8 CRM-C5: Field studio and stage order written to Zoho (Mirror)

**Gate.** AGENT-SAFE to build, with the flag off. 🔴 **OWNER-GATE** to turn
the flag on for an org, and for the first write to a live Zoho. A write to a
customer's Zoho is a write to a third party (`CLAUDE.md` §3a rule 3). C5
needs its own spec audit before dispatch, as CRM-Z9 does.

**The flag.** `CRM_ZOHO_CONFIG_WRITE_ORGS` in `routes/crm/flags.py`, with the
grammar of `CRM_MIRROR_ORGS`. It ships empty. It is separate from the record
flag of CRM-Z9, because a field change reaches every user of that Zoho.

**The consent.** C5 extends the second consent of §19.9. The read consent
does not change, and `ZOHO_READ_SCOPES` gets no write scope. If CRM-Z9 has
not built the second consent route, C5 builds it to the rules of §19.9. The
slice that comes second adds its scopes to the same route.

C5 asks for the narrowest scopes, never `.ALL`. Each name is on a Zoho v8
page:

| Scope | Why | Source |
|---|---|---|
| `ZohoCRM.settings.fields.CREATE` | Create a custom field | [create-custom-field](https://www.zoho.com/crm/developer/docs/api/v8/create-custom-field.html) |
| `ZohoCRM.settings.fields.UPDATE` | Label, help text, default, picklist add and rename | [update-custom-fields](https://www.zoho.com/crm/developer/docs/api/v8/update-custom-fields.html) |
| `ZohoCRM.settings.layouts.UPDATE` | Sections, required, and a move to Unused | [update-custom-layout](https://www.zoho.com/crm/developer/docs/api/v8/update-custom-layout.html) |
| `ZohoCRM.settings.pipeline.UPDATE` | Stage order in a pipeline | [update-pipeline](https://www.zoho.com/crm/developer/docs/api/v8/update-pipeline.html) |

C5 never asks for `ZohoCRM.settings.fields.DELETE`. A retire in Metorite
moves the field to Unused in Zoho. It never deletes a Zoho field or its data.

**What Zoho allows, and what C5 offers.** Paths are under `/crm/v8`.

| Edit | Zoho API | C5 offers it |
|---|---|---|
| Create a custom field | `POST /settings/fields?module=`, 5 fields at most for each call | Yes |
| Rename, help text, default value | `PATCH /settings/fields/{id}?module=` | Yes |
| Add or rename a picklist option | `PATCH` with `pick_list_values`, by `id` or `actual_value` | Yes |
| Retire an option, hide a field | `PATCH /settings/layouts/{id}?module=` with `_delete: {permanent: false}` | Yes |
| Add a section, set required | `PATCH /settings/layouts/{id}?module=` | Yes |
| Stage order in a pipeline | `PUT /settings/pipeline/{id}?layout_id=` with `maps[].sequence_number` | Yes |
| Change a type | Zoho refuses it | No |
| Change an API name | Zoho allows it in its own UI only | No |
| Order fields or sections | No documented write. **UNVERIFIED** | No. The UI says "Order in Zoho" |
| Order picklist options and Lead Status | `sequence_number` shows in reads only. **UNVERIFIED** | No. The C3 overlay holds it |
| Stage probability and forecast type | No documented write. **UNVERIFIED** | No. The C3 overlay holds it |
| Lead conversion mapping | `convert_mapping` is read only. **UNVERIFIED** | No |

Before the build, test each "Yes" row against a Zoho sandbox org. A row that
fails moves to "No". The sandbox is the owner's to set up (§12).

**Scope.**

1. **The writer.** New `crm_sources/zoho/config_writer.py`, inside the
   source seam. It is the one code path that writes Zoho metadata. It
   refuses an edit when the stored scopes lack the scope of that edit
   (§19.9 rule 6).
2. **The routes.** The C4 studio routes and the C3 order route
   (`PUT /crm/statuses/{kind}/order`) check the
   mode. In Mirror mode with the flag on, they call the writer and write
   nothing to Postgres. In Mirror mode with the flag off, they give 409.
3. **Zoho first, then the mirror.** After a 2xx from Zoho, the route reads
   `GET /settings/fields?module=` for that one module, at 1 credit. It
   writes the result through the sync's field path, and returns the new
   definitions.
4. **Errors.** A Zoho error gives 502 with the Zoho `code` and `message`,
   and writes no local row. The studio shows the message beside the field.
   `LIMIT_EXCEEDED` shows "Zoho allows N custom fields in this module on
   your edition."
5. **The limit.** N comes from the edition in the source profile: Standard
   10, Professional 155, Enterprise 300, Ultimate 500, for each module. An
   unknown edition shows the Zoho message only.
6. **Without the flag.** The Mirror studio is read only. Each field shows
   "Edit in Zoho", a link from `deep_link` and the org domain (CRM-Q1).
7. **The audit and the budget.** Each Zoho write writes one audit line: the
   admin, the module, the edit and the Zoho code. It holds no record value
   and no token. Each write counts against the sync budget of the org.

**Non-goals.** Record write-back (CRM-Z9). A path through Zoho's MCP (§7.2).
The `ingestion` writer stays for records only. Custom modules in Zoho.

**Files.** `crm_sources/zoho/config_writer.py` (new), and
`routes/crm/connection.py` if CRM-Z9 has not built the consent. Also
`flags.py`, `field_studio.py`, `admin.py`, `crm_sync/engine.py` (a field
read of one module), the Fields and Deal stages sections, and the fences.

**Done when.**

1. A fake-Zoho test sends a create, a rename and a stage reorder. Each is
   one request with the body of the table. A 400 `LIMIT_EXCEEDED` gives
   502 and writes no row.
2. The same test sends no request with the flag off, or for a connection
   that lacks the scope.
3. An R8 test shows that the field read writes the new definition, and
   that org B does not change.
4. A test shows that `ZOHO_READ_SCOPES` holds no `CREATE`, `UPDATE` or
   `DELETE` scope.
5. `crmC5.test.ts` shows "Edit in Zoho" with the flag off, and the error
   line under a field.
6. **Owner act, not the build.** The owner turns the flag on for a test
   Zoho org and makes one write. The evidence is the field in Zoho and the
   audit line.

**Fences.** `tests/unit/test_crm_zoho_config_write.py` (new),
`tests/unit/test_crm_zoho_config_write_r8.py` (new, R8, `_DB_GATE`),
`tests/unit/test_crm_upsert_from_source_fence.py` and
`app/crm/components/crmC5.test.ts` (new).

**Verification.**

```bash
bash scripts/dev_db.sh && eval "$(bash scripts/dev_db.sh --export)"
uv run pytest tests/unit/test_crm_zoho_config_write.py tests/unit/test_crm_zoho_config_write_r8.py tests/unit/test_crm_upsert_from_source_fence.py -q
cd workbench/control_plane && npx tsc --noEmit && npx vitest run src/app/crm
```

### 20.9 CRM-C6: Field mapping across modules

**Gate.** AGENT-SAFE. The Native write sits behind `CRM_FIELD_STUDIO_ORGS`.

**One section, three cards.** The "Field mapping" section of CRM-C1 holds
them in this order.

**(a) Lead conversion.** Which lead field fills which Contact, Company and
Deal field.

1. **A new migration (expand only).** Take the next free number at build,
   because WS-58 MD-2d took 259 (§20.2). `crm_field_defs` gets `convert_mapping
   JSONB NULL`. A lead row holds `{"contact", "company", "deal"}`, each a
   target key or NULL.
2. **Mirror.** Zoho returns `convert_mapping` on each Leads field of `GET
   /settings/fields?module=Leads`. Its form is `{"Contacts", "Accounts",
   "Deals"}`, each a Zoho API name
   ([field-meta](https://www.zoho.com/crm/developer/docs/api/v8/field-meta.html)).
   The sync stores it with our keys. The card shows it read only, with "Edit
   in Zoho". No write API is documented (**UNVERIFIED**).
3. **Native.** An admin picks a target for each custom lead field. The
   default target has the same key and a compatible type. A pair is
   compatible when the types are equal, or when C4 item 9 (the type change
   matrix) allows the change.
4. **The convert route.** `POST /crm/leads/{id}/convert` copies each mapped
   custom value onto the new contact, company and deal, in the same
   transaction. An unmapped value stays on the lead. The fixed columns keep
   their mapping in code, and the card shows them read only.

**(b) The Field map.** The CRM-Q7a card moves into this section with no
change. Its "Suggest mappings" button is CRM-Q7b, which keeps its contract
(§18.7). Q7b calls `acompletion_with_fallback` with `feature="crm.field_map"`,
so the Router meters it (§20.1 fact 2).

**(c) Lookups.** A read-only table of each lookup field and its target, for
each entity and mirrored module. In Mirror mode the sync fills
`lookup_target` from the Zoho `lookup.module`. In Native mode the C4 studio
sets it. A target that the org does not have shows "Not mirrored".

**Non-goals.** A map between two generic modules. A Zoho write of the
conversion mapping. Activities on a convert.

**Files.** `infra/postgres/<next free>_crm_convert_mapping.sql` (new),
`routes/crm/field_studio.py`, `pipeline.py`, `crm_sources/zoho/mapping.py`,
`crm_sync/engine.py`, the Field mapping section in `app/crm/components/`, and
the fences below.

**Done when.**

1. An R8 test maps two custom lead fields in a Native org and converts a
   lead. The contact and the deal hold the two values. An unmapped value
   stays on the lead. Org B does not change.
2. The same test refuses a map between two incompatible types with 422.
3. A sync test stores `convert_mapping` and `lookup_target` from a Zoho
   fixture. A Mirror write of either gives 409.
4. `crmC6.test.ts` shows the three cards, and the read-only Mirror state.

**Fences.** `tests/unit/test_crm_convert_mapping_r8.py` (new, R8,
`_DB_GATE`), `tests/unit/test_crm_discovery.py`,
`tests/unit/test_crm_field_rules_r8.py` and `app/crm/components/crmC6.test.ts`
(new).

**Verification.**

```bash
bash scripts/dev_db.sh && eval "$(bash scripts/dev_db.sh --export)"
uv run pytest tests/unit/test_crm_convert_mapping_r8.py tests/unit/test_crm_discovery.py tests/unit/test_crm_field_rules_r8.py -q
cd workbench/control_plane && npx tsc --noEmit && npx vitest run src/app/crm
```

#### CRM-C6a: the Native lead-conversion mapping

**The audit (2026-10-14).** GO-NARROWED. C6a is card (a) in Native mode only.
The coordinator resolved the five blockers of the audit, and this contract
records each resolution.

**Scope.**

- A new migration at the next free number. It adds the tenant table
  `crm_convert_mappings`. The table replaces the `convert_mapping` column of
  item 1 of card (a).
- `GET` and `PUT /crm/conversion-mapping`.
- The mapping in `POST /crm/leads/{id}/convert` (`pipeline.py:614`).
- A table for each target entity in the `field-mapping` section, Native mode.

**The table.** One row holds `organization_id`, `source_key`,
`target_entity`, `target_key` (or NULL) and `skip`. `source_key` is the key of a
custom lead field. `target_entity` is `contact`, `company` or `deal`. The row is
unique for each `(organization_id, source_key, target_entity)`. The table has
FORCE RLS and one policy with USING and WITH CHECK, and the migration only
expands (R6).

**What a row means (blocker 2).**

1. A row with `target_key` NULL and `skip` false means "auto". No row at all
   also means "auto".
2. "Auto" maps to the target field with the same key, when that field is live
   and its type is compatible. With no such field, the value is not copied.
3. `skip` true means "unmapped". The conversion does not copy that lead
   field. A skip row has no `target_key`.
4. A row with a `target_key` is "explicit". The key names a custom field of
   the target entity.
5. A pair is compatible when the types are equal. It is also compatible when
   C4 item 9, the type change matrix, allows the change. A lookup pairs only
   with a lookup that has the same target entity.
6. A target field takes one lead field at most. `PUT` refuses two explicit rows
   on one target. An explicit row wins over "auto" for its target.
7. A lead field that is retired, or that is a system field, is not in the map.

**The routes (blocker 5).** Each route is for an admin only (403). The flag
`CRM_FIELD_STUDIO_ORGS` must allow the org, else 404. In Mirror mode each route
gives 409, because `crm_convert_mappings` joins `core.MIRROR_SETTINGS_TABLES`.

- `GET /crm/conversion-mapping` lists each live custom lead field. For each
  target entity it gives the effective target, the reason (`auto`, `skip` or
  `explicit`) and the compatible target fields.
- `PUT /crm/conversion-mapping` writes the whole map in one transaction. It
  checks each pair with the type rule of item 5. A target that does not exist
  gives 422, and so does a retired target. An incompatible pair gives 422. A
  refused body changes no row.

**The conversion (blockers 3 and 4).**

1. The convert route applies the map only when the flag allows the org. Else
   it converts as before.
2. A new contact, company or deal gets each mapped value.
3. A mapped value wins over the default of the target field. The default
   applies only when the mapped value is empty.
4. The convert route reuses a contact that it matches by email
   (`pipeline.py:666-679`). It reuses a company that it matches by name
   (`:701-718`). It also reuses a record that the caller names. On such a
   record, the mapping fills only the empty values. It never replaces a value
   that the record holds.
5. A select or multi-select value that is not an active option of the target
   is skipped for that field.
6. A retired target field is skipped. A hidden target field is still filled.
7. A lead with no company makes no company, as before. Then the company
   mapping does nothing.
8. The conversion is a system writer. It skips "required" and fills the
   defaults, as `_with_defaults` of C4a does.
9. The response lists each skipped value in `skipped`, with the entity, the
   two keys and the reason.
10. D95 holds. In Mirror mode the convert route gives 409, as before, and the
    mapping adds no write path.

**The section (blocker 5).**

- In Native mode the `field-mapping` section shows to an admin only when the
  layout says `studio_admin`. `settingsSections.ts:47` gets that rule.
- The CRM-Q7a Field map card stays Mirror only (`PipelineSettings.tsx:301`).
- The section holds one table for each target entity: lead field, then target
  field. Each row is a `SelectButton` with Auto, Skip and the compatible
  fields. The section uses the shared primitives only, and it navigates with
  `pushView` and `replaceView` only.

**Files.** `infra/postgres/<next free>_crm_convert_mapping.sql` (new), the
four generated tenancy files, `routes/crm/convert_mapping.py` (new),
`pipeline.py`, `core.py`, `settings.py`, `lib/settingsSections.ts`,
`lib/convertMapping.ts` (new), `components/ConvertMappingCard.tsx` (new),
`PipelineSettings.tsx`, `page.tsx` and the fences below. C6a does not touch
`field_rules.py`, `mapping.py` or `FieldMapCard.tsx`, because CRM-C6c edits
them.

**Done when.**

1. An R8 test maps two custom lead fields in a Native org and converts a
   lead. The contact and the deal hold the two values. A skipped value is not
   copied. Org B does not change.
2. `PUT` refuses an incompatible pair, a target that does not exist and a
   retired target, each with 422. The stored map does not change.
3. "Auto" copies a lead field to a target field with the same key and a
   compatible type. A NULL row and no row give the same result.
4. A matched contact keeps each value that it holds, and gets each value
   that it lacks.
5. A mapped value wins over a default. An inactive option is in `skipped`
   with its reason. A hidden target is filled. A retired target is skipped.
6. Each route gives 403 for a member and 409 in Mirror mode. It gives 404
   for an org that the flag does not allow.
7. A static fence reads the migration. It requires ENABLE, FORCE and one
   policy with USING and WITH CHECK.
8. `crmC1.test.ts` shows `field-mapping` to a Native admin with
   `studio_admin`, and hides it in each other Native case. `crmC6a.test.ts`
   shows the rows and the choices of the table.

**Fences.** `tests/unit/test_crm_convert_mapping_r8.py` (new, R8,
`_DB_GATE`), `tests/unit/test_crm_convert_mapping.py` (new: the rules with no
database, and the RLS fence), `test_crm_field_studio_r8.py`,
`test_crm_mirror_readonly.py`, `test_tenant_coverage.py`, `crmC1.test.ts` and
`crmC6a.test.ts` (new).

**Verification.**

```bash
bash scripts/dev_db.sh && eval "$(bash scripts/dev_db.sh --export)"
uv run pytest tests/unit/test_crm_convert_mapping_r8.py tests/unit/test_crm_convert_mapping.py tests/unit/test_crm_field_studio_r8.py tests/unit/test_crm_mirror_readonly.py tests/unit/test_tenant_coverage.py -q
cd workbench/control_plane && npx tsc --noEmit && npx vitest run src/app/crm
```

**CRM-C6b (not in C6a).** Mirror mode: the sync stores the Zoho
`convert_mapping`, and the card shows it read only. C6b also holds the lookup
targets of Mirror mode and the `module:<ApiName>` words. C6b waits for a real
Zoho read of a Leads field.

**Built as (2026-10-14, branch `crm-c6a`).** 🔨 BUILT, not merged. Migration
261 holds `crm_convert_mappings`. The slice adds no flag: the routes and the
conversion read `CRM_FIELD_STUDIO_ORGS`, which ships empty.

- **The routes.** `routes/crm/convert_mapping.py`. `GET` gives `fields` and
  `targets`. `fields` lists each live custom lead field in layout order. Each
  field has a cell for each entity with `target_key`, `reason`, `problem` and
  `choices`. `targets` lists the custom fields of each entity, also the
  retired ones, so the screen can name a retired explicit target.
- **The PUT body.** `{"rows": [...]}`. The route stores each row as the body
  sends it, also a row that means "auto". The screen sends no row for "auto".
- **The 422 codes.** `mapping_source`, `mapping_twice`, `mapping_skip_target`,
  `mapping_target`, `mapping_target_retired`, `mapping_type` and
  `mapping_target_twice`. A unique violation gives 409 `mapping_changed`.
- **Four details that the contract left open.** The builder decided them.
  1. A retired explicit target, or one of a changed type, stays in the map.
     The read names it in `problem`, and the conversion skips it.
  2. The conversion skips a multi-select value whole when one of its options
     is not active. It does not cut the list.
  3. The conversion reuses a record that the caller names in the convert
     body, as it reuses a matched record. The map fills only its empty values.
  4. `skipped` also gives `record_has_value` for a value that a reused record
     keeps, and `invalid_value` for a value that the target check refuses.
- **In Mirror mode** each route gives 409 `mirror_mode_settings` with "The
  conversion mapping comes from Zoho. Change it in Zoho." The conversion
  reads no map in Mirror mode.
- **The screen.** `ConvertMappingCard.tsx` reads the map through
  `useCachedResource`, so a change in the Fields section wakes it. A choice
  sends the whole map and reads it again. A refusal shows at the top of the
  card.
- **Not in this slice.** C6b.

**Review round 1 (2026-10-14).** The verifier and the reviewer found one P1.
The coordinator decided each item.

1. **A broken row never blocks a save (P1).** The screen sends the whole map.
   `check_rows` now passes an explicit row that equals its stored row, also
   when its target retired or changed type. It checks only a new or a changed
   row. A new row on a retired target gives 422 with "<target> is retired.
   Set this cell to Auto or Skip, or restore the field." The cell shows its
   problem and a "Set to Auto" button.
2. **Two writes at once (P2).** The `PUT` takes `pg_advisory_xact_lock` on a
   key of the org before it reads and writes. Migration 261 adds the partial
   unique index `crm_convert_mappings_one_target` on `(organization_id,
   target_entity, target_key)` for each explicit row. 261 is not deployed, so
   the file changed in place, and it stays idempotent.
3. **The fill of a reused record (P2).** `fill_existing` reads `custom` again
   `FOR UPDATE` before it fills. A value that lands after the match stays.
4. **A blocked auto copy.** An explicit row with a `type_mismatch` target
   still claims it. The same-key auto cell that it blocks now has the problem
   `type_mismatch`, and the conversion lists the value in `skipped`.
5. **The note after a convert.** The page shows a toast "N fields were not
   copied", with each field, its entity and its reason.

The new R8 fences cover the broken row, two `PUT` writes at once and the
index. They also cover the 409 path, the stale fill, the blocked auto copy
and a conversion in the second org. The hermetic suite holds the unchanged row and
the index. The vitest file holds the broken cell and the note. A mutation
proved each fence.

### 20.10 CRM-C7: AI reach

**Gate.** AGENT-SAFE. Each new tool reads only. Each read goes through a
`/crm` route as the member, so the access rule and the tenant apply (§10.8).
No agent gets SQL. `acb_skills/history_tools.py` records that rule.

**Scope.**

1. **One implementation of the read tools.** New
   `packages/acb_skills/acb_skills/crm_tools.py`. `crm-assistant` imports its
   read tools from there, and `agent-crm` already depends on `acb-skills`.

   | Tool | Route | Notes |
   |---|---|---|
   | `crm_list_records` | `GET /crm/{entity}` | Status, owner, pipeline, date ranges and `custom.<key>` (CRM-Q8). `page_size` up to 50 |
   | `crm_get_record` | `GET /crm/{entity}/{id}` and `/crm/fields/{entity}` | Labels for each custom key and option value |
   | `crm_list_modules`, `crm_list_module_records`, `crm_get_module_record` | `GET /crm/modules...` (CRM-Q6b) | Native gets an empty list |
   | `crm_report` | `GET /crm/reports/{kind}` | `pipeline`, `funnel`, `win-loss` or `owners` |
   | `crm_list_fields` | `GET /crm/fields/{entity}` | Labels, types and options |
   | `crm_list_pipelines` | `GET /crm/pipelines` | With the effective stage probability (CRM-C3) |
   | `crm_deal_contacts` | `GET /crm/deals/{id}/contacts` | With the roles |
   | `crm_list_owners` | `GET /crm/owners` | |
   | `crm_aggregate` | `GET /crm/aggregate` (new) | Item 2 |
   | `crm_stage_dwell` | `GET /crm/aggregate/stage-dwell` (new) | Item 2 |

2. **One row cap.** A list tool takes `page` and `page_size`, and
   `page_size` is 50 at most. A tool answer holds 50 rows at most. When
   more rows exist, it ends with "More: page=N". `search_crm` and
   `get_pipeline` take the same cap of 50, in place of 25.
3. **The aggregate routes.** New `routes/crm/aggregate.py`. Any member that
   the access rule admits may call them.
   - `GET /crm/aggregate` takes `entity`, `measure` and `group_by`, plus
     each CRM-Q8 filter. `measure` is `count`, `sum_amount` or `weighted`.
     `group_by` is `status`, `owner`, `pipeline`, `source`, or a `month` of
     `created`, `updated` or `close`. Any other value gives 422. The SQL is
     fixed text with bound values. `weighted` uses the C3 effective value.
     If C3 has not merged, C7 reads `probability`, and C3 changes it.
   - The filters come from the one Q8 builder: `ExtraFilterInput` and
     `extra_filters` (`routes/crm/core.py:982` and `:994`). `aggregate.py`
     holds no second copy of the filter SQL.
   - `GET /crm/aggregate/stage-dwell` reads `crm_status_changes`. It gives
     the median and 90th percentile days in each stage. It lists the open
     deals past `max_dwell_days`, or past a `days` argument. It is the read
     half of CRM-I1, which still owns the UI.
   - **Mirror mode has no stage history.** The sync writes no row to
     `crm_status_changes`. So in Mirror mode `stage-dwell` returns an empty
     result with `reason: "mirror_no_history"`. The tool then says "Zoho
     does not send the stage history, so the CRM cannot measure time in a
     stage."
   - `routes/crm/__init__.py` imports `aggregate`, as it imports the other
     route modules.
4. **The mode, for a member.** New `GET /crm/mode` returns `{mode}` to any
   member that the access rule admits. `GET /crm/settings` stays admin only.
5. **No record-write tool in Mirror mode.** At run assembly, after
   `_apply_own_tool_scope` (`orchestrator/executor.py:1189` and its two
   siblings), a filter reads `GET /crm/mode` as the member. In Mirror mode
   it drops `create_lead`, `update_deal_status` and `convert_lead`.
   `log_activity` stays, because a Metorite-only note saves in Mirror mode
   (CRM-Z6). If the read fails, the tools stay, and the 409 relay of today
   is the fallback.
6. **Every agent can reach the CRM (owner decision, 2026-10-12).** The owner
   asked that the data is open to any agent.
   - A new family `crm` in `skill_families.py` holds the read tools.
     `_tool_injection.py` injects it as it injects `query_history`.
   - `crm` joins `DEFAULT_PROFILE` (`skill_families.py:228`). An agent with
     no `tool_scope` gets the CRM read tools, as it gets each other
     injectable tool. The orchestrator's `config.json` scope names them.
   - The gateway stays the only gate: the kill switch `CRM_ENABLED`,
     `feature:crm`, and the access rule.
   - The tool message tells the two refusals apart. A 404 says "The CRM is
     off for this organization." A 403 says "The CRM is not open to you."
   - `acb_skills/addendum.py` gets the family text, as each family has.
7. **Remove `agent-sales-assistant`.** The owner said on 2026-10-12 that it
   does not exist and is not needed. Remove each reference, and do not
   point it at another agent.
   **Item 7 exception (decision, 2026-10-13): email drafting consults
   `crm-assistant` with the member's identity.** The consult sends
   `user_email`, so the CRM tools read as that member, and a draft gets
   real CRM context. The meeting copilot gets no CRM row: its
   `call_agent` carries no member, and a CRM tool refuses such a run.
   - `apps/services/gateway/agents.json`, the registry entry. The same PR
     deletes it. `_load_dynamic_agents` copies each `agents.json` entry into
     the database on each call (`routes/agent.py:786`). So a kept entry
     brings the row back after migration 256.
   - **Migration 256** runs `DELETE FROM dynamic_agents WHERE name =
     'agent-sales-assistant'`. No foreign key points to that table.
   - `gateway/routes/agent.py:596` (a comment) and `:1135` (the `@sales`
     example).
   - `orchestrator/agents.py:67`, `:271` and `:279`. #981 deleted the
     orchestrator's `instructions.md` on 2026-10-14.
   - `gateway/routes/notes/copilot_context.py:57` and `:261`.
   - `gateway/routes/email/automation/drafting.py:1191`, `:1202` and `:1231`.
   - `src/components/AgentChat.tsx:146`, `src/app/api/agent/chat/route.ts:764`
     and `src/app/email/lib/useDraftSession.ts:63`.
   - `tests/unit/test_agent_config_runtime.py:95` and the `_AT_BUILD` entry
     at `:110`. Both held the label for WS43-G11. The removal ends that wait.
   - Seven unit tests, `assistantCheckpoint.test.ts`, `e2e/chat.spec.ts` and
     two scripts use the name as a sample. Give each another live agent
     name. `grep -rlE "sales[-_]assistant"` lists them.
   - The docs that hold WS43-G11 open get one line each, which says that
     the agent is gone and the item is moot: `work_plan.md:5399`, and
     `specs/maf_coding_engine.md:1633`, `:4318` and `:5451`.
   - Migrations 15 and 16 name it in two comments. They are history and do
     not change.
8. **WS-55 stays out.** The inbound MCP server excludes the CRM in v1.
9. **The AI cost.** A read tool calls no model. The agent's own model calls
   already go through the Router. CRM-I3 and CRM-I4 still wait for UX-Q3.

**Non-goals.** A new sales agent. The owner configures one later. A new
write tool.

**Files.**

- New: `packages/acb_skills/acb_skills/crm_tools.py`,
  `routes/crm/aggregate.py`, `infra/postgres/256_remove_sales_assistant.sql`,
  and the new tests below.
- Skills: `acb_skills/skill_families.py` (the family and `DEFAULT_PROFILE`)
  and `acb_skills/addendum.py`.
- Orchestrator: `orchestrator/_tool_injection.py`, `orchestrator/executor.py`
  and `apps/agents/agent-orchestrator/config.json`.
- Agent: `apps/agents/agent-crm/agents.py`, `config.json` (`own_tool_scope`)
  and `instructions.md`.
- Gateway: `routes/crm/__init__.py` (the router import) and
  `routes/crm/settings.py` (`GET /crm/mode`).
- The files of item 7.

**Done when.**

1. `test_crm_agent.py` shows each new tool calling its route with the member
   headers. The answer shows labels, not raw keys.
2. `test_crm_agent.py` shows that a list tool sends `page_size` 50 at most.
   It shows that an answer of 60 rows holds 50 and ends with "More: page=2".
3. `test_crm_agent.py` shows the 404 message and the 403 message apart.
4. An R8 test shows each measure and group, and a 422 for an unknown value.
   It shows the effective probability and the dwell numbers. A second org
   sees no row. A Mirror org gets an empty dwell result with its reason.
5. A test in `test_crm_aggregate.py` proves that `aggregate.py` calls
   `extra_filters`. It holds no custom-field filter SQL of its own.
6. An R8 test calls `GET /crm/mode` as two members. The member that the
   access rule admits gets the mode. The other member gets 403.
   `GET /crm/settings` still gives 403 to that member.
7. `tests/unit/test_crm_agent_mode_filter.py` shows that a Mirror member's
   run lacks the three record-write tools and keeps `log_activity`. A
   failed mode read keeps all four.
8. `test_skills_registry.py` passes with the `crm` family. A test shows an
   agent with no `tool_scope` gets the CRM read tools.
9. The new `test_no_sales_assistant.py` scans the tree with the pattern
   `\b(agent[-_])?sales[-_]assistant\b`. That matches
   `agent-sales-assistant`, `agent_sales_assistant`, `sales-assistant` and
   `sales_assistant`. It fails on a match outside `project-docs/`, `docs/`,
   `infra/postgres/` and the fence itself.
10. An R8 test applies migration 256, then calls `_load_dynamic_agents`. The
    row of `agent-sales-assistant` does not come back.
11. After the deploy, the ledger shows 256. A count of the row in
    `dynamic_agents` gives 0.

**Fences.** `tests/unit/test_crm_agent.py`, `tests/unit/test_crm_aggregate.py`
(new), `tests/unit/test_crm_aggregate_r8.py` (new, R8, `_DB_GATE`),
`tests/unit/test_crm_agent_mode_filter.py` (new),
`tests/unit/test_skills_registry.py`, `tests/unit/test_no_sales_assistant.py`
(new), `tests/unit/test_remove_sales_assistant_r8.py` (new, R8, `_DB_GATE`)
and `tests/unit/test_agent_config_runtime.py`.

**Verification.**

```bash
bash scripts/dev_db.sh && eval "$(bash scripts/dev_db.sh --export)"
uv run pytest tests/unit/test_crm_agent.py tests/unit/test_crm_agent_write.py tests/unit/test_crm_aggregate.py tests/unit/test_crm_aggregate_r8.py tests/unit/test_crm_agent_mode_filter.py tests/unit/test_no_sales_assistant.py tests/unit/test_remove_sales_assistant_r8.py tests/unit/test_agent_config_runtime.py -q
uv run pytest tests/unit/test_copilot_resume.py tests/unit/test_delegation_no_egress.py tests/unit/test_h201_readers_under_rls.py tests/unit/test_memory_authorization.py tests/unit/test_notes_copilot_context.py tests/unit/test_resolve_agent_for_run.py tests/unit/test_rooms.py -q
uv run pytest tests/unit/test_skills_registry.py tests/unit/test_generated_addendum.py tests/unit/test_skill_index.py tests/unit/test_skill_toggle_enforcement.py tests/unit/test_integrations_skills_route.py tests/unit/test_crm_kill_switch.py tests/unit/test_crm_access.py -q
uv run ruff check packages/acb_skills/acb_skills/crm_tools.py packages/acb_skills/acb_skills/skill_families.py packages/acb_skills/acb_skills/addendum.py apps/services/gateway/gateway/routes/crm/aggregate.py apps/services/gateway/gateway/routes/crm/settings.py apps/services/orchestrator/orchestrator/_tool_injection.py apps/agents/agent-crm/agents.py
uv run mypy packages/acb_skills/acb_skills/crm_tools.py apps/services/gateway/gateway/routes/crm/aggregate.py
cd workbench/control_plane && npx tsc --noEmit && npx vitest run src/lib/assistantCheckpoint.test.ts
```

**Built as (2026-10-13, branch `crm-c7`).** 🔨 BUILT, not merged. The
build follows the resolutions D1 to D11 of the dispatch.

- **The tools.** `packages/acb_skills/acb_skills/crm_tools.py` holds the 12
  read tools. An answer holds 50 rows at most, and `page_size` goes up to 50
  (D1). `search_crm` caps at 50. A 404 from the kill switch and a 403 from
  the access rule each come back as a sentence (D3). A missing record is
  still an error.
- **The family.** `crm` is `scope_governed` and in `DEFAULT_PROFILE` (D2).
  The `tool_scope` of the orchestrator and the `own_tool_scope` of
  `crm-assistant` name the 12 tools. `tool_annotations.TOOL_ANNOTATIONS`
  holds their risk, and the risk block does not list them.
- **The routes.** `routes/crm/aggregate.py`. The month groups are
  `created_month`, `updated_month` and `close_month`. In Mirror mode
  `created_month` is a 422, as the Q8 created filter is. The aggregate builds
  its filters through `records.list_query`, the one list builder (D10).
  Weighted reuses `core.WEIGHTED_SQL`, one lane at a time. Stage dwell takes
  `days`, `page` and `page_size`. In Mirror mode it gives an empty result
  with a `reason` (D9). `GET /crm/mode` is in `settings.py`.
- **The Mirror filter.** `_tool_injection._drop_mirror_record_writes` runs
  after `_apply_own_tool_scope` at the three sites. A `no_egress` run reads
  nothing, because the egress control drops the three writes there anyway.
- **The removal.** The migration is **256**, not 254. The open PR #938 holds
  254, and the CRM-C3 worktree holds 255. ⚠️ `dynamic_agents` has FORCE row
  level security in production (`generated/04_policies.sql`). So the
  migration binds each tenant in turn, as 221 does. The email draft
  consult names `crm-assistant` (the item 7 exception). The meeting
  copilot has no CRM row. WS43-G11 is moot.
- **Review round 1.** A tool refuses an identity with no `@` (a shared
  room's compartment), and it sends nothing. The export also builds its
  filters through `records.list_query`. The refusal test covers each of
  the 12 tools.
- **The fences.** `test_crm_aggregate_r8.py`, `test_crm_aggregate.py`,
  `test_crm_agent.py`, `test_crm_mirror_run_tools.py`,
  `test_skills_registry.py`, `test_no_sales_assistant.py`,
  `test_no_sales_assistant_r8.py` and `test_agent_config_runtime.py`.
- **Open.** Done-when 6 waits for the deploy: the ledger shows 256, and a
  count of the row in `dynamic_agents` gives 0.

### 20.11 What the CRM still lacks against Zoho

This list reads §6, §9, §13.7, §18.5, §19.2 and §19.3. It names what stays
unbuilt after CRM-C1 to CRM-C7. It writes no contract.

**Planned in this spec, not built.**

- **Daily work.** Activities view (CRM-X5), follow-ups in the task store
  (CRM-S1), notifications (CRM-S11) and the onboarding list (CRM-X13).
- **Pipeline.** Pipeline health and idle days (CRM-I1), and targets with
  the forecast by month (CRM-I2). Win moments (CRM-X6 and CRM-X7), a board
  you can shape (CRM-X8) and win likelihood (CRM-X14).
- **Data.** CSV import (CRM-S3), bulk edit (CRM-S4), and duplicate check and
  merge (CRM-S5 and CRM-I6). Web forms (CRM-S8), field history (CRM-S10),
  multi-currency (CRM-S12 and CRM-Q11), and tags (CRM-Q13).
- **Zoho.** Record write-back (CRM-Z9), near-real-time sync (CRM-Z8),
  per-owner visibility (CRM-Q12), and the Zoho layout on the record page
  (CRM-Q10). C4 builds the Native half of CRM-Q10.
- **Process.** Stage transition forms and cadences (§19.3). A cadence waits
  for UX-Q4.
- **AI.** Deal brief and weekly digest (CRM-I3 and CRM-I4). They wait for
  UX-Q3.

**Not in this spec at all (new).**

| Gap | What Zoho has |
|---|---|
| Validation rules | A rule on a field value that blocks a save |
| Assignment rules | Round robin, or a rule by source or region, for a new lead or deal |
| Workflow rules | On a create, an edit or a date: update a field, make a task, send a mail or call a webhook |
| Blueprints | A required process for each stage, with checks at each move |
| Email templates and mass email | Templates with merge fields, and a send to a list |
| Approval processes | A record waits for an approver before it moves |
| Territories | Records grouped by region, with their own access |
| Lead scoring | Points from fields and activity. CRM-X14 scores deals only |
| Field permissions for each role | Who sees and edits each field |
| A recycle bin | A deleted record comes back within a time limit |

**Recommended order after CRM-C1 to CRM-C7.**

1. **CRM-I1.** C3 and C7 already build its two halves.
2. **CRM-S1, then CRM-X5.** Follow-ups in the task store, then the view.
3. **CRM-I2.** The owner answered UX-Q2.
4. **CRM-Z9**, flag off. The owner answered UX-Q6.
5. **Validation rules (new).** Small, and the C4 layout is their home.
6. **CRM-S3, then CRM-S5.** Import, then duplicate check and merge.
7. **CRM-S4**, with CRM-X9 first.
8. **Assignment rules (new).** They need the owner list of CRM-X1 only.
9. **CRM-X8, then CRM-X6 and CRM-X7.**
10. **Workflow rules and email templates (new).** A field update and a task
    need no owner answer. A mail action waits for UX-Q4.
11. **CRM-S8** web forms.
12. **CRM-Q11, CRM-Q12 and CRM-Q13.**
13. **Blueprints, approvals, territories and lead scoring (new).** Last,
    because they matter most to large sales teams.
