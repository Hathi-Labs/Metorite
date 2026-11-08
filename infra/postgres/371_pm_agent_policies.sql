-- ============================================================================
-- 371_pm_agent_policies.sql — one policy store for agent work (WS-MCP SW-94,
-- Metorite task 1940, agent_coordination.md §3.11.5 and SW-94, D186).
--
-- What: one new table. `pm_agent_policies` holds the policy rows of an org.
--       A row has a scope (organization, project, lane, member or box), a
--       risk tier or `any`, a key from the fixed list in code, a value, the
--       author and a version. Each save adds a row, so the history of a
--       scope is its rows. `gateway/routes/agent_policies.py` is the one
--       writer and holds the two readers, `policy_for` and `policy_for_actor`.
-- Why:  The settings of agent work were each in their own store. One store,
--       resolved by app project and risk tier, holds them now (D186.8).
-- Depends on: 130_org_access_control.sql (organization), 146 (pm_projects).
--
-- Expand only (R6): one new table, three indexes, one trigger function and one
-- trigger. No rename, no drop, no change to an existing table. No running
-- code reads or writes the table before this release.
-- Idempotent per infra/postgres/README.md: CREATE TABLE IF NOT EXISTS, CREATE
-- INDEX IF NOT EXISTS, CREATE OR REPLACE FUNCTION, DROP TRIGGER IF EXISTS then
-- CREATE, and an RLS block guarded on relforcerowsecurity.
-- Tenant-scoped (R5), in the house shape of 340 (pm_agent_guidance):
-- `organization_id` with ENABLE and FORCE row level security and one policy
-- with USING and WITH CHECK. `REFERENCES` comes before `DEFAULT`, because the
-- tenancy ratchet in tests/unit/test_tenancy_boundary.py matches the two with
-- no comma between. The org of a new row comes from the DEFAULT, the bound
-- tenant. The writer never names it. No GRANT: the roles of 130 hold the
-- default privileges.
--
-- Scope: `scope_kind` names the kind. `project_id` is set for the kind
-- `project` only, and ON DELETE CASCADE removes the rows with the project.
-- `scope_ref` is set for `lane`, `member` and `box` only: a lane id, the
-- member's email in lower case, or the id of a box connection. The lane and
-- box tables are later slices (SW-67, EA-19), so `scope_ref` has no foreign
-- key. The CHECK `pm_agent_policies_scope_shape` holds the pairing.
--
-- Key: the list of keys is in code (`agent_policies.REGISTRY`), so a new key
-- needs no migration. The CHECK holds the shape of a key only. The route
-- answers 422 for a key that is not in the list.
--
-- Value: JSONB. A SQL NULL is a cleared scope: the scope then gives no value,
-- and the next level applies. A JSON `null` is the value "none" (for example
-- `release_cutoff`).
--
-- Version: starts at 1 for each (scope, tier, key), and the writer adds one
-- for each save. Postgres treats NULL as distinct in a unique index, so the
-- index keys on COALESCE(x, a fixed value), as 340 does. It does not use NULLS
-- NOT DISTINCT, which needs Postgres 15.
--
-- Append only: the trigger `pm_agent_policies_guard` refuses an UPDATE of
-- each row, for each role. A DELETE stays legal, so the cascade of a project
-- or an org works.
--
-- The number (R1): the builder takes the number at build time, and
-- scripts/premerge.sh checks it again at the merge. On 2026-11-08 origin/main
-- held 366 and the open pull requests held numbers up to 370, so this file
-- took 371. (The first build took 367, which an open pull request also held.)
-- Pinned by tests/unit/test_agent_policies_r8.py, which finds this
-- file by CONTENT, never by number (R1).
-- ============================================================================

BEGIN;

