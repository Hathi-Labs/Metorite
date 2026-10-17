-- ============================================================================
-- 281_crm_digests.sql — the weekly CRM digest of one org.
--
-- What: one new table, `crm_digests`. After a sync run, a background step of
--       a listed org reads up to 8 CRM-Q25 aggregates for the week before,
--       sends them as JSON to the powerful tier, and checks each number of
--       the text against them. A row holds the Monday of the week in the CRM
--       zone (`week_start`), the numbers as `jsonb`, the text, the request id
--       of the newest try, the count of tries and the times. `body` stays
--       NULL until a text passes the number check. Only
--       gateway/crm_sync/digest.py writes it, and only `GET /crm/digests` and
--       the My Day `digest_ready` card read it.
-- Why:  WS-53 CRM-Q28, spec project-docs/specs/crm_platform.md §18.11.2.
--       The unique index on (organization_id, week_start) allows one digest
--       for each week. `tries` is the cost bound: the step adds one try
--       BEFORE each request goes out, and the CHECK stops it at 3.
-- Depends on: 130_org_access_control.sql (organization).
--
-- Expand only (R6): one new table and its index. No rename, no drop, and no
-- change to an existing table. Old code names no new object.
-- Idempotent per infra/postgres/README.md: CREATE TABLE IF NOT EXISTS, CREATE
-- INDEX IF NOT EXISTS and an RLS block guarded on relforcerowsecurity. A
-- second run changes nothing.
-- Tenant-scoped (R5): `organization_id` with ENABLE and FORCE row level
-- security and one policy with USING and WITH CHECK, in the shape of
-- 279_crm_note_vectors.sql. `REFERENCES` comes before `DEFAULT`, because the
-- tenancy ratchet in tests/unit/test_tenancy_boundary.py matches the two with
-- no comma between. The foreign key is inline, never `ADD CONSTRAINT
-- <table>_org_fk`, because generated phase 3 adds that name.
-- The number is taken at build time (R1). On 2026-10-17 origin/main held 280
-- (#1112). Check it again at merge.
-- Pinned by tests/unit/test_crm_digest_r8.py, which finds this file by
-- CONTENT, never by number (R1).
-- ============================================================================

BEGIN;

CREATE TABLE IF NOT EXISTS crm_digests (
    organization_id  UUID NOT NULL
                     REFERENCES organization (id) ON DELETE CASCADE
                     DEFAULT current_setting('app.tenant_id', true)::uuid,
    id               UUID NOT NULL DEFAULT gen_random_uuid(),
    -- The Monday of the week that the digest counts, in the CRM zone.
    week_start       DATE NOT NULL,
    -- The numbers of the CRM-Q25 aggregates, as the prompt held them.
    numbers          JSONB NOT NULL DEFAULT '{}'::jsonb,
    -- The text. NULL until a text passes the number check.
    body             TEXT NULL,
    -- The request id of the newest try.
    request_id       TEXT NULL,
    tries            SMALLINT NOT NULL DEFAULT 0,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- When the text was stored. The My Day card reads it.
    stored_at        TIMESTAMPTZ NULL,
    PRIMARY KEY (id),
    CONSTRAINT crm_digests_tries_range CHECK (tries >= 0 AND tries <= 3)
);

CREATE UNIQUE INDEX IF NOT EXISTS crm_digests_org_week_uidx
    ON crm_digests (organization_id, week_start);

DO $rls$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_class
         WHERE oid = to_regclass('crm_digests')
           AND relrowsecurity
           AND relforcerowsecurity
    ) THEN
        ALTER TABLE crm_digests ENABLE ROW LEVEL SECURITY;
        ALTER TABLE crm_digests FORCE  ROW LEVEL SECURITY;
        DROP POLICY IF EXISTS crm_digests_tenant_isolation ON crm_digests;
        CREATE POLICY crm_digests_tenant_isolation ON crm_digests
            USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
            WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);
    END IF;
END
$rls$;

COMMENT ON TABLE crm_digests IS
    'WS-53 CRM-Q28: the weekly CRM digest of one org. The numbers come from '
    'CRM-Q25 aggregates, and the text holds no number that is not in them. '
    'Only gateway/crm_sync/digest.py writes it. crm_platform.md §18.11.2.';

COMMIT;
