-- ============================================================================
-- 184_rbac_user_identity_rekey_expand.sql — WS-29 H6 slice 4 (D48), EXPAND, DARK
-- ============================================================================
-- Spec: project-docs/specs/saas_multitenancy_handover.md §H6 (Slice 4 — RBAC
--       re-key, EXPAND) · saas_multitenancy.md §11 · board WS-29 · D48
--       (2026-08-22, owner-ratified).
--
-- ⚠️ **RBAC RE-KEY, EXPAND HALF. This changes NO read path.** D48 re-keys the
-- three RBAC tables — `user_role`, `user_permission_override`, `org_group_member`
-- — from `app_user.id` → `user_identity.id`, resolved in TWO phases and shipped
-- dark behind `IDENTITY_CUTOVER`. This migration is the EXPAND: it ADDS the
-- nullable `user_identity_id` column, BACKFILLS it via the `lower(email)` bridge,
-- and INDEXES it — so the later per-module READ cutovers (separate PRs, behind
-- the flag) have a populated column to move onto. A DUAL-WRITE on every RBAC
-- INSERT keeps the column current going forward (the write sites are listed in
-- §H6's Slice-4 EXPAND block). `app_user.id` stays the AUTHORITATIVE key; nothing
-- reads `user_identity_id` yet.
--
-- The bridge (mirrors migration 182's identity seed + 158's credential re-key):
--   RBAC.user_id → app_user.id → lower(app_user.email) → user_identity.id.
-- Every RBAC row HAS an `app_user` — all three FKs are `ON DELETE CASCADE` onto
-- `app_user(id)` (130:108-114, 130:120-129, 138:52-62) — so the join to
-- `app_user` never drops a row; `user_identity_id` is left NULL only where no
-- `user_identity` exists for that address (a member who predates the shadow and
-- has never signed in / been re-invited since — closed by a later backfill re-run
-- or the CONTRACT slice, never a wrong value).
--
-- ⚠️ **The FK is `ON DELETE SET NULL`, deliberately — NOT 158's `ON DELETE
-- CASCADE`.** 158 made `organization_id` authoritative in the same migration
-- (NOT NULL, re-keyed PK), so a cascade was correct there. Here `user_identity_id`
-- is a NULLABLE SHADOW: `app_user.id` is still the authoritative key (`user_id`,
-- `ON DELETE CASCADE`), so a deleted `user_identity` must NULL the shadow FK, never
-- cascade-delete an RBAC grant `app_user.id` still backs. The CONTRACT slice
-- (OWNER-GATE) re-keys this to `CASCADE` when `user_identity_id` becomes the key
-- and `user_id` is dropped.
--
-- R6 EXPAND: additive, forward-only, IDEMPOTENT. `ADD COLUMN IF NOT EXISTS` +
-- `CREATE INDEX IF NOT EXISTS` are inert on re-run; the backfill's
-- `user_identity_id IS DISTINCT FROM ui.id` guard makes a re-run touch ZERO rows.
-- Each UPDATE sets ONLY `user_identity_id`, never `user_id`/`role_id`/`permission`/
-- `group_id`, so it can never move a grant. Mirrors 182/183's idempotent shape.
--
-- ⚠️ R5: creates NO table, adds NO `gen_tenant_migration.EXEMPT` row and opens no
-- DB-connection site. It adds a nullable COLUMN + an INDEX to three EXISTING
-- RLS-forced tables and does NOT touch their RLS — ENABLE/FORCE/policies live in
-- `generated/04_policies.sql` (:611-616 / :842-847 / :849-854) and stay unchanged
-- (R6 expand does not re-key RLS; the FK targets the RLS-EXEMPT `user_identity`).
--
-- ⚠️ **The mirror-time bridge writes these RLS-FORCED tables, so it runs GUC-BOUND
-- (repair 2026-08-22, round 2).** These three tables carry `FORCE ROW LEVEL
-- SECURITY` keyed on `organization_id = current_setting('app.tenant_id', true)`.
-- The SIXTH-INSERT bridge below (`acb_auth.access._bridge_rbac_to_identity`, run
-- by `mirror_identity_membership`) UPDATEs them, so under real phase-4 RLS it MUST
-- `SET LOCAL app.tenant_id` to the human's org first — an UNBOUND UPDATE sees the
-- GUC as NULL → 0 visible rows → it silently no-ops (best-effort swallows it),
-- leaving a provisioned owner's `user_identity_id` NULL and, once
-- `IDENTITY_CUTOVER` flips the role leg, locked out. The bridge reuses the ONE GUC
-- seam `acb_common.db.tenant_session` (no second GUC path). This is the same
-- SECURITY-INVOKER-under-FORCE-RLS tension migration 179's header flagged as
-- "REVISIT AT H3", and it binds ANY H6 write path that touches an RLS-forced
-- table — not just this migration's exempt-table dual-writes. Fence:
-- `test_h6_rbac_rekey.py::TestTheRealBridgeFunctionUnderForceRls` (drives the real
-- bridge as the non-priv acb_app role; RED if the bind is removed).
--
-- Fence: `tests/unit/test_h6_rbac_rekey.py` (R8, `TENANT_LADDER_DATABASE_URL`, in
-- `pr-check.yml`'s skip guard) — proves backfill-correctness (every row's
-- `user_identity_id` == the `lower(email)`-bridged identity, NULL only where none),
-- idempotence (re-run = 0), and the dual-write (a fresh INSERT populates both
-- columns equal).
--
-- Idempotent. Depends on: 130_org_access_control.sql (user_role,
-- user_permission_override), 138_groups_and_session_participants.sql
-- (org_group_member), 159_control_plane.sql (user_identity), 09_app_user.sql
-- (app_user.email), 162_app_user_email_case.sql (lower(email) uniqueness).
-- ============================================================================

-- ── user_role — (…, user_identity_id) ───────────────────────────────────────

ALTER TABLE user_role
    ADD COLUMN IF NOT EXISTS user_identity_id UUID
        REFERENCES user_identity(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS user_role_user_identity_idx
    ON user_role (user_identity_id);

UPDATE user_role ur
   SET user_identity_id = ui.id
  FROM app_user au
  JOIN user_identity ui ON lower(ui.email) = lower(au.email)
 WHERE ur.user_id = au.id
   AND ur.user_identity_id IS DISTINCT FROM ui.id;

-- ── user_permission_override — (…, user_identity_id) ────────────────────────

ALTER TABLE user_permission_override
    ADD COLUMN IF NOT EXISTS user_identity_id UUID
        REFERENCES user_identity(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS user_permission_override_user_identity_idx
    ON user_permission_override (user_identity_id);

UPDATE user_permission_override upo
   SET user_identity_id = ui.id
  FROM app_user au
  JOIN user_identity ui ON lower(ui.email) = lower(au.email)
 WHERE upo.user_id = au.id
   AND upo.user_identity_id IS DISTINCT FROM ui.id;

-- ── org_group_member — (…, user_identity_id) ────────────────────────────────

ALTER TABLE org_group_member
    ADD COLUMN IF NOT EXISTS user_identity_id UUID
        REFERENCES user_identity(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS org_group_member_user_identity_idx
    ON org_group_member (user_identity_id);

UPDATE org_group_member ogm
   SET user_identity_id = ui.id
  FROM app_user au
  JOIN user_identity ui ON lower(ui.email) = lower(au.email)
 WHERE ogm.user_id = au.id
   AND ogm.user_identity_id IS DISTINCT FROM ui.id;
