-- Metorite — MCP Server Registry.
-- Stores Model Context Protocol server configurations.
-- MCP servers self-describe their tools at connection time;
-- the executor queries this table at agent-run time and injects
-- matching servers into GitHubCopilotAgent.mcp_servers.

CREATE TABLE IF NOT EXISTS mcp_servers (
    name         TEXT PRIMARY KEY,              -- e.g. "postgres", "brave-search"
    label        TEXT NOT NULL,                 -- Human-readable name
    description  TEXT NOT NULL DEFAULT '',
    transport    TEXT NOT NULL DEFAULT 'http-sse',  -- 'stdio' | 'http-sse'
    command      TEXT,                          -- stdio: e.g. 'npx -y @modelcontextprotocol/server-postgres'
    url          TEXT,                          -- http-sse: e.g. 'https://mcp.brave.com/sse'
    env_vars     JSONB NOT NULL DEFAULT '{}'::jsonb,   -- env vars for the server process
    headers      JSONB NOT NULL DEFAULT '{}'::jsonb,   -- HTTP headers (auth tokens, etc.)
    agent_scope  JSONB NOT NULL DEFAULT '["*"]'::jsonb, -- ["*"] or ["agent-sales", ...]
    enabled      BOOLEAN NOT NULL DEFAULT true,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT mcp_servers_transport_check CHECK (
        (transport = 'stdio' AND command IS NOT NULL) OR
        (transport = 'http-sse' AND url IS NOT NULL)
    )
);

CREATE INDEX IF NOT EXISTS idx_mcp_servers_enabled ON mcp_servers (enabled);
CREATE INDEX IF NOT EXISTS idx_mcp_servers_transport ON mcp_servers (transport);

-- No seed rows: example MCP servers are not provisioned. The table is kept so
-- existing code paths (executor injection, integrations CRUD) keep working;
-- servers can be registered via the API/UI if/when MCP is adopted. (The prior
-- seed shipped invalid rows — http-sse with a NULL url — which violated
-- mcp_servers_transport_check and broke idempotent migration replays.)
