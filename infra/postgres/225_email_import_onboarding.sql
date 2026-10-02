-- ============================================================================
-- 225_email_import_onboarding.sql — the columns of guided mailbox onboarding.
--
-- What: eight columns on `email_accounts`. Each one is nullable, with no
--       default, no CHECK and no backfill.
-- Why:  WS-17 EM-T6 (`project-docs/specs/email_app_master_plan.md` §10.4.7),
--       owner decisions D-EM-10 to D-EM-16 (§10.2). EM-T6a writes three of
--       the columns: `import_since`, `import_phase` (for a range of 0 months)
--       and `onboarding_done_at`. EM-T6b to EM-T6e write the rest and add no
--       migration.
--
-- **The columns.**
--   * `import_since` — the import floor that the member chose at the first
--     connect (0 to 6 months). NULL means the mailbox connected before EM-T6,
--     and then the ceiling of 180 days alone binds it.
--   * `import_reached_at`, `import_phase`, `import_count`, `import_estimate` —
--     the progress of the first import (EM-T6b). The OAuth callback writes
--     `import_phase = 'done'` for a range of 0 months (EM-T6a).
--   * `stored_bytes`, `stored_bytes_at` — the storage meter (EM-T6c).
--   * `onboarding_done_at` — the member closed the guided setup (EM-T6a).
--
-- **NULL is a state, so there is no default (R6).** A default would make every
-- existing row claim a range, a phase or a meter reading that nobody wrote.
-- `import_phase` is TEXT with no CHECK, so a later phase name needs no
-- migration. The code owns the vocabulary.
--
-- Expand only (R6): old code names none of these columns, so it runs on the new
-- schema unchanged. `ADD COLUMN` with no default rewrites no row.
-- Idempotent: `ADD COLUMN IF NOT EXISTS`. A second run changes nothing.
-- Creates no table, so it adds no tenant exemption (R5). `email_accounts` is
-- already tenant-scoped, and new columns inherit its row level security.
-- Pinned by tests/unit/test_email_import_floor.py, which finds this file by
-- CONTENT, never by number (R1).
-- ============================================================================

ALTER TABLE email_accounts
    ADD COLUMN IF NOT EXISTS import_since       TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS import_reached_at  TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS import_phase       TEXT,
    ADD COLUMN IF NOT EXISTS import_count       INTEGER,
    ADD COLUMN IF NOT EXISTS import_estimate    INTEGER,
    ADD COLUMN IF NOT EXISTS stored_bytes       BIGINT,
    ADD COLUMN IF NOT EXISTS stored_bytes_at    TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS onboarding_done_at TIMESTAMPTZ;
