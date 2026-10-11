-- ============================================================================
-- 242_crm_settings.sql — one CRM settings row for each organization.
--
-- What: `crm_settings`, keyed on the tenant `organization_id`. It holds the
--       CRM mode of the organization and who in it may open the CRM.
--         * `mode`: NULL (not chosen yet), `native` or `mirror`. In CRM-T5
--           only the change from NULL to `native` is allowed. The route
--           `PATCH /crm/settings` seeds the stages and lost reasons of the
--           organization in that same transaction (routes/crm/seed.py).
--         * `access`: `admins` (the default), `everyone` or `groups`.
--         * `access_groups`: the `group:<slug>` subjects that `groups`
--           admits. The route validates each one through the Projects
--           grant vocabulary (D12).
--       A missing row reads as mode NULL and access `admins`.
-- Why:  WS-53 CRM-T5, spec project-docs/specs/crm_platform.md §3.1 (the
--       mode), §10 item 6 and §16 Q3 (the admin picks who sees the CRM, and
--       the default is admins only).
-- Depends on: 130_org_access_control.sql (organization),
--             241_crm_tenancy.sql (the per-tenant keys that the seed uses).
--
-- Expand only (R6): one new table. No rename, no drop, no UPDATE. Old code
-- names no new object.
-- Idempotent per infra/postgres/README.md: CREATE TABLE IF NOT EXISTS, and an
-- RLS block guarded on relforcerowsecurity. A second run changes nothing.
-- Tenant-scoped (R5): the primary key IS `organization_id`, with ENABLE and
-- FORCE row level security and one policy with USING and WITH CHECK, in the
-- shape of 238_whatsapp_bot_messages.sql. `REFERENCES` comes before
-- `DEFAULT`, because the tenancy ratchet in tests/unit/test_tenancy_boundary.py
-- matches the two with no comma between. The column default is the bound
-- tenant, as 241 does. The generated phase files carry the same table.
-- Pinned by tests/unit/test_crm_access_r8.py, which finds this file by
-- CONTENT, never by number (R1).
-- ============================================================================

BEGIN;

CREATE TABLE IF NOT EXISTS crm_settings (
    organization_id  UUID PRIMARY KEY
                     REFERENCES organization (id) ON DELETE CASCADE
                     DEFAULT current_setting('app.tenant_id', true)::uuid,
    mode             TEXT NULL,
    access           TEXT NOT NULL DEFAULT 'admins',
    access_groups    TEXT[] NOT NULL DEFAULT '{}',
    updated_by       TEXT,
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT crm_settings_mode_known CHECK (
        mode IN ('native', 'mirror')
    ),
    CONSTRAINT crm_settings_access_known CHECK (
        access IN ('admins', 'everyone', 'groups')
    )
);

DO $rls$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_class
         WHERE oid = 'crm_settings'::regclass
           AND relrowsecurity
           AND relforcerowsecurity
    ) THEN
        EXECUTE 'ALTER TABLE crm_settings ENABLE ROW LEVEL SECURITY';
        EXECUTE 'ALTER TABLE crm_settings FORCE  ROW LEVEL SECURITY';
        EXECUTE 'DROP POLICY IF EXISTS crm_settings_tenant_isolation '
                'ON crm_settings';
        EXECUTE 'CREATE POLICY crm_settings_tenant_isolation '
                'ON crm_settings '
                '    USING      (organization_id = '
                '        current_setting(''app.tenant_id'', true)::uuid) '
                '    WITH CHECK (organization_id = '
                '        current_setting(''app.tenant_id'', true)::uuid)';
    END IF;
END
$rls$;

COMMENT ON TABLE crm_settings IS
    'WS-53 CRM-T5: the CRM mode of an organization and who may open the CRM. '
    'No row means mode NULL and access admins. crm_platform.md §3.1, §16 Q3.';

COMMIT;
