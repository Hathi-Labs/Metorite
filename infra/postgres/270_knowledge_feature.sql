-- ============================================================================
-- 270_knowledge_feature.sql — the `knowledge` feature of the Knowledge app.
--
-- What: one `feature_catalog` row, `knowledge`, in the `apps` category at
--       sort order 58, after People (57). The matching FEATURES entry is in
--       packages/acb_auth/acb_auth/permissions.py. A catalog row with no
--       FEATURES entry is invisible to /auth/me, even for an owner, and
--       tests/unit/test_org_access_control.py pins the two together.
-- Why:  WS-61 KB-0, spec project-docs/specs/knowledge_base.md §5.2 and §17.
--       The Knowledge app needs its own slug before its pane and its route
--       gate can exist.
-- Who holds it: `is_default` is true, as §5.2 says. Migration 207 gives
--       `feature:*` to `member` and `manager`, and `owner` holds `*`, so the
--       slug reaches every role except `guest`. This file grants nothing. The
--       capability `knowledge:manage` lives in CAPABILITIES only. Its seed on
--       `admin` is KB-1, through provision_org_roles.
-- Depends on: 130_org_access_control.sql (feature_catalog).
--
-- Expand only (R6): one new row. No new table, no new column, no rename and
-- no drop. Old code reads the row as one more catalog entry.
-- Idempotent per infra/postgres/README.md: INSERT ... ON CONFLICT (slug) DO
-- UPDATE. A second run changes nothing. The update does not touch
-- `is_default`, the same rule as 130 and 144: an admin may have changed the
-- default set, and a redeploy must not undo that.
-- Tenant scope (R5): feature_catalog is a global vocabulary table with no
-- organization_id, and this file adds no table.
-- The number is taken at build time (R1). On 2026-10-15 main held 268, and
-- the branch crm-atomic-sync held 269. Check it again at merge.
-- ============================================================================

INSERT INTO feature_catalog (slug, label, description, nav_href, category, sort_order, is_default) VALUES
    ('knowledge', 'Knowledge', 'What the company knows, written down', '/knowledge', 'apps', 58, true)
ON CONFLICT (slug) DO UPDATE
    SET label       = EXCLUDED.label,
        description = EXCLUDED.description,
        nav_href    = EXCLUDED.nav_href,
        category    = EXCLUDED.category,
        sort_order  = EXCLUDED.sort_order;
        -- is_default is intentionally NOT overwritten (same rule as 130).
