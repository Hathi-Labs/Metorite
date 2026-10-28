-- ============================================================================
-- 315_brief_runs.sql — the brief record and its claims (WS-BRIEF BR-4,
-- Metorite #588).
--
-- What: two new tables. `brief_runs` holds one brief or one close-out of one
--       member for one local date. `brief_claims` holds one line or one
--       number of a run, each with its source and its link.
--       gateway/brief/store.py is the one writer of both.
-- Why:  No table that exists today fits (core_brief.md §3.1). The planner of
--       BR-2a-1 writes a run, and the delivery of BR-2c reads it. This slice
--       adds no producer and no reader.
-- Depends on: 130_org_access_control.sql (organization).
--
-- Expand only (R6): two new tables and their indexes. No rename, no drop, and
-- no change to an existing table. Old code names no new object. A NOT NULL
-- with no default stands only on a column that the one writer always fills.
-- No running code writes these tables, so no old INSERT can miss a column.
-- Idempotent per infra/postgres/README.md: CREATE TABLE IF NOT EXISTS, CREATE
-- INDEX IF NOT EXISTS and an RLS block guarded on relforcerowsecurity.
-- Tenant-scoped (R5): `organization_id` with ENABLE and FORCE row level
-- security and one policy with USING and WITH CHECK, in the shape of 312.
-- `REFERENCES` comes before `DEFAULT`, because the tenancy ratchet in
-- tests/unit/test_tenancy_boundary.py matches the two with no comma between.
-- The org of a new row comes from the DEFAULT, the bound tenant. The writer
-- never names it.
-- A claim points at its run by `(organization_id, run_id)`, so a claim can
-- never point at a run of another org. A foreign key check skips RLS, and the
-- pair closes that gap.
-- The `<app>:<ref>` shape of `source_id` has no validator in code. The CHECK
-- `brief_claims_source_id_shape` is the fence of the shape (core_brief.md
-- §3.3). A reader splits it on the first colon only (my_day_feed.md §4.1).
-- Retention: runs older than 90 days go, by `jobs.prune` of BR-1b
-- (core_brief.md §9 Q6). This file deletes nothing.
-- The number is taken at build time (R1). On 2026-10-28 origin/main held 312,
-- one open branch claimed 313 and PR #1439 claimed 314. Check it again at
-- merge. Pinned by tests/unit/test_brief_store_r8.py, which finds this file by
-- CONTENT, never by number (R1).
-- ============================================================================

BEGIN;

CREATE TABLE IF NOT EXISTS brief_runs (
    organization_id   UUID NOT NULL
                      REFERENCES organization (id) ON DELETE CASCADE
                      DEFAULT current_setting('app.tenant_id', true)::uuid,
    id                UUID NOT NULL DEFAULT gen_random_uuid(),
    member_email      TEXT NOT NULL,
    kind              TEXT NOT NULL,
    -- The member's date of the slot.
    local_date        DATE NOT NULL,
    -- 07:30 or 18:30 local, as an instant.
    slot_at           TIMESTAMPTZ NOT NULL,
    state             TEXT NOT NULL DEFAULT 'planned',
    -- The first sentence, and who wrote it.
    lead              TEXT NULL,
    lead_source       TEXT NULL,
    -- The claims that the lead cites.
    lead_claim_ids    UUID[] NOT NULL DEFAULT '{}',
    -- Joins the usage_event row of the phrase call.
    model_request_id  TEXT NULL,
    -- The AUTO-2 run. No foreign key: that table is not on main yet.
    unattended_run_id UUID NULL,
    -- The error type only, never a payload.
    error             TEXT NULL,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    ready_at          TIMESTAMPTZ NULL,
    delivered_at      TIMESTAMPTZ NULL,
    PRIMARY KEY (id),
    -- One run for each member, kind and local date.
    CONSTRAINT brief_runs_slot_key UNIQUE (organization_id, member_email, kind, local_date),
    -- The target of the claims' foreign key, so a claim stays in its org.
    CONSTRAINT brief_runs_org_id_key UNIQUE (organization_id, id),
    CONSTRAINT brief_runs_member_email CHECK (
        member_email <> '' AND member_email = lower(member_email)),
    CONSTRAINT brief_runs_kind CHECK (kind IN ('brief', 'closeout')),
    CONSTRAINT brief_runs_state CHECK (state IN (
        'planned', 'gathered', 'phrasing', 'ready', 'delivered', 'empty', 'failed')),
    CONSTRAINT brief_runs_lead_source CHECK (
        lead_source IS NULL OR lead_source IN ('template', 'model')),
    -- A lead that a model wrote must cite a claim.
    CONSTRAINT brief_runs_model_lead_cites CHECK (
        lead_source IS DISTINCT FROM 'model' OR cardinality(lead_claim_ids) > 0),
    CONSTRAINT brief_runs_error CHECK (
        error IS NULL OR char_length(error) BETWEEN 1 AND 200)
);

CREATE TABLE IF NOT EXISTS brief_claims (
    organization_id   UUID NOT NULL
                      REFERENCES organization (id) ON DELETE CASCADE
                      DEFAULT current_setting('app.tenant_id', true)::uuid,
    id                UUID NOT NULL DEFAULT gen_random_uuid(),
    run_id            UUID NOT NULL,
    -- The order in the brief.
    position          INTEGER NOT NULL,
    section           TEXT NOT NULL,
    -- The line that code wrote, from the card.
    text              TEXT NOT NULL,
    source_app        TEXT NOT NULL,
    -- The card kind, or `count` for a number over a list.
    source_kind       TEXT NOT NULL,
    -- The signal_id shape `<app>:<ref>`.
    source_id         TEXT NOT NULL,
    -- For a `count`: the counted ids, at most 50.
    source_ids        TEXT[] NOT NULL DEFAULT '{}',
    -- The deep link. Code builds it, never a model (core_brief.md §3.3).
    href              TEXT NOT NULL,
    -- The number that the line states, and the field of the facts with it.
    value             NUMERIC NULL,
    value_path        TEXT NULL,
    -- The card's fingerprint at gather time.
    fingerprint       TEXT NULL,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (id),
    CONSTRAINT brief_claims_run_fk FOREIGN KEY (organization_id, run_id)
        REFERENCES brief_runs (organization_id, id) ON DELETE CASCADE,
    CONSTRAINT brief_claims_position CHECK (position >= 0),
    CONSTRAINT brief_claims_section CHECK (section IN (
        'now', 'needs_you', 'numbers', 'done', 'moved', 'acted')),
    CONSTRAINT brief_claims_text CHECK (text <> ''),
    -- A claim with no source cannot exist.
    CONSTRAINT brief_claims_source_present CHECK (
        source_app <> '' AND source_kind <> '' AND source_id <> '' AND href <> ''),
    CONSTRAINT brief_claims_source_id_shape CHECK (source_id ~ '^[a-z_]+:.+'),
    CONSTRAINT brief_claims_source_ids_max CHECK (cardinality(source_ids) <= 50),
    CONSTRAINT brief_claims_value_path CHECK (value IS NULL OR value_path IS NOT NULL)
);

-- The claims of a run, in order.
CREATE INDEX IF NOT EXISTS brief_claims_run_idx
    ON brief_claims (organization_id, run_id, position);

DO $rls$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_class
         WHERE oid = to_regclass('brief_runs')
           AND relrowsecurity
           AND relforcerowsecurity
    ) THEN
        ALTER TABLE brief_runs ENABLE ROW LEVEL SECURITY;
        ALTER TABLE brief_runs FORCE  ROW LEVEL SECURITY;
        DROP POLICY IF EXISTS brief_runs_tenant_isolation ON brief_runs;
        CREATE POLICY brief_runs_tenant_isolation ON brief_runs
            USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
            WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_class
         WHERE oid = to_regclass('brief_claims')
           AND relrowsecurity
           AND relforcerowsecurity
    ) THEN
        ALTER TABLE brief_claims ENABLE ROW LEVEL SECURITY;
        ALTER TABLE brief_claims FORCE  ROW LEVEL SECURITY;
        DROP POLICY IF EXISTS brief_claims_tenant_isolation ON brief_claims;
        CREATE POLICY brief_claims_tenant_isolation ON brief_claims
            USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
            WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);
    END IF;
END
$rls$;

COMMENT ON TABLE brief_runs IS
    'One brief or close-out of one member for one local date (WS-BRIEF BR-4). '
    'Only gateway/brief/store.py writes it. project-docs/specs/core_brief.md §3.2.';
COMMENT ON TABLE brief_claims IS
    'One line or number of a brief run, with its source and link (WS-BRIEF BR-4). '
    'Only gateway/brief/store.py writes it. project-docs/specs/core_brief.md §3.3.';

COMMIT;
