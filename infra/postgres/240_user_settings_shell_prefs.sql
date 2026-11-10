-- ============================================================================
-- 240_user_settings_shell_prefs.sql — the member's shell layout (WS-44 NS-7)
-- ============================================================================
-- Spec: project-docs/specs/navigation_shell.md §8.2 · R5 · R6.
--
-- One new column on `user_settings`, `shell_prefs`. It holds the member's
-- preset, their answer to the first sign-in question ("answered" or
-- "skipped"), their pins, and an optional card order and job order. The
-- shell reads and writes it through `GET` and `PUT /auth/me/shell`
-- (`routes/admin/me.py`).
--
-- NULL means "never asked, use the role's preset". So the column is nullable
-- with NO default (R6): a default would make every existing row claim an
-- answer that nobody gave, and the first sign-in question would never show.
--
-- No new table, so `test_tenant_coverage.py` changes nothing. `user_settings`
-- is tenant-scoped since the generated phases, and keyed
-- `(organization_id, user_id)` since 239. So one address keeps one layout in
-- each of its organizations.
--
-- Expand only. Old code never names the column, so it meets this schema as
-- it met the old one.
--
-- Depends on: 51_gtd_settings.sql (the table), 239_user_settings_per_org.sql.
-- ============================================================================

BEGIN;

ALTER TABLE user_settings ADD COLUMN IF NOT EXISTS shell_prefs JSONB;

COMMENT ON COLUMN user_settings.shell_prefs IS
    'NS-7: the member''s shell layout (preset, answer, pins, card and job '
    'order). NULL = never asked, so the role''s preset applies.';

COMMIT;
