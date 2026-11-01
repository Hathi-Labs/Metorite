-- ============================================================================
-- 324_goal_records.sql — the goal record of a long job (WS-WEB WEB-10,
-- Metorite task 26607a5a).
--
-- What: one new table. `goal_records` holds one goal of one member: the
--       objective, the state, a version for compare-and-set, the rounds so
--       far and their cap, the credits so far and their cap, and a block code.
--       packages/acb_common/acb_common/goal_store.py is the one writer.
-- Why:  A research job needs more than one turn (web_research.md §6). The
--       goal keeps its objective across turns and restarts, and its caps stop
--       the job. WEB-11 runs the research job on this record. WS-43 CA runs
--       and Studio builds can use it later, so the name is not `research_*`.
--       This slice adds the record and its store only: no route, no tool and
--       no reader.
-- Depends on: 130_org_access_control.sql (organization).
--
-- Expand only (R6): one new table, one index, one trigger function and one
-- trigger. No rename, no drop, and no change to an existing table. Old code
-- names no new object. A NOT NULL with no default stands only on a column
-- that the one writer always fills. No running code writes this table, so no
-- old INSERT can miss a column.
-- Idempotent per infra/postgres/README.md: CREATE TABLE IF NOT EXISTS, CREATE
-- INDEX IF NOT EXISTS, CREATE OR REPLACE FUNCTION, DROP TRIGGER IF EXISTS then
-- CREATE, and an RLS block guarded on relforcerowsecurity.
-- Tenant-scoped (R5): `organization_id` with ENABLE and FORCE row level
-- security and one policy with USING and WITH CHECK, in the shape of 320.
-- `REFERENCES` comes before `DEFAULT`, because the tenancy ratchet in
-- tests/unit/test_tenancy_boundary.py matches the two with no comma between.
-- The org of a new row comes from the DEFAULT, the bound tenant. The writer
-- never names it.
-- Private to its member: the store filters each statement on `member_email`,
-- and RLS filters on the org. No share to a group or to the org is here.
-- Compare-and-set: `version` starts at 1. Each write of the store names the
-- version that the caller read, and raises it by one. The trigger
-- `goal_records_guard` refuses an UPDATE that does not raise the version by
-- exactly one, for each role. So a writer that forgets the version cannot
-- break the compare-and-set of the store.
-- The objective never changes: the same trigger refuses a change of `id`,
-- `organization_id`, `member_email`, `kind`, `objective` or `created_at`. A
-- DELETE stays legal, so the cascade of an org and the member purge work.
-- The caps: `round_cap` is a count of rounds (40 for research), and
-- `cost_cap_credits` is in AI credits, NUMERIC(14, 4) as `usage_event.
-- billed_credits` and `credit_ledger.delta` hold them. An active goal is
-- always under both caps: the CHECK `goal_records_active_under_caps` holds
-- it. The store moves a goal to `blocked` with `cap_reached` in the write
-- that reaches a cap.
-- Block codes: the closed set `cap_reached`, `needs_member` and
-- `source_unreachable`. A goal is `blocked` if and only if it has a code.
-- The store holds one sentence for the member for each code.
-- Permission to continue is not durable (web_research.md §6). No column holds
-- a permission, a consent or an approval. A restart asks the member again
-- before a new round. tests/unit/test_goal_record_r8.py pins the column list.
-- `job_id` names the `jobs` row (312) that runs the goal. No foreign key: a
-- job row can go before its goal, and the goal stays.
-- Retention: no pruning yet. A later slice owns it. This file deletes nothing.
-- The member purge (gateway/routes/admin/members.py) deletes the member's
-- goals of the bound org.
-- The number is taken at build time (R1). On 2026-11-01 origin/main held 320,
-- 322 (322_apps_format.sql) and 323 (323_studio_objects.sql, STU-C1). An open
-- branch claimed 321 (PKG-4). Check it again at merge.
-- Pinned by tests/unit/test_goal_record_r8.py, which finds this file by
-- CONTENT, never by number (R1).
-- ============================================================================

BEGIN;

