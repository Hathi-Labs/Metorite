-- ============================================================================
-- 447_studio_mcp_tool_approvals.sql — the approved text of each MCP tool of
-- an org (WS-STU B1b-3, task 3280).
--
-- What: one new table. `studio_mcp_tool_approvals` holds one row for each
--       server and tool that an admin of the org approved: the SHA-256 of
--       the approved text (the tool name, its cleaned description and its
--       hints), who approved it and when.
--       gateway/studio/mcp_approvals.py is the one module that runs SQL on it.
-- Why:  ai_studio.md §14 B1b-3, the tool-poisoning gate. A server can change
--       the text of a tool after an admin connected it. A tool whose text
--       differs from the approved text is `changed: true` in the catalog. It
--       is class B there, and the live read of a preview refuses it, until
--       an admin approves the new text.
-- Depends on: 130_org_access_control.sql (organization).
--
-- NO SECRET AND NO URL. A row holds the server key, the tool name, a hash
-- and a member address. The description itself is not stored.
--
-- A row is keyed by the server NAME. A server that another connection
-- replaced keeps its name, so its old rows stay. The text of its tools then
-- hashes the same only when the text is the same. A delete of a server does
-- not delete its rows (B1b-3 decision).
--
-- Expand only (R6): one new table and one index. No rename, no drop, and no
-- change of an old object. Idempotent: CREATE TABLE IF NOT EXISTS, CREATE
-- UNIQUE INDEX IF NOT EXISTS, and an RLS block guarded on
-- relforcerowsecurity.
-- Tenant-scoped (R5): `organization_id`, ENABLE and FORCE row level
-- security, and one policy with USING and WITH CHECK, as 441 does.
-- `REFERENCES` comes before `DEFAULT`, because the tenancy ratchet in
-- tests/unit/test_tenancy_boundary.py matches the two with no comma between.
-- The org of a new row comes from the DEFAULT, the bound tenant. The writer
-- never names it.
--
-- The number is taken at build time (R1): 447 was booked for this task.
-- Check it again at merge. Pinned by tests/unit/test_studio_mcp_approvals.py
-- and tests/unit/test_studio_mcp_approvals_r8.py, which find this file by
-- CONTENT, never by number (R1).
-- ============================================================================

BEGIN;

CREATE TABLE IF NOT EXISTS studio_mcp_tool_approvals (
    organization_id  UUID NOT NULL
                     REFERENCES organization (id) ON DELETE CASCADE
                     DEFAULT current_setting('app.tenant_id', true)::uuid,
    id               UUID NOT NULL DEFAULT gen_random_uuid(),
    -- The server key: `connections/mcp.NAME_RE`.
    server           TEXT NOT NULL,
    -- The tool name: `studio_manifest.MCP_TOOL_NAME`.
    tool             TEXT NOT NULL,
    -- The SHA-256 hex of the canonical approved text
    -- (`mcp_actions.tool_text_hash`).
    text_hash        TEXT NOT NULL,
    -- The member who approved it, in lower case.
    approved_by      TEXT NOT NULL,
    approved_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (id),
    CONSTRAINT studio_mcp_tool_approvals_server CHECK (
        server ~ '^[a-z0-9][a-z0-9-]{0,48}[a-z0-9]$'),
    CONSTRAINT studio_mcp_tool_approvals_tool CHECK (
        tool ~ '^[A-Za-z0-9_][A-Za-z0-9_-]{0,63}$'),
    CONSTRAINT studio_mcp_tool_approvals_hash CHECK (text_hash ~ '^[0-9a-f]{64}$'),
    CONSTRAINT studio_mcp_tool_approvals_by CHECK (
        approved_by <> '' AND approved_by = lower(approved_by))
);

-- One approval for each tool of each server of an org. The upsert of an
-- approval and the one read of the catalog use it.
CREATE UNIQUE INDEX IF NOT EXISTS studio_mcp_tool_approvals_tool_uq
    ON studio_mcp_tool_approvals (organization_id, server, tool);

DO $rls$
DECLARE
    t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY['studio_mcp_tool_approvals'] LOOP
        IF NOT EXISTS (
            SELECT 1 FROM pg_class
             WHERE oid = to_regclass(t)
               AND relrowsecurity
               AND relforcerowsecurity
        ) THEN
            EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
            EXECUTE format('ALTER TABLE %I FORCE  ROW LEVEL SECURITY', t);
            EXECUTE format('DROP POLICY IF EXISTS %I ON %I', t || '_tenant_isolation', t);
            EXECUTE format(
                'CREATE POLICY %I ON %I '
                'USING      (organization_id = current_setting(''app.tenant_id'', true)::uuid) '
                'WITH CHECK (organization_id = current_setting(''app.tenant_id'', true)::uuid)',
                t || '_tenant_isolation', t);
        END IF;
    END LOOP;
END
$rls$;

COMMENT ON TABLE studio_mcp_tool_approvals IS
    'The approved text hash of each MCP tool of an org (WS-STU B1b-3). '
    'It holds no description, no secret and no url. RLS covers the org.';

COMMIT;
