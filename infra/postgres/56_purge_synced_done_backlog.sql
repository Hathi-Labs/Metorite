-- 56_purge_synced_done_backlog.sql — one-time cleanup of the completed-task
-- backlog that a freshly-connected ClickUp workspace imported before the
-- "don't mirror completed tasks" default landed (55_gtd_mirror_done_tasks.sql).
--
-- What: DELETE mirrored rows that are already completed. Scoped strictly to
--       source <> 'LOCAL' (never touches tasks captured in Metorite) AND
--       disposition = 'DONE'. gtd_waiting cascades (ON DELETE CASCADE); DONE
--       rows carry no open waiting record anyway.
-- Why:  ~500 completed ClickUp tasks were pushing the user's own LOCAL/open
--       items past the list row cap. With done-mirroring now OFF by default,
--       future syncs won't re-import these; this clears the ones already stored.
-- Idempotent: re-running deletes nothing once the backlog is gone. A user who
--       later opts INTO mirror_done_tasks and runs a FULL sync can re-import.
-- Depends on: 48_task_manager_gtd.sql, 55_gtd_mirror_done_tasks.sql.

DELETE FROM gtd_items
 WHERE source <> 'LOCAL'
   AND disposition = 'DONE';
