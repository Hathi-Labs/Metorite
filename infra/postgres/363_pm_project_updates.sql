-- ============================================================================
-- 363_pm_project_updates.sql — a project update with its health (WS-39 LP-1a,
-- Metorite task 988).
--
-- What: one new table. `pm_project_updates` holds one update of one
--       `pm_projects` node: the health (`on_track`, `at_risk` or `off_track`)
--       that the author picks, a short text, who posted it and when.
--       gateway/routes/projects/updates.py is the one writer.
-- Why:  Linear parity P3 (project_management_app.md §14.2 row LP-1, design
--       §14.2a). A lead tells the team how a project is going. LP-1a stores
--       the update, lists it, and shows the newest one on the project. The
--       AI draft, the digest, the reminder, the roll-up of a space and the
--       computed health are later slices.
-- Depends on: 130_org_access_control.sql (organization) and 146
--       (pm_projects).
--
-- Expand only (R6): one new table, one index, one trigger function and one
-- trigger. No rename, no drop, and no change to an existing table. `pm_projects`
-- gains no constraint, so this file takes no long lock on it. Old code names
-- no new object, and no running code writes this table.
-- Idempotent per infra/postgres/README.md: CREATE TABLE IF NOT EXISTS, CREATE
-- INDEX IF NOT EXISTS, CREATE OR REPLACE FUNCTION, DROP TRIGGER IF EXISTS then
-- CREATE, and an RLS block guarded on relforcerowsecurity.
-- Tenant-scoped (R5): `organization_id` with ENABLE and FORCE row level
-- security and one policy with USING and WITH CHECK, in the shape of 339.
-- `REFERENCES` comes before `DEFAULT`, because the tenancy ratchet in
-- tests/unit/test_tenancy_boundary.py matches the two with no comma between.
-- The org of a new row comes from the DEFAULT, the bound tenant. The writer
-- never names it.
--
-- Same-org link. `pm_projects` has no UNIQUE (organization_id, id), so a
-- composite foreign key is not possible without a constraint on a hot table.
-- A plain foreign key on the primary key gives the cascade. The trigger
-- `pm_project_updates_same_org` gives the same-org rule: the project of an
-- update must be a row of the update's own org, for each role.
--
-- `author` is the actor string of the session, in lower case. The route
-- never reads it from the body. `drafted_by_ai` is false in LP-1a: no route
-- writes true until the drafter slice. `meta` holds `via` (D-PM-36) and
-- nothing that a person typed.
-- The body cap (20000 characters) is the cap of an agent comment. The route
-- refuses a longer body first, so the CHECK is the second line.
-- Retention: no pruning. This file deletes nothing.
--
-- The number is taken at build time (R1). On 2026-11-08 origin/main held up
-- to 359. Open branches held 360 (two), 361 (SP-1), 362 (SP-8), 366 and 367.
-- Check it again at merge.
-- Pinned by tests/unit/test_pm_project_updates_r8.py, which finds this file
-- by CONTENT, never by number (R1).
-- ============================================================================

BEGIN;

CREATE TABLE IF NOT EXISTS pm_project_updates (
    id               UUID NOT NULL DEFAULT gen_random_uuid(),
    organization_id  UUID NOT NULL
                     REFERENCES organization (id) ON DELETE CASCADE
                     DEFAULT current_setting('app.tenant_id', true)::uuid,
    -- The project that the update is about.
    project_id       UUID NOT NULL REFERENCES pm_projects (id) ON DELETE CASCADE,
    -- The actor of the session, in lower case. Never a body field.
    author           TEXT NOT NULL,
    health           TEXT NOT NULL,
    body             TEXT NOT NULL,
    posted_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    drafted_by_ai    BOOLEAN NOT NULL DEFAULT false,
    -- `via` (D-PM-36): the channel that the member acted through.
    meta             JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (id),
    -- The target of a later child's foreign key, so a child stays in its org.
    CONSTRAINT pm_project_updates_org_id_key UNIQUE (organization_id, id),
    CONSTRAINT pm_project_updates_health CHECK (
        health IN ('on_track', 'at_risk', 'off_track')),
    CONSTRAINT pm_project_updates_author CHECK (
        author <> '' AND author = lower(author)),
    CONSTRAINT pm_project_updates_body CHECK (
        char_length(btrim(body)) > 0 AND char_length(body) <= 20000),
    CONSTRAINT pm_project_updates_meta CHECK (jsonb_typeof(meta) = 'object')
);

-- The updates of one project, newest first. The id breaks a tie of the time,
-- so a page of the list never skips or repeats a row.
CREATE INDEX IF NOT EXISTS idx_pm_project_updates_project
    ON pm_project_updates (project_id, posted_at DESC, id DESC);

-- The project of an update is a row of the update's own org. For each role,
-- the table owner too. Fence: tests/unit/test_pm_project_updates_r8.py.
CREATE OR REPLACE FUNCTION pm_project_updates_same_org() RETURNS trigger
LANGUAGE plpgsql AS $fn$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pm_projects
         WHERE id = NEW.project_id AND organization_id = NEW.organization_id
    ) THEN
        RAISE EXCEPTION 'pm_project_updates: the project is not a project of this organization'
            USING ERRCODE = 'foreign_key_violation';
    END IF;
    RETURN NEW;
END
$fn$;

DROP TRIGGER IF EXISTS pm_project_updates_same_org ON pm_project_updates;
CREATE TRIGGER pm_project_updates_same_org
    BEFORE INSERT OR UPDATE OF organization_id, project_id
    ON pm_project_updates
    FOR EACH ROW EXECUTE FUNCTION pm_project_updates_same_org();

DO $rls$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_class
         WHERE oid = to_regclass('pm_project_updates')
           AND relrowsecurity
           AND relforcerowsecurity
    ) THEN
        ALTER TABLE pm_project_updates ENABLE ROW LEVEL SECURITY;
        ALTER TABLE pm_project_updates FORCE  ROW LEVEL SECURITY;
        DROP POLICY IF EXISTS pm_project_updates_tenant_isolation ON pm_project_updates;
        CREATE POLICY pm_project_updates_tenant_isolation ON pm_project_updates
            USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
            WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);
    END IF;
END
$rls$;

COMMENT ON TABLE pm_project_updates IS
    'One update of a project with its health (WS-39 LP-1a). '
    'Only gateway/routes/projects/updates.py writes it. '
    'project-docs/specs/project_management_app.md §14.2a.';

COMMIT;
