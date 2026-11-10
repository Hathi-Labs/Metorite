-- ============================================================================
-- 264_email_mailbox_purges.sql — one row for each Mem0 purge that a disconnect owes.
--
-- What: one new table, `email_mailbox_purges`. `DELETE /email/accounts/{id}`
--       writes one row in the SAME transaction as its `DELETE` of the
--       `email_accounts` row. The route's own task runs the two Mem0 passes and
--       marks the row `done`. When the process stops first, the row stays
--       `open`, and the sweeper (`routes/email/memory_purge.py`, hosted by the
--       calendar auto-rollover loop) runs the passes later with a back-off.
-- Why:  WS-17 EM-G11f2 (PR-G8), spec
--       project-docs/specs/email_app_master_plan.md §12.3.13.9, f2 item 1.
--       Before it, a restart between the commit and the purge lost the purge,
--       and the Mem0 memory of a deleted mailbox stayed for ever.
-- Depends on: 130_org_access_control.sql (organization).
--
-- ⚠️ `account_id` has NO foreign key to `email_accounts`, on purpose. The row
-- must outlive the mailbox row. With a cascade, the DELETE that writes the row
-- would delete it again, and the child fence of f2 item 3 would count it.
--
-- Expand only (R6): one new table. No rename, no drop, and no change to an
-- existing table. Old code names no new object.
-- Idempotent per infra/postgres/README.md: CREATE TABLE IF NOT EXISTS, CREATE
-- INDEX IF NOT EXISTS and an RLS block guarded on relforcerowsecurity. A
-- second run changes nothing.
-- Tenant-scoped (R5): `organization_id` with ENABLE and FORCE row level
-- security and one policy with USING and WITH CHECK, in the shape of
-- 262_my_day_judgments.sql. `REFERENCES` comes before `DEFAULT`, because the
-- tenancy ratchet in tests/unit/test_tenancy_boundary.py matches the two with
-- no comma between. The foreign key is inline, never
-- `ADD CONSTRAINT <table>_org_fk`, because generated phase 3 adds that name.
-- The organization comes from the bound session (the column default), never
-- from request input.
-- The number is taken at build time (R1). On 2026-10-14 main held 263, and no
-- open pull request held a migration. Check it again at merge.
-- Pinned by tests/unit/test_email_mailbox_purge_durable.py, which finds this
-- file by CONTENT, never by number (R1).
-- ============================================================================

BEGIN;

CREATE TABLE IF NOT EXISTS email_mailbox_purges (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id  UUID NOT NULL
                     REFERENCES organization (id) ON DELETE CASCADE
                     DEFAULT current_setting('app.tenant_id', true)::uuid,
    owner_email      TEXT NOT NULL,
    account_id       UUID NOT NULL,
    state            TEXT NOT NULL DEFAULT 'open',
    passes           SMALLINT NOT NULL DEFAULT 0,
    tries            INTEGER NOT NULL DEFAULT 0,
    last_error       TEXT NULL,
    next_try_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    done_at          TIMESTAMPTZ NULL,
    CONSTRAINT email_mailbox_purges_one_per_mailbox UNIQUE (organization_id, account_id),
    CONSTRAINT email_mailbox_purges_state_known
        CHECK (state IN ('open', 'done', 'refused')),
    CONSTRAINT email_mailbox_purges_passes_range CHECK (passes BETWEEN 0 AND 2)
);

-- The sweeper reads the open rows that are due, one org at a time.
CREATE INDEX IF NOT EXISTS idx_email_mailbox_purges_open_due
    ON email_mailbox_purges (organization_id, next_try_at)
    WHERE state = 'open';

DO $rls$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_class
         WHERE oid = to_regclass('email_mailbox_purges')
           AND relrowsecurity
           AND relforcerowsecurity
    ) THEN
        ALTER TABLE email_mailbox_purges ENABLE ROW LEVEL SECURITY;
        ALTER TABLE email_mailbox_purges FORCE  ROW LEVEL SECURITY;
        DROP POLICY IF EXISTS email_mailbox_purges_tenant_isolation ON email_mailbox_purges;
        CREATE POLICY email_mailbox_purges_tenant_isolation ON email_mailbox_purges
            USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
            WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);
    END IF;
END
$rls$;

COMMENT ON TABLE email_mailbox_purges IS
    'WS-17 EM-G11f2: one row for each Mem0 purge that a mailbox disconnect owes. '
    'Written in the transaction of the DELETE. No foreign key to email_accounts, '
    'on purpose. routes/email/memory_purge.py runs and closes it. '
    'email_app_master_plan.md §12.3.13.9.';

COMMIT;
