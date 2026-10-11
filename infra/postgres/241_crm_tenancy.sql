-- ============================================================================
-- 241_crm_tenancy.sql — every crm_* table belongs to one organization.
--
-- What: the tenant column `organization_id`, FORCE row level security and
--       per-tenant unique keys on all 13 crm_* tables, on the NUMBERED
--       ladder. WS-53 CRM-T1, spec project-docs/specs/crm_platform.md §4.2,
--       decision D95.2.
--
-- Why:  `crm_contacts`, `crm_deals` and `crm_activities` had no tenant
--       column, so a member of any organization could read and write their
--       rows (CR-1). Their old `organization_id` was the customer company, and
--       144_crm.sql now renames it to `company_id` (CR-2). The unique keys
--       were global, so a second organization would collide on its first
--       import (CR-3).
--
-- Two starting points, one result:
--   * Production: the generated phases (infra/postgres/generated/) gave ten
--     of the tables the column, NOT NULL, `<t>_org_fk` and a policy on
--     2026-08-23. `ADD COLUMN IF NOT EXISTS` then skips the whole clause,
--     the inline REFERENCES too, so no second foreign key appears. The RLS
--     block skips each table that already forces row level security, so no
--     second policy appears.
--   * A fresh install: no generated phase ran (the ladder never replays
--     `generated/`, H-104). This file adds the column, with the cascade that
--     tests/unit/test_org_purge_tenant.py requires, and the policy.
--
-- The foreign key is declared INLINE and never as `ADD CONSTRAINT <t>_org_fk`,
-- because generated phase 3 adds that name and would collide with it.
-- `REFERENCES` comes before `DEFAULT`: the tenancy ratchet in
-- tests/unit/test_tenancy_boundary.py matches the two with no comma between.
--
-- ⚠️ Foreign key NAMES differ between the two shapes, so a later migration
-- must find these keys through pg_constraint and never by name. On an
-- upgraded database the COMPANY key keeps `crm_<t>_organization_id_fkey` (a
-- column rename keeps the constraint name), and the tenant key from this file
-- is `crm_<t>_organization_id_fkey1`. On a fresh install the company key is
-- `crm_<t>_company_id_fkey`, and the tenant key is
-- `crm_<t>_organization_id_fkey`. Here <t> is contacts, deals or activities.
--
-- Depends on: 130_org_access_control.sql (organization),
--             144_crm.sql (the CRM tables, and the company rename),
--             145_crm_zoho_sync.sql (crm_zoho_tombstones, crm_sync_cursors),
--             163_crm_auto_lead_cursor.sql (crm_auto_lead_cursors),
--             169_crm_stage_discipline.sql (required_fields).
-- Idempotent: ADD COLUMN IF NOT EXISTS, a fill whose WHERE empties, SET NOT
--             NULL, an RLS block guarded on relforcerowsecurity, old keys found
--             through pg_constraint, CREATE ... IF NOT EXISTS for the new ones.
-- Pinned by tests/unit/test_crm_rename_upgrade.py (R8: fresh install, upgrade,
-- replay and the production shape).
-- ============================================================================

BEGIN;

-- ── 1. The column ───────────────────────────────────────────────────────────
--
-- Same type and default as `generated/01_add_columns.sql`, plus the
-- ON DELETE CASCADE that the purge path needs.

ALTER TABLE crm_companies
    ADD COLUMN IF NOT EXISTS organization_id UUID
    REFERENCES organization (id) ON DELETE CASCADE
    DEFAULT current_setting('app.tenant_id', true)::uuid;

ALTER TABLE crm_contacts
    ADD COLUMN IF NOT EXISTS organization_id UUID
    REFERENCES organization (id) ON DELETE CASCADE
    DEFAULT current_setting('app.tenant_id', true)::uuid;

ALTER TABLE crm_leads
    ADD COLUMN IF NOT EXISTS organization_id UUID
    REFERENCES organization (id) ON DELETE CASCADE
    DEFAULT current_setting('app.tenant_id', true)::uuid;

