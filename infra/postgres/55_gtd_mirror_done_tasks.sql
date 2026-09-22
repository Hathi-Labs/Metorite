-- 55_gtd_mirror_done_tasks.sql — control whether completed provider tasks are
-- mirrored into the local board (task_manager_app.md §5.1 dual-source model).
--
-- What: one per-user boolean on user_settings —
--       mirror_done_tasks — when TRUE, the sync imports already-completed
--                          provider tasks as SYNCED/DONE rows. When FALSE
--                          (default), NEW closed tasks are skipped so a
--                          connected workspace's large completed backlog can't
--                          swamp the working views. Existing mirrored rows
--                          still flip to DONE when closed upstream regardless.
-- Why:  a freshly-connected ClickUp workspace can carry hundreds of closed
--       tasks; importing them all pushes the user's own LOCAL/open items past
--       the list row cap. Default FALSE keeps the board lean; the user can opt
--       back in from Settings. Pairs with the "all" view excluding DONE and the
--       LOCAL-first list ordering.
-- Depends on: 51_gtd_settings.sql. Idempotent.

ALTER TABLE user_settings
    ADD COLUMN IF NOT EXISTS mirror_done_tasks BOOLEAN NOT NULL DEFAULT false;
