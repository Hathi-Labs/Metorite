-- ============================================================================
-- 379_pm_templates.sql — the template store of Projects (WS-39 TPL-1a,
-- Metorite task 18, Linear parity P7).
--
-- What: two new tables.
--       * `pm_templates` holds one template of one organization: a kind
--         (`project` or `task`), a name, a description, a work style, and a
--         versioned JSON payload. TPL-1a writes `kind = 'project'` only. The
--         payload is a snapshot of one project: its statuses, task types,
--         custom fields, tags and starter tasks, as a tree with day offsets.
--         It holds no id of the source, no person, no comment and no
--         activity. `source_project_id` names the project that it came from,
--         and a delete of that project sets it to NULL.
--       * `pm_template_applies` holds one apply of a template: the member,
--         the idempotency key, the hash of the request and the project that
--         the apply made. A UNIQUE index on (organization_id, applied_by,
--         idempotency_key) makes a replay return the first project.
--       gateway/routes/projects/templates.py is the one writer of both
--       tables.
-- Why:  The owner asked for named, reusable project templates that carry
--       the tags, the fields and the statuses (2026-09-24, task 18).
--       project-docs/specs/project_management_app.md §14.2 rows TPL-1a and
--       TPL-1b, and §14.8, hold the design.
-- Depends on: 130_org_access_control.sql (organization) and 146
--       (pm_projects).
--
-- Expand only (R6): two new tables, their keys and indexes, one trigger
-- function and two triggers. No rename, no drop and no new column on an old
-- table. Old code names no new object.
-- Idempotent per infra/postgres/README.md: CREATE TABLE IF NOT EXISTS, CREATE
-- INDEX IF NOT EXISTS, CREATE OR REPLACE FUNCTION, DROP TRIGGER IF EXISTS then
-- CREATE, and an RLS block guarded on relforcerowsecurity.
--
-- Tenant-scoped (R5): each table has `organization_id`, ENABLE and FORCE row
-- level security, and one policy with USING and WITH CHECK, in the shape of
-- 339 and the cycle store. `REFERENCES` comes before `DEFAULT`, because the
-- tenancy ratchet in tests/unit/test_tenancy_boundary.py matches the two with
-- no comma between. The org of a new row comes from the DEFAULT, the bound
-- tenant. The writer never names it.
-- Same org. `pm_projects` has no UNIQUE (organization_id, id), and this file
-- adds none (an index build on a hot table). So the trigger
-- `pm_templates_same_org` refuses a source project of another org, and
-- `pm_template_applies_same_org` refuses a template or a project of another
-- org, for each role, as 339 does. Under RLS the row of another org is not
-- there, so the check fails closed. A NULL reference passes, so the SET NULL
-- of a delete still works.
--
-- The payload is data, never code. Three CHECKs hold its shape: a JSON
-- object, `schema_version` 1, and at most 1048576 bytes of jsonb text. The
-- route checks the rest (the counts, the depth, the lengths) on each save
-- and on each apply.
-- One name for each kind in each org: the unique index
-- `uq_pm_templates_name` on (organization_id, lower(name), kind). The save
-- route answers 409 first. The index holds under a race.
--
-- ON DELETE.
--   * `pm_templates.source_project_id` SET NULL: a deleted project leaves its
--     templates, because a template is a copy and not a link.
--   * `pm_template_applies.template_id` and `.project_id` SET NULL: the
--     record of an apply stays. A replay of a key whose project is gone gets
--     409, and the client sends a new key.
--   * Each table CASCADEs from `organization`.
--
-- Version. A template has no edit route in TPL-1a, so no If-Match.
-- No score for any person: no column or index ranks a member.
-- Retention: no pruning. This file deletes nothing.
--
-- The number is taken at build time (R1). It was 368 at build on 2026-11-08.
-- On 2026-11-09 origin/main held 366, and open branches held 367 and 369 to
-- 373, so it became 374. On 2026-11-09 SG-1 merged 378 to main, and open
-- branches held 375, 380, 382 and 383, so it became 379. Check it again
-- at merge.
-- Pinned by tests/unit/test_pm_templates_r8.py, which finds this file by
-- CONTENT, never by number (R1).
-- ============================================================================

