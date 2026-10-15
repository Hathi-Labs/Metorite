-- ============================================================================
-- 272_knowledge_pages.sql — the spaces and pages of the Knowledge app, and the
-- seed of `knowledge:manage` onto `admin`.
--
-- What: five new tables and one capability seed.
--       `kb_spaces`        — the unit of visibility and of ownership. Kinds:
--                            company, team, me, agent. One `company` space per
--                            organization and one `me` space per member, each
--                            made race-free by a partial unique index.
--       `kb_space_grants`  — who reads, writes or manages a space, in the D12
--                            subject grammar of Projects: `org`, `group:<slug>`
--                            or a bare email. The validator in code is
--                            acb_auth.permissions.normalize_grant_subject.
--       `kb_pages`         — one Markdown document with its state (draft,
--                            published, archived), its author and reviewers.
--       `kb_page_versions` — append-only. One row for each write, with the
--                            reason and the channel (`via`).
--       `kb_links`         — a typed reference from a page to another thing.
--       Seed: `knowledge:manage` for the `admin` role of every organization,
--       and in provision_org_roles, so a new organization has it too.
-- Why:  WS-61 KB-1, spec project-docs/specs/knowledge_base.md §4.1, §4.2,
--       §5.1, §5.2, §5.5 and §12.
-- Depends on: 130_org_access_control.sql (organization, org_role,
--       org_role_permission), 200_provision_org_roles_tenancy.sql (the
--       function body that part 3 rewrites), 270_knowledge_feature.sql.
--
-- Not here, on purpose: `kb_page_files` (KB-2a), `kb_chunks` (KB-3),
-- `kb_gaps` (KB-4) and the source tables (KB-7).
--
-- Expand only (R6): five new tables, their indexes, one INSERT of rows that
-- ON CONFLICT DO NOTHING skips, and one function body with one more array
-- element. No rename, no drop, and no change to an existing column.
-- Idempotent per infra/postgres/README.md: CREATE TABLE IF NOT EXISTS,
-- CREATE INDEX IF NOT EXISTS, an RLS block guarded on relforcerowsecurity,
-- and a function rewrite that does nothing when the element is already there.
-- Tenant-scoped (R5): `organization_id` with ENABLE and FORCE row level
-- security and one policy with USING and WITH CHECK, in the shape of
-- 268_artifacts.sql. `REFERENCES` comes before `DEFAULT`, because the tenancy
-- ratchet in tests/unit/test_tenancy_boundary.py matches the two with no
-- comma between. The foreign key is inline, never `ADD CONSTRAINT
-- <table>_org_fk`, because generated phase 3 adds that name.
-- The number is taken at build time (R1). On 2026-10-15 main held 270, and
-- the branch ws59-tell-me held 271. Check it again at merge. The tests find
-- this file by CONTENT, never by number.
-- ============================================================================

BEGIN;

-- ── 1. The tables ───────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS kb_spaces (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id  UUID NOT NULL
                     REFERENCES organization (id) ON DELETE CASCADE
                     DEFAULT current_setting('app.tenant_id', true)::uuid,
    slug             TEXT NOT NULL
                     CHECK (slug ~ '^[a-z0-9][a-z0-9-]{0,62}$'),
    name             TEXT NOT NULL
                     CHECK (char_length(name) BETWEEN 1 AND 120),
    purpose          TEXT NOT NULL DEFAULT ''
                     CHECK (char_length(purpose) <= 300),
    icon             TEXT NOT NULL DEFAULT ''
                     CHECK (char_length(icon) <= 40),
    kind             TEXT NOT NULL
                     CHECK (kind IN ('company', 'team', 'me', 'agent')),
    -- The member who manages the space. NULL for the `company` space, which
    -- the code makes lazily: the member who opens the app first must not
    -- become its owner. A `me` space always names its member.
    owner_email      TEXT NULL,
    archived_at      TIMESTAMPTZ NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (kind <> 'me' OR owner_email IS NOT NULL)
);

-- One space for each slug in an organization, except the `me` spaces, which
-- all carry the slug `me`. One `me` space for each member. The code inserts
-- with ON CONFLICT DO NOTHING on these two keys and then reads again, so two
-- first writes at the same time make one row.
CREATE UNIQUE INDEX IF NOT EXISTS kb_spaces_slug_key
    ON kb_spaces (organization_id, slug) WHERE kind <> 'me';
CREATE UNIQUE INDEX IF NOT EXISTS kb_spaces_me_key
    ON kb_spaces (organization_id, owner_email) WHERE kind = 'me';

