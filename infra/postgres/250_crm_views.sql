-- ============================================================================
-- 250_crm_views.sql — saved views of the CRM lists and boards.
--
-- What: `crm_views`, one row for each saved view. A view holds the filters, the
--       sort, the chosen custom columns, the layout (list or board) and the
--       deal pipeline of one CRM list. It follows the shape of `pm_views`.
--         * `entity`: leads, deals, contacts or organizations.
--         * `owner_email`: the member who made the view.
--         * `shared`: FALSE is a private view. TRUE shows it to the whole
--           organization. Only an admin sets TRUE (the route checks this).
--         * `filters`: the filter parameters of the list route, as JSON.
--         * `sort`: the sort key and direction, as JSON, or NULL.
--         * `columns`: the custom field keys that the list shows, or NULL.
-- Why:  WS-53 CRM-Q8 and CRM-S2, spec project-docs/specs/crm_platform.md
--       §5.3 and §18.5. A view is not a CRM record, so it works in Mirror
--       mode as well.
-- Depends on: 130_org_access_control.sql (organization).
--
-- Expand only (R6): one new table. No rename, no drop, no UPDATE. Old code
-- names no new object.
-- Idempotent per infra/postgres/README.md: CREATE TABLE IF NOT EXISTS, and an
-- RLS block guarded on relforcerowsecurity. A second run changes nothing.
-- Tenant-scoped (R5): `organization_id` with ENABLE and FORCE row level
-- security and one policy with USING and WITH CHECK, in the shape of
-- 243_crm_settings.sql. `REFERENCES` comes before `DEFAULT`, because the
-- tenancy ratchet in tests/unit/test_tenancy_boundary.py matches the two with
-- no comma between. The generated phase files carry the same table.
-- Pinned by tests/unit/test_crm_views_r8.py, which finds this file by CONTENT,
-- never by number (R1).
-- ============================================================================

BEGIN;

CREATE TABLE IF NOT EXISTS crm_views (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id  UUID NOT NULL
                     REFERENCES organization (id) ON DELETE CASCADE
                     DEFAULT current_setting('app.tenant_id', true)::uuid,
    entity           TEXT NOT NULL,
    name             TEXT NOT NULL,
    owner_email      TEXT NOT NULL,
    shared           BOOLEAN NOT NULL DEFAULT FALSE,
    layout           TEXT NOT NULL DEFAULT 'list',
    filters          JSONB NOT NULL DEFAULT '{}'::jsonb,
    sort             JSONB,
    columns          JSONB,
    pipeline_id      UUID,
    position         INT NOT NULL DEFAULT 0,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT crm_views_entity_known CHECK (
        entity IN ('leads', 'deals', 'contacts', 'organizations')
    ),
    CONSTRAINT crm_views_layout_known CHECK (layout IN ('list', 'board'))
);

CREATE INDEX IF NOT EXISTS crm_views_org_entity_idx
    ON crm_views (organization_id, entity, position);

DO $rls$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_class
         WHERE oid = 'crm_views'::regclass
           AND relrowsecurity
           AND relforcerowsecurity
    ) THEN
        EXECUTE 'ALTER TABLE crm_views ENABLE ROW LEVEL SECURITY';
        EXECUTE 'ALTER TABLE crm_views FORCE  ROW LEVEL SECURITY';
        EXECUTE 'DROP POLICY IF EXISTS crm_views_tenant_isolation '
                'ON crm_views';
        EXECUTE 'CREATE POLICY crm_views_tenant_isolation '
                'ON crm_views '
                '    USING      (organization_id = '
                '        current_setting(''app.tenant_id'', true)::uuid) '
                '    WITH CHECK (organization_id = '
                '        current_setting(''app.tenant_id'', true)::uuid)';
    END IF;
END
$rls$;

COMMENT ON TABLE crm_views IS
    'WS-53 CRM-Q8: a saved view of a CRM list or board. Private to its owner '
    'unless shared. crm_platform.md §5.3, §18.5.';

COMMIT;
