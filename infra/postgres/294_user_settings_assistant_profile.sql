-- ============================================================================
-- 294_user_settings_assistant_profile.sql — the member's assistant profile
-- (WS-63 PZ-1)
-- ============================================================================
-- Spec: project-docs/specs/personalization.md §4 and §9 PZ-1 · R5 · R6.
--
-- One new column on `user_settings`, `assistant_profile`. It holds what the
-- member told the assistant to keep: the form of address, the answer style,
-- named aliases and short facts. The routes of
-- `routes/admin/assistant_profile.py` (`/auth/me/assistant-profile`) read it
-- and write it. A PUT merges one key with `jsonb_set`, and a fact or an alias
-- changes in one statement. No write replaces the column, except the
-- "forget everything" DELETE, which sets it to NULL.
--
-- It is not inside `shell_prefs`. `PUT /auth/me/shell` writes that whole
-- object, so a layout save would delete the profile.
--
-- NULL means "the member set nothing". So the column is nullable with NO
-- default (R6). Nothing reads it before PZ-1, and the executor reads it only
-- from PZ-2, behind a flag.
--
-- No new table, so `test_tenant_coverage.py` changes nothing. `user_settings`
-- is tenant-scoped since the generated phases, and keyed
-- `(organization_id, user_id)` since 239. So a member in two organizations
-- has two profiles (D15).
--
-- Expand only. Old code never names the column, so it meets this schema as
-- it met the old one.
--
-- Depends on: 51_gtd_settings.sql (the table), 239_user_settings_per_org.sql.
-- ============================================================================

BEGIN;

ALTER TABLE user_settings ADD COLUMN IF NOT EXISTS assistant_profile JSONB;

COMMENT ON COLUMN user_settings.assistant_profile IS
    'PZ-1: the member''s assistant profile (address, style, aliases, facts). '
    'NULL = nothing set.';

COMMIT;
