-- 97_gtd_planning_prefs.sql — the "how should the AI organize my day" prefs
-- (renumbered 93→97 to resolve a duplicate-prefix collision on main; additive)
-- (spec: calendar_ai_review.md §4; calendar_focus_os.md). The planner used to
-- fill the day wall-to-wall with focus work; these make it plan like a human —
-- with a configurable philosophy, breaks between long focus runs, and an
-- optional protected lunch.
--
-- What (all on user_settings, additive + idempotent):
--   planning_prompt      free-text standing instruction the LLM planner obeys
--                        every time ("leave breathing room, batch calls, protect
--                        deep work…"). NULL → the app's sensible default is used.
--   max_focus_run_mins   insert a break after this many continuous focus minutes
--                        (default 90). 0 disables auto-breaks.
--   break_mins           the length of that inserted break (default 10).
--   lunch_start_hour /   an optional protected lunch window (local hours); the
--   lunch_end_hour       planner won't book over it. NULL = no protected lunch.
-- Why: a calendar that ignores breaks, whitespace and lunch treats the user
--   like a machine. The per-run energy note already personalises a single day;
--   planning_prompt personalises EVERY day, and the break/lunch geometry makes
--   the packer leave room to be human.
-- Depends on: 48_task_manager_gtd.sql, 77_gtd_calendar_prefs.sql.

ALTER TABLE user_settings
    ADD COLUMN IF NOT EXISTS planning_prompt    TEXT,
    ADD COLUMN IF NOT EXISTS max_focus_run_mins INT  DEFAULT 90,
    ADD COLUMN IF NOT EXISTS break_mins         INT  DEFAULT 10,
    ADD COLUMN IF NOT EXISTS lunch_start_hour   INT,
    ADD COLUMN IF NOT EXISTS lunch_end_hour     INT;
