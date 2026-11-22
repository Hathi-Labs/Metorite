-- ============================================================================
-- 340_pm_agent_guidance.sql — the org's written rules for agents (WS-MCP
-- SW-31a, Metorite task 1048, D140.6, D160).
--
-- What: one new table. `pm_agent_guidance` holds the guidance blocks of an
--       org, a space and a project. Each save adds a row, so the history of
--       a scope is its rows. The gateway route `agent_guidance.py` is the one
--       writer, and `get_task_context` and `claim_task` of the MCP edge read
--       the blocks that apply to a task.
-- Why:  An agent that works a task needs the org's rules for that work. The
--       text is the org's own, so it is untrusted input to the agent.
--       agent_coordination.md SW-31 plans the table. project_alignment.md
--       §4.2 (ALN-5) extends it with rules, so this table has the shape of
--       §4.2 from the start: the rule columns are nullable or defaulted, and
--       SW-31a writes `kind = 'guidance'` only.
-- Depends on: 130_org_access_control.sql (organization), 146 (pm_projects).
--
-- Expand only (R6): one new table, three indexes, one trigger function and one
-- trigger. No rename, no drop, no change to an existing table. No running
-- code reads or writes the table before this release.
-- Idempotent per infra/postgres/README.md: CREATE TABLE IF NOT EXISTS, CREATE
-- INDEX IF NOT EXISTS, CREATE OR REPLACE FUNCTION, DROP TRIGGER IF EXISTS then
-- CREATE, and an RLS block guarded on relforcerowsecurity.
-- Tenant-scoped (R5): `organization_id` with ENABLE and FORCE row level
-- security and one policy with USING and WITH CHECK. `REFERENCES` comes
-- before `DEFAULT`, because the tenancy ratchet in
-- tests/unit/test_tenancy_boundary.py matches the two with no comma between.
-- The org of a new row comes from the DEFAULT, the bound tenant. The writer
-- never names it. No GRANT: the roles of 130 hold the default privileges.
--
-- Scope: `project_id` IS NULL means the org level. Else it names a
-- `pm_projects` row: a space (a root row, parent_project_id IS NULL) or a
-- project below it. ON DELETE CASCADE removes the guidance with its project.
-- Centers are not involved (D49).
--
-- Version: starts at 1 for a scope, and the writer adds one for each save.
-- The unique index keeps one row for each (scope, kind, rule_key, version).
-- Postgres treats NULL as distinct in a unique index, and `project_id` and
-- `rule_key` are NULL for the org level and for guidance. So the index keys
-- on COALESCE(x, the nil UUID). It does not use NULLS NOT DISTINCT, which
-- needs Postgres 15, and the expression form works on each version. Two
-- concurrent saves of one scope end as one row and one unique violation, and
-- the writer retries with the next version.
--
-- Append only: the trigger `pm_agent_guidance_guard` refuses an UPDATE of a
-- `guidance` row, for each role. A DELETE stays legal, so the cascade of a
-- project or an org works. A `rule` row (ALN-5) may change `state`, and that
-- slice owns the trigger for it.
--
-- The caps (8,000 characters for a block of guidance) are in the route, not
-- in a CHECK: the only CHECK of this table is on `kind` (project_alignment.md
-- §4.2 keeps the rule body at 300 characters, and a CHECK on `body` would
-- need to know the kind).
--
-- The rule columns (`rule_key`, `source_page_id`, `source_page_version`,
-- `quote`, `level`, `state`, `proposed_by`) are for ALN-5. SW-31a writes
-- NULL in each. A CHECK on `level` and `state` waits for that slice.
--
-- The number (R1): 335 at build time. On 2026-11-03 origin/main held 330
-- to 332, and branches held 333 and 334. Renumbered to 340 at the merge of
-- 2026-11-04 (fix round 1): main held 337 (PZ-33b) and 338 (Studio C7b), and
-- premerge refuses a number at or below the highest on main. Open branches
-- held 336 (CRM change history) and 339 (ALN-4). Check it again at merge.
-- Pinned by tests/unit/test_agent_guidance_r8.py, which finds this file by
-- CONTENT, never by number (R1).
-- ============================================================================

