-- ============================================================================
-- 365_crm_lead_pipelines.sql — lead pipelines on the one pipeline table
-- (WS-CRM CRM-M4a, Metorite task mt-a53987cf).
--
-- What: the schema of a lead pipeline. No row of data changes.
--       * `crm_pipelines.kind`: `deal` or `lead`, NOT NULL DEFAULT `deal`.
--         The CHECK `crm_pipelines_kind` holds the two values.
--       * `crm_pipelines.feeds_pipeline_id`: nullable. For a lead pipeline
--         it names the deal pipeline that a converted lead's deal joins. Its
--         foreign key has ON DELETE SET NULL. The CHECK
--         `crm_pipelines_feeds_lead` keeps it NULL on a deal pipeline.
--       * `crm_lead_pipeline_stages`: which lead statuses sit in which lead
--         pipeline, in what order. A lead status (`crm_lead_statuses`) stays
--         one org-wide list, as a deal stage does for `crm_pipeline_stages`.
--       * `crm_leads.pipeline_id`: nullable, with an index. NULL means the
--         default lead list, which is every lead today.
--       * Three guard triggers. Each refuses a row that names a pipeline or a
--         status of another org, or a pipeline of the wrong kind. A fourth
--         trigger keeps `kind` fixed after the INSERT.
-- Why:  WS-CRM CRM-M4 (project-docs/specs/crm_conversational.md §8). The
--       deal pipeline seam of CRM-M3a (`routes/crm/pipelines.py`) takes a
--       kind, so a lead pipeline needs no second table or route.
-- Depends on: 144_crm.sql (crm_leads, crm_lead_statuses),
--             241_crm_tenancy.sql (the tenant key of both),
--             247_crm_source_profiles_pipelines.sql (crm_pipelines).
--
-- Expand only (R6). Two new columns on `crm_pipelines`: `kind` has a
-- constant default, so the ALTER does not rewrite the table, and an INSERT
-- of old code that names no kind gets `deal`. `feeds_pipeline_id` is
-- nullable with no default, and old code never names it. One new nullable
-- column on `crm_leads` with no default, which old code never names. One new
-- table, which old code never names. No rename, no drop, no backfill.
-- Each CHECK is added NOT VALID and then validated in a guarded block, as
-- 359 does. Every existing row is a deal pipeline with no feed, so each
-- CHECK holds. The file sets lock_timeout first, and the runner tries the
-- whole file again on a lock timeout.
-- Idempotent per infra/postgres/README.md: ADD COLUMN IF NOT EXISTS, CREATE
-- TABLE IF NOT EXISTS, CREATE INDEX IF NOT EXISTS, constraints guarded on
-- pg_constraint, CREATE OR REPLACE FUNCTION, DROP TRIGGER IF EXISTS then
-- CREATE, and an RLS block guarded on relforcerowsecurity. A second run
-- changes nothing.
-- Tenant-scoped (R5): `crm_lead_pipeline_stages` has `organization_id` with
-- ENABLE and FORCE row level security and one policy with USING and WITH
-- CHECK, in the shape of `crm_pipeline_stages` (247). `REFERENCES` comes
-- before `DEFAULT`, because the tenancy ratchet in
-- tests/unit/test_tenancy_boundary.py matches the two with no comma between.
-- The foreign key is inline, never `ADD CONSTRAINT <table>_org_fk`, because
-- generated phase 3 adds that name. The generated phases carry the table.
--
-- Same-org links. `crm_pipelines`, `crm_lead_statuses` and `crm_leads` have
-- no UNIQUE (organization_id, id), so a composite foreign key is not
-- possible without a new constraint on each. Plain foreign keys give the
-- cascade and the SET NULL. Three triggers give the same-org rule, as 339
-- does, for each role. Under row level security a trigger reads the rows of
-- the bound org only, so a row of another org reads as missing.
--   * `crm_pipelines_feeds_guard`: a feed is a deal pipeline of the org.
--   * `crm_lead_pipeline_stages_guard`: the pipeline is a lead pipeline of
--     the org, and the status is a lead status of the org.
--   * `crm_leads_pipeline_guard`: a lead's pipeline is a lead pipeline of
--     the org. A NULL pipeline returns at once, so each old write passes.
-- The three check the row that holds the link. A fourth trigger,
-- `crm_pipelines_kind_fixed`, refuses each change of `kind` after the
-- INSERT, so a fed deal pipeline never becomes a lead one, and a lead
-- pipeline never keeps lead statuses as a deal one. No sync, switch or seed
-- path writes `kind` on an UPDATE.
--
-- The number is taken at build time (R1). It was 360 until 2026-11-08 15:30
-- UTC, when a signature-images branch moved to 360 too. Branches and worktrees
-- then held 360 to 364, 366 and 367, so this file took 365. Check it again at
-- merge.
-- Pinned by tests/unit/test_crm_lead_pipelines.py, which finds this file by
-- CONTENT, never by number (R1).
-- ============================================================================

