-- ============================================================================
-- 190_gtd_retirement_drop.sql — WS-39 S3c. The `gtd_*` task store goes away.
--
-- What: D53.5 release (3). Drops the two tables S3b replaced — `gtd_items` and
--   `gtd_waiting` — once, irreversibly, and only when it is safe.
--
-- ⚠️ THIS MIGRATION IS INERT ON A NORMAL DEPLOY. It drops nothing unless BOTH
--   conditions hold, and it is written that way because we CANNOT ROLL BACK
--   (R6): there is no down-migration, only roll forward or restore from backup.
--
--     1. ARMED — a row exists in `gtd_retirement_arm` (migration 189 created
--        that table empty). The owner inserts it by hand, after verifying.
--     2. ACCOUNTED FOR — every `gtd_items` row carries `migrated_task_id`.
--
--   Neither alone is sufficient, and the reason is worth stating: "the data
--   looks migrated" is also what a HALF-FINISHED move looks like, and "somebody
--   armed it" is also what an over-confident afternoon looks like. One is a
--   fact about the data, the other is a human taking responsibility, and a
--   destructive act should need both.
--
--   To arm:
--       SELECT * FROM gtd_backfill_plan;   -- must return ZERO rows
--       INSERT INTO gtd_retirement_arm (armed_by, note)
--       VALUES ('owner@example.com', 'S3b verified, lens flipped, N days clean');
--
-- ⚠️ WHAT THIS DOES NOT DROP, and why each one survives:
--
--   * `gtd_settings`, `gtd_day_state`, `gtd_rollover_log` — **D53.6 names these
--     explicitly.** They are per-member CALENDAR state, not task rows; they
--     belong to D54's Calendar app and are not part of this retirement. A sweep
--     that took them with the rest is the specific mistake D53.6 exists to stop.
--   * `gtd_people`, `gtd_person_absences`, `gtd_person_credentials`,
--     `gtd_person_resumes`, `gtd_person_skills` — the PEOPLE DIRECTORY. The
--     `gtd_` prefix is the only thing they share with the task store; they back
--     `fetchPeople` / `createPerson` / `uploadResume`, which have nothing to do
--     with D53 and no `pm_*` destination.
--   * `gtd_horizons` — **WS-21 owns Horizons** (`work_plan.md` §4,
--     DO-NOT-DISPATCH). Dropping it here would retire another workstream's
--     substrate on WS-39's authority.
--   * `gtd_reviews` — Weekly Review is WS-18's, and NO-GO pending its JSON
--     contract. Dead is not the same as retired.
--   * `gtd_projects`, `gtd_spaces`, `gtd_folders`, `gtd_contexts`,
--     `gtd_attachments` — the LOCAL project tree and its furniture. These wait
--     on **S3a-client slice 5**, which ports `/hierarchy` · `/spaces` ·
--     `/folders` · `/local-projects` onto `pm_projects`. ⚠️ H-33 originally
--     filed that family as "deletion, not porting"; slice 4 measured it and
--     found the opposite — `routes/tasks/hierarchy.py` is the LOCAL tree and
--     its own header says "SYNCED projects are NOT here". Dropping these before
--     the port would delete the Tasks app's ability to organise projects at
--     all. A later migration retires them, after slice 5.
--
-- ⚠️ SEQUENCING THIS FILE CANNOT CHECK. `gtd_items` is still read by the
--   gateway whenever `TASKS_LENS` is off, and by the CRUD/AI routes that slice
--   5 has not yet ported (`routes/tasks/ai.py` alone names `gtd_*` 33 times).
--   SQL cannot see an environment variable or an unported route, so arming is
--   also the human's assertion that slice 5 has landed and both flags are on.
--   `docs/TASKS_LENS.md` carries the order.
--
-- Idempotent: once the tables are gone the guard finds nothing and says so.
--
-- Depends on: 189_gtd_backfill_to_pm.sql (the arm table + migrated_task_id).
-- Pinned by: tests/unit/test_gtd_backfill.py, tests/live/live_ws39_s3c.sql.
-- ============================================================================

