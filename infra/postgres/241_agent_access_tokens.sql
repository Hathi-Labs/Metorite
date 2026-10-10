-- 241_agent_access_tokens.sql — the token store for outside agents (WS-55 EA-1).
--
-- What: three tables.
--   agent_connection  one row for each outside agent client that one member
--                     authorized in one organization. It holds the scope
--                     ceiling, the project pins and the lifecycle. TENANT-SCOPED.
--   agent_token       the access tokens, the refresh tokens and the
--                     authorization codes, told apart by `kind`. It holds only
--                     the SHA-256 of each token. RLS-EXEMPT.
--   agent_client      a cache of client metadata (a Client ID Metadata
--                     Document, or a dynamic registration). No tenant data.
--                     RLS-EXEMPT.
-- Why:  spec `project-docs/specs/external_agent_access.md` §7, §10.3 and §15
--       EA-1 · board WS-55 · decision D97.
-- Depends on: 130_org_access_control.sql (`organization`).
--
-- ── Why agent_token is RLS-EXEMPT ──────────────────────────────────────────
--
-- An outside agent sends only an opaque token. The edge must find the token
-- BEFORE it knows the tenant, so a tenant-scoped table cannot serve the
-- lookup. The lookup returns the connection id AND the organization id, and
-- the edge then binds that tenant before it reads anything else.
-- `user_identity`, `org_membership` and `auth_email_otp_token` (186) are
-- exempt for the same reason. The table still carries `organization_id`,
-- with a foreign key, and a composite foreign key to agent_connection makes
-- the two agree. The entry sits in `scripts/gen_tenant_migration.EXEMPT` with
-- its reason, and the map IS the security review.
--
-- ── Why agent_connection is tenant-scoped ──────────────────────────────────
--
-- The connection holds the member, the scopes and the pins. Nothing needs to
-- read it before the tenant is known. The guarded block below is the block of
-- 237_chat_pending_ask.sql: ENABLE and FORCE ROW LEVEL SECURITY, then the
-- policy `agent_connection_tenant_isolation` with USING and WITH CHECK.
--
-- ── The names fail loudly on a clash ───────────────────────────────────────
--
-- These names are generic. `CREATE TABLE IF NOT EXISTS` against a stranger's
-- relation of the same name is a SILENT no-op, and the store would then read
-- and write somebody else's table. So each guard below RAISES when a relation
-- of that name exists without this migration's own marker column. The shape
-- is 186_auth_email_otp_token.sql's.
--
-- EXPAND-ONLY (R6): three new tables, no change to an existing one. Idempotent
-- per infra/postgres/README.md.
--
-- Fence: tests/unit/test_agent_connection_tokens.py (R8, a phase-4-promoted
-- catalog, as a NON-privileged role). It finds this file by CONTENT, never by
-- number (R1).

BEGIN;

-- ── The collision guards ───────────────────────────────────────────────────
DO $guard$
BEGIN
    IF to_regclass('public.agent_connection') IS NOT NULL
       AND NOT EXISTS (
           SELECT 1 FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name   = 'agent_connection'
              AND column_name  = 'include_personal'
       )
    THEN
        RAISE EXCEPTION
            'agent_connection already exists and is NOT the WS-55 table '
            '(no include_personal column). Refusing to adopt it.';
    END IF;
    IF to_regclass('public.agent_token') IS NOT NULL
       AND NOT EXISTS (
           SELECT 1 FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name   = 'agent_token'
              AND column_name  = 'code_challenge'
       )
    THEN
        RAISE EXCEPTION
            'agent_token already exists and is NOT the WS-55 table '
            '(no code_challenge column). Refusing to adopt it.';
    END IF;
    IF to_regclass('public.agent_client') IS NOT NULL
       AND NOT EXISTS (
           SELECT 1 FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name   = 'agent_client'
              AND column_name  = 'registered_via'
       )
    THEN
        RAISE EXCEPTION
            'agent_client already exists and is NOT the WS-55 table '
            '(no registered_via column). Refusing to adopt it.';
    END IF;
END
$guard$;

-- ── agent_connection (tenant-scoped) ───────────────────────────────────────
CREATE TABLE IF NOT EXISTS agent_connection (
    id               UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    -- Bound at consent from the member's session. Never read from a request.
    organization_id  UUID        NOT NULL REFERENCES organization (id) ON DELETE CASCADE,
    member_email     TEXT        NOT NULL,
    client_id        TEXT        NOT NULL,
    client_name      TEXT        NOT NULL DEFAULT '',
    -- From the verified metadata document, not a self-reported name.
    client_origin    TEXT        NOT NULL DEFAULT '',
    -- The ceiling (spec §8). A scope never adds access.
    scopes           TEXT[]      NOT NULL DEFAULT '{}',
    -- Empty means every project the member sees.
    pinned_projects  UUID[]      NOT NULL DEFAULT '{}',
    -- D-EA-4: personal projects are out unless the member opts in.
    include_personal BOOLEAN     NOT NULL DEFAULT false,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_used_at     TIMESTAMPTZ,
    expires_at       TIMESTAMPTZ NOT NULL,
    revoked_at       TIMESTAMPTZ,
    -- The target of agent_token's composite key, so a token's organization
    -- is always its connection's organization.
    CONSTRAINT agent_connection_id_org_key UNIQUE (id, organization_id)
);

