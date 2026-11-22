-- ============================================================================
-- 473_pm_project_sequences.sql — named number sequences of a project, and
-- the reservations that hand each number out once.
--
-- What: two new tables.
--       * `pm_project_sequences`: one row for each named counter of a project,
--         for example `migration`, `decision` or `handoff`. `last_value` is
--         the newest number handed out. The unique key (organization_id,
--         project_id, name) makes the reserve one atomic statement:
--         INSERT ... ON CONFLICT DO UPDATE SET last_value = last_value + 1
--         RETURNING. Two callers never get the same number.
--       * `pm_sequence_reservations`: one row for each number handed out. It
--         names the task, and the agent run when an agent reserved it. Its
--         `state` is `reserved`, `used` or `released`.
--       The owner's answer of 2026-11-22 on task 861 (prompt e6f9f76c):
--       a released number is BURNED, never handed out again. So no code
--       lowers `last_value`, and the unique key (sequence_id, value) refuses
--       a second row for one number. A reservation is `used` on the agent's
--       call or when a pull request that the task references merges. This
--       slice (SW-27a) stores the state. SW-27c sets `used` and `released`.
--       Only gateway/routes/projects/sequences.py writes these tables.
-- Why:  SW-27 "Project sequences", agent_coordination.md (Metorite task 861).
--       Parallel agents read a shared number from their own checkout, and two
--       take the same one (R1: three migration clashes in two weeks).
-- Depends on: 130_org_access_control.sql (organization), 146_projects.sql
--       (pm_projects, pm_tasks), 267_pm_agent_runs.sql (pm_agent_runs).
--
-- Expand only (R6): two new tables and their indexes. No rename, no drop and
-- no change to a row of another table. The gateway that runs before the
-- restart never reads them, so old code meets the new schema with no change.
-- Idempotent per infra/postgres/README.md: CREATE TABLE IF NOT EXISTS,
-- CREATE INDEX IF NOT EXISTS, and RLS blocks guarded on relforcerowsecurity.
-- Tenant-scoped (R5): `organization_id` with ENABLE and FORCE row level
-- security and one policy with USING and WITH CHECK on each table, in the
-- shape of 305_pm_agent_run_strands.sql. `REFERENCES` comes before `DEFAULT`
-- (the ratchet in tests/unit/test_tenancy_boundary.py). The organization
-- foreign key is inline, never `ADD CONSTRAINT <table>_org_fk`, because
-- generated phase 3 adds that name. The organization comes from the bound
-- session (the column default), never from request input.
-- No member email is stored: a reservation names its task and its run, so no
-- person's data lives here.
-- The number is taken at build time (R1). On 2026-11-22 main held 472, and the
-- open PRs #2719 and #2744 held 474 and 475. The owner named 473 on task 861.
-- Check it again at merge. Pinned by tests/unit/test_project_sequences_r8.py,
-- which finds this file by CONTENT, never by number (R1).
-- ============================================================================

BEGIN;

CREATE TABLE IF NOT EXISTS pm_project_sequences (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id   UUID NOT NULL
                      REFERENCES organization (id) ON DELETE CASCADE
                      DEFAULT current_setting('app.tenant_id', true)::uuid,
    project_id        UUID NOT NULL REFERENCES pm_projects (id) ON DELETE CASCADE,
    -- Lowercase letters, digits, `_` and `-`, 1 to 40 characters.
    name              TEXT NOT NULL
                      CHECK (name ~ '^[a-z][a-z0-9_-]{0,39}$'),
    -- The newest number handed out. It only grows (burned numbers).
    last_value        BIGINT NOT NULL DEFAULT 0 CHECK (last_value >= 0),
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- One counter for each name of a project. The reserve's ON CONFLICT target.
CREATE UNIQUE INDEX IF NOT EXISTS pm_project_sequences_name
    ON pm_project_sequences (organization_id, project_id, name);

CREATE TABLE IF NOT EXISTS pm_sequence_reservations (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id   UUID NOT NULL
                      REFERENCES organization (id) ON DELETE CASCADE
                      DEFAULT current_setting('app.tenant_id', true)::uuid,
    sequence_id       UUID NOT NULL
                      REFERENCES pm_project_sequences (id) ON DELETE CASCADE,
    value             BIGINT NOT NULL CHECK (value > 0),
    task_id           UUID NOT NULL REFERENCES pm_tasks (id) ON DELETE CASCADE,
    -- The agent run that reserved it, when an agent did. A run delete keeps
    -- the reservation, because the number stays burned.
    run_id            UUID NULL REFERENCES pm_agent_runs (id) ON DELETE SET NULL,
    state             TEXT NOT NULL DEFAULT 'reserved'
                      CHECK (state IN ('reserved', 'used', 'released')),
    reserved_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    used_at           TIMESTAMPTZ NULL,
    released_at       TIMESTAMPTZ NULL,
    CHECK ((state = 'used') = (used_at IS NOT NULL)),
    CHECK ((state = 'released') = (released_at IS NOT NULL))
);

-- One row for each number of a sequence: a number is handed out once.
CREATE UNIQUE INDEX IF NOT EXISTS pm_sequence_reservations_value
    ON pm_sequence_reservations (sequence_id, value);

-- SW-27c (Needs attention): the open reservations of an organization.
CREATE INDEX IF NOT EXISTS pm_sequence_reservations_open
    ON pm_sequence_reservations (organization_id, reserved_at)
    WHERE state = 'reserved';

-- A delete of a task or a run cascades or nulls here. Without these indexes
-- each such delete reads the whole table.
CREATE INDEX IF NOT EXISTS pm_sequence_reservations_task
    ON pm_sequence_reservations (task_id);
CREATE INDEX IF NOT EXISTS pm_sequence_reservations_run
    ON pm_sequence_reservations (run_id);

DO $rls$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_class
         WHERE oid = to_regclass('pm_project_sequences')
           AND relrowsecurity
           AND relforcerowsecurity
    ) THEN
        ALTER TABLE pm_project_sequences ENABLE ROW LEVEL SECURITY;
        ALTER TABLE pm_project_sequences FORCE  ROW LEVEL SECURITY;
        DROP POLICY IF EXISTS pm_project_sequences_tenant_isolation
            ON pm_project_sequences;
        CREATE POLICY pm_project_sequences_tenant_isolation ON pm_project_sequences
            USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
            WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_class
         WHERE oid = to_regclass('pm_sequence_reservations')
           AND relrowsecurity
           AND relforcerowsecurity
    ) THEN
        ALTER TABLE pm_sequence_reservations ENABLE ROW LEVEL SECURITY;
        ALTER TABLE pm_sequence_reservations FORCE  ROW LEVEL SECURITY;
        DROP POLICY IF EXISTS pm_sequence_reservations_tenant_isolation
            ON pm_sequence_reservations;
        CREATE POLICY pm_sequence_reservations_tenant_isolation ON pm_sequence_reservations
            USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
            WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);
    END IF;
END
$rls$;

COMMENT ON TABLE pm_project_sequences IS
    'SW-27: a named number sequence of a project. last_value only grows; a '
    'released number is burned. Only gateway/routes/projects/sequences.py '
    'writes it. agent_coordination.md, task 861.';
COMMENT ON TABLE pm_sequence_reservations IS
    'SW-27: one number handed out once, with its task and its agent run. '
    'state is reserved, used or released. Only '
    'gateway/routes/projects/sequences.py writes it. Task 861.';

COMMIT;
