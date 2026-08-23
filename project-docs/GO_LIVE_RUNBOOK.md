# Go-live runbook — Fractalworks + customer #2 (multi-tenant launch)

**Status (2026-08-23): isolation binding COMPLETE and DARK.** Everything needed to make the
shared-DB RLS cutover safe for the **reduced launch scope** is built, verified on real Postgres,
adversarially reviewed, and waiting in a stack of PRs (#61–69, **unmerged**). Phase 0 (deploy +
migrations 182–185) is already applied to the box. This runbook is the ordered **owner** cutover
sequence. Owner: vjvarada.

**Launch scope (owner decision 2026-08-23):** **Tasks, Calendar, Projects, User-management +
agent chat — ONLY.** Email assistant, scheduled workflows, and WhatsApp/ClickUp inbound are
**deferred and cleanly disabled** for launch (bound post-launch).

> **D45:** every step that touches the box is OWNER-GATE. An agent may drive the grant-covered
> mechanics only under a dated `ALLOW YYYY-MM-DD <gate>` line in `.claude/OWNER_GRANTS.md`. Merging
> to main + the RLS cutover + provisioning real orgs are §6 owner acts.
>
> **The cutover is RECOVERABLE, not a blind one-way jump:** `01–03` are additive; `04` (RLS) is
> reversible via `DISABLE ROW LEVEL SECURITY`; the `.env`/flag changes revert. A staged live
> attempt on 2026-08-23 was rolled back cleanly at Checkpoint A (no data loss) — that is what
> caught the background-writer gap this stack now closes.

---

## What is already true (verified, dark)
- **Phase 0 DONE (2026-08-23):** migrations 182–185 applied to the box tenant DB (effects verified);
  gateway/workbench/operator-console rebuilt + restarted on `d460eba4`; health 200.
