-- ============================================================================
-- 223_email_accounts_unique_per_tenant.sql — one mailbox may connect in two
-- organizations.
--
-- What: `email_accounts.organization_id` on the NUMBERED ladder, backfilled,
--       and the two tenant-blind unique rules on the table replaced with
--       per-tenant ones.
-- Why:  WS-17 EM-T2a (`project-docs/specs/email_app_master_plan.md` §10.4.5).
--
-- **The collision.** Migration 17 declares
--   UNIQUE (user_id, provider, email_address)
-- and migration 47 declares
--   idx_email_accounts_one_default ON (user_id) WHERE is_default
-- with no tenant in either. Today `app_user` holds each address once, so the
-- rule does not fire. It fires when a member moves to another organization
-- and connects the same mailbox again. Row level security hides the old row,
-- so the read-first in `_save_account` and `create_account` finds nothing,
-- and the INSERT then fails with a unique violation the member cannot see.
-- The default index fails the same way: the first mailbox in the new
-- organization asks for `is_default = true`, and the old default refuses it.
--
-- **One file, not two releases (R6).** Each replacement is WEAKER than the
-- rule it replaces, so every row that satisfies the old rule satisfies the new
-- one. Old code names neither the constraint nor the index (no `ON CONFLICT`
-- on this table), so old code works on the new schema. Migration 209 did the
-- same for `people`.
--
-- **Two starting points, one file.**
--   * Production: `generated/01..04` are applied. The column exists, it is
--     NOT NULL, and `email_accounts_org_fk` cascades. `ADD COLUMN IF NOT
--     EXISTS` then skips the whole column clause, the inline REFERENCES too,
--     so no second foreign key appears. The backfill finds no NULL row.
--   * A fresh ladder database: no tenancy phase ran (the ladder never replays
--     `generated/`, H-104). The column is created here, with the cascade that
--     `test_org_purge_tenant` requires.
--
-- Depends on: 17_email_accounts.sql (the constraint dropped here),
--             47_email_default_account.sql (the index dropped here),
--             130_org_access_control.sql (app_user, organization).
-- Idempotent: ADD COLUMN IF NOT EXISTS, a backfill whose WHERE empties,
--             DROP ... IF EXISTS, CREATE UNIQUE INDEX IF NOT EXISTS.
-- No CONCURRENTLY: the table is small, and a failed concurrent build leaves
-- an INVALID index behind.
-- Pinned by tests/unit/test_email_account_unique_per_tenant.py.
-- ============================================================================

BEGIN;

-- ── 1. The column ───────────────────────────────────────────────────────────
--
-- Same type and default as `generated/01_add_columns.sql`, plus the
-- ON DELETE CASCADE that the purge path needs. `REFERENCES` precedes
-- `DEFAULT`, because the tenancy ratchet matches the two with no comma between
-- them (see migrations 174 and 209).
ALTER TABLE email_accounts
    ADD COLUMN IF NOT EXISTS organization_id UUID
    REFERENCES organization (id) ON DELETE CASCADE
    DEFAULT current_setting('app.tenant_id', true)::uuid;


-- ── 2. Who owns each existing row ───────────────────────────────────────────

DO $backfill$
DECLARE
    by_member bigint;
    orphans   bigint;
    only_org  uuid;
    adopted   bigint := 0;
BEGIN
    -- (a) `app_user` is the authority on the organization of an address
    --     (D-MT-1 (a)). `user_id` holds the address of the member.
    UPDATE email_accounts a
       SET organization_id = u.organization_id
      FROM app_user u
     WHERE a.organization_id IS NULL
       AND a.user_id IS NOT NULL
       AND lower(btrim(a.user_id)) = lower(btrim(u.email))
       AND u.organization_id IS NOT NULL;
    GET DIAGNOSTICS by_member = ROW_COUNT;

    SELECT count(*) INTO orphans
      FROM email_accounts WHERE organization_id IS NULL;

    -- (b) No member matches. With one organization the answer is plain.
    --     With more than one, a guess could put a mailbox in the wrong
    --     customer, so the row stays NULL and the warning below says so.
    IF orphans > 0 AND (SELECT count(*) FROM organization) = 1 THEN
        SELECT id INTO only_org FROM organization;
        UPDATE email_accounts SET organization_id = only_org
         WHERE organization_id IS NULL;
        GET DIAGNOSTICS adopted = ROW_COUNT;
        orphans := 0;
    END IF;

    RAISE NOTICE
        '223: % mailbox row(s) tenanted from app_user, % adopted by the only '
        'organization, % left NULL',
        by_member, adopted, orphans;

    IF orphans > 0 THEN
        RAISE WARNING
            '223: % email_accounts row(s) match no member and keep '
            'organization_id NULL. The per-tenant unique index does not '
            'constrain them, and no tenant can read them under row level '
            'security. Set them by hand.', orphans;
    END IF;
END
$backfill$;


-- ── 3. One mailbox row per organization, member, provider and address ──────
--
-- Dropped and created in one transaction. Two unique rules mean the stricter
-- one decides, so the old rule left in place would refuse the second
-- organization exactly as before.

ALTER TABLE email_accounts
    DROP CONSTRAINT IF EXISTS email_accounts_user_id_provider_email_address_key;

CREATE UNIQUE INDEX IF NOT EXISTS uq_email_accounts_org_owner_mailbox
    ON email_accounts (organization_id, user_id, provider, email_address);

COMMENT ON INDEX uq_email_accounts_org_owner_mailbox IS
    'EM-T2a: a member connects a mailbox once WITHIN an organization. '
    'Replaced migration 17''s UNIQUE (user_id, provider, email_address), '
    'which had no tenant.';


-- ── 4. One default mailbox per member per organization ──────────────────────

DROP INDEX IF EXISTS idx_email_accounts_one_default;

CREATE UNIQUE INDEX IF NOT EXISTS uq_email_accounts_org_one_default
    ON email_accounts (organization_id, user_id)
    WHERE is_default;

COMMENT ON INDEX uq_email_accounts_org_one_default IS
    'EM-T2a: at most one default mailbox per member WITHIN an organization. '
    'Replaced migration 47''s idx_email_accounts_one_default, which had no '
    'tenant.';

COMMIT;
