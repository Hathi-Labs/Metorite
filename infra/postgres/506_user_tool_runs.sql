-- ============================================================================
-- 506_user_tool_runs.sql - the run record of a user tool: one row for each
-- step that ran code (agent_sandbox.md §7.3 and §12.4 decision 10, SBX-4b1,
-- task 3986).
--
-- What: one new table. `user_tool_runs` holds one row for each step of a
--       user tool: a `build`, a `smoke` round or a `run`. It holds the
--       member, the run, the chat, the step, the backend, the outcome, the
--       exit code, the counts of the broker and the door, and a fixed error
--       class. gateway/studio/user_tool_runs.py is the one module that runs
--       SQL on the table. The member purge (gateway/routes/admin/members.py,
--       key `user_tool_runs`) deletes the rows of a leaver.
-- Why:  agent_sandbox.md §6.3 and §12.4. The step engine of SBX-4b1 writes
--       one `build` row for each build and one `smoke` row for each round.
--       SBX-4b2 refuses a second build of one gap by its `build` row. SBX-7
--       adds the credits of a row. No code writes a row before the step
--       engine lands.
-- Depends on: 02_chat_history.sql (chat_session), 130_org_access_control.sql
--             (organization).
--
-- THE TEXT OF A ROW. No column holds tenant text. `member_email`, `run_id`
--       and `thread_id` are ids. `phase`, `backend`, `outcome` and `signal`
--       hold the lists of §7.3. `error_class` holds a fixed code, never free
--       text, so its CHECK holds the shape of a code only.
--
-- THE CHAT. `thread_id` is nullable, and references the plain
--       `chat_session (id)` with ON DELETE SET NULL (§12.4 decision 10). A
--       run row is the record of a step that ran code, and SBX-7 adds its
--       credits. So a Delete of the chat keeps the row. The one writer
--       selects the chat under the bound tenant, so a chat of another org
--       gives no row. A foreign key check skips RLS, so no statement names a
--       chat by its id alone. The partial index on `thread_id` serves the
--       SET NULL, so a Delete of a chat does not scan the table.
--
-- NO OTHER FOREIGN KEY. `app_id`, `gap_id` and `repair_of` have none, so the
--       row stays after the delete of a package, a gap or a run (§7.3).
--
-- Expand only (R6): one new table and three indexes. No rename, no drop, no
-- change to an old object. The old code never names the table. Idempotent.
-- The foreign key takes a SHARE ROW EXCLUSIVE lock on `chat_session`, so the
-- file sets `lock_timeout` first, as 459 and 504 do. Tenant-scoped (R5):
-- `organization_id` with ENABLE and FORCE row level security and one policy
-- with USING and WITH CHECK. `REFERENCES` comes before `DEFAULT` (the
-- tenancy ratchet).
--
-- The number is taken at build time (R1): the lane booked 506 with
-- ~/lanes/reserve_migration.sh. Check it again at merge. Pinned by
-- tests/unit/test_user_tool_registry.py, which finds this file by CONTENT,
-- and tests/unit/test_tenant_coverage.py.
-- ============================================================================

BEGIN;

SET LOCAL lock_timeout = '5s';

