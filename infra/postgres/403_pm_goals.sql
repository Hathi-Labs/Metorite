-- ============================================================================
-- 403_pm_goals.sql - the Goal type, and the link of an epic to its Goal
-- (WS-39 WM-5, Metorite task 984)
-- ============================================================================
-- Spec: project-docs/specs/project_management_app.md §13.6a row WM-5 · D204.4
--   · D199.6 · R1 · R5 · R6.
--
-- R1: on 2026-11-11 origin/main held 401 as its highest file. An open pull
--   request holds 402. So this file is 403. Check the number again at merge
--   (scripts/premerge.sh).
--
-- What: two columns.
--   pm_task_types.is_goal  BOOLEAN NOT NULL DEFAULT false. A task of this
--     type is a Goal. The pattern of `is_epic` (migration 168).
--     `core.is_goal_type` is its one reader.
--   pm_tasks.goal_task_id  UUID NULL, a self reference with ON DELETE SET
--     NULL. The Goal of an epic. In the spec, "the parent of epics" means
--     this link for a Goal. `parent_task_id` stays the tree inside one
--     project, and this link works across projects. One column gives one
--     Goal for each epic (D199.6), so a second link replaces the first.
--   One partial index (organization_id, goal_task_id) serves the read of
--   `rollup.goal_rollup`: the epics of one Goal.
--
-- Who writes them: `POST /projects/types` and `PATCH /projects/types/{id}`
--   write `is_goal` (`routes/projects/admin.py`). `POST /projects/tasks` and
--   `PATCH /projects/tasks/{id}` write `goal_task_id`
--   (`routes/projects/tasks.py`). `core.assert_goal_link` holds the rules:
--   only an epic carries a Goal, and the Goal is a Goal task of the same
--   organization that the writer can see. While `PROJECTS_GOALS_ORGS` does
--   not hold the organization, each writer refuses, so every row keeps the
--   default.
--
-- Expand only (R6): `is_goal` has a constant DEFAULT, so the ADD COLUMN
--   changes only the catalog (Postgres 11 and later). `goal_task_id` is
--   nullable with no default and no backfill. The old gateway names each
--   column of its INSERT and its UPDATE, so it never writes one of these.
--   `row_to_model` maps by model field, so no old reply carries them.
--   Nothing is renamed or dropped, and no contract phase is owed.
--   Idempotent: ADD COLUMN IF NOT EXISTS, and CREATE INDEX IF NOT EXISTS.
--   A second run changes nothing.
--
-- The field trigger of 304 (`trg_pm_tasks_stamp_fields`) sees a change of
--   `goal_task_id` as a change of a field. So a link moves `fields_at`, and
--   a version from before the link gets 412.
--
-- Tenancy (R5): `pm_tasks` and `pm_task_types` are tenant-scoped, with FORCE
--   RLS and the generated policies. New columns change neither. So this file
--   adds no table, no policy and no `gen_tenant_migration.EXEMPT` row. The
--   foreign key cannot hold the same organization, so `core.assert_goal_link`
--   loads the Goal through the visibility seam of the writer. RLS refuses a
--   row of another tenant before that. No column holds a member address, so
--   the member purge needs no change.
--
-- Never wait for the lock: `ALTER TABLE` takes ACCESS EXCLUSIVE, and every
--   task write takes this table. So the file sets `lock_timeout` first, as
--   304 and 401 do. On a lock timeout the runner tries the whole file again.
--   No CONCURRENTLY: the runner applies the file in one transaction. The
--   index is partial on a column that is NULL in every row, so it is empty.
--
-- Depends on: 146_projects.sql (the tables), 161_projects_tenancy.sql
--   (organization_id), 168_projects_delta_sync.sql (the `is_epic` pattern).
-- Pinned by: tests/unit/test_goal_rollup_r8.py (R8), which finds this file by
--   CONTENT, never by number (R1).
-- ============================================================================

SET lock_timeout = '5s';

ALTER TABLE pm_task_types
    ADD COLUMN IF NOT EXISTS is_goal BOOLEAN NOT NULL DEFAULT false;

ALTER TABLE pm_tasks
    ADD COLUMN IF NOT EXISTS goal_task_id UUID NULL
        REFERENCES pm_tasks (id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_pm_tasks_org_goal
    ON pm_tasks (organization_id, goal_task_id)
    WHERE goal_task_id IS NOT NULL;

COMMENT ON COLUMN pm_task_types.is_goal IS
    'WM-5: a task of this type is a Goal. core.is_goal_type reads it. POST and PATCH /projects/types write it.';
COMMENT ON COLUMN pm_tasks.goal_task_id IS
    'WM-5: the Goal of this epic, across projects. Only an epic carries it (core.assert_goal_link). POST and PATCH /projects/tasks write it. NULL means no Goal.';
