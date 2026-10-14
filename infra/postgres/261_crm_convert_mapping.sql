-- ============================================================================
-- 261_crm_convert_mapping.sql — the lead-conversion mapping of a Native CRM.
--
-- What: one new table, `crm_convert_mappings`. A row says which field of a
--       contact, a company or a deal takes the value of one custom lead field
--       when a lead converts. `target_key` NULL with `skip` false means "auto":
--       the target field with the same key, when one exists with a compatible
--       type. `skip` true means "unmapped". No row also means "auto".
-- Why:  WS-53 CRM-C6a, spec project-docs/specs/crm_platform.md §20.9.
-- Depends on: 130_org_access_control.sql (organization).
--
-- Expand only (R6): one new table and no change to a table that exists. Old
-- code names no new object, so it runs on this schema as before.
-- Idempotent per infra/postgres/README.md: CREATE TABLE IF NOT EXISTS and an
-- RLS block guarded on relforcerowsecurity. A second run changes nothing.
-- Tenant-scoped (R5): `organization_id` with ENABLE and FORCE row level
-- security and one policy with USING and WITH CHECK, in the shape of
-- 260_crm_field_studio.sql. `REFERENCES` comes before `DEFAULT`, because the
-- tenancy ratchet in tests/unit/test_tenancy_boundary.py matches the two with
-- no comma between. The foreign key is inline, never `ADD CONSTRAINT
-- <table>_org_fk`, because generated phase 3 adds that name.
-- The keys are text, with no foreign key to `crm_field_defs`: a field never
-- leaves that table (a retire sets `retired_at`), and the routes check each
-- key against it. Pinned by tests/unit/test_crm_convert_mapping.py and
-- tests/unit/test_crm_convert_mapping_r8.py, which find this file by CONTENT,
-- never by number (R1).
-- ============================================================================

BEGIN;

CREATE TABLE IF NOT EXISTS crm_convert_mappings (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id  UUID NOT NULL
                     REFERENCES organization (id) ON DELETE CASCADE
                     DEFAULT current_setting('app.tenant_id', true)::uuid,
    source_key       TEXT NOT NULL
                     CHECK (char_length(source_key) BETWEEN 1 AND 100),
    target_entity    TEXT NOT NULL
                     CHECK (target_entity IN ('contact', 'company', 'deal')),
    target_key       TEXT NULL
                     CHECK (target_key IS NULL OR char_length(target_key) BETWEEN 1 AND 100),
    skip             BOOLEAN NOT NULL DEFAULT false,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- A skip row copies nothing, so it names no target.
    CONSTRAINT crm_convert_mappings_skip_no_target
        CHECK (NOT (skip AND target_key IS NOT NULL)),
    CONSTRAINT crm_convert_mappings_one_row
        UNIQUE (organization_id, source_key, target_entity)
);

-- A target field takes one explicit row at most (C6a review, P2). The PUT
-- route refuses two, and this index holds the rule against a race too.
CREATE UNIQUE INDEX IF NOT EXISTS crm_convert_mappings_one_target
    ON crm_convert_mappings (organization_id, target_entity, target_key)
    WHERE target_key IS NOT NULL AND NOT skip;

DO $rls$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_class
         WHERE oid = to_regclass('crm_convert_mappings')
           AND relrowsecurity
           AND relforcerowsecurity
    ) THEN
        ALTER TABLE crm_convert_mappings ENABLE ROW LEVEL SECURITY;
        ALTER TABLE crm_convert_mappings FORCE  ROW LEVEL SECURITY;
        DROP POLICY IF EXISTS crm_convert_mappings_tenant_isolation ON crm_convert_mappings;
        CREATE POLICY crm_convert_mappings_tenant_isolation ON crm_convert_mappings
            USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
            WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);
    END IF;
END
$rls$;

COMMENT ON TABLE crm_convert_mappings IS
    'WS-53 CRM-C6a: which contact, company or deal field takes one custom lead '
    'field at a lead conversion. crm_platform.md §20.9.';

COMMIT;
