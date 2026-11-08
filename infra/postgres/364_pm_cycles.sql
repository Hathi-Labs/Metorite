-- ============================================================================
-- 364_pm_cycles.sql — the cycle store of Projects (WS-39 SP-1, Metorite task
-- 960, D195).
--
-- What: four new tables and one wider CHECK.
--       * `pm_cycles` holds one cycle (a sprint) of one project node: a
--         space, a folder or a project. It has a name, a goal, two dates, an
--         optional cooldown, a state (`planned`, `active` or `closed`), an
--         optional credit budget, and the totals of the start.
--       * `pm_cycle_tasks` holds the membership: one row for each task in a
--         cycle. UNIQUE (task_id) keeps a task in one cycle at most.
--       * `pm_cycle_committed` holds the start snapshot: one row for each
--         task that the cycle held at its start, with its estimate then.
--       * `pm_cycle_capacity` holds the capacity snapshot of the start: the
--         review minutes of each named person.
--       * The CHECK `pm_activities_type_check` takes `cycle_transfer`, the
--         logged move of one unfinished task at a close.
--       gateway/routes/projects/cycles.py is the one writer of the four
--       tables, and `core.record_activity` writes the `cycle_transfer` row.
-- Why:  D195 reverses the sprint non-goal of Projects.
--       project-docs/specs/project_management_app.md §13.2 and §13.2a hold
--       the calls and the data model.
-- Depends on: 130_org_access_control.sql (organization), 146 (pm_projects,
--       pm_tasks, pm_activities) and 210 (the last CHECK of
--       pm_activities.type).
--
-- Expand only (R6): four new tables, their keys and indexes, four trigger
-- functions and their triggers, and one CHECK that accepts one more value.
-- No rename, no drop of a table or a column, and no new column on an old
-- table. Old code names no new object. Each type that old code writes still
-- passes the wider CHECK.
-- The CHECK is added NOT VALID and then validated, as 359 does. The ALTER
-- TABLE takes the ACCESS EXCLUSIVE lock of `pm_activities`, and the file
-- holds it until COMMIT. VALIDATE reads each row of `pm_activities`, so the
-- time of the scan follows the row count, and writes to the table wait for
-- it. On 2026-11-08 production held 10,769 rows, so the scan is short. The
-- file sets lock_timeout first, and the runner tries the whole file again on
-- a lock timeout.
-- Idempotent per infra/postgres/README.md: CREATE TABLE IF NOT EXISTS, CREATE
-- INDEX IF NOT EXISTS, CREATE OR REPLACE FUNCTION, DROP TRIGGER IF EXISTS then
-- CREATE, DROP CONSTRAINT IF EXISTS then ADD, and an RLS block guarded on
-- relforcerowsecurity.
--
-- Tenant-scoped (R5): each table has `organization_id`, ENABLE and FORCE row
-- level security, and one policy with USING and WITH CHECK, in the shape of
-- 315 and 339. `REFERENCES` comes before `DEFAULT`, because the tenancy
-- ratchet in tests/unit/test_tenancy_boundary.py matches the two with no comma
-- between. The org of a new row comes from the DEFAULT, the bound tenant. The
-- writer never names it.
-- Same org. A child of `pm_cycles` points at its cycle by
-- `(organization_id, cycle_id)`, so it cannot point at a cycle of another
-- org. A foreign key check skips RLS, and the pair closes that gap.
-- `pm_projects` and `pm_tasks` have no UNIQUE (organization_id, id), and this
-- file adds none (an index build on two hot tables). So the triggers
-- `pm_cycles_same_org` and `pm_cycle_tasks_same_org` refuse a node or a task
-- of another org, for each role, as 339 does. Under RLS the row of another
-- org is not there, so the check fails closed.
--
-- One active cycle for each node: the partial unique index
-- `uq_pm_cycles_one_active`. The start route answers 409 first. The index
-- holds under a race and under a direct INSERT.
-- One cycle for each task: UNIQUE (task_id) on `pm_cycle_tasks`.
--
-- ON DELETE.
--   * `pm_cycles.project_id` CASCADE: a deleted node takes its cycles.
--   * `pm_cycle_tasks.task_id` CASCADE: a deleted task leaves its cycle. A
--     RESTRICT would make each task delete fail while the task sits in a
--     cycle, and no delete path knows about cycles. An archive keeps the
--     `pm_tasks` row, so the membership stays and the cycle still counts it.
--   * `pm_cycle_committed.task_id` has NO foreign key. The row is the record
--     of the start, so a later delete of the task does not change the start
--     total. The reader counts a task that is not there as hidden.
--   * Each child CASCADEs from `pm_cycles`, and each table from
--     `organization`.
--
-- The snapshot does not change. The trigger `pm_cycles_guard` refuses, for
-- each role: a move of a cycle to another node or org, any change of a closed
-- cycle, a step back to `planned`, a close with no start, and a change of the
-- start columns after the start. The trigger `pm_cycle_snapshot_immutable`
-- refuses each UPDATE of `pm_cycle_committed` and `pm_cycle_capacity`. A
-- DELETE stays legal, so each cascade works.
--
-- Version. A cycle has no version column. `updated_at` is the If-Match token,
-- as for `pm_tasks` (core.require_precondition, D-PM-20).
-- No score for any person: no column or index ranks a member.
-- Retention: no pruning. This file deletes nothing.
--
-- The number is taken at build time (R1). It was 361 at build. On 2026-11-08
-- origin/main held 359, and branches held 360 to 363, 366 and 367, so it
-- became 364. Check it again at merge.
-- Pinned by tests/unit/test_sprint_store_r8.py, which finds this file by
-- CONTENT, never by number (R1).
-- ============================================================================

