-- Inbox-zero parity for rule actions: per-action delay and attachments.
--
-- inbox-zero lets a draft/reply/forward/send action (a) be delayed, and
-- (b) carry attachments. In Metorite, attachments are drawn from our
-- artifacts system, so `attachments` stores a JSON array of artifact
-- references the draft should attach. Idempotent (02+ auto-applied on deploy).

ALTER TABLE email_actions
    ADD COLUMN IF NOT EXISTS delay_minutes INTEGER;

ALTER TABLE email_actions
    ADD COLUMN IF NOT EXISTS attachments JSONB NOT NULL DEFAULT '[]';
