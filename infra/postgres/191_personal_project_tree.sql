-- ============================================================================
-- 191_personal_project_tree.sql — a member's personal projects become a TREE.
--
-- What: re-keys `uq_pm_projects_personal_owner` so that `personal_owner`
--   identifies a member's whole private tree rather than a single row. The
--   uniqueness moves onto the ROOT alone.
--
-- Why: owner directive 2026-08-26 — "these should expressly be called personal
--   projects … each user should be able to have some sort of categorization …
--   these do not show up in the project management app but show up in the tasks
--   app." Those three sentences are one requirement, and this index is what
--   makes it true without adding a single concept.
--
--   Migration 147 made `personal_owner` unique across every row, because at the
--   time a member had exactly one personal project. So only a ROOT could carry
--   the column, and any child had to be created with `personal_owner IS NULL` —
--   which is precisely the value the Projects app treats as "this is team
--   work". A member's private categories would therefore have appeared on the
--   company board. Measured, not feared: `routes/projects/tree.py` selects
--   `WHERE {vis.project_clause()} AND personal_owner IS NULL`.
--
--   After this migration the column means **"this project is private to this
--   person"** at every depth, and `parent_project_id IS NULL` is what
--   distinguishes the one root from the categories under it. Depth is the
--   meaning; there is no `kind` column beside it, for migration 147's own
--   stated reason — "two columns that must agree are two columns that can
--   disagree".
--
-- ⚠️ The two queries that matter need NO change, and that is the point of
--   choosing this shape over a new column or a new table:
--     * `tree.py:152`      — `AND personal_owner IS NULL` now excludes the
--                            whole private tree, at every depth, from the
--                            Projects app.
--     * `personal.py:664`  — `OR lower(proj.personal_owner) = :who` now
--                            includes the whole private tree, at every depth,
--                            in the Tasks app.
--   One index change; both surfaces become correct at once.
--
-- ⚠️ ONE reader does change, and it is in this PR: `_load_personal_project`
--   asks for "my personal project" and must now say which one it means. It
--   gains `AND parent_project_id IS NULL`. Without that it would return an
--   arbitrary row of the tree — and its result is used as a WRITE TARGET, so a
--   capture could land in a category instead of the inbox.
--
-- Safety: this is a WIDENING. Every row that satisfied the old constraint
--   satisfies the new one (the new one constrains a strict subset — roots
--   only), so it cannot fail on existing data. The new index is created BEFORE
--   the old one is dropped, so there is no window in which two roots could be
--   minted for one member.
--
-- Idempotent per infra/postgres/README.md.
--
-- Depends on: 147_projects_personal.sql (personal_owner), 146_projects.sql
--   (parent_project_id).
-- Pinned by: tests/unit/test_personal_tree.py, tests/live/live_ws39_personal_tree.sql.
-- ============================================================================

-- New first. Partial on the root, and still case-folded because every read
-- compares folded (R10).
CREATE UNIQUE INDEX IF NOT EXISTS uq_pm_projects_personal_root
    ON pm_projects (lower(personal_owner))
    WHERE personal_owner IS NOT NULL AND parent_project_id IS NULL;

-- Then the old one. Dropping it is what allows a child to carry the column.
DROP INDEX IF EXISTS uq_pm_projects_personal_owner;

COMMENT ON COLUMN pm_projects.personal_owner IS
    'Non-null = this project is PRIVATE to this member, at any depth. '
    'The one row with parent_project_id IS NULL is their root ("Inbox"); '
    'children are their own categories. Unique per member on the ROOT only '
    '(uq_pm_projects_personal_root). The Projects app excludes every row '
    'carrying this column; the Tasks app includes every row carrying it.';
