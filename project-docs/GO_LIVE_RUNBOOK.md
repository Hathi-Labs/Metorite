# Go-live runbook — Fractalworks + customer #2 (multi-tenant launch)

**Status:** build side COMPLETE (main @ d460eba4, 2026-08-22 — WS-29 H6 isolation stack +
CP-8 provisioning UI, all dark). This runbook is the **ordered owner sequence** to take the
first two customers live on the shared-DB multi-tenant platform. Owner: vjvarada.

> ⚠️ **The H3 step (Phase 1) is a one-way cliff — we CANNOT roll back, only restore.**
> Rehearse Phase 1 end-to-end on a **fresh restore of the production dump** before running it
> live. The scratch rehearsal (`tests/unit/test_h3_rls_promotion_rehearsal.py`) proved the
> mechanism; do it once more against the real dump.
>
> **D45:** each phase that touches the box is OWNER-GATE. An agent may drive the grant-covered
> mechanics only under a dated `ALLOW YYYY-MM-DD <gate>` line in `.claude/OWNER_GRANTS.md`.
> The flag flips are `enforcement-flip`; the H3 promotion + provisioning against real orgs are
> §6 owner acts.

---

## What is already true (verified, dark)
- Sign-in identity resolves from the RLS-EXEMPT `user_identity ⋈ org_membership` behind
  `IDENTITY_CUTOVER` (default OFF); the role leg is GUC-bound; suspended members refused.
- RBAC has a populated `user_identity_id` shadow (migration 184 + mirror bridge, GUC-bound).
- `provision_organization` binds `app.tenant_id` (migration 185) → provisioning works under RLS.
- Tasks/calendar background jobs are tenant-bound (H4, scoped); Projects was already bound.
- Operator console has a "New customer" provision action + manual (no-Razorpay) activation.
- Deployment key `gateway` carries `{resolve, provision, seat_admin}`.

## Deferred, NOT launch-blocking (post-launch)
`access_request`→RLS-exempt (sign-in *knock* queue), the dormant broker/ingestion/reconciler
H4 jobs, the per-module RBAC read re-key (multi-org / MT-1f — 3b already resolves roles without
it), the slice-5 CONTRACT drop of the old `user_id`, and Razorpay.

---

## Phase 0 — Deploy code + migrations to the box  ·  gate: `deploy` (+ `secrets`)
The box `acb-pull.timer` git-updates `main` onto the box but does **not** run migrations or
restart services. So:
1. Apply migrations **182 → 183 → 184 → 185** to the tenant DB (identity/status/RBAC backfills +
   provision-bind). Expand/contract, additive — safe **before** RLS.
2. Rebuild + restart **acb-gateway** (auth changes + flags), **acb-workbench** (frontend),
   **acb-operator-console** (surfaces the "New customer" button).
3. Verify: services `active`; `api.metorite.com/health` = 200; the four migrations show in the
   ledger.

## Phase 1 — H3 RLS promotion  ·  gate: OWNER (maintenance window; see handover §H3.1)
1. Create the non-privileged `acb_app` role (NOSUPERUSER … NOBYPASSRLS); point gateway +
   background services at it. Migrations keep running as the owner.
2. **Flip `IDENTITY_CUTOVER=true`** (gateway env) + restart — identity now resolves via the
   exempt tables so sign-in survives RLS.
3. Apply `infra/postgres/generated/01_add_columns.sql → 02_backfill.sql → 03_constraints.sql`
   (additive; 03 takes ACCESS EXCLUSIVE per table — window it).
4. **Apply `04_policies.sql` — THE CLIFF** (ENABLE + FORCE RLS). The instant it lands, any
   unbound connection reads zero rows; every product path is now bound, so it is correct.
5. **Verify by EVIDENCE (not a green job):**
   - `SELECT count(*) FROM pg_policies;` > 0 (≈140 tables).
   - A member signs in → resolves `is_active=true` with their roles.
   - A tasks rollover processes only its org; a cross-org `SELECT` returns 0 rows.
6. Rollback for phase 4 only (if you must): `ALTER TABLE <t> DISABLE ROW LEVEL SECURITY;`.

## Phase 2 — Bind sign-in to orgs  ·  gate: `enforcement-flip` (OWNER)
- Flip **`CUSTOMER_CONSOLE_RESOLVE_ENABLED=true`** — sign-in resolves each user to their org via
  the console. (Fail-closed on any console hiccup — that is intended.)

## Phase 3 — External sign-in prerequisites  ·  gate: OWNER (external accounts)
- **Publish the Google consent screen** (Google Cloud Console) — required for external customers
  to sign in via Google (or add each customer as a Test user in the interim).
- Set **provider LLM keys** in the box `.env` (with 2+ orgs, DB-stored keys need the env
  fallback after a restart).

## Phase 4 — Provision the two customers  ·  gate: OWNER (via the dashboard)
- Operator console → **"New customer"**: provision **Fractalworks** and **customer #2**
  (fields: slug, name, owner_email, deployment_label = `gateway`). Creates org + owner + Core
  seats + trial + placement, under RLS.
- **Manual-activate** each plan (no Razorpay) + allot seats/AI credits.
- Each customer owner signs in via Google → resolves to their own isolated org. Done.

---

## Sequencing notes
- Phase 0 must precede Phase 1 (migrations before the cliff).
- `IDENTITY_CUTOVER` (Phase 1.2) must be ON **before** `04_policies.sql` (Phase 1.4) or sign-in
  bricks.
- H4 for tasks/calendar must be deployed (it is, once Phase 0 lands) before the flip, or that
  automation goes dark — the other background jobs (broker/ingestion/reconciler) remain dark
  until their H4 lands (deferred).
- Provisioning (Phase 4) works under RLS because of migration 185.
