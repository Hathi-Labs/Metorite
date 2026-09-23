-- ============================================================================
-- 216_gtd_task_store_drop.sql — WS-39 S8 PR 2. The `gtd_*` task store goes.
--
-- Spec: project-docs/specs/my_tasks_cutover.md §4.3 (the table map) and §5 S8.
-- Decisions: D53.5 (three releases), D53.6 (what survives), D73.
--
-- ⚠️ THIS MIGRATION IS ONE-WAY. We cannot roll back (R6). It drops tables.
--
-- ⚠️ IT FAILS CLOSED. If any `gtd_items` row has no `migrated_task_id`, the
--   guard in migration 190 RAISES, this file stops, and the deploy stops with
--   it. Nothing is dropped and nothing is changed, because the whole file is
--   one transaction. That is the intended behaviour. Do not widen the guard.
--   Run `SELECT * FROM gtd_backfill_plan;` on the box and decide each row.
--
-- Steps, in order:
--
--   (0) Refuse when a `gtd_items` row holds a value in a column the backfill
--       never copied. The RAISE names the column and the row count.
--   (c) `wa_commitments.gtd_item_id` -> `task_id`. The copy goes FIRST,
--       because it reads `gtd_items.migrated_task_id`. Migration 211 was the
--       expand half. This is the contract half, and it drops the old column.
--   (a) Arm, then call `gtd_retirement_drop()` (190). It drops `gtd_items`
--       and `gtd_waiting`, or it refuses. Then assert that both are gone.
--   (b) Drop the rest of the store in foreign-key order, with no CASCADE:
--       `gtd_projects`, `gtd_folders`, `gtd_spaces`, `gtd_contexts`, then the
--       S3b/S3c scaffolding (`gtd_backfill_plan`, `gtd_backfill_to_pm`,
--       `gtd_retirement_arm`, `gtd_retirement_drop`).
--
-- ⚠️ THE ARM MOVED INTO THIS FILE, and that is a change to the runbook. The
--   spec's §6 step 11 made arming a hand INSERT on the box. Here the arm is a
--   row this file writes, so the act is reviewed code and not a hand step.
--   The data check does not move: 190's guard still counts the unmigrated
--   rows and refuses on one. What was a human's assertion ("nothing still
--   writes gtd_items") is now the merge of S8 PR 1 and PR 2, which remove
--   every reader and writer, and the fences that keep them removed.
--
-- WHAT THIS DOES NOT DROP (D53.6, and the §4.3 map):
--
--   * `user_settings`, `calendar_day_state`, `calendar_rollover_log`. Calendar
--     and member state, renamed in slice 2 (51, 78, 92).
--   * `people` and its four children. The People directory, slice 1.
--   * `attachments`, `my_tasks_horizons`, `my_tasks_reviews`. Renamed in their
--     creating migrations (52 and 48), in this same PR.
--   * `task_accounts`. It carries no `gtd_` prefix. H-151 does not name it.
--
-- Idempotent. Every step is guarded, so this file does the right thing on a
-- fresh ladder, on an upgraded box, and on a replay that ignores the ledger.
-- On a full replay, migration 48 builds empty `gtd_*` tables again, and this
-- file drops them again. An empty table has no unmigrated row.
--
-- Pinned by: tests/unit/test_gtd_backfill.py (the drop set, read as text, and
--   the ladder, live), tests/live/live_ws39_s8d.py (seeded upgrade and the
--   refusal, R8).
-- ============================================================================

BEGIN;

-- ── (0) Refuse to drop a value the backfill never copied ────────────────────
--
-- The backfill (212, the last definition) copies the title, the notes, the
-- disposition, context, energy, the estimate, the dates and `deleted_at`. It
-- copies nothing else. A row that holds a value in another column would lose
-- it here, and we cannot roll back (R6). So each column below must hold its
-- default, or this file RAISES and names the column and the row count.
--
-- `archived_at` is on the list. 212 maps `deleted_at` to the new store's
-- `archived_at`, and it never reads the old `archived_at` column.
-- `flexible` is exempt: the new store reads NULL as flexible.
-- Production passed this check by hand on 2026-09-23 (my_tasks_cutover.md
-- §5 S8). This block makes the check hold on every other box too.
--
-- Read through dynamic SQL, because a lone re-run of migration 48 builds a
-- `gtd_items` that lacks most of these columns.

DO $s8_uncopied$
DECLARE
    v_check record;
    v_rows  bigint;
