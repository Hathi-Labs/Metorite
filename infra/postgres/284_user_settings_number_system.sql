-- ============================================================================
-- 284_user_settings_number_system.sql — a member's number system
-- ============================================================================
-- Spec: project-docs/specs/whatsapp_assistant_channel.md §13.10 · R5 · R6.
-- Owner, 2026-10-19: "Use the user's preferences, and keep it in the
-- organizational memory."
--
-- One new column on `user_settings`, `number_system`. It is the member's
-- override of the org's choice, which `org_settings` holds under the key
-- `number_system` (no schema change: that table is a key/value store).
-- `acb_common.number_system.resolve` is the one rule: the member's value
-- wins, then the org's, then 'indian' for every org.
--
-- NULL means "use the organization's setting". So the column is nullable
-- with NO default (R6). The CHECK names the two systems, and NULL passes it.
--
-- No new table, so `test_tenant_coverage.py` changes nothing. `user_settings`
-- is tenant-scoped since the generated phases, and keyed
-- `(organization_id, user_id)` since 239.
--
-- Expand only. Old code never names the column, so it meets this schema as
-- it met the old one.
--
-- Depends on: 51_gtd_settings.sql (the table), 239_user_settings_per_org.sql.
-- ============================================================================

BEGIN;

ALTER TABLE user_settings ADD COLUMN IF NOT EXISTS number_system TEXT;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'user_settings_number_system_check'
          AND conrelid = 'user_settings'::regclass
    ) THEN
        ALTER TABLE user_settings
            ADD CONSTRAINT user_settings_number_system_check
            CHECK (number_system IS NULL OR number_system IN ('indian', 'international'));
    END IF;
END $$;

COMMENT ON COLUMN user_settings.number_system IS
    'The member''s number system: indian (lakh, crore) or international '
    '(million, billion). NULL = use the organization''s setting.';

COMMIT;
