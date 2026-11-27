-- ============================================================================
-- 468_studio_skill_runs.sql - the store of the runs of a saved skill
-- (WS-TOF TOF-3a, ai_studio_rows/F12.md slice F12a, Metorite task 3418).
--
-- What: one new table. `studio_skill_runs` holds one row for each run of a
--       skill of AI Studio: a `live` chat turn, a `test` of F4, or a repair
--       `proposal`. A row keeps the version that ran, the result, the
--       reason code of a failure, the names of the held writes, and the
--       repair key of the body that ran. A row never changes after its
--       INSERT. gateway/studio/skill_runs.py is the one module that runs
--       SQL on the table. F12a adds no caller: F12b writes the live and the
--       test rows, and F12c writes the proposals and reads the history.
-- Why:  F12.md "Not a second store". Nothing keeps the reason of a failed
--       skill run today, and the TOF-2b failure store holds no skill id by
--       design. This table is the ONE store of skill runs.
-- Depends on: 130_org_access_control.sql (organization),
--             320_studio_skills.sql (studio_skills).
--
-- NO INPUT AND NO VALUE. No column holds the words of a member or a value
-- of a write. `input_hash` is a SHA-256 in 64 hex digits (the CHECK).
-- `writes` holds action names and key names only, and the writer refuses
-- an item that holds a value. `detail` names a tool, an action, a key or an
-- error class. `reason` is a code (the CHECK).
--
-- THE KINDS. A `proposal` row exactly has a `repair_of`, the id of the
-- failed live row that it repairs (CHECK). A `live` row has an
-- `input_hash` and a `run_id` (CHECKs). A `test` row has a `test_id`
-- (CHECK). A row passed exactly when its reason is `passed`, and `credits`
-- is never below 0 (CHECKs). The unique partial index `studio_skill_runs_one_repair` holds
-- one proposal for each failed run. The unique partial index
-- `studio_skill_runs_one_failed_live` holds one failed live row for each
-- run of a skill. A NULL `run_id` never collides in a unique index, so the
-- CHECK on `run_id` makes the second index hold.
--
-- THE SKILL. The foreign key is `(organization_id, skill_id)` to
-- `studio_skills (organization_id, id)` with ON DELETE CASCADE, as the
-- versions of 320 do. So a row never points at a skill of another org, and
-- a delete of the skill or of the org deletes its rows.
--
-- IMMUTABLE. The trigger `studio_skill_runs_immutable` refuses each UPDATE,
-- for each role, the table owner too, as `studio_skill_versions_immutable`
-- of 320 does. A DELETE stays legal, so the cascade and the member purge
-- still work. The member purge (gateway/routes/admin/members.py) deletes
-- the rows of the member in the bound org, on `created_by`.
--
-- Expand only (R6): one new table, its keys, two indexes and one more
-- unique index, one trigger function and one trigger. No rename, no drop,
-- and no change to an old object. The old code never names the table.
-- Idempotent per infra/postgres/README.md: CREATE TABLE IF NOT EXISTS,
-- CREATE INDEX IF NOT EXISTS, CREATE OR REPLACE FUNCTION, DROP TRIGGER IF
-- EXISTS then CREATE, and an RLS block guarded on relforcerowsecurity. The
-- foreign key to `studio_skills` takes a SHARE ROW EXCLUSIVE lock on that
-- table until COMMIT. So the file sets `lock_timeout` first, as 464 does.
-- Tenant-scoped (R5): `organization_id` with ENABLE and FORCE row level
-- security, and one policy with USING and WITH CHECK, as 320 does. The org
-- of a new row comes from the DEFAULT, the bound tenant. The writer never
-- names it. `REFERENCES` comes before `DEFAULT`, because the tenancy ratchet
-- in tests/unit/test_tenancy_boundary.py matches the two with no comma
-- between. The generated phases carry the table.
--
-- The number is taken at build time (R1): 468 was booked for task 3418, and
-- premerge.sh checks it again at merge. Pinned by
-- tests/unit/test_studio_skill_runs_r8.py, which finds this file by
-- CONTENT, never by number (R1).
-- ============================================================================

BEGIN;

SET LOCAL lock_timeout = '5s';

