-- ============================================================================
-- 390_email_auto_run_chosen_at.sql - the time of the member's Auto-run choice
-- (WS-17 EM-G11c2, Metorite task 2305, parent task 2267)
-- ============================================================================
-- Spec: project-docs/specs/email_app_master_plan.md §12.3.13.7 · R1 · R5 · R6.
-- R1: origin/main held 372 as its highest file on 2026-11-09. Open PRs and
-- branches hold 371, 373, 374, 375 and 378. So this file was 382, with
-- headroom. Check the number again at merge (scripts/premerge.sh).
-- At 01:45 UTC on 2026-11-09 main held 384. A branch held 385 and SG-2
-- (task 2178) held 388. So the file moved to 390.
--
-- Why: task 2267 shows a notice for a Gmail mailbox with no stored Auto-run
--   choice. It read "a settings row exists" as "the member chose". But each
--   save of the AI settings screen PUTs the whole GET body, with
--   `auto_run: false`, and the upsert writes a row. So a signature save
--   recorded a choice of OFF that the member never made, and the notice
--   never showed.
--
-- What: one nullable column on `email_assistant_settings`.
--   auto_run_chosen_at  TIMESTAMPTZ. When the member first chose Auto-run ON
--                       or OFF on purpose. NULL means no explicit choice.
--
-- Who writes it: only `PUT /email/assistant/settings`
--   (`gateway/routes/email/automation/assistant.py`), and only for a body
--   with the marker `auto_run_choice: true` and a value of `auto_run`. It
--   stamps once with COALESCE and never clears the stamp.
-- Who reads it: the GET and the PUT answer. They give `auto_run_chosen` as
--   `auto_run_chosen_at IS NOT NULL OR auto_run`. `rules.auto_run_on` does
--   not read it, so no background write depends on it.
--
-- Expand only (R6): one new nullable column with no default and no
--   backfill. A row from before this file reads "chosen" only when it
--   stores `auto_run = true`. Old code names each column of its INSERT, so
--   it never writes this one. The ALTER changes only the catalog.
--   Idempotent: ADD COLUMN IF NOT EXISTS. A second run changes nothing.
--
-- Tenancy (R5): `email_assistant_settings` is tenant-scoped, with FORCE RLS
--   and the generated policies. A new column changes neither. So this file
--   adds no table, no policy and no `gen_tenant_migration.EXEMPT` row.
--
-- Never wait for the lock: `ALTER TABLE` takes ACCESS EXCLUSIVE, and the
--   digest and the drafting jobs write this table. So the file sets
--   `lock_timeout`, as 350 does.
--   On a lock timeout the runner tries the whole file again.
--
-- Depends on: 20_email_assistant_settings.sql (the table).
-- Pinned by: tests/unit/test_email_background_writes.py (R8), which finds
--   this file by CONTENT, never by number (R1).
-- ============================================================================

SET lock_timeout = '5s';

ALTER TABLE email_assistant_settings
    ADD COLUMN IF NOT EXISTS auto_run_chosen_at TIMESTAMPTZ;

COMMENT ON COLUMN email_assistant_settings.auto_run_chosen_at IS
    'Task 2305: when the member first chose Auto-run on purpose. NULL means no explicit choice. Only PUT /email/assistant/settings with auto_run_choice writes it, once, and nothing clears it.';
