-- 215_projects_sealed.sql — a departed member's personal tree can be SEALED (D63, H-49)
--
-- What: `pm_projects.sealed_at`, a nullable timestamp, plus a partial index.
-- Why:  when a member leaves, the company is entitled to the WORK, not to the
--       WORKSPACE. The tree is retained and made invisible, never deleted.
-- Depends on: 146_projects.sql (the table), 147_projects_personal.sql
--             (`personal_owner`). Idempotent.
--
-- ## Why a column and not a delete
--
-- D63, taken 2026-08-26, settled this BEFORE the flow it governs existed —
-- deliberately, because the default somebody picks under time pressure while
-- building deactivation is the wrong way to decide what happens to a departed
-- colleague's private work.
--
-- The principle: **the company is entitled to the work, not to the workspace.**
-- So the tree splits by what was ALREADY shared, rather than being treated as
-- one object:
--
--   * tasks assigned to somebody else  → they keep seeing them (see below)
--   * tasks only ever theirs           → sealed. Retained, invisible, NEVER
--                                        deleted, for the same reason R6
--                                        applies everywhere else: you cannot
--                                        undo a delete.
--   * their `pm_task_personal` rows on TEAM tasks → untouched. The task is the
--     team's and needs reassigning. The overlay is theirs and stops being read.
--   * the Areas themselves → sealed with the tree.
--
-- ## ⚠️ The hand-over needs no data movement, and that is the point
--
-- `task_visibility_clause` has TWO arms: the project-grant closure, and an
-- `EXISTS` over `pm_task_assignees` that WS-27j added so delegating outward
-- stops being a silent no-op. The assignee arm does not consult the project.
--
-- So sealing the tree removes the GRANT arm and leaves the ASSIGNEE arm alone.
-- A task the departed member had assigned to a colleague stays visible to that
-- colleague, automatically, because it was never private from them. D63's
-- "handed over" is a consequence of the seal rather than a second operation —
-- no row moves, no ownership rewrite, nothing to get wrong halfway.
--
-- ## Measured on production 2026-09-23, before writing any of this
--
-- 1 personal root, 1 personal project, 2 tasks in it, and ZERO tasks assigned
-- to anybody but the owner. 4 members, every one `active`; `app_user.status`
-- has still only ever held `'active'`, exactly as D63 predicted.
--
-- Two guards already make the cross-assigned state unreachable going forward:
-- `assert_move_keeps_privacy` refuses team → personal and personal → somebody
-- else's personal, and `assert_assignable_here` refuses assigning a non-owner
-- inside a personal project. So the hand-over arm normally has nothing to do,
-- and the honest reading is that the SEAL is the feature. The arm still works
-- for rows that predate both guards.
--
-- ## Shape
--
-- Nullable with no default (R6 expand): every existing row means "not sealed"
-- without a backfill, and old code that never mentions the column keeps
-- working against the new schema. There is no `sealed_by` column here —
-- WHO sealed a tree, and who later opened it, belongs in the activity spine
-- where it can be read as history, not in a column that only holds the last
-- writer.

ALTER TABLE pm_projects
    ADD COLUMN IF NOT EXISTS sealed_at TIMESTAMPTZ;

COMMENT ON COLUMN pm_projects.sealed_at IS
    'D63: when this project was sealed on a member''s deactivation. '
    'NULL means live. A sealed project and its subtree leave every '
    'visibility clause, including the data:org:read one. Retained, never '
    'deleted; an owner-only logged action may reopen it.';

-- Partial, so the millions of live projects that will never carry the column
-- cost nothing. The read that wants this asks "is anything sealed for this
-- tenant", which is rare and small.
CREATE INDEX IF NOT EXISTS ix_pm_projects_sealed
    ON pm_projects (organization_id, sealed_at)
    WHERE sealed_at IS NOT NULL;
