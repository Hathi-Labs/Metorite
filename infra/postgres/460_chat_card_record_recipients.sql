-- ============================================================================
-- 460_chat_card_record_recipients.sql - the sent-mail record of a chat: the
-- people and the time of each mail that an approved card sent (task 3594).
--
-- What: two new nullable columns on `chat_card_record` (459). `recipients`
--       holds the addresses that the member approved on the card. `sent_at`
--       holds the time that the mail went. A send tool sets them once, with
--       `message_id`, AFTER the provider took the mail
--       (orchestrator/card_record.py `update_sent`). The run context of the
--       next turn reads the last 5 sent acts of the chat, so the chat does not
--       send the same mail again, and does not say that a sent mail failed.
-- Why:  chat_organization.md §16.3, the bug of run a8ad07f3 (2026-11-16). Turn
--       2 did not see the tool result of turn 1 and sent the mail again.
-- Depends on: 459_chat_card_record.sql (chat_card_record).
--
-- NO TEXT OF THE MAIL. No column holds a subject, a body or a file name.
--       `recipients` holds addresses only. The CHECK refuses an array of more
--       than 50 items, an empty array, a NULL item, an item with a capital
--       letter, an item with no `@`, and an item with white space.
--
-- Expand only (R6): two nullable columns, no default, no rename, no drop. The
-- old code never names them. Idempotent: ADD COLUMN IF NOT EXISTS skips the
-- column and its CHECK on a second run. The ALTER takes an ACCESS EXCLUSIVE
-- lock on `chat_card_record`, so the file sets `lock_timeout` first, as 459
-- does. The table is tenant-scoped already (459), and RLS covers the new
-- columns.
--
-- The number is taken at build time (R1): 460 was booked for this task. Check
-- it again at merge. Pinned by tests/unit/test_sent_record.py and
-- tests/unit/test_sent_record_r8.py, which find this file by CONTENT.
-- ============================================================================

BEGIN;

SET LOCAL lock_timeout = '5s';

ALTER TABLE chat_card_record
    ADD COLUMN IF NOT EXISTS recipients TEXT[]
        CONSTRAINT chat_card_record_recipients CHECK (
            recipients IS NULL
            OR (
                array_ndims(recipients) = 1
                AND cardinality(recipients) BETWEEN 1 AND 50
                AND array_position(recipients, NULL) IS NULL
                AND array_to_string(recipients, ' ')
                    = lower(array_to_string(recipients, ' '))
                AND array_to_string(recipients, ' ')
                    ~ '^[^[:space:]@]*@[^[:space:]]*( [^[:space:]@]*@[^[:space:]]*)*$'
                AND cardinality(recipients)
                    = cardinality(string_to_array(array_to_string(recipients, ' '), ' '))
            ));

ALTER TABLE chat_card_record
    ADD COLUMN IF NOT EXISTS sent_at TIMESTAMPTZ;

COMMENT ON COLUMN chat_card_record.recipients IS
    'Task 3594: the addresses that the member approved on a send card, set once '
    'with message_id after the mail went. Addresses only, never mail text.';
COMMENT ON COLUMN chat_card_record.sent_at IS
    'Task 3594: the time that the mail of an approved send card went.';

COMMIT;
