-- ============================================================================
-- 358_user_settings_appearance.sql — the member's appearance (WS-PKG PKG-20)
-- ============================================================================
-- Spec: project-docs/specs/package_host.md §9.5 and the PKG-20 row · R5 · R6.
--
-- One new column on `user_settings`, `appearance`. It holds the record
-- `{mode, density, accent, updated_at}`. `GET` and `PUT /auth/me/appearance`
-- (`routes/admin/me.py`) read it and write it. The PUT writes the whole
-- record through `write_member_settings`, the one write of the row. The
-- server stamps `updated_at`.
--
-- It is not inside `shell_prefs`. `PUT /auth/me/shell` writes that whole
-- object, so a layout save would delete the appearance.
--
-- NULL means "the member saved no appearance on the server". So the column is
-- nullable with NO default (R6). The browser copy stays the first paint.
--
-- No new table, so `test_tenant_coverage.py` changes nothing. `user_settings`
-- is tenant-scoped since the generated phases, and keyed
-- `(organization_id, user_id)` since 239. The member purge deletes the whole
-- row, so it deletes this column too.
--
-- Expand only. Old code never names the column, so it meets this schema as
-- it met the old one.
--
-- Depends on: 51_gtd_settings.sql (the table), 239_user_settings_per_org.sql.
-- ============================================================================

BEGIN;

ALTER TABLE user_settings ADD COLUMN IF NOT EXISTS appearance JSONB;

COMMENT ON COLUMN user_settings.appearance IS
    'PKG-20: the member''s appearance {mode, density, accent, updated_at}. '
    'NULL = no server copy.';

COMMIT;
