-- ============================================================================
-- 247_crm_source_profiles_pipelines.sql — the source profile of a connection,
-- and the deal pipelines.
--
-- What: three new tables and two new columns.
--         * `crm_source_profiles`: the shape of the customer's Zoho (edition,
--           currency, modules, layouts, pipelines), one row for each version.
--           A new version is kept only when the hash of the profile changes.
--           The profile holds metadata only. It never holds a record or a token.
--         * `crm_pipelines`: one row for each deal pipeline. A mirrored
--           pipeline has `ext_source` and `ext_id`, and the `layout_ext_id` of
--           the Zoho layout that holds it. A native pipeline has NULL in all
--           three.
--         * `crm_pipeline_stages`: which deal stages sit in which pipeline, in
--           what order. A stage (`crm_deal_statuses`) stays one global list, so
--           one stage can sit in many pipelines.
--         * `crm_deals.pipeline_id`: the pipeline of a deal. NULL is allowed.
--         * `crm_deal_statuses.forecast_category`: the forecast category of a
--           stage, as the provider names it. NULL is allowed.
--       The backfill gives each organization that has deal stages, and is not
--       in Mirror mode, one default pipeline called "Sales pipeline" with all
--       its stages, and points its deals at it.
-- Why:  WS-53 CRM-Q1 and CRM-Q2, spec project-docs/specs/crm_platform.md
--       §18.3 (the source profile) and §18.5 (the tickets).
-- Depends on: 130_org_access_control.sql (organization),
--             144_crm.sql (crm_deals, crm_deal_statuses),
--             243_crm_settings.sql (crm_settings.mode),
--             245_integration_connections.sql (integration_connections).
--
-- Expand only (R6): three new tables and two nullable columns. No rename, no
-- drop. The one UPDATE fills the new nullable `crm_deals.pipeline_id`, and it
-- runs only for an organization that has no pipeline yet. Old code names no
-- new object.
-- Idempotent per infra/postgres/README.md: CREATE TABLE IF NOT EXISTS,
-- CREATE INDEX IF NOT EXISTS, ADD COLUMN IF NOT EXISTS, an RLS block guarded on
-- relforcerowsecurity, and a backfill guarded on "no pipeline yet". A second
-- run changes nothing.
-- Tenant-scoped (R5): each new table has `organization_id` with ENABLE and
-- FORCE row level security and one policy with USING and WITH CHECK, in the
-- shape of 243_crm_settings.sql. `REFERENCES` comes before `DEFAULT`, because
-- the tenancy ratchet in tests/unit/test_tenancy_boundary.py matches the two
-- with no comma between. The foreign keys are inline, never
-- `ADD CONSTRAINT <table>_org_fk`, because generated phase 3 adds that name.
-- Pinned by tests/unit/test_crm_pipelines_r8.py, which finds this file by
-- CONTENT, never by number (R1).
-- ============================================================================

BEGIN;