-- The guard is a FUNCTION that the file then calls once, rather than a bare
-- `DO` block. Same behaviour on deploy, but a `DO` block can only be executed
-- by re-running the file, which means its refusal paths could only ever be
-- tested by NOT testing them. `tests/live/live_ws39_s3c.sql` calls this four
-- times in one transaction — unarmed, armed-but-unsafe, armed-and-safe, and
-- again after the drop — which is the only way to prove a guard actually guards.

CREATE OR REPLACE FUNCTION gtd_retirement_drop() RETURNS void
LANGUAGE plpgsql AS $s3c$
DECLARE
    v_armed      bigint;
    v_unmoved    bigint;
    v_total      bigint;
    v_armed_by   text;
BEGIN
    -- Already retired? Then there is nothing to say and nothing to do.
    IF to_regclass('public.gtd_items') IS NULL THEN
        RAISE NOTICE 'S3c: gtd_items is already gone — nothing to do.';
        RETURN;
    END IF;

    IF to_regclass('public.gtd_retirement_arm') IS NULL THEN
        RAISE NOTICE 'S3c: no arming table (migration 189 has not applied) — skipping.';
        RETURN;
    END IF;

    SELECT count(*) INTO v_armed FROM gtd_retirement_arm;

    IF v_armed = 0 THEN
        RAISE NOTICE 'S3c: NOT ARMED — gtd_items kept. This is the normal state; '
                     'the drop is a deliberate act, not a consequence of deploying.';
        RETURN;
    END IF;

    SELECT count(*), count(*) FILTER (WHERE migrated_task_id IS NULL)
      INTO v_total, v_unmoved
      FROM gtd_items;

    IF v_unmoved > 0 THEN
        -- Armed but not safe. This is a refusal, not a warning: an armed drop
        -- that quietly skipped would leave somebody believing the retirement
        -- had happened.
        RAISE EXCEPTION
            'S3c REFUSED: % of % gtd_items rows have no migrated_task_id, so '
            'dropping the table would destroy them and we cannot roll back (R6). '
            'Run SELECT * FROM gtd_backfill_plan; to see them. Rows reading '
            '"unmappable" have no resolvable owner — decide each one deliberately '
            '(give the address an app_user, or delete the row) rather than '
            'widening this guard.',
            v_unmoved, v_total;
    END IF;

    SELECT armed_by INTO v_armed_by FROM gtd_retirement_arm ORDER BY armed_at LIMIT 1;
    RAISE NOTICE 'S3c: armed by % — all % rows accounted for. Dropping.',
                 v_armed_by, v_total;

    -- S3b's own scaffolding goes first. `gtd_backfill_plan` SELECTs from
    -- `gtd_items`, so Postgres refuses to drop the table underneath it —
    -- measured, not predicted: this exact DROP failed with "cannot drop table
    -- gtd_items because other objects depend on it" the first time the S3c
    -- suite ran. Worth naming, because the failure would otherwise have
    -- surfaced while ARMED, mid-cutover, on a ladder that cannot roll back.
    -- The view and the function are S3b's tools; the job is over when the
    -- table is gone, and a preview of a table that no longer exists is just a
    -- broken object waiting to confuse somebody.
    DROP VIEW     IF EXISTS gtd_backfill_plan;
    DROP FUNCTION IF EXISTS gtd_backfill_to_pm(boolean);

    -- Then `gtd_waiting` before `gtd_items`: it holds the FK, so this order
    -- needs no CASCADE. That is deliberate — CASCADE would silently take
    -- anything ELSE that had grown a dependency on these tables, which is
    -- exactly the kind of thing a retirement should stop and ask about. If a
    -- future dependent appears, this DROP fails loudly and somebody looks.
    DROP TABLE IF EXISTS gtd_waiting;
    DROP TABLE IF EXISTS gtd_items;

    RAISE NOTICE 'S3c: gtd_items and gtd_waiting are retired. '
                 'The local project tree waits on slice 5; see this file''s header.';
END
$s3c$;

COMMENT ON FUNCTION gtd_retirement_drop() IS
    'WS-39 S3c. Drops gtd_items + gtd_waiting, but only when armed AND every '
    'row carries migrated_task_id. Inert otherwise. OWNER-GATE (work_plan.md §6 (f)).';

-- The one call. On every deploy until somebody arms it, this prints a notice
-- and returns.
SELECT gtd_retirement_drop();
