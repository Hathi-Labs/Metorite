-- ============================================================================
-- 421_agent_run_signal.sql - the linked-event clock of an agent run (WS-MCP
-- task 1045b, agent_coordination.md slice 1045b, §3.3)
-- ============================================================================
-- Spec: project-docs/specs/agent_coordination.md, slice 1045b · Metorite task
-- 1045 · R1 · R5 · R6 · R8.
-- R1: origin/main held 417 as its highest file on 2026-11-12. Open PRs 2137
-- and 2156 hold 416, open PR 2089 holds 418, and the SW-25 branch holds 417.
-- So this file is 421. Check the number again at merge.
--
-- What: one nullable column on `pm_agent_runs`.
--   last_signal_at  TIMESTAMPTZ NULL. When the newest linked event came: a
--                   push to a linked pull request (`synchronize`) or a check
--                   event on its head. Only the GitHub webhook writes it
--                   (`agent_runs.signal_linked_runs`).
--
-- Why a second column: the life of a run is the newer of `last_activity_at`
--   and `last_signal_at` (`filters.run_life_at_sql`). The stale band, the
--   lease and the strand fit read that. The answered rule
--   (`filters.AGENT_RUN_ANSWER_WAITS_SQL`) reads `last_activity_at` only. A
--   linked event that moved `last_activity_at` would make the run read
--   `answered`, and `needs_agent` would list it.
--
-- Expand only (R6): one new column, no default, no backfill, no rename. The
--   old code names each column by name, so it never reads the new one. A NULL
--   reads as no linked event, so every row from before this file keeps its
--   state. Idempotent: ADD COLUMN IF NOT EXISTS.
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
    ADD COLUMN IF NOT EXISTS last_signal_at TIMESTAMPTZ;

COMMENT ON COLUMN pm_agent_runs.last_signal_at IS
    '1045b: the newest linked event (a push to a linked pull request or a '
    'check event). It renews the lease and the stale band. The answered rule '
    'never reads it. agent_coordination.md slice 1045b.';
