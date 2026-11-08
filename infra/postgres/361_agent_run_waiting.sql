-- ============================================================================
-- 361_agent_run_waiting.sql - the `waiting` time of an agent run (WS-MCP
-- task 1045a, agent_coordination.md §3.2, §3.3, D140.8)
-- ============================================================================
-- Spec: project-docs/specs/agent_coordination.md, slice 1045a · Metorite task
-- 1045 · R1 · R5 · R6 · R8.
-- R1: origin/main held 348 as its highest file on 2026-11-06. Branch
-- kg4-structural-edges holds 346, branch mt-1170f52b-cc2-today holds 349 and
-- branch mt-c6e41e26-cc5a-focus-log holds 350. So this file is 361 (renumbered at merge, R1: main held 359 and an open branch held 360). Check the
-- number again at merge.
--
-- What: two nullable columns on `pm_agent_runs`.
--   waiting_on     TEXT NULL, with the named CHECK `pm_agent_runs_waiting_on`
--                  (ci, review, deploy, member, agent). What the agent
--                  waits on. `agent` is another (external) agent. The
--                  agent's own sub-agents are work, so no value names them.
--   waiting_until  TIMESTAMPTZ NULL. When the wait ends. The run reads
--                  `waiting` until this time, and `stale` after it, while no
--                  new activity comes (`filters.AGENT_RUN_STATE_SQL`).
--
-- Who writes them: only `agent_runs._move_run`, called by `_write_activity`,
--   the one write path of an activity. An activity with `waiting` sets both.
--   Any other activity clears both. The note of the wait stays in the
--   activity's `content.waiting`, so this file adds no note column.
--
-- Expand only (R6): two new columns, no default, no backfill, no rename. The
--   old code names each column by name, so it never reads the new ones, and
--   its UPDATE leaves them NULL. A NULL wait reads as no wait, so every row
--   from before this file keeps its state. Idempotent: ADD COLUMN IF NOT
--   EXISTS, and the CHECK only while `pg_constraint` lacks its name.
--
-- Tenancy (R5): `pm_agent_runs` is tenant-scoped, with FORCE RLS (267). The
--   columns change neither. So this file adds no table, no policy and no
--   `gen_tenant_migration.EXEMPT` row.
--
-- Never wait for the lock: `ALTER TABLE` takes ACCESS EXCLUSIVE on
--   `pm_agent_runs`, and each agent call reads that table. So the file sets
--   `lock_timeout` itself, as 348 does. On a lock timeout the runner tries
--   the whole file again, and the file is idempotent.
--
-- Depends on: 267_pm_agent_runs.sql (the table).
-- Pinned by: tests/unit/test_agent_runs_r8.py (R8), which finds this file by
--   CONTENT, never by number (R1).
-- ============================================================================

SET lock_timeout = '5s';

-- One statement, so the table takes its ACCESS EXCLUSIVE lock once.
ALTER TABLE pm_agent_runs
    ADD COLUMN IF NOT EXISTS waiting_on TEXT,
    ADD COLUMN IF NOT EXISTS waiting_until TIMESTAMPTZ;

DO $checks$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conname = 'pm_agent_runs_waiting_on'
           AND conrelid = 'pm_agent_runs'::regclass
    ) THEN
        ALTER TABLE pm_agent_runs ADD CONSTRAINT pm_agent_runs_waiting_on
            CHECK (waiting_on IS NULL
                   OR waiting_on IN ('ci', 'review', 'deploy', 'member', 'agent'));
    END IF;
END
$checks$;

COMMENT ON COLUMN pm_agent_runs.waiting_on IS
    'Task 1045a. What the agent waits on: ci, review, deploy, member or agent (another agent). NULL when the newest activity set no wait.';

COMMENT ON COLUMN pm_agent_runs.waiting_until IS
    'Task 1045a. When the wait ends. The run reads waiting until then, and stale after it, while no new activity comes. NULL when no wait is set.';
