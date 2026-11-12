-- ============================================================================
-- 417_apps_slug_global_key_drop.sql - drop the global slug key of an app. The
-- CONTRACT half of an expand/contract change (WS-STU A3b-2, Metorite task
-- 2958).
-- ============================================================================
-- Spec: project-docs/specs/ai_studio.md §14 row A3b-2 · R1 · R5 · R6 · R8.
-- R1: origin/main held 415 on 2026-11-12, and open PR 2137 holds 416. So this
-- file is 417. Check the number again at merge (scripts/premerge.sh).
--
-- What: drop `apps_slug_key`, the inline UNIQUE on `slug` alone from
-- migration 114. After this file, only `apps_org_slug_key` on
-- `(organization_id, slug)` (migration 414) keeps a slug unique. Two orgs may
-- hold one slug. The create of org B then answers the slug `foo` when org B
-- holds no `foo`, and the answer gives no sign of the slug of org A.
--
-- **Why it is safe now (R6).** Migration 414 shipped in the release of
-- 2026-11-11 21:30 UTC. Since then no deployed code names `ON CONFLICT (slug)`
-- on `apps`. Postgres needs a unique rule on exactly `(slug)` for that
-- clause, so the drop had to wait one release. The fence is
-- `test_no_apps_insert_names_on_conflict_slug`. No foreign key points at
-- `apps (slug)`.
--
-- **Safety net.** The file creates `apps_org_slug_key` again with IF NOT
-- EXISTS before it drops the old key, so the table never holds a window
-- with no slug rule at all.
--
-- **NULL organization_id.** Production holds no NULL row (phase 3 of the
-- tenancy ladder sets the column NOT NULL). A database that skipped the
-- phases can hold NULL rows, and a unique index treats each NULL as
-- distinct. That is the same as for any tenant table, and the app role never
-- inserts a NULL.
--
-- Tenancy (R5): no table, no policy, no `gen_tenant_migration.EXEMPT` row.
-- The readers of an app by slug also bind the org by an explicit filter
-- (`load_app`, the roster read, the agent tool read).
--
-- Never wait for the lock: ALTER TABLE takes an ACCESS EXCLUSIVE lock on
-- `apps`, and each App Workshop request needs that table. So the file sets
-- lock_timeout first, as 414 does. On a lock timeout the runner tries the
-- whole file again, and the file is idempotent.
-- Depends on: 114_custom_apps.sql (apps_slug_key), 414_apps_slug_per_org.sql
--             (apps_org_slug_key).
-- Idempotent: CREATE UNIQUE INDEX IF NOT EXISTS, DROP ... IF EXISTS.
-- Pinned by tests/unit/test_apps_slug_per_org_r8.py, which finds this file by
-- CONTENT, never by number (R1).
-- ============================================================================

BEGIN;

SET LOCAL lock_timeout = '5s';

-- ── 1. The org key must exist before the global key goes ────────────────────
CREATE UNIQUE INDEX IF NOT EXISTS apps_org_slug_key
    ON apps (organization_id, slug);

-- ── 2. Drop the global key ──────────────────────────────────────────────────
--
-- Migration 114 declared it inline (a constraint). The second statement covers
-- a database where it exists as a bare index.
ALTER TABLE apps DROP CONSTRAINT IF EXISTS apps_slug_key;
DROP INDEX IF EXISTS apps_slug_key;

COMMENT ON INDEX apps_org_slug_key IS
    'WS-STU A3b: one slug per organization for an app. This is the only '
    'slug key since A3b-2 dropped apps_slug_key. '
    'project-docs/specs/ai_studio.md §14.';

COMMIT;
