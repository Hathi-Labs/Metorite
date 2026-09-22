-- 57_gtd_workflow_stages_and_archive.sql — configurable Kanban stages for the
-- Next Actions board + task archiving (task_manager_app.md).
--
-- What:
--   gtd_items.workflow_stage — the task's stage on the local Kanban board
--       (TODO | IN PROCESS | WAITING FOR | DONE by default; user-configurable).
--       Distinct from provider_status (the connected tool's own native stage).
--       NULL = unset → the board treats it as the first configured stage.
--   gtd_items.archived_at    — when set, the task is archived: hidden from every
--       active view and only shown in the Archive view. Independent of DONE.
--   user_settings.workflow_stages — the user's ordered board stages (JSONB array
--       of strings). Default matches the built-in stage set.
-- Why:  the board grouped only by @context; a real workflow board (Jira/ClickUp
--       style) needs per-user configurable stages, and archiving keeps finished
--       clutter out of the active board while remaining recoverable.
-- Depends on: 48_task_manager_gtd.sql, 51_gtd_settings.sql. Idempotent.

ALTER TABLE gtd_items
    ADD COLUMN IF NOT EXISTS workflow_stage TEXT;

ALTER TABLE gtd_items
    ADD COLUMN IF NOT EXISTS archived_at TIMESTAMPTZ;

-- Fast archive filtering (active views exclude archived rows).
CREATE INDEX IF NOT EXISTS idx_gtd_items_archived
    ON gtd_items(user_id, archived_at);

ALTER TABLE user_settings
    ADD COLUMN IF NOT EXISTS workflow_stages JSONB
        NOT NULL DEFAULT '["TODO", "IN PROCESS", "WAITING FOR", "DONE"]'::jsonb;