BEGIN;

SET LOCAL lock_timeout = '5s';

-- ── The cycle ───────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS pm_cycles (
    id                       UUID NOT NULL DEFAULT gen_random_uuid(),
    organization_id          UUID NOT NULL
                             REFERENCES organization (id) ON DELETE CASCADE
                             DEFAULT current_setting('app.tenant_id', true)::uuid,
    -- The node of the cycle: a space, a folder or a project.
    project_id               UUID NOT NULL REFERENCES pm_projects (id) ON DELETE CASCADE,
    name                     TEXT NOT NULL,
    -- One sentence. Required from the start on.
    goal                     TEXT NULL,
    starts_on                DATE NOT NULL,
    ends_on                  DATE NOT NULL,
    cooldown_days            INTEGER NULL,
    state                    TEXT NOT NULL DEFAULT 'planned',
    -- AI credits, as `usage_event` holds them.
    credit_budget            NUMERIC(14, 4) NULL,
    -- The totals of the start snapshot. NULL while planned.
    committed_task_count     INTEGER NULL,
    committed_estimate_mins  INTEGER NULL,
    started_at               TIMESTAMPTZ NULL,
    started_by               TEXT NULL,
    closed_at                TIMESTAMPTZ NULL,
    closed_by                TEXT NULL,
    created_by               TEXT NOT NULL,
    created_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (id),
    -- The target of a child's foreign key, so a child stays in its org.
    CONSTRAINT pm_cycles_org_id_key UNIQUE (organization_id, id),
    CONSTRAINT pm_cycles_state CHECK (state IN ('planned', 'active', 'closed')),
    CONSTRAINT pm_cycles_name CHECK (
        btrim(name) <> '' AND char_length(name) <= 120),
    CONSTRAINT pm_cycles_goal_length CHECK (
        goal IS NULL OR char_length(goal) <= 500),
    -- A cycle that started has a goal.
    CONSTRAINT pm_cycles_goal_at_start CHECK (
        state = 'planned' OR (goal IS NOT NULL AND btrim(goal) <> '')),
    CONSTRAINT pm_cycles_dates CHECK (ends_on >= starts_on),
    CONSTRAINT pm_cycles_cooldown CHECK (
        cooldown_days IS NULL OR cooldown_days BETWEEN 0 AND 30),
    CONSTRAINT pm_cycles_budget CHECK (credit_budget IS NULL OR credit_budget >= 0),
    -- The start is whole: a started cycle has each start column, and a
    -- planned cycle has none.
    CONSTRAINT pm_cycles_start_whole CHECK (
        (state = 'planned') = (started_at IS NULL)
        AND (started_at IS NULL) = (started_by IS NULL)
        AND (started_at IS NULL) = (committed_task_count IS NULL)
        AND (started_at IS NULL) = (committed_estimate_mins IS NULL)),
    CONSTRAINT pm_cycles_committed_counts CHECK (
        (committed_task_count IS NULL OR committed_task_count >= 0)
        AND (committed_estimate_mins IS NULL OR committed_estimate_mins >= 0)),
    -- The close is whole: a closed cycle has each close column.
    CONSTRAINT pm_cycles_close_whole CHECK (
        (state = 'closed') = (closed_at IS NOT NULL)
        AND (closed_at IS NULL) = (closed_by IS NULL))
);