BEGIN;

SET LOCAL lock_timeout = '5s';

-- ── The template ────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS pm_templates (
    id                 UUID NOT NULL DEFAULT gen_random_uuid(),
    organization_id    UUID NOT NULL
                       REFERENCES organization (id) ON DELETE CASCADE
                       DEFAULT current_setting('app.tenant_id', true)::uuid,
    -- `project` today. `task` is legal for a later slice.
    kind               TEXT NOT NULL DEFAULT 'project',
    name               TEXT NOT NULL,
    description        TEXT NULL,
    -- The work style (WM-0), when the source had one. NULL in TPL-1a.
    work_style         TEXT NULL,
    -- The versioned snapshot. See the header.
    payload            JSONB NOT NULL,
    -- The project that the save read. A copy, not a link.
    source_project_id  UUID NULL REFERENCES pm_projects (id) ON DELETE SET NULL,
    -- An email, from the session.
    created_by         TEXT NOT NULL,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (id),
    CONSTRAINT pm_templates_org_id_key UNIQUE (organization_id, id),
    CONSTRAINT pm_templates_kind CHECK (kind IN ('project', 'task')),
    CONSTRAINT pm_templates_name CHECK (
        btrim(name) <> '' AND char_length(name) <= 120),
    CONSTRAINT pm_templates_description CHECK (
        description IS NULL OR char_length(description) <= 2000),
    CONSTRAINT pm_templates_work_style CHECK (
        work_style IS NULL OR char_length(work_style) BETWEEN 1 AND 40),
    CONSTRAINT pm_templates_payload_object CHECK (jsonb_typeof(payload) = 'object'),
    -- IS NOT DISTINCT FROM, because a CHECK passes on NULL, and a payload
    -- with no `schema_version` must fail.
    CONSTRAINT pm_templates_payload_version CHECK (
        payload -> 'schema_version' IS NOT DISTINCT FROM '1'::jsonb),
    CONSTRAINT pm_templates_payload_size CHECK (
        octet_length(payload::text) <= 1048576),
    CONSTRAINT pm_templates_created_by CHECK (created_by = lower(created_by))
);

-- One name for each kind in each org, in any case.
CREATE UNIQUE INDEX IF NOT EXISTS uq_pm_templates_name
    ON pm_templates (organization_id, lower(name), kind);

-- The SET NULL of a project delete.
CREATE INDEX IF NOT EXISTS pm_templates_source_idx
    ON pm_templates (source_project_id)
    WHERE source_project_id IS NOT NULL;

-- ── The apply record ────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS pm_template_applies (
    id                 UUID NOT NULL DEFAULT gen_random_uuid(),
    organization_id    UUID NOT NULL
                       REFERENCES organization (id) ON DELETE CASCADE
                       DEFAULT current_setting('app.tenant_id', true)::uuid,
    template_id        UUID NULL REFERENCES pm_templates (id) ON DELETE SET NULL,
    -- NULL until the project of the apply exists, in the same transaction.
    project_id         UUID NULL REFERENCES pm_projects (id) ON DELETE SET NULL,
    -- An email, from the session.
    applied_by         TEXT NOT NULL,
    idempotency_key    TEXT NOT NULL,
    -- The SHA-256 of the request, so a key sent with another request is 409.
    request_hash       TEXT NOT NULL,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (id),
    CONSTRAINT pm_template_applies_key CHECK (
        char_length(idempotency_key) BETWEEN 1 AND 128),
    CONSTRAINT pm_template_applies_hash CHECK (request_hash ~ '^[0-9a-f]{64}$'),
    CONSTRAINT pm_template_applies_by CHECK (applied_by = lower(applied_by))
);

