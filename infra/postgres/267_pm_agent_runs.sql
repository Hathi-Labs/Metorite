-- ============================================================================
-- 267_pm_agent_runs.sql — agent runs on a task, and the typed activities of
-- each run (the Plane model).
--
-- What: two new tables and one wider CHECK.
--       * `pm_agent_runs` holds one row for each run of an outside agent on
--         one task. The gateway sets every status. An agent never writes one.
--       * `pm_agent_run_activities` holds one row for each typed activity of
--         a run: prompt, thought, action, response, elicitation or error.
--         `seq` is the cursor of the long poll (EA12-2).
--       * `pm_notifications_kind_check` gains `agent_question`, by DROP and
--         ADD as 214 does. `NOTIFICATION_KINDS` in
--         gateway/routes/projects/notifications.py gains the same kind.
--       The routes in gateway/routes/projects/agent_runs.py are the only
--       writer of both tables.
-- Why:  WS-55 EA-12a, spec project-docs/specs/external_agent_access.md §12.1
--       (D-EA-10, EA12-1 to EA12-13).
-- Depends on: 130_org_access_control.sql (organization), 146_projects.sql
--       (pm_tasks, pm_activities), 152_projects_notifications.sql and
--       214_projects_nudge_notification.sql (pm_notifications),
--       248_agent_access_tokens.sql (agent_connection).
--
-- Expand only (R6): two new tables, their indexes, and a CHECK that only
-- WIDENS. No rename and no drop of data. Old code never writes
-- `agent_question`, so the gateway that runs before the restart keeps
-- working against the wider constraint.
-- Idempotent per infra/postgres/README.md: CREATE TABLE IF NOT EXISTS,
-- CREATE INDEX IF NOT EXISTS, RLS blocks guarded on relforcerowsecurity, and
-- a DROP IF EXISTS before the ADD of the CHECK. A second run changes nothing.
-- Tenant-scoped (R5): each table has `organization_id` with ENABLE and FORCE
-- row level security and one policy with USING and WITH CHECK, in the shape
-- of 255_pm_task_refs.sql. `REFERENCES` comes before `DEFAULT`, because the
-- tenancy ratchet in tests/unit/test_tenancy_boundary.py matches the two
-- with no comma between. The organization foreign key is inline, never
-- `ADD CONSTRAINT <table>_org_fk`, because generated phase 3 adds that name.
-- The organization comes from the bound session (the column default), never
-- from request input.
-- The number is taken at build time (R1). On 2026-10-14 main held 264, and
-- the open branch ws55-ea6d held 265 (`265_pm_tasks_source_mcp.sql`). So
-- this file takes 267. Check it again at merge.
-- Pinned by tests/unit/test_agent_runs_r8.py, which finds this file by
-- CONTENT, never by number (R1).
-- ============================================================================

BEGIN;

CREATE TABLE IF NOT EXISTS pm_agent_runs (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id         UUID NOT NULL
                            REFERENCES organization (id) ON DELETE CASCADE
                            DEFAULT current_setting('app.tenant_id', true)::uuid,
    task_id                 UUID NOT NULL REFERENCES pm_tasks (id) ON DELETE CASCADE,
    -- The member the agent acts as. From the context, never from a body.
    member_email            TEXT NOT NULL,
    -- The connection that started or last wrote the run.
    connection_id           UUID NULL REFERENCES agent_connection (id) ON DELETE SET NULL,
    client_via              TEXT NOT NULL
                            CHECK (client_via ~ '^mcp:[a-z0-9:_.-]{1,60}$'),
    status                  TEXT NOT NULL
                            CHECK (status IN ('created', 'in_progress', 'awaiting',
                                              'completed', 'stopping', 'stopped',
                                              'failed', 'stale')),
    -- The member, or `mcp:<slug>`.
    started_by              TEXT NOT NULL,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at              TIMESTAMPTZ NULL,
    ended_at                TIMESTAMPTZ NULL,
    stopped_at              TIMESTAMPTZ NULL,
    stopped_by              TEXT NULL,
    external_link           TEXT NULL
                            CHECK (external_link IS NULL
                                   OR (external_link ~ '^https://'
                                       AND char_length(external_link) <= 2048)),
    -- The source of the derived `stale` and `answered` states.
    last_activity_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_agent_activity_at  TIMESTAMPTZ NULL,
    error_metadata          JSONB NULL,
    idempotency_key         TEXT NULL
                            CHECK (idempotency_key IS NULL
                                   OR char_length(idempotency_key) BETWEEN 1 AND 128)
);

-- A second start with the same key returns the first run.
CREATE UNIQUE INDEX IF NOT EXISTS pm_agent_runs_idempotency_key
    ON pm_agent_runs (organization_id, member_email, idempotency_key)
    WHERE idempotency_key IS NOT NULL;

-- One OPEN run for each task, member and client (EA12-6). A second start
-- finds this row and returns it with `created: false`.
CREATE UNIQUE INDEX IF NOT EXISTS pm_agent_runs_one_open
    ON pm_agent_runs (organization_id, task_id, member_email, client_via)
    WHERE status IN ('created', 'in_progress', 'awaiting', 'stopping');

-- The runs of one task, newest first. The pill reads it too.
CREATE INDEX IF NOT EXISTS pm_agent_runs_task
    ON pm_agent_runs (task_id, created_at DESC);

-- The runs of one member, newest first (`GET /projects/agent-runs`).
CREATE INDEX IF NOT EXISTS pm_agent_runs_member
    ON pm_agent_runs (organization_id, member_email, created_at DESC);

