-- ============================================================================
-- 342_pm_agent_runs_app_subject.sql — an agent run on an app, with no task
-- (WS-MCP MCP-P1b-1, AD-5).
--
-- What: six changes to `pm_agent_runs` and `pm_agent_run_activities` (267).
--       * `task_id` becomes nullable on both tables.
--       * Both tables gain `app TEXT NULL`, the id of a door app (for example
--         `knowledge`) or `studio`. A CHECK holds its shape.
--       * A CHECK `..._one_subject` on each table: a row has a task or an
--         app, never both and never neither. It is added NOT VALID and then
--         validated, so the scan of the old rows takes no long lock.
--       * The CHECK of `client_via` (runs) and of `actor_via` (activities)
--         accepts `studio:` as well as `mcp:`. A Studio request has no MCP
--         client (ai_studio.md §14 B2c).
--       * An index for the list of the requests of one member, and an index
--         for the cap of open requests of one connection (AD-13).
--       The approval store is `gateway/app_requests.py`: it writes the app
--       runs. `acb_auth/app_requests.py` spends a request in one UPDATE.
--       `routes/projects/agent_runs.py` stays the only writer of TASK runs.
-- Why:  external_agent_access.md §19.5 and §19.12 AD-5, AD-16. An app request
--       of the MCP door, or of a Studio write, waits for its member on an
--       agent run that names an app and no task. The member answers it on
--       the card of Integrations, AI agents.
-- Depends on: 267_pm_agent_runs.sql, 289_agent_run_exclusive_claim.sql.
--
-- Expand only (R6). Each change WIDENS what the tables take. Old code always
-- writes a task and no app, so each of its rows passes both new CHECKs, and
-- its stamps `mcp:<slug>` pass the wider pattern. No row changes. The partial
-- unique indexes of 267 and 289 need no change: Postgres treats each NULL
-- `task_id` as distinct, so an app run never holds a task.
-- Idempotent per infra/postgres/README.md: DROP NOT NULL and ADD COLUMN IF
-- NOT EXISTS change nothing on a second run. Each CHECK is added only while
-- `pg_constraint` lacks it, or lacks `studio` in its definition. CREATE INDEX
-- IF NOT EXISTS.
-- The ALTER TABLE statements take the ACCESS EXCLUSIVE lock of each table for
-- a moment. VALIDATE then reads the rows under a lighter lock. So the file
-- sets lock_timeout first, as 338 does, and the runner tries the whole file
-- again on a lock timeout.
--
-- Tenant-scoped (R5): no new table. RLS and the policies of 267 cover both
-- tables, and the generated phases already carry them. The org of a new row
-- comes from the column DEFAULT, the bound tenant.
--
-- The number is taken at build time (R1). On 2026-11-04 origin/main held 340,
-- and the open pull request of crm_change_log held 341. Check it again at
-- merge.
-- Pinned by tests/unit/test_app_requests_r8.py, which finds this file by
-- CONTENT, never by number (R1).
-- ============================================================================

BEGIN;

SET LOCAL lock_timeout = '5s';

-- A run and its activities may name an app in place of a task.
ALTER TABLE pm_agent_runs ALTER COLUMN task_id DROP NOT NULL;
ALTER TABLE pm_agent_run_activities ALTER COLUMN task_id DROP NOT NULL;

ALTER TABLE pm_agent_runs ADD COLUMN IF NOT EXISTS app TEXT NULL;
ALTER TABLE pm_agent_run_activities ADD COLUMN IF NOT EXISTS app TEXT NULL;

