-- 215_pm_tasks_estimate_backfill.sql — one estimate per task (D76, WS-39 S6f)
--
-- What: copy each member's overlay estimate (`pm_task_personal.
--       time_estimate_mins`) into the task's SHARED estimate
--       (`pm_tasks.estimate_mins`), where the task has none. And carry each
--       Focus-matrix "important" flag (`pm_task_personal.important = true`)
--       into the shared Priority (`pm_tasks.importance = 2`, High), where the
--       task's Priority is unset or below High.
-- Why:  D76 (owner directive 2026-09-23). A fact about the WORK has one home.
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
-- 5. **Important raises, never lowers.** D76 derives Important from
--    `importance >= 2`, so an overlay flag with no copy would silently read
--    as "not important" the day this ships. The flag chosen by rule 2 (the
--    assignee's first) sets High on a task that is unset or Low/Normal. A
--    task already High or Urgent is left alone, and a `false` flag changes
--    nothing: it never lowers a Priority somebody set on the board. On a
--    task in a member's own tree there is one member, so the flag is theirs.
-- 6. **`updated_at` is left alone.** A deploy-time copy is not an edit a
--    person made, and bumping it would reorder every "recently updated" list
--    and wake every open board's delta read for no change they can see.
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
                    WHERE filename = '215_pm_tasks_estimate_backfill.sql') THEN
        RAISE NOTICE '215: estimate backfill already applied — skipped';
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

    WITH flag AS (
        SELECT DISTINCT ON (p.task_id)
               p.task_id,
               p.important
          FROM pm_task_personal p
          JOIN pm_tasks t
            ON t.id = p.task_id
           AND t.organization_id = p.organization_id
          LEFT JOIN pm_task_assignees a
            ON a.task_id = p.task_id
           AND lower(a.assignee) = lower(p.member_email)
         WHERE p.important IS NOT NULL
           AND (t.importance IS NULL OR t.importance < 2)
         ORDER BY p.task_id,
                  (a.task_id IS NULL),
                  a.assigned_at NULLS LAST,
                  a.assignee NULLS LAST,
                  p.updated_at,
                  p.member_email
    )
    UPDATE pm_tasks t
       SET importance = 2
      FROM flag
     WHERE t.id = flag.task_id
       AND flag.important
       AND (t.importance IS NULL OR t.importance < 2);
END
$$;