ALTER TABLE crm_deals
    ADD COLUMN IF NOT EXISTS organization_id UUID
    REFERENCES organization (id) ON DELETE CASCADE
    DEFAULT current_setting('app.tenant_id', true)::uuid;

ALTER TABLE crm_activities
    ADD COLUMN IF NOT EXISTS organization_id UUID
    REFERENCES organization (id) ON DELETE CASCADE
    DEFAULT current_setting('app.tenant_id', true)::uuid;

ALTER TABLE crm_lead_statuses
    ADD COLUMN IF NOT EXISTS organization_id UUID
    REFERENCES organization (id) ON DELETE CASCADE
    DEFAULT current_setting('app.tenant_id', true)::uuid;

ALTER TABLE crm_deal_statuses
    ADD COLUMN IF NOT EXISTS organization_id UUID
    REFERENCES organization (id) ON DELETE CASCADE
    DEFAULT current_setting('app.tenant_id', true)::uuid;

ALTER TABLE crm_lost_reasons
    ADD COLUMN IF NOT EXISTS organization_id UUID
    REFERENCES organization (id) ON DELETE CASCADE
    DEFAULT current_setting('app.tenant_id', true)::uuid;

ALTER TABLE crm_deal_contacts
    ADD COLUMN IF NOT EXISTS organization_id UUID
    REFERENCES organization (id) ON DELETE CASCADE
    DEFAULT current_setting('app.tenant_id', true)::uuid;

ALTER TABLE crm_status_changes
    ADD COLUMN IF NOT EXISTS organization_id UUID
    REFERENCES organization (id) ON DELETE CASCADE
    DEFAULT current_setting('app.tenant_id', true)::uuid;

ALTER TABLE crm_zoho_tombstones
    ADD COLUMN IF NOT EXISTS organization_id UUID
    REFERENCES organization (id) ON DELETE CASCADE
    DEFAULT current_setting('app.tenant_id', true)::uuid;

ALTER TABLE crm_sync_cursors
    ADD COLUMN IF NOT EXISTS organization_id UUID
    REFERENCES organization (id) ON DELETE CASCADE
    DEFAULT current_setting('app.tenant_id', true)::uuid;

ALTER TABLE crm_auto_lead_cursors
    ADD COLUMN IF NOT EXISTS organization_id UUID
    REFERENCES organization (id) ON DELETE CASCADE
    DEFAULT current_setting('app.tenant_id', true)::uuid;


-- ── 2. Who owns each existing row ───────────────────────────────────────────
--
-- Only a table with rows that have no tenant needs an owner. The owner is
-- `fracktalworks` if it exists. Else it is `default`, which migration 130
-- seeds. Else it is the only organization, if exactly one exists. Else the
-- file stops and names the table, because a guess could put rows in the wrong
-- customer. The `default` step keeps a shared dev database working: it holds
-- many test organizations and no `fracktalworks`. On production every row
-- already has its tenant (the seed rows belong to `default`), so this changes
-- nothing there.

DO $fill$
DECLARE
    crm_tables CONSTANT text[] := ARRAY[
        'crm_companies', 'crm_contacts', 'crm_leads', 'crm_deals',
        'crm_activities', 'crm_lead_statuses', 'crm_deal_statuses',
        'crm_lost_reasons', 'crm_deal_contacts', 'crm_status_changes',
        'crm_zoho_tombstones', 'crm_sync_cursors', 'crm_auto_lead_cursors'
    ];
    t        text;
    owner_id uuid;
    orphans  bigint;
    orgs     bigint;
