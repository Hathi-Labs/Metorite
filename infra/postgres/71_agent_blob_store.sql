-- Agent workspace blob store (Part 2) — durable, authoritative storage for the
-- three MAF agent folders (agent-data/, inputs/, outputs/).
--
-- Model (mirrors how Mem0 works): Postgres is the SOURCE OF TRUTH; the on-disk
-- workspace under {agents_clone_dir}/repos/{agent} is a rehydratable cache. A
-- wiped volume or a migrated box restores its files from here. The file manager,
-- chat, and artifacts apps keep reading the disk workspace unchanged — the store
-- sits behind it (write-through on writes, fault-in on read miss, rehydrate on
-- load).
--
-- Two tables:
--   agent_blob         — current content of every live file, keyed (agent, path).
--   agent_file_history — append-only log of every UNIQUE version (by sha256) an
--                        agent created or modified over time, so we can track and
--                        directly access the full history of any file.
--
-- The same schema is portable to MAF agents on a second tenant deployment
-- (agent_name is the only tenant/agent key; no Metorite-specific coupling).

-- ── Current content — one row per live file ────────────────────────────────
CREATE TABLE IF NOT EXISTS agent_blob (
    agent_name  TEXT        NOT NULL,
    -- Workspace-relative POSIX path, e.g. "agent-data/memory.md",
    -- "outputs/reports/q3.html", "inputs/photo.png".
    path        TEXT        NOT NULL,
    -- Which of the three visible folders this file lives under (fast filtering
    -- for the file manager + promote/move logic). Derived from path's first seg.
    folder      TEXT        NOT NULL
                            CHECK (folder IN ('agent-data', 'inputs', 'outputs')),
    content     BYTEA       NOT NULL,
    sha256      TEXT        NOT NULL,
    size        BIGINT      NOT NULL,
    mime_type   TEXT        NOT NULL DEFAULT 'application/octet-stream',
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (agent_name, path)
);

CREATE INDEX IF NOT EXISTS agent_blob_agent_folder_idx
    ON agent_blob (agent_name, folder, updated_at DESC);

-- ── Append-only version history — every unique file version over time ──────
-- One row per (agent, path, sha256) — a new row only when content actually
-- changes (same-sha rewrites are deduped by the unique index). This is the
-- "track every unique file the agent creates or modifies" record; each row is a
-- directly retrievable version.
CREATE TABLE IF NOT EXISTS agent_file_history (
    id          BIGSERIAL   PRIMARY KEY,
    agent_name  TEXT        NOT NULL,
    path        TEXT        NOT NULL,
    folder      TEXT        NOT NULL,
    sha256      TEXT        NOT NULL,
    size        BIGINT      NOT NULL,
    mime_type   TEXT        NOT NULL DEFAULT 'application/octet-stream',
    -- 'create' | 'modify' | 'delete' | 'promote' (inputs→agent-data move).
    action      TEXT        NOT NULL DEFAULT 'modify',
    -- Provenance: which run/session/actor produced this version (nullable — a
    -- user upload via the app has no run_id).
    run_id      TEXT,
    session_id  TEXT,
    actor       TEXT        NOT NULL DEFAULT 'agent',   -- 'agent' | 'user' | 'system'
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Dedupe: the same content (sha) written to the same path is one version, not
-- many. A genuine change (new sha) is a new row. Deletes carry a sentinel sha so
-- they don't collide with a prior content version.
CREATE UNIQUE INDEX IF NOT EXISTS agent_file_history_unique_version_idx
    ON agent_file_history (agent_name, path, sha256, action);

CREATE INDEX IF NOT EXISTS agent_file_history_agent_path_idx
    ON agent_file_history (agent_name, path, created_at DESC);

CREATE INDEX IF NOT EXISTS agent_file_history_agent_recent_idx
    ON agent_file_history (agent_name, created_at DESC);
