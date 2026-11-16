-- ============================================================================
-- 452_chat_session_member_topic.sql - the topic of a chat, for each member
-- (WS-CO CO-2b, task 3588, parent task 3501).
--
-- What: two nullable columns on `chat_session_member`, the per-member row of
--       a chat that migration 450 made. `topic_id` is the topic of the chat
--       for that member. `set_by` says who put it there: 'auto' (the
--       automatic pass of CO-1) or 'member' (the member's own move).
--       gateway/chat_session_member.py is the one module that writes them.
-- Why:  chat_organization.md §6 and §10 (D163). A member's move survives the
--       next automatic pass, and a private topic never reaches a colleague.
-- Depends on: 450_chat_session_member.sql (the table), 451_chat_topic.sql
--             (`chat_topic` and its UNIQUE (organization_id, id)).
--
-- THE LIFE OF A ROW. A pin (CO-11) or a move inserts the row, or changes it.
-- A move of the member sets `topic_id` and `set_by = 'member'`. A move to
-- "No topic" sets `topic_id` to NULL and keeps `set_by = 'member'`, so the
-- pass does not put a topic back. The pass sets `topic_id` with
-- `set_by = 'auto'` ONLY where no row exists, or where `set_by` is NULL or
-- 'auto'. It never writes over a row with `set_by = 'member'`. An unpin keeps
-- the row and its topic. A merge of company topics moves each row of the
-- source to the target, in its own transaction, and keeps `set_by`. A Delete
-- of the chat deletes the row (the cascade of 450). The member purge deletes
-- each row of the member in the bound org.
--
-- THE KEY. `(organization_id, topic_id)` references `chat_topic
-- (organization_id, id)`, so a row never names a topic of another org. A
-- foreign key check skips RLS, and the composite key is the guard of that.
-- ON DELETE SET NULL (topic_id) clears the topic and keeps the org: a plain
-- SET NULL on a composite key also clears `organization_id`, which is NOT
-- NULL, so a delete of a topic would fail. Postgres 15 and later take the
-- column list. CI, dev_db.sh and dev_up.sh run pgvector pg16, and the notes
-- name production as Postgres 17. A topic goes only by a merge (which moves
-- its rows first), by the member purge (a member topic, whose only rows are
-- the leaver's own) or with its org.
--
-- THE CHECKS. `set_by` is NULL, 'auto' or 'member'. A row with a topic names
-- who set it. A pin row with no topic has `set_by` NULL.
--
-- WHO READS WHAT. RLS covers the org only. The member rule is in the SQL of
-- the module: each statement filters on the member. A move selects the chat
-- under the visibility rule of gateway/rooms.py, and locks the topic first
-- through gateway/chat_topic.py, under the member rule of D163.2.
--
-- Expand only (R6): two nullable columns, two CHECKs, one foreign key and two
-- indexes. No rename, no drop, no data, and the old code never names the new
-- columns. Idempotent: ADD COLUMN IF NOT EXISTS, CREATE INDEX IF NOT EXISTS,
-- and each constraint in a DO block guarded on pg_constraint. The foreign
-- key takes a SHARE ROW EXCLUSIVE lock on both tables until COMMIT, and the
-- chat read takes the first one, so the file sets `lock_timeout` first, as
-- 450 does. The tenancy is the table's own (450). This file adds no table,
-- so the generated phases do not change, as for 345.
--
-- The number is taken at build time (R1): 452 was booked for this task, and
-- the branch held 451 as its highest file on 2026-11-17. Check it again at
-- merge. Pinned by tests/unit/test_chat_topic_move.py and
-- tests/unit/test_chat_topic_move_r8.py, which find this file by CONTENT,
-- never by number (R1).
-- ============================================================================

BEGIN;

SET LOCAL lock_timeout = '5s';

ALTER TABLE chat_session_member
    ADD COLUMN IF NOT EXISTS topic_id UUID,
    ADD COLUMN IF NOT EXISTS set_by   TEXT;

DO $co2b$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = 'chat_session_member'::regclass
           AND conname = 'chat_session_member_set_by'
    ) THEN
        ALTER TABLE chat_session_member
            ADD CONSTRAINT chat_session_member_set_by
            CHECK (set_by IN ('auto', 'member'));
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = 'chat_session_member'::regclass
           AND conname = 'chat_session_member_topic_set_by'
    ) THEN
        ALTER TABLE chat_session_member
            ADD CONSTRAINT chat_session_member_topic_set_by
            CHECK (topic_id IS NULL OR set_by IS NOT NULL);
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = 'chat_session_member'::regclass
           AND conname = 'chat_session_member_topic_fk'
    ) THEN
        ALTER TABLE chat_session_member
            ADD CONSTRAINT chat_session_member_topic_fk
            FOREIGN KEY (organization_id, topic_id)
            REFERENCES chat_topic (organization_id, id)
            ON DELETE SET NULL (topic_id);
    END IF;
END
$co2b$;

-- The read of the chat list: the topics of one member.
CREATE INDEX IF NOT EXISTS chat_session_member_topic_idx
    ON chat_session_member (organization_id, member_email, topic_id);

-- The rows of one topic: the move of a merge, and the SET NULL of a delete.
CREATE INDEX IF NOT EXISTS chat_session_member_topic_ref_idx
    ON chat_session_member (organization_id, topic_id)
    WHERE topic_id IS NOT NULL;

COMMENT ON COLUMN chat_session_member.topic_id IS
    'The topic of the chat for this member (WS-CO CO-2b). NULL: no topic.';
COMMENT ON COLUMN chat_session_member.set_by IS
    'Who set topic_id: auto (the pass of CO-1) or member (the member''s move). '
    'The pass never writes over a member row.';

COMMIT;
