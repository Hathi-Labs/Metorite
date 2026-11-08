-- ============================================================================
-- 388_email_signature_settings.sql — the signature mode of each mailbox
-- (WS-17 SG-2, Metorite task 2178).
--
-- What: one new table, `email_signature_settings`. A row holds the signature
--       mode of one mailbox: `custom`, `template` or `company` (D-EM-64), the
--       layout and the fields of a layout signature, and `reply_mode`: what a
--       reply and a forward carry (`full`, `short` or `none`, D-EM-71).
-- Why:  WS-17 SG-2, spec project-docs/specs/email_app_master_plan.md §23.2,
--       §23.3.3 and §23.4. The resolver `resolve_signature` in
--       gateway/routes/email/signature.py reads the row, and it reads it only
--       while EMAIL_SIGNATURE_TEMPLATES_ORGS holds the org.
-- Depends on: 17_email_accounts.sql (email_accounts),
--             130_org_access_control.sql (organization).
--
-- Expand only (R6): one new table. No rename, no drop, no backfill, and no
-- change to an existing table. `email_assistant_settings.signature` stays the
-- store of the free HTML of a `custom` mailbox.
-- Rule 7 (§23.0): a mailbox with NO row here is `custom`, so it sends the
-- same bytes as before. The file writes no row.
-- Idempotent per infra/postgres/README.md: CREATE TABLE IF NOT EXISTS and an
-- RLS block guarded on relforcerowsecurity. A second run changes nothing.
-- Tenant-scoped (R5): its own `organization_id`, with ENABLE and FORCE row
-- level security and one policy with USING and WITH CHECK, in the shape of
-- 341. `REFERENCES` comes before `DEFAULT`, because the tenancy ratchet in
-- tests/unit/test_tenancy_boundary.py matches the two with no comma between.
-- The foreign key is inline, never `ADD CONSTRAINT <table>_org_fk`, because
-- generated phase 3 adds that name.
-- The key is `account_id`, a child of `email_accounts` with ON DELETE CASCADE
-- (gateway AGENTS.md item 8), so a disconnect takes the row with the mailbox.
-- Each route that writes it (SG-3) proves send access to the mailbox first
-- (`_assert_account_owner(..., level="send")`, §23.4).
-- The number is taken at build time (R1). On 2026-11-09 origin/main held 378
-- (378_email_signature_images.sql). Open PRs held 379 and 383, the branch of
-- task 2305 held 382, and other branches and worktrees held 380 and 384. No
-- main file, open PR, branch or worktree held 385 to 388, so this file took
-- 388, with room for merges during the CI round. Check it again at merge
-- (premerge.sh).
-- Pinned by tests/unit/test_email_signature_settings_r8.py, which finds this
-- file by CONTENT, never by number (R1).
-- ============================================================================

BEGIN;

CREATE TABLE IF NOT EXISTS email_signature_settings (
    account_id       UUID NOT NULL REFERENCES email_accounts (id) ON DELETE CASCADE,
    organization_id  UUID NOT NULL
                     REFERENCES organization (id) ON DELETE CASCADE
                     DEFAULT current_setting('app.tenant_id', true)::uuid,
    -- D-EM-64. `custom` keeps the free HTML in email_assistant_settings.
    mode             TEXT NOT NULL DEFAULT 'custom',
    -- A layout of §23.3.2. A `template` row needs one.
    layout           TEXT NULL,
    -- The member's field map of §23.3.1, as a JSON object.
    fields           JSONB NOT NULL DEFAULT '{}'::jsonb,
    -- D-EM-71. Each existing mailbox starts at `full`.
    reply_mode       TEXT NOT NULL DEFAULT 'full',
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (account_id),
    CONSTRAINT email_signature_settings_mode CHECK (
        mode IN ('custom', 'template', 'company')),
    CONSTRAINT email_signature_settings_layout CHECK (
        layout IS NULL
        OR layout IN ('classic', 'stacked', 'minimal', 'photo', 'compact')),
    CONSTRAINT email_signature_settings_template_layout CHECK (
        mode <> 'template' OR layout IS NOT NULL),
    CONSTRAINT email_signature_settings_reply_mode CHECK (
        reply_mode IN ('full', 'short', 'none')),
    CONSTRAINT email_signature_settings_fields CHECK (
        jsonb_typeof(fields) = 'object' AND octet_length(fields::text) <= 16384)
);

DO $rls$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_class
         WHERE oid = to_regclass('email_signature_settings')
           AND relrowsecurity
           AND relforcerowsecurity
    ) THEN
        ALTER TABLE email_signature_settings ENABLE ROW LEVEL SECURITY;
        ALTER TABLE email_signature_settings FORCE  ROW LEVEL SECURITY;
        DROP POLICY IF EXISTS email_signature_settings_tenant_isolation
            ON email_signature_settings;
        CREATE POLICY email_signature_settings_tenant_isolation
            ON email_signature_settings
            USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
            WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);
    END IF;
END
$rls$;

COMMENT ON TABLE email_signature_settings IS
    'WS-17 SG-2: the signature mode of one mailbox (custom, template or company), '
    'its layout and fields, and reply_mode. No row means custom. '
    'gateway/routes/email/signature.py reads it. email_app_master_plan.md §23.3.3.';

COMMIT;
