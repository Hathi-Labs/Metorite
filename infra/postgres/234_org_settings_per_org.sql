-- ============================================================================
-- 234_org_settings_per_org.sql — org settings stop being deployment-wide
-- ============================================================================
-- Spec: project-docs/specs/saas_multitenancy.md §6.3 · the same re-keying as
-- 158_per_org_credentials.sql (MT-0d), for the one table it left behind.
--
-- `org_settings` is `key TEXT PRIMARY KEY` (151_org_settings.sql) — one
-- `branding` row and one `appearance` row for the whole box. On production the
-- generated tenancy phases have added `organization_id` and FORCED row-level
-- security, but the primary key is still `key` alone. Two faults follow:
--
-- 1. Tenant B's logo upload collides with tenant A's `branding` row.
-- 2. `acb_common/org_settings.py` wrote through a connection that bound no
--    tenant, so every upload failed with `invalid input syntax for type uuid`
--    (measured 2026-10-08, two PUT /settings/branding 500s) and every read
--    returned nothing. That code now binds the tenant; this migration gives it
--    a per-organization key to write against.
--
-- Idempotent, and safe in both shapes a database can be in:
--   * production, where the generated phases already added the column and
--     forced RLS — only the primary key changes;
--   * a fresh ladder, where the column does not exist yet — it is added here,
--     as 158 does, and the generated phases later find it present.
-- Depends on: 151_org_settings.sql, 130_org_access_control.sql (organization).
-- ============================================================================

-- One transaction. The runner pipes the file to psql without -1, so without
-- this a lock timeout on ADD PRIMARY KEY would leave the table with NO key.
BEGIN;

ALTER TABLE org_settings
    ADD COLUMN IF NOT EXISTS organization_id UUID
        REFERENCES organization(id) ON DELETE CASCADE;

-- A row that predates tenancy belongs to the ONLY organization, when there is
-- only one. With two or more, the subquery is NULL and the check below
-- refuses, because no rule here can say whose row it is. It names no slug on
-- purpose (the default-slug ratchet in test_org_provisioning.py). Under forced
-- RLS and no bound tenant this matches nothing, which is correct: there the
-- column is already NOT NULL.
UPDATE org_settings
   SET organization_id = (SELECT id FROM organization
                           WHERE (SELECT count(*) FROM organization) = 1)
 WHERE organization_id IS NULL;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM org_settings WHERE organization_id IS NULL) THEN
        RAISE EXCEPTION
            'org_settings has rows with no organization — refusing to re-key. '
            'Resolve them before re-running 234.';
    END IF;
END $$;

ALTER TABLE org_settings ALTER COLUMN organization_id SET NOT NULL;

-- Re-point the primary key. Dropping first is safe: the new column set is a
-- strict superset, so no duplicate can appear.
ALTER TABLE org_settings DROP CONSTRAINT IF EXISTS org_settings_pkey;
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'org_settings_org_key_pkey'
    ) THEN
        ALTER TABLE org_settings
            ADD CONSTRAINT org_settings_org_key_pkey
            PRIMARY KEY (organization_id, key);
    END IF;
END $$;

COMMIT;