CREATE TABLE IF NOT EXISTS studio_skill_runs (
    id                UUID NOT NULL DEFAULT gen_random_uuid(),
    organization_id   UUID NOT NULL
                      REFERENCES organization (id) ON DELETE CASCADE
                      DEFAULT current_setting('app.tenant_id', true)::uuid,
    skill_id          UUID NOT NULL,
    -- The version that ran. For a proposal, the base version that it changes.
    version           INTEGER NOT NULL,
    kind              TEXT NOT NULL,
    passed            BOOLEAN NOT NULL,
    -- The reason of each failure. skill_runs.REASONS holds the closed set.
    reason            TEXT NOT NULL,
    -- Names only: a tool, an action, a key or an error class. Never a value.
    detail            TEXT NOT NULL DEFAULT '',
    -- The repair key of the body that ran.
    body_key          TEXT NOT NULL,
    -- The hash of the org id and the input. Never the input.
    input_hash        TEXT NULL,
    -- At most 10 items {"action": ..., "keys": [...]}. Names only.
    writes            JSONB NOT NULL DEFAULT '[]'::jsonb,
    -- On a proposal, the id of the failed live row.
    repair_of         UUID NULL,
    -- On a test row, the id of the F4 test.
    test_id           TEXT NULL,
    run_id            TEXT NULL,
    thread_id         TEXT NULL,
    -- The spend of a test row or a proposal.
    credits           NUMERIC NULL,
    created_by        TEXT NOT NULL,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (id),
    CONSTRAINT studio_skill_runs_skill_fk FOREIGN KEY (organization_id, skill_id)
        REFERENCES studio_skills (organization_id, id) ON DELETE CASCADE,
    CONSTRAINT studio_skill_runs_version CHECK (version >= 1),
    CONSTRAINT studio_skill_runs_kind CHECK (kind IN ('live', 'test', 'proposal')),
    CONSTRAINT studio_skill_runs_reason CHECK (reason ~ '^[a-z_]{1,32}$'),
    CONSTRAINT studio_skill_runs_detail CHECK (char_length(detail) <= 300),
    CONSTRAINT studio_skill_runs_body_key CHECK (body_key ~ '^[0-9a-f]{64}$'),
    CONSTRAINT studio_skill_runs_input_hash CHECK (
        input_hash IS NULL OR input_hash ~ '^[0-9a-f]{64}$'),
    CONSTRAINT studio_skill_runs_writes_array CHECK (jsonb_typeof(writes) = 'array'),
    -- The CASE keeps jsonb_array_length off a value that is no array.
    CONSTRAINT studio_skill_runs_writes_items CHECK (
        CASE WHEN jsonb_typeof(writes) = 'array'
             THEN jsonb_array_length(writes) <= 10 ELSE false END),
    -- 4096 bytes of jsonb text. The writer holds the same number.
    CONSTRAINT studio_skill_runs_writes_size CHECK (octet_length(writes::text) <= 4096),
    -- A proposal exactly when the row repairs a failed run.
    CONSTRAINT studio_skill_runs_proposal_repairs CHECK (
        (kind = 'proposal') = (repair_of IS NOT NULL)),
    CONSTRAINT studio_skill_runs_live_input CHECK (
        kind <> 'live' OR input_hash IS NOT NULL),
    -- A NULL run_id never collides in a unique index, so a live row has one.
    CONSTRAINT studio_skill_runs_live_run CHECK (kind <> 'live' OR run_id IS NOT NULL),
    CONSTRAINT studio_skill_runs_test_id CHECK (kind <> 'test' OR test_id IS NOT NULL),
    CONSTRAINT studio_skill_runs_created_by CHECK (
        created_by <> '' AND created_by = lower(created_by)),
    -- A row passed exactly when its reason is `passed`.
    CONSTRAINT studio_skill_runs_passed_reason CHECK (passed = (reason = 'passed')),
    CONSTRAINT studio_skill_runs_credits CHECK (credits IS NULL OR credits >= 0)
);

-- The history of one skill, newest first.
CREATE INDEX IF NOT EXISTS studio_skill_runs_skill_idx
    ON studio_skill_runs (organization_id, skill_id, created_at DESC);

-- One repair for each failed run.
CREATE UNIQUE INDEX IF NOT EXISTS studio_skill_runs_one_repair
    ON studio_skill_runs (organization_id, repair_of)
    WHERE kind = 'proposal';

-- One failed live row for each run of a skill.
CREATE UNIQUE INDEX IF NOT EXISTS studio_skill_runs_one_failed_live
    ON studio_skill_runs (organization_id, skill_id, run_id)
    WHERE kind = 'live' AND NOT passed;

-- A stored run never changes. The trigger refuses each UPDATE, for each
-- role, the table owner too. Fence: tests/unit/test_studio_skill_runs_r8.py.
CREATE OR REPLACE FUNCTION studio_skill_runs_refuse_update() RETURNS trigger
LANGUAGE plpgsql AS $fn$
BEGIN
    RAISE EXCEPTION 'studio_skill_runs: a stored run cannot change'
        USING ERRCODE = 'check_violation';
END
$fn$;

DROP TRIGGER IF EXISTS studio_skill_runs_immutable ON studio_skill_runs;
CREATE TRIGGER studio_skill_runs_immutable
    BEFORE UPDATE ON studio_skill_runs
    FOR EACH ROW EXECUTE FUNCTION studio_skill_runs_refuse_update();

DO $rls$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_class
         WHERE oid = to_regclass('studio_skill_runs')
           AND relrowsecurity
           AND relforcerowsecurity
    ) THEN
        ALTER TABLE studio_skill_runs ENABLE ROW LEVEL SECURITY;
        ALTER TABLE studio_skill_runs FORCE  ROW LEVEL SECURITY;
        DROP POLICY IF EXISTS studio_skill_runs_tenant_isolation ON studio_skill_runs;
        CREATE POLICY studio_skill_runs_tenant_isolation ON studio_skill_runs
            USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
            WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);
    END IF;
END
$rls$;

COMMENT ON TABLE studio_skill_runs IS
    'One immutable run of a Studio skill: live, test or proposal (WS-TOF TOF-3a, F12a). '
    'A trigger refuses each UPDATE. No input text and no value. '
    'Only gateway/studio/skill_runs.py runs SQL on it. project-docs/specs/ai_studio_rows/F12.md.';

COMMIT;
