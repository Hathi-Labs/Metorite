-- ============================================================================
-- 298_whatsapp_act_confirm_code.sql — the reply code of an email act that a
-- WhatsApp run parks.
--
-- What: one nullable column, `whatsapp_pending_acts.confirm_code_hash`. An
--       email write from WhatsApp (an archive, a label, a move, a reply, a
--       send, a schedule, a forward or a new mail) parks as a pending act,
--       as a WAC-4 act does. Its summary ends with a short code, and the
--       member approves it with "YES <code>". The column holds the SHA-256
--       of that code in hex, never the code. NULL is every act that the
--       Confirm button answers (a Projects act and a WAC-P4 act), so each
--       older row and each older code path is exactly as before.
--       `whatsapp_pending_acts.wrong_codes` counts the wrong codes of one
--       act. The fifth closes the act. NULL reads as none.
-- Why:  H-391, `project-docs/specs/whatsapp_assistant_channel.md` §18 and
--       `email_app_master_plan.md` §21.13. A channel changes delivery, not
--       function (owner rule, 2026-10-10).
-- Depends on: 242_whatsapp_pending_acts.sql (whatsapp_pending_acts).
--
-- Expand only (R6): two nullable columns with no default, and two CHECKs
-- that every NULL passes, so no row is rewritten and old code names no new
-- object. The table keeps its FORCE row level security and its policy of
-- 242, so the column is tenant-scoped with its row (R5).
-- Idempotent per infra/postgres/README.md: a second run changes nothing.
-- Pinned by tests/unit/test_wac_email_confirm_r8.py, which finds this file
-- by CONTENT, never by number (R1).
-- ============================================================================

BEGIN;

ALTER TABLE whatsapp_pending_acts ADD COLUMN IF NOT EXISTS confirm_code_hash TEXT;
ALTER TABLE whatsapp_pending_acts ADD COLUMN IF NOT EXISTS wrong_codes SMALLINT;

DO $code$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conname = 'whatsapp_pending_acts_code_hash_shape'
           AND conrelid = 'whatsapp_pending_acts'::regclass
    ) THEN
        ALTER TABLE whatsapp_pending_acts
            ADD CONSTRAINT whatsapp_pending_acts_code_hash_shape
            CHECK (confirm_code_hash IS NULL
                   OR confirm_code_hash ~ '^[0-9a-f]{64}$');
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conname = 'whatsapp_pending_acts_wrong_codes_range'
           AND conrelid = 'whatsapp_pending_acts'::regclass
    ) THEN
        ALTER TABLE whatsapp_pending_acts
            ADD CONSTRAINT whatsapp_pending_acts_wrong_codes_range
            CHECK (wrong_codes IS NULL OR wrong_codes >= 0);
    END IF;
END
$code$;

COMMENT ON COLUMN whatsapp_pending_acts.confirm_code_hash IS
    'H-391: the SHA-256 (hex) of the reply code of an email act. NULL: the '
    'Confirm button answers the act. whatsapp_assistant_channel.md §18.';

COMMENT ON COLUMN whatsapp_pending_acts.wrong_codes IS
    'H-391: the wrong reply codes of this act. The fifth closes it. NULL: none.';

COMMIT;
