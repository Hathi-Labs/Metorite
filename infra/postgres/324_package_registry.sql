-- ============================================================================
-- 324_package_registry.sql — the run-time registry of installed packages
-- (WS-PKG PKG-4, Metorite task bb82b1f9).
--
-- What: two new tables and one new key on `apps`. `package_install` holds one
--       install of one Studio app at one scope. `package_contribution` holds
--       one claim of one install at one contribution point (package_host.md
--       §4). `apps` gains UNIQUE (organization_id, id), the target of the
--       composite foreign key of an install.
-- Why:  package_host.md §5.1. The resolve of PKG-5 reads these rows. This
--       slice adds the tables only: no writer, no reader, no route, no flag.
--       A system package is a file (§3.3), so it has no row here.
-- Depends on: 114_custom_apps.sql (apps), 130_org_access_control.sql
--             (organization).
--
-- Expand only (R6): two new tables, their keys and one index. On `apps`, one
-- nullable column (only where it is missing) and one UNIQUE key. No rename,
-- no drop, and no change to a row. Old code names no new object.
-- Idempotent per infra/postgres/README.md: the column, the tables and the
-- index each take IF NOT EXISTS. A DO block guards the key on pg_constraint,
-- and the RLS block is guarded on relforcerowsecurity.
--
-- `apps.organization_id` on the numbered ladder. The generated phase
-- `generated/01_add_columns.sql` declares that column, and the ladder never
-- replays `generated/` (H-104). So a fresh ladder database has no column for
-- the key or the foreign key, and this file adds it first, as 209 and 223 do
-- for `people` and `email_accounts`. It has the type and the default of the
-- generated phase, and the ON DELETE CASCADE that test_org_purge_tenant.py
-- requires. Production has the column from the generated phases, so ADD
-- COLUMN IF NOT EXISTS skips the whole clause there, the inline foreign key
-- too, and no second foreign key appears. Phase 3 later adds `apps_org_fk` on
-- a fresh database, as it does on `email_accounts`.
--
-- The key on `apps`. `id` is the primary key already, so no row can break
-- UNIQUE (organization_id, id). The table is small. ADD CONSTRAINT builds the
-- index under ACCESS EXCLUSIVE, so the file sets lock_timeout first. On a
-- lock timeout the runner tries the whole file again, and the file is
-- idempotent.
--
-- Tenant-scoped (R5): each new table has `organization_id` with ENABLE and
-- FORCE row level security and one policy with USING and WITH CHECK, in the
-- shape of 312 and 315. `REFERENCES` comes before `DEFAULT`, because the
-- tenancy ratchet in tests/unit/test_tenancy_boundary.py matches the two with
-- no comma between. The org of a new row comes from the DEFAULT, the bound
-- tenant. The writer never names it.
-- An install points at its app by (organization_id, app_id), and a
-- contribution points at its install by (organization_id, install_id). So
-- neither can point at a row of another org. A foreign key check skips RLS,
-- and the pair closes that gap. Both cascade: a deleted app takes its
-- installs, and a deleted install takes its contributions (§5.3).
--
-- The five decisions of §5.1:
-- 1. `needs_setup` is not a column. The resolve computes it (§5.2 step 4).
-- 2. `scope_subject` holds the D12 grammar. The CHECK below holds the shape
--    of each kind. The writer (PKG-5) passes each value through
--    acb_auth.permissions.normalize_grant_subject first.
-- 3. A share with named people stays `private`. The people get rows in
--    `app_grants` (114).
-- 4. A duplicate claim is refused in code (PKG-5), not by an index, because
--    some points take many claims. The primary key of a contribution only
--    keeps one claim of one install at one point and key.
-- 5. The member purge (gateway/routes/admin/members.py) deletes a private
--    install of the member in the bound org only when nobody else sees it:
--    no `app_grants` row of its app names another subject. Its
--    contributions go by cascade. The purge keeps and counts a private
--    install that the member shared with named people (decision 3), and the
--    group and org installs that the member made. The CHECK
--    `package_install_private_installer` makes the owner of a private
--    install its installer, so the two sides find each row of the member.
--
-- The number was 321 at build time. At merge on 2026-11-01, origin/main held
-- 320, 322 and 323, so the file took 324 (R1). The comments of 322 and 323
-- still name 321, and they mean this file. Check it again at merge. Pinned by
-- tests/unit/test_package_registry_r8.py, which finds this file by CONTENT,
-- never by number (R1).
-- ============================================================================

BEGIN;

SET LOCAL lock_timeout = '5s';

-- ── 1. The tenant column of `apps`, where it is missing ──────────────────────
ALTER TABLE apps
    ADD COLUMN IF NOT EXISTS organization_id UUID
    REFERENCES organization (id) ON DELETE CASCADE
    DEFAULT current_setting('app.tenant_id', true)::uuid;

-- ── 2. The key on `apps`, the target of the install's foreign key ───────────
DO $apps_key$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = 'apps'::regclass
           AND conname = 'apps_org_id_key'
    ) THEN
        ALTER TABLE apps ADD CONSTRAINT apps_org_id_key UNIQUE (organization_id, id);
    END IF;
END
$apps_key$;