BEGIN
    SELECT id INTO owner_id FROM organization WHERE slug = 'fracktalworks';
    IF owner_id IS NULL THEN
        SELECT id INTO owner_id FROM organization WHERE slug = 'default';
    END IF;
    SELECT count(*) INTO orgs FROM organization;
    IF owner_id IS NULL AND orgs = 1 THEN
        SELECT id INTO owner_id FROM organization;
    END IF;

    FOREACH t IN ARRAY crm_tables LOOP
        EXECUTE format(
            'SELECT count(*) FROM %I WHERE organization_id IS NULL', t
        ) INTO orphans;
        CONTINUE WHEN orphans = 0;
        IF owner_id IS NULL THEN
            RAISE EXCEPTION
                '241: % has % row(s) with no organization_id. No organization '
                'is named fracktalworks or default and % organizations exist, so the '
                'owner is not clear. Set organization_id on those rows by '
                'hand, then apply this file again.', t, orphans, orgs;
        END IF;
        EXECUTE format(
            'UPDATE %I SET organization_id = $1 WHERE organization_id IS NULL', t
        ) USING owner_id;
        RAISE NOTICE '241: % row(s) of % now belong to organization %',
            orphans, t, owner_id;
    END LOOP;
END
$fill$;


-- ── 3. NOT NULL, and an index for the policy ────────────────────────────────
--
-- The index name is the one generated phase 3 uses, with IF NOT EXISTS, so
-- the two cannot collide. crm_companies keeps the name that production gave
-- it before the rename (`crm_organizations_org_idx`), as the gtd renames keep
-- theirs, so production does not get a second index on the same column.

ALTER TABLE crm_companies         ALTER COLUMN organization_id SET NOT NULL;
ALTER TABLE crm_contacts          ALTER COLUMN organization_id SET NOT NULL;
ALTER TABLE crm_leads             ALTER COLUMN organization_id SET NOT NULL;
ALTER TABLE crm_deals             ALTER COLUMN organization_id SET NOT NULL;
ALTER TABLE crm_activities        ALTER COLUMN organization_id SET NOT NULL;
ALTER TABLE crm_lead_statuses     ALTER COLUMN organization_id SET NOT NULL;
ALTER TABLE crm_deal_statuses     ALTER COLUMN organization_id SET NOT NULL;
ALTER TABLE crm_lost_reasons      ALTER COLUMN organization_id SET NOT NULL;
ALTER TABLE crm_deal_contacts     ALTER COLUMN organization_id SET NOT NULL;
ALTER TABLE crm_status_changes    ALTER COLUMN organization_id SET NOT NULL;
ALTER TABLE crm_zoho_tombstones   ALTER COLUMN organization_id SET NOT NULL;
ALTER TABLE crm_sync_cursors      ALTER COLUMN organization_id SET NOT NULL;
ALTER TABLE crm_auto_lead_cursors ALTER COLUMN organization_id SET NOT NULL;

CREATE INDEX IF NOT EXISTS crm_organizations_org_idx ON crm_companies (organization_id);
CREATE INDEX IF NOT EXISTS crm_contacts_org_idx ON crm_contacts (organization_id);
CREATE INDEX IF NOT EXISTS crm_leads_org_idx ON crm_leads (organization_id);
CREATE INDEX IF NOT EXISTS crm_deals_org_idx ON crm_deals (organization_id);
CREATE INDEX IF NOT EXISTS crm_activities_org_idx ON crm_activities (organization_id);
CREATE INDEX IF NOT EXISTS crm_lead_statuses_org_idx ON crm_lead_statuses (organization_id);
CREATE INDEX IF NOT EXISTS crm_deal_statuses_org_idx ON crm_deal_statuses (organization_id);
CREATE INDEX IF NOT EXISTS crm_lost_reasons_org_idx ON crm_lost_reasons (organization_id);
CREATE INDEX IF NOT EXISTS crm_deal_contacts_org_idx ON crm_deal_contacts (organization_id);
CREATE INDEX IF NOT EXISTS crm_status_changes_org_idx ON crm_status_changes (organization_id);
CREATE INDEX IF NOT EXISTS crm_zoho_tombstones_org_idx ON crm_zoho_tombstones (organization_id);
CREATE INDEX IF NOT EXISTS crm_sync_cursors_org_idx ON crm_sync_cursors (organization_id);
CREATE INDEX IF NOT EXISTS crm_auto_lead_cursors_org_idx ON crm_auto_lead_cursors (organization_id);


