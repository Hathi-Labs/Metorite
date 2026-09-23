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

ALTER TABLE gtd_items ADD COLUMN IF NOT EXISTS attachments JSONB;