BEGIN
    IF to_regclass('public.gtd_items') IS NULL THEN
        RETURN;
    END IF;

    FOR v_check IN
        SELECT * FROM (VALUES
            ('origin',          'origin IS NOT NULL AND origin NOT IN (''{}''::jsonb, ''null''::jsonb)'),
            ('attachments',     'attachments IS NOT NULL AND attachments NOT IN (''[]''::jsonb, ''null''::jsonb)'),
            ('sort_key',        'sort_key IS NOT NULL'),
            ('important',       'important'),
            ('leveraged',       'leveraged'),
            ('kept_mine',       'kept_mine'),
            ('deep_work',       'deep_work'),
            ('scheduled_start', 'scheduled_start IS NOT NULL'),
            ('scheduled_end',   'scheduled_end IS NOT NULL'),
            ('actual_start',    'actual_start IS NOT NULL'),
            ('actual_end',      'actual_end IS NOT NULL'),
            ('parent_item_id',  'parent_item_id IS NOT NULL'),
            ('archived_at',     'archived_at IS NOT NULL'),
            ('workflow_stage',  'workflow_stage IS NOT NULL'),
            ('assignees',       'assignees IS NOT NULL AND assignees NOT IN (''[]''::jsonb, ''null''::jsonb)'),
            ('horizon_id',      'horizon_id IS NOT NULL')
        ) AS c(col, predicate)
    LOOP
        CONTINUE WHEN NOT EXISTS (
            SELECT 1 FROM information_schema.columns
             WHERE table_schema = current_schema()
               AND table_name = 'gtd_items' AND column_name = v_check.col);
        EXECUTE format('SELECT count(*) FROM gtd_items WHERE %s', v_check.predicate)
           INTO v_rows;
        IF v_rows > 0 THEN
            RAISE EXCEPTION
                'S8 REFUSED: % gtd_items rows hold a value in %, and the backfill '
                'never copied that column. Dropping the table would lose it, and '
                'we cannot roll back (R6). Nothing was changed. Move or clear '
                'those values by hand, then deploy again.',
                v_rows, v_check.col;
        END IF;
    END LOOP;
END
$s8_uncopied$;


-- ── (c) The contract half of `wa_commitments.gtd_item_id` ───────────────────
--
-- A value moves only when it names a `pm_tasks` row that exists. `task_id`
-- carries a foreign key to `pm_tasks` (211), and `migrated_task_id` does not
-- (189), so an unchecked copy could fail on a task deleted since the move.
-- Two sources, in order: the moved row's pointer, then the value itself when
-- a writer already stored a `pm_tasks` id there. A value that names neither
-- points at nothing, and the NOTICE counts it.

DO $s8_commitments$
DECLARE
    v_moved  bigint := 0;
    v_direct bigint := 0;
    v_lost   bigint := 0;
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
         WHERE table_schema = current_schema()
           AND table_name = 'wa_commitments' AND column_name = 'gtd_item_id'
    ) THEN
        RAISE NOTICE 'S8: wa_commitments.gtd_item_id is already gone.';
        RETURN;
    END IF;

    IF to_regclass('public.gtd_items') IS NOT NULL THEN
        UPDATE wa_commitments k
           SET task_id = i.migrated_task_id
          FROM gtd_items i
          JOIN pm_tasks t ON t.id = i.migrated_task_id
         WHERE k.gtd_item_id = i.id
           AND k.task_id IS NULL;
        GET DIAGNOSTICS v_moved = ROW_COUNT;
    END IF;

    UPDATE wa_commitments k
       SET task_id = k.gtd_item_id
      FROM pm_tasks t
     WHERE t.id = k.gtd_item_id
       AND k.task_id IS NULL;
    GET DIAGNOSTICS v_direct = ROW_COUNT;

    SELECT count(*) INTO v_lost
      FROM wa_commitments
     WHERE gtd_item_id IS NOT NULL AND task_id IS NULL;

    RAISE NOTICE 'S8: wa_commitments.task_id set on % rows through gtd_items, '
                 '% rows directly. % rows point at no task and lose the pointer.',
                 v_moved, v_direct, v_lost;

    ALTER TABLE wa_commitments DROP COLUMN gtd_item_id;
END
$s8_commitments$;


-- ── (c2) `action_item.dispatch_ref` for a meeting action approved as a task ─
--
-- Migration 129's convention put the `gtd_items` id in `dispatch_ref` for
-- `kind = 'task'`. S8c now writes the `pm_tasks` id there. Rows written
-- before S8c keep the old id, which names nothing once the table is gone. So
-- this remaps them the same way as the commitments above: through
-- `migrated_task_id`, and only onto a `pm_tasks` row that exists. The
-- column is TEXT, so the other kinds (`sent:...`, `artifact:...`) never
-- match a uuid and stay as they are.

DO $s8_dispatch_ref$
DECLARE
    v_moved bigint := 0;
BEGIN
    -- A lone re-run of 48 builds a gtd_items without migrated_task_id (189
    -- adds it). That table is empty, so there is nothing to remap.
    IF to_regclass('public.gtd_items') IS NULL
       OR to_regclass('public.action_item') IS NULL
       OR NOT EXISTS (
            SELECT 1 FROM information_schema.columns
             WHERE table_schema = current_schema()
               AND table_name = 'gtd_items' AND column_name = 'migrated_task_id')
    THEN
        RETURN;
    END IF;

    UPDATE action_item a
       SET dispatch_ref = i.migrated_task_id::text
      FROM gtd_items i
      JOIN pm_tasks t ON t.id = i.migrated_task_id
     WHERE a.kind = 'task'
       AND a.dispatch_ref = i.id::text;
    GET DIAGNOSTICS v_moved = ROW_COUNT;

    RAISE NOTICE 'S8: action_item.dispatch_ref remapped on % task rows.', v_moved;
