-- 98_gtd_day_templates.sql — flexible recurring calendar windows (spec:
-- (renumbered 94→98 to resolve a duplicate-prefix collision on main; additive)
-- calendar_focus_os.md ideal-week templates; user request 2026-07).
--
-- What: user_settings.day_templates — a JSONB list of recurring windows the AI
--   planner reasons over, so the day has shape without being rigid:
--     [{ days:[0..6], start_hour, end_hour, kind:'block'|'focus', label, theme }]
--   • days   : weekday numbers (0=Sun … 6=Sat); [] = every day.
--   • kind='block' : PROTECTED — no tasks are scheduled here (lunch, rest, gym,
--                    family, workout). The planner treats it as busy.
--   • kind='focus' : a THEMED window the planner prefers for a kind of work
--                    (deep work, calls, meetings, admin, R&D) — matching tasks
--                    are drawn into it and the LLM batches them there.
--   • label  : human name shown on the grid / in settings.
--   • theme  : for focus windows, the kind of work ("deep", "calls", …).
-- Why: people want to block recurring habits and reserve times for specific
--   task types — but flexibly, edited whenever. A single JSONB list keeps it
--   easy to add/change/remove without schema churn, alongside the simpler
--   energy-window + lunch prefs already present.
-- Depends on: 48_task_manager_gtd.sql, 77_gtd_calendar_prefs.sql. ADDITIVE.

ALTER TABLE user_settings
    ADD COLUMN IF NOT EXISTS day_templates JSONB DEFAULT '[]'::jsonb;
