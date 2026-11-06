-- ============================================================================
-- 350_focus_sessions.sql — the focus-session log (WS-MDC CC-5a, Metorite task
-- 1972, D188 D-MD-5).
--
-- What: one new table. `focus_sessions` holds one focus session of one member
--       on one task: the start, the end, the planned end, the pauses, the
--       timer mode and the answer to "Is it done?". The gateway module
--       `routes/projects/focus_sessions.py` is the one writer.
-- Why:  Before this table, actual time was one `actual_start` and
--       `actual_end` pair on `pm_task_personal` (187). Each session wrote over
--       the pair, so no row held a session, a pause or an outcome
--       (my_day_command_centre.md §2.2 item 5, §9.3).
-- Depends on: 130_org_access_control.sql (organization), 146 (pm_tasks).
--
-- Expand only (R6): one new table and three indexes. No rename, no drop, no
-- change to an existing table. No running code reads or writes the table
-- before this release, so NOT NULL columns and defaults are safe.
-- Idempotent per infra/postgres/README.md: CREATE TABLE IF NOT EXISTS, CREATE
-- INDEX IF NOT EXISTS, and an RLS block guarded on relforcerowsecurity.
--
-- Tenant-scoped (R5), in the idiom of 340: `organization_id` with ENABLE and
-- FORCE row level security and one policy with USING and WITH CHECK.
-- `REFERENCES` comes before `DEFAULT`, because the tenancy ratchet in
-- tests/unit/test_tenancy_boundary.py matches the two with no comma between.
-- The org of a new row comes from the DEFAULT, the bound tenant. The writer
-- never names it. No GRANT: the roles of 130 hold the default privileges.
--
-- Private to the member (D-MD-5). RLS covers the org only, as on
-- `pm_task_personal` and `reminders`. Each statement of the writer also
-- names `member_email` from the session, so a manager reads no minutes of
-- another member. `member_email` is lower case, and a CHECK holds that.
--
-- One open session for each member: the partial unique index
-- `focus_sessions_one_open`. A start ends the open session as `replaced`
-- and inserts the new row in one transaction. Two starts at once leave one
-- open row, because the second insert meets the index and tries once more.
--
-- The pause: `paused_at` is set while a pause runs. A resume adds the pause
-- to `paused_seconds` and moves `planned_end` by the same length, so a pause
-- stops the timer. The minutes of a session are the end minus the start
-- minus the paused seconds.
--
-- `task_id` cascades: a deleted task takes its sessions with it. The index
-- on `task_id` serves that cascade.
--
-- The number (R1): 350 at build time. On 2026-11-07 origin/main held 347,
-- a branch held 348 (the revoke reason of EA-18a) and CC-2 took 349 for its
-- build. No remote branch held 350. Check it again at merge.
-- Pinned by tests/unit/test_focus_session_rls_r8.py and
-- tests/unit/test_focus_sessions_r8.py, which find this file by CONTENT,
-- never by number (R1).
-- ============================================================================

BEGIN;

-- The foreign keys take a lock on `organization` and `pm_tasks`. Never wait
-- for it: on a lock timeout the runner tries the whole file again.
SET LOCAL lock_timeout = '5s';

CREATE TABLE IF NOT EXISTS focus_sessions (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id  UUID NOT NULL
                     REFERENCES organization (id) ON DELETE CASCADE
                     DEFAULT current_setting('app.tenant_id', true)::uuid,
    -- The owner, lower case. The session decides it, never a request body.
    member_email     TEXT NOT NULL,
    task_id          UUID NOT NULL REFERENCES pm_tasks (id) ON DELETE CASCADE,
    started_at       TIMESTAMPTZ NOT NULL,
    -- NULL while the session runs.
    ended_at         TIMESTAMPTZ NULL,
    -- The end of the timer (the start plus 25 or 50 minutes) or, for Flow,
    -- the end of the member's block. NULL for Flow with no block.
    planned_end      TIMESTAMPTZ NULL,
    paused_seconds   INTEGER NOT NULL DEFAULT 0,
    -- The start of the pause that runs now. NULL when no pause runs.
    paused_at        TIMESTAMPTZ NULL,
    mode             TEXT NOT NULL,
    -- The answer to "Is it done?", or what the server did. NULL while open.
    outcome          TEXT NULL,
    CONSTRAINT focus_sessions_member_email CHECK (
        member_email <> '' AND member_email = lower(member_email)),
    CONSTRAINT focus_sessions_mode CHECK (mode IN ('25/5', '50/10', 'flow')),
    CONSTRAINT focus_sessions_outcome CHECK (
        outcome IS NULL
        OR outcome IN ('done', 'almost', 'not_now', 'blocked', 'replaced', 'auto_closed')),
    CONSTRAINT focus_sessions_paused_seconds CHECK (paused_seconds >= 0),
    -- An ended session has an outcome, and an open one has none.
    CONSTRAINT focus_sessions_end_outcome CHECK ((ended_at IS NULL) = (outcome IS NULL)),
    CONSTRAINT focus_sessions_end_order CHECK (ended_at IS NULL OR ended_at >= started_at),
    CONSTRAINT focus_sessions_planned_order CHECK (
        planned_end IS NULL OR planned_end >= started_at),
    -- Only an open session can be paused.
    CONSTRAINT focus_sessions_pause_open CHECK (paused_at IS NULL OR ended_at IS NULL)
);

-- The day read: the sessions of one member in one local day.
CREATE INDEX IF NOT EXISTS idx_focus_sessions_member_started
    ON focus_sessions (organization_id, member_email, started_at);

-- At most one open session for each member.
CREATE UNIQUE INDEX IF NOT EXISTS focus_sessions_one_open
    ON focus_sessions (organization_id, member_email)
    WHERE ended_at IS NULL;

-- The cascade of a deleted task reads this column.
CREATE INDEX IF NOT EXISTS idx_focus_sessions_task
    ON focus_sessions (task_id);

DO $rls$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_class
         WHERE oid = to_regclass('focus_sessions')
           AND relrowsecurity
           AND relforcerowsecurity
    ) THEN
        ALTER TABLE focus_sessions ENABLE ROW LEVEL SECURITY;
        ALTER TABLE focus_sessions FORCE  ROW LEVEL SECURITY;
        DROP POLICY IF EXISTS focus_sessions_tenant_isolation ON focus_sessions;
        CREATE POLICY focus_sessions_tenant_isolation ON focus_sessions
            USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
            WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);
    END IF;
END
$rls$;

COMMENT ON TABLE focus_sessions IS
    'The focus-session log: one row for each focus session of one member on one task. '
    'Private to the member. WS-MDC CC-5a, my_day_command_centre.md §9.3, D188 D-MD-5.';

COMMIT;