CREATE TABLE IF NOT EXISTS kb_space_grants (
    organization_id  UUID NOT NULL
                     REFERENCES organization (id) ON DELETE CASCADE
                     DEFAULT current_setting('app.tenant_id', true)::uuid,
    space_id         UUID NOT NULL REFERENCES kb_spaces (id) ON DELETE CASCADE,
    -- 'org' | 'group:<slug>' | '<email>', stored folded. The validator in
    -- code is the fence, and this CHECK only refuses a shape no validator
    -- would pass.
    subject          TEXT NOT NULL
                     CHECK (subject = lower(subject)
                            AND (subject = 'org'
                                 OR subject ~ '^group:.+$'
                                 OR subject ~ '^[^@[:space:]]+@[^@[:space:]]+$')),
    level            TEXT NOT NULL CHECK (level IN ('read', 'write', 'manage')),
    created_by       TEXT NOT NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (organization_id, space_id, subject)
);

CREATE INDEX IF NOT EXISTS idx_kb_space_grants_space
    ON kb_space_grants (space_id);

CREATE TABLE IF NOT EXISTS kb_pages (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id  UUID NOT NULL
                     REFERENCES organization (id) ON DELETE CASCADE
                     DEFAULT current_setting('app.tenant_id', true)::uuid,
    -- No CASCADE: a space with pages is archived, never deleted under them.
    space_id         UUID NOT NULL REFERENCES kb_spaces (id),
    parent_id        UUID NULL REFERENCES kb_pages (id) ON DELETE SET NULL,
    position         INTEGER NOT NULL DEFAULT 0,
    title            TEXT NOT NULL
                     CHECK (char_length(title) BETWEEN 1 AND 300),
    -- Markdown is the text of record (§4.2).
    body_md          TEXT NOT NULL DEFAULT ''
                     CHECK (char_length(body_md) <= 1000000),
    state            TEXT NOT NULL DEFAULT 'draft'
                     CHECK (state IN ('draft', 'published', 'archived')),
    author_email     TEXT NOT NULL CHECK (author_email <> ''),
    reviewers        TEXT[] NOT NULL DEFAULT '{}',
    review_at        TIMESTAMPTZ NULL,
    -- The provider document a page came from (KB-7). NULL for a written page.
    source_ref       TEXT NULL,
    origin           JSONB NULL,
    published_at     TIMESTAMPTZ NULL,
    archived_at      TIMESTAMPTZ NULL,
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (parent_id IS NULL OR parent_id <> id)
);

CREATE INDEX IF NOT EXISTS idx_kb_pages_tree
    ON kb_pages (organization_id, space_id, parent_id, position);
CREATE INDEX IF NOT EXISTS idx_kb_pages_author
    ON kb_pages (organization_id, author_email);
CREATE INDEX IF NOT EXISTS idx_kb_pages_reviewers
    ON kb_pages USING GIN (reviewers);

CREATE TABLE IF NOT EXISTS kb_page_versions (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id  UUID NOT NULL
                     REFERENCES organization (id) ON DELETE CASCADE
                     DEFAULT current_setting('app.tenant_id', true)::uuid,
    page_id          UUID NOT NULL REFERENCES kb_pages (id) ON DELETE CASCADE,
    version          INTEGER NOT NULL CHECK (version >= 1),
    body_md          TEXT NOT NULL,
    title            TEXT NOT NULL,
    author_email     TEXT NOT NULL,
    -- §4.2 names six reasons. KB-1 adds `create`, `move` and `archive`,
    -- because each write appends a version and those three writes are not
    -- an edit.
    reason           TEXT NOT NULL
                     CHECK (reason IN ('create', 'edit', 'publish', 'move',
                                       'archive', 'revert', 'agent_proposal',
                                       'accept', 'import')),
    -- §5.4: through what the write came.
    via              TEXT NOT NULL DEFAULT 'web'
                     CHECK (via IN ('web', 'chat', 'mcp', 'sync', 'artifact')),
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (page_id, version)
);