BEGIN;

SET LOCAL lock_timeout = '5s';

-- ── 1. The kind of a pipeline, and the deal pipeline that a lead one feeds ──

ALTER TABLE crm_pipelines
    ADD COLUMN IF NOT EXISTS kind TEXT NOT NULL DEFAULT 'deal';

ALTER TABLE crm_pipelines
    ADD COLUMN IF NOT EXISTS feeds_pipeline_id UUID NULL
    REFERENCES crm_pipelines (id) ON DELETE SET NULL;

DO $checks$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conname = 'crm_pipelines_kind'
           AND conrelid = 'crm_pipelines'::regclass
    ) THEN
        ALTER TABLE crm_pipelines
            ADD CONSTRAINT crm_pipelines_kind
            CHECK (kind IN ('deal', 'lead')) NOT VALID;
    END IF;
    IF EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conname = 'crm_pipelines_kind'
           AND conrelid = 'crm_pipelines'::regclass
           AND NOT convalidated
    ) THEN
        ALTER TABLE crm_pipelines VALIDATE CONSTRAINT crm_pipelines_kind;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conname = 'crm_pipelines_feeds_lead'
           AND conrelid = 'crm_pipelines'::regclass
    ) THEN
        ALTER TABLE crm_pipelines
            ADD CONSTRAINT crm_pipelines_feeds_lead
            CHECK (feeds_pipeline_id IS NULL OR kind = 'lead') NOT VALID;
    END IF;
    IF EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conname = 'crm_pipelines_feeds_lead'
           AND conrelid = 'crm_pipelines'::regclass
           AND NOT convalidated
    ) THEN
        ALTER TABLE crm_pipelines VALIDATE CONSTRAINT crm_pipelines_feeds_lead;
    END IF;
END
$checks$;

CREATE INDEX IF NOT EXISTS crm_pipelines_feeds_idx
    ON crm_pipelines (feeds_pipeline_id) WHERE feeds_pipeline_id IS NOT NULL;

-- A feed is a deal pipeline of the same org, for each role.
CREATE OR REPLACE FUNCTION crm_pipelines_feeds_guard() RETURNS trigger
LANGUAGE plpgsql AS $fn$
BEGIN
    IF NEW.feeds_pipeline_id IS NULL THEN
        RETURN NEW;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM crm_pipelines
         WHERE id = NEW.feeds_pipeline_id
           AND organization_id = NEW.organization_id
           AND kind = 'deal'
    ) THEN
        RAISE EXCEPTION 'crm_pipelines: a lead pipeline feeds a deal pipeline of its own organization'
            USING ERRCODE = 'foreign_key_violation';
    END IF;
    RETURN NEW;
END
$fn$;

DROP TRIGGER IF EXISTS crm_pipelines_feeds_guard ON crm_pipelines;
CREATE TRIGGER crm_pipelines_feeds_guard
    BEFORE INSERT OR UPDATE OF organization_id, kind, feeds_pipeline_id
    ON crm_pipelines
    FOR EACH ROW EXECUTE FUNCTION crm_pipelines_feeds_guard();

-- The kind of a pipeline is fixed at its INSERT, for each role. A change
-- would leave the links of the other kind behind.
CREATE OR REPLACE FUNCTION crm_pipelines_kind_fixed() RETURNS trigger
LANGUAGE plpgsql AS $fn$
BEGIN
    IF NEW.kind IS DISTINCT FROM OLD.kind THEN
        RAISE EXCEPTION 'crm_pipelines: the kind of a pipeline cannot change'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END
$fn$;

DROP TRIGGER IF EXISTS crm_pipelines_kind_fixed ON crm_pipelines;
CREATE TRIGGER crm_pipelines_kind_fixed
    BEFORE UPDATE OF kind ON crm_pipelines
    FOR EACH ROW EXECUTE FUNCTION crm_pipelines_kind_fixed();

-- ── 2. The lead statuses of a lead pipeline ─────────────────────────────────

CREATE TABLE IF NOT EXISTS crm_lead_pipeline_stages (
    organization_id  UUID NOT NULL
                     REFERENCES organization (id) ON DELETE CASCADE
                     DEFAULT current_setting('app.tenant_id', true)::uuid,
    pipeline_id      UUID NOT NULL
                     REFERENCES crm_pipelines (id) ON DELETE CASCADE,
    status_id        UUID NOT NULL
                     REFERENCES crm_lead_statuses (id) ON DELETE CASCADE,
    position         INT NOT NULL DEFAULT 0,
    PRIMARY KEY (pipeline_id, status_id)
);

