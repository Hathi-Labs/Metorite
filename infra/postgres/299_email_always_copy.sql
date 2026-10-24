-- ============================================================================
-- 299_email_always_copy.sql — the member's "always copy" list of a mailbox.
--
-- What: one column on `email_assistant_settings`, `always_copy`, a JSON list
--       of rules. Each rule is `{"match": <domain or address>, "cc": [<address>,
--       ...]}`. The empty list is the default, so each mailbox starts with no
--       rule.
-- Why:  WS-17, `project-docs/specs/email_app_master_plan.md` §21.15 (item 7b of
--       §21.12, owner 2026-10-24). "Always copy Kiran and Veena on mail to
--       jainuniversity.ac.in." A rule adds those colleagues to the member's
--       own reply card and new-mail card, as rows that the member can untick.
--       It never sends and never acts on its own. It is not a mail rule,
--       because a mail rule runs on incoming mail.
-- Depends on: 20_email_assistant_settings.sql.
--
-- The one writer is `PUT /email/assistant/always-copy`
-- (`gateway/routes/email/automation/always_copy.py`). It takes only a
-- colleague of the organization, or a person the member wrote to before, in
-- a `cc` list. The table is already tenant-scoped (generated policies), so
-- this file adds no table, no policy and no exemption (R5).
--
-- R6: expand only. The column is NOT NULL with a constant default, so the
-- ADD is a catalog change with no table rewrite, and old code never reads it.
-- ============================================================================
ALTER TABLE email_assistant_settings
    ADD COLUMN IF NOT EXISTS always_copy JSONB NOT NULL DEFAULT '[]'::jsonb;

COMMENT ON COLUMN email_assistant_settings.always_copy IS
    'The always-copy rules of the mailbox (§21.15): [{"match": domain or address, '
    '"cc": [address, ...]}]. Each cc is an org or a sent person when saved.';
