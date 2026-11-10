-- ============================================================================
-- 275_crm_activity_module_record.sql — an activity can belong to a module record.
--
-- What: `crm_activities` gains `module_record_id`, a nullable foreign key to
--       `crm_module_records`, and a partial index where it is not null. The
--       CHECK `crm_activities_target_required` widens, so an activity with
--       only a module record is legal. Nothing else changes.
-- Why:  WS-53 CRM-Q23a, spec project-docs/specs/crm_platform.md §18.10. A Zoho
--       task, note, call or meeting whose parent is a custom module found no
--       target, and the sync did not store it. DEWiN's import read 1,145
--       tasks and linked none.
-- Depends on: 144_crm.sql (`crm_activities` and its CHECK) and
--       251_crm_module_records.sql (`crm_module_records`).
--
-- Expand only (R6). One nullable column with no default, one index, and a
-- CHECK that accepts every row it accepted before. The old gateway runs
-- against this schema for a moment during the deploy. It never names the new
-- column, so it meets no refusal. No row changes. No rename, no drop of data.
--
-- The number was taken at build time (R1). On 2026-10-16 origin/main held
-- 273, and the branch crm-q17a1 held 274. Check it again at merge.
--
-- Idempotent. `ADD COLUMN IF NOT EXISTS` and `CREATE INDEX IF NOT EXISTS`.
-- The CHECK changes inside ONE `DO` block, and the block runs only while
-- `pg_get_constraintdef` lacks `module_record_id`. So a replay changes
-- nothing. The block drops the CHECK, adds it again as NOT VALID and then
-- validates it, all in the one transaction of this file. No other session
-- ever sees the table without a CHECK, because `apply_migrations.sh` has no
-- wrapper of its own and this file opens BEGIN itself.
--
-- Tenancy (R5). No new table, so no `gen_tenant_migration.EXEMPT` row and no
-- policy change. The column sits on a table that has FORCE RLS already.
--
-- Fences: tests/unit/test_crm_migration.py (the text of this file, found by
-- CONTENT and never by number) and tests/unit/test_crm_module_activities_r8.py
-- (R8: the replay, the CHECK on a real database, and the cascade).
-- ============================================================================

SET lock_timeout = '5s';

BEGIN;

ALTER TABLE crm_activities
    ADD COLUMN IF NOT EXISTS module_record_id UUID NULL
        REFERENCES crm_module_records (id) ON DELETE CASCADE;

-- The timeline of one module record (GET /crm/modules/{module}/records/{id}/
-- timeline). Partial, because most activities name a core record.
CREATE INDEX IF NOT EXISTS idx_crm_activities_module_record_id_created_at
    ON crm_activities (module_record_id, created_at)
    WHERE module_record_id IS NOT NULL;

DO $widen_crm_activities_target_required$
DECLARE
    current_def text;
BEGIN
    SELECT pg_get_constraintdef(c.oid) INTO current_def
      FROM pg_constraint c
     WHERE c.conrelid = to_regclass('public.crm_activities')
       AND c.conname = 'crm_activities_target_required';
    IF current_def IS NULL OR position('module_record_id' IN current_def) = 0 THEN
        ALTER TABLE crm_activities
            DROP CONSTRAINT IF EXISTS crm_activities_target_required;
        ALTER TABLE crm_activities
            ADD CONSTRAINT crm_activities_target_required CHECK (
                lead_id IS NOT NULL
                OR deal_id IS NOT NULL
                OR contact_id IS NOT NULL
                OR company_id IS NOT NULL
                OR module_record_id IS NOT NULL
            ) NOT VALID;
        ALTER TABLE crm_activities
            VALIDATE CONSTRAINT crm_activities_target_required;
        RAISE NOTICE 'crm_activities_target_required now accepts module_record_id';
    END IF;
END
$widen_crm_activities_target_required$;

COMMENT ON COLUMN crm_activities.module_record_id IS
    'WS-53 CRM-Q23a: the generic module record this activity belongs to. '
    'Only the mirror sync sets it. crm_platform.md §18.10.';

COMMIT;
