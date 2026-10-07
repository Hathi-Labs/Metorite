-- ============================================================================
-- 232_wa_history_sync.sql — the columns of the coexistence history import.
--
-- What: six nullable columns on three WhatsApp tables.
--   * `wa_accounts.history_sync_state` — the state of the history import.
--     NULL means the row is not coexistence, or it is older than this file.
--     The other values are 'pending', 'requested', 'failed', 'declined' and
--     'complete'.
--   * `wa_accounts.history_sync_error` — the text that the member sees when
--     the state is 'failed' or 'declined'.
--   * `wa_accounts.history_import_progress` — Meta's progress, 0 to 100, of
--     the highest phase seen.
--   * `wa_messages.delivery_status` — 'pending', 'sent', 'delivered', 'read',
--     'played' or 'failed'. It only moves forward.
--   * `wa_messages.from_history` — true for a row of the history import.
--   * `wa_contacts.in_address_book` — true while the contact is in the
--     address book of the WhatsApp Business app.
-- Why:  spec `project-docs/specs/whatsapp_message_manager.md` §12.3 F4 and
--       F5, §12.4.1 picks P2, P6, P10 and P11 · board WS-20 slice WA-C3.
--
-- **R6, expand only.** Every statement is ADD COLUMN IF NOT EXISTS, so a
-- replay is safe and old code meets the new columns with no change. No
-- column is NOT NULL, no backfill runs and no constraint is added. The
-- vocabulary of each TEXT column lives in the code, not in a CHECK, so a new
-- Meta value is a code change and never a blocking migration.
--
-- `from_history` has DEFAULT false. On Postgres 11 and later that default is
-- a catalog change, and the table is not rewritten.
--
-- **R5.** It creates no table, so `gen_tenant_migration.EXEMPT` gets no row.
-- The three tables already carry `organization_id` and FORCE RLS.
--
-- Depends on: 102_whatsapp.sql. Fences:
-- `tests/unit/test_whatsapp_history_under_rls.py` (R8, two replays on the
-- promoted catalog) and `tests/unit/test_whatsapp_persist.py`.
-- ============================================================================

SET lock_timeout = '5s';

BEGIN;

ALTER TABLE wa_accounts ADD COLUMN IF NOT EXISTS history_sync_state TEXT;
ALTER TABLE wa_accounts ADD COLUMN IF NOT EXISTS history_sync_error TEXT;
ALTER TABLE wa_accounts ADD COLUMN IF NOT EXISTS history_import_progress SMALLINT;

ALTER TABLE wa_messages ADD COLUMN IF NOT EXISTS delivery_status TEXT;
ALTER TABLE wa_messages ADD COLUMN IF NOT EXISTS from_history BOOLEAN DEFAULT false;

ALTER TABLE wa_contacts ADD COLUMN IF NOT EXISTS in_address_book BOOLEAN;

COMMENT ON COLUMN wa_accounts.history_import_phase IS
    'The highest Meta history phase seen, plus 1: 1 = day 0-1, 2 = day 1-90, '
    '3 = day 90-180. 0 = no phase seen. Written by persist_sync_result (WA-C3).';
COMMENT ON COLUMN wa_accounts.history_sync_state IS
    'NULL = not coexistence or an older row. Else pending | requested | failed '
    '| declined | complete (WA-C3).';
COMMENT ON COLUMN wa_messages.delivery_status IS
    'pending < sent < delivered < read < played, and failed. Moves forward '
    'only (WA-C3).';

COMMIT;
