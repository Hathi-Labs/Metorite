-- Metorite — Dynamic Agent Registry.
-- Replaces agents.json — user-registered agents persist in Postgres
-- so they survive git reset --hard, deploys, and reboots.
-- The old agents.json file is kept for backward-compatible reads
-- during migration; writes go exclusively to this table.

CREATE TABLE IF NOT EXISTS dynamic_agents (
    name              TEXT PRIMARY KEY,              -- e.g. "agent-sales-assistant"
    description       TEXT NOT NULL DEFAULT '',
    tags              JSONB NOT NULL DEFAULT '[]'::jsonb,
    status            TEXT NOT NULL DEFAULT 'live',
    agent_runtime     TEXT NOT NULL DEFAULT 'maf',   -- 'maf' | 'github-copilot'
    repo_url          TEXT,                          -- GitHub URL (null for local-path agents)
    repo_name         TEXT,                          -- "org/repo" slug
    local_path        TEXT,                          -- Absolute path (null for GitHub agents)
    integrations      JSONB NOT NULL DEFAULT '[]'::jsonb,
    optional_integrations JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_dynamic_agents_runtime ON dynamic_agents (agent_runtime);
CREATE INDEX IF NOT EXISTS idx_dynamic_agents_status  ON dynamic_agents (status);