-- One active cycle for each node (§13.2). A planned cycle and a closed cycle
-- are not limited.
CREATE UNIQUE INDEX IF NOT EXISTS uq_pm_cycles_one_active
    ON pm_cycles (project_id)
    WHERE state = 'active';

-- The cycles of one node, newest first. It also serves the cascade of a node.
CREATE INDEX IF NOT EXISTS idx_pm_cycles_project
    ON pm_cycles (project_id, starts_on DESC);

-- ── The membership ──────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS pm_cycle_tasks (
    id               UUID NOT NULL DEFAULT gen_random_uuid(),
    organization_id  UUID NOT NULL
                     REFERENCES organization (id) ON DELETE CASCADE
                     DEFAULT current_setting('app.tenant_id', true)::uuid,
    cycle_id         UUID NOT NULL,
    task_id          UUID NOT NULL REFERENCES pm_tasks (id) ON DELETE CASCADE,
    added_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- The member who added the task, from the session.
    added_by         TEXT NOT NULL,
    PRIMARY KEY (id),
    -- A task sits in one cycle at most (§13.2).
    CONSTRAINT pm_cycle_tasks_one_cycle UNIQUE (task_id),
    CONSTRAINT pm_cycle_tasks_cycle_fk FOREIGN KEY (organization_id, cycle_id)
        REFERENCES pm_cycles (organization_id, id) ON DELETE CASCADE,
    CONSTRAINT pm_cycle_tasks_added_by CHECK (btrim(added_by) <> '')
);

-- The tasks of one cycle. It also serves the cascade of a cycle.
CREATE INDEX IF NOT EXISTS idx_pm_cycle_tasks_cycle
    ON pm_cycle_tasks (organization_id, cycle_id);

-- ── The start snapshot ──────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS pm_cycle_committed (
    organization_id  UUID NOT NULL
                     REFERENCES organization (id) ON DELETE CASCADE
                     DEFAULT current_setting('app.tenant_id', true)::uuid,
    cycle_id         UUID NOT NULL,
    -- No foreign key: the row records the start (see the header).
    task_id          UUID NOT NULL,
    -- `pm_tasks.estimate_mins` at the start. NULL when the task had none.
    estimate_mins    INTEGER NULL,
    PRIMARY KEY (cycle_id, task_id),
    CONSTRAINT pm_cycle_committed_cycle_fk FOREIGN KEY (organization_id, cycle_id)
        REFERENCES pm_cycles (organization_id, id) ON DELETE CASCADE,
    CONSTRAINT pm_cycle_committed_estimate CHECK (
        estimate_mins IS NULL OR estimate_mins >= 0)
);

CREATE INDEX IF NOT EXISTS idx_pm_cycle_committed_cycle
    ON pm_cycle_committed (organization_id, cycle_id);

-- ── The capacity snapshot ───────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS pm_cycle_capacity (
    organization_id  UUID NOT NULL
                     REFERENCES organization (id) ON DELETE CASCADE
                     DEFAULT current_setting('app.tenant_id', true)::uuid,
    cycle_id         UUID NOT NULL,
    -- A member, by the lower-case address.
    member_email     TEXT NOT NULL,
    review_minutes   INTEGER NOT NULL,
    PRIMARY KEY (cycle_id, member_email),
    CONSTRAINT pm_cycle_capacity_cycle_fk FOREIGN KEY (organization_id, cycle_id)
        REFERENCES pm_cycles (organization_id, id) ON DELETE CASCADE,
    CONSTRAINT pm_cycle_capacity_member CHECK (
        member_email <> '' AND member_email = lower(member_email)),
    CONSTRAINT pm_cycle_capacity_minutes CHECK (review_minutes >= 0)
);

CREATE INDEX IF NOT EXISTS idx_pm_cycle_capacity_cycle
    ON pm_cycle_capacity (organization_id, cycle_id);

-- ── The node and the task of a row are rows of the row's own org ────────────
-- For each role, the table owner too. Fence: tests/unit/test_sprint_store_r8.py.

