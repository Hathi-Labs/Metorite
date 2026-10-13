-- ============================================================================
-- 258_crm_field_studio.sql — the field studio of a Native CRM.
--
-- What: one new table, `crm_field_sections`, and seven new columns on
--       `crm_field_defs`. A section groups the fields of one entity (lead,
--       contact, company, deal) on the record page. The seven columns let an
--       admin of a Native CRM shape a field: its section, "required", a help
--       text, a default value, the entity that a lookup names, a row that
--       overlays a fixed column (`is_system`), and the time it was retired.
-- Why:  WS-53 CRM-C4a, spec project-docs/specs/crm_platform.md §20.7.
-- Depends on: 130_org_access_control.sql (organization) and
--       244_crm_mirror_columns.sql (crm_field_defs).
--
-- Expand only (R6): one new table, one index, and seven nullable or
-- defaulted columns. No rename, no drop, and no change to a column that
-- exists. Old code names no new object, so it runs on this schema as before.
-- Idempotent per infra/postgres/README.md: CREATE TABLE IF NOT EXISTS,
-- CREATE INDEX IF NOT EXISTS, ADD COLUMN IF NOT EXISTS, and an RLS block
-- guarded on relforcerowsecurity. A second run changes nothing.
-- Tenant-scoped (R5): `organization_id` with ENABLE and FORCE row level
-- security and one policy with USING and WITH CHECK, in the shape of
-- 251_crm_module_records.sql. `REFERENCES` comes before `DEFAULT`, because the
-- tenancy ratchet in tests/unit/test_tenancy_boundary.py matches the two with
-- no comma between. The foreign key is inline, never `ADD CONSTRAINT
-- <table>_org_fk`, because generated phase 3 adds that name.
-- `crm_field_defs.section_id` names a section with ON DELETE SET NULL. The
-- section delete route also clears it first, in the same transaction.
-- The sync never names the seven columns: `_FIELD_DEF_SQL` in
-- gateway/crm_sync/engine.py lists its own, and its conflict update skips a
-- row with `source = 'metorite'`. Pinned by tests/unit/test_crm_field_studio_r8.py,
-- which finds this file by CONTENT, never by number (R1).
-- ============================================================================

BEGIN;

CREATE TABLE IF NOT EXISTS crm_field_sections (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id  UUID NOT NULL
                     REFERENCES organization (id) ON DELETE CASCADE
                     DEFAULT current_setting('app.tenant_id', true)::uuid,
    entity           TEXT NOT NULL
                     CHECK (entity IN ('lead', 'contact', 'company', 'deal')),
    label            TEXT NOT NULL
                     CHECK (char_length(label) BETWEEN 1 AND 80),
    position         INT NOT NULL DEFAULT 0,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS crm_field_sections_entity_idx
    ON crm_field_sections (organization_id, entity, position);

DO $rls$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_class
         WHERE oid = to_regclass('crm_field_sections')
           AND relrowsecurity
           AND relforcerowsecurity
    ) THEN
        ALTER TABLE crm_field_sections ENABLE ROW LEVEL SECURITY;
        ALTER TABLE crm_field_sections FORCE  ROW LEVEL SECURITY;
        DROP POLICY IF EXISTS crm_field_sections_tenant_isolation ON crm_field_sections;
        CREATE POLICY crm_field_sections_tenant_isolation ON crm_field_sections
            USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
            WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);
    END IF;
END
$rls$;

-- The seven columns of the studio. Each is NULL or has a default, so a row
-- that the sync wrote before this migration reads as a plain custom field.
ALTER TABLE crm_field_defs
    ADD COLUMN IF NOT EXISTS section_id UUID NULL
        REFERENCES crm_field_sections (id) ON DELETE SET NULL;
ALTER TABLE crm_field_defs
    ADD COLUMN IF NOT EXISTS required BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE crm_field_defs ADD COLUMN IF NOT EXISTS help_text TEXT NULL;
ALTER TABLE crm_field_defs ADD COLUMN IF NOT EXISTS default_value JSONB NULL;
ALTER TABLE crm_field_defs ADD COLUMN IF NOT EXISTS lookup_target TEXT NULL;
ALTER TABLE crm_field_defs
    ADD COLUMN IF NOT EXISTS is_system BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE crm_field_defs ADD COLUMN IF NOT EXISTS retired_at TIMESTAMPTZ NULL;

COMMENT ON TABLE crm_field_sections IS
    'WS-53 CRM-C4a: a section of the record page that groups the fields of one '
    'entity. crm_platform.md §20.7.';

COMMIT;