CREATE TABLE IF NOT EXISTS pm_agent_run_activities (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id   UUID NOT NULL
                      REFERENCES organization (id) ON DELETE CASCADE
                      DEFAULT current_setting('app.tenant_id', true)::uuid,
    run_id            UUID NOT NULL REFERENCES pm_agent_runs (id) ON DELETE CASCADE,
    task_id           UUID NOT NULL REFERENCES pm_tasks (id) ON DELETE CASCADE,
    -- The cursor `after` of the long poll (EA12-2). A timestamp can tie, and
    -- the cap of 20 ephemeral rows deletes rows.
    seq               BIGINT GENERATED ALWAYS AS IDENTITY,
    -- Written as `= ANY (ARRAY[...])`, not `IN (...)`, on purpose:
    -- tests/unit/test_projects_migration.py reads the LAST `CHECK (type IN`
    -- of the tree as the vocabulary of `pm_activities.type`. The two forms
    -- enforce the same rule.
    type              TEXT NOT NULL
                      CHECK (type = ANY (ARRAY['prompt', 'thought', 'action', 'response',
                                               'elicitation', 'error'])),
    content           JSONB NOT NULL DEFAULT '{}'::jsonb,
    content_metadata  JSONB NULL,
    -- True for `thought` and `action`. The route keeps the last 20.
    ephemeral         BOOLEAN NOT NULL DEFAULT false,
    signal            TEXT NULL
                      CHECK (signal IS NULL OR signal IN ('continue', 'stop', 'select')),
    signal_metadata   JSONB NULL,
    -- The member email. `actor_via` is `mcp:<slug>` for an agent, and NULL
    -- for a person.
    actor             TEXT NOT NULL,
    actor_via         TEXT NULL
                      CHECK (actor_via IS NULL OR actor_via ~ '^mcp:[a-z0-9:_.-]{1,60}$'),
    -- The elicitation that a prompt answers. The server sets it (EA12-7).
    in_reply_to       UUID NULL REFERENCES pm_agent_run_activities (id) ON DELETE SET NULL,
    -- The timeline row (a `comment`), when the activity wrote one.
    pm_activity_id    UUID NULL REFERENCES pm_activities (id) ON DELETE SET NULL,
    idempotency_key   TEXT NULL
                      CHECK (idempotency_key IS NULL
                             OR char_length(idempotency_key) BETWEEN 1 AND 128),
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- A second write with the same key returns the first row (R5).
CREATE UNIQUE INDEX IF NOT EXISTS pm_agent_run_activities_idempotency_key
    ON pm_agent_run_activities (run_id, idempotency_key)
    WHERE idempotency_key IS NOT NULL;

-- The activities of one run after a cursor, in order.
CREATE INDEX IF NOT EXISTS pm_agent_run_activities_run_seq
    ON pm_agent_run_activities (run_id, seq);

-- The answer of a question. The open-question reads look up `in_reply_to`
-- for each elicitation, and the self foreign key needs it on a delete.
CREATE INDEX IF NOT EXISTS pm_agent_run_activities_in_reply_to
    ON pm_agent_run_activities (in_reply_to)
    WHERE in_reply_to IS NOT NULL;

-- A delete of a timeline row sets this column to NULL. Without an index,
-- each such delete reads the whole table.
CREATE INDEX IF NOT EXISTS pm_agent_run_activities_pm_activity
    ON pm_agent_run_activities (pm_activity_id)
    WHERE pm_activity_id IS NOT NULL;

-- A delete of a task cascades here through `task_id`.
CREATE INDEX IF NOT EXISTS pm_agent_run_activities_task
    ON pm_agent_run_activities (task_id);

DO $rls$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_class
         WHERE oid = to_regclass('pm_agent_runs')
           AND relrowsecurity
           AND relforcerowsecurity
    ) THEN
        ALTER TABLE pm_agent_runs ENABLE ROW LEVEL SECURITY;
        ALTER TABLE pm_agent_runs FORCE  ROW LEVEL SECURITY;
        DROP POLICY IF EXISTS pm_agent_runs_tenant_isolation ON pm_agent_runs;
        CREATE POLICY pm_agent_runs_tenant_isolation ON pm_agent_runs
            USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
            WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_class
         WHERE oid = to_regclass('pm_agent_run_activities')
           AND relrowsecurity
           AND relforcerowsecurity
    ) THEN
        ALTER TABLE pm_agent_run_activities ENABLE ROW LEVEL SECURITY;
        ALTER TABLE pm_agent_run_activities FORCE  ROW LEVEL SECURITY;
        DROP POLICY IF EXISTS pm_agent_run_activities_tenant_isolation
            ON pm_agent_run_activities;
        CREATE POLICY pm_agent_run_activities_tenant_isolation ON pm_agent_run_activities
            USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
            WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);
    END IF;
END
$rls$;

COMMENT ON TABLE pm_agent_runs IS
    'WS-55 EA-12a: one run of an outside agent on one task. The gateway sets '
    'every status. Only routes/projects/agent_runs.py writes it. '
    'external_agent_access.md §12.1.';
COMMENT ON TABLE pm_agent_run_activities IS
    'WS-55 EA-12a: one typed activity of an agent run (prompt, thought, '
    'action, response, elicitation, error). seq is the long-poll cursor. '
    'Only routes/projects/agent_runs.py writes it. external_agent_access.md §12.1.';

-- The notification of the question an agent asks a member (EA12-8). WIDENS
-- the CHECK that 214 last defined. tests/unit/test_projects_notifications.py
-- reads the whole ladder and holds the last definition to NOTIFICATION_KINDS.
ALTER TABLE pm_notifications DROP CONSTRAINT IF EXISTS pm_notifications_kind_check;

ALTER TABLE pm_notifications
    ADD CONSTRAINT pm_notifications_kind_check
    CHECK (kind IN ('assigned', 'mention', 'comment', 'nudge', 'agent_question'));

COMMIT;