CREATE TABLE IF NOT EXISTS kb_links (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id  UUID NOT NULL
                     REFERENCES organization (id) ON DELETE CASCADE
                     DEFAULT current_setting('app.tenant_id', true)::uuid,
    page_id          UUID NOT NULL REFERENCES kb_pages (id) ON DELETE CASCADE,
    target_kind      TEXT NOT NULL
                     CHECK (target_kind IN ('page', 'task', 'project',
                                            'crm_record', 'email_thread',
                                            'meeting', 'artifact', 'url')),
    -- A UUID (an artifact's too), or a URL. A link is a reference and never
    -- a grant (§5.5).
    target_id        TEXT NOT NULL,
    label            TEXT NULL,
    created_by       TEXT NOT NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (organization_id, page_id, target_kind, target_id)
);

-- "Knowledge about this" (§10.2) is one scan on this index.
CREATE INDEX IF NOT EXISTS idx_kb_links_target
    ON kb_links (organization_id, target_kind, target_id);

DO $rls$
DECLARE
    t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY['kb_spaces', 'kb_space_grants', 'kb_pages',
                             'kb_page_versions', 'kb_links'] LOOP
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

COMMENT ON TABLE kb_spaces IS
    'WS-61 KB-1: a Knowledge space, the unit of visibility and ownership. '
    'knowledge_base.md §4.1.';
COMMENT ON TABLE kb_space_grants IS
    'WS-61 KB-1: who reads, writes or manages a space, in the D12 subject '
    'grammar (org, group:<slug>, email). knowledge_base.md §5.1.';
COMMENT ON TABLE kb_pages IS
    'WS-61 KB-1: one Knowledge page. Every read goes through '
    'gateway.routes.knowledge.core.visible_pages_clause. knowledge_base.md §4.2.';
COMMENT ON TABLE kb_page_versions IS
    'WS-61 KB-1: append-only history of a page, one row per write, with the '
    'reason and the channel. knowledge_base.md §4.2 and §5.4.';
COMMENT ON TABLE kb_links IS
    'WS-61 KB-1: a typed reference from a page to another thing. A link is '
    'never a grant. knowledge_base.md §5.5.';


-- ── 2. `knowledge:manage` for `admin`, in every organization that exists ────
--
-- §5.2: the owner holds it through `*`, and `admin` holds it by seed.
-- `manager` and `member` do not. It reads published pages only, and it never
-- opens a `me` space or a draft that is not its own (the code holds that).
--
-- TWO ARMS, guarded on whether the tenancy column exists, in the shape of
-- 207 part 1 and for its reason: `org_role_permission.organization_id` comes
-- from the generated phase, which the ladder does not replay, and the phase
-- makes it NOT NULL. The tenant comes from the ROLE being granted, never
-- from a GUC, because this statement crosses every organization by design.
DO $grant$
DECLARE
    has_org_column boolean;
BEGIN
    SELECT EXISTS (
        SELECT 1 FROM information_schema.columns
         WHERE table_name = 'org_role_permission'
           AND column_name = 'organization_id'
    ) INTO has_org_column;

    IF has_org_column THEN
        INSERT INTO org_role_permission (role_id, permission, organization_id)
        SELECT r.id, 'knowledge:manage', r.organization_id
          FROM org_role r
         WHERE r.slug = 'admin'
        ON CONFLICT DO NOTHING;
    ELSE
        INSERT INTO org_role_permission (role_id, permission)
        SELECT r.id, 'knowledge:manage'
          FROM org_role r
         WHERE r.slug = 'admin'
        ON CONFLICT DO NOTHING;
    END IF;
END
$grant$;


-- ── 3. And in every organization made from now on ───────────────────────────
--
-- provision_org_roles carries its own copy of each role's grants. A seed of
-- today's organizations alone would leave every new one without the
-- capability. The function is 200's to own, so this file does not restate
-- it: it reads the live definition, adds one element to the admin array, and
-- runs the result, as 207 part 2 does.
--
-- The anchor is the element `'admin:audit:read',`. Only the admin array of
-- 200 holds it. The billing capability is NOT a safe anchor, because the
-- `agent_service` array holds it too. The block counts the anchor and raises
-- unless it occurs exactly once, so a changed function body stops the deploy
-- and does not grant the capability to the wrong role.
DO $seed$
DECLARE
    src     text;
    out     text;
    anchor  constant text := '''admin:audit:read'',';
    hits    integer;
BEGIN
    -- pg_get_functiondef, NOT prosrc: prosrc is the body alone and is not a
    -- runnable statement.
    SELECT pg_get_functiondef(p.oid) INTO src
      FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
     WHERE p.proname = 'provision_org_roles' AND n.nspname = current_schema()
     LIMIT 1;

    IF src IS NULL THEN
        RAISE NOTICE '272: provision_org_roles absent, nothing to rewrite';
        RETURN;
    END IF;

    IF position('''knowledge:manage''' in src) > 0 THEN
        RAISE NOTICE '272: provision_org_roles already grants knowledge:manage';
        RETURN;
    END IF;

    hits := (length(src) - length(replace(src, anchor, ''))) / length(anchor);
    IF hits <> 1 THEN
        RAISE EXCEPTION
            '272: provision_org_roles holds % copies of the admin anchor, not 1. '
            '200 changed shape. Update 272 to match before deploying, or new '
            'organizations get knowledge:manage on the wrong role or on none.',
            hits;
    END IF;

    out := replace(src, anchor, anchor || ' ''knowledge:manage'',');
    EXECUTE out;
    RAISE NOTICE '272: provision_org_roles rewritten to grant knowledge:manage to admin';
END
$seed$;

COMMIT;
