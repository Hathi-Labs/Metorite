-- ============================================================================
-- 316_user_settings_agent_delete_default_on.sql — the member's switch "Let my
-- AI agents delete tasks" is ON until the member turns it off (WS-55 EA-6i)
-- ============================================================================
-- Spec: project-docs/specs/external_agent_access.md EA3-51 and §15 EA-6h and
-- EA-6i · R5 · R6 · R8.
--
-- The owner, 2026-10-30: "Agents delete switch should default to on." This
-- amends 295, where the column was `NOT NULL DEFAULT false`.
--
-- What: `user_settings.agent_delete_enabled` becomes nullable, and the file
--   drops its default, so the default is NULL. NULL means "the member has
--   not chosen". `delete_enabled()` in
--   `gateway/routes/admin/agent_access_switch.py` reads NULL as ON, and it
--   reads no row as ON too. Only a stored `false` is OFF. The PUT of
--   `/auth/me/agent-access` still writes an explicit `true` or `false`.
--
-- The backfill: each `false` becomes NULL, in the transaction of the ALTER.
--   Each `true` stays true. No row is deleted, and no row goes to `false`.
--
-- What the backfill changes: 295 gave each row `false` by default, and the
--   PUT writes `false` too. So the column cannot tell the old default apart
--   from an explicit off. Every existing member switches ON, and stays ON
--   until they turn it off. A member who turned it off before this file ran
--   must turn it off again. This is the owner's decision.
--
-- Why it is safe: a delete still needs the three other layers of EA3-51.
--   The tool carries `destructiveHint: true`, the call names the exact title
--   and the version, and the person says yes in the agent first. The member
--   can turn the switch off at any time, in Integrations, AI agents.
--
-- The deploy window (R6): the deploy applies this file before it restarts
--   the services. Old code reads the column with `row[0] is True`, so for
--   those minutes it reads NULL as OFF. `tools/list` hides `delete_task`,
--   and the agent-delete route refuses with 403. That fails closed. An insert
--   by old code does not name the column, so it gets NULL, which new code
--   reads as ON.
--
-- Idempotent: the DO block works only while the column is still NOT NULL.
--   A second run finds it nullable, and it changes nothing. ⚠️ This guard is
--   load-bearing. After the first run a stored `false` is an explicit off.
--   The runner re-applies a changed file, and REPLAY_ALL re-applies each
--   file. A second backfill would turn each explicit off back into ON.
--
-- ── FORCE RLS ───────────────────────────────────────────────────────────────
--
-- `user_settings` carries FORCE ROW LEVEL SECURITY once `generated/04` has
-- run, and the runner connects as the table owner, which FORCE binds too.
-- So, when the table has row security, the file binds each tenant in turn
-- with a local `set_config('app.tenant_id', ...)` and runs the UPDATE for
-- that tenant, as 221 and 297 do. A table with no row security (the shape
-- of a ladder replay) gets one UPDATE with no tenant bound.
-- `organization` has no policy, so the loop can read it.
--
-- No new table, so `test_tenant_coverage.py` changes nothing. `user_settings`
-- stays keyed `(organization_id, user_id)` since 239, so a member in two
-- organizations has two switches (D15).
--
-- Depends on: 239_user_settings_per_org.sql, 295_user_settings_agent_delete.sql.
-- Pinned by: tests/unit/test_agent_delete_default_r8.py (R8).
-- ============================================================================

BEGIN;

DO $$
DECLARE
    org uuid;
    n bigint;
    total bigint := 0;
BEGIN
    -- Run once. After the first run the column is nullable, and a stored
    -- `false` is a member's explicit off, which a replay must keep.
    IF NOT EXISTS (
        SELECT 1 FROM pg_attribute
         WHERE attrelid = 'user_settings'::regclass
           AND attname = 'agent_delete_enabled'
           AND attnotnull
           AND NOT attisdropped) THEN
        RAISE NOTICE '316: agent_delete_enabled is nullable already, so nothing changes';
        RETURN;
    END IF;

    ALTER TABLE user_settings
        ALTER COLUMN agent_delete_enabled DROP NOT NULL,
        ALTER COLUMN agent_delete_enabled DROP DEFAULT;

    IF EXISTS (SELECT 1 FROM pg_class
                WHERE oid = 'user_settings'::regclass AND relrowsecurity) THEN
        -- Under the policy an unbound UPDATE sees no row. So bind each
        -- tenant in turn. A role that bypasses RLS does all rows in the
        -- first pass, and the next passes find none.
        FOR org IN SELECT id FROM organization LOOP
            PERFORM set_config('app.tenant_id', org::text, true);
            UPDATE user_settings SET agent_delete_enabled = NULL
             WHERE agent_delete_enabled = false;
            GET DIAGNOSTICS n = ROW_COUNT;
            total := total + n;
        END LOOP;
    ELSE
        UPDATE user_settings SET agent_delete_enabled = NULL
         WHERE agent_delete_enabled = false;
        GET DIAGNOSTICS n = ROW_COUNT;
        total := total + n;
    END IF;

    RAISE NOTICE '316: % row(s) of agent_delete_enabled went from false to NULL (on)', total;
END $$;

COMMENT ON COLUMN user_settings.agent_delete_enabled IS
    'EA-6h and EA-6i: the member lets their MCP agents delete tasks. NULL means '
    'the member has not chosen, and it reads as ON. Only false is OFF.';

COMMIT;
