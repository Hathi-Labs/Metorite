-- ============================================================================
-- 265_pm_tasks_source_mcp.sql — WS-55 EA-6d: the task source `mcp`.
--
-- What: `pm_tasks.source` accepts one more value, `mcp`. The CHECK widens to
--       the union of the old values and `mcp`. Nothing else changes.
-- Why:  spec `project-docs/specs/external_agent_access.md` §9.2 and §15
--       EA-6d (decision EA3-31). An outside agent makes a task through the
--       MCP edge (`create_task`). The gateway stamps `source = 'mcp'` on such
--       a task, so a report can tell it from a task that a member made
--       (`manual`). The gateway sets the value only for a signed agent write.
--       A request body never sets it: `core.CLIENT_TASK_SOURCES` leaves `mcp`
--       out, so a member who sends `source: mcp` gets 422.
-- Depends on: 146_projects.sql (`pm_tasks` and its inline CHECK, which
--       Postgres named `pm_tasks_source_check`).
--
-- Expand only (R6). The CHECK widens and accepts every value it accepted
-- before. The old gateway runs against this schema for a moment during the
-- deploy, and it never writes `mcp`, so it meets no refusal. No row changes.
-- No table, no column and no rename.
--
-- The number was taken at build time (R1). On 2026-10-14 main held 263, and
-- the open PR #1020 held 264. Check it again at merge.
--
-- Idempotent: DROP CONSTRAINT IF EXISTS, then ADD. A replay gives the same
-- constraint. The ADD scans `pm_tasks` once under its lock, and every row
-- passes, because the new set holds every old value.
--
-- Fences: `tests/unit/test_projects_migration.py`
-- (`test_the_task_source_vocabulary_mirror_is_exact`, which reads this file
-- by CONTENT and never by number) and `tests/unit/test_mcp_edge_task_writes.py`
-- (R8: the constraint on a real database, and a task that an agent makes
-- reads `mcp`).
-- ============================================================================

SET lock_timeout = '5s';

BEGIN;

ALTER TABLE pm_tasks DROP CONSTRAINT IF EXISTS pm_tasks_source_check;
ALTER TABLE pm_tasks ADD CONSTRAINT pm_tasks_source_check
    CHECK (source IN ('manual', 'import', 'email', 'agent', 'automation', 'mcp'));

COMMIT;
