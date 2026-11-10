-- ============================================================================
-- 257_crm_stage_overlay.sql — a Metorite overlay on each stage, status and
-- lost reason, and the Zoho id of each mirrored stage.
--
-- What: expand-only columns on the three tables of the pipeline settings.
--         * `crm_deal_statuses`: `hidden`, `ui_position`, `ui_probability`,
--           `ui_color`, `ext_id` and `source_gone_at`.
--         * `crm_lead_statuses`: `hidden`, `ui_position`, `ui_color`,
--           `ext_id` and `source_gone_at`.
--         * `crm_lost_reasons`: `hidden`.
--       Each status table also gets one partial unique index on
--       `(organization_id, ext_id) WHERE ext_id IS NOT NULL`.
--         * `hidden` and the `ui_*` columns are the OVERLAY. Only the admin
--           door writes them (`core.MIRROR_OVERLAY_COLUMNS`). The sync never
--           writes them, and no write to Zoho reads them.
--         * `ext_id` is the id of the Zoho picklist value. The sync matches a
--           stage by it first, so a Zoho rename keeps the row and its overlay.
--         * `source_gone_at` is set when the Zoho picklist no longer lists
--           the value. The sync never deletes the row.
-- Why:  WS-53 CRM-C3, spec project-docs/specs/crm_platform.md §20.6. The
--       admin of a Mirror org can order, hide, colour and weight the stages
--       with no write to Zoho.
-- Depends on: 144_crm.sql (the three tables), 241_crm_tenancy.sql (the tenant
--             key and FORCE row level security).
--
-- Expand only (R6): each new column is nullable, or NOT NULL with a default.
-- No rename, no drop, no UPDATE. Old code reads `SELECT *` and never names a
-- new column.
-- Idempotent per infra/postgres/README.md: ADD COLUMN IF NOT EXISTS and
-- CREATE UNIQUE INDEX IF NOT EXISTS. A second run changes nothing.
-- Tenant-scoped (R5): no new table. The three tables keep `organization_id`
-- and FORCE row level security from 241.
-- Pinned by tests/unit/test_crm_stage_overlay_r8.py, which finds this file by
-- CONTENT, never by number (R1).
-- ============================================================================

BEGIN;

ALTER TABLE crm_deal_statuses
    ADD COLUMN IF NOT EXISTS hidden BOOLEAN NOT NULL DEFAULT false,
    ADD COLUMN IF NOT EXISTS ui_position INT NULL,
    ADD COLUMN IF NOT EXISTS ui_probability SMALLINT NULL
        CONSTRAINT crm_deal_statuses_ui_probability_pct
        CHECK (ui_probability BETWEEN 0 AND 100),
    ADD COLUMN IF NOT EXISTS ui_color TEXT NULL,
    ADD COLUMN IF NOT EXISTS ext_id TEXT NULL,
    ADD COLUMN IF NOT EXISTS source_gone_at TIMESTAMPTZ NULL;

ALTER TABLE crm_lead_statuses
    ADD COLUMN IF NOT EXISTS hidden BOOLEAN NOT NULL DEFAULT false,
    ADD COLUMN IF NOT EXISTS ui_position INT NULL,
    ADD COLUMN IF NOT EXISTS ui_color TEXT NULL,
    ADD COLUMN IF NOT EXISTS ext_id TEXT NULL,
    ADD COLUMN IF NOT EXISTS source_gone_at TIMESTAMPTZ NULL;

ALTER TABLE crm_lost_reasons
    ADD COLUMN IF NOT EXISTS hidden BOOLEAN NOT NULL DEFAULT false;

-- One Zoho picklist value is one row in each org. Two orgs may hold the same
-- id, because the key starts with the tenant.
CREATE UNIQUE INDEX IF NOT EXISTS uq_crm_deal_statuses_org_ext_id
    ON crm_deal_statuses (organization_id, ext_id) WHERE ext_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_crm_lead_statuses_org_ext_id
    ON crm_lead_statuses (organization_id, ext_id) WHERE ext_id IS NOT NULL;

COMMIT;