-- ── 3. The two tables ───────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS package_install (
    organization_id   UUID NOT NULL
                      REFERENCES organization (id) ON DELETE CASCADE
                      DEFAULT current_setting('app.tenant_id', true)::uuid,
    id                UUID NOT NULL DEFAULT gen_random_uuid(),
    app_id            UUID NOT NULL,
    -- An `app_versions.version` of the app. NULL is the owner's working draft.
    version           INTEGER NULL,
    scope_kind        TEXT NOT NULL,
    -- The owner's address for `private`, `group:<slug>` for `group`, `org`
    -- for `org` (D12).
    scope_subject     TEXT NOT NULL,
    installed_by      TEXT NOT NULL,
    -- NULL: the install does not expire.
    ends_at           TIMESTAMPTZ NULL,
    state             TEXT NOT NULL DEFAULT 'active',
    archived_at       TIMESTAMPTZ NULL,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (id),
    -- The target of the contributions' foreign key, so a claim stays in its org.
    CONSTRAINT package_install_org_id_key UNIQUE (organization_id, id),
    -- One app has one install at each scope.
    CONSTRAINT package_install_scope_key
        UNIQUE (organization_id, app_id, scope_kind, scope_subject),
    CONSTRAINT package_install_app_fk FOREIGN KEY (organization_id, app_id)
        REFERENCES apps (organization_id, id) ON DELETE CASCADE,
    CONSTRAINT package_install_scope_kind CHECK (
        scope_kind IN ('private', 'group', 'org')),
    -- The shape of each kind. Each value is lower case, as D12 stores it.
    -- A private address is the email shape of the CHECK of 272: one `@`
    -- and no whitespace. normalize_grant_subject accepts the same shape, so
    -- `a:b@x.test` is an address. A `group:` value is a group, never an
    -- address. `org` has no `@`, so the address shape refuses it.
    CONSTRAINT package_install_scope_subject CHECK (
        (scope_kind = 'org' AND scope_subject = 'org')
        OR (scope_kind = 'group'
            AND scope_subject ~ '^group:\S'
            AND scope_subject = lower(scope_subject))
        OR (scope_kind = 'private'
            AND scope_subject = lower(scope_subject)
            AND scope_subject !~ '^group:'
            AND scope_subject ~ '^[^@[:space:]]+@[^@[:space:]]+$')),
    CONSTRAINT package_install_installed_by CHECK (installed_by = lower(installed_by)),
    -- The owner of a private install made it. The purge finds a private
    -- install by its owner, so it puts each row that names the leaver on
    -- one side: deleted or kept, and counted.
    CONSTRAINT package_install_private_installer CHECK (
        scope_kind <> 'private' OR installed_by = scope_subject),
    -- Only the owner runs a draft.
    CONSTRAINT package_install_draft_private CHECK (
        version IS NOT NULL OR scope_kind = 'private'),
    CONSTRAINT package_install_state CHECK (
        state IN ('active', 'paused', 'archived')),
    -- `archived_at` is set when, and only when, the install is archived.
    CONSTRAINT package_install_archived_at CHECK (
        (state = 'archived') = (archived_at IS NOT NULL))
);

-- The expiry job of PKG-10 reads the installs that have an end date.
CREATE INDEX IF NOT EXISTS package_install_ends_at_idx
    ON package_install (organization_id, ends_at)
    WHERE ends_at IS NOT NULL;

CREATE TABLE IF NOT EXISTS package_contribution (
    organization_id   UUID NOT NULL
                      REFERENCES organization (id) ON DELETE CASCADE
                      DEFAULT current_setting('app.tenant_id', true)::uuid,
    install_id        UUID NOT NULL,
    -- One of the 13 points of package_host.md §4.
    point             TEXT NOT NULL,
    -- The key that the point's conflict rule names: an href, a job id, a
    -- card key.
    key               TEXT NOT NULL,
    payload           JSONB NOT NULL DEFAULT '{}',
    position          INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (organization_id, install_id, point, key),
    CONSTRAINT package_contribution_install_fk FOREIGN KEY (organization_id, install_id)
        REFERENCES package_install (organization_id, id) ON DELETE CASCADE,
    CONSTRAINT package_contribution_point CHECK (point IN (
        'nav', 'page', 'jobs', 'search', 'needs', 'cards', 'skill',
        'actions', 'triggers', 'agent', 'slot', 'fields', 'settings')),
    -- 65536 bytes (64 KiB) of JSON text.
    CONSTRAINT package_contribution_payload_size CHECK (
        octet_length(payload::text) <= 65536)
);

-- ── 4. Row level security ───────────────────────────────────────────────────
DO $rls$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_class
         WHERE oid = to_regclass('package_install')
           AND relrowsecurity
           AND relforcerowsecurity
    ) THEN
        ALTER TABLE package_install ENABLE ROW LEVEL SECURITY;
        ALTER TABLE package_install FORCE  ROW LEVEL SECURITY;
        DROP POLICY IF EXISTS package_install_tenant_isolation ON package_install;
        CREATE POLICY package_install_tenant_isolation ON package_install
            USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
            WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_class
         WHERE oid = to_regclass('package_contribution')
           AND relrowsecurity
           AND relforcerowsecurity
    ) THEN
        ALTER TABLE package_contribution ENABLE ROW LEVEL SECURITY;
        ALTER TABLE package_contribution FORCE  ROW LEVEL SECURITY;
        DROP POLICY IF EXISTS package_contribution_tenant_isolation ON package_contribution;
        CREATE POLICY package_contribution_tenant_isolation ON package_contribution
            USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
            WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);
    END IF;
END
$rls$;

COMMENT ON TABLE package_install IS
    'One install of one Studio app at one scope (WS-PKG PKG-4). '
    'A system package has no row. project-docs/specs/package_host.md §5.1.';
COMMENT ON TABLE package_contribution IS
    'One claim of one install at one contribution point (WS-PKG PKG-4). '
    'project-docs/specs/package_host.md §4 and §5.1.';

COMMIT;
