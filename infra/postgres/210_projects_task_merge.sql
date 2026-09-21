-- ============================================================================
-- 210_projects_task_merge.sql — fold one task into another
-- ============================================================================
-- Spec: project-docs/specs/project_management_app.md §3.4 / §11.18.
-- Owner request, 2026-09-21: *"enabling us to merge multiple tasks into one.
-- So if I right click on a task, I should be able to merge it into another
-- task. And the data of both the tasks are appropriately combined."*
--
-- What: three columns on `pm_tasks` recording that this task's content now
-- lives somewhere else, and one widened CHECK so the timeline can say so.
--
-- ⚠️ **A MERGED TASK IS AN ARCHIVED TASK. The CHECK below is that sentence,
-- and it is the whole design.**
--
-- The first draft hid merged tasks with a rule of their own — absent from
-- every list, board and count. The owner found the hole in it immediately:
-- *"what happens when we want to delete or archive a task? There is no way
-- to do that because it'll be unseen in the UI."* Exactly right. A row that
-- nothing can list is a row nothing can manage, and they accumulate.
--
-- So merging does not invent a second kind of hidden. It ARCHIVES, which
-- already means "out of every view, count and report, and reachable from the
-- Archived shelf" (D-PM-34, built 2026-09-20). Everything follows for free:
--
--   * every existing `archived_at IS NULL` clause excludes it, and no read
--     anywhere needs to learn a second exclusion;
--   * the Archived filter lists it, with its own count;
--   * Delete and Unarchive already work on anything on that shelf.
--
-- `pm_tasks_merged_is_archived` makes that unbreakable rather than
-- remembered. A future endpoint cannot set the pointer and forget the shelf,
-- which is the one mistake that would put a task beyond reach.
--
-- ⚠️ **ON DELETE SET NULL, not CASCADE.** Deleting the task somebody merged
-- INTO must not delete the stub that points at it — the stub is a record that
-- work happened, and its own history is still readable. It simply stops
-- redirecting. Migration 208 took the same line for a comment's parent, for
-- the same reason: a cascade here destroys a row its owner never touched.
--
-- ⚠️ **No `merged_from` column, on purpose.** "Which tasks were merged into
-- me" is the reverse of this pointer and is one indexed read; storing it
-- twice is two places to disagree. The index below is what makes that read
-- cheap.
--
-- R5: no new table, no new connection site. Three columns on a table that has
-- carried `organization_id` since 161, so a stub inherits the tenant of the
-- task it already was — there is no path by which a merge moves a row between
-- tenants, and `merge` refuses a cross-PROJECT merge long before tenancy
-- could come into it.
--
-- R6 EXPAND: all three nullable, no default, no backfill. NULL means "never
-- merged", which is the honest value for every row written before today.
-- Old code that never selects them keeps working, which is what makes this
-- safe to apply before the gateway restarts.
--
-- Idempotent per infra/postgres/README.md. Depends on 146_projects.sql,
-- which creates pm_tasks (archived_at included) and pm_activities.
-- Fence: tests/unit/test_projects_merge.py, tests/live/live_task_merge.py.
-- ============================================================================

BEGIN;

ALTER TABLE pm_tasks
    ADD COLUMN IF NOT EXISTS merged_into_task_id UUID
        REFERENCES pm_tasks (id) ON DELETE SET NULL;
ALTER TABLE pm_tasks ADD COLUMN IF NOT EXISTS merged_at TIMESTAMPTZ;
ALTER TABLE pm_tasks ADD COLUMN IF NOT EXISTS merged_by TEXT;

DO $$
BEGIN
    -- A task is not merged into itself. The only cycle a single pointer can
    -- form on its own; the longer ones are refused in `merge_tasks`, which
    -- can walk the chain.
    ALTER TABLE pm_tasks
        ADD CONSTRAINT pm_tasks_merged_is_not_self
        CHECK (merged_into_task_id IS NULL OR merged_into_task_id <> id);
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$
BEGIN
    -- 🔴 THE invariant. A merged task is on the archive shelf, so it can
    -- always be found, unarchived or deleted. See the header.
    ALTER TABLE pm_tasks
        ADD CONSTRAINT pm_tasks_merged_is_archived
        CHECK (merged_into_task_id IS NULL OR archived_at IS NOT NULL);
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

-- "What was merged into this task" and "follow this redirect" are both this
-- index. Partial, because the column is NULL on almost every row for ever.
CREATE INDEX IF NOT EXISTS idx_pm_tasks_merged_into
    ON pm_tasks (merged_into_task_id)
    WHERE merged_into_task_id IS NOT NULL;

-- ── The timeline needs a word for it ────────────────────────────────────
--
-- A merge is not a `field_change` and not a `system` event. It is a thing a
-- person did to two tasks, it is written on BOTH of them, and somebody
-- reading either one later has to be able to see it. `record_activity`
-- refuses a type this CHECK does not list — the trap that once made every
-- attachment upload answer 422 — so the vocabulary is widened here.
ALTER TABLE pm_activities DROP CONSTRAINT IF EXISTS pm_activities_type_check;
ALTER TABLE pm_activities
    ADD CONSTRAINT pm_activities_type_check
    CHECK (type IN ('comment', 'status_change', 'field_change', 'link',
                    'assignment', 'agent_run', 'sync', 'system',
                    'attachment', 'mention', 'merge'));

COMMENT ON COLUMN pm_tasks.merged_into_task_id IS
    'The task this one was folded into, or NULL. Its content (comments, '
    'history, attachments, assignees, tags, watchers, subtasks) has MOVED '
    'there and does not exist here any more. A row carrying this is always '
    'archived too — pm_tasks_merged_is_archived — so it stays reachable from '
    'the Archived shelf, where Delete and Unarchive work on it as on '
    'anything else. Unarchiving clears this pointer: the task is then no '
    'longer merged, though its content stays where it went.';

COMMIT;
