-- ============================================================================
-- 319_studio_skills.sql — the skill store of AI Studio (WS-STU F1, Metorite
-- task 70761c91).
--
-- What: two new tables. `studio_skills` holds one skill of one organization:
--       its name, its slug, its owner, its visibility and its state.
--       `studio_skill_versions` holds one stored version of a skill. A version
--       never changes after the INSERT.
--       gateway/studio/skills_store.py is the one writer of both.
-- Why:  A skill tells Mira when and how to use a package (ai_studio.md §3.5).
--       The skill creator of F2 writes these rows, and the tool packs of F3
--       read them. This slice adds the store only: no route and no reader.
-- Depends on: 130_org_access_control.sql (organization).
--
-- Expand only (R6): two new tables, their keys, one trigger function and one
-- trigger. No rename, no drop, and no change to an existing table. Old
-- code names no new object. A NOT NULL with no default stands only on a
-- column that the one writer always fills. No running code writes these
-- tables, so no old INSERT can miss a column.
-- Idempotent per infra/postgres/README.md: CREATE TABLE IF NOT EXISTS, CREATE
-- OR REPLACE FUNCTION, DROP TRIGGER IF EXISTS then CREATE, and an RLS block
-- guarded on relforcerowsecurity.
-- Tenant-scoped (R5): `organization_id` with ENABLE and FORCE row level
-- security and one policy with USING and WITH CHECK, in the shape of 315.
-- `REFERENCES` comes before `DEFAULT`, because the tenancy ratchet in
-- tests/unit/test_tenancy_boundary.py matches the two with no comma between.
-- The org of a new row comes from the DEFAULT, the bound tenant. The writer
-- never names it.
-- A version points at its skill by `(organization_id, skill_id)`, so a version
-- can never point at a skill of another org. A foreign key check skips RLS,
-- and the pair closes that gap.
-- Private by default: `visibility` is 'private' unless a writer sets it. F1
-- has no writer of visibility. J2 owns the share, the group slug and the
-- approval, so no column of them is here.
-- A slug is unique for each owner: UNIQUE (organization_id, owner_email,
-- slug). With one slug for each org, Ben could learn the name of a private
-- skill of Ana from the refusal. Two members may each hold `layoff-plan`. A
-- tool pack id (F3) uses the skill `id`, never the slug. The key also serves
-- the list of one owner, so no second index is here.
-- `current_version` is the newest version of the skill, and NULL before the
-- first one. The store raises it and writes the version in ONE statement, so
-- the row lock of the skill gives two writers two numbers.
-- A version is immutable. The trigger `studio_skill_versions_immutable`
-- refuses each UPDATE, of any column, for every role. A DELETE stays legal,
-- so the cascade of a skill, of an org and of the member purge still works.
-- The body cap: the jsonb text of `body` holds at most 262144 bytes (256 KiB).
-- A skill is instructions, a list of actions and its tests, and a body over
-- that size is not a skill. The store refuses it first, with the same number.
-- The store measures its own JSON text. It refuses a float that Postgres
-- writes longer (1e308 becomes 309 digits), so the two texts have one length.
-- `content_hash` is the SHA-256 of the body as the store wrote it, in 64
-- lower-case hex digits.
-- The member purge (gateway/routes/admin/members.py) deletes the member's
-- skills of the bound org, and their versions by cascade.
-- The number is taken at build time (R1). On 2026-11-01 origin/main held 318.
-- Check it again at merge. Pinned by tests/unit/test_studio_skills_r8.py,
-- which finds this file by CONTENT, never by number (R1).
-- ============================================================================

BEGIN;

CREATE TABLE IF NOT EXISTS studio_skills (
    organization_id   UUID NOT NULL
                      REFERENCES organization (id) ON DELETE CASCADE
                      DEFAULT current_setting('app.tenant_id', true)::uuid,
    id                UUID NOT NULL DEFAULT gen_random_uuid(),
    -- What a member reads.
    name              TEXT NOT NULL,
    -- The key of the skill for its owner, for a URL. A tool pack id uses
    -- `id`, because two members may hold one slug.
    slug              TEXT NOT NULL,
    -- One line for a list. The version body holds the description that
    -- tells Mira when to use the skill.
    description       TEXT NOT NULL DEFAULT '',
    owner_email       TEXT NOT NULL,
    visibility        TEXT NOT NULL DEFAULT 'private',
    state             TEXT NOT NULL DEFAULT 'draft',
    -- The newest version. NULL before the first version.
    current_version   INTEGER NULL,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (id),
    -- One slug for each owner, so a refusal names no skill of a colleague.
    CONSTRAINT studio_skills_slug_key UNIQUE (organization_id, owner_email, slug),
    -- The target of the versions' foreign key, so a version stays in its org.
    CONSTRAINT studio_skills_org_id_key UNIQUE (organization_id, id),
    CONSTRAINT studio_skills_owner_email CHECK (
        owner_email <> '' AND owner_email = lower(owner_email)),
    CONSTRAINT studio_skills_name CHECK (
        btrim(name) <> '' AND char_length(name) <= 120),
    CONSTRAINT studio_skills_slug CHECK (
        char_length(slug) <= 64 AND slug ~ '^[a-z0-9]+(-[a-z0-9]+)*$'),
    -- 1024 characters, as the description of an Agent Skills SKILL.md.
    CONSTRAINT studio_skills_description CHECK (char_length(description) <= 1024),
    CONSTRAINT studio_skills_visibility CHECK (
        visibility IN ('private', 'group', 'org')),
    CONSTRAINT studio_skills_state CHECK (
        state IN ('draft', 'published', 'paused', 'archived')),
    CONSTRAINT studio_skills_current_version CHECK (
        current_version IS NULL OR current_version >= 1)
);

