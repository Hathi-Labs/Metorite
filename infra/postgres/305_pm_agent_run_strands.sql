-- ============================================================================
-- 305_pm_agent_run_strands.sql — the strands of agent runs: one fingerprint
-- for each nudge, and the recovery record that follows it.
--
-- What: one new table, `pm_agent_run_strands`.
--       * A row is one strand of one run for one reason: the sweep found the
--         run stranded (in_progress, stale, and no live path) and nudged the
--         run's member once (`nudged_at`). The partial unique index on
--         `(organization_id, run_id, reason) WHERE cleared_at IS NULL` is the
--         FINGERPRINT, so the nudge goes once, also across two gateway
--         processes.
--       * The second miss turns the same row into a RECOVERY RECORD
--         (`recovered_at`, `evidence`), owned by `member_email`. SW-11 (Needs
--         attention) reads the open ones through
--         `pm_agent_run_strands_open_recovery`.
--       * Any activity on the run after the first miss clears the row
--         (`cleared_at`, `cleared_reason`). `seen_activity_at` holds the
--         `last_activity_at` of the run at the first miss, so the test is one
--         comparison.
--       Only gateway/agent_run_sweep.py writes the table.
-- Why:  WS-55 SW-10a, spec project-docs/specs/external_agent_access.md §12.6
--       (Metorite task #341).
-- Depends on: 130_org_access_control.sql (organization), 146_projects.sql
--       (pm_tasks), 267_pm_agent_runs.sql (pm_agent_runs).
--
-- Expand only (R6): one new table and its indexes. No rename, no drop and no
-- change to a row of another table. The gateway that runs before the restart
-- never reads the table, so old code meets the new schema with no change.
-- Idempotent per infra/postgres/README.md: CREATE TABLE IF NOT EXISTS,
-- CREATE INDEX IF NOT EXISTS, and an RLS block guarded on
-- relforcerowsecurity. A second run changes nothing.
-- Tenant-scoped (R5): `organization_id` with ENABLE and FORCE row level
-- security and one policy with USING and WITH CHECK, in the shape of
-- 267_pm_agent_runs.sql. `REFERENCES` comes before `DEFAULT`, because the
-- tenancy ratchet in tests/unit/test_tenancy_boundary.py matches the two with
-- no comma between. The organization foreign key is inline, never
-- `ADD CONSTRAINT <table>_org_fk`, because generated phase 3 adds that name.
-- The organization comes from the bound session (the column default), never
-- from request input: the sweep binds one org with `tenant_session(org)`.
-- The number is taken at build time (R1). On 2026-10-25 main held 303, and
-- the open PR #1317 (branch g7-link-version-mt-baa82d19) held 304. So this
-- file takes 305. Check it again at merge.
-- Pinned by tests/unit/test_agent_run_sweep_r8.py, which finds this file by
-- CONTENT, never by number (R1).
-- ============================================================================

BEGIN;

CREATE TABLE IF NOT EXISTS pm_agent_run_strands (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id   UUID NOT NULL
                      REFERENCES organization (id) ON DELETE CASCADE
                      DEFAULT current_setting('app.tenant_id', true)::uuid,
    run_id            UUID NOT NULL REFERENCES pm_agent_runs (id) ON DELETE CASCADE,
    task_id           UUID NOT NULL REFERENCES pm_tasks (id) ON DELETE CASCADE,
    -- The run's member: the one person the nudge goes to, and the owner of
    -- the recovery record.
    member_email      TEXT NOT NULL,
    -- `answer_unread`: a member answered and no agent read it.
    -- `agent_quiet`: the agent sent nothing. `filters.STRAND_REASONS`.
    reason            TEXT NOT NULL
                      CHECK (reason IN ('agent_quiet', 'answer_unread')),
    -- The `last_activity_at` of the run at the first miss. A newer value on
    -- the run means a new activity, and the row no longer fits it.
    seen_activity_at  TIMESTAMPTZ NOT NULL,
    -- The first miss: the fingerprint, and the one bell.
    nudged_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- The second miss: the recovery record and its evidence.
    recovered_at      TIMESTAMPTZ NULL,
    evidence          JSONB NULL,
    -- The row stopped fitting its run: `new_activity` (an activity after the
    -- first miss) or `run_moved` (the run left in_progress).
    cleared_at        TIMESTAMPTZ NULL,
    cleared_reason    TEXT NULL
                      CHECK (cleared_reason IS NULL
                             OR cleared_reason IN ('new_activity', 'run_moved')),
    CHECK (recovered_at IS NULL OR evidence IS NOT NULL),
    CHECK ((cleared_at IS NULL) = (cleared_reason IS NULL))
);

-- The fingerprint: ONE open strand for each run and reason. The first miss
-- inserts with ON CONFLICT DO NOTHING, so two processes send one bell.
CREATE UNIQUE INDEX IF NOT EXISTS pm_agent_run_strands_fingerprint
    ON pm_agent_run_strands (organization_id, run_id, reason)
    WHERE cleared_at IS NULL;

-- SW-11 (Needs attention): the open recovery records of one member.
CREATE INDEX IF NOT EXISTS pm_agent_run_strands_open_recovery
    ON pm_agent_run_strands (organization_id, member_email, recovered_at DESC)
    WHERE recovered_at IS NOT NULL AND cleared_at IS NULL;

-- A delete of a run or of a task cascades here. Without these indexes each
-- such delete reads the whole table.
CREATE INDEX IF NOT EXISTS pm_agent_run_strands_run
    ON pm_agent_run_strands (run_id);
CREATE INDEX IF NOT EXISTS pm_agent_run_strands_task
    ON pm_agent_run_strands (task_id);

DO $rls$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_class
         WHERE oid = to_regclass('pm_agent_run_strands')
           AND relrowsecurity
           AND relforcerowsecurity
    ) THEN
        ALTER TABLE pm_agent_run_strands ENABLE ROW LEVEL SECURITY;
        ALTER TABLE pm_agent_run_strands FORCE  ROW LEVEL SECURITY;
        DROP POLICY IF EXISTS pm_agent_run_strands_tenant_isolation
            ON pm_agent_run_strands;
        CREATE POLICY pm_agent_run_strands_tenant_isolation ON pm_agent_run_strands
            USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
            WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);
    END IF;
END
$rls$;

COMMENT ON TABLE pm_agent_run_strands IS
    'WS-55 SW-10a: one strand of an agent run. The first miss is the '
    'fingerprint and the one nudge. The second miss makes it a recovery '
    'record. Only gateway/agent_run_sweep.py writes it. '
    'external_agent_access.md §12.6.';

COMMIT;
