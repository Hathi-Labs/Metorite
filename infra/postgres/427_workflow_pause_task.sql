-- ============================================================================
-- 427_workflow_pause_task.sql — STU-E4: the pause of a human step.
-- ============================================================================
-- Spec: project-docs/specs/ai_studio.md §3.3 and §14 row E4.
--
-- What: `workflow_run_pauses.reason` accepts one more value, `task`. A run that
--       reaches a human step makes a task in `pm_tasks` and pauses with this
--       reason. The scan of `scheduler.scan_done_tasks` resumes it when the
--       task moves to a done status.
-- Depends on: 132_workflows.sql (`workflow_run_pauses` and its inline CHECK,
--       which Postgres named `workflow_run_pauses_reason_check`).
--
-- Expand only (R6). The CHECK widens and accepts every value that it accepted
-- before. The old gateway runs against this schema for a moment during the
-- deploy, and it never writes `task`, so it meets no refusal. No row changes.
-- No table, no column and no rename.
--
-- R5: no new table. `workflow_run_pauses` keeps its `organization_id` column,
-- its FORCE ROW LEVEL SECURITY and its policy from the generated phases.
--
-- The number was taken at build time (R1). On 2026-11-13 main held 426. Check
-- it again at merge.
--
-- Idempotent: DROP CONSTRAINT IF EXISTS, then ADD. A replay gives the same
-- constraint. Every row passes the ADD, because the new set holds each old value.
--
-- Fence: `tests/unit/test_workflows_human_step_r8.py` (R8: the constraint on a
-- real database, and a run that pauses with the reason `task`).
-- ============================================================================

SET lock_timeout = '5s';

BEGIN;

ALTER TABLE workflow_run_pauses DROP CONSTRAINT IF EXISTS workflow_run_pauses_reason_check;
ALTER TABLE workflow_run_pauses ADD CONSTRAINT workflow_run_pauses_reason_check
    CHECK (reason IN ('approval', 'wait', 'oauth', 'task'));

COMMIT;
