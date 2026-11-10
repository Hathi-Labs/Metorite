-- ============================================================================
-- 300_kb_sources.sql — company file sources for Knowledge, and the Google grant.
--
-- What: two new tables, their indexes, one new function, and one function
--       replaced with the SAME signature.
--       `kb_sources` — one row for each connected file source of an org:
--                   the `integration_connections` row that holds its token
--                   (`connection_id`), the provider, `kind` (member or org),
--                   the member who owns it, `state`, the Google account it
--                   reads (`account_email`, `account_id`, for the
--                   same-account check), `node_count`, `discovered_at` and
--                   `last_error`. One row for each connection.
--       `kb_source_nodes` — one row for each folder and file that discovery
--                   found. Metadata only: name, type, size, modified time,
--                   link. NO text and NO bytes. `explored` is false for a
--                   folder whose children a capped discovery did not read.
--                   `state` is live or gone.
--       `kb_google_drive_grant_shared(email, exclude)` — does another live
--                   Google Drive connection of this address exist, in any
--                   tenant. One bit, never a row.
--       `email_gmail_grant_shared(email, exclude)` — the function of 263,
--                   replaced. It now ALSO counts a live Google Drive
--                   connection of the address. The signature and the
--                   return type do not change.
-- Why:  WS-61 KC-1a, spec project-docs/specs/knowledge_curation.md §7.2
--       step 1, §16 and §22, and knowledge_base.md §7.4. Metorite owns ONE
--       Google OAuth client. A Drive consent and a Gmail consent of the same
--       Google account join ONE Google grant, and a revoke ends the whole
--       grant. So:
--         * a Drive disconnect revokes only when NO Gmail mailbox and NO
--           other Drive connection of the address exists, in any tenant;
--         * a Gmail disconnect (routes/email/teardown.py, GRANT_SHARED_SQL)
--           must not revoke while a Drive connection of the address lives.
--           Before this file, the 263 function counted Gmail rows only, so
--           a Gmail disconnect killed the Drive token. The replacement
--           closes that hole with no change to the gateway.
-- Depends on: 130_org_access_control.sql (organization),
--       245_integration_connections.sql (integration_connections),
--       263_email_gmail_grant_shared.sql (the function it replaces),
--       272_knowledge_pages.sql (kb_spaces).
--
-- Expand only (R6): two new tables, one new function, and CREATE OR REPLACE
-- of a function with its signature, its return type and its grants kept.
-- No column of an existing table changes, no rename, no drop, and no row
-- changes. The old gateway names no new object. The old gateway calls the
-- replaced function in the same way, and an answer of true only skips a
-- revoke, which fails safe toward a live token.
-- Idempotent per infra/postgres/README.md: CREATE TABLE IF NOT EXISTS,
-- CREATE INDEX IF NOT EXISTS, CREATE OR REPLACE FUNCTION, and an RLS block
-- guarded on relforcerowsecurity. A second run changes nothing.
-- Tenant-scoped (R5): each table has `organization_id` with ENABLE and FORCE
-- row level security and one policy with USING and WITH CHECK, in the shape
-- of 293_kb_gaps.sql. `REFERENCES` comes before `DEFAULT`, because the
-- tenancy ratchet in tests/unit/test_tenancy_boundary.py matches the two
-- with no comma between. The foreign key is inline, never
-- `ADD CONSTRAINT <table>_org_fk`, because generated phase 3 adds that name.
-- The gateway takes the org and the member from the session, never from a
-- request body.
-- ⚠️ `integration_connections` has a unique index on (org, provider,
-- owning_app, scope_kind, member). So one member has at most ONE Google
-- Drive source in one org.
-- The number is taken at build time (R1). On 2026-10-25 main held 298, and
-- the open PR #1288 held 299. Check it again at merge. The tests find this
-- file by CONTENT, never by number.
--
-- ── The two functions: why SECURITY DEFINER, and why it is safe here ────────
--
-- The rules of 222_chat_session_exists.sql and 263, unchanged:
--
--   * Each returns a boolean, never a row. The gateway passes the address of
--     the account that it has just disconnected, never a request value.
--   * `SET search_path = pg_catalog, pg_temp`, and every name is qualified
--     with `public.`. So a caller cannot plant a table of its own.
--   * FORCE RLS binds the OWNER too, unless it is a superuser or BYPASSRLS.
--     When the owner cannot see every row of a table that the function
--     reads, it raises a WARNING and returns NULL. The gateway reads NULL as
--     "cannot tell" and skips the revoke. That fails safe toward the other
--     token: a missed revoke leaves a grant that the member can remove at
--     Google, and a wrong revoke breaks a live mailbox or a live Drive.
--   * EXECUTE goes to the app role only. The file revokes it from PUBLIC and
--     from the three Supabase roles by name, and grants it to `acb_app`
--     when that role exists.
--
-- Fences: tests/unit/test_kc_sources_r8.py (FORCE RLS as a role that is not
-- a superuser, not the owner and not BYPASSRLS, and both functions in both
-- directions), tests/unit/test_kc_sources_google_grant.py (the gateway half),
-- tests/unit/test_tenant_coverage.py and tests/unit/test_email_disconnect_order.py.
-- ============================================================================

