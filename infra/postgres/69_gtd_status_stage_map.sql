-- 69_gtd_status_stage_map.sql — map ClickUp statuses → the 4 fixed Next-Actions
-- stages (task_manager_app.md).
--
-- What:
--   user_settings.status_stage_map — a JSON object {clickup_status: local_stage}
--       mapping each UNIQUE upstream status name (across every connected
--       ClickUp project) to one of the user's workflow_stages
--       (TODO / IN PROCESS / WAITING FOR / DONE). Keyed by the normalized
--       (trimmed, lower-cased) status name.
--
-- Why:  Different ClickUp projects carry different status vocabularies, and the
--       Next-Actions board should show ONLY the user's 4 fixed stages — not the
--       raw union of every upstream status (cluttered). This map is the
--       translation layer:
--         inbound  — a synced task groups into the stage its ClickUp status
--                    maps to (unmapped → auto-guessed by name, else first stage);
--         outbound — dragging a card to a stage writes back a ClickUp status
--                    from THAT task's own project which maps to the stage; if the
--                    project has no status mapped to the target stage, the move
--                    stays local (no upstream write).
--       The per-project status set is resolved on demand from ClickUp at
--       write-back time, so only the name→stage map needs storing here.
--
-- Empty object default: before the user (or the auto-guess seed) maps anything,
-- callers fall back to the name heuristic + first stage, so the board works.
-- Depends on: 48_task_manager_gtd.sql. Idempotent.

ALTER TABLE user_settings
    ADD COLUMN IF NOT EXISTS status_stage_map JSONB NOT NULL DEFAULT '{}'::jsonb;
