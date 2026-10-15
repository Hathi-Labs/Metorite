-- ============================================================================
-- 268_crm_sync_holds.sql — the modules that the mirror sync holds back.
--
-- What: one new column, `crm_settings.sync_holds`. It is a JSONB map from a
--       count key of `crm_sync_runs.counts` (`contacts`, `activities`,
--       `module:<ApiName>` …) to `{"held", "by", "count", "at"}`. A held key
--       is not read by the scheduled sync. An admin can sync it on its own.
-- Why:  WS-53 CRM-Q16, spec project-docs/specs/crm_platform.md §18.8. The
--       first import of one org read 18,674 activities with the rest and
--       strained the production database on 2026-10-15.
-- Depends on: 243_crm_settings.sql (crm_settings).
--
-- Expand only (R6): one column, NOT NULL with a constant default, so no row
-- rewrite. No rename, no drop. Old code names no new column, so it runs on
-- this schema as before.
-- Idempotent per infra/postgres/README.md: ADD COLUMN IF NOT EXISTS. A second
-- run changes nothing.
-- Tenant-scoped (R5): the column is on `crm_settings`, which already has
-- FORCE row level security with one policy (243). No new table.
-- Only gateway/crm_sync/holds.py reads or writes the column. Pinned by
-- tests/unit/test_crm_sync_holds_r8.py, which finds this file by CONTENT,
-- never by number (R1).
-- ============================================================================

BEGIN;

ALTER TABLE crm_settings
    ADD COLUMN IF NOT EXISTS sync_holds JSONB NOT NULL DEFAULT '{}'::jsonb;

COMMIT;