SET lock_timeout = '5s';

BEGIN;

CREATE TABLE IF NOT EXISTS kb_sources (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id   UUID NOT NULL
                      REFERENCES organization (id) ON DELETE CASCADE
                      DEFAULT current_setting('app.tenant_id', true)::uuid,
    connection_id     UUID NOT NULL
                      REFERENCES integration_connections (id) ON DELETE CASCADE,
    provider          TEXT NOT NULL
                      CONSTRAINT kb_sources_provider_check
                      CHECK (provider IN ('google_drive', 'onedrive')),
    kind              TEXT NOT NULL DEFAULT 'member'
                      CONSTRAINT kb_sources_kind_check
                      CHECK (kind IN ('member', 'org')),
    owner_email       TEXT NOT NULL
                      CONSTRAINT kb_sources_owner_email_length
                      CHECK (char_length(owner_email) BETWEEN 3 AND 320),
    state             TEXT NOT NULL DEFAULT 'discovering'
                      CONSTRAINT kb_sources_state_check
                      CHECK (state IN ('discovering', 'ready', 'failed',
                                       'needs_reconnect', 'disconnected')),
    default_space_id  UUID NULL REFERENCES kb_spaces (id) ON DELETE SET NULL,
    account_email     TEXT NULL,
    account_id        TEXT NULL,
    node_count        INTEGER NOT NULL DEFAULT 0
                      CONSTRAINT kb_sources_node_count_positive CHECK (node_count >= 0),
    discovered_at     TIMESTAMPTZ NULL,
    last_error        TEXT NULL
                      CONSTRAINT kb_sources_last_error_length
                      CHECK (last_error IS NULL OR char_length(last_error) <= 64),
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT kb_sources_org_connection_key UNIQUE (organization_id, connection_id)
);

-- The list of a member's own sources.
CREATE INDEX IF NOT EXISTS idx_kb_sources_owner
    ON kb_sources (organization_id, owner_email);

CREATE TABLE IF NOT EXISTS kb_source_nodes (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id   UUID NOT NULL
                      REFERENCES organization (id) ON DELETE CASCADE
                      DEFAULT current_setting('app.tenant_id', true)::uuid,
    source_id         UUID NOT NULL REFERENCES kb_sources (id) ON DELETE CASCADE,
    ext_id            TEXT NOT NULL
                      CONSTRAINT kb_source_nodes_ext_id_length
                      CHECK (char_length(ext_id) BETWEEN 1 AND 200),
    parent_ext_id     TEXT NULL,
    drive_id          TEXT NULL,
    kind              TEXT NOT NULL
                      CONSTRAINT kb_source_nodes_kind_check
                      CHECK (kind IN ('folder', 'file')),
    name              TEXT NOT NULL
                      CONSTRAINT kb_source_nodes_name_length
                      CHECK (char_length(name) BETWEEN 1 AND 500),
    mime              TEXT NULL,
    size              BIGINT NULL,
    modified_at       TIMESTAMPTZ NULL,
    content_hash      TEXT NULL,
    web_url           TEXT NULL,
    state             TEXT NOT NULL DEFAULT 'live'
                      CONSTRAINT kb_source_nodes_state_check
                      CHECK (state IN ('live', 'gone')),
    explored          BOOLEAN NOT NULL DEFAULT true,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT kb_source_nodes_ext_key UNIQUE (organization_id, source_id, ext_id)
);

-- The tree read: the children of one folder.
CREATE INDEX IF NOT EXISTS idx_kb_source_nodes_parent
    ON kb_source_nodes (source_id, parent_ext_id);

DO $rls$
DECLARE
    t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY['kb_sources', 'kb_source_nodes'] LOOP
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

COMMENT ON TABLE kb_sources IS
    'WS-61 KC-1a: one connected company file source (Google Drive first). '
    'The token lives in integration_connections only. A disconnect sets '
    'state disconnected and deletes the nodes. knowledge_curation.md §16.';

COMMENT ON TABLE kb_source_nodes IS
    'WS-61 KC-1a: one folder or file of a source, metadata only, never text '
    'or bytes. explored is false for a folder that a capped discovery did not '
    'list. knowledge_base.md §7.2.';

-- ── Another live Google Drive connection of this address ────────────────────

CREATE OR REPLACE FUNCTION public.kb_google_drive_grant_shared(p_email text, p_exclude uuid)
RETURNS boolean
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $kb_google_drive_grant_shared$
DECLARE
    sees_all boolean;
