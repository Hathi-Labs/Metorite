-- ============================================================================
-- 313_unattended_ledger.sql — the ledger of unattended runs (WS-BRIEF AUTO-2,
-- Metorite #814).
--
-- What: two new tables.
--       `unattended_runs` holds one row for each run of one job in one slot
--       of one org. The row commits BEFORE the work, so a restart finds the
--       slot taken and does not run the job a second time.
--       `unattended_incidents` groups the failures of one job and one error
--       class of one org. One row stays open, and its count goes up.
--       gateway/jobs/unattended.py is the one writer of both tables.
-- Why:  A brief or another send that runs with nobody watching must run at
--       most once for each slot. A crash between the send and the record
--       must not send again. A run with an unknown outcome gets `unknown`
--       and no retry. Reference: docs/job_queue.md §9.
-- Depends on: 130_org_access_control.sql (organization).
--
-- Expand only (R6): two new tables and their indexes. No rename, no drop, and
-- no change to an existing table. Old code names no new object. A NOT NULL
-- with no default stands only on a column that the one writer always fills.
-- No running code writes these tables, and UNATTENDED_LEDGER is OFF.
-- Idempotent per infra/postgres/README.md: CREATE TABLE IF NOT EXISTS, CREATE
-- INDEX IF NOT EXISTS and an RLS block guarded on relforcerowsecurity.
-- Tenant-scoped (R5): `organization_id` with ENABLE and FORCE row level
-- security and one policy with USING and WITH CHECK, in the shape of 312.
-- `REFERENCES` comes before `DEFAULT`, because the tenancy ratchet in
-- tests/unit/test_tenancy_boundary.py matches the two with no comma between.
-- The org of a new row comes from the DEFAULT, the bound tenant. The writer
-- never names it.
-- The number is taken at build time (R1). On 2026-10-28 origin/main held 312,
-- and an open branch claimed 314. Check it again at merge. Pinned by
-- tests/unit/test_unattended_ledger.py, which finds this file by CONTENT,
-- never by number (R1).
-- ============================================================================

BEGIN;

CREATE TABLE IF NOT EXISTS unattended_incidents (
    organization_id  UUID NOT NULL
                     REFERENCES organization (id) ON DELETE CASCADE
                     DEFAULT current_setting('app.tenant_id', true)::uuid,
    id               UUID NOT NULL DEFAULT gen_random_uuid(),
    -- The job, for example `reach.brief`.
    job              TEXT NOT NULL,
    -- The type of the failure, for example `TimeoutError` or `send_unsure`.
    error_class      TEXT NOT NULL,
    state            TEXT NOT NULL DEFAULT 'open',
    -- The failures that this incident holds. The first failure is 1.
    count            INTEGER NOT NULL DEFAULT 1,
    first_seen       TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen        TIMESTAMPTZ NOT NULL DEFAULT now(),
    closed_at        TIMESTAMPTZ NULL,
    PRIMARY KEY (id),
    CONSTRAINT unattended_incidents_job CHECK (job ~ '^[a-z][a-z0-9_.]{0,63}$'),
    CONSTRAINT unattended_incidents_error_class CHECK (
        error_class ~ '^[A-Za-z][A-Za-z0-9_.:]{0,63}$'),
    CONSTRAINT unattended_incidents_state CHECK (state IN ('open', 'closed')),
    CONSTRAINT unattended_incidents_count CHECK (count >= 1)
);

-- One open incident for each job and error class of an org. A closed row
-- frees the pair, so the next failure opens a new incident.
CREATE UNIQUE INDEX IF NOT EXISTS unattended_incidents_open_idx
    ON unattended_incidents (organization_id, job, error_class)
    WHERE state = 'open';

CREATE TABLE IF NOT EXISTS unattended_runs (
    organization_id  UUID NOT NULL
                     REFERENCES organization (id) ON DELETE CASCADE
                     DEFAULT current_setting('app.tenant_id', true)::uuid,
    id               UUID NOT NULL DEFAULT gen_random_uuid(),
    -- The job, for example `reach.brief`.
    job              TEXT NOT NULL,
    -- The identity of one run of the job. A Reach brief uses the outbox key
    -- of the brief, so the two keys agree. The key holds no member text.
    slot             TEXT NOT NULL,
    -- The time that the slot is for, when the job has one.
    scheduled_for    TIMESTAMPTZ NULL,
    state            TEXT NOT NULL DEFAULT 'started',
    -- The process that began the run. A restart has a new value.
    runner           UUID NOT NULL,
    error_class      TEXT NULL,
    incident_id      UUID NULL REFERENCES unattended_incidents (id) ON DELETE SET NULL,
    started_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- The last progress that the run told the ledger about.
    progress_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at      TIMESTAMPTZ NULL,
    PRIMARY KEY (id),
    CONSTRAINT unattended_runs_slot_once UNIQUE (organization_id, job, slot),
    CONSTRAINT unattended_runs_job CHECK (job ~ '^[a-z][a-z0-9_.]{0,63}$'),
    CONSTRAINT unattended_runs_slot CHECK (char_length(slot) BETWEEN 1 AND 200),
    CONSTRAINT unattended_runs_state CHECK (
        state IN ('started', 'ok', 'failed', 'unknown', 'stalled')),
    CONSTRAINT unattended_runs_error_class CHECK (
        error_class IS NULL OR error_class ~ '^[A-Za-z][A-Za-z0-9_.:]{0,63}$'),
    CONSTRAINT unattended_runs_finished CHECK (
        (state = 'started') = (finished_at IS NULL))
);

-- The open runs of an org, for the stall sweep and the restart.
CREATE INDEX IF NOT EXISTS unattended_runs_started_idx
    ON unattended_runs (organization_id, progress_at) WHERE state = 'started';

DO $rls$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_class
         WHERE oid = to_regclass('unattended_incidents')
           AND relrowsecurity
           AND relforcerowsecurity
    ) THEN
        ALTER TABLE unattended_incidents ENABLE ROW LEVEL SECURITY;
        ALTER TABLE unattended_incidents FORCE  ROW LEVEL SECURITY;
        DROP POLICY IF EXISTS unattended_incidents_tenant_isolation ON unattended_incidents;
        CREATE POLICY unattended_incidents_tenant_isolation ON unattended_incidents
            USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
            WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_class
         WHERE oid = to_regclass('unattended_runs')
           AND relrowsecurity
           AND relforcerowsecurity
    ) THEN
        ALTER TABLE unattended_runs ENABLE ROW LEVEL SECURITY;
        ALTER TABLE unattended_runs FORCE  ROW LEVEL SECURITY;
        DROP POLICY IF EXISTS unattended_runs_tenant_isolation ON unattended_runs;
        CREATE POLICY unattended_runs_tenant_isolation ON unattended_runs
            USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
            WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);
    END IF;
END
$rls$;

COMMENT ON TABLE unattended_runs IS
    'The ledger of unattended runs (WS-BRIEF AUTO-2). One row for each job, '
    'slot and org, committed before the work. Only gateway/jobs/unattended.py '
    'writes it. docs/job_queue.md.';
COMMENT ON TABLE unattended_incidents IS
    'The failures of one job and one error class of an org, grouped (WS-BRIEF '
    'AUTO-2). One open row with a count. Only gateway/jobs/unattended.py '
    'writes it. docs/job_queue.md.';

COMMIT;
