-- 216_pm_tasks_estimate_backfill.sql — one estimate per task (D77, WS-39 S6f)
--
-- What: copy each member's overlay estimate (`pm_task_personal.
--       time_estimate_mins`) into the task's SHARED estimate
--       (`pm_tasks.estimate_mins`), where the task has none. Nothing else.
-- Why:  D77 (owner directive 2026-09-23). A fact about the WORK has one home.
--       Before this, My Tasks wrote its estimate to the overlay and People
--       capacity, analytics and the Projects board read `pm_tasks`. So an
--       estimate typed in My Tasks never reached capacity. From S6f on, both
--       apps read and write `estimate_mins`, and this carries the values the
--       overlay already holds across.
-- Depends on: 146_projects.sql (`pm_tasks.estimate_mins`),
--             147_projects_personal.sql (`time_estimate_mins`),
--             161_projects_tenancy.sql (`organization_id` on both tables).
--
-- ## The rules
--
-- 1. **Only where the task has no estimate.** A shared estimate somebody set
--    on the board is the team's number, and it wins. An overlay value that
--    disagrees with it stays on the overlay row, unread (R6: no drop).
-- 2. **One value per task, chosen the same way every time.** Several members
--    can each hold an estimate for one task. An ASSIGNEE's value wins over a
--    non-assignee's (the person doing the work sized it), the first assignee
--    by `pm_task_assignees` order (`assigned_at`, then `assignee`) wins over
--    a later one, and then the earliest `updated_at`, then the address.
-- 3. **Zero is not an estimate.** The Tasks app cleared its estimate by
--    writing 0, so a 0 is "cleared", not "no time at all".
-- 4. **Tenant-correct.** The overlay row and the task must belong to one
--    organization. They always do (161's triggers stamp both from the
--    project), and the join says so rather than trusting it.
-- 5. **`updated_at` is left alone.** A deploy-time copy is not an edit a
--    person made, and bumping it would reorder every "recently updated" list
--    and wake every open board's delta read for no change they can see.
--
-- ## What this file does NOT touch: priority
--
-- The overlay's `important` is the member's own answer, and it stays where
-- it is (D76). The shared `importance` only SEEDS it on read, and the seed
-- writes nothing. So no flag is copied into `importance` here. An earlier
-- draft of this file did that, under a withdrawn draft of D77. That draft
-- never merged, so production never ran it. A developer's scratch database
-- can hold its ledger line under the old name `215_pm_tasks_estimate_
-- backfill.sql`. Nothing reads that line, and this file runs once there too.
--
-- ## Why 216
--
-- This file was built as 215. Main took 215 first (`215_projects_sealed.sql`,
-- #428), so this file moved to 216 before its merge (R1).
--
-- ## Idempotent, and a replay is a no-op
--
-- The runner records this file in `schema_migrations` once it applies. A
-- whole-ladder replay (`MIGRATION_REPLAY_ALL=1`) runs every file again, and a
-- plain `WHERE estimate_mins IS NULL` guard would then REFILL an estimate a
-- member cleared on purpose since the first run. So the copy runs only while
-- the ledger does not yet name this file. A database with no ledger (a bare
-- scratch replay) takes the `IS NULL` guard alone, which is idempotent on
-- its own: a second run finds nothing left to fill.

DO $$
BEGIN
    IF to_regclass('public.schema_migrations') IS NOT NULL
       AND EXISTS (SELECT 1 FROM schema_migrations
                    WHERE filename = '216_pm_tasks_estimate_backfill.sql') THEN
        RAISE NOTICE '216: estimate backfill already applied — skipped';
        RETURN;
    END IF;

    WITH pick AS (
        SELECT DISTINCT ON (p.task_id)
               p.task_id,
               p.time_estimate_mins AS mins
          FROM pm_task_personal p
          JOIN pm_tasks t
            ON t.id = p.task_id
           AND t.organization_id = p.organization_id
          LEFT JOIN pm_task_assignees a
            ON a.task_id = p.task_id
           AND lower(a.assignee) = lower(p.member_email)
         WHERE p.time_estimate_mins IS NOT NULL
           AND p.time_estimate_mins > 0
           AND t.estimate_mins IS NULL
         ORDER BY p.task_id,
                  (a.task_id IS NULL),
                  a.assigned_at NULLS LAST,
                  a.assignee NULLS LAST,
                  p.updated_at,
                  p.member_email
    )
    UPDATE pm_tasks t
       SET estimate_mins = pick.mins
      FROM pick
     WHERE t.id = pick.task_id
       AND t.estimate_mins IS NULL;
END
$$;
