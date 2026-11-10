-- ============================================================================
-- 295_user_settings_agent_delete.sql — the member's switch "Let my AI agents
-- delete tasks" (WS-55 EA-6h)
-- ============================================================================
-- Spec: project-docs/specs/external_agent_access.md EA3-51 and §15 EA-6h ·
-- R5 · R6.
--
-- One new column on `user_settings`, `agent_delete_enabled`. The routes of
-- `routes/admin/agent_access_switch.py` (`/auth/me/agent-access`) read it and
-- write it. `POST /projects/tasks/{task_id}/agent-delete` reads it, and it
-- refuses with 403 while the column is false. The MCP edge reads it at
-- `tools/list`, and it shows the tool `delete_task` only while it is true.
--
-- `NOT NULL DEFAULT false`: the default is OFF, and a member turns it on.
-- On Postgres 11 and later this ADD COLUMN writes no row, because the
-- default is a constant.
--
-- No new table, so `test_tenant_coverage.py` changes nothing. `user_settings`
-- is tenant-scoped since the generated phases, and keyed
-- `(organization_id, user_id)` since 239. So a member in two organizations
-- has two switches (D15).
--
-- Expand only. Old code never names the column, and an insert by old code
-- gets the default, so old code meets this schema as it met the old one.
--
-- Depends on: 51_gtd_settings.sql (the table), 239_user_settings_per_org.sql.
-- ============================================================================

BEGIN;

ALTER TABLE user_settings
    ADD COLUMN IF NOT EXISTS agent_delete_enabled BOOLEAN NOT NULL DEFAULT false;

COMMENT ON COLUMN user_settings.agent_delete_enabled IS
    'EA-6h: the member lets their MCP agents delete tasks. Default false.';

COMMIT;
