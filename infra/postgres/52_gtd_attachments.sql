-- 52_gtd_attachments.sql — capture-time context attachments (photo/file/link).
--
-- What: attachments (uploaded file store: owner, name, mime, size, disk
--       path) + gtd_items.attachments JSONB — the per-item list of context
--       references: {kind: 'file'|'image'|'link', name, url, attachment_id?,
--       mime?, size?}. Links are JSONB-only; files/images also have a
--       attachments row backing the served bytes.
-- Why:  GTD capture wants context kept WITH the item ("for more context
--       later") — a photo of a whiteboard, a spec PDF, a URL.
-- Depends on: 48_task_manager_gtd.sql. Idempotent.

-- == The `gtd_` name is retired (WS-39 S8 PR 2, D73.3, 2026-09-23) ===========
--
-- The file registry survives the task-store drop, because My Tasks and
-- Projects both write it (migration 150 joins it to pm_tasks). A shared
-- registry takes a bare name, so it is now `attachments`.
--
-- THE RENAME LIVES IN THE FILE THAT CREATES THE TABLE. A fresh install makes
-- the new name directly. An older database is renamed with its rows, and the
-- CREATE below then skips. A replay finds the work done. The argument and the
-- fence are in `tests/unit/test_gtd_rename_upgrade.py`.
--
-- relkind = 'r' leaves a view that wears the old name alone. Index, constraint
-- and policy names keep their old spelling (D73.3).
--
-- The files on disk do not move. Each row stores its own path, and the upload
-- directory keeps its name (`GTD_ATTACHMENTS_DIR`).
--
-- WARNING: the block spells its OLD name once, as a quoted literal. A later
-- rename sweep must not rewrite it. Sweep FIRST, add the prologue SECOND.

DO $rename_attachments$
DECLARE
    old_name CONSTANT text := 'gtd_attachments';
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
         WHERE c.relname = old_name AND c.relkind = 'r'
           AND n.nspname = current_schema()
    ) AND to_regclass('public.attachments') IS NULL THEN
        EXECUTE format('ALTER TABLE %I RENAME TO %I', old_name, 'attachments');
        RAISE NOTICE 'renamed % -> attachments', old_name;
    END IF;
END
$rename_attachments$;

CREATE TABLE IF NOT EXISTS attachments (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id TEXT NOT NULL,
    name TEXT NOT NULL,
    mime TEXT,
    size_bytes BIGINT DEFAULT 0,
    path TEXT NOT NULL,                  -- server-local storage path
    created_at TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_gtd_attachments_user ON attachments(user_id);

-- Guarded since WS-39 S8 PR 2. Migration 217 drops gtd_items, and this file
-- re-runs on its own after that (its checksum changed with the prologue
-- above). A bare ALTER then fails on a table that is gone.
DO $s8_items_attachments$
BEGIN
    IF to_regclass('public.gtd_items') IS NOT NULL THEN
        ALTER TABLE gtd_items ADD COLUMN IF NOT EXISTS attachments JSONB;
    END IF;
END
$s8_items_attachments$;
