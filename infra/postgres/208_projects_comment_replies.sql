-- ============================================================================
-- 208_projects_comment_replies.sql — a comment can answer another comment
-- ============================================================================
-- Spec: project-docs/specs/project_management_app.md §3.8 (the activity spine).
-- Owner request, 2026-09-21: *"enable threaded comments in the sense that we
-- should be able to reply to certain comments. Limit the depth. I think just
-- one layer of reply should be fine."*
--
-- What: `pm_activities.parent_id`, a self-reference. A row carrying one is a
-- REPLY to the comment it names.
--
-- ⚠️ **ONE LEVEL, and the depth cap is APPLICATION code, not a constraint.**
-- Postgres cannot express "the row I point at must itself point at nothing"
-- in a CHECK — a CHECK sees one row. A trigger could, and buying a trigger
-- here would put half the rule in SQL and half in Python, which is the shape
-- that rots. `activities.py::add_comment` owns the whole rule and
-- `tests/unit/test_projects_comments.py` is its fence. What this migration
-- DOES enforce is the pair a constraint can see: a row is not its own parent,
-- and a parent must exist.
--
-- ⚠️ **NO CASCADE on the reply, on purpose, and this is the interesting
-- ruling.** `ON DELETE CASCADE` would read as the tidy choice. It is the
-- wrong one twice:
--
--   * A comment delete in this product is a SOFT delete (`deleted_at`), which
--     a foreign key never sees. So the cascade would only fire on a hard row
--     removal, and would not do the thing its name suggests.
--   * Making it fire would mean one person deleting their own comment silently
--     destroys OTHER people's replies under it. Nobody asked for that, and it
--     is not recoverable.
--
-- So the parent is `ON DELETE SET NULL`: if the root ever is hard-deleted, its
-- replies survive as ordinary top-level comments. The client already handles
-- that shape, because a soft-deleted root is withheld from the timeline and
-- leaves exactly the same orphan — `activityStream.ts::threadComments` promotes
-- a reply whose parent is not in the page, and its suite pins it.
--
-- ⚠️ **A reply is a COMMENT answering a COMMENT, never a system event.** No
-- constraint can say that either (the type lives on the other row), so it is
-- the same application rule and the same fence. The index below is why the
-- check is cheap: one indexed read per posted reply.
--
-- R5: no new table and no new connection site. A column on `pm_activities`,
-- which has carried `organization_id` since 161 and whose two triggers fill it
-- from the parent task or project — a reply inherits its tenant from its own
-- task exactly as its root did, so there is no path by which a reply lands in
-- a tenant its task does not have.
--
-- R6 EXPAND: nullable, no default, no backfill. NULL is the honest value for
-- every row written before today and for every comment that answers nothing.
-- Old code that never selects the column keeps working, which is what makes
-- this safe to apply before the gateway restarts.
--
-- Idempotent per infra/postgres/README.md. Depends on 146_projects.sql
-- (pm_activities). Fence: tests/unit/test_projects_comments.py.
-- ============================================================================

BEGIN;

ALTER TABLE pm_activities
    ADD COLUMN IF NOT EXISTS parent_id UUID
        REFERENCES pm_activities (id) ON DELETE SET NULL;

-- A row is not its own parent. The only cycle a one-level thread can form,
-- and the one a constraint can see.
DO $$
BEGIN
    ALTER TABLE pm_activities
        ADD CONSTRAINT pm_activities_parent_is_not_self
        CHECK (parent_id IS NULL OR parent_id <> id);
EXCEPTION
    WHEN duplicate_object THEN NULL;
END $$;

-- Reading one thread is "every row whose parent is this one", and posting a
-- reply is one lookup of the named parent. Both are this index.
CREATE INDEX IF NOT EXISTS idx_pm_activities_parent_id
    ON pm_activities (parent_id)
    WHERE parent_id IS NOT NULL;

COMMENT ON COLUMN pm_activities.parent_id IS
    'The comment this comment answers, or NULL for a top-level entry. '
    'ONE level only: the depth cap, and the rule that both ends must be '
    'comments on the same task, are enforced in activities.py::add_comment '
    'because a CHECK cannot read the row it points at. ON DELETE SET NULL, '
    'not CASCADE — deleting a comment must not destroy other people''s '
    'replies, and a soft delete would not fire a cascade anyway.';

COMMIT;
