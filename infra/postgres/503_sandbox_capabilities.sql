-- ============================================================================
-- 503_sandbox_capabilities.sql - the run capability of the sandbox door
-- (WS-TO SBX-2, Metorite task 3801)
-- ============================================================================
-- Spec: project-docs/specs/agent_sandbox.md §7.5 and §12.2 decision 5 · R1 ·
--   R5 · R6 · R7 · R8.
--
-- R1: the lane took 503 at build time from the reservation script of the
--   home box. On 2026-11-26 main held 498, and the production ledger held up
--   to 501. Check the number again at merge (scripts/premerge.sh).
--
-- What: one new table, `sandbox_capabilities`. One row is one capability
--   of one step (build, smoke or run) of one tool run. The row holds the
--   SHA-256 of the token, never the token. The door of the gateway looks the
--   row up by that hash, and the row names the organization and the member.
--
-- Why it is RLS-EXEMPT (R5 (a)): the door reads the row BEFORE it knows the
--   tenant, so a policy keyed on app.tenant_id could never match that read.
--   The row still carries `organization_id` with a foreign key, and the
--   lookup returns it, so the door binds that tenant before any other read.
--   `scripts/gen_tenant_migration.py` lists the table in `EXEMPT` with that
--   reason, as it lists `agent_token` (248). That entry is the security
--   review. `tests/unit/test_tenant_coverage.py` pins it.
--
-- Why a new table and not a new kind of `agent_token`: `agent_token` has
--   `connection_id NOT NULL` with a foreign key, and a CHECK of three kinds.
--   A capability has no connection. A new kind would relax a NOT NULL and a
--   CHECK on a live auth table (decision 5).
--
-- The rules of the rows:
--   * `expires_at` is at most 15 minutes after `created_at` (§5.1). The
--     store computes both in the INSERT, from the clock of the database.
--   * A smoke step holds no write scope (§5.3).
--   * A sweep deletes a row 1 day after `expires_at` (§7.5). SBX-4 schedules
--     it with the first real mint.
--
-- Grants: no RLS policy, because the table is exempt. So the file revokes
--   every privilege from PUBLIC and from the three Supabase roles, in the
--   loop of 222. The app role `acb_app` gets SELECT, INSERT, UPDATE and
--   DELETE in a guarded block. The default privileges give the same, and the
--   explicit grant makes the intent readable.
--
-- R6: expand only. One new table, with CREATE TABLE IF NOT EXISTS and
--   CREATE INDEX IF NOT EXISTS. It changes no table that exists, and old code
--   names nothing new. A second run changes nothing.
-- ============================================================================

CREATE TABLE IF NOT EXISTS sandbox_capabilities (
    -- The lowercase hex SHA-256 of the whole header value, `msbx_` included.
    -- The token itself is never stored.
    token_sha256    TEXT        PRIMARY KEY,
    organization_id UUID        NOT NULL REFERENCES organization (id) ON DELETE CASCADE,
    member_email    TEXT        NOT NULL,
    run_id          TEXT        NOT NULL,
    thread_id       TEXT        NOT NULL,
    -- The Studio package and its version (D187). Null during a build.
    app_id          UUID,
    version         INTEGER,
    phase           TEXT        NOT NULL,
    read_scopes     TEXT[]      NOT NULL DEFAULT '{}',
    write_scopes    TEXT[]      NOT NULL DEFAULT '{}',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at      TIMESTAMPTZ NOT NULL,
    revoked_at      TIMESTAMPTZ,
    CONSTRAINT sandbox_capabilities_hash_shape
        CHECK (token_sha256 ~ '^[0-9a-f]{64}$'),
    CONSTRAINT sandbox_capabilities_phase_known
        CHECK (phase IN ('build', 'smoke', 'run')),
    CONSTRAINT sandbox_capabilities_smoke_reads_only
        CHECK (phase <> 'smoke' OR cardinality(write_scopes) = 0),
    CONSTRAINT sandbox_capabilities_ttl_ceiling
        CHECK (expires_at <= created_at + interval '15 minutes')
);

-- "Revoke every live capability of this step".
CREATE INDEX IF NOT EXISTS idx_sandbox_capabilities_run
    ON sandbox_capabilities (organization_id, run_id);

-- The sweep of old rows.
CREATE INDEX IF NOT EXISTS idx_sandbox_capabilities_expires
    ON sandbox_capabilities (expires_at);

COMMENT ON TABLE sandbox_capabilities IS
    'The run capabilities of the sandbox door (agent_sandbox.md 12.2, SBX-2). '
    'RLS-EXEMPT: the door looks a row up by token hash before the tenant is '
    'known. Holds the SHA-256 of each token, never the token.';

REVOKE ALL ON TABLE sandbox_capabilities FROM PUBLIC;

DO $$
DECLARE
    r text;
BEGIN
    -- Supabase's default privileges hand every new table to these roles.
    FOREACH r IN ARRAY ARRAY['anon', 'authenticated', 'service_role'] LOOP
        IF EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = r) THEN
            EXECUTE format('REVOKE ALL ON TABLE sandbox_capabilities FROM %I', r);
        END IF;
    END LOOP;
    -- The application role of saas_multitenancy_handover.md §H3.
    IF EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = 'acb_app') THEN
        GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE sandbox_capabilities TO acb_app;
    END IF;
END
$$;
