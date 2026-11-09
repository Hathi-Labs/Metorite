-- ============================================================================
-- 398_crm_pipeline_stage_values.sql — the win chance and the warning days of a
-- stage, for each deal pipeline (WS-CRM CRM-M3b, D166, Metorite task 2041).
--
-- What: two nullable columns on `crm_pipeline_stages`, the row that puts one
--       deal stage in one deal pipeline.
--       * `probability`: the win chance of the stage in this pipeline, 0 to
--         100. The CHECK `crm_pipeline_stages_probability_pct` holds the range.
--       * `max_dwell_days`: the days a deal may sit in the stage in this
--         pipeline before its card shows a warning. The CHECK
--         `crm_pipeline_stages_max_dwell_days_min` holds 1 or more.
--       NULL means "follow the stage row": each reader takes
--       COALESCE(ps.<column>, s.<column>), so a NULL membership reads what it
--       read before this file.
--       The file writes NO row. Each membership stays NULL (repair 2).
-- Why:  WS-CRM CRM-M3 build item 3 (project-docs/specs/crm_conversational.md
--       §8). A stage name is one shared row, and two pipelines may share it.
--       The win chance edited in one pipeline must not change the other.
-- Depends on: 144_crm.sql (crm_deal_statuses.probability),
--             169_crm_stage_discipline.sql (crm_deal_statuses.max_dwell_days),
--             247_crm_source_profiles_pipelines.sql (crm_pipeline_stages).
--
-- Expand only (R6). Two new nullable columns with no default, so the ALTER
-- does not rewrite the table. Old code never names them: an INSERT of old code
-- leaves both NULL, and each reader of the new code then reads the stage row,
-- as old code does. Old code writes the stage row only, and reads the stage
-- row only. The stage row keeps both columns. No rename, no drop.
-- Each CHECK is added NOT VALID, and a guarded block validates it, as 386 does.
-- The file sets lock_timeout first, and the runner tries the whole file again
-- on a lock timeout.
--
-- Why no backfill (CRM-M3b repair 2). The deploy applies this file BEFORE it
-- restarts the services, so old code runs for a time on the new schema. In
-- that time an admin can set a win chance through the old status PATCH, which
-- writes the stage row only. A copy of the stage row in a membership would
-- then hide that edit for ever, because each new reader takes the membership
-- first. A NULL membership reads the stage row, so with no copy each reader
-- sees what it saw before, and that edit too. New code writes a value into a
-- membership only after the restart: the route of one pipeline, the old route
-- for every pipeline (core.fan_out_stage_values), and a copy when a membership
-- is made. A later contract release may fill the NULLs and drop the COALESCE.
-- Idempotent per infra/postgres/README.md: ADD COLUMN IF NOT EXISTS, and
-- constraints guarded on pg_constraint. A second run changes nothing.
-- Tenant-bound (R5): no new table. `crm_pipeline_stages` keeps its tenant
-- `organization_id` and its FORCE row level security (247). The file writes no
-- row, so it binds no organization. The generated phases need no change,
-- because the file adds no table and no policy.
--
-- The number is taken at build time (R1). It was 394 until 2026-11-09 11:15
-- UTC, when two branches held 396 and 397. Main held 390, so this file took
-- 398, above each ref and each worktree. Check it again at merge.
-- Pinned by tests/unit/test_crm_pipelines_native.py, which finds this file by
-- CONTENT, never by number (R1).
-- ============================================================================

BEGIN;

SET LOCAL lock_timeout = '5s';

-- ── 1. The two columns ──────────────────────────────────────────────────────

ALTER TABLE crm_pipeline_stages
    ADD COLUMN IF NOT EXISTS probability SMALLINT NULL;

ALTER TABLE crm_pipeline_stages
    ADD COLUMN IF NOT EXISTS max_dwell_days SMALLINT NULL;

DO $add$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conname = 'crm_pipeline_stages_probability_pct'
           AND conrelid = 'crm_pipeline_stages'::regclass
    ) THEN
        ALTER TABLE crm_pipeline_stages
            ADD CONSTRAINT crm_pipeline_stages_probability_pct
            CHECK (probability IS NULL OR probability BETWEEN 0 AND 100) NOT VALID;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conname = 'crm_pipeline_stages_max_dwell_days_min'
           AND conrelid = 'crm_pipeline_stages'::regclass
    ) THEN
        ALTER TABLE crm_pipeline_stages
            ADD CONSTRAINT crm_pipeline_stages_max_dwell_days_min
            CHECK (max_dwell_days IS NULL OR max_dwell_days >= 1) NOT VALID;
    END IF;
END
$add$;

-- ── 2. Validate the two CHECKs ──────────────────────────────────────────────

DO $validate$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conname = 'crm_pipeline_stages_probability_pct'
           AND conrelid = 'crm_pipeline_stages'::regclass
           AND NOT convalidated
    ) THEN
        ALTER TABLE crm_pipeline_stages
            VALIDATE CONSTRAINT crm_pipeline_stages_probability_pct;
    END IF;
    IF EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conname = 'crm_pipeline_stages_max_dwell_days_min'
           AND conrelid = 'crm_pipeline_stages'::regclass
           AND NOT convalidated
    ) THEN
        ALTER TABLE crm_pipeline_stages
            VALIDATE CONSTRAINT crm_pipeline_stages_max_dwell_days_min;
    END IF;
END
$validate$;

COMMENT ON COLUMN crm_pipeline_stages.probability IS
    'WS-CRM CRM-M3b (D166): the win chance of the stage in this deal pipeline, '
    '0 to 100. NULL reads the stage row (crm_deal_statuses.probability). '
    'crm_conversational.md §8 CRM-M3.';
COMMENT ON COLUMN crm_pipeline_stages.max_dwell_days IS
    'WS-CRM CRM-M3b (D166): the warning days of the stage in this deal '
    'pipeline. NULL reads the stage row (crm_deal_statuses.max_dwell_days).';

COMMIT;
