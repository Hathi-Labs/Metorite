-- ============================================================================
-- 244_crm_mirror_columns.sql — the columns a Zoho mirror needs, and the
-- table that describes the custom fields.
--
-- What: the five CRM entity tables (crm_companies, crm_contacts, crm_leads,
--       crm_deals, crm_activities) gain `ext_source`, `ext_id`,
--       `ext_modified_at` and `custom`. Each gets a per-tenant unique index
--       on (organization_id, ext_source, ext_id). `crm_deals` also gains
--       `currency` and `exchange_rate`. A new table, `crm_field_defs`, holds
--       one row per field a tenant has: its label, type and options.
--       WS-53 CRM-Z3, spec project-docs/specs/crm_platform.md §3.5 and §5.3.
--
-- Why:  The mirror writes rows that come from a provider. It keys them on
--       the provider name and the provider id, so a second provider needs no
--       new column. `zoho_id` stays: the live sync and the importer still
--       read it. `custom` holds every provider field that has no native
--       column, and `crm_field_defs` says what each key means.
--
-- Safe to replay (R6): every new column is nullable or has a default, the
-- NULLs in the unique index never collide, and `crm_deals.currency` already
-- exists (144_crm.sql, NOT NULL DEFAULT 'INR') so its ADD is a no-op.
-- Nothing is renamed and nothing is tightened.
--
-- `crm_field_defs` is tenant-scoped on the numbered ladder. The foreign key
-- is declared INLINE and never as `ADD CONSTRAINT crm_field_defs_org_fk`,
-- because generated phase 3 adds that name. `REFERENCES` comes before
-- `DEFAULT`, as the ratchet in tests/unit/test_tenancy_boundary.py expects.
--
-- Depends on: 130_org_access_control.sql (organization),
--             241_crm_tenancy.sql (the tenant column on the five tables).
-- Idempotent: ADD COLUMN IF NOT EXISTS, CREATE ... IF NOT EXISTS, and an RLS
--             block guarded on relforcerowsecurity.
-- ============================================================================

BEGIN;

-- ── 1. The mirror columns on the five entity tables ─────────────────────────

ALTER TABLE crm_companies
    ADD COLUMN IF NOT EXISTS ext_source      TEXT,
    ADD COLUMN IF NOT EXISTS ext_id          TEXT,
    ADD COLUMN IF NOT EXISTS ext_modified_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS custom          JSONB NOT NULL DEFAULT '{}';

ALTER TABLE crm_contacts
    ADD COLUMN IF NOT EXISTS ext_source      TEXT,
    ADD COLUMN IF NOT EXISTS ext_id          TEXT,
    ADD COLUMN IF NOT EXISTS ext_modified_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS custom          JSONB NOT NULL DEFAULT '{}';

ALTER TABLE crm_leads
    ADD COLUMN IF NOT EXISTS ext_source      TEXT,
    ADD COLUMN IF NOT EXISTS ext_id          TEXT,
    ADD COLUMN IF NOT EXISTS ext_modified_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS custom          JSONB NOT NULL DEFAULT '{}';

ALTER TABLE crm_deals
    ADD COLUMN IF NOT EXISTS ext_source      TEXT,
    ADD COLUMN IF NOT EXISTS ext_id          TEXT,
    ADD COLUMN IF NOT EXISTS ext_modified_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS custom          JSONB NOT NULL DEFAULT '{}',
    ADD COLUMN IF NOT EXISTS currency        TEXT,
    ADD COLUMN IF NOT EXISTS exchange_rate   NUMERIC;

ALTER TABLE crm_activities
    ADD COLUMN IF NOT EXISTS ext_source      TEXT,
    ADD COLUMN IF NOT EXISTS ext_id          TEXT,
    ADD COLUMN IF NOT EXISTS ext_modified_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS custom          JSONB NOT NULL DEFAULT '{}';


-- ── 2. One external record, one row, per tenant ─────────────────────────────
--
-- Unique INDEXES, the shape of 241. `ON CONFLICT (organization_id, ext_source,
-- ext_id)` in routes/crm/core.py::upsert_from_source infers them. A native
-- row has NULL in both columns, and NULLs never collide.

CREATE UNIQUE INDEX IF NOT EXISTS crm_companies_ext_key
    ON crm_companies (organization_id, ext_source, ext_id);
CREATE UNIQUE INDEX IF NOT EXISTS crm_contacts_ext_key
    ON crm_contacts (organization_id, ext_source, ext_id);
CREATE UNIQUE INDEX IF NOT EXISTS crm_leads_ext_key
    ON crm_leads (organization_id, ext_source, ext_id);
CREATE UNIQUE INDEX IF NOT EXISTS crm_deals_ext_key
    ON crm_deals (organization_id, ext_source, ext_id);
CREATE UNIQUE INDEX IF NOT EXISTS crm_activities_ext_key
    ON crm_activities (organization_id, ext_source, ext_id);


-- ── 3. crm_field_defs — what each custom field means ────────────────────────

CREATE TABLE IF NOT EXISTS crm_field_defs (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id   UUID NOT NULL
                      REFERENCES organization (id) ON DELETE CASCADE
                      DEFAULT current_setting('app.tenant_id', true)::uuid,
    entity            TEXT NOT NULL,
    key               TEXT NOT NULL,
    label             TEXT NOT NULL,
    type              TEXT NOT NULL DEFAULT 'text',
    options           JSONB,
    source            TEXT,
    provider_api_name TEXT,
    position          INT NOT NULL DEFAULT 0,
    visible           BOOLEAN NOT NULL DEFAULT TRUE,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (organization_id, entity, key)
);

CREATE INDEX IF NOT EXISTS crm_field_defs_org_idx ON crm_field_defs (organization_id);

DO $rls$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_class
         WHERE oid = to_regclass('public.crm_field_defs')
           AND relrowsecurity
           AND relforcerowsecurity
    ) THEN
        ALTER TABLE crm_field_defs ENABLE ROW LEVEL SECURITY;
        ALTER TABLE crm_field_defs FORCE  ROW LEVEL SECURITY;
        DROP POLICY IF EXISTS crm_field_defs_tenant_isolation ON crm_field_defs;
        CREATE POLICY crm_field_defs_tenant_isolation ON crm_field_defs
            USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
            WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);
    END IF;
END
$rls$;

COMMIT;