-- The member's own list, and the admin list (EA-4).
CREATE INDEX IF NOT EXISTS idx_agent_connection_member_live
    ON agent_connection (organization_id, member_email)
    WHERE revoked_at IS NULL;

DO $rls$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_class
         WHERE oid = 'agent_connection'::regclass
           AND relrowsecurity
           AND relforcerowsecurity
    ) THEN
        EXECUTE 'ALTER TABLE agent_connection ENABLE ROW LEVEL SECURITY';
        EXECUTE 'ALTER TABLE agent_connection FORCE  ROW LEVEL SECURITY';
        EXECUTE 'DROP POLICY IF EXISTS agent_connection_tenant_isolation '
                'ON agent_connection';
        EXECUTE 'CREATE POLICY agent_connection_tenant_isolation '
                'ON agent_connection '
                '    USING      (organization_id = '
                '        current_setting(''app.tenant_id'', true)::uuid) '
                '    WITH CHECK (organization_id = '
                '        current_setting(''app.tenant_id'', true)::uuid)';
    END IF;
END
$rls$;

-- ── agent_token (RLS-EXEMPT, looked up before the tenant is known) ─────────
CREATE TABLE IF NOT EXISTS agent_token (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id UUID        NOT NULL REFERENCES organization (id) ON DELETE CASCADE,
    connection_id   UUID        NOT NULL,
    kind            TEXT        NOT NULL,
    -- The lowercase hex SHA-256 of the token. The token itself is never stored.
    token_hash      TEXT        NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at      TIMESTAMPTZ NOT NULL,
    -- A refresh token or a code is spent once. The spend is a single-row
    -- UPDATE ... WHERE used_at IS NULL, so two callers cannot both win.
    used_at         TIMESTAMPTZ,
    revoked_at      TIMESTAMPTZ,
    -- What the client asked for. A code must carry all of these. An access or
    -- a refresh token copies the scope, the resource and the client id.
    client_id       TEXT,
    code_challenge  TEXT,
    redirect_uri    TEXT,
    resource        TEXT,
    scope           TEXT,
    CONSTRAINT agent_token_hash_key UNIQUE (token_hash),
    CONSTRAINT agent_token_kind_known
        CHECK (kind IN ('access', 'refresh', 'code')),
    CONSTRAINT agent_token_hash_shape
        CHECK (token_hash ~ '^[0-9a-f]{64}$'),
    CONSTRAINT agent_token_code_is_complete
        CHECK (kind <> 'code' OR (client_id IS NOT NULL
                                  AND code_challenge IS NOT NULL
                                  AND redirect_uri IS NOT NULL
                                  AND resource IS NOT NULL)),
    CONSTRAINT agent_token_connection_fk
        FOREIGN KEY (connection_id, organization_id)
        REFERENCES agent_connection (id, organization_id) ON DELETE CASCADE
);

-- "Revoke every token of this connection".
CREATE INDEX IF NOT EXISTS idx_agent_token_connection
    ON agent_token (connection_id);

-- ── agent_client (RLS-EXEMPT, global, no tenant data) ──────────────────────
CREATE TABLE IF NOT EXISTS agent_client (
    -- For a metadata document this is its https URL. For a dynamic
    -- registration it is the id the server issued.
    client_id       TEXT        PRIMARY KEY,
    registered_via  TEXT        NOT NULL,
    metadata        JSONB       NOT NULL DEFAULT '{}'::jsonb,
    fetched_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- When the cache entry, or an unused registration, stops being valid.
    expires_at      TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT agent_client_registered_via_known
        CHECK (registered_via IN ('cimd', 'dcr')),
    CONSTRAINT agent_client_metadata_is_object
        CHECK (jsonb_typeof(metadata) = 'object')
);

COMMENT ON TABLE agent_connection IS
    'WS-55 EA-1: one outside agent client that one member authorized in one '
    'organization. Tenant-scoped, FORCE RLS. external_agent_access.md §7.';
COMMENT ON TABLE agent_token IS
    'WS-55 EA-1: access tokens, refresh tokens and authorization codes, by '
    'kind. Stores only a SHA-256 of each token. RLS-EXEMPT because the lookup '
    'runs before the tenant is known; listed in gen_tenant_migration.EXEMPT. '
    'external_agent_access.md §10.3.';
COMMENT ON TABLE agent_client IS
    'WS-55 EA-1: cached client metadata (cimd or dcr). No tenant data. '
    'RLS-EXEMPT; listed in gen_tenant_migration.EXEMPT.';

COMMIT;