CREATE OR REPLACE FUNCTION pm_cycles_same_org() RETURNS trigger
LANGUAGE plpgsql AS $fn$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pm_projects
         WHERE id = NEW.project_id AND organization_id = NEW.organization_id
    ) THEN
        RAISE EXCEPTION 'pm_cycles: the node is not a project of this organization'
            USING ERRCODE = 'foreign_key_violation';
    END IF;
    RETURN NEW;
END
$fn$;

DROP TRIGGER IF EXISTS pm_cycles_same_org ON pm_cycles;
CREATE TRIGGER pm_cycles_same_org
    BEFORE INSERT OR UPDATE OF organization_id, project_id
    ON pm_cycles
    FOR EACH ROW EXECUTE FUNCTION pm_cycles_same_org();

CREATE OR REPLACE FUNCTION pm_cycle_tasks_same_org() RETURNS trigger
LANGUAGE plpgsql AS $fn$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pm_tasks
         WHERE id = NEW.task_id AND organization_id = NEW.organization_id
    ) THEN
        RAISE EXCEPTION 'pm_cycle_tasks: the task is not a task of this organization'
            USING ERRCODE = 'foreign_key_violation';
    END IF;
    RETURN NEW;
END
$fn$;

DROP TRIGGER IF EXISTS pm_cycle_tasks_same_org ON pm_cycle_tasks;
CREATE TRIGGER pm_cycle_tasks_same_org
    BEFORE INSERT OR UPDATE OF organization_id, task_id
    ON pm_cycle_tasks
    FOR EACH ROW EXECUTE FUNCTION pm_cycle_tasks_same_org();

-- ── The life of a cycle, and the snapshot ───────────────────────────────────
-- For each role. Fence: tests/unit/test_sprint_store_r8.py.

CREATE OR REPLACE FUNCTION pm_cycles_guard_update() RETURNS trigger
LANGUAGE plpgsql AS $fn$
BEGIN
    IF NEW.id IS DISTINCT FROM OLD.id
       OR NEW.organization_id IS DISTINCT FROM OLD.organization_id
       OR NEW.project_id IS DISTINCT FROM OLD.project_id
       OR NEW.created_by IS DISTINCT FROM OLD.created_by
       OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
        RAISE EXCEPTION 'pm_cycles: a cycle stays on its node'
            USING ERRCODE = 'check_violation';
    END IF;
    IF OLD.state = 'closed' THEN
        RAISE EXCEPTION 'pm_cycles: a closed cycle does not change'
            USING ERRCODE = 'check_violation';
    END IF;
    IF OLD.state = 'active' AND NEW.state = 'planned' THEN
        RAISE EXCEPTION 'pm_cycles: a started cycle does not go back to planned'
            USING ERRCODE = 'check_violation';
    END IF;
    IF OLD.state = 'planned' AND NEW.state = 'closed' THEN
        RAISE EXCEPTION 'pm_cycles: a cycle starts before it closes'
            USING ERRCODE = 'check_violation';
    END IF;
    IF OLD.state <> 'planned' AND (
           NEW.started_at IS DISTINCT FROM OLD.started_at
           OR NEW.started_by IS DISTINCT FROM OLD.started_by
           OR NEW.committed_task_count IS DISTINCT FROM OLD.committed_task_count
           OR NEW.committed_estimate_mins IS DISTINCT FROM OLD.committed_estimate_mins) THEN
        RAISE EXCEPTION 'pm_cycles: the start snapshot does not change'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END
$fn$;

DROP TRIGGER IF EXISTS pm_cycles_guard ON pm_cycles;
CREATE TRIGGER pm_cycles_guard
    BEFORE UPDATE ON pm_cycles
    FOR EACH ROW EXECUTE FUNCTION pm_cycles_guard_update();

CREATE OR REPLACE FUNCTION pm_cycle_snapshot_immutable() RETURNS trigger
LANGUAGE plpgsql AS $fn$
BEGIN
    RAISE EXCEPTION '%: a snapshot of the start does not change', TG_TABLE_NAME
        USING ERRCODE = 'check_violation';
END
$fn$;

DROP TRIGGER IF EXISTS pm_cycle_committed_immutable ON pm_cycle_committed;
CREATE TRIGGER pm_cycle_committed_immutable
    BEFORE UPDATE ON pm_cycle_committed
    FOR EACH ROW EXECUTE FUNCTION pm_cycle_snapshot_immutable();

