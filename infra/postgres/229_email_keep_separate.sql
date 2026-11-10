-- ============================================================================
-- 229_email_keep_separate.sql — "Keep separate" for a mailbox.
--
-- What: one column on `email_accounts`, `in_all_inboxes BOOLEAN NOT NULL
--       DEFAULT true`. False means that the member keeps the mailbox separate.
-- Why:  WS-17 EM-T8g-1 (`project-docs/specs/email_app_master_plan.md`
--       §11.7.7), decisions D-EM-28 and D-EM-30 (§11.2). A member can keep a
--       mailbox out of each read of more than one mailbox, for example a
--       mailbox under a confidentiality agreement. The list, the facets,
--       search and `/senders` then leave it out when no `account_id` names
--       it. A read that names it still gets its rows.
--
-- **The identity does not change.** A separate mailbox stays in the self set
-- of the member (D-EM-27, D-EM-30), so `automation/identity.py` reads no new
-- column.
--
-- Expand only (R6): the constant default fills each existing row with true,
-- and no code fills the column. Old code names no new column, so it runs on
-- the new schema unchanged. Old code that inserts a row gets the default, and
-- the default is the behaviour from before this migration.
-- Idempotent: `ADD COLUMN IF NOT EXISTS`. A second run changes nothing.
-- Adds no table, so it adds no tenant exemption (R5). `email_accounts` is
-- already tenant-scoped, and the new column inherits its row level security.
-- Pinned by tests/unit/test_email_keep_separate.py, which finds this file by
-- CONTENT, never by number (R1).
-- ============================================================================

BEGIN;

ALTER TABLE email_accounts
    ADD COLUMN IF NOT EXISTS in_all_inboxes BOOLEAN NOT NULL DEFAULT true;

COMMENT ON COLUMN email_accounts.in_all_inboxes IS
    'EM-T8g-1: false keeps the mailbox separate (D-EM-28). A read of more '
    'than one mailbox leaves it out. A read that names it gets its rows.';

COMMIT;
