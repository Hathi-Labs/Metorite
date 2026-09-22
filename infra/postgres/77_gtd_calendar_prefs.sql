-- 77_gtd_calendar_prefs.sql — calendar/timeboxing preferences that the day
-- grid + the AI planner reason over (spec: calendar_timeboxing.md §5).
--
-- What: per-user calendar prefs on user_settings —
--   day_start_hour / day_end_hour : the visible + plannable day window.
--   daily_capacity_mins           : soft focus-hours budget (overcommit flag).
--   buffer_mins                   : slack the planner leaves between blocks so
--                                   one overrun doesn't cascade.
--   energy_windows                : [{start_hour,end_hour,energy}] — the user's
--                                   peak/trough windows; the planner places
--                                   high-energy work in peak windows.
-- Why: timeboxing fails when it ignores capacity, energy and buffers. These
--   prefs let both the grid and the LLM planner respect real limits.
-- Depends on: 48_task_manager_gtd.sql (user_settings). ADDITIVE + idempotent;
--   getattr-style defaults keep pre-migration rows working.

ALTER TABLE user_settings
    ADD COLUMN IF NOT EXISTS day_start_hour      INT   DEFAULT 7,
    ADD COLUMN IF NOT EXISTS day_end_hour        INT   DEFAULT 22,
    ADD COLUMN IF NOT EXISTS daily_capacity_mins INT   DEFAULT 360,
    ADD COLUMN IF NOT EXISTS buffer_mins         INT   DEFAULT 0,
    ADD COLUMN IF NOT EXISTS energy_windows      JSONB DEFAULT '[]'::jsonb;
