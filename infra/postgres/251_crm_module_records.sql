-- ============================================================================
-- 251_crm_module_records.sql — the generic record store of the mirror.
--
-- What: one new table, `crm_module_records`. It holds one row for each record
--       of a provider module that has no table of its own. Examples are the
--       Zoho Products, Quotes and Invoices, and each custom module. The record
--       keeps its fields in `fields` (JSONB), keyed by the provider's field API
--       name. `crm_field_defs` describes them under the entity
--       `module:<ApiName>`. A lookup stays in `fields` as the provider sends
--       it. This table has no link table.
-- Why:  WS-53 CRM-Q6a, spec project-docs/specs/crm_platform.md §18.7.
-- Depends on: 130_org_access_control.sql (organization).
--
-- Expand only (R6): one new table and two indexes. No rename, no drop, and no
-- change to an existing table. Old code names no new object.
-- Idempotent per infra/postgres/README.md: CREATE TABLE IF NOT EXISTS,
-- CREATE INDEX IF NOT EXISTS, and an RLS block guarded on
-- relforcerowsecurity. A second run changes nothing.
-- Tenant-scoped (R5): `organization_id` with ENABLE and FORCE row level
-- security and one policy with USING and WITH CHECK, in the shape of
-- 243_crm_settings.sql and 247_crm_source_profiles_pipelines.sql. `REFERENCES`
-- comes before `DEFAULT`, because the tenancy ratchet in
-- tests/unit/test_tenancy_boundary.py matches the two with no comma between.
-- The foreign key is inline, never `ADD CONSTRAINT <table>_org_fk`, because
-- generated phase 3 adds that name.
-- No native writer exists. Only the sync writes this table, through the two
-- doors in gateway/routes/crm/core.py (`upsert_module_records_from_source`
-- and `delete_module_records_from_source`). The fences are
-- tests/unit/test_crm_upsert_from_source_fence.py and
-- tests/unit/test_crm_mirror_readonly.py. Pinned by
-- tests/unit/test_crm_module_records_r8.py, which finds this file by CONTENT,
-- never by number (R1).
-- ============================================================================

BEGIN;

CREATE TABLE IF NOT EXISTS crm_module_records (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id  UUID NOT NULL
                     REFERENCES organization (id) ON DELETE CASCADE
                     DEFAULT current_setting('app.tenant_id', true)::uuid,
    module           TEXT NOT NULL,
    ext_source       TEXT NOT NULL,
    ext_id           TEXT NOT NULL,
    name             TEXT NULL,
    owner_email      TEXT NULL,
    fields           JSONB NOT NULL DEFAULT '{}',
    ext_url          TEXT NULL,
    ext_created_at   TIMESTAMPTZ NULL,
    ext_modified_at  TIMESTAMPTZ NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- One provider record, one row, for each tenant. The door's ON CONFLICT
-- clause infers this index.
CREATE UNIQUE INDEX IF NOT EXISTS crm_module_records_ext_key
    ON crm_module_records (organization_id, ext_source, module, ext_id);

-- The list of one module, newest change first (CRM-Q6b reads it).
CREATE INDEX IF NOT EXISTS crm_module_records_module_idx
    ON crm_module_records (organization_id, module, ext_modified_at DESC);

DO $rls$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_class
         WHERE oid = to_regclass('crm_module_records')
           AND relrowsecurity
           AND relforcerowsecurity
    ) THEN
        ALTER TABLE crm_module_records ENABLE ROW LEVEL SECURITY;
        ALTER TABLE crm_module_records FORCE  ROW LEVEL SECURITY;
        DROP POLICY IF EXISTS crm_module_records_tenant_isolation ON crm_module_records;
        CREATE POLICY crm_module_records_tenant_isolation ON crm_module_records
            USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
            WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);
    END IF;
END
$rls$;

COMMENT ON TABLE crm_module_records IS
    'WS-53 CRM-Q6a: one record of a provider module that has no table of its '
    'own. Only the mirror sync writes it. crm_platform.md §18.7.';

COMMIT;