DO $subject$
BEGIN
    -- The shape of an app id: `APP_ID_PATTERN` of acb_common/app_manifests.
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = to_regclass('pm_agent_runs')
           AND conname = 'pm_agent_runs_app_shape'
    ) THEN
        ALTER TABLE pm_agent_runs
            ADD CONSTRAINT pm_agent_runs_app_shape
            CHECK (app IS NULL OR app ~ '^[a-z]{1,32}$');
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = to_regclass('pm_agent_run_activities')
           AND conname = 'pm_agent_run_activities_app_shape'
    ) THEN
        ALTER TABLE pm_agent_run_activities
            ADD CONSTRAINT pm_agent_run_activities_app_shape
            CHECK (app IS NULL OR app ~ '^[a-z]{1,32}$');
    END IF;

    -- One subject for each row: a task or an app.
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = to_regclass('pm_agent_runs')
           AND conname = 'pm_agent_runs_one_subject'
    ) THEN
        ALTER TABLE pm_agent_runs
            ADD CONSTRAINT pm_agent_runs_one_subject
            CHECK ((task_id IS NULL) <> (app IS NULL)) NOT VALID;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = to_regclass('pm_agent_run_activities')
           AND conname = 'pm_agent_run_activities_one_subject'
    ) THEN
        ALTER TABLE pm_agent_run_activities
            ADD CONSTRAINT pm_agent_run_activities_one_subject
            CHECK ((task_id IS NULL) <> (app IS NULL)) NOT VALID;
    END IF;

    -- The stamp of the client: `mcp:<host>:<slug>`, or `studio:<...>` for a
    -- Studio request. 267 named both CHECKs inline, so Postgres gave them
    -- these names.
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = to_regclass('pm_agent_runs')
           AND conname = 'pm_agent_runs_client_via_check'
           AND pg_get_constraintdef(oid) LIKE '%studio%'
    ) THEN
        ALTER TABLE pm_agent_runs DROP CONSTRAINT IF EXISTS pm_agent_runs_client_via_check;
        ALTER TABLE pm_agent_runs
            ADD CONSTRAINT pm_agent_runs_client_via_check
            CHECK (client_via ~ '^(mcp|studio):[a-z0-9:_.-]{1,60}$') NOT VALID;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = to_regclass('pm_agent_run_activities')
           AND conname = 'pm_agent_run_activities_actor_via_check'
           AND pg_get_constraintdef(oid) LIKE '%studio%'
    ) THEN
        ALTER TABLE pm_agent_run_activities
            DROP CONSTRAINT IF EXISTS pm_agent_run_activities_actor_via_check;
        ALTER TABLE pm_agent_run_activities
            ADD CONSTRAINT pm_agent_run_activities_actor_via_check
            CHECK (actor_via IS NULL OR actor_via ~ '^(mcp|studio):[a-z0-9:_.-]{1,60}$')
            NOT VALID;
    END IF;
END
$subject$;

-- Each old row holds a task, no app and an `mcp:` stamp, so each passes.
-- VALIDATE of a valid CHECK changes nothing on a second run.
ALTER TABLE pm_agent_runs VALIDATE CONSTRAINT pm_agent_runs_one_subject;
ALTER TABLE pm_agent_run_activities VALIDATE CONSTRAINT pm_agent_run_activities_one_subject;
ALTER TABLE pm_agent_runs VALIDATE CONSTRAINT pm_agent_runs_client_via_check;
ALTER TABLE pm_agent_run_activities VALIDATE CONSTRAINT pm_agent_run_activities_actor_via_check;

-- "Waiting for you": the app requests of one member, newest first.
CREATE INDEX IF NOT EXISTS pm_agent_runs_app_member
    ON pm_agent_runs (organization_id, member_email, created_at DESC)
    WHERE app IS NOT NULL;

-- The cap of AD-13: the open app requests of one connection.
CREATE INDEX IF NOT EXISTS pm_agent_runs_app_open_connection
    ON pm_agent_runs (organization_id, connection_id)
    WHERE app IS NOT NULL AND status IN ('awaiting', 'in_progress');

COMMENT ON TABLE pm_agent_runs IS
    'WS-55 EA-12a: one run of an outside agent on one task. MCP-P1b-1 (AD-5): '
    'or one app request, with app set and no task. The gateway sets every '
    'status. routes/projects/agent_runs.py writes the task runs, '
    'gateway/app_requests.py writes the app runs, and '
    'acb_auth/app_requests.py spends an app request. '
    'external_agent_access.md §12.1 and §19.5.';
COMMENT ON TABLE pm_agent_run_activities IS
    'WS-55 EA-12a: one typed activity of an agent run (prompt, thought, '
    'action, response, elicitation, error). seq is the long-poll cursor. '
    'MCP-P1b-1: an activity of an app run names its app and no task. '
    'The writers are those of pm_agent_runs. external_agent_access.md §12.1 '
    'and §19.5.';

COMMIT;
