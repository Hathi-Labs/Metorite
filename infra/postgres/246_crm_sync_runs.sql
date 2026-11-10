-- ============================================================================
-- 246_crm_sync_runs.sql — one row for each run of the CRM mirror sync, and the
-- columns that let a run resume.
--
-- What: `crm_sync_runs`, one row for each import, incremental or reconcile run
--       of one organization.
--         * `kind` is `import`, `incremental` or `reconcile`.
--         * `status` is `running`, `ok`, `failed` or `stopped`.
--         * `counts` holds, for each entity, how many records the run saw,
--           wrote and deleted. `estimate` holds the provider's own record
--           counts when the adapter can read them.
--         * `error` holds a short safe code. It never holds free text from
--           the provider.
--       A partial unique index allows ONE `running` row for each organization.
--       That index is the run lock, and it works across gateway workers.
--       A run with a heartbeat older than 15 minutes can be taken over.
--       The five mirror tables gain `ext_seen_at` (the last full walk that read
--       the row). `crm_sync_cursors` (145, tenant-scoped since 241) gains three
--       nullable columns: `cursor_state` (where a read stops, so it can resume),
--       `initial_done_at` (the first full read of the entity ended) and
--       `last_reconciled_at` (the last full id compare).
-- Why:  WS-53 CRM-Z4 and CRM-Z5, spec project-docs/specs/crm_platform.md
--       §5.3 and §5.4.
-- Depends on: 130_org_access_control.sql (organization),
--             145_crm_zoho_sync.sql and 241_crm_tenancy.sql (crm_sync_cursors),
--             245_integration_connections.sql (integration_connections).
--
-- Expand only (R6): one new table and eight new nullable columns. No rename,
-- no drop, no UPDATE. Old code names no new object.
-- Idempotent per infra/postgres/README.md: CREATE TABLE IF NOT EXISTS,
-- CREATE INDEX IF NOT EXISTS, ADD COLUMN IF NOT EXISTS, and an RLS block
-- guarded on relforcerowsecurity. A second run changes nothing.
-- Tenant-scoped (R5): `organization_id` with ENABLE and FORCE row level
-- security and one policy with USING and WITH CHECK, in the shape of
-- 243_crm_settings.sql. `REFERENCES` comes before `DEFAULT`, because the
-- tenancy ratchet in tests/unit/test_tenancy_boundary.py matches the two with
-- no comma between. The generated phase files carry the same table.
-- Pinned by tests/unit/test_crm_sync_engine_r8.py, which finds this file by
-- CONTENT, never by number (R1).
-- ============================================================================

BEGIN;

CREATE TABLE IF NOT EXISTS crm_sync_runs (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id  UUID NOT NULL
                     REFERENCES organization (id) ON DELETE CASCADE
                     DEFAULT current_setting('app.tenant_id', true)::uuid,
    connection_id    UUID NULL
                     REFERENCES integration_connections (id) ON DELETE SET NULL,
    kind             TEXT NOT NULL,
    status           TEXT NOT NULL DEFAULT 'running',
    started_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at      TIMESTAMPTZ NULL,
    heartbeat_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    counts           JSONB NOT NULL DEFAULT '{}',
    estimate         JSONB NULL,
    credits_used     INT NULL,
    error            TEXT NULL,
    CONSTRAINT crm_sync_runs_kind_known CHECK (
        kind IN ('import', 'incremental', 'reconcile')
    ),
    CONSTRAINT crm_sync_runs_status_known CHECK (
        status IN ('running', 'ok', 'failed', 'stopped')
    )
);

-- The run lock: one running row for each organization.
CREATE UNIQUE INDEX IF NOT EXISTS crm_sync_runs_one_running_idx
    ON crm_sync_runs (organization_id)
    WHERE status = 'running';

CREATE INDEX IF NOT EXISTS crm_sync_runs_org_started_idx
    ON crm_sync_runs (organization_id, started_at DESC);

DO $rls$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_class
         WHERE oid = 'crm_sync_runs'::regclass
           AND relrowsecurity
           AND relforcerowsecurity
    ) THEN
        EXECUTE 'ALTER TABLE crm_sync_runs ENABLE ROW LEVEL SECURITY';
        EXECUTE 'ALTER TABLE crm_sync_runs FORCE  ROW LEVEL SECURITY';
        EXECUTE 'DROP POLICY IF EXISTS crm_sync_runs_tenant_isolation '
                'ON crm_sync_runs';
        EXECUTE 'CREATE POLICY crm_sync_runs_tenant_isolation '
                'ON crm_sync_runs '
                '    USING      (organization_id = '
                '        current_setting(''app.tenant_id'', true)::uuid) '
                '    WITH CHECK (organization_id = '
                '        current_setting(''app.tenant_id'', true)::uuid)';
    END IF;
END
$rls$;

-- Where a read stops, so a stopped import resumes with no repeat. The
-- cursor of an entity moves only in the transaction that wrote its page.
-- `ext_seen_at` is the time of the last full id walk (a reconcile) that read the
-- row. A walk deletes the mirror rows that it did not read. The stamp lets a
-- walk stop and resume across runs with no list of ids in memory.
ALTER TABLE crm_companies  ADD COLUMN IF NOT EXISTS ext_seen_at TIMESTAMPTZ NULL;
ALTER TABLE crm_contacts   ADD COLUMN IF NOT EXISTS ext_seen_at TIMESTAMPTZ NULL;
ALTER TABLE crm_leads      ADD COLUMN IF NOT EXISTS ext_seen_at TIMESTAMPTZ NULL;
ALTER TABLE crm_deals      ADD COLUMN IF NOT EXISTS ext_seen_at TIMESTAMPTZ NULL;
ALTER TABLE crm_activities ADD COLUMN IF NOT EXISTS ext_seen_at TIMESTAMPTZ NULL;

ALTER TABLE crm_sync_cursors
    ADD COLUMN IF NOT EXISTS cursor_state       JSONB NULL,
    ADD COLUMN IF NOT EXISTS initial_done_at    TIMESTAMPTZ NULL,
    ADD COLUMN IF NOT EXISTS last_reconciled_at TIMESTAMPTZ NULL;

COMMENT ON TABLE crm_sync_runs IS
    'WS-53 CRM-Z4/Z5: one row for each run of the mirror sync. The partial '
    'unique index on running rows is the run lock. crm_platform.md §5.3, §5.4.';

COMMIT;
