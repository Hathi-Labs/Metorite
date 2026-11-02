-- ============================================================================
-- 328_chat_message_member_typed.sql - the mark of a message that the member
-- typed (WS-63 PZ-33b round 4, D-PZ-37)
-- ============================================================================
-- Spec: project-docs/specs/personalization.md §15.3 and §15.11 (D-PZ-37)
-- · R5 · R6 · R8. R1: origin/main held 327 as its highest file at build time
-- (2026-11-02), so this file is 328. Check the number again at merge.
--
-- What: one nullable BOOLEAN column, `chat_message.member_typed`. TRUE is a
--   member row that the member typed in the message box and sent. FALSE is a
--   member row that the server saved from a run request with no mark: a
--   tapped pill, a choice, a card action, a feed starter, the Fix prompt, a
--   card answer and a Continue turn. NULL is a row that no run request saved,
--   and each row stored before this file. Only TRUE counts. So each old row
--   counts for nothing (the owner, 2026-11-02).
--
-- Who writes it: only `routes/agent.py` (`_write_prompt_row`), from the run
--   request of the member. The client save of the thread
--   (`POST /chat/sessions/{id}/messages`) names no such field, so it inserts
--   NULL and keeps the stored value on an update
--   (`routes/chat.py::_MESSAGE_UPSERT_SQL`). So a client save can neither set
--   the mark nor clear it.
--
-- Why a column, and not a key of an existing JSONB column: the client save
--   writes `custom_events` and `agent_state` of each row, and a non-empty
--   array replaces the stored one. A mark there would need a second merge
--   rule in the one upsert. `authority` is the clearance of an agent row, and
--   the redaction rule reads it. A mark there would mix two meanings in one
--   column that a security rule reads.
--
-- Expand only (R6): no default, no backfill, no rename. The old code names
--   each column by name, so it never reads the new column and never clears
--   it. Idempotent: ADD COLUMN IF NOT EXISTS. A column with no default
--   changes only the catalog, so the ALTER does not rewrite the table.
--
-- Tenancy (R5): `chat_message` already has the tenant `organization_id` and
--   FORCE RLS with its policy (`generated/03_constraints.sql` and
--   `generated/04_policies.sql`). The column changes neither. So this file
--   adds no table, no policy and no `gen_tenant_migration.EXEMPT` row.
--
-- Never wait for the lock: `ALTER TABLE` takes ACCESS EXCLUSIVE on
--   `chat_message`, and each chat read and write needs that table. So the
--   file sets `lock_timeout` itself, as 220 and 319 do. On a lock timeout the
--   runner tries the whole file again, and the file is idempotent.
--
-- Depends on: 02_chat_history.sql (the table).
-- Pinned by: tests/unit/test_chat_summary_r8.py (R8).
-- ============================================================================

SET lock_timeout = '5s';

ALTER TABLE chat_message ADD COLUMN IF NOT EXISTS member_typed BOOLEAN;

COMMENT ON COLUMN chat_message.member_typed IS
    'TRUE when the member typed this row in the message box (WS-63 D-PZ-37). Only routes/agent.py writes it, from the run request. FALSE and NULL count for nothing.';
