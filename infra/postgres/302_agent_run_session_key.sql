-- ============================================================================
-- 302_agent_run_session_key.sql — the MCP session of an agent run.
--
-- What: one nullable column on `pm_agent_runs`, `session_key`, and a CHECK on
--       its shape: NULL, or 22 base64url characters.
-- Why:  WS-55 SW-7 (Metorite task #338). Two terminals on ONE machine share
--       one `agent_connection` and one stamp, so the gate counted terminal
--       A's run as terminal B's own. The edge now gives each MCP session a
--       key (spec §12.5), signs it into each gateway call as
--       `X-Agent-Session`, and the gateway stores it on the run. The own run
--       of an agent is then member + stamp + connection + session.
-- Depends on: 267_pm_agent_runs.sql.
--
-- Expand only (R6). The column is NULL for every existing row, and old code
-- never reads or writes it. A NULL key is the TRANSITION rule of §12.5: a
-- run with no key counts as the own run of any session of its connection,
-- and the first agent session that touches it adopts it. No index changes:
-- `pm_agent_runs_one_open_per_task` already allows one open run per task, and
-- `pm_agent_runs_one_open` stays as it is.
-- Idempotent: ADD COLUMN IF NOT EXISTS, DROP CONSTRAINT IF EXISTS before ADD.
-- Tenant-scoped (R5): no new table. The column lives on a table under FORCE
-- RLS already.
-- The number is taken at build time (R1). On 2026-10-25 main held 301, and
-- branch kc-1a-drive held 300. Check it again at merge.
-- Pinned by tests/unit/test_agent_run_exclusive.py, which finds this file by
-- CONTENT, never by number (R1).
-- ============================================================================

BEGIN;

ALTER TABLE pm_agent_runs ADD COLUMN IF NOT EXISTS session_key TEXT NULL;

ALTER TABLE pm_agent_runs DROP CONSTRAINT IF EXISTS pm_agent_runs_session_key_shape;

ALTER TABLE pm_agent_runs
    ADD CONSTRAINT pm_agent_runs_session_key_shape
    CHECK (session_key IS NULL OR session_key ~ '^[A-Za-z0-9_-]{22}$');

COMMENT ON COLUMN pm_agent_runs.session_key IS
    'WS-55 SW-7. The MCP session key of the agent session that holds the run '
    '(spec external_agent_access.md §12.5). NULL for a member start and for a '
    'run from before SW-7: any session of the connection may adopt it.';

COMMIT;