CREATE TABLE IF NOT EXISTS goal_records (
    organization_id   UUID NOT NULL
                      REFERENCES organization (id) ON DELETE CASCADE
                      DEFAULT current_setting('app.tenant_id', true)::uuid,
    id                UUID NOT NULL DEFAULT gen_random_uuid(),
    member_email      TEXT NOT NULL,
    -- What runs the goal, in the shape of `jobs.kind`.
    kind              TEXT NOT NULL DEFAULT 'research',
    -- What the member asked for. It never changes after the INSERT.
    objective         TEXT NOT NULL,
    state             TEXT NOT NULL DEFAULT 'active',
    -- The compare-and-set token. Each UPDATE raises it by one.
    version           INTEGER NOT NULL DEFAULT 1,
    round_count       INTEGER NOT NULL DEFAULT 0,
    round_cap         INTEGER NOT NULL,
    -- AI credits, as usage_event.billed_credits holds them.
    cost_used_credits NUMERIC(14, 4) NOT NULL DEFAULT 0,
    cost_cap_credits  NUMERIC(14, 4) NOT NULL,
    -- Why a `blocked` goal waits. NULL in every other state.
    block_code        TEXT NULL,
    -- The jobs row (312) that runs the goal. No foreign key.
    job_id            UUID NULL,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- Set when the goal is done, failed or cancelled.
    finished_at       TIMESTAMPTZ NULL,
    PRIMARY KEY (id),
    -- The target of a child's foreign key, so a child stays in its org.
    CONSTRAINT goal_records_org_id_key UNIQUE (organization_id, id),
    CONSTRAINT goal_records_member_email CHECK (
        member_email <> '' AND member_email = lower(member_email)),
    CONSTRAINT goal_records_kind CHECK (kind ~ '^[a-z][a-z0-9_.]{0,63}$'),
    CONSTRAINT goal_records_objective CHECK (
        btrim(objective) <> '' AND char_length(objective) <= 2000),
    CONSTRAINT goal_records_state CHECK (
        state IN ('active', 'blocked', 'done', 'failed', 'cancelled')),
    CONSTRAINT goal_records_block_code CHECK (
        block_code IS NULL
        OR block_code IN ('cap_reached', 'needs_member', 'source_unreachable')),
    -- A goal is blocked if and only if it has a code.
    CONSTRAINT goal_records_blocked_has_code CHECK (
        (state = 'blocked') = (block_code IS NOT NULL)),
    -- A goal is finished if and only if its state is final.
    CONSTRAINT goal_records_finished CHECK (
        (state IN ('done', 'failed', 'cancelled')) = (finished_at IS NOT NULL)),
    CONSTRAINT goal_records_version CHECK (version >= 1),
    CONSTRAINT goal_records_round_cap CHECK (round_cap BETWEEN 1 AND 10000),
    CONSTRAINT goal_records_round_count CHECK (round_count >= 0),
    -- Postgres sorts NaN above every number, so `>= 0` alone takes a NaN.
    -- Each NUMERIC CHECK refuses NaN by name.
    CONSTRAINT goal_records_cost_cap CHECK (
        cost_cap_credits <> 'NaN'
        AND cost_cap_credits > 0 AND cost_cap_credits <= 1000000000),
    CONSTRAINT goal_records_cost_used CHECK (
        cost_used_credits <> 'NaN' AND cost_used_credits >= 0),
    -- The cap stops the job: an active goal is under both caps.
    CONSTRAINT goal_records_active_under_caps CHECK (
        state <> 'active'
        OR (round_count < round_cap AND cost_used_credits < cost_cap_credits))
);

-- The list of one member, newest change first.
CREATE INDEX IF NOT EXISTS idx_goal_records_member
    ON goal_records (organization_id, member_email, updated_at DESC);

-- The identity of a goal never changes, and each UPDATE raises the version by
-- exactly one. For each role, the table owner too.
-- Fence: tests/unit/test_goal_record_r8.py.
CREATE OR REPLACE FUNCTION goal_records_guard_update() RETURNS trigger
LANGUAGE plpgsql AS $fn$
BEGIN
    IF NEW.id IS DISTINCT FROM OLD.id
       OR NEW.organization_id IS DISTINCT FROM OLD.organization_id
       OR NEW.member_email IS DISTINCT FROM OLD.member_email
       OR NEW.kind IS DISTINCT FROM OLD.kind
       OR NEW.objective IS DISTINCT FROM OLD.objective
       OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
        RAISE EXCEPTION 'goal_records: the objective and the identity of a goal cannot change'
            USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.version IS DISTINCT FROM OLD.version + 1 THEN
        RAISE EXCEPTION 'goal_records: each update raises the version by one'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END
$fn$;

DROP TRIGGER IF EXISTS goal_records_guard ON goal_records;
CREATE TRIGGER goal_records_guard
    BEFORE UPDATE ON goal_records
    FOR EACH ROW EXECUTE FUNCTION goal_records_guard_update();

DO $rls$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_class
         WHERE oid = to_regclass('goal_records')
           AND relrowsecurity
           AND relforcerowsecurity
    ) THEN
        ALTER TABLE goal_records ENABLE ROW LEVEL SECURITY;
        ALTER TABLE goal_records FORCE  ROW LEVEL SECURITY;
        DROP POLICY IF EXISTS goal_records_tenant_isolation ON goal_records;
        CREATE POLICY goal_records_tenant_isolation ON goal_records
            USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
            WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);
    END IF;
END
$rls$;

COMMENT ON TABLE goal_records IS
    'One goal of one member for a long job: objective, state, version, caps and block code '
    '(WS-WEB WEB-10). Only packages/acb_common/acb_common/goal_store.py writes it. '
    'project-docs/specs/web_research.md §6.';

COMMIT;
