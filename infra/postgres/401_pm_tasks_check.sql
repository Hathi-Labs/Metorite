-- ============================================================================
-- 401_pm_tasks_check.sql - a task carries a Check (WS-MCP SW-23, Metorite
-- task 857)
-- ============================================================================
-- Spec: project-docs/specs/agent_coordination.md SW-23 and §3.5 · R1 · R5 · R6.
--
-- R1: on 2026-11-11 origin/main held 393 as its highest file. Open branches
--   and worktrees held 396, 398, 399 and 400. So this file is 401. Check the
--   number again at merge (scripts/premerge.sh).
--
-- Why: a HANDOFF.md entry has a Check, a read-only command that proves the
--   work is still open (D135.1). SW-23 puts the Check on the task, so an
--   agent reads it from my_work, runs it, and closes the task with the
--   evidence when it passes.
--
-- What: four nullable columns on `pm_tasks`.
--   check_text           TEXT. The command. NULL means the task has no Check.
--   check_last_run_at    TIMESTAMPTZ. When an agent last reported a run.
--   check_last_result    TEXT. `open`, `passed` or `cannot_run`.
--   check_last_evidence  TEXT. What the last run showed.
--   One named CHECK holds the three results. A second named CHECK keeps the
--   result and its time together: both are set, or both are NULL.
--
-- Who writes them: `POST /projects/tasks` and `PATCH /projects/tasks/{id}`
--   write `check_text` (`gateway/routes/projects/tasks.py`). A PATCH that
--   changes the text clears the three result columns, because the old
--   result was about another command. Only
--   `POST /projects/tasks/{id}/check-result` (`routes/projects/checks.py`)
--   writes the result columns. While `AGENT_TASK_CHECKS_ENABLED` is off, each
--   writer refuses, so every row keeps NULL.
--
-- Expand only (R6): four new nullable columns with no default and no
--   backfill, so the ALTER changes only the catalog. The old gateway names
--   each column of its INSERT and its UPDATE, so it never writes one of
--   these. `row_to_model` maps by model field, and `TaskModel` names none of
--   them, so no old reply carries them. Each CHECK passes on NULL, so it
--   is added NOT VALID and then validated. The table is big, and VALIDATE
--   takes only SHARE UPDATE EXCLUSIVE.
--   Idempotent: ADD COLUMN IF NOT EXISTS, and each CHECK is added only while
--   `pg_constraint` lacks its name. A second run changes nothing.
--
-- The field trigger of 304 (`trg_pm_tasks_stamp_fields`) sees a change of
--   these columns as a change of a field. So a reported run moves
--   `fields_at`, and a version from before the report gets 412. The report
--   answers the new version.
--
-- Tenancy (R5): `pm_tasks` is tenant-scoped, with FORCE RLS and the
--   generated policies. New columns change neither. So this file adds no
--   table, no policy and no `gen_tenant_migration.EXEMPT` row. No column
--   holds a member address, so the member purge needs no change.
--
-- Never wait for the lock: `ALTER TABLE` takes ACCESS EXCLUSIVE, and every
--   task write takes this table. So the file sets `lock_timeout` first, as
--   304 does. On a lock timeout the runner tries the whole file again.
--
-- Depends on: 146_projects.sql (the table).
-- Pinned by: tests/unit/test_task_check_r8.py (R8), which finds this file by
--   CONTENT, never by number (R1).
-- ============================================================================

SET lock_timeout = '5s';

ALTER TABLE pm_tasks ADD COLUMN IF NOT EXISTS check_text TEXT;
ALTER TABLE pm_tasks ADD COLUMN IF NOT EXISTS check_last_run_at TIMESTAMPTZ;
ALTER TABLE pm_tasks ADD COLUMN IF NOT EXISTS check_last_result TEXT;
ALTER TABLE pm_tasks ADD COLUMN IF NOT EXISTS check_last_evidence TEXT;

DO $pm_check$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                    WHERE conname = 'pm_tasks_check_result_known') THEN
        ALTER TABLE pm_tasks ADD CONSTRAINT pm_tasks_check_result_known
            CHECK (check_last_result IS NULL
                   OR check_last_result IN ('open', 'passed', 'cannot_run'))
            NOT VALID;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                    WHERE conname = 'pm_tasks_check_run_whole') THEN
        ALTER TABLE pm_tasks ADD CONSTRAINT pm_tasks_check_run_whole
            CHECK ((check_last_result IS NULL) = (check_last_run_at IS NULL))
            NOT VALID;
    END IF;
END $pm_check$;

ALTER TABLE pm_tasks VALIDATE CONSTRAINT pm_tasks_check_result_known;
ALTER TABLE pm_tasks VALIDATE CONSTRAINT pm_tasks_check_run_whole;

COMMENT ON COLUMN pm_tasks.check_text IS
    'SW-23: the Check of the task, a read-only command that proves the work is still open. NULL means no Check. POST and PATCH /projects/tasks write it.';
COMMENT ON COLUMN pm_tasks.check_last_run_at IS
    'SW-23: when an agent last reported a run of the Check. Only POST /projects/tasks/{id}/check-result writes it.';
COMMENT ON COLUMN pm_tasks.check_last_result IS
    'SW-23: open, passed or cannot_run. Only POST /projects/tasks/{id}/check-result writes it. A change of check_text clears it.';
COMMENT ON COLUMN pm_tasks.check_last_evidence IS
    'SW-23: what the last run of the Check showed. Only POST /projects/tasks/{id}/check-result writes it.';