-- ── 4. Row level security ───────────────────────────────────────────────────
--
-- The block of 238_whatsapp_bot_messages.sql, once for each table. A table
-- that already forces row level security keeps the policy it has. On
-- production the generated phase put `<t>_tenant_isolation` on ten of them,
-- and a renamed table keeps the policy name it had. So crm_companies uses
-- `crm_organizations_tenant_isolation`, and so does the committed phase 4.
-- Each policy name here is the one phase 4 uses, so phase 4 replaces it and
-- adds no second policy.

DO $rls$
DECLARE
    crm_tables CONSTANT text[] := ARRAY[
        'crm_companies', 'crm_contacts', 'crm_leads', 'crm_deals',
        'crm_activities', 'crm_lead_statuses', 'crm_deal_statuses',
        'crm_lost_reasons', 'crm_deal_contacts', 'crm_status_changes',
        'crm_zoho_tombstones', 'crm_sync_cursors', 'crm_auto_lead_cursors'
    ];
    t      text;
    policy text;
BEGIN
    FOREACH t IN ARRAY crm_tables LOOP
        policy := CASE t
            WHEN 'crm_companies' THEN 'crm_organizations_tenant_isolation'
            ELSE t || '_tenant_isolation'
        END;
        CONTINUE WHEN EXISTS (
            SELECT 1 FROM pg_class
             WHERE oid = to_regclass(format('public.%I', t))
               AND relrowsecurity
               AND relforcerowsecurity
        );
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
        EXECUTE format('ALTER TABLE %I FORCE  ROW LEVEL SECURITY', t);
        EXECUTE format('DROP POLICY IF EXISTS %I ON %I', policy, t);
        EXECUTE format(
            'CREATE POLICY %I ON %I '
            '    USING      (organization_id = '
            '        current_setting(''app.tenant_id'', true)::uuid) '
            '    WITH CHECK (organization_id = '
            '        current_setting(''app.tenant_id'', true)::uuid)',
            policy, t);
    END LOOP;
END
$rls$;


-- ── 5. Unique keys per tenant (CR-3) ────────────────────────────────────────
--
-- The old one-column keys are found through pg_constraint and never by name.
-- A fresh install calls one `crm_companies_zoho_id_key`, and an upgraded
-- database calls the same key `crm_organizations_zoho_id_key`, because a
-- rename keeps constraint names. The new keys are unique INDEXES, the shape of
-- 223_email_accounts_unique_per_tenant.sql, and `ON CONFLICT (organization_id,
-- zoho_id)` in routes/crm/core.py infers them.

DO $old_keys$
DECLARE
    old_key RECORD;
BEGIN
    FOR old_key IN
        SELECT rel.relname AS tbl, c.conname
          FROM pg_constraint c
          JOIN pg_class rel ON rel.oid = c.conrelid
          JOIN pg_attribute a
            ON a.attrelid = c.conrelid AND a.attnum = c.conkey[1]
         WHERE c.contype = 'u'
           AND array_length(c.conkey, 1) = 1
           AND rel.relnamespace = to_regnamespace(current_schema())
           AND (rel.relname, a.attname) IN (
                ('crm_companies', 'zoho_id'),
                ('crm_contacts', 'zoho_id'),
                ('crm_leads', 'zoho_id'),
                ('crm_deals', 'zoho_id'),
                ('crm_activities', 'zoho_id'),
                ('crm_lead_statuses', 'name'),
                ('crm_deal_statuses', 'name'),
                ('crm_lost_reasons', 'label'))
    LOOP
        EXECUTE format('ALTER TABLE %I DROP CONSTRAINT %I',
                       old_key.tbl, old_key.conname);
        RAISE NOTICE '241: dropped the global key % on %',
            old_key.conname, old_key.tbl;
    END LOOP;
