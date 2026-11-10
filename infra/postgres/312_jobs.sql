-- ============================================================================
-- 312_jobs.sql — the shared durable job queue (WS-BRIEF BR-1a, Metorite #582).
--
-- What: one new table. `jobs` holds one unit of background work of one org:
--       a kind, a JSON payload, a state, the attempts, the next run time and
--       the lease of the worker that runs it. gateway/jobs/queue.py is the
--       one writer. gateway/jobs/worker.py claims, runs and settles a row.
-- Why:  The gateway has five loops that each claim rows with FOR UPDATE SKIP
--       LOCKED. BR-1a gives them one queue. BR-1b moves them onto it. This
--       slice adds no producer. Reference: docs/job_queue.md.
-- Depends on: 130_org_access_control.sql (organization).
--
-- Expand only (R6): one new table and three indexes. No rename, no drop, and
-- no change to an existing table. Old code names no new object. A NOT NULL
-- with no default stands only on a column that the one writer always fills
-- (`kind`). No running code writes this table, so no old INSERT can miss it.
-- Idempotent per infra/postgres/README.md: CREATE TABLE IF NOT EXISTS, CREATE
-- INDEX IF NOT EXISTS and an RLS block guarded on relforcerowsecurity.
-- Tenant-scoped (R5): `organization_id` with ENABLE and FORCE row level
-- security and one policy with USING and WITH CHECK, in the shape of 307.
-- `REFERENCES` comes before `DEFAULT`, because the tenancy ratchet in
-- tests/unit/test_tenancy_boundary.py matches the two with no comma between.
-- The org of a new row comes from the DEFAULT, the bound tenant. The writer
-- never names it.
-- The number is taken at build time (R1). On 2026-10-28 origin/main held 311,
-- and an open branch claimed 310. Check it again at merge. Pinned by
-- tests/unit/test_job_queue.py, which finds this file by CONTENT, never by
-- number (R1).
-- ============================================================================

BEGIN;

CREATE TABLE IF NOT EXISTS jobs (
    organization_id  UUID NOT NULL
                     REFERENCES organization (id) ON DELETE CASCADE
                     DEFAULT current_setting('app.tenant_id', true)::uuid,
    id               UUID NOT NULL DEFAULT gen_random_uuid(),
    -- The handler that runs the job, for example `email.send_later`.
    kind             TEXT NOT NULL,
    -- A JSON object. The writer refuses NaN, Infinity and a large payload.
    payload          JSONB NOT NULL DEFAULT '{}'::jsonb,
    state            TEXT NOT NULL DEFAULT 'queued',
    -- The claims so far. A claim adds 1, also the claim after a lost lease.
    attempts         INTEGER NOT NULL DEFAULT 0,
    max_attempts     INTEGER NOT NULL DEFAULT 5,
    -- The earliest time of the next claim. A failure moves it by the backoff.
    run_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- The lease. `locked_by` is the claim token of one worker and one claim.
    -- A `running` row whose `lease_until` has passed goes to the next claim.
    locked_by        UUID NULL,
    locked_at        TIMESTAMPTZ NULL,
    lease_until      TIMESTAMPTZ NULL,
    last_error       TEXT NULL,
    -- One live job for each key, kind and org. A `done` or `failed` row
    -- frees the key (the partial unique index below).
    dedupe_key       TEXT NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at      TIMESTAMPTZ NULL,
    PRIMARY KEY (id),
    CONSTRAINT jobs_kind CHECK (kind ~ '^[a-z][a-z0-9_.]{0,63}$'),
    CONSTRAINT jobs_payload_object CHECK (jsonb_typeof(payload) = 'object'),
    CONSTRAINT jobs_state CHECK (state IN ('queued', 'running', 'done', 'failed')),
    CONSTRAINT jobs_attempts CHECK (
        max_attempts BETWEEN 1 AND 25 AND attempts BETWEEN 0 AND max_attempts),
    CONSTRAINT jobs_lease CHECK (
        state <> 'running'
        OR (locked_by IS NOT NULL AND lease_until IS NOT NULL)),
    CONSTRAINT jobs_last_error CHECK (
        last_error IS NULL OR char_length(last_error) <= 2000),
    CONSTRAINT jobs_dedupe_key CHECK (
        dedupe_key IS NULL OR char_length(dedupe_key) BETWEEN 1 AND 200)
);

-- The claim reads the queued rows that are due, oldest first.
CREATE INDEX IF NOT EXISTS jobs_due_idx
    ON jobs (organization_id, run_at, id) WHERE state = 'queued';
-- The claim also takes a running row whose lease has passed.
CREATE INDEX IF NOT EXISTS jobs_lease_idx
    ON jobs (organization_id, lease_until) WHERE state = 'running';
-- One live job for each dedupe key of a kind of an org.
CREATE UNIQUE INDEX IF NOT EXISTS jobs_dedupe_live_idx
    ON jobs (organization_id, kind, dedupe_key)
    WHERE dedupe_key IS NOT NULL AND state IN ('queued', 'running');

DO $rls$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_class
         WHERE oid = to_regclass('jobs')
           AND relrowsecurity
           AND relforcerowsecurity
    ) THEN
        ALTER TABLE jobs ENABLE ROW LEVEL SECURITY;
        ALTER TABLE jobs FORCE  ROW LEVEL SECURITY;
        DROP POLICY IF EXISTS jobs_tenant_isolation ON jobs;
        CREATE POLICY jobs_tenant_isolation ON jobs
            USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
            WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);
    END IF;
END
$rls$;

COMMENT ON TABLE jobs IS
    'The shared durable job queue (WS-BRIEF BR-1a). Only gateway/jobs/queue.py '
    'writes it. A worker claims a row with FOR UPDATE SKIP LOCKED inside '
    'tenant_session(org). docs/job_queue.md.';

COMMIT;
