-- ============================================================================
-- 422_task_link_affects.sql — the link type `affects`, with a note (SW-25)
-- ============================================================================
-- Spec: project-docs/specs/agent_coordination.md (SW-25) · R5 · R6.
--
-- An affects note is a `pm_task_links` row of the type `affects`. The source
-- task says: "my change affects the target task". The note is the short text
-- that tells the holder of the target task what changes.
--
-- Three changes to `pm_task_links`:
--   1. The CHECK of `link_type` accepts `affects`.
--   2. `note` holds the text. It is NULL for every old row. At most 500
--      characters.
--   3. `note_read_at` is the time that an agent read the note with
--      `get_task_context`. NULL means that no agent has read it yet. `my_work`
--      lists the note while it is NULL.
--
-- No new table, so `test_tenant_coverage.py` changes nothing. The table has
-- its `organization_id` and its policy since 161.
--
-- Expand only. The CHECK gets wider, and the two columns are nullable with no
-- default. Old code never names the columns, and it never writes `affects`.
-- The CHECK name is the one that Postgres gave in 146.
--
-- Depends on: 146_projects.sql, 161_projects_tenancy.sql.
-- ============================================================================

BEGIN;

ALTER TABLE pm_task_links DROP CONSTRAINT IF EXISTS pm_task_links_link_type_check;
ALTER TABLE pm_task_links ADD CONSTRAINT pm_task_links_link_type_check
    CHECK (link_type IN ('blocks', 'relates_to', 'duplicates', 'affects'));

ALTER TABLE pm_task_links ADD COLUMN IF NOT EXISTS note TEXT;
ALTER TABLE pm_task_links ADD COLUMN IF NOT EXISTS note_read_at TIMESTAMPTZ;

ALTER TABLE pm_task_links DROP CONSTRAINT IF EXISTS pm_task_links_note_len;
ALTER TABLE pm_task_links ADD CONSTRAINT pm_task_links_note_len
    CHECK (note IS NULL OR char_length(note) <= 500);

-- `my_work` reads the unread notes of a few target tasks.
CREATE INDEX IF NOT EXISTS idx_pm_task_links_unread_note
    ON pm_task_links (target_task_id)
 WHERE link_type = 'affects' AND note_read_at IS NULL;

COMMIT;
