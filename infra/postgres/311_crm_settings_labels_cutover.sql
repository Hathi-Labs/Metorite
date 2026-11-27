-- ============================================================================
-- 311_crm_settings_labels_cutover.sql — the names of the core records in a
-- Native CRM, and the two stamps of a cutover.
--
-- What: three new columns on `crm_settings`. Each is nullable, with no default.
--         * `labels` JSONB: the names that an admin of a Native CRM gave to
--           the four core entities, by API slug. The shape is the one that
--           `GET /crm/mode` already serves in Mirror mode:
--             {"contacts": {"label": "Existing Sites",
--                           "singular_label": "Existing Site"}, ...}
--           NULL means the names of the CRM ("Contacts", "Companies" ...).
--           `PUT /crm/settings/labels` is the one writer (CRM-Z10g). The
--           start over (`POST /crm/reset`) sets it back to NULL.
--         * `cutover_at` TIMESTAMPTZ and `cutover_by` TEXT: the time and the
--           admin of the move from Mirror to Native mode. CRM-Z10b writes
--           them. Nothing writes them in this release. The start over sets
--           them back to NULL.
-- Why:  WS-53 CRM-Z10g and CRM-Z10b, D133.1 (project-docs/work_plan.md §3).
--       An org that leaves Zoho keeps its Zoho names, and an admin can rename
--       them. The cutover columns come now, so the switch needs no second
--       migration.
-- Depends on: 243_crm_settings.sql (crm_settings).
--
-- Expand only (R6): three nullable columns with no default, so no row
-- rewrite. No rename, no drop, no UPDATE. Old code names no new column, so it
-- runs on this schema as before.
-- Idempotent per infra/postgres/README.md: ADD COLUMN IF NOT EXISTS. A second
-- run changes nothing.
-- Tenant-scoped (R5): no new table. `crm_settings` already has FORCE row
-- level security with one policy (243).
-- Pinned by tests/unit/test_crm_labels_r8.py, which finds this file by
-- CONTENT, never by number (R1).
-- ============================================================================

BEGIN;

ALTER TABLE crm_settings ADD COLUMN IF NOT EXISTS labels JSONB NULL;
ALTER TABLE crm_settings ADD COLUMN IF NOT EXISTS cutover_at TIMESTAMPTZ NULL;
ALTER TABLE crm_settings ADD COLUMN IF NOT EXISTS cutover_by TEXT NULL;

COMMENT ON COLUMN crm_settings.labels IS
    'CRM-Z10g: the names of the core entities in a Native CRM, by API slug, '
    '{slug: {label, singular_label}}. NULL = the names of the CRM.';
COMMENT ON COLUMN crm_settings.cutover_at IS
    'CRM-Z10b: when the org moved from Mirror to Native mode. NULL = never.';
COMMENT ON COLUMN crm_settings.cutover_by IS
    'CRM-Z10b: the admin who moved the org from Mirror to Native mode.';

COMMIT;
