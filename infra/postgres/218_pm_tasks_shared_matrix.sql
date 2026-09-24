-- 218 — D78: the priority matrix is ONE shared answer on the task.
--
-- Owner decision, 2026-09-24. Projects and My Tasks show one priority
-- system, the matrix (important x urgent x leveraged). Its two stated
-- inputs are facts about the WORK, so they live on `pm_tasks`, and every
-- member reads the same answer:
--
--   * Important is `pm_tasks.importance >= 2`. The column keeps its 0-3
--     values, so every reader that compares it still works. NULL is
--     "nobody has judged this yet".
--   * Leveraged is the new `pm_tasks.leveraged` below.
--   * Urgent is still derived from `due_at` and is never stored.
--
-- This AMENDS D76, which kept `important` and `leveraged` on the member's
-- overlay (`pm_task_personal`, migration 188). Those two overlay columns
-- stay on disk until a later contract (R6), and the gateway refuses a write
-- to either one by name (`RETIRED_OVERLAY_KEYS`).
--
-- ## The carry, once
--
-- A member's stated flag is copied onto the task. It only RAISES:
--   * any member's `important = true` lifts `importance` to 2 when it is
--     NULL or below 2. A Highest (3) stays 3.
--   * any member's `leveraged = true` sets `pm_tasks.leveraged`.
-- A member's `important = false` on a High task is NOT carried. One answer
-- per task is the decision, and the shared answer was already High.
--
-- Neither update touches `updated_at`, the same call 216 makes. A bump
-- would make every flagged task look freshly edited to the Analytics ageing
-- bands and to every "recently updated" sort. The cost: a page left open
-- across the deploy shows the old flags until it reloads. The deploy
-- restarts the app, so that page reloads soon anyway.
--
-- ## Replay
--
-- The carry runs only while the ledger does not yet name this file, the
-- same guard as 216. A later replay must not re-raise a flag a member
-- cleared on the shared task since the first run.

ALTER TABLE pm_tasks
    ADD COLUMN IF NOT EXISTS leveraged BOOLEAN DEFAULT false;

COMMENT ON COLUMN pm_tasks.leveraged IS
    'D78: the matrix''s shared Leveraged input (asymmetric upside). '
    'Important is importance >= 2. Urgent derives from due_at.';

DO $$
BEGIN
    IF to_regclass('public.schema_migrations') IS NOT NULL
       AND EXISTS (SELECT 1 FROM schema_migrations
                    WHERE filename = '218_pm_tasks_shared_matrix.sql') THEN
        RAISE NOTICE '218: matrix carry already applied — skipped';
        RETURN;
    END IF;

    UPDATE pm_tasks t
       SET importance = 2
     WHERE (t.importance IS NULL OR t.importance < 2)
       AND EXISTS (SELECT 1 FROM pm_task_personal p
                    WHERE p.task_id = t.id
                      AND p.organization_id = t.organization_id
                      AND p.important IS TRUE);

    UPDATE pm_tasks t
       SET leveraged = true
     WHERE t.leveraged IS NOT TRUE
       AND EXISTS (SELECT 1 FROM pm_task_personal p
                    WHERE p.task_id = t.id
                      AND p.organization_id = t.organization_id
                      AND p.leveraged IS TRUE);
END
$$;
