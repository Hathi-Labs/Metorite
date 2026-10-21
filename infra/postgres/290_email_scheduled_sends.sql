-- ============================================================================
-- 290_email_scheduled_sends.sql — a draft that the member asked to send later.
--
-- What: one new table, `email_scheduled_sends`. One row is one draft of one
--       mailbox, the time to send it, and the recipients that the member's
--       card showed. The send-later job (gateway/routes/email/automation/
--       send_later.py) sends a due row through the one draft-send path, with
--       those recipients as `expect`, so a draft whose recipients changed
--       after the card is not sent. Only send_later.py writes it.
-- Why:  owner, 2026-10-20: the chat must do each email task. "Send later"
--       had no backend (HANDOFF H-375). Spec email_app_master_plan.md §19.
-- Depends on: 130_org_access_control.sql (organization),
--             17_email_accounts.sql (email_accounts, email_messages).
--
-- Expand only (R6): one new table and two indexes. No rename, no drop, and
-- no change to an existing table. Old code names no new object.
-- Idempotent per infra/postgres/README.md: CREATE TABLE IF NOT EXISTS, CREATE
-- INDEX IF NOT EXISTS and an RLS block guarded on relforcerowsecurity.
-- Tenant-scoped (R5): `organization_id` with ENABLE and FORCE row level
-- security and one policy with USING and WITH CHECK, in the shape of
-- 283_crm_business_rules.sql. `REFERENCES` comes before `DEFAULT`, because
-- the tenancy ratchet in tests/unit/test_tenancy_boundary.py matches the two
-- with no comma between. The foreign keys are inline.
-- The number is taken at build time (R1). On 2026-10-21 origin/main held
-- 288, and open PR #1192 held 289, so this takes 290. Check it again at merge.
-- Pinned by tests/unit/test_email_send_later.py, which finds this file by
-- CONTENT, never by number (R1).
-- ============================================================================

BEGIN;

CREATE TABLE IF NOT EXISTS email_scheduled_sends (
    organization_id  UUID NOT NULL
                     REFERENCES organization (id) ON DELETE CASCADE
                     DEFAULT current_setting('app.tenant_id', true)::uuid,
    id               UUID NOT NULL DEFAULT gen_random_uuid(),
    account_id       UUID NOT NULL REFERENCES email_accounts (id) ON DELETE CASCADE,
    -- The local row of the draft. CASCADE, as every edge under a mailbox is
    -- (test_email_mailbox_purge_durable.py): a disconnect leaves no row. The
    -- send drops the draft row, and its schedule row goes with it: the mail
    -- is then in Sent. A failed send keeps the draft, so it keeps its row.
    draft_id         UUID NOT NULL REFERENCES email_messages (id) ON DELETE CASCADE,
    -- The subject when the member scheduled it, for the list after the send.
    subject          TEXT NULL,
    -- The member who asked, lower case: the owner of the mailbox.
    owner_email      TEXT NOT NULL,
    send_at          TIMESTAMPTZ NOT NULL,
    -- The To, Cc and Bcc that the card showed: {"to": [...], "cc": [...],
    -- "bcc": [...]}. The send refuses a draft whose recipients differ.
    expect           JSONB NOT NULL,
    -- queued -> sending -> sent | failed. queued -> cancelled.
    status           TEXT NOT NULL DEFAULT 'queued',
    error            TEXT NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    sent_at          TIMESTAMPTZ NULL,
    PRIMARY KEY (id),
    CONSTRAINT email_scheduled_sends_status CHECK (
        status IN ('queued', 'sending', 'sent', 'failed', 'cancelled')),
    CONSTRAINT email_scheduled_sends_error CHECK (
        error IS NULL OR char_length(error) <= 500)
);

-- The job reads the due rows of an org, oldest first.
CREATE INDEX IF NOT EXISTS email_scheduled_sends_due_idx
    ON email_scheduled_sends (organization_id, send_at)
    WHERE status = 'queued';

-- One open schedule for each draft: a second one would send it twice.
CREATE UNIQUE INDEX IF NOT EXISTS email_scheduled_sends_one_open_uidx
    ON email_scheduled_sends (draft_id)
    WHERE status IN ('queued', 'sending');

DO $rls$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_class
         WHERE oid = to_regclass('email_scheduled_sends')
           AND relrowsecurity
           AND relforcerowsecurity
    ) THEN
        ALTER TABLE email_scheduled_sends ENABLE ROW LEVEL SECURITY;
        ALTER TABLE email_scheduled_sends FORCE  ROW LEVEL SECURITY;
        DROP POLICY IF EXISTS email_scheduled_sends_tenant_isolation ON email_scheduled_sends;
        CREATE POLICY email_scheduled_sends_tenant_isolation ON email_scheduled_sends
            USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
            WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);
    END IF;
END
$rls$;

COMMENT ON TABLE email_scheduled_sends IS
    'Send later: a draft, the time to send it and the recipients its card '
    'showed. Only gateway/routes/email/automation/send_later.py writes it. '
    'email_app_master_plan.md §19.';

COMMIT;