CREATE TABLE IF NOT EXISTS studio_skill_versions (
    organization_id   UUID NOT NULL
                      REFERENCES organization (id) ON DELETE CASCADE
                      DEFAULT current_setting('app.tenant_id', true)::uuid,
    skill_id          UUID NOT NULL,
    version           INTEGER NOT NULL,
    -- description, instructions, actions, cards, knowledge and tests
    -- (ai_studio.md §3.5). The store checks a light shape only.
    body              JSONB NOT NULL,
    content_hash      TEXT NOT NULL,
    created_by        TEXT NOT NULL,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (organization_id, skill_id, version),
    CONSTRAINT studio_skill_versions_skill_fk FOREIGN KEY (organization_id, skill_id)
        REFERENCES studio_skills (organization_id, id) ON DELETE CASCADE,
    CONSTRAINT studio_skill_versions_version CHECK (version >= 1),
    CONSTRAINT studio_skill_versions_body_object CHECK (jsonb_typeof(body) = 'object'),
    -- 262144 bytes (256 KiB) of JSON text. The store holds the same number.
    CONSTRAINT studio_skill_versions_body_size CHECK (octet_length(body::text) <= 262144),
    CONSTRAINT studio_skill_versions_content_hash CHECK (content_hash ~ '^[0-9a-f]{64}$'),
    CONSTRAINT studio_skill_versions_created_by CHECK (
        created_by <> '' AND created_by = lower(created_by))
);

-- A stored version never changes. The trigger refuses each UPDATE, for each
-- role, the table owner too. Fence: tests/unit/test_studio_skills_r8.py.
CREATE OR REPLACE FUNCTION studio_skill_versions_refuse_update() RETURNS trigger
LANGUAGE plpgsql AS $fn$
BEGIN
    RAISE EXCEPTION 'studio_skill_versions: a stored version cannot change'
        USING ERRCODE = 'check_violation';
END
$fn$;

DROP TRIGGER IF EXISTS studio_skill_versions_immutable ON studio_skill_versions;
CREATE TRIGGER studio_skill_versions_immutable
    BEFORE UPDATE ON studio_skill_versions
    FOR EACH ROW EXECUTE FUNCTION studio_skill_versions_refuse_update();

DO $rls$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_class
         WHERE oid = to_regclass('studio_skills')
           AND relrowsecurity
           AND relforcerowsecurity
    ) THEN
        ALTER TABLE studio_skills ENABLE ROW LEVEL SECURITY;
        ALTER TABLE studio_skills FORCE  ROW LEVEL SECURITY;
        DROP POLICY IF EXISTS studio_skills_tenant_isolation ON studio_skills;
        CREATE POLICY studio_skills_tenant_isolation ON studio_skills
            USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
            WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_class
         WHERE oid = to_regclass('studio_skill_versions')
           AND relrowsecurity
           AND relforcerowsecurity
    ) THEN
        ALTER TABLE studio_skill_versions ENABLE ROW LEVEL SECURITY;
        ALTER TABLE studio_skill_versions FORCE  ROW LEVEL SECURITY;
        DROP POLICY IF EXISTS studio_skill_versions_tenant_isolation ON studio_skill_versions;
        CREATE POLICY studio_skill_versions_tenant_isolation ON studio_skill_versions
            USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
            WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);
    END IF;
END
$rls$;

COMMENT ON TABLE studio_skills IS
    'One skill of AI Studio: name, slug, owner, visibility and state (WS-STU F1). '
    'Only gateway/studio/skills_store.py writes it. project-docs/specs/ai_studio.md §3.5.';
COMMENT ON TABLE studio_skill_versions IS
    'One immutable version of a Studio skill (WS-STU F1). A trigger refuses each UPDATE. '
    'Only gateway/studio/skills_store.py writes it. project-docs/specs/ai_studio.md §3.5.';

COMMIT;
