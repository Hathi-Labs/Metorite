-- 220_chat_message_run_integrity.sql — only the run changes an agent reply,
-- and the fold seals it.
--
-- What: two nullable columns on `chat_message`:
--       `run_member_email` (the member who started the run that wrote an
--       agent row) and `run_final_at` (the time the run's fold wrote it).
-- Why:  spec `project-docs/specs/projects_ai_chat.md` §19 · board WS-27bm
--       slice S13. The upsert in `gateway/routes/chat.py`
--       (`_MESSAGE_UPSERT_SQL`) reads both columns in its `WHERE`.
-- Depends on: 02_chat_history.sql (the table), 139_room_authorship_and_agents.sql
--       (`author_kind`). Idempotent.
--
-- ── The rule the columns carry ──────────────────────────────────────────────
--
-- The row id comes from the client. Before S13, any room sender could put
-- new words in an agent reply that another member's run wrote. Now:
--
--   * `run_member_email` is set by the FIRST writer of an agent row, lower-
--     cased, and never changed after. A client write gives the caller, and
--     the fold gives the member who started the run.
--   * A client may update an agent row only when it is that member, and only
--     while `run_final_at` is NULL.
--   * The fold sets `run_final_at`. After that, only the fold may write.
--
-- ── Why no default and no backfill ──────────────────────────────────────────
--
-- R6 expand. NULL `run_member_email` means "no writer ever named the run".
-- That holds for every row written before this migration and for rows written
-- during the deploy, by old code on the new schema. The upsert lets only the
-- fold update such a row, so a legacy agent reply is not open to a client.
-- A backfill would have to guess the member from `chat_session.user_id`,
-- which in a room names the owner and not the sender. A guess is worse than
-- NULL here.
--
-- NULL `run_final_at` means "not sealed". A default of now() would seal every
-- live, unfinished reply at deploy time, and its own checkpoints would then
-- fail until the fold.
--
-- ── Tenancy ─────────────────────────────────────────────────────────────────
--
-- `chat_message` already carries `organization_id` and its RLS policy. This
-- file adds columns to it, so it adds no table, no policy and no
-- `gen_tenant_migration.EXEMPT` row.

ALTER TABLE chat_message
    ADD COLUMN IF NOT EXISTS run_member_email TEXT,
    ADD COLUMN IF NOT EXISTS run_final_at     TIMESTAMPTZ;

COMMENT ON COLUMN chat_message.run_member_email IS
    'Lower-cased email of the member who started the run that wrote this agent row. Set once by the first writer. NULL: only the fold may update the row (S13).';
COMMENT ON COLUMN chat_message.run_final_at IS
    'When the run''s fold wrote this agent row. After it is set, no client write changes the row (S13).';
