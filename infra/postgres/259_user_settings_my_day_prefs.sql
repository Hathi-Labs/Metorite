-- ============================================================================
-- 259_user_settings_my_day_prefs.sql — My Day's feed preferences (WS-58 MD-2d)
-- ============================================================================
-- Spec: project-docs/specs/my_day_feed.md §13.3 and §16 MD-2d · R5 · R6.
--
-- One new column on `user_settings`, `my_day_prefs`. It holds three keys:
-- `mutes` (§6.6), `open_in_app` (§11.2 rule 4) and `corrections` (§10.5).
-- `GET` and `PUT /shell/feed/prefs` (`routes/shell/prefs.py`) read it and
-- write it. The PUT merges one key with `jsonb_set`, and never replaces the
-- column.
--
-- It is not inside `shell_prefs`. `PUT /auth/me/shell` writes that whole
-- object, so a layout save would delete every mute and every correction.
--
-- NULL means "the member set no preference". So the column is nullable with
-- NO default (R6). Nothing reads it before MD-2d.
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

ALTER TABLE user_settings ADD COLUMN IF NOT EXISTS my_day_prefs JSONB;

COMMENT ON COLUMN user_settings.my_day_prefs IS
    'MD-2d: My Day''s feed preferences (mutes, open_in_app, corrections). '
    'NULL = no preference set.';

COMMIT;
