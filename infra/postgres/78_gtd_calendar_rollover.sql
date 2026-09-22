-- 78_gtd_calendar_rollover.sql — automatic nightly roll-over of incomplete
-- time-blocks (spec: calendar_timeboxing.md §6, roadmap P3).
--
-- What:
--   user_settings.timezone           IANA tz name (e.g. 'Asia/Kolkata') so the
--                                   server can compute each user's LOCAL day.
--   user_settings.auto_rollover      opt-out toggle (default on).
--   user_settings.last_rollover_date guard so the job rolls at most once per
--                                   local day (idempotent boundary trigger).
--   calendar_rollover_log                audit/history: which block moved, from→to.
-- Why: falling behind is timeboxing's #1 failure. The manual banner covers
--   intraday; this handles the day boundary automatically. It APPLIES (not a
--   proposal) from a background loop, so it needs server-side tz + prefs (the
--   calendar prefs from migration 77 are already stored).
-- Depends on: 48_task_manager_gtd.sql, 77_gtd_calendar_prefs.sql. ADDITIVE +
--   idempotent. getattr-style defaults keep pre-migration rows working.

ALTER TABLE user_settings
    ADD COLUMN IF NOT EXISTS timezone           TEXT DEFAULT 'UTC',
    ADD COLUMN IF NOT EXISTS auto_rollover       BOOLEAN DEFAULT true,
    ADD COLUMN IF NOT EXISTS last_rollover_date  DATE;

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
-- WARNING: this block names the gtd_rollover_log table, so it must survive a rename
-- sweep. Slice 1 added it BEFORE sweeping the tree, and the sweep rewrote
-- `ALTER TABLE gtd_rollover_log RENAME TO calendar_rollover_log` into `ALTER TABLE calendar_rollover_log RENAME TO calendar_rollover_log` -- a
-- silent no-op that left an upgraded database on the old tables with new
-- empty ones beside them. Sweep FIRST, add this SECOND. The old name is
-- spelled ONCE here, as a quoted literal passed to format().

DO $rename_calendar_rollover_log$
DECLARE
    old_name CONSTANT text := 'gtd_rollover_log';
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
         WHERE c.relname = old_name AND c.relkind = 'r'
           AND n.nspname = current_schema()
    ) AND to_regclass('public.calendar_rollover_log') IS NULL THEN
        EXECUTE format('ALTER TABLE %I RENAME TO %I', old_name, 'calendar_rollover_log');
        RAISE NOTICE 'renamed % -> calendar_rollover_log', old_name;
    END IF;
END
$rename_calendar_rollover_log$;

CREATE TABLE IF NOT EXISTS calendar_rollover_log (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     TEXT NOT NULL,
    item_id     UUID NOT NULL,
    title       TEXT,
    rolled_from TIMESTAMPTZ,
    rolled_to   TIMESTAMPTZ,
    created_at  TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_gtd_rollover_log_user
    ON calendar_rollover_log (user_id, created_at DESC);