END
$s8_dispatch_ref$;


-- ── (a) Arm, and let 190's guard decide ─────────────────────────────────────
--
-- The row names this file and the S7 run record. 190's guard reads the arm
-- first and the data second. An unmigrated row makes it RAISE, which aborts
-- this transaction and the deploy.

DO $s8_drop$
BEGIN
    IF to_regclass('public.gtd_items') IS NULL THEN
        RAISE NOTICE 'S8: gtd_items is already gone.';
        RETURN;
    END IF;

    -- A re-run of migration 48 by itself, after this file ran, builds the
    -- store again, empty. 189's arm and 190's guard are gone by then. An
    -- empty table holds nothing to lose, so it goes. A table with rows is a
    -- writer nobody removed, and that stops the deploy.
    IF to_regclass('public.gtd_retirement_arm') IS NULL THEN
        IF EXISTS (SELECT 1 FROM gtd_items) THEN
            RAISE EXCEPTION
                'S8 REFUSED: gtd_items is back and holds rows, and the arm '
                'is gone. Something still writes the retired store. Find it.';
        END IF;
        DROP TABLE IF EXISTS gtd_waiting;
        DROP TABLE IF EXISTS gtd_items;
        RETURN;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM gtd_retirement_arm
         WHERE armed_by = 'migration 216 (WS-39 S8, D73)'
    ) THEN
        INSERT INTO gtd_retirement_arm (armed_by, note)
        VALUES (
            'migration 216 (WS-39 S8, D73)',
            'Armed by reviewed code, not by hand. S7 run record '
            '(my_tasks_cutover.md): backfill applied 2026-09-23 00:21 UTC, '
            'gtd_backfill_plan returned zero rows, TASKS_LENS on since 00:29 UTC. '
            'S8 PR 1 and PR 2 removed every reader of the gtd_ task store.'
        );
    END IF;

    PERFORM gtd_retirement_drop();

    IF to_regclass('public.gtd_items') IS NOT NULL
       OR to_regclass('public.gtd_waiting') IS NOT NULL THEN
        RAISE EXCEPTION
            'S8 REFUSED: gtd_retirement_drop() returned and the task store is '
            'still here. Nothing was dropped. Read the NOTICE above it.';
    END IF;
END
$s8_drop$;


-- ── (b) The rest of the store, in foreign-key order ─────────────────────────
--
-- No CASCADE, on purpose. A dependent nobody named makes a DROP fail loudly,
-- and the transaction takes back everything above it.
--
--   gtd_projects  references gtd_spaces, gtd_folders, my_tasks_horizons and
--                 task_accounts. Nothing references it now that gtd_items is
--                 gone.
--   gtd_folders   references gtd_spaces.
--   gtd_spaces    referenced by the two above only.
--   gtd_contexts  no foreign keys either way (§4.5: contexts are derived).
--
-- ⚠️ These four tables must be EMPTY. The backfill turned a member's LOCAL
-- projects into Areas, but it never deleted the old rows, and it copied
-- nothing from spaces, folders or contexts. A row here is data 216 would
-- lose, so it RAISES and names the table. Production held 0 rows in all
-- four on 2026-09-23 (my_tasks_cutover.md §5 S8).
--
-- 190's function already drops the view and the backfill when it fires. The
-- two lines below cover a database where it never fired, such as a replay.

DO $s8_tree_empty$
DECLARE
    v_table text;
    v_rows  bigint;
BEGIN
    FOREACH v_table IN ARRAY ARRAY['gtd_projects', 'gtd_spaces', 'gtd_folders',
                                   'gtd_contexts']
    LOOP
        CONTINUE WHEN to_regclass('public.' || v_table) IS NULL;
        EXECUTE format('SELECT count(*) FROM %I', v_table) INTO v_rows;
        IF v_rows > 0 THEN
            RAISE EXCEPTION
                'S8 REFUSED: % holds % rows, and dropping it would lose them. '
                'We cannot roll back (R6). Nothing was changed. Confirm each '
                'row reached the one store, delete it by hand, then deploy again.',
                v_table, v_rows;
        END IF;
    END LOOP;
END
$s8_tree_empty$;

DROP VIEW     IF EXISTS gtd_backfill_plan;
DROP FUNCTION IF EXISTS gtd_backfill_to_pm(boolean);

DROP TABLE IF EXISTS gtd_projects;
DROP TABLE IF EXISTS gtd_folders;
DROP TABLE IF EXISTS gtd_spaces;
DROP TABLE IF EXISTS gtd_contexts;

-- The arm and its guard go last. Their one job is done.
DROP TABLE    IF EXISTS gtd_retirement_arm;
DROP FUNCTION IF EXISTS gtd_retirement_drop();

COMMIT;
