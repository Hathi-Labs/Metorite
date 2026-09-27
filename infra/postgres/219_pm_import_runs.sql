-- 219_pm_import_runs.sql — one row per file import, and the key that makes an
-- import safe to run twice.
--
-- What: `pm_import_runs`, and a partial UNIQUE index on `pm_tasks` over the
--       import provenance in `origin`.
-- Why:  spec `project-docs/specs/project_import.md` §6.1 and §7.1 · decision
--       D80 · board WS-41 slice I-2.
-- Depends on: 146_projects.sql (pm_tasks), 161_projects_tenancy.sql
--       (organization, pm_tasks.organization_id), 211_pm_tasks_origin.sql
--       (pm_tasks.origin).
--
-- ── pm_import_runs ──────────────────────────────────────────────────────────
--
-- A run records what an admin uploaded, what the dry run found, and what the
-- admin confirmed. I-2 writes two states: `planned`, and `discarded` when a
-- newer upload replaces the organization's open run (one open run per
-- organization, spec §7.4). The writer (I-3) adds `applying`, `done` and
-- `failed`. The CHECK names them all now, so a later slice adds no migration
-- to widen it.
--
-- The uploaded FILES are not in this table. They sit on local disk under
-- `PROJECT_IMPORT_DIR/<organization_id>/<run id>/`, and `files` records each
-- one's name, size and SHA-256. A 50 MB export does not belong in a row.
--
-- ⚠️ `plan` and `mapping` hold names and emails from the customer's source
-- tool. Every read goes through `tenant_session`, and the FORCE policy below
-- is the second lock.
--
-- ── The provenance index ────────────────────────────────────────────────────
--
-- One imported task per (organization, source tool, source id). The writer
-- (I-3) inserts with ON CONFLICT DO NOTHING against it, so a second run of one
-- file, or a resume after a restart, skips what exists (§6.9). PARTIAL on
-- `origin->>'kind' = 'import'`: no task has that kind today, so the index
-- builds empty and costs nothing until the first import.
--
-- R6 EXPAND only: a new table and a new index. Nothing existing changes, so
-- old code meets this schema without noticing it.
--
-- Idempotent per infra/postgres/README.md. Pinned by
-- tests/unit/test_projects_import_routes.py
-- (test_the_migration_forces_rls_and_keys_the_import_origin) and
-- tests/live/live_ws41_import.py.

BEGIN;

CREATE TABLE IF NOT EXISTS pm_import_runs (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id UUID NOT NULL REFERENCES organization (id) ON DELETE CASCADE,
    created_by      TEXT NOT NULL,
    source          TEXT NOT NULL,
    state           TEXT NOT NULL DEFAULT 'uploaded',
    files           JSONB NOT NULL DEFAULT '[]'::jsonb,
    mapping         JSONB NOT NULL DEFAULT '{}'::jsonb,
    plan            JSONB NOT NULL DEFAULT '{}'::jsonb,
    progress        JSONB NOT NULL DEFAULT '{}'::jsonb,
    report          JSONB NOT NULL DEFAULT '{}'::jsonb,
    heartbeat_at    TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at     TIMESTAMPTZ,
    CONSTRAINT pm_import_runs_state_known CHECK (
        state IN ('uploaded', 'planned', 'applying', 'done', 'failed', 'discarded')
    ),
    CONSTRAINT pm_import_runs_source_known CHECK (source ~ '^[a-z][a-z0-9_]{0,31}$'),
    CONSTRAINT pm_import_runs_created_by_lowercased CHECK (created_by = lower(created_by))
);

CREATE INDEX IF NOT EXISTS idx_pm_import_runs_org_created
    ON pm_import_runs (organization_id, created_at DESC);

DO $rls$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_class
         WHERE oid = 'pm_import_runs'::regclass
           AND relrowsecurity
           AND relforcerowsecurity
    ) THEN
        EXECUTE 'ALTER TABLE pm_import_runs ENABLE ROW LEVEL SECURITY';
        EXECUTE 'ALTER TABLE pm_import_runs FORCE  ROW LEVEL SECURITY';
        EXECUTE 'DROP POLICY IF EXISTS pm_import_runs_tenant_isolation '
                'ON pm_import_runs';
        EXECUTE 'CREATE POLICY pm_import_runs_tenant_isolation '
                'ON pm_import_runs '
                '    USING      (organization_id = '
                '        current_setting(''app.tenant_id'', true)::uuid) '
                '    WITH CHECK (organization_id = '
                '        current_setting(''app.tenant_id'', true)::uuid)';
    END IF;
END
$rls$;

CREATE UNIQUE INDEX IF NOT EXISTS uq_pm_tasks_import_origin
    ON pm_tasks (organization_id, (origin->>'source'), (origin->>'external_id'))
    WHERE origin->>'kind' = 'import';

COMMENT ON INDEX uq_pm_tasks_import_origin IS
    'One imported task per (organization, source tool, source id). The file '
    'importer inserts ON CONFLICT DO NOTHING against it, so a re-run or a '
    'resume skips what exists. project_import.md §6.1 · D80 · WS-41.';

COMMIT;