CREATE TABLE IF NOT EXISTS pm_agent_policies (
    id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id      UUID NOT NULL
                         REFERENCES organization (id) ON DELETE CASCADE
                         DEFAULT current_setting('app.tenant_id', true)::uuid,
    scope_kind           TEXT NOT NULL
                         CHECK (scope_kind IN ('organization', 'project', 'lane', 'member', 'box')),
    -- Set for the kind `project` only.
    project_id           UUID NULL REFERENCES pm_projects (id) ON DELETE CASCADE,
    -- Set for `lane`, `member` and `box` only.
    scope_ref            TEXT NULL CHECK (scope_ref IS NULL OR length(scope_ref) BETWEEN 1 AND 320),
    tier                 TEXT NOT NULL DEFAULT 'any'
                         CHECK (tier IN ('any', 'LOW', 'STANDARD', 'HIGH')),
    key                  TEXT NOT NULL CHECK (key ~ '^[a-z][a-z0-9_]{0,62}$'),
    -- SQL NULL clears the scope. JSON null is the value "none".
    value                JSONB NULL,
    version              INTEGER NOT NULL DEFAULT 1,
    -- The member who saved this version. From the session, never a body.
    written_by           TEXT NOT NULL,
    written_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT pm_agent_policies_scope_shape CHECK (
        (scope_kind = 'organization' AND project_id IS NULL AND scope_ref IS NULL)
        OR (scope_kind = 'project' AND project_id IS NOT NULL AND scope_ref IS NULL)
        OR (scope_kind IN ('lane', 'member', 'box')
            AND project_id IS NULL AND scope_ref IS NOT NULL)
    )
);

-- One row for each version of a (scope, tier, key). NULL-safe: see the header.
CREATE UNIQUE INDEX IF NOT EXISTS pm_agent_policies_version
    ON pm_agent_policies (
        organization_id,
        scope_kind,
        COALESCE(project_id, '00000000-0000-0000-0000-000000000000'::uuid),
        COALESCE(scope_ref, ''),
        tier,
        key,
        version
    );

-- The newest version of each scope for one key.
CREATE INDEX IF NOT EXISTS pm_agent_policies_key
    ON pm_agent_policies (organization_id, key, scope_kind, version DESC);

-- The cascade of a project reads this column.
CREATE INDEX IF NOT EXISTS pm_agent_policies_project
    ON pm_agent_policies (project_id)
    WHERE project_id IS NOT NULL;

-- A saved policy never changes: the next save is a new row.
-- Fence: tests/unit/test_agent_policies_r8.py.
CREATE OR REPLACE FUNCTION pm_agent_policies_guard_update() RETURNS trigger
LANGUAGE plpgsql AS $fn$
BEGIN
    RAISE EXCEPTION 'pm_agent_policies: a saved policy cannot change, save a new version'
        USING ERRCODE = 'check_violation';
END
$fn$;

DROP TRIGGER IF EXISTS pm_agent_policies_guard ON pm_agent_policies;
CREATE TRIGGER pm_agent_policies_guard
    BEFORE UPDATE ON pm_agent_policies
    FOR EACH ROW EXECUTE FUNCTION pm_agent_policies_guard_update();

DO $rls$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_class
         WHERE oid = to_regclass('pm_agent_policies')
           AND relrowsecurity
           AND relforcerowsecurity
    ) THEN
        ALTER TABLE pm_agent_policies ENABLE ROW LEVEL SECURITY;
        ALTER TABLE pm_agent_policies FORCE  ROW LEVEL SECURITY;
        DROP POLICY IF EXISTS pm_agent_policies_tenant_isolation ON pm_agent_policies;
        CREATE POLICY pm_agent_policies_tenant_isolation ON pm_agent_policies
            USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
            WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);
    END IF;
END
$rls$;

COMMENT ON TABLE pm_agent_policies IS
    'The policies of agent work: one row for each saved version of a (scope, tier, key). '
    'Append only. WS-MCP SW-94, agent_coordination.md §3.11.5 and SW-94.';

COMMIT;