- **H3 cutover REHEARSED on a real prod-dump (PASS):** `01→02→03→04` under the non-priv `acb_app`
  role → 140 policies / 140 forced; 0 NULL `organization_id` on every scoped table; isolation held
  (unbound read = 0 rows, bound = the org's rows).
- **Identity:** `IDENTITY_CUTOVER` (default OFF) resolves sign-in from the RLS-EXEMPT
  `user_identity ⋈ org_membership`; role leg GUC-bound; suspended members refused. RBAC has a
  populated `user_identity_id` shadow (migration 184).
- **Agent/chat engine tenant-bound behind `ACB_GRAPH_TENANT_BIND` (default OFF) — PRs #61–67:**
  the sync `tenant_session` seam on `acb_graph`; executor org-threading (`_RUN_ORG`, server-side
  from `UserContext`, R11); `chat_session` / `pending_commit` / `audit_event` writes; run-based
  sources (copilot/chat + sub-agent inheritance + batch); agent-tool reads (entity/sales retrieval,
  granted apps, skill toggles). All fail-closed, all verified real-PG under the non-priv role.
  `audit_event` is scoped with system/cron events falling back to the operator/default org (Option A).
- **Out-of-scope always-on loops defanged — PR #68:** `EMAIL_SYNC_ENABLED` + `WORKFLOW_SCHEDULER_ENABLED`
  kill-switches (default ON). A completeness sweep confirmed **all 7 gateway always-on loops** are
  accounted for (in-scope-bound, gated, or flag-off) — nothing stray breaks the cutover.
- **`mcp_servers`** agent-injection read scoped to the run's org — PR #69 (behind the flag).
- **Projects** background writers verified BOUND (lifecycle sweep refuses without org; caller binds).
- `provision_organization` binds `app.tenant_id` (migration 185) → provisioning works under RLS.
  Operator console "New customer" + manual (no-Razorpay) activation. Deployment key `gateway` carries
  `{resolve, provision, seat_admin}`.

## The PR stack — merge BOTTOM-UP before the cutover
`#61 seam` → `#62 org-threading` → `#63 chat_session` → `#64 pending_commit` → `#65 audit_event` →
`#66 run-based sources` → `#67 agent-tool reads` → `#68 defang` → `#69 mcp_servers`. (Plus `#60`, this doc.)

> ⚠️ **Stacked-PR hazard:** merge in order (#61 first). Do **NOT** `gh pr merge --delete-branch` on a
> stacked PR — deleting its base branch CLOSES the dependent PR. Retarget each next PR to `main` as
> its base lands, or merge in order **without** `--delete-branch`.

## Deferred — bound post-launch, cleanly OFF for now
- **Email assistant / email-sync pipeline** — `EMAIL_SYNC_ENABLED=false` at cutover.
- **Scheduled workflows** — `WORKFLOW_SCHEDULER_ENABLED=false` at cutover (interactive workflows still run).
- **WhatsApp / ClickUp inbound** — unconfigured; **ClickUp to be REMOVED**, replaced later by a CSV
  project-name upload.
- **`integrations.py:1492`** — the MCP-server *admin* route also reads `mcp_servers` unfiltered (a
  separate cross-tenant read). Fix before MCP management is used by tenants.
- Flag-gated jobs stay OFF: `CRM_ZOHO_SYNC`, `INGESTION_CONSUMER`, `WHATSAPP_ENRICHMENT`, `SELF_MUTATION`.

### ⚠️ CRM homonym — deferred, HARD GUARD RAIL
`crm_contacts` / `crm_deals` / `crm_activities` carry **no tenant isolation**: their `organization_id`
is a *homonym* (FKs `crm_organizations`, the customer *company*, not the tenant — `144_crm.sql`), so
the generator's `HOMONYM_BLOCKED` list correctly refuses to scope them. **GUARD RAIL:** the CRM app is
gated on `feature:crm` (ships with a **Sales Center** package, not Core); provisioning grants Core only.
**Do NOT grant `feature:crm` / a Sales Center to any tenant** until the column is renamed — the launch
customers use Tasks/Calendar/Projects, so this costs nothing today.

---

## Cutover sequence

### Step A — merge the stack + deploy · gate: `deploy`
1. Merge PRs **#61 → #69 bottom-up** to `main` (heed the stacked-PR hazard above).
2. Deploy to the box (`acb-pull` / git pull) + **rebuild+restart** gateway/workbench/operator-console.
   Verify `api.metorite.com/health` = 200. *(Migrations 182–185 already applied; the stack adds no
   numbered migrations.)*

### Step B — H3 cutover (staged, escape-hatched) · gate: OWNER · maintenance window
Optionally rehearse on a fresh prod-dump restore first (proven). On the live box:
1. **Fresh backup** (`scripts/backup_db.sh`) — the restore point.
2. **Create `acb_app`** (`NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS`) + grants per handover §H3
   (`CONNECT`/`USAGE`/`SELECT,INSERT,UPDATE,DELETE ON ALL TABLES`/`USAGE,SELECT ON ALL SEQUENCES` +
   `ALTER DEFAULT PRIVILEGES`).
3. **Apply generated `01_add_columns → 02_backfill → 03_constraints`** as owner `acb` (additive;
   lock_timeout-guarded).
4. **Set env + repoint the gateway, then restart gateway** (see the flag table below): `DATABASE_URL`→
   `acb_app`, `IDENTITY_CUTOVER=true`, `ACB_GRAPH_TENANT_BIND=true`, `EMAIL_SYNC_ENABLED=false`,
   `WORKFLOW_SCHEDULER_ENABLED=false`; confirm the flag-off jobs are off.
5. **CHECKPOINT A (pre-cliff, RLS still OFF):** health 200; gateway now connects as `acb_app`; a test
   sign-in resolves; **no background-loop errors in the logs**. If anything is wrong → revert `.env`,
   restart, diagnose. Nothing irreversible has happened yet.
6. **Apply `04_policies.sql` — THE CLIFF** (ENABLE + FORCE RLS).
7. **CHECKPOINT B (verify by EVIDENCE):** `SELECT count(*) FROM pg_policies` ≈ 140; a member signs in →
   `is_active=true` with roles; a cross-org `SELECT` returns **0 rows**; Tasks/Calendar/Projects work;
   **agent chat works** (persists `chat_session`, retrieves context). If broken → escape hatch:
   `ALTER TABLE <t> DISABLE ROW LEVEL SECURITY;` and/or revert the flags.

### Step C — bind sign-in to orgs · gate: `enforcement-flip`
- Flip **`CUSTOMER_CONSOLE_RESOLVE_ENABLED=true`** (fail-closed on any console hiccup — intended).

### Step D — external sign-in prerequisites · gate: OWNER
- **Publish the Google consent screen** (or add each customer as a Test user in the interim).
- Set **provider LLM keys** in the box `.env`.

### Step E — provision the two customers · gate: OWNER (dashboard)
- Operator console → **New customer**: Fractalworks + customer #2 (slug, name, owner_email,
  `deployment_label=gateway`). Creates org + owner + **Core** seats + trial + placement, under RLS.
- **Manual-activate** each plan + allot seats/credits. **Core / Tasks-Calendar-Projects only — NO
  Sales Center / `feature:crm`.**
- Each owner signs in via Google → resolves to their own isolated org. Done.

## Env-flag summary (set at Step B unless noted)
| flag | cutover value | why |
|---|---|---|
| `DATABASE_URL` | `acb_app` DSN | the app MUST connect non-priv, or the superuser bypasses RLS and the cutover is cosmetic |
| `IDENTITY_CUTOVER` | `true` | sign-in resolves via the exempt tables (survives RLS) |
| `ACB_GRAPH_TENANT_BIND` | `true` | binds the agent/chat engine writes + reads (PRs #61–69) |
| `EMAIL_SYNC_ENABLED` | `false` | out-of-scope loop; would error unbound under RLS |
| `WORKFLOW_SCHEDULER_ENABLED` | `false` | out-of-scope loop; ditto |
| `CUSTOMER_CONSOLE_RESOLVE_ENABLED` | `true` (Step C) | binds sign-in to orgs |
| `CRM_ZOHO_SYNC` / `INGESTION_CONSUMER` / `WHATSAPP_ENRICHMENT` / `SELF_MUTATION` | `false` (confirm) | out-of-scope; already default-off |

## Sequencing notes
- Step A (merge + deploy) precedes Step B (the cutover).
- `IDENTITY_CUTOVER` + `ACB_GRAPH_TENANT_BIND` ON and `DATABASE_URL`→`acb_app` must be in effect
  **before** `04_policies.sql` — Checkpoint A verifies this on the live app before the irreversible-ish step.
- The kill-switches (`EMAIL_SYNC_ENABLED`/`WORKFLOW_SCHEDULER_ENABLED=false`) must be set before Step B.5
  or those loops error unbound the moment `04` lands.
- Provisioning (Step E) works under RLS because of migration 185.
