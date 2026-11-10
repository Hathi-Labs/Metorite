-- ============================================================================
-- 283_crm_business_rules.sql — the business rules of the CRM of one org.
--
-- What: one new table, `crm_business_rules`. A rule is a short free-text
--       statement of how the org reads its own data, such as "All amounts
--       net of GST" or "IP is our real earning. Rent and EB are
--       pass-through." A CRM admin writes a rule for the org
--       (`scope = 'org'`). Each member with CRM access writes a rule for
--       themselves only (`scope = 'member'`, with `member_email`). Only
--       gateway/routes/crm/rules.py writes it. The org-facts seam reads it
--       as subject `rule:<slug>`, and the mapping prompts and the
--       recommendation of an open question read it too.
-- Why:  WS-53 CRM-Q33, spec project-docs/specs/crm_platform.md §18.15.
--       Owner direction 2026-10-18: "Store their answers as preferences."
--       A rule is no part of the mapping plan, because a member who is not
--       an admin writes one, and the plan is the admin's versioned store.
-- Depends on: 130_org_access_control.sql (organization).
--
-- Expand only (R6): one new table and its index. No rename, no drop, and no
-- change to an existing table. Old code names no new object.
-- Idempotent per infra/postgres/README.md: CREATE TABLE IF NOT EXISTS, CREATE
-- INDEX IF NOT EXISTS and an RLS block guarded on relforcerowsecurity. A
-- second run changes nothing.
-- Tenant-scoped (R5): `organization_id` with ENABLE and FORCE row level
-- security and one policy with USING and WITH CHECK, in the shape of
-- 281_crm_digests.sql. `REFERENCES` comes before `DEFAULT`, because the
-- tenancy ratchet in tests/unit/test_tenancy_boundary.py matches the two with
-- no comma between. The foreign key is inline, never `ADD CONSTRAINT
-- <table>_org_fk`, because generated phase 3 adds that name.
-- The number is taken at build time (R1). On 2026-10-18 origin/main held 282
-- (#1142), and no open branch held 283. Check it again at merge.
-- Pinned by tests/unit/test_crm_rules_r8.py, which finds this file by
-- CONTENT, never by number (R1).
-- ============================================================================

BEGIN;

CREATE TABLE IF NOT EXISTS crm_business_rules (
    organization_id  UUID NOT NULL
                     REFERENCES organization (id) ON DELETE CASCADE
                     DEFAULT current_setting('app.tenant_id', true)::uuid,
    id               UUID NOT NULL DEFAULT gen_random_uuid(),
    -- 'org': a CRM admin wrote it for everyone. 'member': one member wrote
    -- it for themselves only.
    scope            TEXT NOT NULL,
    -- The member of a 'member' rule, lower case. NULL on an 'org' rule.
    member_email     TEXT NULL,
    -- The short id of the rule in the subject `rule:<slug>`.
    slug             TEXT NOT NULL,
    -- The words of the rule, as the member confirmed them.
    body             TEXT NOT NULL,
    -- Where the member confirmed it: the CRM settings, the web chat or
    -- WhatsApp.
    origin           TEXT NOT NULL,
    created_by       TEXT NOT NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_by       TEXT NOT NULL,
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (id),
    CONSTRAINT crm_business_rules_scope CHECK (scope IN ('org', 'member')),
    CONSTRAINT crm_business_rules_member CHECK (
        (scope = 'org' AND member_email IS NULL)
        OR (scope = 'member' AND member_email IS NOT NULL)
    ),
    CONSTRAINT crm_business_rules_body CHECK (char_length(body) BETWEEN 1 AND 300),
    CONSTRAINT crm_business_rules_slug CHECK (slug ~ '^[a-z0-9][a-z0-9-]{0,47}$'),
    CONSTRAINT crm_business_rules_origin CHECK (origin IN ('settings', 'chat', 'whatsapp'))
);

-- One slug for each owner of a rule: the org, or one member.
CREATE UNIQUE INDEX IF NOT EXISTS crm_business_rules_owner_slug_uidx
    ON crm_business_rules (organization_id, scope, COALESCE(member_email, ''), slug);

DO $rls$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_class
         WHERE oid = to_regclass('crm_business_rules')
           AND relrowsecurity
           AND relforcerowsecurity
    ) THEN
        ALTER TABLE crm_business_rules ENABLE ROW LEVEL SECURITY;
        ALTER TABLE crm_business_rules FORCE  ROW LEVEL SECURITY;
        DROP POLICY IF EXISTS crm_business_rules_tenant_isolation ON crm_business_rules;
        CREATE POLICY crm_business_rules_tenant_isolation ON crm_business_rules
            USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
            WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);
    END IF;
END
$rls$;

COMMENT ON TABLE crm_business_rules IS
    'WS-53 CRM-Q33: the business rules of the CRM of one org, as free text. '
    'An admin writes an org rule, and a member writes a rule for themselves. '
    'Only gateway/routes/crm/rules.py writes it. crm_platform.md §18.15.';

COMMIT;