-- ── 1. The source profile ───────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS crm_source_profiles (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id  UUID NOT NULL
                     REFERENCES organization (id) ON DELETE CASCADE
                     DEFAULT current_setting('app.tenant_id', true)::uuid,
    connection_id    UUID NOT NULL
                     REFERENCES integration_connections (id) ON DELETE CASCADE,
    version          INT NOT NULL,
    fetched_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    profile          JSONB NOT NULL,
    profile_hash     TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS crm_source_profiles_version_key
    ON crm_source_profiles (organization_id, connection_id, version);

-- ── 2. The deal pipelines ───────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS crm_pipelines (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id  UUID NOT NULL
                     REFERENCES organization (id) ON DELETE CASCADE
                     DEFAULT current_setting('app.tenant_id', true)::uuid,
    name             TEXT NOT NULL,
    position         INT NOT NULL DEFAULT 0,
    is_default       BOOLEAN NOT NULL DEFAULT false,
    ext_source       TEXT NULL,
    ext_id           TEXT NULL,
    layout_ext_id    TEXT NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- One external pipeline, one row, for each tenant. A native pipeline has NULL
-- in both columns, and NULLs never collide. `upsert_from_source` infers this
-- index in its ON CONFLICT clause.
CREATE UNIQUE INDEX IF NOT EXISTS crm_pipelines_ext_key
    ON crm_pipelines (organization_id, ext_source, ext_id);

CREATE TABLE IF NOT EXISTS crm_pipeline_stages (
    organization_id  UUID NOT NULL
                     REFERENCES organization (id) ON DELETE CASCADE
                     DEFAULT current_setting('app.tenant_id', true)::uuid,
    pipeline_id      UUID NOT NULL
                     REFERENCES crm_pipelines (id) ON DELETE CASCADE,
    status_id        UUID NOT NULL
                     REFERENCES crm_deal_statuses (id) ON DELETE CASCADE,
    position         INT NOT NULL DEFAULT 0,
    PRIMARY KEY (pipeline_id, status_id)
);

CREATE INDEX IF NOT EXISTS crm_pipeline_stages_status_idx
    ON crm_pipeline_stages (status_id);

-- ── 3. The two columns ──────────────────────────────────────────────────────

ALTER TABLE crm_deals
    ADD COLUMN IF NOT EXISTS pipeline_id UUID NULL
    REFERENCES crm_pipelines (id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS crm_deals_pipeline_idx ON crm_deals (pipeline_id);

-- Serves the re-link of the sync (crm_sync/engine.py::_relink_deals).
CREATE INDEX IF NOT EXISTS crm_deals_unlinked_idx
    ON crm_deals (organization_id)
    WHERE ext_source = 'zoho' AND pipeline_id IS NULL;

ALTER TABLE crm_deal_statuses
    ADD COLUMN IF NOT EXISTS forecast_category TEXT NULL;

-- ── 4. Row level security on the three new tables ───────────────────────────
--
-- Before the backfill, so the first run inserts without a policy in the way.
-- A replay finds the policy in place, and the backfill binds the tenant.

DO $rls$
DECLARE
    t text;
BEGIN
    FOREACH t IN ARRAY ARRAY[
        'crm_source_profiles', 'crm_pipelines', 'crm_pipeline_stages'
    ] LOOP
        IF NOT EXISTS (
            SELECT 1 FROM pg_class
             WHERE oid = to_regclass(t)
               AND relrowsecurity
               AND relforcerowsecurity
        ) THEN
            EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
            EXECUTE format('ALTER TABLE %I FORCE  ROW LEVEL SECURITY', t);
            EXECUTE format('DROP POLICY IF EXISTS %I ON %I',
                           t || '_tenant_isolation', t);
            EXECUTE format(
                'CREATE POLICY %I ON %I '
                '    USING      (organization_id = '
                '        current_setting(''app.tenant_id'', true)::uuid) '
                '    WITH CHECK (organization_id = '
                '        current_setting(''app.tenant_id'', true)::uuid)',
                t || '_tenant_isolation', t);
        END IF;
    END LOOP;
END
$rls$;

-- ── 5. The backfill: one default pipeline for each native org ───────────────
--
-- An organization that has deal stages, is not in Mirror mode, and has no
-- pipeline gets "Sales pipeline" with all its stages in stage order. Its deals
-- point at it. A Mirror organization gets its pipelines from the discovery
-- step of the sync. An organization that already has a pipeline is left alone,
-- so a stage that an admin removed from the pipeline does not come back.
--
-- The runner connects as the table owner, and FORCE binds the owner too
-- unless it is a superuser or BYPASSRLS (see 221 and 241). So a role that
-- RLS binds runs the work once for each organization, with that organization
-- bound. The binding is LOCAL to this transaction. A role that bypasses row
-- level security binds nothing.

DO $backfill$
DECLARE
    org_id uuid;
    new_pipeline uuid;
    bypass boolean;
BEGIN
    SELECT rolsuper OR rolbypassrls INTO bypass
      FROM pg_roles WHERE rolname = current_user;

    FOR org_id IN SELECT id FROM organization ORDER BY id LOOP
        IF NOT bypass THEN
            PERFORM set_config('app.tenant_id', org_id::text, true);
        END IF;

        IF EXISTS (SELECT 1 FROM crm_pipelines WHERE organization_id = org_id) THEN
            CONTINUE;
        END IF;
        IF NOT EXISTS (
            SELECT 1 FROM crm_deal_statuses WHERE organization_id = org_id
        ) THEN
            CONTINUE;
        END IF;
        IF EXISTS (
            SELECT 1 FROM crm_settings
             WHERE organization_id = org_id AND mode = 'mirror'
        ) THEN
            CONTINUE;
        END IF;

        INSERT INTO crm_pipelines (organization_id, name, position, is_default)
        VALUES (org_id, 'Sales pipeline', 0, true)
        RETURNING id INTO new_pipeline;

        INSERT INTO crm_pipeline_stages (organization_id, pipeline_id, status_id, position)
        SELECT org_id, new_pipeline, s.id, s.position
          FROM crm_deal_statuses s
         WHERE s.organization_id = org_id;

        UPDATE crm_deals
           SET pipeline_id = new_pipeline
         WHERE organization_id = org_id AND pipeline_id IS NULL;
    END LOOP;
END
$backfill$;

COMMENT ON TABLE crm_source_profiles IS
    'WS-53 CRM-Q1: the shape of the customer''s Zoho, one row for each version. '
    'Metadata only. crm_platform.md §18.3.';
COMMENT ON TABLE crm_pipelines IS
    'WS-53 CRM-Q2: one deal pipeline. A stage is a global crm_deal_statuses row '
    'that sits in many pipelines through crm_pipeline_stages. crm_platform.md §18.5.';
COMMENT ON TABLE crm_pipeline_stages IS
    'WS-53 CRM-Q2: which deal stages sit in which pipeline, in what order.';

COMMIT;
