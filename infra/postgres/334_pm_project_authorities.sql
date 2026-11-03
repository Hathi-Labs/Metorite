-- ============================================================================
-- 334_pm_project_authorities.sql — a lead pins a published Knowledge page to a
-- project as its authority (WS-ALN ALN-4, Metorite task 10172c41).
--
-- What: one new table. `pm_project_authorities` holds one pin of one
--       `kb_pages` row to one `pm_projects` node. It keeps the version of the
--       page at the pin, who pinned and when, a state, and who removed the pin
--       and when. gateway/routes/projects/authorities.py is the one writer.
-- Why:  A project needs a named source of truth (project_alignment.md §4.1).
--       ALN-5 reads the pins to find the pages that hold the rules. This
--       slice adds the table and three routes only: no UI, no flag, no job.
-- Depends on: 130_org_access_control.sql (organization), 146 (pm_projects)
--       and 272_knowledge_pages.sql (kb_pages).
--
-- Expand only (R6): one new table, two indexes, one trigger function and one
-- trigger. No rename, no drop, and no change to an existing table. `pm_projects`
-- and `kb_pages` gain no constraint, so this file takes no long lock on either.
-- Old code names no new object, and no running code writes this table.
-- Idempotent per infra/postgres/README.md: CREATE TABLE IF NOT EXISTS, CREATE
-- INDEX IF NOT EXISTS, CREATE OR REPLACE FUNCTION, DROP TRIGGER IF EXISTS then
-- CREATE, and an RLS block guarded on relforcerowsecurity.
-- Tenant-scoped (R5): `organization_id` with ENABLE and FORCE row level
-- security and one policy with USING and WITH CHECK, in the shape of 325.
-- `REFERENCES` comes before `DEFAULT`, because the tenancy ratchet in
-- tests/unit/test_tenancy_boundary.py matches the two with no comma between.
-- The org of a new row comes from the DEFAULT, the bound tenant. The writer
-- never names it.
--
-- Same-org link. Neither `pm_projects` nor `kb_pages` has UNIQUE
-- (organization_id, id), so a composite foreign key is not possible without a
-- constraint on two hot tables. That is an ACCESS EXCLUSIVE index build on
-- each, and this slice does not take it. Two plain foreign keys on the primary
-- keys give the cascade. The trigger `pm_project_authorities_same_org` gives
-- the same-org rule: the project and the page of a pin must both be rows of
-- the pin's own org, for each role. tests/unit/test_project_authorities_r8.py
-- pins both.
--
-- Version. `page_version` is the highest `kb_page_versions.version` of the
-- page at the pin. `kb_pages` has no version column of its own.
-- State. `active` and `removed` are the states that code writes. `stale` is
-- legal here for ALN-5, but ALN-4 derives "stale" at read time from the page
-- and writes nothing: Knowledge emits no event on an archive.
-- A removal keeps the row (`state = 'removed'`, `removed_by`, `removed_at`).
-- One active pin for each project and page: the partial unique index.
-- Retention: no pruning. A later slice owns it. This file deletes nothing.
-- The number was 329 at build time (R1). At merge on 2026-11-03, main held up
-- to 332 (ALN-2a) and a branch held 333, so the file became 334. Check it
-- again at merge.
-- Pinned by tests/unit/test_project_authorities_r8.py, which finds this file
-- by CONTENT, never by number (R1).
-- ============================================================================

BEGIN;

