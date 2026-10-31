-- ============================================================================
-- 318_chat_session_member_summary.sql - a short summary of each private chat
-- (WS-63 PZ-33a, D142)
-- ============================================================================
-- Spec: project-docs/specs/personalization.md §15.3 (D-PZ-25) and §15.9
-- · R5 · R6 · R8.
--
-- What: one nullable JSONB column, `chat_session.member_summary`. It holds
--   the summary of one private chat with Mira: at most 3 sentences, and at
--   most 3 follow-ups. A summary that the member deleted reads
--   `{"v": 1, "off": true}`, and no job writes that chat again. NULL is a
--   chat with no summary.
--
-- Why a column, and not a table: a summary has the life of its chat. A
--   delete of the chat deletes the summary. The member purge deletes the
--   member's private chats (`routes/admin/members.py`, the row
--   `private_chat_sessions`), so the purge needs no new row.
--
-- One module: `apps/services/gateway/gateway/assistant_summaries.py` is the
--   only code that names the column. Each other read of `chat_session`
--   names its columns, so no list, room, search or staff route returns it.
--   Fence: tests/unit/test_chat_summary_private.py.
--
-- Expand only (R6): no default, no backfill, no rename. The old code names
--   no new object. It reads and writes each column by name, so it never
--   reads the new column and never clears it. Idempotent: ADD COLUMN IF NOT
--   EXISTS. A column with no default changes only the catalog, so the ALTER
--   does not rewrite the table.
--
-- Tenancy (R5): `chat_session` already has the tenant `organization_id`
--   and FORCE RLS with its policy (`generated/03_constraints.sql` and
--   `generated/04_policies.sql`). The column changes neither. So this file
--   adds no table, no policy and no `gen_tenant_migration.EXEMPT` row.
--
-- Never wait for the lock: `ALTER TABLE` takes ACCESS EXCLUSIVE on
--   `chat_session`, and each chat read and write needs that table. So the
--   file sets `lock_timeout` itself, as 220 does. A hand replay through psql
--   then fails fast too. On a lock timeout the runner tries the whole file
--   again, and the file is idempotent.
--
-- Depends on: 02_chat_history.sql (the table).
-- Pinned by: tests/unit/test_chat_summary_r8.py (R8).
-- ============================================================================

SET lock_timeout = '5s';

ALTER TABLE chat_session ADD COLUMN IF NOT EXISTS member_summary JSONB;

COMMENT ON COLUMN chat_session.member_summary IS
    'The summary of one private chat with Mira, for its member only (WS-63 PZ-33). Only gateway/assistant_summaries.py reads or writes it. {"v": 1, "off": true} is a summary that the member deleted.';
