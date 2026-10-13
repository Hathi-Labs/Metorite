-- ============================================================================
-- 255_pm_task_refs.sql — the references of a task: links, pull requests,
-- issues, commits, branches, comments and documents.
--
-- What: one new table, `pm_task_refs`. Each row is one URL that a person, the
--       chat agent or an outside agent attached to one task. The classifier
--       in gateway/routes/projects/refs_classify.py gives each row its
--       `kind`, `provider` and `ref`. The route in
--       gateway/routes/projects/refs.py is the only writer. For a GitHub pull
--       request or issue the route also reads the title and the state through
--       the organization's GitHub token, and stamps `fetched_at`.
-- Why:  WS-57 TR-1, spec project-docs/specs/task_references.md §5 and §6.
--       The table is the one external_agent_access.md §11 proposed, with the
--       `issue`, `comment` and `doc` kinds and the `fetched_at` and `via`
--       columns added.
-- Depends on: 130_org_access_control.sql (organization), 146_projects.sql
--       (pm_tasks).
--
-- Expand only (R6): one new table and one index. No rename, no drop, and no
-- change to an existing table. Old code names no new object.
-- Idempotent per infra/postgres/README.md: CREATE TABLE IF NOT EXISTS,
-- CREATE UNIQUE INDEX IF NOT EXISTS, and an RLS block guarded on
-- relforcerowsecurity. A second run changes nothing.
-- Tenant-scoped (R5): `organization_id` with ENABLE and FORCE row level
-- security and one policy with USING and WITH CHECK, in the shape of
-- 251_crm_module_records.sql. `REFERENCES` comes before `DEFAULT`, because
-- the tenancy ratchet in tests/unit/test_tenancy_boundary.py matches the two
-- with no comma between. The foreign key is inline, never
-- `ADD CONSTRAINT <table>_org_fk`, because generated phase 3 adds that name.
-- The `pm_activities.type` CHECK already holds `link` (210), so each add and
-- each remove writes its timeline row with no change to that CHECK.
-- The number is taken at build time (R1). On 2026-10-13 main held 252, and
-- two open branches held 253 and 254. Check it again at merge.
-- Pinned by tests/unit/test_task_refs_route.py, which finds this file by
-- CONTENT, never by number (R1).
-- ============================================================================

BEGIN;

CREATE TABLE IF NOT EXISTS pm_task_refs (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id  UUID NOT NULL
                     REFERENCES organization (id) ON DELETE CASCADE
                     DEFAULT current_setting('app.tenant_id', true)::uuid,
    task_id          UUID NOT NULL REFERENCES pm_tasks (id) ON DELETE CASCADE,
    kind             TEXT NOT NULL
                     CHECK (kind IN ('pull_request', 'issue', 'commit', 'branch',
                                     'comment', 'doc', 'url')),
    provider         TEXT NOT NULL
                     CHECK (provider IN ('github', 'gitlab', 'bitbucket', 'google',
                                         'figma', 'notion', 'other')),
    ref              TEXT NOT NULL,
    url              TEXT NOT NULL CHECK (char_length(url) <= 2048),
    title            TEXT NULL,
    state            TEXT NULL
                     CHECK (state IS NULL
                            OR state IN ('open', 'draft', 'merged', 'closed')),
    fetched_at       TIMESTAMPTZ NULL,
    via              TEXT NOT NULL DEFAULT 'ui'
                     CHECK (via ~ '^(ui|chat|text|mcp:[a-z0-9:_.-]{1,60})$'),
    created_by       TEXT NOT NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- One reference, one row, for each task. A second add of the same URL finds
-- this key, and the route returns the first row. The list of one task reads
-- the same index through its leading columns.
CREATE UNIQUE INDEX IF NOT EXISTS pm_task_refs_key
    ON pm_task_refs (organization_id, task_id, kind, provider, ref);

DO $rls$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_class
         WHERE oid = to_regclass('pm_task_refs')
           AND relrowsecurity
           AND relforcerowsecurity
    ) THEN
        ALTER TABLE pm_task_refs ENABLE ROW LEVEL SECURITY;
        ALTER TABLE pm_task_refs FORCE  ROW LEVEL SECURITY;
        DROP POLICY IF EXISTS pm_task_refs_tenant_isolation ON pm_task_refs;
        CREATE POLICY pm_task_refs_tenant_isolation ON pm_task_refs
            USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
            WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);
    END IF;
END
$rls$;

COMMENT ON TABLE pm_task_refs IS
    'WS-57 TR-1: one URL attached to one task (a pull request, an issue, a '
    'commit, a branch, a comment, a document or a plain link). Only '
    'routes/projects/refs.py writes it. task_references.md §5.';

COMMIT;
