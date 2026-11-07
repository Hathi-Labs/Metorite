-- ============================================================================
-- 347_studio_feature.sql — the `studio` feature of AI Studio.
--
-- What: one `feature_catalog` row, `studio`, in the `apps` category at sort
--       order 96, after Workflows (95). The matching FEATURES entry is in
--       packages/acb_auth/acb_auth/permissions.py. A catalog row with no
--       FEATURES entry is invisible to /auth/me, even for an owner, and
--       tests/unit/test_org_access_control.py pins the two together.
-- Why:  WS-STU J1, spec project-docs/specs/ai_studio.md §9 and §14 J1. The
--       router of every `/studio` route checks `feature:studio`
--       (gateway/routes/studio/actions.py). Before J1 it checked
--       `feature:workflows`.
-- Who holds it: `is_default` is true. Migration 207 gives `feature:*` to
--       `member` and `manager`, migration 130 gives it to `admin`, and
--       `owner` holds `*`. So the slug reaches every role except `guest`.
--       This file grants nothing, and it writes no role, no member and no
--       override row. The capability `studio:publish` lives in CAPABILITIES
--       only. Its seed on `admin` is J1-2 (Metorite task 1707).
-- Depends on: 130_org_access_control.sql (feature_catalog).
--
-- Expand only (R6): one new row. No new table, no new column, no rename and
-- no drop. Old code reads the row as one more catalog entry.
-- Idempotent per infra/postgres/README.md: INSERT ... ON CONFLICT (slug) DO
-- UPDATE. A second run changes nothing. The update does not touch
-- `is_default`, the same rule as 130, 144 and 270: an admin may have changed
-- the default set, and a redeploy must not undo that.
-- Tenant scope (R5): feature_catalog is a global vocabulary table with no
-- organization_id, and this file adds no table.
-- The number is taken at build time (R1). On 2026-11-05 origin/main held 345
-- (345_chat_session_continued_from.sql), and the branch
-- kg4-structural-edges-mt-2a446b84 held 346. No other branch or worktree
-- held 346 or higher. Check it again at merge (scripts/premerge.sh).
-- ============================================================================

INSERT INTO feature_catalog (slug, label, description, nav_href, category, sort_order, is_default) VALUES
    ('studio', 'Studio', 'Apps, workflows and skills in one library', '/studio', 'apps', 96, true)
ON CONFLICT (slug) DO UPDATE
    SET label       = EXCLUDED.label,
        description = EXCLUDED.description,
        nav_href    = EXCLUDED.nav_href,
        category    = EXCLUDED.category,
        sort_order  = EXCLUDED.sort_order;
        -- is_default is intentionally NOT overwritten (same rule as 130).