CREATE TABLE IF NOT EXISTS pm_project_authorities (
    id               UUID NOT NULL DEFAULT gen_random_uuid(),
    organization_id  UUID NOT NULL
                     REFERENCES organization (id) ON DELETE CASCADE
                     DEFAULT current_setting('app.tenant_id', true)::uuid,
    -- The node that the page governs: a space or a project.
    project_id       UUID NOT NULL REFERENCES pm_projects (id) ON DELETE CASCADE,
    -- The page. It was published at the pin.
    page_id          UUID NOT NULL REFERENCES kb_pages (id) ON DELETE CASCADE,
    -- The highest kb_page_versions.version of the page at the pin.
    page_version     INTEGER NOT NULL,
    -- An email, from the session.
    pinned_by        TEXT NOT NULL,
    pinned_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    state            TEXT NOT NULL DEFAULT 'active',
    removed_by       TEXT NULL,
    removed_at       TIMESTAMPTZ NULL,
    PRIMARY KEY (id),
    -- The target of a child's foreign key, so a child stays in its org.
    CONSTRAINT pm_project_authorities_org_id_key UNIQUE (organization_id, id),
    CONSTRAINT pm_project_authorities_version CHECK (page_version >= 1),
    CONSTRAINT pm_project_authorities_pinned_by CHECK (
        pinned_by <> '' AND pinned_by = lower(pinned_by)),
    CONSTRAINT pm_project_authorities_removed_by CHECK (
        removed_by IS NULL
        OR (removed_by <> '' AND removed_by = lower(removed_by))),
    CONSTRAINT pm_project_authorities_state CHECK (
        state IN ('active', 'stale', 'removed')),
    -- A pin is removed if and only if it has a removal time.
    CONSTRAINT pm_project_authorities_removed CHECK (
        (state = 'removed') = (removed_at IS NOT NULL))
);

-- One active pin for each project and page. A removed pin may be pinned again.
CREATE UNIQUE INDEX IF NOT EXISTS uq_pm_project_authorities_active
    ON pm_project_authorities (organization_id, project_id, page_id)
    WHERE state <> 'removed';

-- The pins of one project, and the projects that one page governs.
CREATE INDEX IF NOT EXISTS idx_pm_project_authorities_project
    ON pm_project_authorities (organization_id, project_id, pinned_at DESC);
CREATE INDEX IF NOT EXISTS idx_pm_project_authorities_page
    ON pm_project_authorities (organization_id, page_id);

-- The project and the page of a pin are rows of the pin's own org. For each
-- role, the table owner too. Fence: tests/unit/test_project_authorities_r8.py.
CREATE OR REPLACE FUNCTION pm_project_authorities_same_org() RETURNS trigger
LANGUAGE plpgsql AS $fn$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pm_projects
         WHERE id = NEW.project_id AND organization_id = NEW.organization_id
    ) THEN
        RAISE EXCEPTION 'pm_project_authorities: the project is not a project of this organization'
            USING ERRCODE = 'foreign_key_violation';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM kb_pages
         WHERE id = NEW.page_id AND organization_id = NEW.organization_id
    ) THEN
        RAISE EXCEPTION 'pm_project_authorities: the page is not a page of this organization'
            USING ERRCODE = 'foreign_key_violation';
    END IF;
    RETURN NEW;
END
$fn$;

DROP TRIGGER IF EXISTS pm_project_authorities_same_org ON pm_project_authorities;
CREATE TRIGGER pm_project_authorities_same_org
    BEFORE INSERT OR UPDATE OF organization_id, project_id, page_id
    ON pm_project_authorities
    FOR EACH ROW EXECUTE FUNCTION pm_project_authorities_same_org();

DO $rls$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_class
         WHERE oid = to_regclass('pm_project_authorities')
           AND relrowsecurity
           AND relforcerowsecurity
    ) THEN
        ALTER TABLE pm_project_authorities ENABLE ROW LEVEL SECURITY;
        ALTER TABLE pm_project_authorities FORCE  ROW LEVEL SECURITY;
        DROP POLICY IF EXISTS pm_project_authorities_tenant_isolation ON pm_project_authorities;
        CREATE POLICY pm_project_authorities_tenant_isolation ON pm_project_authorities
            USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
            WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);
    END IF;
END
$rls$;

COMMENT ON TABLE pm_project_authorities IS
    'One pin of a published Knowledge page to a project as its authority (WS-ALN ALN-4). '
    'Only gateway/routes/projects/authorities.py writes it. '
    'project-docs/specs/project_alignment.md §4.1.';

COMMIT;