-- One apply for each key of each member.
CREATE UNIQUE INDEX IF NOT EXISTS uq_pm_template_applies_key
    ON pm_template_applies (organization_id, applied_by, idempotency_key);

-- The SET NULL of a project delete and of a template delete.
CREATE INDEX IF NOT EXISTS pm_template_applies_project_idx
    ON pm_template_applies (project_id)
    WHERE project_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS pm_template_applies_template_idx
    ON pm_template_applies (template_id)
    WHERE template_id IS NOT NULL;

-- ── Same org ────────────────────────────────────────────────────────────────

CREATE OR REPLACE FUNCTION pm_templates_same_org() RETURNS trigger
LANGUAGE plpgsql AS $fn$
BEGIN
    IF NEW.source_project_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM pm_projects
         WHERE id = NEW.source_project_id AND organization_id = NEW.organization_id
    ) THEN
        RAISE EXCEPTION 'pm_templates: the source is not a project of this organization'
            USING ERRCODE = 'foreign_key_violation';
    END IF;
    RETURN NEW;
END
$fn$;

DROP TRIGGER IF EXISTS pm_templates_same_org ON pm_templates;
CREATE TRIGGER pm_templates_same_org
    BEFORE INSERT OR UPDATE OF organization_id, source_project_id
    ON pm_templates
    FOR EACH ROW EXECUTE FUNCTION pm_templates_same_org();

CREATE OR REPLACE FUNCTION pm_template_applies_same_org() RETURNS trigger
LANGUAGE plpgsql AS $fn$
BEGIN
    IF NEW.template_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM pm_templates
         WHERE id = NEW.template_id AND organization_id = NEW.organization_id
    ) THEN
        RAISE EXCEPTION 'pm_template_applies: the template is not a template of this organization'
            USING ERRCODE = 'foreign_key_violation';
    END IF;
    IF NEW.project_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM pm_projects
         WHERE id = NEW.project_id AND organization_id = NEW.organization_id
    ) THEN
        RAISE EXCEPTION 'pm_template_applies: the project is not a project of this organization'
            USING ERRCODE = 'foreign_key_violation';
    END IF;
    RETURN NEW;
END
$fn$;

DROP TRIGGER IF EXISTS pm_template_applies_same_org ON pm_template_applies;
CREATE TRIGGER pm_template_applies_same_org
    BEFORE INSERT OR UPDATE OF organization_id, template_id, project_id
    ON pm_template_applies
    FOR EACH ROW EXECUTE FUNCTION pm_template_applies_same_org();

-- ── Row level security ──────────────────────────────────────────────────────

DO $rls$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_class
         WHERE oid = to_regclass('pm_templates')
           AND relrowsecurity
           AND relforcerowsecurity
    ) THEN
        ALTER TABLE pm_templates ENABLE ROW LEVEL SECURITY;
        ALTER TABLE pm_templates FORCE  ROW LEVEL SECURITY;
        DROP POLICY IF EXISTS pm_templates_tenant_isolation ON pm_templates;
        CREATE POLICY pm_templates_tenant_isolation ON pm_templates
            USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
            WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_class
         WHERE oid = to_regclass('pm_template_applies')
           AND relrowsecurity
           AND relforcerowsecurity
    ) THEN
        ALTER TABLE pm_template_applies ENABLE ROW LEVEL SECURITY;
        ALTER TABLE pm_template_applies FORCE  ROW LEVEL SECURITY;
        DROP POLICY IF EXISTS pm_template_applies_tenant_isolation ON pm_template_applies;
        CREATE POLICY pm_template_applies_tenant_isolation ON pm_template_applies
            USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
            WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);
    END IF;
END
$rls$;

COMMENT ON TABLE pm_templates IS
    'One template of one organization (WS-39 TPL-1a). '
    'Only gateway/routes/projects/templates.py writes it. '
    'project-docs/specs/project_management_app.md §14.8.';
COMMENT ON TABLE pm_template_applies IS
    'One apply of a template, keyed by the member and the idempotency key. TPL-1a.';

COMMIT;
