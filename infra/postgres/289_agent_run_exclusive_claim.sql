-- ============================================================================
-- 289_agent_run_exclusive_claim.sql — one open agent run for each task.
--
-- What: three changes to `pm_agent_runs`.
--       * The status CHECK gains `abandoned` and `released`, by DROP and ADD.
--         `abandoned`: another session took the task over after the lease.
--         `released`: the holder gave the task up without finishing.
--       * Each task with more than one open run keeps its NEWEST open run.
--         The older ones move to `abandoned`, so the index below can build.
--       * A partial unique index on (organization_id, task_id) over the four
--         open statuses. At most one open run for each task, across every
--         member and every client.
-- Why:  WS-55 EA-14a, owner directive 2026-10-20. If one AI works on a task,
--       no other AI session may claim or start work on it. The old index
--       `pm_agent_runs_one_open` is per member and client, so two members, or
--       two clients of one member, could both hold one task. That index
--       stays: it is the narrower rule and it costs nothing.
-- Depends on: 267_pm_agent_runs.sql.
--
-- Expand only (R6). The CHECK only WIDENS, so the gateway that runs before
-- the restart keeps working against it. The old gateway never writes the two
-- new statuses. The new index is the one change old code can meet: old code
-- may insert a second open run on a task for another member, and the insert
-- then fails with a unique violation until the new gateway runs. The deploy
-- applies migrations before it restarts services (R6), and production holds
-- 0 rows of this table at the time of writing, so the window is empty.
-- Idempotent: DROP IF EXISTS before ADD, CREATE INDEX IF NOT EXISTS, and an
-- UPDATE that matches nothing on the second run.
-- Tenant-scoped (R5): no new table. The UPDATE names no organization, and it
-- joins on `organization_id` so a task never counts a run of another tenant.
-- The migration role sees every row, as in 288 and the other backfills.
-- The number is taken at build time (R1). On 2026-10-20 main held 288.
-- Check it again at merge.
-- Pinned by tests/unit/test_agent_run_exclusive.py, which finds this file by
-- CONTENT, never by number (R1).
-- ============================================================================

BEGIN;

ALTER TABLE pm_agent_runs DROP CONSTRAINT IF EXISTS pm_agent_runs_status_check;

ALTER TABLE pm_agent_runs
    ADD CONSTRAINT pm_agent_runs_status_check
    CHECK (status IN ('created', 'in_progress', 'awaiting',
                      'completed', 'stopping', 'stopped',
                      'failed', 'stale', 'abandoned', 'released'));

-- Close the duplicates FIRST. For each task, the newest open run stays, and
-- every older open run becomes `abandoned`. The order is (created_at, id), so
-- a tie on the time still picks one run.
UPDATE pm_agent_runs r
   SET status = 'abandoned',
       ended_at = now()
 WHERE r.status IN ('created', 'in_progress', 'awaiting', 'stopping')
   AND EXISTS (
        SELECT 1 FROM pm_agent_runs n
         WHERE n.organization_id = r.organization_id
           AND n.task_id = r.task_id
           AND n.status IN ('created', 'in_progress', 'awaiting', 'stopping')
           AND (n.created_at, n.id) > (r.created_at, r.id)
   );

CREATE UNIQUE INDEX IF NOT EXISTS pm_agent_runs_one_open_per_task
    ON pm_agent_runs (organization_id, task_id)
    WHERE status IN ('created', 'in_progress', 'awaiting', 'stopping');

COMMENT ON INDEX pm_agent_runs_one_open_per_task IS
    'WS-55 EA-14a: one OPEN run for each task, whoever holds it. The gate in '
    'routes/projects/agent_runs.py (_gate_task) reads the holder first, and '
    'this index decides a race.';

COMMIT;
