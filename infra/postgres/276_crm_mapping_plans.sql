-- ============================================================================
-- 276_crm_mapping_plans.sql — the versions of the mapping plan of an org.
--
-- What: one new table, `crm_mapping_plans`. Each row is one version of the
--       mapping plan of an organization: a version number, a state, a JSON
--       snapshot of the rules, who made it and when, and who approved it and
--       when. The states are `draft`, `approved` and `superseded`. One
--       version at most is `approved` for each organization.
--       `crm_field_rules` stays the live set, and its indexes do not change.
--       An approve writes the snapshot into `crm_field_rules` in one
--       transaction (gateway/routes/crm/mapping_plans.py).
-- Why:  WS-53 CRM-Q18-1, spec project-docs/specs/crm_platform.md §18.9.6,
--       "CRM-Q18 · One plan".
-- Depends on: 130_org_access_control.sql (organization).
--
-- Expand only (R6): one new table and two indexes. No rename, no drop, and no
-- change to an existing table. The file writes no data: the first read of the
-- plan of an org builds version 1 from its approved rules. Old code names no
-- new object.
-- Idempotent per infra/postgres/README.md: CREATE TABLE IF NOT EXISTS,
-- CREATE (UNIQUE) INDEX IF NOT EXISTS, and an RLS block guarded on
-- relforcerowsecurity. A second run changes nothing.
-- Tenant-scoped (R5): `organization_id` with ENABLE and FORCE row level
-- security and one policy with USING and WITH CHECK, in the shape of
-- 253_crm_field_rules.sql. `REFERENCES` comes before `DEFAULT`, because the
-- tenancy ratchet in tests/unit/test_tenancy_boundary.py matches the two with
-- no comma between. The foreign key is inline, never `ADD CONSTRAINT
-- <table>_org_fk`, because generated phase 3 adds that name.
-- Pinned by tests/unit/test_crm_map_plan_r8.py, which finds this file by
-- CONTENT, never by number (R1).
-- ============================================================================

BEGIN;

CREATE TABLE IF NOT EXISTS crm_mapping_plans (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id  UUID NOT NULL
                     REFERENCES organization (id) ON DELETE CASCADE
                     DEFAULT current_setting('app.tenant_id', true)::uuid,
    version          INT NOT NULL CHECK (version >= 1),
    state            TEXT NOT NULL
                     CHECK (state IN ('draft', 'approved', 'superseded')),
    -- A JSON list. Each item has a `kind`. In CRM-Q18 each kind is `field`:
    -- {kind, entity, field_api_name, field_type, target_column,
    --  profile_version}.
    rules            JSONB NOT NULL DEFAULT '[]'::jsonb
                     CHECK (jsonb_typeof(rules) = 'array'),
    created_by       TEXT NOT NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    approved_by      TEXT NULL,
    approved_at      TIMESTAMPTZ NULL,
    -- The version that this version was built from. NULL for version 1.
    based_on         INT NULL
);

-- One row for each version number, for each tenant. A race of two first
-- reads inserts version 1 once.
CREATE UNIQUE INDEX IF NOT EXISTS crm_mapping_plans_version_key
    ON crm_mapping_plans (organization_id, version);

-- One approved version for each tenant.
CREATE UNIQUE INDEX IF NOT EXISTS crm_mapping_plans_one_approved
    ON crm_mapping_plans (organization_id)
    WHERE state = 'approved';

DO $rls$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_class
         WHERE oid = to_regclass('crm_mapping_plans')
           AND relrowsecurity
           AND relforcerowsecurity
    ) THEN
        ALTER TABLE crm_mapping_plans ENABLE ROW LEVEL SECURITY;
        ALTER TABLE crm_mapping_plans FORCE  ROW LEVEL SECURITY;
        DROP POLICY IF EXISTS crm_mapping_plans_tenant_isolation ON crm_mapping_plans;
        CREATE POLICY crm_mapping_plans_tenant_isolation ON crm_mapping_plans
            USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
            WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);
    END IF;
END
$rls$;

COMMENT ON TABLE crm_mapping_plans IS
    'WS-53 CRM-Q18-1: one version of the mapping plan of an org. crm_field_rules '
    'is the live set. crm_platform.md §18.9.6.';

COMMIT;
