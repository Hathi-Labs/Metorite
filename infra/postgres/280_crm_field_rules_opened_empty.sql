-- ============================================================================
-- 280_crm_field_rules_opened_empty.sql — a field rule that an empty column
-- opened.
--
-- What: `crm_field_rules` gains `opened_empty`, a nullable BOOLEAN with no
--       default. An approve sets it on the row of a rule when the target is a
--       fixed column that only its fill of 0 opened (the column is in
--       `fill_rank.empty_columns`). NULL and false mean the same thing: the
--       layouts opened the column, or it is a free column.
-- Why:  WS-53 CRM-Q18-3, spec project-docs/specs/crm_platform.md §18.9.6,
--       "CRM-Q18-3 · A column that is empty opens". Once the rule fills its
--       column, the fill is no longer 0. So the code cannot find out later
--       that a 0 fill opened the rule, and the mark must be stored. The sync's
--       `mark_stale` reads it, and then never makes the rule stale only
--       because its standard field is on a layout.
-- Depends on: 253_crm_field_rules.sql (`crm_field_rules`).
--
-- Expand only (R6). One nullable column with no default. No row changes, no
-- rename, no drop. The old gateway runs against this schema for a moment
-- during the deploy. It never names the new column, so it meets no refusal.
--
-- The number was taken at build time (R1). On 2026-10-17 origin/main held
-- 278, and the branch of CRM-Q27 held 279. Check it again at merge.
--
-- Idempotent. `ADD COLUMN IF NOT EXISTS`, and COMMENT ON COLUMN replaces its
-- text. A replay changes nothing.
--
-- Tenancy (R5). No new table, so no `gen_tenant_migration.EXEMPT` row and no
-- policy change. The column sits on a table that has FORCE RLS already.
--
-- Fence: tests/unit/test_crm_field_rules_r8.py (R8), which finds this file
-- by CONTENT, never by number.
-- ============================================================================

SET lock_timeout = '5s';

BEGIN;

ALTER TABLE crm_field_rules
    ADD COLUMN IF NOT EXISTS opened_empty BOOLEAN NULL;

COMMENT ON COLUMN crm_field_rules.opened_empty IS
    'WS-53 CRM-Q18-3: true when an empty column (fill 0) opened this rule. '
    'NULL means false. crm_platform.md §18.9.6.';

COMMIT;