CREATE TABLE IF NOT EXISTS user_tool_runs (
    organization_id  UUID NOT NULL
                     REFERENCES organization (id) ON DELETE CASCADE
                     DEFAULT current_setting('app.tenant_id', true)::uuid,
    id               UUID NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
    -- The package and its version. Null for a build that did not persist.
    -- No foreign key, so the row stays after a delete.
    app_id           UUID,
    version          INTEGER,
    -- The gap of the build. No foreign key.
    gap_id           UUID,
    -- The failed run that this repair answers (§6.7). No foreign key.
    repair_of        UUID,
    -- The signal of a failed run (§6.7).
    signal           TEXT,
    -- The verified member of the run, in lower case.
    member_email     TEXT NOT NULL,
    -- The agent run.
    run_id           TEXT NOT NULL,
    -- The chat. A Delete of the chat sets it to NULL and keeps the row.
    thread_id        TEXT REFERENCES chat_session (id) ON DELETE SET NULL,
    phase            TEXT NOT NULL,
    backend          TEXT NOT NULL,
    outcome          TEXT NOT NULL,
    exit_code        INTEGER,
    -- Measured by the broker. SBX-4b fills wall_ms only.
    wall_ms          INTEGER,
    cpu_ms           INTEGER,
    peak_mem_mb      INTEGER,
    -- Counted by the door. SBX-4b writes the three write counts as 0.
    api_reads        INTEGER NOT NULL DEFAULT 0,
    writes_held      INTEGER NOT NULL DEFAULT 0,
    writes_approved  INTEGER NOT NULL DEFAULT 0,
    writes_refused   INTEGER NOT NULL DEFAULT 0,
    -- From the proxy log. NULL in SBX-4b.
    egress_bytes     BIGINT,
    -- NULL until SBX-7.
    credits          NUMERIC,
    -- A fixed code, never free text.
    error_class      TEXT,
    started_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    ended_at         TIMESTAMPTZ,
    CONSTRAINT user_tool_runs_email CHECK (
        member_email <> '' AND member_email = lower(member_email)
        AND length(member_email) <= 320),
    CONSTRAINT user_tool_runs_run CHECK (
        run_id <> '' AND length(run_id) <= 200
        AND (thread_id IS NULL OR (thread_id <> '' AND length(thread_id) <= 200))),
    CONSTRAINT user_tool_runs_version CHECK (version IS NULL OR version >= 1),
    CONSTRAINT user_tool_runs_phase CHECK (phase IN ('build', 'smoke', 'run')),
    CONSTRAINT user_tool_runs_backend CHECK (
        backend IN ('docker-runc', 'docker-runsc', 'remote-runsc', 'e2b')),
    CONSTRAINT user_tool_runs_outcome CHECK (
        outcome IN ('succeeded', 'failed', 'timeout', 'refused', 'cancelled')),
    CONSTRAINT user_tool_runs_signal CHECK (
        signal IS NULL OR signal IN ('exit_code', 'schema', 'door_5xx')),
    CONSTRAINT user_tool_runs_counts CHECK (
        (wall_ms IS NULL OR wall_ms >= 0)
        AND (cpu_ms IS NULL OR cpu_ms >= 0)
        AND (peak_mem_mb IS NULL OR peak_mem_mb >= 0)
        AND api_reads >= 0 AND writes_held >= 0
        AND writes_approved >= 0 AND writes_refused >= 0
        AND (egress_bytes IS NULL OR egress_bytes >= 0)
        AND (credits IS NULL OR credits >= 0)),
    CONSTRAINT user_tool_runs_error_class CHECK (
        error_class IS NULL OR error_class ~ '^[a-z0-9_]{1,64}$'),
    CONSTRAINT user_tool_runs_ended CHECK (ended_at IS NULL OR ended_at >= started_at)
);

-- The runs of an org, newest first (the meter of SBX-7 and the view of SBX-5).
CREATE INDEX IF NOT EXISTS user_tool_runs_org_started_idx
    ON user_tool_runs (organization_id, started_at DESC);

-- The ON DELETE SET NULL of a chat.
CREATE INDEX IF NOT EXISTS user_tool_runs_thread_idx
    ON user_tool_runs (thread_id) WHERE thread_id IS NOT NULL;

-- The `build` row of a gap (SBX-4b2 refuses a second build of one gap).
CREATE INDEX IF NOT EXISTS user_tool_runs_gap_idx
    ON user_tool_runs (organization_id, gap_id) WHERE gap_id IS NOT NULL;

DO $rls$
DECLARE
    t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY['user_tool_runs'] LOOP
        IF NOT EXISTS (
            SELECT 1 FROM pg_class
             WHERE oid = to_regclass(t)
               AND relrowsecurity
               AND relforcerowsecurity
        ) THEN
            EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
            EXECUTE format('ALTER TABLE %I FORCE  ROW LEVEL SECURITY', t);
            EXECUTE format('DROP POLICY IF EXISTS %I ON %I', t || '_tenant_isolation', t);
            EXECUTE format(
                'CREATE POLICY %I ON %I '
                'USING      (organization_id = current_setting(''app.tenant_id'', true)::uuid) '
                'WITH CHECK (organization_id = current_setting(''app.tenant_id'', true)::uuid)',
                t || '_tenant_isolation', t);
        END IF;
    END LOOP;
END
$rls$;

COMMENT ON TABLE user_tool_runs IS
    'The run record of a user tool (agent_sandbox.md §7.3, SBX-4b1): one row '
    'for each build, smoke round or run. Each column holds an id, a count or '
    'a fixed word. A Delete of the chat sets thread_id to NULL and keeps the '
    'row. The member purge deletes the row. RLS covers the org.';

COMMIT;