DROP TRIGGER IF EXISTS pm_cycle_capacity_immutable ON pm_cycle_capacity;
CREATE TRIGGER pm_cycle_capacity_immutable
    BEFORE UPDATE ON pm_cycle_capacity
    FOR EACH ROW EXECUTE FUNCTION pm_cycle_snapshot_immutable();

-- ── Row level security ──────────────────────────────────────────────────────

DO $rls$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_class
         WHERE oid = to_regclass('pm_cycles')
           AND relrowsecurity
           AND relforcerowsecurity
    ) THEN
        ALTER TABLE pm_cycles ENABLE ROW LEVEL SECURITY;
        ALTER TABLE pm_cycles FORCE  ROW LEVEL SECURITY;
        DROP POLICY IF EXISTS pm_cycles_tenant_isolation ON pm_cycles;
        CREATE POLICY pm_cycles_tenant_isolation ON pm_cycles
            USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
            WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_class
         WHERE oid = to_regclass('pm_cycle_tasks')
           AND relrowsecurity
           AND relforcerowsecurity
    ) THEN
        ALTER TABLE pm_cycle_tasks ENABLE ROW LEVEL SECURITY;
        ALTER TABLE pm_cycle_tasks FORCE  ROW LEVEL SECURITY;
        DROP POLICY IF EXISTS pm_cycle_tasks_tenant_isolation ON pm_cycle_tasks;
        CREATE POLICY pm_cycle_tasks_tenant_isolation ON pm_cycle_tasks
            USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
            WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_class
         WHERE oid = to_regclass('pm_cycle_committed')
           AND relrowsecurity
           AND relforcerowsecurity
    ) THEN
        ALTER TABLE pm_cycle_committed ENABLE ROW LEVEL SECURITY;
        ALTER TABLE pm_cycle_committed FORCE  ROW LEVEL SECURITY;
        DROP POLICY IF EXISTS pm_cycle_committed_tenant_isolation ON pm_cycle_committed;
        CREATE POLICY pm_cycle_committed_tenant_isolation ON pm_cycle_committed
            USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
            WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_class
         WHERE oid = to_regclass('pm_cycle_capacity')
           AND relrowsecurity
           AND relforcerowsecurity
    ) THEN
        ALTER TABLE pm_cycle_capacity ENABLE ROW LEVEL SECURITY;
        ALTER TABLE pm_cycle_capacity FORCE  ROW LEVEL SECURITY;
        DROP POLICY IF EXISTS pm_cycle_capacity_tenant_isolation ON pm_cycle_capacity;
        CREATE POLICY pm_cycle_capacity_tenant_isolation ON pm_cycle_capacity
            USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
            WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);
    END IF;
END
$rls$;

-- ── The carry-over log ──────────────────────────────────────────────────────
-- `record_activity` refuses a type that this CHECK does not list, and
-- `core.ACTIVITY_TYPES` mirrors it. tests/unit/test_projects_migration.py
-- holds the tuple equal to the last CHECK.

ALTER TABLE pm_activities DROP CONSTRAINT IF EXISTS pm_activities_type_check;
ALTER TABLE pm_activities
    ADD CONSTRAINT pm_activities_type_check
    CHECK (type IN ('comment', 'status_change', 'field_change', 'link',
                    'assignment', 'agent_run', 'sync', 'system',
                    'attachment', 'mention', 'merge', 'cycle_transfer')) NOT VALID;
ALTER TABLE pm_activities VALIDATE CONSTRAINT pm_activities_type_check;

COMMENT ON TABLE pm_cycles IS
    'One cycle (a sprint) of one project node (WS-39 SP-1, D195). '
    'Only gateway/routes/projects/cycles.py writes it. '
    'project-docs/specs/project_management_app.md §13.2a.';
COMMENT ON TABLE pm_cycle_tasks IS
    'The tasks of a cycle. A task sits in one cycle at most (UNIQUE task_id). SP-1.';
COMMENT ON TABLE pm_cycle_committed IS
    'The start snapshot of a cycle: each task it held at the start. Never updated. SP-1.';
COMMENT ON TABLE pm_cycle_capacity IS
    'The capacity snapshot of a cycle: review minutes of each named person at the start. SP-1.';

COMMIT;