BEGIN
    IF p_email IS NULL OR pg_catalog.btrim(p_email) = '' THEN
        RETURN NULL;   -- cannot tell; the caller skips the revoke
    END IF;

    SELECT r.rolsuper OR r.rolbypassrls
            OR NOT (c.relrowsecurity OR c.relforcerowsecurity)
      INTO sees_all
      FROM pg_catalog.pg_roles r, pg_catalog.pg_class c
     WHERE r.rolname = current_user
       AND c.oid = 'public.integration_connections'::pg_catalog.regclass;

    IF NOT COALESCE(sees_all, false) THEN
        RAISE WARNING
            'kb_google_drive_grant_shared: the owner % cannot see through '
            'row level security on integration_connections, so no revoke runs',
            current_user;
        RETURN NULL;   -- cannot tell; the caller skips the revoke
    END IF;

    RETURN EXISTS (
        SELECT 1 FROM public.integration_connections ic
         WHERE ic.provider = 'google_drive'
           AND ic.status <> 'disconnected'
           AND pg_catalog.lower(ic.provider_meta ->> 'account_email')
               = pg_catalog.lower(pg_catalog.btrim(p_email))
           AND (p_exclude IS NULL OR ic.id <> p_exclude)
    );
END
$kb_google_drive_grant_shared$;

COMMENT ON FUNCTION public.kb_google_drive_grant_shared(text, uuid) IS
    'True when another live google_drive integration_connections row with '
    'this account address exists in any tenant. One bit, never a row. NULL '
    'when the owner cannot see through RLS. WS-61 KC-1a.';

-- ── The 263 function, replaced: a live Drive connection counts too ──────────
--
-- Same name, same arguments, same return type (R6). CREATE OR REPLACE keeps
-- the owner and the grants. The Gmail half is the body of 263, unchanged.
-- The owner must see through RLS on BOTH tables, or the answer is NULL.

CREATE OR REPLACE FUNCTION public.email_gmail_grant_shared(p_email text, p_exclude uuid)
RETURNS boolean
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $email_gmail_grant_shared$
DECLARE
    sees_all boolean;
BEGIN
    IF p_email IS NULL OR pg_catalog.btrim(p_email) = '' THEN
        RETURN NULL;   -- cannot tell; the caller skips the revoke
    END IF;

    -- current_user is the OWNER inside a DEFINER function. A table with no
    -- RLS shows every row to it. A table with RLS shows every row only to a
    -- superuser or a BYPASSRLS owner. Both tables must show every row.
    SELECT pg_catalog.bool_and(r.rolsuper OR r.rolbypassrls
            OR NOT (c.relrowsecurity OR c.relforcerowsecurity))
      INTO sees_all
      FROM pg_catalog.pg_roles r, pg_catalog.pg_class c
     WHERE r.rolname = current_user
       AND c.oid IN ('public.email_accounts'::pg_catalog.regclass,
                     'public.integration_connections'::pg_catalog.regclass);

    IF NOT COALESCE(sees_all, false) THEN
        RAISE WARNING
            'email_gmail_grant_shared: the owner % cannot see through '
            'row level security on email_accounts or integration_connections, '
            'so no revoke runs',
            current_user;
        RETURN NULL;   -- cannot tell; the caller skips the revoke
    END IF;

    RETURN EXISTS (
        SELECT 1 FROM public.email_accounts a
         WHERE a.provider = 'gmail'
           AND pg_catalog.lower(a.email_address) = pg_catalog.lower(p_email)
           AND (p_exclude IS NULL OR a.id <> p_exclude)
    ) OR EXISTS (
        -- WS-61 KC-1a: a live Drive connection of the same Google account
        -- holds the same grant. A Gmail disconnect must not end it.
        SELECT 1 FROM public.integration_connections ic
         WHERE ic.provider = 'google_drive'
           AND ic.status <> 'disconnected'
           AND pg_catalog.lower(ic.provider_meta ->> 'account_email')
               = pg_catalog.lower(pg_catalog.btrim(p_email))
           AND (p_exclude IS NULL OR ic.id <> p_exclude)
    );
END
$email_gmail_grant_shared$;

COMMENT ON FUNCTION public.email_gmail_grant_shared(text, uuid) IS
    'True when another gmail email_accounts row, or a live google_drive '
    'integration_connections row, with this address exists in any tenant. '
    'One bit, never a row. NULL when the owner cannot see through RLS. '
    'WS-17 EM-G11f1 and WS-61 KC-1a.';

-- ── EXECUTE to the app role only, for both functions ────────────────────────

REVOKE ALL ON FUNCTION public.kb_google_drive_grant_shared(text, uuid) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.email_gmail_grant_shared(text, uuid) FROM PUBLIC;

DO $$
DECLARE
    r text;
    f text;
BEGIN
    FOREACH f IN ARRAY ARRAY['public.kb_google_drive_grant_shared(text, uuid)',
                             'public.email_gmail_grant_shared(text, uuid)'] LOOP
        -- Supabase's default privileges hand every new function to these roles.
        FOREACH r IN ARRAY ARRAY['anon', 'authenticated', 'service_role'] LOOP
            IF EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = r) THEN
                EXECUTE format('REVOKE ALL ON FUNCTION %s FROM %I', f, r);
            END IF;
        END LOOP;
        -- The application role of saas_multitenancy_handover.md §H3.
        IF EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = 'acb_app') THEN
            EXECUTE format('GRANT EXECUTE ON FUNCTION %s TO acb_app', f);
        END IF;
    END LOOP;
END
$$;

COMMIT;
