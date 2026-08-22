-- ============================================================================
-- 182_identity_membership_catchup.sql — WS-29 H6 slice 1 (MT-1a-2), DARK
-- ============================================================================
-- Spec: project-docs/specs/saas_multitenancy_handover.md §H6 ·
--       saas_multitenancy.md §11 / §1.5 · board WS-29 · D15.
--
-- ⚠️ **CATCH-UP BACKFILL. This changes NO read path.** Migration 159 seeded
-- `user_identity` + `org_membership` from `app_user` ONCE, at the moment those
-- tables were created. Nothing has kept them current since except
-- `console_resolve`'s Console-gated projection, so every invite / bootstrap /
-- provision that landed an `app_user` row AFTER 159 is MISSING from the shadow
-- tables. H6's identity cutover cannot move a single read onto them until they
-- are COMPLETE; this closes that gap by re-running 159's idempotent seed logic,
-- so every `app_user` member with a tenant now has a matching identity row and a
-- membership row.
--
-- The DUAL-WRITE on the two `app_user` write paths (WS-29 H6 slice 1,
-- `acb_auth.access.mirror_identity_membership`) keeps membership EXISTENCE
-- current going forward; this migration is the one-time catch-up for the gap
-- 159→now. Together they make the shadow tables trustworthy for WHO belongs to
-- WHICH org, WITHOUT moving any read.
--
-- ⚠️ **STATUS IS A SNAPSHOT, NOT KEPT CURRENT — a hard input for the H6 read
-- cutover.** This backfill copies `app_user.status` at backfill time, and the
-- dual-write is create-only (`ON CONFLICT DO NOTHING`). Neither the dual-write
-- nor this migration propagates a LATER status change: `members.py`'s
-- suspend / remove / reactivate mutate `app_user.status` and never touch the
-- shadow, so `org_membership.status` drifts stale. This is harmless while dark
-- (no tenant-plane reader consumes `org_membership.status` — `console_resolve`
-- filters on `resolved_at IS NOT NULL` only), but the read-cutover slice MUST
-- either (a) add status mirroring to the suspend/remove/reactivate paths first,
-- or (b) reconcile status against `app_user` — otherwise a suspended/removed
-- member would be admitted from a stale shadow row (a latent P0 for the cutover).
--
-- R6 EXPAND: additive, forward-only, idempotent. Every INSERT is guarded by an
-- `ON CONFLICT … DO NOTHING`, so a re-run is a zero-net-change no-op and NO
-- existing row is ever rewritten — an identity is never moved between orgs
-- (§H6:672-674). This is 159's seed VERBATIM in shape, deliberately, so a row
-- this migration creates and a row the dual-write creates are the same row.
--
-- ⚠️ R5: `user_identity` and `org_membership` are already in
-- `gen_tenant_migration.EXEMPT` (control-plane identity tables, cross-tenant by
-- design). This creates no table, adds no exemption row and opens no
-- DB-connection site. Match the ACTUAL tenant-plane schema: 159 (user_identity
-- :67-79, org_membership :83-104, no `role` column) + 177 (`org_membership.
-- resolved_at`, left NULL here — a catch-up is NOT a registry resolution).
--
-- Idempotent. Depends on: 159_control_plane.sql (user_identity, org_membership).
-- ============================================================================

-- ── Identity: one row per human, backfilled from every app_user address ─────
-- 159's seed VERBATIM. `DISTINCT ON (lower(email))` keeps it one row per human
-- even if a case-variant pair slipped into app_user before 162's functional
-- index; `ON CONFLICT DO NOTHING` on the `lower(email)` unique index makes the
-- re-run inert.
INSERT INTO user_identity (email, display_name)
SELECT DISTINCT ON (lower(u.email)) u.email, COALESCE(u.display_name, '')
  FROM app_user u
 WHERE u.email IS NOT NULL AND u.email <> ''
 ORDER BY lower(u.email), u.created_at ASC
ON CONFLICT DO NOTHING;

-- ── Membership: the tenant-scoped half, backfilled from app_user ────────────
-- 159's seed VERBATIM. `ON CONFLICT (organization_id, user_id) DO NOTHING` is
-- the create-only guard: a member already mirrored is left EXACTLY as it was,
-- and a human who belongs to a second org gets a SECOND row (never a rewrite of
-- the first). No `role` column exists on this table — do not add one.
INSERT INTO org_membership (organization_id, user_id, status, joined_at, last_active_at)
SELECT u.organization_id,
       i.id,
       COALESCE(u.status, 'active'),
       COALESCE(u.joined_at, u.created_at),
       u.last_active_at
  FROM app_user u
  JOIN user_identity i ON lower(i.email) = lower(u.email)
 WHERE u.organization_id IS NOT NULL
ON CONFLICT (organization_id, user_id) DO NOTHING;
