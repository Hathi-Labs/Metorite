-- ============================================================================
-- 448_chat_pending_ask_stored_call.sql - the stored call of a card (WS-51
-- RESUME-1a, chat_run_continuity.md §4.12.4 item 1, Metorite task 3260)
-- ============================================================================
-- Spec: project-docs/specs/chat_run_continuity.md §4.12.4 item 1 and the
-- RESUME-1 row of §4.12.7 · R1 · R5 · R6 · R8.
-- R1: the number 448 was booked for this task on 2026-11-15. origin/main held
-- 444 as its highest file. Check the number again at merge.
--
-- What: one nullable column on `chat_pending_ask` (237).
--   stored_call  JSONB NULL. The call that a plain confirmation card guards:
--                the tool, its module, the agent, the keyword arguments as
--                JSON gives them back, the MAF call id, the card text, the
--                act keys and their digest. Version 1 of the shape is
--                `orchestrator.pending_ask` and `acb_skills.stored_call`.
--                Only `pending_ask.insert_ask` writes it, at the card. A
--                move to `answered` or `closed`, and `close_thread_asks`,
--                set it to NULL. A move to `parked` keeps it.
--
-- Why: a late Approve of a parked card can then run the call that the member
--   saw, once, with no model and no second card (RESUME-2). This file only
--   stores it. No route reads it, and `pending_ask._COLUMNS` leaves it out,
--   so no browser read returns it.
--
-- Dark: `CHAT_RESUME_ORGS` (empty by default). With the flag off nothing
--   writes the column, so each row holds NULL and is as before.
--
-- Expand only (R6): one new column, no default, no backfill and no CHECK. No
--   name of the table changes. The old code names each column, so it never reads or
--   writes the new one. A NULL reads as "no stored call". Idempotent: ADD
--   COLUMN IF NOT EXISTS, and COMMENT ON is a plain replace.
--
-- Tenancy (R5): `chat_pending_ask` is tenant-scoped, with FORCE RLS (237 and
--   generated/01..04). The column changes neither. So this file adds no
--   table, no policy and no `gen_tenant_migration.EXEMPT` row.
--
-- Never wait for the lock: `ALTER TABLE` takes ACCESS EXCLUSIVE on
--   `chat_pending_ask`, and each card of a chat writes that table. So the
--   file sets `lock_timeout` itself, as 421 does. On a lock timeout the
--   runner tries the whole file again, and the file is idempotent.
--
-- Depends on: 237_chat_pending_ask.sql (the table).
-- Pinned by: tests/unit/test_resume_stored_call_r8.py (R8), which finds this
--   file by CONTENT, never by number (R1).
-- ============================================================================

BEGIN;

SET LOCAL lock_timeout = '5s';

ALTER TABLE chat_pending_ask
    ADD COLUMN IF NOT EXISTS stored_call JSONB;

COMMENT ON COLUMN chat_pending_ask.stored_call IS
    'RESUME-1a: the call that a plain confirmation card guards, written at the '
    'card. NULL when the card stores none, and after an answer or a close. No '
    'browser read returns it. chat_run_continuity.md 4.12.4 item 1.';

COMMIT;
