-- ============================================================================
-- 231_email_insights.sql — the fact table of email Insights, and its two
-- progress columns and its opt-in.
--
-- What: `email_insights`, one row for each fact that the Insights job takes
--       from a mail or from a file of a mail. Two columns on `email_messages`:
--       `insights_at` (the job read the message) and `insights_tries` (the
--       count of failures), with a partial index for the batch read. One
--       column on `email_assistant_settings`: `insights_enabled`, the opt-in
--       of the mailbox, false by default (D-EM-39).
-- Why:  WS-17 EM-T14a, `project-docs/specs/email_app_master_plan.md` §13.3
--       and §13.9.1. Decisions D-EM-39, D-EM-41, D-EM-42 and D-EM-44.
-- Depends on: 17_email_accounts.sql (email_accounts, email_messages,
--       email_attachments), 20_email_assistant_settings.sql,
--       130_org_access_control.sql (organization).
--
-- ── email_insights ──────────────────────────────────────────────────────────
--
-- The one writer is `write_facts` in
-- `gateway/routes/email/automation/insights_store.py`. It writes
-- `organization_id` from `current_tenant()`, and `counterpart_email` from the
-- message row, never from a model. The quote rule (D-EM-41) means a number
-- comes from the text: code parses `amount`, `currency` and `due_on` from
-- `quote`, and code sets `confidence`.
--
-- `fact_type` has no CHECK on purpose. The write path holds the closed list of
-- §13.4 and refuses any other type, so a new type needs no migration.
--
-- Each foreign key cascades. A disconnect deletes the mailbox, and the cascade
-- deletes its facts. "Remove older mail from Metorite" deletes messages, and
-- the cascade deletes their facts.
--
-- ⚠️ A fact holds text from the mail of ONE member (D-EM-4, D-EM-46). The
-- owner check is in the gateway, as for every `email_*` table. Row level
-- security binds the organization only.
--
-- ⚠️ The lock on `email_messages`. The file is ONE transaction. Its first
-- `ADD COLUMN` on `email_messages` takes an ACCESS EXCLUSIVE lock, and the
-- transaction holds that lock until COMMIT. So the partial index build, which
-- scans the whole table, runs while READS and writes of `email_messages` wait
-- (review round 1, F3). The `ADD COLUMN` itself rewrites no row. Every
-- existing row has `insights_at` NULL, so the index holds every row today. A
-- deploy applies this file before the services restart.
--
-- ⚠️ The index on `attachment_id` serves the cascade. Referential actions
-- bypass row level security, so without it each delete of an attachment
-- scans `email_insights` across every organization (review round 1, P2).
--
-- Expand only (R6): a new table with four indexes, three new columns with a
-- constant default or NULL, and one new index on `email_messages`. No rename,
-- no drop, no UPDATE. Old code names no
-- new column, so it runs on the new schema unchanged. A constant default
-- rewrites no row (Postgres 11 and later).
-- Idempotent per infra/postgres/README.md: a second run changes nothing.
-- Tenant-scoped (R5): `organization_id`, ENABLE + FORCE row level security and
-- a policy with USING and WITH CHECK, in the shape of 219_pm_import_runs.sql.
-- The generated phase files carry the same table (`generated/`).
-- Pinned by tests/unit/test_email_insights_store.py, which finds this file by
-- CONTENT, never by number (R1).
-- ============================================================================

BEGIN;

