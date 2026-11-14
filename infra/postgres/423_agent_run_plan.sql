-- ============================================================================
-- 423_agent_run_plan.sql - the plan of an agent run (WS-MCP task 1049, SW-32,
-- agent_coordination.md, D140.7)
-- ============================================================================
-- Spec: project-docs/specs/agent_coordination.md, slice SW-32 · Metorite task
-- 1049 · R1 · R5 · R6 · R8.
-- R1: on 2026-11-12 origin/main held 418 as its highest file, and open
-- pull requests held 419 to 422. So this file is 423. Check the number again
-- at merge.
--
-- What: one nullable column on `pm_agent_runs`.
--   plan  JSONB NULL, with the named CHECK `pm_agent_runs_plan_shape`. A
--         JSON array of at most 30 steps. Each step is {content, status}.
--         NULL means the agent set no plan.
--
-- Who writes it: only `agent_runs.set_run_plan`, on the run of the calling
--   agent. Each call replaces the whole list (D140.7). The gateway checks
--   the steps (the text, the four statuses, 200 characters). The CHECK is
--   the last guard against a bad write: the shape and the cap of 30.
--
-- Expand only (R6): one new column, no default, no backfill, no rename. The
--   old code names each column by name, so it never reads the new one, and
--   its UPDATE leaves it NULL. A NULL plan reads as no plan, so every row
--   from before this file keeps its look. Idempotent: ADD COLUMN IF NOT
--   EXISTS, and the CHECK only while `pg_constraint` lacks its name.
--
-- Tenancy (R5): `pm_agent_runs` is tenant-scoped, with FORCE RLS (267). The
--   column changes neither. So this file adds no table, no policy and no
--   `gen_tenant_migration.EXEMPT` row.
--
-- Never wait for the lock: `ALTER TABLE` takes ACCESS EXCLUSIVE on
--   `pm_agent_runs`, and each agent call reads that table. So the file sets
--   `lock_timeout` itself, as 403 does. On a lock timeout the runner tries
--   the whole file again, and the file is idempotent.
--
-- Depends on: 267_pm_agent_runs.sql (the table).
-- Pinned by: tests/unit/test_agent_runs_r8.py (R8), which finds this file by
--   CONTENT, never by number (R1).
-- ============================================================================

SET lock_timeout = '5s';

ALTER TABLE pm_agent_runs
    ADD COLUMN IF NOT EXISTS plan JSONB;

DO $checks$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conname = 'pm_agent_runs_plan_shape'
           AND conrelid = 'pm_agent_runs'::regclass
    ) THEN
        ALTER TABLE pm_agent_runs ADD CONSTRAINT pm_agent_runs_plan_shape
            CHECK (plan IS NULL
                   OR (jsonb_typeof(plan) = 'array' AND jsonb_array_length(plan) <= 30));
    END IF;
END
$checks$;

COMMENT ON COLUMN pm_agent_runs.plan IS
    'Task 1049 (SW-32). The checklist that the agent keeps current: a JSON array of at most 30 {content, status} steps. Each set_run_plan call replaces the whole list. NULL when the agent set no plan.';
