-- ============================================================================
-- 227_email_mailbox_color_slot.sql — the colour slot of a mailbox.
--
-- What: one column on `email_accounts`, `color_slot SMALLINT`, with a CHECK of
--       1 to 12. A backfill gives each existing mailbox a slot, in the order
--       of `created_at` for each member in each organization.
-- Why:  WS-17 EM-T8b (`project-docs/specs/email_app_master_plan.md` §11.7.2),
--       decision D-EM-21 (§11.2), defect MB-8 (§11.1). A member can connect
--       several mailboxes, and each surface that can show two of them draws a
--       chip. Before this, every mailbox got the colour `#6366f1`, so two
--       mailboxes looked the same.
--
-- **The slot is a slot of the categorical ramp, never a colour.** The UI maps
-- it through `accentForSlot` in `src/lib/categorical.ts`. A hex value in the
-- row would break design rule 1. The old `avatar_color` column stays for old
-- code (R6), and no new code reads it.
--
-- **The backfill.** Each member gets slots 1, 2, 3 and on, oldest mailbox
-- first. A member with more than 12 mailboxes repeats from 1. Only a row with
-- a NULL slot changes, so a second run changes nothing, and a slot that the
-- member chose is never moved.
--
-- Expand only (R6): old code names no new column, so it runs on the new schema
-- unchanged. Old code that inserts a row after this migration leaves the slot
-- NULL, and the UI then falls back to a hash of the mailbox id. The column
-- stays nullable for that reason.
-- Idempotent: `ADD COLUMN IF NOT EXISTS`, a guarded CHECK, and a backfill of
-- NULL rows only.
-- Creates no table, so it adds no tenant exemption (R5). `email_accounts` is
-- already tenant-scoped, and the new column inherits its row level security.
-- Pinned by tests/unit/test_email_mailbox_identity.py, which finds this file
-- by CONTENT, never by number (R1).
-- ============================================================================

BEGIN;

ALTER TABLE email_accounts
    ADD COLUMN IF NOT EXISTS color_slot SMALLINT;

DO $check$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conname = 'email_accounts_color_slot_range'
           AND conrelid = 'email_accounts'::regclass
    ) THEN
        ALTER TABLE email_accounts
            ADD CONSTRAINT email_accounts_color_slot_range
            CHECK (color_slot IS NULL OR color_slot BETWEEN 1 AND 12);
    END IF;
END
$check$;

COMMENT ON COLUMN email_accounts.color_slot IS
    'EM-T8b: the slot of the categorical ramp (--cat-1 to --cat-12) of the '
    'mailbox chip. NULL means no slot yet, and the UI hashes the mailbox id.';

WITH ranked AS (
    SELECT id,
           row_number() OVER (
               PARTITION BY organization_id, lower(user_id)
               ORDER BY created_at, id
           ) AS n
      FROM email_accounts
)
UPDATE email_accounts a
   SET color_slot = ((r.n - 1) % 12) + 1
  FROM ranked r
 WHERE a.id = r.id
   AND a.color_slot IS NULL;

COMMIT;