CREATE TABLE IF NOT EXISTS email_insights (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id   UUID NOT NULL REFERENCES organization (id) ON DELETE CASCADE,
    account_id        UUID NOT NULL REFERENCES email_accounts (id) ON DELETE CASCADE,
    message_id        UUID NOT NULL REFERENCES email_messages (id) ON DELETE CASCADE,
    attachment_id     UUID REFERENCES email_attachments (id) ON DELETE CASCADE,
    domain            TEXT NOT NULL,
    fact_type         TEXT NOT NULL,
    direction         TEXT,
    title             TEXT NOT NULL,
    counterpart       TEXT,
    counterpart_email TEXT,
    ref               TEXT,
    amount            NUMERIC(18, 2),
    currency          CHAR(3),
    due_on            DATE,
    quote             TEXT NOT NULL,
    confidence        REAL NOT NULL,
    extractor_version TEXT NOT NULL,
    dedupe_key        TEXT NOT NULL,
    state             TEXT NOT NULL DEFAULT 'open',
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT email_insights_domain_known CHECK (
        domain IN ('finance', 'projects', 'sales', 'company')
    ),
    CONSTRAINT email_insights_direction_known CHECK (
        direction IS NULL OR direction IN ('payable', 'receivable')
    ),
    CONSTRAINT email_insights_state_known CHECK (
        state IN ('open', 'done', 'dismissed')
    ),
    CONSTRAINT email_insights_confidence_range CHECK (
        confidence >= 0 AND confidence <= 1
    )
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_email_insights_account_dedupe
    ON email_insights (account_id, dedupe_key);

CREATE INDEX IF NOT EXISTS idx_email_insights_tabs
    ON email_insights (account_id, domain, state, due_on);

CREATE INDEX IF NOT EXISTS idx_email_insights_message
    ON email_insights (message_id);

CREATE INDEX IF NOT EXISTS idx_email_insights_attachment
    ON email_insights (attachment_id)
    WHERE attachment_id IS NOT NULL;

DO $rls$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_class
         WHERE oid = 'email_insights'::regclass
           AND relrowsecurity
           AND relforcerowsecurity
    ) THEN
        EXECUTE 'ALTER TABLE email_insights ENABLE ROW LEVEL SECURITY';
        EXECUTE 'ALTER TABLE email_insights FORCE  ROW LEVEL SECURITY';
        EXECUTE 'DROP POLICY IF EXISTS email_insights_tenant_isolation '
                'ON email_insights';
        EXECUTE 'CREATE POLICY email_insights_tenant_isolation '
                'ON email_insights '
                '    USING      (organization_id = '
                '        current_setting(''app.tenant_id'', true)::uuid) '
                '    WITH CHECK (organization_id = '
                '        current_setting(''app.tenant_id'', true)::uuid)';
    END IF;
END
$rls$;

COMMENT ON TABLE email_insights IS
    'EM-T14a: one fact from a mail or a file of a mail. The one writer is '
    'insights_store.write_facts. Private to the member of the mailbox '
    '(D-EM-4, D-EM-46). email_app_master_plan.md §13.3.';

-- ── The progress columns of email_messages ──────────────────────────────────

ALTER TABLE email_messages
    ADD COLUMN IF NOT EXISTS insights_at TIMESTAMPTZ;

ALTER TABLE email_messages
    ADD COLUMN IF NOT EXISTS insights_tries SMALLINT NOT NULL DEFAULT 0;

CREATE INDEX IF NOT EXISTS idx_email_messages_insights_pending
    ON email_messages (account_id, received_at DESC)
    WHERE insights_at IS NULL;

COMMENT ON COLUMN email_messages.insights_at IS
    'EM-T14a: the Insights job read this message, with facts or with none.';

COMMENT ON COLUMN email_messages.insights_tries IS
    'EM-T14a: the count of failed Insights reads of this message.';

-- ── The opt-in (D-EM-39) ────────────────────────────────────────────────────

ALTER TABLE email_assistant_settings
    ADD COLUMN IF NOT EXISTS insights_enabled BOOLEAN NOT NULL DEFAULT false;

COMMENT ON COLUMN email_assistant_settings.insights_enabled IS
    'EM-T14a: the member turned Insights on for this mailbox (D-EM-39). A '
    'missing row reads as false. Only an opted-in mailbox sends its text to a '
    'model (D-EM-44).';

COMMIT;
