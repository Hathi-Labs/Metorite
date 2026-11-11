-- ============================================================================
-- 414_apps_slug_per_org.sql - one slug per organization for an app. The
-- EXPAND half of an expand/contract change (WS-STU A3b-1, Metorite task 1310).
-- ============================================================================
-- Spec: project-docs/specs/ai_studio.md §14 rows A3b-1 and A3b-2 · R1 · R5 ·
-- R6 · R8.
-- R1: origin/main held 404 as its highest file on 2026-11-11. Open branches
-- hold 405, 406, 408, 411 and 413. So this file is 414. Check the number
-- again at merge (scripts/premerge.sh).
--
-- What: (1) `apps.organization_id` on the NUMBERED ladder, only where it is
--       missing. (2) the unique index `apps_org_slug_key` on
--       `(organization_id, slug)`.
--
-- **The fault.** Migration 114 made `slug` UNIQUE over all orgs
-- (`apps_slug_key`). FORCE RLS shows a member only the rows of the own org.
-- So when org A holds the slug `foo`, the create of org B meets the row of
-- org A on the key and takes `foo-2`. That tells org B that some org holds
-- `foo`. Each organization must own its own slugs.
--
-- **Expand now, contract one release later (R6).** The deploy applies
-- migrations BEFORE it restarts the services. So the OLD gateway runs against
-- this schema for a short time. The old code names `ON CONFLICT (slug)`, and
-- Postgres needs a unique rule on exactly `(slug)` for that clause. This file
-- therefore keeps `apps_slug_key` and only adds the new index. Old code works
-- on the new schema.
--
--   Release N (this file): both keys exist. The new code writes a bare
--     `ON CONFLICT DO NOTHING`, which works with either key. The folder of a
--     new app is `apps_root()/<app id>`, never `apps_root()/<slug>`.
--   Release N+1 (A3b-2, a later numbered file): drop `apps_slug_key`. Only
--     then can org B take a slug that org A holds. It must NOT ship with this
--     file. If it did, the old code's `ON CONFLICT (slug)` would fail on each
--     create until the restart, because no unique rule on `(slug)` would
--     remain.
--
-- **NULL organization_id.** Production carries the generated phases 01 to 04.
-- Phase 3 sets `apps.organization_id` NOT NULL, so production holds no NULL
-- row. A database that never ran the tenancy phases can hold NULL rows. A
-- unique index treats each NULL as distinct, so it does not constrain those
-- rows. Until the contract, `apps_slug_key` still keeps each slug once.
--
-- **No duplicate can block the build.** While `slug` alone is unique, each
-- slug occurs once in the table. So no two rows can share
-- `(organization_id, slug)`, and the CREATE cannot fail on data.
--
-- **Two starting points, one file** (the same as migrations 223 and 354).
--   * Production: the column exists, NOT NULL, with `apps_org_fk`.
--     `ADD COLUMN IF NOT EXISTS` then skips the whole column clause, the
--     inline REFERENCES too, so no second foreign key appears.
--   * A fresh ladder database: no tenancy phase ran (the ladder never replays
--     `generated/`). The column is created here, with the cascade that
--     `test_org_purge_tenant` requires, so the index has a column.
--
-- Tenancy (R5): `apps` already has the tenant key and FORCE RLS with its
-- policy. This file adds no table, no policy and no
-- `gen_tenant_migration.EXEMPT` row.
--
-- Never wait for the lock: CREATE INDEX takes a SHARE lock on `apps`, and
-- each App Workshop write needs that table. So the file sets lock_timeout
-- first, as 322 does. On a lock timeout the runner tries the whole file
-- again, and the file is idempotent.
-- Depends on: 114_custom_apps.sql (apps, apps_slug_key),
--             130_org_access_control.sql (organization).
-- Idempotent: ADD COLUMN IF NOT EXISTS, CREATE UNIQUE INDEX IF NOT EXISTS.
-- No CONCURRENTLY: the table holds one row per app, and a failed concurrent
-- build leaves an INVALID index behind (see migration 223).
-- Pinned by tests/unit/test_apps_slug_per_org_r8.py, which finds this file by
-- CONTENT, never by number (R1).
-- ============================================================================

BEGIN;

SET LOCAL lock_timeout = '5s';

-- ── 1. The column, only where the tenancy phases did not add it ─────────────
--
-- Same type and default as `generated/01_add_columns.sql`, plus the cascade
-- that the purge path needs. `REFERENCES` precedes `DEFAULT`, as in 223.
ALTER TABLE apps
    ADD COLUMN IF NOT EXISTS organization_id UUID
    REFERENCES organization (id) ON DELETE CASCADE
    DEFAULT current_setting('app.tenant_id', true)::uuid;

-- ── 2. One slug per organization ────────────────────────────────────────────
--
-- apps_slug_key stays. The contract release (A3b-2) drops it.
CREATE UNIQUE INDEX IF NOT EXISTS apps_org_slug_key
    ON apps (organization_id, slug);

COMMENT ON INDEX apps_org_slug_key IS
    'WS-STU A3b-1: one slug per organization for an app. apps_slug_key on '
    'slug alone stays until the contract release A3b-2 drops it. '
    'project-docs/specs/ai_studio.md §14.';

COMMIT;
