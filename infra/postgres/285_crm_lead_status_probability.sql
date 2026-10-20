-- ============================================================================
-- 285_crm_lead_status_probability.sql — a win chance on a lead status
-- ============================================================================
-- Spec: project-docs/specs/crm_platform.md §20 (CRM-U6) · R5 · R6.
-- Owner, 2026-10-20: "Set a conversion probability for each stage."
--
-- A deal stage already carries `probability` (144) and the Mirror overlay
-- `ui_probability` (257). A lead status had neither. This migration adds both,
-- with the same meaning. The lane header then shows the expected number of
-- leads that convert.
--
-- `probability` is NOT NULL with default 0, so old code that never names the
-- column meets the new schema unchanged (R6). `ui_probability` is nullable.
-- NULL means "use the source value". Both carry a CHECK of 0 to 100.
--
-- No new table, so `test_tenant_coverage.py` changes nothing.
--
-- Expand only. Idempotent. The one UPDATE sets a won status to 100.
--
-- Depends on: 144_crm.sql (the table), 257_crm_stage_overlay.sql.
-- ============================================================================

BEGIN;

ALTER TABLE crm_lead_statuses
    ADD COLUMN IF NOT EXISTS probability SMALLINT NOT NULL DEFAULT 0;
ALTER TABLE crm_lead_statuses
    ADD COLUMN IF NOT EXISTS ui_probability SMALLINT;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'crm_lead_statuses_probability_check'
          AND conrelid = 'crm_lead_statuses'::regclass
    ) THEN
        ALTER TABLE crm_lead_statuses
            ADD CONSTRAINT crm_lead_statuses_probability_check
            CHECK (probability BETWEEN 0 AND 100);
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'crm_lead_statuses_ui_probability_check'
          AND conrelid = 'crm_lead_statuses'::regclass
    ) THEN
        ALTER TABLE crm_lead_statuses
            ADD CONSTRAINT crm_lead_statuses_ui_probability_check
            CHECK (ui_probability IS NULL OR ui_probability BETWEEN 0 AND 100);
    END IF;
END $$;

-- A won status always forecasts 100 (D-CRM-10). The column is new, so a won
-- status holds the default 0 until this runs. The statement is idempotent.
UPDATE crm_lead_statuses SET probability = 100 WHERE type = 'won' AND probability <> 100;

COMMENT ON COLUMN crm_lead_statuses.probability IS
    'The chance, in percent, that a lead in this status converts. Source value.';
COMMENT ON COLUMN crm_lead_statuses.ui_probability IS
    'Mirror mode overlay of probability. NULL = use the source value.';

COMMIT;
