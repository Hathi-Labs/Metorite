-- ============================================================================
-- 322_apps_format.sql — the package store of AI Studio (WS-STU A3).
--
-- What: one column, two CHECKs and one trigger on `apps`.
--       `apps.format` tells an App Workshop app (`code`) from a Studio
--       package (`manifest`). A package keeps its manifest in the column
--       `apps.manifest`, which 114 made already. So no new table and no
--       second app table (ai_studio.md §10).
--       gateway/studio/packages_store.py is the one writer of a package.
-- Why:  ai_studio.md §3.6 and the §14 A3 row. A Studio package is a row of
--       `apps`, so the publish and the versions of A4 reuse `app_versions`,
--       and an install of WS-PKG points at an app id.
-- Depends on: 114_custom_apps.sql (apps).
--
-- Expand only (R6): one nullable column with a constant default, two CHECKs
-- that each old row passes, one trigger function and one trigger. No rename,
-- no drop and no change to a row.
-- * `format` is nullable with DEFAULT 'code'. On Postgres 11 and later, a
--   constant default changes only the catalog, so the ALTER rewrites no row.
--   An old row reads as `code` with no backfill.
-- * The old code names no new object. Its two INSERTs (`routes/apps/
--   lifecycle.py`, the create and the fork) name no `format`, so each new
--   row of theirs is `code`.
-- * A reader takes NULL as `code` too: each reader that must skip a package
--   uses `format IS DISTINCT FROM 'manifest'`.
--
-- The CHECKs:
-- * `apps_format`: `format` is NULL, `code` or `manifest`.
-- * `apps_manifest_package`: a package holds a JSON object with a top-level
--   number `studio` (the schema version of A2), and the jsonb text of the
--   manifest holds at most 262144 bytes (256 KiB). The store refuses each of
--   these first. A code app passes with any manifest, so each old row passes.
--
-- The trigger `apps_format_fixed` refuses a change of `format` after the
-- INSERT, for each role, the table owner too. NULL and `code` count as one
-- value. Without it, an UPDATE could turn a code app into a package that
-- never passed the validator of A2.
--
-- Tenancy (R5): `apps` already has the tenant `organization_id` and FORCE
-- RLS with its policy (`generated/03_constraints.sql` and
-- `generated/04_policies.sql`). This file changes neither. So it adds no
-- table, no policy and no `gen_tenant_migration.EXEMPT` row.
--
-- Never wait for the lock: ALTER TABLE takes ACCESS EXCLUSIVE on `apps`, and
-- each App Workshop read needs that table. So the file sets lock_timeout
-- first, as 321 does. On a lock timeout the runner tries the whole file
-- again, and the file is idempotent.
-- Idempotent per infra/postgres/README.md: ADD COLUMN IF NOT EXISTS, a DO
-- block on pg_constraint for each CHECK (ADD CONSTRAINT has no IF NOT
-- EXISTS), CREATE OR REPLACE FUNCTION, and a DO block on pg_trigger.
--
-- `apps.slug` is UNIQUE for all orgs (114). The store adds 8 hex digits of
-- the new id to the slug, so a slug of org A never blocks a package of org
-- B, and no refusal names it. A slug key for each org is a later contract
-- step.
--
-- The number is taken at build time (R1). On 2026-11-01 origin/main held
-- 320, and the open pull request of WS-PKG PKG-4 claims 321. Check it again
-- at merge. Pinned by tests/unit/test_studio_packages_r8.py, which finds
-- this file by CONTENT, never by number (R1).
-- ============================================================================

BEGIN;

SET LOCAL lock_timeout = '5s';

-- ── 1. The column ──────────────────────────────────────────────────────────
ALTER TABLE apps ADD COLUMN IF NOT EXISTS format TEXT DEFAULT 'code';

-- ── 2. The two CHECKs ──────────────────────────────────────────────────────
DO $apps_format$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = 'apps'::regclass
           AND conname = 'apps_format'
    ) THEN
        ALTER TABLE apps ADD CONSTRAINT apps_format CHECK (
            format IS NULL OR format IN ('code', 'manifest'));
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = 'apps'::regclass
           AND conname = 'apps_manifest_package'
    ) THEN
        -- 262144 bytes (256 KiB) of jsonb text. The store holds the same number.
        -- A manifest with no `studio` key gives `jsonb_typeof(NULL)`, and a
        -- CHECK passes on NULL. So the test of `studio` is IS NOT DISTINCT
        -- FROM, which is never NULL.
        ALTER TABLE apps ADD CONSTRAINT apps_manifest_package CHECK (
            format IS DISTINCT FROM 'manifest'
            OR (jsonb_typeof(manifest) = 'object'
                AND jsonb_typeof(manifest -> 'studio') IS NOT DISTINCT FROM 'number'
                AND octet_length(manifest::text) <= 262144));
    END IF;
END
$apps_format$;

-- ── 3. The format of an app never changes ──────────────────────────────────
CREATE OR REPLACE FUNCTION apps_refuse_format_change() RETURNS trigger
LANGUAGE plpgsql AS $fn$
BEGIN
    IF COALESCE(NEW.format, 'code') IS DISTINCT FROM COALESCE(OLD.format, 'code') THEN
        RAISE EXCEPTION 'apps: the format of an app cannot change'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END
$fn$;

DO $apps_trigger$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger
         WHERE tgrelid = 'apps'::regclass
           AND tgname = 'apps_format_fixed'
           AND NOT tgisinternal
    ) THEN
        CREATE TRIGGER apps_format_fixed
            BEFORE UPDATE OF format ON apps
            FOR EACH ROW EXECUTE FUNCTION apps_refuse_format_change();
    END IF;
END
$apps_trigger$;

COMMENT ON COLUMN apps.format IS
    'code: an App Workshop app. manifest: a Studio package (WS-STU A3). NULL reads as code. '
    'A trigger refuses a change. Only gateway/studio/packages_store.py writes manifest. '
    'project-docs/specs/ai_studio.md §3.6.';

COMMIT;
