-- ============================================================================
-- 268_artifacts.sql — the artifact registry: one row for every file a member
-- or an agent makes under inputs/ or outputs/, with its provenance, its kind,
-- its scope and its text index.
--
-- What: three new tables.
--       `artifacts`        — one row per stored file, keyed by the blob store
--                            key (agent_name, instance, path) inside the
--                            tenant. The bytes stay in `agent_blob`. Only
--                            acb_memory/artifacts.py writes it.
--       `artifact_member`  — the per-member overlay (star, opens), because two
--                            members can see one artifact and disagree about
--                            it, as they do about a task (pm_task_personal).
--       `artifact_shares`  — the named audiences on top of `scope`, with the
--                            D12 subject grammar (org, group:<slug>,
--                            project:<uuid>, user:<email>).
-- Why:  WS-60 AM-1, spec project-docs/specs/artifact_management.md §4.
--       Before this, an artifact had no row of its own, so nothing could
--       search it, share it, star it or expire it.
-- Depends on: 130_org_access_control.sql (organization),
--       71_agent_blob_store.sql and 136_agent_blob_instance.sql (the key).
--
-- Expand only (R6): three new tables and their indexes. No rename, no drop,
-- and no change to an existing table. Old code names no new object.
-- Idempotent per infra/postgres/README.md: CREATE TABLE IF NOT EXISTS,
-- CREATE INDEX IF NOT EXISTS, and an RLS block guarded on
-- relforcerowsecurity. A second run changes nothing.
-- Tenant-scoped (R5): `organization_id` with ENABLE and FORCE row level
-- security and one policy with USING and WITH CHECK, in the shape of
-- 255_pm_task_refs.sql. `REFERENCES` comes before `DEFAULT`, because the
-- tenancy ratchet in tests/unit/test_tenancy_boundary.py matches the two
-- with no comma between. The foreign key is inline, never
-- `ADD CONSTRAINT <table>_org_fk`, because generated phase 3 adds that name.
-- `project_id` and `task_id` carry no foreign key on purpose: a task or a
-- project may go while the artifact stays, and the read path checks the
-- task's visibility at read time (AM-3), not through a constraint.
-- The number is taken at build time (R1). On 2026-10-15 main held 267.
-- Check it again at merge. tests/unit/test_artifact_registry.py finds this
-- file by CONTENT, never by number.
-- ============================================================================

BEGIN;

CREATE TABLE IF NOT EXISTS artifacts (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id  UUID NOT NULL
                     REFERENCES organization (id) ON DELETE CASCADE
                     DEFAULT current_setting('app.tenant_id', true)::uuid,
    owner_email      TEXT NOT NULL,
    -- The blob store key. The bytes are agent_blob (agent_name, instance, path).
    agent_name       TEXT NOT NULL,
    instance         TEXT NOT NULL DEFAULT '',
    path             TEXT NOT NULL,
    sha256           TEXT NOT NULL,
    size             BIGINT NOT NULL DEFAULT 0,
    mime_type        TEXT NOT NULL DEFAULT 'application/octet-stream',
    version          INTEGER NOT NULL DEFAULT 1,
    kind             TEXT NOT NULL
                     CHECK (kind IN ('document', 'data', 'html', 'react', 'image',
                                     'pdf', 'code', 'archive', 'other')),
    title            TEXT NOT NULL CHECK (char_length(title) <= 500),
    -- 'member', or the name of the agent that wrote it.
    made_by          TEXT NOT NULL,
    via              TEXT NOT NULL DEFAULT 'chat'
                     CHECK (via ~ '^(chat|upload|email|whatsapp|import|app|copy|backfill|mcp:[a-z0-9:_.-]{1,60})$'),
    session_id       TEXT NULL,
    run_id           TEXT NULL,
    project_id       UUID NULL,
    task_id          UUID NULL,
    scope            TEXT NOT NULL DEFAULT 'personal'
                     CHECK (scope IN ('personal', 'project', 'org')),
    labels           TEXT[] NOT NULL DEFAULT '{}',
    search_tsv       TSVECTOR NULL,
    status           TEXT NOT NULL DEFAULT 'active'
                     CHECK (status IN ('active', 'trashed')),
    keep             BOOLEAN NOT NULL DEFAULT false,
    expires_at       TIMESTAMPTZ NULL,
    trashed_at       TIMESTAMPTZ NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_activity_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- One stored file, one row. The register upsert lands on this key.
CREATE UNIQUE INDEX IF NOT EXISTS artifacts_store_key
    ON artifacts (organization_id, agent_name, instance, path);
CREATE INDEX IF NOT EXISTS idx_artifacts_owner_recent
    ON artifacts (organization_id, owner_email, last_activity_at DESC);
CREATE INDEX IF NOT EXISTS idx_artifacts_session
    ON artifacts (organization_id, session_id);
CREATE INDEX IF NOT EXISTS idx_artifacts_search
    ON artifacts USING GIN (search_tsv);

CREATE TABLE IF NOT EXISTS artifact_member (
    artifact_id      UUID NOT NULL REFERENCES artifacts (id) ON DELETE CASCADE,
    member_email     TEXT NOT NULL,
    organization_id  UUID NOT NULL
                     REFERENCES organization (id) ON DELETE CASCADE
                     DEFAULT current_setting('app.tenant_id', true)::uuid,
    starred          BOOLEAN NOT NULL DEFAULT false,
    last_opened_at   TIMESTAMPTZ NULL,
    open_count       INTEGER NOT NULL DEFAULT 0,
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (artifact_id, member_email)
);

CREATE TABLE IF NOT EXISTS artifact_shares (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id  UUID NOT NULL
                     REFERENCES organization (id) ON DELETE CASCADE
                     DEFAULT current_setting('app.tenant_id', true)::uuid,
    artifact_id      UUID NOT NULL REFERENCES artifacts (id) ON DELETE CASCADE,
    -- The one D12 subject grammar. The validator in code is the fence, and
    -- this CHECK only refuses a shape no validator would pass.
    subject          TEXT NOT NULL
                     CHECK (subject ~ '^(org|group:[a-z0-9][a-z0-9_-]{0,63}|project:[0-9a-f-]{36}|user:[^@[:space:]]+@[^@[:space:]]+)$'),
    role             TEXT NOT NULL DEFAULT 'view'
                     CHECK (role IN ('view', 'edit')),
    granted_by       TEXT NOT NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS artifact_shares_key
    ON artifact_shares (organization_id, artifact_id, subject);

DO $rls$
DECLARE
    t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY['artifacts', 'artifact_member', 'artifact_shares'] LOOP
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

COMMENT ON TABLE artifacts IS
    'WS-60 AM-1: one row per stored file under inputs/ or outputs/, with its '
    'provenance, kind, scope and text index. The bytes are agent_blob. Only '
    'acb_memory/artifacts.py writes it. artifact_management.md §4.';
COMMENT ON TABLE artifact_member IS
    'WS-60 AM-1: the per-member overlay of an artifact (star, opens). '
    'artifact_management.md §4.2.';
COMMENT ON TABLE artifact_shares IS
    'WS-60 AM-1: the named audiences of an artifact on top of its scope, in '
    'the D12 subject grammar. artifact_management.md §4.3.';

COMMIT;
