-- ============================================================================
-- 183_org_membership_status_reconcile.sql — WS-29 H6 slice 3a (D48), DARK
-- ============================================================================
-- Spec: project-docs/specs/saas_multitenancy_handover.md §H6 ·
--       saas_multitenancy.md §11 · board WS-29 · D48 (2026-08-22, owner-ratified).
--
-- ⚠️ **STATUS RECONCILE. This changes NO read path.** Migration 159 seeded, and
-- 182 caught up, `org_membership.status` from `app_user.status` — but as a
-- SNAPSHOT. Slice 1's dual-write is create-only (`ON CONFLICT DO NOTHING`), so
-- every suspend / remove / reactivate landed AFTER a member's shadow row existed
-- left `org_membership.status` STALE (182's header + §H6:671-681 record this as a
-- latent P0 for the read cutover). Slice 3a's forward mirror
-- (`acb_auth.access.mirror_membership_status`, called from `members.py`'s
-- suspend/remove/reactivate and `provision_member`) keeps it CURRENT going
-- forward; THIS migration is the one-time catch-up that aligns the rows that
-- already drifted.
--
-- ⚠️ **Trustworthy for the status of EXISTING, app_user-backed rows — NOT for
-- active-orphan rows the read cutover must still handle.** This UPDATE joins
-- `app_user`, so it can only align a membership that still HAS an `app_user` row;
-- it cannot fix an `org_membership` row whose `app_user` is gone or was never
-- committed. Two such active-orphan cases remain the read-cutover's job (see §H6):
-- (a) PURGE — `members.py` `purge_member` deletes `app_user` but not the shadow,
-- leaving an active membership with no `app_user`; (b) APPROVE-ROLLBACK — the
-- provision-path mirror commits on its own session inside the caller's txn, so a
-- concurrent-approve 409 rolls back `app_user` while the shadow stays active. The
-- cutover MUST read status only for rows that still exist in `app_user` (join /
-- intersect), OR slice 5 must make the mirror atomic with the authoritative write
-- and prune on purge (D48 terminal slice). Until a read moves, all of this is dark.
--
-- ⚠️ **Reconcile FORWARD, not backward — this is the ONLY direction available.**
-- Under H3's FORCE-RLS promotion the identity leg reads `user_identity` ⋈
-- `org_membership` on the UNBOUND session while `app_user` is RLS-FORCED, so a
-- post-cutover reconcile FROM `app_user` is impossible. Status must live current
-- in the RLS-EXEMPT `org_membership.status`; this puts it there (D48).
--
-- R6 EXPAND: additive, forward-only, IDEMPOTENT. `m.status IS DISTINCT FROM
-- COALESCE(u.status, 'active')` is the whole guard — a re-run touches zero rows.
-- It is a SCOPED UPDATE of EXISTING rows: it sets ONLY `status`, never
-- `organization_id` and never `user_id`, so it can never move an identity between
-- orgs (§H6:672-674); it creates and deletes no row (existence is 159/182's job);
-- and it never touches `resolved_at` (that column is `console_resolve`'s). The
-- `COALESCE(u.status, 'active')` mirrors 159's seed exactly, so a row this
-- migration aligns and a row the forward mirror writes agree.
--
-- ⚠️ R5: `org_membership` is already in `gen_tenant_migration.EXEMPT`
-- (control-plane identity table, cross-tenant by design). This creates no table,
-- adds no exemption row and opens no DB-connection site.
--
-- Idempotent. Depends on: 159_control_plane.sql (org_membership, user_identity),
-- 09_app_user.sql / 130_org_access_control.sql (app_user.status).
-- ============================================================================

-- Align every mirrored membership's status to its authoritative `app_user` row.
-- The join key is the identity (`user_identity.id`, matched on `lower(email)`
-- against 162's functional index) AND the organization — so a human in two orgs
-- is reconciled per membership and never has one org's status leak into another.
UPDATE org_membership m
   SET status = COALESCE(u.status, 'active')
  FROM app_user u
  JOIN user_identity i ON lower(i.email) = lower(u.email)
 WHERE m.user_id = i.id
   AND m.organization_id = u.organization_id
   AND m.status IS DISTINCT FROM COALESCE(u.status, 'active');
