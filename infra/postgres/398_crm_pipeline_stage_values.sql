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
--       NULL means "not set here": each reader takes
--       COALESCE(ps.<column>, s.<column>), so the stage row fills a NULL.
--       The file copies the value of each stage row into each of its
--       memberships, so every pipeline reads what it read before.
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
-- row only. The stage row keeps both columns. No rename, no drop. A later
-- contract release may drop the COALESCE, after each writer sets both values.
-- Each CHECK is added NOT VALID, and a guarded block validates it, as 386 does.
-- The backfill writes only a value inside each range, so each CHECK holds. A
-- stage row with a value outside the range leaves NULL, and the COALESCE reads
-- that stage row, as before. The file sets lock_timeout first, and the runner
-- tries the whole file again on a lock timeout.
-- Idempotent per infra/postgres/README.md: ADD COLUMN IF NOT EXISTS,
-- constraints guarded on pg_constraint, and a backfill that writes only a
-- membership whose two columns are both NULL. A second run changes no value
-- that a reader sees.
-- Tenant-bound (R5): no new table. `crm_pipeline_stages` keeps its tenant
-- `organization_id` and its FORCE row level security (247). The backfill binds
-- each organization in turn, as 247 does, because FORCE binds the table owner
-- too unless it is a superuser or BYPASSRLS. The generated phases need no
-- change, because the file adds no table and no policy.
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

-- ── 2. The backfill: each membership takes the values of its stage row ──────
--
-- One organization at a time, with that organization bound when row level
-- security binds the runner. The binding is LOCAL to this transaction. A role
-- that bypasses row level security binds nothing. A membership that holds a
-- value already keeps it, so a second run changes nothing that a reader sees.

DO $backfill$
DECLARE
    org_id uuid;
    bypass boolean;
BEGIN
    SELECT rolsuper OR rolbypassrls INTO bypass
      FROM pg_roles WHERE rolname = current_user;

    FOR org_id IN SELECT id FROM organization ORDER BY id LOOP
        IF NOT bypass THEN
            PERFORM set_config('app.tenant_id', org_id::text, true);
        END IF;

        UPDATE crm_pipeline_stages ps
           SET probability = CASE WHEN s.probability BETWEEN 0 AND 100
                                  THEN s.probability END,
               max_dwell_days = CASE WHEN s.max_dwell_days >= 1
                                     THEN s.max_dwell_days END
          FROM crm_deal_statuses s
         WHERE s.id = ps.status_id
           AND s.organization_id = org_id
           AND ps.organization_id = org_id
           AND ps.probability IS NULL
           AND ps.max_dwell_days IS NULL;
    END LOOP;
END
$backfill$;

-- ── 3. Validate the two CHECKs ──────────────────────────────────────────────

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