BEGIN;

CREATE TABLE IF NOT EXISTS pm_agent_guidance (
    id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id      UUID NOT NULL
                         REFERENCES organization (id) ON DELETE CASCADE
                         DEFAULT current_setting('app.tenant_id', true)::uuid,
    -- NULL = the org level. Else a space (root project) or a project.
    project_id           UUID NULL REFERENCES pm_projects (id) ON DELETE CASCADE,
    -- `guidance` today. ALN-5 adds `rule`.
    kind                 TEXT NOT NULL DEFAULT 'guidance'
                         CHECK (kind IN ('guidance', 'rule')),
    -- NULL for guidance. A UUID that stays the same across the versions of
    -- one rule (ALN-5).
    rule_key             UUID NULL,
    version              INTEGER NOT NULL DEFAULT 1,
    body                 TEXT NOT NULL,
    -- The member who saved this version. From the session, never a body.
    written_by           TEXT NOT NULL,
    written_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- ALN-5 columns. NULL in each row of SW-31a.
    source_page_id       UUID NULL,
    source_page_version  INTEGER NULL,
    quote                TEXT NULL,
    level                TEXT NULL,
    state                TEXT NULL,
    proposed_by          TEXT NULL
);

-- One row for each version of a scope. NULL-safe: see the header.
CREATE UNIQUE INDEX IF NOT EXISTS pm_agent_guidance_version
    ON pm_agent_guidance (
        organization_id,
        COALESCE(project_id, '00000000-0000-0000-0000-000000000000'::uuid),
        kind,
        COALESCE(rule_key, '00000000-0000-0000-0000-000000000000'::uuid),
        version
    );

-- The newest version of each scope in a set of scopes.
CREATE INDEX IF NOT EXISTS pm_agent_guidance_scope
    ON pm_agent_guidance (organization_id, project_id, kind, version DESC);

-- The cascade of a project reads this column.
CREATE INDEX IF NOT EXISTS pm_agent_guidance_project
    ON pm_agent_guidance (project_id)
    WHERE project_id IS NOT NULL;

-- A saved block of guidance never changes: the next save is a new row.
-- Fence: tests/unit/test_agent_guidance_r8.py.
CREATE OR REPLACE FUNCTION pm_agent_guidance_guard_update() RETURNS trigger
LANGUAGE plpgsql AS $fn$
BEGIN
    IF OLD.kind = 'guidance' THEN
        RAISE EXCEPTION 'pm_agent_guidance: a saved block of guidance cannot change, save a new version'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END
$fn$;

DROP TRIGGER IF EXISTS pm_agent_guidance_guard ON pm_agent_guidance;
CREATE TRIGGER pm_agent_guidance_guard
    BEFORE UPDATE ON pm_agent_guidance
    FOR EACH ROW EXECUTE FUNCTION pm_agent_guidance_guard_update();

DO $rls$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_class
         WHERE oid = to_regclass('pm_agent_guidance')
           AND relrowsecurity
           AND relforcerowsecurity
    ) THEN
        ALTER TABLE pm_agent_guidance ENABLE ROW LEVEL SECURITY;
        ALTER TABLE pm_agent_guidance FORCE  ROW LEVEL SECURITY;
        DROP POLICY IF EXISTS pm_agent_guidance_tenant_isolation ON pm_agent_guidance;
        CREATE POLICY pm_agent_guidance_tenant_isolation ON pm_agent_guidance
            USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
            WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);
    END IF;
END
$rls$;

COMMENT ON TABLE pm_agent_guidance IS
    'The org''s written rules for agents: one row for each saved version of a scope '
    '(org, space or project). Append only. WS-MCP SW-31a, agent_coordination.md SW-31, '
    'project_alignment.md §4.2.';

COMMIT;
