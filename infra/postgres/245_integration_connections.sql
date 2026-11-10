-- ============================================================================
-- 245_integration_connections.sql — the one registry of external connections.
--
-- What: `integration_connections`, one row for each connection of an
--       organization to an outside provider. It is general, not CRM-only.
--         * `provider` and `owning_app` say what the connection is and which
--           app owns it. Example: `zoho` and `crm`.
--         * `scope_kind` is `org` (one for the whole organization) or
--           `member` (one for one member, named in `member_email`).
--         * `status` is `connected`, `needs_reconnect` or `disconnected`.
--         * `provider_meta` holds non-secret facts, such as the data centre.
--         * `credentials_encrypted` holds the Fernet blob of the tokens. ONE
--           store module reads and writes it
--           (gateway/connections/store.py). Nothing else names the column.
--       It also adds `crm_settings.connection_id`, the pointer from the CRM
--       settings row to its connection.
-- Why:  WS-53 CRM-Z2, spec project-docs/specs/crm_platform.md §5.3 and §7.1.
--       The Integrations app (WS-54) reads this one registry later.
-- Depends on: 130_org_access_control.sql (organization),
--             243_crm_settings.sql (crm_settings).
--
-- Expand only (R6): one new table and one new nullable column. No rename, no
-- drop, no UPDATE. Old code names no new object.
-- Idempotent per infra/postgres/README.md: CREATE TABLE IF NOT EXISTS,
-- CREATE UNIQUE INDEX IF NOT EXISTS, ADD COLUMN IF NOT EXISTS, and an RLS
-- block guarded on relforcerowsecurity. A second run changes nothing.
-- Tenant-scoped (R5): `organization_id` with ENABLE and FORCE row level
-- security and one policy with USING and WITH CHECK, in the shape of
-- 243_crm_settings.sql. `REFERENCES` comes before `DEFAULT`, because the
-- tenancy ratchet in tests/unit/test_tenancy_boundary.py matches the two with
-- no comma between. The generated phase files carry the same table.
-- One live connection for each (organization, provider, owning app, scope
-- kind, member): a reconnect updates the row and never adds a second one.
-- Pinned by tests/unit/test_crm_connection_r8.py, which finds this file by
-- CONTENT, never by number (R1).
-- ============================================================================

BEGIN;

CREATE TABLE IF NOT EXISTS integration_connections (
    id                     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id        UUID NOT NULL
                           REFERENCES organization (id) ON DELETE CASCADE
                           DEFAULT current_setting('app.tenant_id', true)::uuid,
    provider               TEXT NOT NULL,
    owning_app             TEXT NOT NULL,
    scope_kind             TEXT NOT NULL DEFAULT 'org',
    member_email           TEXT NULL,
    status                 TEXT NOT NULL DEFAULT 'connected',
    provider_meta          JSONB NOT NULL DEFAULT '{}',
    credentials_encrypted  TEXT NULL,
    scopes                 TEXT[] NOT NULL DEFAULT '{}',
    connected_by           TEXT,
    last_sync_at           TIMESTAMPTZ,
    last_error             TEXT,
    created_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT integration_connections_scope_known CHECK (
        scope_kind IN ('org', 'member')
    ),
    CONSTRAINT integration_connections_status_known CHECK (
        status IN ('connected', 'needs_reconnect', 'disconnected')
    ),
    CONSTRAINT integration_connections_member_matches_scope CHECK (
        (scope_kind = 'org' AND member_email IS NULL)
        OR (scope_kind = 'member' AND member_email IS NOT NULL)
    )
);

CREATE UNIQUE INDEX IF NOT EXISTS integration_connections_one_live_idx
    ON integration_connections (
        organization_id, provider, owning_app, scope_kind,
        (COALESCE(member_email, ''))
    );

DO $rls$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_class
         WHERE oid = 'integration_connections'::regclass
           AND relrowsecurity
           AND relforcerowsecurity
    ) THEN
        EXECUTE 'ALTER TABLE integration_connections ENABLE ROW LEVEL SECURITY';
        EXECUTE 'ALTER TABLE integration_connections FORCE  ROW LEVEL SECURITY';
        EXECUTE 'DROP POLICY IF EXISTS integration_connections_tenant_isolation '
                'ON integration_connections';
        EXECUTE 'CREATE POLICY integration_connections_tenant_isolation '
                'ON integration_connections '
                '    USING      (organization_id = '
                '        current_setting(''app.tenant_id'', true)::uuid) '
                '    WITH CHECK (organization_id = '
                '        current_setting(''app.tenant_id'', true)::uuid)';
    END IF;
END
$rls$;

ALTER TABLE crm_settings
    ADD COLUMN IF NOT EXISTS connection_id UUID NULL
    REFERENCES integration_connections (id) ON DELETE SET NULL;

COMMENT ON TABLE integration_connections IS
    'WS-53 CRM-Z2: the registry of external connections. One store module '
    'reads credentials_encrypted. crm_platform.md §5.3, §7.1.';

COMMIT;
