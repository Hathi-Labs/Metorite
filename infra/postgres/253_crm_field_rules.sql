-- ============================================================================
-- 253_crm_field_rules.sql — the field rules of the mirror (the Field map).
--
-- What: one new table, `crm_field_rules`. It holds one row for each field of a
--       known entity (lead, contact, company, deal) that an admin decided, or
--       that the assistant proposed. An approved rule copies the value of a
--       field that goes to `custom` into one of our columns. The legal target
--       columns live in `gateway/crm_sources/zoho/mapping.py`
--       (`FIELD_RULE_TARGETS`), not in this file.
-- Why:  WS-53 CRM-Q7a, spec project-docs/specs/crm_platform.md §18.7.
-- Depends on: 130_org_access_control.sql (organization).
--
-- Expand only (R6): one new table and two indexes. No rename, no drop, and no
-- change to an existing table. Old code names no new object.
-- Idempotent per infra/postgres/README.md: CREATE TABLE IF NOT EXISTS,
-- CREATE (UNIQUE) INDEX IF NOT EXISTS, and an RLS block guarded on
-- relforcerowsecurity. A second run changes nothing.
-- Tenant-scoped (R5): `organization_id` with ENABLE and FORCE row level
-- security and one policy with USING and WITH CHECK, in the shape of
-- 251_crm_module_records.sql. `REFERENCES` comes before `DEFAULT`, because the
-- tenancy ratchet in tests/unit/test_tenancy_boundary.py matches the two with
-- no comma between. The foreign key is inline, never `ADD CONSTRAINT
-- <table>_org_fk`, because generated phase 3 adds that name.
-- One approved rule for each target column: the partial unique index
-- `crm_field_rules_one_approved`. A second approve gives 409 at the route.
-- The routes write only this table. They never write a CRM record. The sync
-- applies an approved rule through the door `apply_field_rule_from_source`
-- in gateway/routes/crm/core.py. Pinned by tests/unit/test_crm_field_rules_r8.py,
-- which finds this file by CONTENT, never by number (R1).
-- ============================================================================

BEGIN;

CREATE TABLE IF NOT EXISTS crm_field_rules (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id  UUID NOT NULL
                     REFERENCES organization (id) ON DELETE CASCADE
                     DEFAULT current_setting('app.tenant_id', true)::uuid,
    ext_source       TEXT NOT NULL,
    entity           TEXT NOT NULL
                     CHECK (entity IN ('lead', 'contact', 'company', 'deal')),
    field_api_name   TEXT NOT NULL,
    -- The `crm_field_defs.type` word when the rule was made.
    field_type       TEXT NOT NULL,
    -- The `crm_source_profiles.version` that the rule was made from.
    profile_version  INT NOT NULL,
    target_column    TEXT NULL,
    status           TEXT NOT NULL
                     CHECK (status IN ('proposed', 'approved', 'rejected', 'stale')),
    proposed_by      TEXT NOT NULL
                     CHECK (proposed_by IN ('ai', 'admin')),
    confidence       NUMERIC NULL
                     CHECK (confidence IS NULL OR (confidence >= 0 AND confidence <= 1)),
    reason           TEXT NULL
                     CHECK (reason IS NULL OR char_length(reason) <= 140),
    decided_by       TEXT NULL,
    decided_at       TIMESTAMPTZ NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- One rule for each provider field of an entity, for each tenant.
CREATE UNIQUE INDEX IF NOT EXISTS crm_field_rules_field_key
    ON crm_field_rules (organization_id, ext_source, entity, field_api_name);

-- One approved rule for each target column of an entity, for each tenant.
CREATE UNIQUE INDEX IF NOT EXISTS crm_field_rules_one_approved
    ON crm_field_rules (organization_id, entity, target_column)
    WHERE status = 'approved';

DO $rls$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_class
         WHERE oid = to_regclass('crm_field_rules')
           AND relrowsecurity
           AND relforcerowsecurity
    ) THEN
        ALTER TABLE crm_field_rules ENABLE ROW LEVEL SECURITY;
        ALTER TABLE crm_field_rules FORCE  ROW LEVEL SECURITY;
        DROP POLICY IF EXISTS crm_field_rules_tenant_isolation ON crm_field_rules;
        CREATE POLICY crm_field_rules_tenant_isolation ON crm_field_rules
            USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
            WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);
    END IF;
END
$rls$;

COMMENT ON TABLE crm_field_rules IS
    'WS-53 CRM-Q7a: one rule that maps a provider field in custom to one of our '
    'columns. An admin approves or rejects it. crm_platform.md §18.7.';

COMMIT;
