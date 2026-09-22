-- 68_gtd_item_priority_flags.sql — the prioritization matrix inputs
-- (task_manager_app.md). Two manual judgment flags per task + a "kept mine"
-- dismissal for the delegate/schedule suggestion.
--
-- What:
--   gtd_items.important — manual: something stalls/breaks if I skip this
--       (downside / obligation). Default false: an untagged task hasn't
--       *earned* importance.
--   gtd_items.leveraged — manual: asymmetric 100x upside (an investor call, a
--       grant, a key hire). The scarce flag — most tasks are false.
--   gtd_items.kept_mine — the user dismissed the "consider delegating /
--       scheduling" suggestion for this task ("no, this one's mine"), so the
--       hint stops nagging. Independent of the flags above.
--
-- Urgency is NOT stored: it's derived from due_at (overdue or within the
-- configured window) at read time, so it can never go stale. The 8-cell matrix
-- label (Founder Fire … Eliminate) is likewise computed from
-- important × urgent × leveraged, never stored — it's a projection.
--
-- Why:  GTD deliberately leaves "priority" (its 4th engage criterion) to
--       intuition. This adds a lightweight, explicit weight axis — important
--       (downside) and leveraged (upside) as *separate* dimensions so
--       high-leverage non-urgent work competes with fires instead of losing to
--       them — while urgency stays automatic.
-- Depends on: 48_task_manager_gtd.sql. Idempotent.

ALTER TABLE gtd_items
    ADD COLUMN IF NOT EXISTS important BOOLEAN NOT NULL DEFAULT false;

ALTER TABLE gtd_items
    ADD COLUMN IF NOT EXISTS leveraged BOOLEAN NOT NULL DEFAULT false;

ALTER TABLE gtd_items
    ADD COLUMN IF NOT EXISTS kept_mine BOOLEAN NOT NULL DEFAULT false;

-- The urgency window (hours from now within which a due task counts as urgent).
-- A per-user setting so the threshold is tunable without a code change; default
-- 48h (overdue or due within two days).
ALTER TABLE user_settings
    ADD COLUMN IF NOT EXISTS urgent_window_hours INTEGER NOT NULL DEFAULT 48;
