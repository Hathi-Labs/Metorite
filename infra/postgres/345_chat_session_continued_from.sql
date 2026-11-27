-- ============================================================================
-- 345_chat_session_continued_from.sql - the link of a chat that continues a
-- held chat (WS-67 WT-1, task 1684)
-- ============================================================================
-- Spec: project-docs/specs/one_assistant_context.md §9.3 item 8 and §9.4 WT-1
-- · R5 · R6 · R8. R1: origin/main held 344 as its highest file on
-- 2026-11-05. So this file is 345. Check the number again at merge.
--
-- What: one nullable TEXT column, `chat_session.continued_from`. It holds the
--   id of the chat that this chat continues. NULL is a chat that continues no
--   chat, and each chat stored before this file.
--
-- Who writes it: only `POST /chat/sessions/{id}/continue`
--   (`routes/chat.py::_open_continued_chat`), on the new chat that the server
--   mints. No client body names the column.
--
-- Who reads it: three places only. They are the WT-1 route, the session list
--   and the chip of CO-5 (`chat_organization.md` §7.1). A context builder, the
--   summary job and memory assembly never read the old chat through it.
--   Fence: tests/unit/test_continued_from_readers.py.
--
-- The foreign key: `chat_session(id)` with ON DELETE SET NULL, so a delete of
--   the old chat clears the link and never deletes the new chat. The file
--   adds it NOT VALID (the pattern of 148). A NOT VALID key checks each new
--   write and reads no old row, so the ALTER does not scan the table. Each
--   old row is NULL, so a later release can VALIDATE it with no failure.
--
-- Expand only (R6): no default, no backfill, no rename. The old code names
--   each column by name, so it never reads the new column. Idempotent: ADD
--   COLUMN IF NOT EXISTS, and a guarded DO block for the key.
--
-- Tenancy (R5): `chat_session` already has the tenant `organization_id` and
--   FORCE RLS with its policy (`generated/03_constraints.sql` and
--   `generated/04_policies.sql`). The column changes neither. So this file
--   adds no table, no policy and no `gen_tenant_migration.EXEMPT` row. The
--   route reads the old chat under the tenant of the caller before it writes
--   the link.
--
-- Never wait for the lock: `ALTER TABLE` takes ACCESS EXCLUSIVE on
--   `chat_session`, and each chat read and write needs that table. So the
--   file sets `lock_timeout` itself, as 337 does. On a lock timeout the
--   runner tries the whole file again, and the file is idempotent.
--
-- Depends on: 02_chat_history.sql (the table).
-- Pinned by: tests/unit/test_continue_carry.py (R8).
-- ============================================================================

SET lock_timeout = '5s';

ALTER TABLE chat_session ADD COLUMN IF NOT EXISTS continued_from TEXT;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint c
          JOIN pg_class t ON t.oid = c.conrelid
         WHERE t.relname = 'chat_session'
           AND c.conname = 'chat_session_continued_from_fk'
    ) THEN
        ALTER TABLE chat_session
          ADD CONSTRAINT chat_session_continued_from_fk
          FOREIGN KEY (continued_from) REFERENCES chat_session (id)
          ON DELETE SET NULL
          NOT VALID;
    END IF;
END $$;

COMMENT ON COLUMN chat_session.continued_from IS
    'The id of the held chat that this chat continues (WS-67 WT-1). Only POST /chat/sessions/{id}/continue writes it. NULL: no such chat.';
