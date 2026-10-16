-- ============================================================================
-- 274_crm_field_fill.sql — the fill rank of each field of the mirror.
--
-- What: one new table, `crm_field_fill`. It holds one row for each key of an
--       entity: how many records fill it (`filled`), how many records the
--       entity holds (`total`), and how many different filled values it has
--       (`distinct_count`). `entity` is a word of `crm_field_defs.entity`:
--       `lead`, `contact`, `company`, `deal` or `module:<ApiName>`. `key` is
--       a fixed column (`email`, `status_id`), `custom.<key>` for a custom
--       field of a core entity, or the API name of a field of a module.
-- Why:  WS-53 CRM-Q17a-1, spec project-docs/specs/crm_platform.md §18.9.3.
--       The lists and the record page rank fields by fill, and the export
--       leaves out an empty standard column.
-- Depends on: 130_org_access_control.sql (organization).
--
-- Expand only (R6): one new table. No rename, no drop. Old code names no new
-- object.
-- Idempotent per infra/postgres/README.md: CREATE TABLE IF NOT EXISTS and an
-- RLS block guarded on relforcerowsecurity. A second run changes nothing.
-- Tenant-scoped (R5): `organization_id` with ENABLE and FORCE row level
-- security and one policy with USING and WITH CHECK, in the shape of
-- 247_crm_source_profiles_pipelines.sql. `REFERENCES` comes before `DEFAULT`,
-- and the foreign key is inline, for the reasons that 247 gives.
-- Only gateway/crm_sync/fill.py writes the table. The CRM-X0 start over
-- empties it (core.RESET_TABLES). Pinned by tests/unit/test_crm_fill_rank_r8.py,
-- which finds this file by CONTENT, never by number (R1).
-- ============================================================================

BEGIN;

CREATE TABLE IF NOT EXISTS crm_field_fill (
    organization_id  UUID NOT NULL
                     REFERENCES organization (id) ON DELETE CASCADE
                     DEFAULT current_setting('app.tenant_id', true)::uuid,
    entity           TEXT NOT NULL,
    key              TEXT NOT NULL,
    filled           INT NOT NULL DEFAULT 0,
    total            INT NOT NULL DEFAULT 0,
    distinct_count   INT NOT NULL DEFAULT 0,
    computed_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (organization_id, entity, key)
);

DO $rls$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_class
         WHERE oid = to_regclass('crm_field_fill')
           AND relrowsecurity
           AND relforcerowsecurity
    ) THEN
        ALTER TABLE crm_field_fill ENABLE ROW LEVEL SECURITY;
        ALTER TABLE crm_field_fill FORCE  ROW LEVEL SECURITY;
        DROP POLICY IF EXISTS crm_field_fill_tenant_isolation ON crm_field_fill;
        CREATE POLICY crm_field_fill_tenant_isolation ON crm_field_fill
            USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
            WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);
    END IF;
END
$rls$;

COMMENT ON TABLE crm_field_fill IS
    'WS-53 CRM-Q17a-1: the fill rank of each key of each entity of the mirror. '
    'Only the sync writes it. crm_platform.md §18.9.3.';

COMMIT;
