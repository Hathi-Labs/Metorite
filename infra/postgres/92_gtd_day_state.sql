-- 92_gtd_day_state.sql — per-LOCAL-day Focus-OS state (spec:
-- calendar_focus_os.md §5, calendar_ai_review.md §4.1).
--
-- What: the ★ One Thing and the tomorrow-seeds that Focus Mode's rituals set.
--   These were localStorage-only, so server-side AI (the day planner, the chat
--   agent, a future morning digest) could not see the user's committed top
--   priority or their pre-picked next-day tasks. Persisting them here makes the
--   One Thing a first-class planning input everywhere and syncs it across
--   devices.
--     one_thing_id : the item the user committed as today's single priority.
--     seed_ids     : item ids picked during shutdown to pre-load THIS day's
--                    morning plan (yesterday writes tomorrow's row).
-- Keyed by (user_id, LOCAL day) so it rolls over naturally at midnight; the row
-- is created lazily the first time a day gets a One Thing or seeds.
-- Why: the calendar can only "smartly manage the day with AI" if the AI can see
--   what the user decided matters most — this table is that bridge.
-- Depends on: 48_task_manager_gtd.sql (gtd_items). ADDITIVE + idempotent.

-- == The `gtd_` name is retired (owner directive, 2026-09-21) ================
--
-- **THE RENAME LIVES IN THE FILE THAT CREATES THE TABLE, and that is what
-- makes it safe.** The obvious alternative -- one migration at the end that
-- renames -- breaks every earlier file on replay: an ALTER TABLE addresses a
-- name that is gone, and CREATE TABLE IF NOT EXISTS happily builds an empty
-- duplicate beside the real one. Both shapes were measured on 2026-09-21 and
-- both were abandoned. `tests/unit/test_people_rename_upgrade.py` carries the
-- full argument, and slice 1 (the People family) shipped on this mechanism.
--
-- One file answers for one table in all three states:
--
--   * Fresh install -- nothing to rename, the CREATE below makes the new
--     name directly.
--   * A database that predates this -- the old table is renamed WITH ITS
--     ROWS, and the CREATE below then finds the name taken and skips.
--   * Replay -- already renamed, the guard matches nothing. Idempotent.
--
-- relkind = 'r' so a view wearing the old name is left alone rather than
-- renamed into the table's place.
--
-- Index, constraint and POLICY names keep their old spelling. A rename does
-- not touch them, and no query names one.
--
-- WARNING: this block names the gtd_day_state table, so it must survive a rename
-- sweep. Slice 1 added it BEFORE sweeping the tree, and the sweep rewrote
-- `ALTER TABLE gtd_day_state RENAME TO calendar_day_state` into `ALTER TABLE calendar_day_state RENAME TO calendar_day_state` -- a
-- silent no-op that left an upgraded database on the old tables with new
-- empty ones beside them. Sweep FIRST, add this SECOND. The old name is
-- spelled ONCE here, as a quoted literal passed to format().

DO $rename_calendar_day_state$
DECLARE
    old_name CONSTANT text := 'gtd_day_state';
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
         WHERE c.relname = old_name AND c.relkind = 'r'
           AND n.nspname = current_schema()
    ) AND to_regclass('public.calendar_day_state') IS NULL THEN
        EXECUTE format('ALTER TABLE %I RENAME TO %I', old_name, 'calendar_day_state');
        RAISE NOTICE 'renamed % -> calendar_day_state', old_name;
    END IF;
END
$rename_calendar_day_state$;

CREATE TABLE IF NOT EXISTS calendar_day_state (
    user_id      TEXT NOT NULL,
    day          DATE NOT NULL,               -- the user's LOCAL calendar day
    one_thing_id UUID,                         -- the ★ One Thing for this day
    seed_ids     JSONB DEFAULT '[]'::jsonb,    -- ids seeded for this day's plan
    updated_at   TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY (user_id, day)
);