END
$old_keys$;

CREATE UNIQUE INDEX IF NOT EXISTS uq_crm_companies_org_zoho_id
    ON crm_companies (organization_id, zoho_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_crm_contacts_org_zoho_id
    ON crm_contacts (organization_id, zoho_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_crm_leads_org_zoho_id
    ON crm_leads (organization_id, zoho_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_crm_deals_org_zoho_id
    ON crm_deals (organization_id, zoho_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_crm_activities_org_zoho_id
    ON crm_activities (organization_id, zoho_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_crm_lead_statuses_org_name
    ON crm_lead_statuses (organization_id, name);
CREATE UNIQUE INDEX IF NOT EXISTS uq_crm_deal_statuses_org_name
    ON crm_deal_statuses (organization_id, name);
CREATE UNIQUE INDEX IF NOT EXISTS uq_crm_lost_reasons_org_label
    ON crm_lost_reasons (organization_id, label);

-- One pull cursor per organization and module. The primary key was `module`
-- alone, so a second organization would have read the first one's watermark.
DO $cursor_key$
DECLARE
    pk_name text;
    pk_cols text[];
BEGIN
    SELECT c.conname,
           array_agg(a.attname::text ORDER BY k.ord)
      INTO pk_name, pk_cols
      FROM pg_constraint c
      CROSS JOIN LATERAL unnest(c.conkey) WITH ORDINALITY AS k(attnum, ord)
      JOIN pg_attribute a
        ON a.attrelid = c.conrelid AND a.attnum = k.attnum
     WHERE c.conrelid = to_regclass('public.crm_sync_cursors')
       AND c.contype = 'p'
     GROUP BY c.conname;

    IF pk_cols IS DISTINCT FROM ARRAY['organization_id', 'module'] THEN
        IF pk_name IS NOT NULL THEN
            EXECUTE format('ALTER TABLE crm_sync_cursors DROP CONSTRAINT %I',
                           pk_name);
        END IF;
        ALTER TABLE crm_sync_cursors
            ADD CONSTRAINT crm_sync_cursors_pkey
            PRIMARY KEY (organization_id, module);
        RAISE NOTICE '241: crm_sync_cursors is keyed (organization_id, module)';
    END IF;
END
$cursor_key$;


-- ── 6. The renamed column in stage requirements ─────────────────────────────
--
-- `required_fields` names deal columns. A stage that required the company now
-- names `company_id`, because `organization_id` on crm_deals is the tenant.
--
-- The runner connects as the table owner, and FORCE binds the owner too
-- unless it is a superuser or BYPASSRLS (see 221). Such a role sees only the
-- bound tenant's rows, so one unbound UPDATE would change nothing and say
-- nothing. So that role runs the UPDATE once for each organization, with that
-- organization bound, as 221 does. The binding is LOCAL to this transaction.
-- A role that bypasses row level security runs one UPDATE and binds nothing,
-- so a test session that applies the ladder keeps an unset tenant.

DO $required_fields$
DECLARE
    org_id uuid;
BEGIN
    IF (SELECT rolsuper OR rolbypassrls FROM pg_roles
         WHERE rolname = current_user) THEN
        UPDATE crm_deal_statuses
           SET required_fields =
               array_replace(required_fields, 'organization_id', 'company_id')
         WHERE 'organization_id' = ANY (required_fields);
        RETURN;
    END IF;

    FOR org_id IN SELECT id FROM organization LOOP
        PERFORM set_config('app.tenant_id', org_id::text, true);
        UPDATE crm_deal_statuses
           SET required_fields =
               array_replace(required_fields, 'organization_id', 'company_id')
         WHERE 'organization_id' = ANY (required_fields);
    END LOOP;
END
$required_fields$;

COMMIT;
