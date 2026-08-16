-- Metorite — Plugin Registry.
-- Stores Claude-style self-describing plugins.
-- Each plugin ships an ai-plugin.json manifest + openapi.yaml spec
-- served from a URL.  Tools are auto-generated from the OpenAPI spec
-- and injected into the agent's tool list at runtime.

CREATE TABLE IF NOT EXISTS plugins (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name            TEXT UNIQUE NOT NULL,          -- e.g. "stripe", "hubspot"
    label           TEXT NOT NULL,                 -- Human-readable name
    description     TEXT NOT NULL DEFAULT '',
    manifest_url    TEXT NOT NULL,                 -- URL to ai-plugin.json
    openapi_url     TEXT,                          -- URL to openapi.yaml (from manifest)
    logo_url        TEXT,                          -- Plugin logo URL
    auth_type       TEXT NOT NULL DEFAULT 'none',  -- 'oauth2' | 'api_key' | 'none'
    auth_config     JSONB NOT NULL DEFAULT '{}'::jsonb,
    manifest        JSONB NOT NULL DEFAULT '{}'::jsonb,   -- Cached manifest
    openapi_spec    JSONB NOT NULL DEFAULT '{}'::jsonb,   -- Cached & parsed OpenAPI spec
    tools_generated JSONB NOT NULL DEFAULT '[]'::jsonb,   -- Converted tool definitions
    enabled         BOOLEAN NOT NULL DEFAULT true,
    version         TEXT NOT NULL DEFAULT '0.0.0',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_plugins_enabled ON plugins (enabled);
CREATE INDEX IF NOT EXISTS idx_plugins_name ON plugins (name);
CREATE INDEX IF NOT EXISTS idx_plugins_auth_type ON plugins (auth_type);
