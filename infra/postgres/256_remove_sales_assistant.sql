-- ============================================================================
-- 256_remove_sales_assistant.sql — the removed agent leaves dynamic_agents.
--
-- What: one DELETE of each `dynamic_agents` row of `agent-sales-assistant`.
-- Why:  WS-53 CRM-C7 item 6, spec project-docs/specs/crm_platform.md §20.10.
--       The owner said on 2026-10-12 that the agent does not exist and is not
--       needed. The same change deletes its `agents.json` entry, which the
--       importer (`routes/agent.py::_sync_file_into_db`) copied into this
--       table on each load. The importer only adds rows, so without this step
--       the row stays and the agent stays in the list.
-- Number (R1): taken at build time on 2026-10-13. The spec reserved 254, but
--       the open PR #938 holds 254 and the CRM-C3 worktree holds 255. Check
--       the number again at merge.
-- Depends on: 15_dynamic_agents.sql (the table), and generated/01..04
--       (organization_id and the RLS policy) when they are applied.
--
-- ── Tenancy ─────────────────────────────────────────────────────────────────
--
-- Two shapes, as in 221. The ladder runs before `generated/`, so a fresh
-- replay has no `organization_id` and no RLS, and one DELETE serves it.
-- Production has `organization_id` and FORCE ROW LEVEL SECURITY on this table
-- (`generated/04_policies.sql`). The runner connects as the table owner, and
-- FORCE binds the owner too unless it is a superuser. An unbound DELETE there
-- removes nothing and reports success. So in that shape the file binds each
-- tenant in turn with `set_config('app.tenant_id', ..., true)`. The bind is
-- local to this transaction. `organization` has no policy, so the loop can
-- read it.
--
-- Safe (R6): no foreign key points at `dynamic_agents`, and nothing reads the
-- row by name after this change. A replay deletes nothing more, so the file
-- is idempotent. It adds no table (R5).
-- Pinned by tests/unit/test_no_sales_assistant_r8.py, which finds this file by
-- CONTENT, never by number (R1), and runs it as a role that is not a
-- superuser, not the owner and not BYPASSRLS.
-- ============================================================================

SET lock_timeout = '5s';

DO $remove_sales_assistant$
DECLARE
    org     uuid;
    tenancy boolean;
BEGIN
    SELECT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = current_schema()
          AND table_name = 'dynamic_agents'
          AND column_name = 'organization_id'
    ) INTO tenancy;

    IF NOT tenancy THEN
        DELETE FROM dynamic_agents WHERE name = 'agent-sales-assistant';
        RETURN;
    END IF;

    FOR org IN SELECT id FROM organization LOOP
        PERFORM set_config('app.tenant_id', org::text, true);
        EXECUTE $q$
            DELETE FROM dynamic_agents
            WHERE name = 'agent-sales-assistant' AND organization_id = $1
        $q$ USING org;
    END LOOP;
END $remove_sales_assistant$;
