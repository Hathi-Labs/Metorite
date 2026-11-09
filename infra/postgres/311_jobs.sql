-- ============================================================================
-- 311_jobs.sql — the shared durable job queue (WS-BRIEF BR-1a).
--
-- What: one new table. `jobs` holds one unit of background work of one
--       tenant: a kind, a small JSON payload, a state, a try count, a due
--       time and a lease. A caller enqueues a job in its own tenant session,
--       so the job commits with the write that asked for it. The worker of
--       gateway/jobs/worker.py takes a due job with FOR UPDATE SKIP LOCKED
--       and sets a lease in the same statement. Then it commits, runs the
--       handler with no transaction open, and settles the job in a new
--       short transaction.
-- Why:  every task of the core brief runs on one durable queue. Spec
--       project-docs/specs/job_queue.md §3 and §4.
-- Depends on: 130_org_access_control.sql (organization).
--
-- Expand only (R6): one new table and three indexes. No rename, no drop, and
-- no change to an existing table. No running code names `jobs`, so no old
-- INSERT can miss a NOT NULL column. Every NOT NULL column has a default but
-- `kind`, which the one writer (`gateway/jobs/store.py::enqueue`) always
-- fills. This is the shape of 307.
-- Idempotent per infra/postgres/README.md: CREATE TABLE IF NOT EXISTS, CREATE
-- INDEX IF NOT EXISTS and an RLS block guarded on relforcerowsecurity.
-- Tenant-scoped (R5): `organization_id` with ENABLE and FORCE row level
-- security and one policy with USING and WITH CHECK, in the shape of 307.
-- `REFERENCES` comes before `DEFAULT`, because the tenancy ratchet in
-- tests/unit/test_tenancy_boundary.py matches the two with no comma between.
-- The org of a job is this column, never a payload key: `enqueue` refuses a
-- payload with a tenant key, and the column default fills the org from the
-- bound tenant (H4).
-- No GRANT line: the default privileges give acb_app SELECT, INSERT, UPDATE
-- and DELETE on each new table, as for 307.
-- The number is taken at build time (R1). On 2026-10-28 origin/main held 309
-- as its highest file and open PR #1376 held 310, so this file takes 311.
-- Check it again at merge. Pinned by tests/unit/test_job_queue_r8.py, which
-- finds this file by CONTENT, never by number (R1).
-- ============================================================================

BEGIN;

CREATE TABLE IF NOT EXISTS jobs (
    organization_id UUID NOT NULL
                    REFERENCES organization (id) ON DELETE CASCADE
                    DEFAULT current_setting('app.tenant_id', true)::uuid,
    id              UUID NOT NULL DEFAULT gen_random_uuid(),
    -- The handler name, for example `jobs.noop`. Each consumer spec owns its
    -- kinds.
    kind            TEXT NOT NULL,
    -- Ids and small parameters only: no message text, no secret, no token.
    -- `enqueue` caps it at 8192 bytes, half of the CHECK below.
    payload         JSONB NOT NULL DEFAULT '{}'::jsonb,
    -- One job for each org, kind and key. NULL means no key.
    idempotency_key TEXT NULL,
    state           TEXT NOT NULL DEFAULT 'queued',
    -- Each claim adds 1, and a claim after a crash does too.
    tries           INTEGER NOT NULL DEFAULT 0,
    max_tries       INTEGER NOT NULL DEFAULT 5,
    -- When the job is due. A retry moves it forward.
    run_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- Set only while the job is `running`. A claim takes a running job again
    -- when its lease expired.
    lease_until     TIMESTAMPTZ NULL,
    -- The class name of the last failure and the first line of its message.
    last_error      TEXT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at      TIMESTAMPTZ NULL,
    finished_at     TIMESTAMPTZ NULL,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (id),
    CONSTRAINT jobs_kind CHECK (kind ~ '^[a-z][a-z0-9_.]{2,63}$'),
    CONSTRAINT jobs_state CHECK (
        state IN ('queued', 'running', 'succeeded', 'dead')),
    CONSTRAINT jobs_lease CHECK ((state = 'running') = (lease_until IS NOT NULL)),
    CONSTRAINT jobs_tries CHECK (tries >= 0 AND max_tries BETWEEN 1 AND 20),
    CONSTRAINT jobs_payload CHECK (
        jsonb_typeof(payload) = 'object' AND octet_length(payload::text) <= 16384),
    CONSTRAINT jobs_key CHECK (
        idempotency_key IS NULL OR char_length(idempotency_key) BETWEEN 1 AND 200),
    CONSTRAINT jobs_error CHECK (
        last_error IS NULL OR char_length(last_error) <= 500)
);

-- The claim: due jobs, and running jobs whose lease expired.
CREATE INDEX IF NOT EXISTS jobs_due_idx
    ON jobs (organization_id, run_at) WHERE state = 'queued';
CREATE INDEX IF NOT EXISTS jobs_lease_idx
    ON jobs (organization_id, lease_until) WHERE state = 'running';
-- One job for each org, kind and key.
CREATE UNIQUE INDEX IF NOT EXISTS jobs_idempotency_key
    ON jobs (organization_id, kind, idempotency_key)
    WHERE idempotency_key IS NOT NULL;

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
    'The shared durable job queue (WS-BRIEF BR-1a). One row is one unit of '
    'background work of one tenant. Only gateway/jobs/store.py writes it. '
    'Spec project-docs/specs/job_queue.md.';

COMMIT;