CREATE INDEX IF NOT EXISTS crm_lead_pipeline_stages_status_idx
    ON crm_lead_pipeline_stages (status_id);

-- The pipeline is a lead pipeline of the row's org, and the status is a lead
-- status of the row's org, for each role.
CREATE OR REPLACE FUNCTION crm_lead_pipeline_stages_guard() RETURNS trigger
LANGUAGE plpgsql AS $fn$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM crm_pipelines
         WHERE id = NEW.pipeline_id
           AND organization_id = NEW.organization_id
           AND kind = 'lead'
    ) THEN
        RAISE EXCEPTION 'crm_lead_pipeline_stages: the pipeline is not a lead pipeline of this organization'
            USING ERRCODE = 'foreign_key_violation';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM crm_lead_statuses
         WHERE id = NEW.status_id
           AND organization_id = NEW.organization_id
    ) THEN
        RAISE EXCEPTION 'crm_lead_pipeline_stages: the status is not a lead status of this organization'
            USING ERRCODE = 'foreign_key_violation';
    END IF;
    RETURN NEW;
END
$fn$;

DROP TRIGGER IF EXISTS crm_lead_pipeline_stages_guard ON crm_lead_pipeline_stages;
CREATE TRIGGER crm_lead_pipeline_stages_guard
    BEFORE INSERT OR UPDATE OF organization_id, pipeline_id, status_id
    ON crm_lead_pipeline_stages
    FOR EACH ROW EXECUTE FUNCTION crm_lead_pipeline_stages_guard();

-- ── 3. The pipeline of a lead ───────────────────────────────────────────────

ALTER TABLE crm_leads
    ADD COLUMN IF NOT EXISTS pipeline_id UUID NULL
    REFERENCES crm_pipelines (id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS crm_leads_pipeline_idx ON crm_leads (pipeline_id);

-- A lead's pipeline is a lead pipeline of its org. NULL, the default lead
-- list, returns at once, so each write of old code passes unchanged.
CREATE OR REPLACE FUNCTION crm_leads_pipeline_guard() RETURNS trigger
LANGUAGE plpgsql AS $fn$
BEGIN
    IF NEW.pipeline_id IS NULL THEN
        RETURN NEW;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM crm_pipelines
         WHERE id = NEW.pipeline_id
           AND organization_id = NEW.organization_id
           AND kind = 'lead'
    ) THEN
        RAISE EXCEPTION 'crm_leads: the pipeline of a lead is a lead pipeline of its organization'
            USING ERRCODE = 'foreign_key_violation';
    END IF;
    RETURN NEW;
END
$fn$;

DROP TRIGGER IF EXISTS crm_leads_pipeline_guard ON crm_leads;
CREATE TRIGGER crm_leads_pipeline_guard
    BEFORE INSERT OR UPDATE OF organization_id, pipeline_id
    ON crm_leads
    FOR EACH ROW EXECUTE FUNCTION crm_leads_pipeline_guard();

-- ── 4. Row level security on the new table ──────────────────────────────────

DO $rls$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_class
         WHERE oid = to_regclass('crm_lead_pipeline_stages')
           AND relrowsecurity
           AND relforcerowsecurity
    ) THEN
        ALTER TABLE crm_lead_pipeline_stages ENABLE ROW LEVEL SECURITY;
        ALTER TABLE crm_lead_pipeline_stages FORCE  ROW LEVEL SECURITY;
        DROP POLICY IF EXISTS crm_lead_pipeline_stages_tenant_isolation
            ON crm_lead_pipeline_stages;
        CREATE POLICY crm_lead_pipeline_stages_tenant_isolation
            ON crm_lead_pipeline_stages
            USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
            WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);
    END IF;
END
$rls$;

COMMENT ON COLUMN crm_pipelines.kind IS
    'WS-CRM CRM-M4a: deal or lead. A reader that means a deal pipeline names '
    'kind = ''deal''. crm_conversational.md §8 CRM-M4.';
COMMENT ON COLUMN crm_pipelines.feeds_pipeline_id IS
    'WS-CRM CRM-M4a: the deal pipeline that a lead pipeline feeds. NULL on a '
    'deal pipeline, and NULL means the default deal pipeline.';
COMMENT ON TABLE crm_lead_pipeline_stages IS
    'WS-CRM CRM-M4a: which lead statuses sit in which lead pipeline, in what order.';
COMMENT ON COLUMN crm_leads.pipeline_id IS
    'WS-CRM CRM-M4a: the lead pipeline of a lead. NULL means the default lead list.';

COMMIT;
