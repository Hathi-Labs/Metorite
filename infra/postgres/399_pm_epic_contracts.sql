-- ============================================================================
-- 399_pm_epic_contracts.sql — the contract of an epic (SP-8a, Metorite task
-- 2291).
--
-- What: one new table. `pm_epic_contracts` holds one row for each epic: the
--       outcome, the acceptance checks, what is out of scope, the risk tier,
--       the appetite in credits and in review hours, the workstream and the
--       spec link. gateway/routes/projects/epic_contract.py is the one writer.
-- Why:  project-docs/specs/project_management_app.md §13.3. An epic needs a
--       contract that a reviewer can accept as a whole. The custom-field store
--       is not the home: it has no list type and no epic-only scope, and each
--       project would define seven fields by hand (§13.3 records the call).
-- Depends on: 130_org_access_control.sql (organization), 146 (pm_tasks) and
--       161_projects_tenancy.sql (the function pm_organization_from_parent).
--
-- Expand only (R6): one new table, one trigger function, two triggers and an
-- RLS block. No rename,
-- no drop and no change to an existing table. `pm_tasks` gains no constraint,
-- so this file takes no long lock on it. Old code names no new object.
-- Each column of the contract is nullable. The code, and not a CHECK, holds
-- the rule of 3 to 7 checks and the cap of each text, so a later release can
-- change a cap with no migration.
-- Idempotent per infra/postgres/README.md: CREATE TABLE IF NOT EXISTS,
-- CREATE OR REPLACE FUNCTION, CREATE OR REPLACE TRIGGER, and an RLS block
-- guarded on relforcerowsecurity.
--
-- Tenant-scoped (R5): `organization_id` with ENABLE and FORCE row level
-- security and one policy with USING and WITH CHECK, in the shape of 339.
-- `REFERENCES` comes before `DEFAULT`, because the tenancy ratchet in
-- tests/unit/test_tenancy_boundary.py matches the two with no comma between.
-- The org of a new row comes from the DEFAULT, the bound tenant. The 161
-- trigger `pm_organization_from_parent('pm_tasks', 'task_id')` then fills a
-- NULL org from the task. The guard `pm_epic_contracts_same_org` refuses a
-- task that is not a row of the contract's org, for each role. So a contract
-- never names a task of another org.
--
-- One row for each epic: `task_id` is the primary key. ON DELETE CASCADE
-- removes the contract with its task. A task whose type stops being an epic
-- keeps its row. The reads then show no contract, and a change of the type
-- back shows it again (§13.3).
--
-- `acceptance` is a JSON array of {id, text, done, evidence}. The only CHECK
-- is that the value is an array. `risk_tier` is LOW, STANDARD or HIGH. The
-- lead sets it. No code proposes a tier in SP-8a.
-- `workstream` is free text until SW-67 adds the lane record: a project id or
-- a name of the track, as the lead writes it.
-- The token of a write is the version of the task (`pm_tasks.updated_at`),
-- and a write touches the task. So the table has no version column (the
-- rule of core.py, "no second monotonic fact about one row").
--
-- The number is taken at build time (R1). It was 362 when the branch was cut:
-- origin/main held 359, and branches held 360 and 361. Later on 2026-11-08
-- origin/main held 366 (PKG-13), and branches and worktrees held 361 and 363
-- to 368, so the file became 369. On 2026-11-09 origin/main held 366, and
-- branches held 367 and 370 to 374, so the file became 375. Later on
-- 2026-11-09 origin/main held 378, LP-1a held 383 and TPL-1a takes 384, and
-- other branches held 380 and 382, so the file became 385. Later on
-- 2026-11-09 origin/main held 390, and branches held 393, 394 and 396, so the
-- file became 397. On 2026-11-09 later still, origin/main held 393, and
-- branches held 396 and 398 (CRM-M3b), so the file became 399, above them all.
-- Check it again at merge.
-- Pinned by tests/unit/test_epic_contract_r8.py, which finds this file by
-- CONTENT, never by number (R1).
-- ============================================================================

BEGIN;

CREATE TABLE IF NOT EXISTS pm_epic_contracts (
    -- The epic. One contract for each epic task.
    task_id                UUID NOT NULL REFERENCES pm_tasks (id) ON DELETE CASCADE,
    organization_id        UUID NOT NULL
                           REFERENCES organization (id) ON DELETE CASCADE
                           DEFAULT current_setting('app.tenant_id', true)::uuid,
    -- One sentence: what is true when the epic is done.
    outcome                TEXT NULL,
    -- 3 to 7 checks that a reviewer can tick. The code holds the count.
    acceptance             JSONB NULL,
    -- What the epic does not do.
    out_of_scope           TEXT NULL,
    -- The review depth. The lead sets it.
    risk_tier              TEXT NULL,
    -- What the epic may spend.
    appetite_credits       NUMERIC NULL,
    appetite_review_hours  NUMERIC NULL,
    -- The track that owns the epic. Free text until SW-67.
    workstream             TEXT NULL,
    -- A Knowledge page or a repository file.
    spec_link              TEXT NULL,
    -- An email or an `agent:` actor, from the session. Never from a body.
    updated_by             TEXT NULL,
    updated_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (task_id),
    CONSTRAINT pm_epic_contracts_acceptance_array CHECK (
        acceptance IS NULL OR jsonb_typeof(acceptance) = 'array'),
    CONSTRAINT pm_epic_contracts_risk_tier CHECK (
        risk_tier IS NULL OR risk_tier IN ('LOW', 'STANDARD', 'HIGH')),
    CONSTRAINT pm_epic_contracts_appetite CHECK (
        (appetite_credits IS NULL OR appetite_credits >= 0)
        AND (appetite_review_hours IS NULL OR appetite_review_hours >= 0))
);

-- Fill and verify the tenant from the task, as 161 attaches the pm_* tables.
-- The function is 161's. Only the attachment is new.
CREATE OR REPLACE TRIGGER trg_pm_epic_contracts_org_from_task
    BEFORE INSERT OR UPDATE ON pm_epic_contracts
    FOR EACH ROW EXECUTE FUNCTION
    pm_organization_from_parent('pm_tasks', 'task_id');

-- The 161 function says nothing when it cannot read the task. Under FORCE
-- RLS a task of another org is a row that it cannot read, so it lets that
-- row through, and the foreign key (which ignores RLS) passes too. This guard
-- closes that gap, in the shape of 339: the task of a contract is a row of
-- the contract's own org, for each role. Triggers fire in name order, so
-- `..._org_from_task` fills a NULL org first, and `..._same_org` checks next.
-- Fence: tests/unit/test_epic_contract_r8.py.
CREATE OR REPLACE FUNCTION pm_epic_contracts_same_org() RETURNS trigger
LANGUAGE plpgsql AS $fn$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pm_tasks
         WHERE id = NEW.task_id AND organization_id = NEW.organization_id
    ) THEN
        RAISE EXCEPTION 'pm_epic_contracts: the task is not a task of this organization'
            USING ERRCODE = 'foreign_key_violation';
    END IF;
    RETURN NEW;
END
$fn$;

CREATE OR REPLACE TRIGGER trg_pm_epic_contracts_same_org
    BEFORE INSERT OR UPDATE OF organization_id, task_id ON pm_epic_contracts
    FOR EACH ROW EXECUTE FUNCTION pm_epic_contracts_same_org();

DO $rls$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_class
         WHERE oid = to_regclass('pm_epic_contracts')
           AND relrowsecurity
           AND relforcerowsecurity
    ) THEN
        ALTER TABLE pm_epic_contracts ENABLE ROW LEVEL SECURITY;
        ALTER TABLE pm_epic_contracts FORCE ROW LEVEL SECURITY;
        DROP POLICY IF EXISTS pm_epic_contracts_tenant_isolation ON pm_epic_contracts;
        CREATE POLICY pm_epic_contracts_tenant_isolation ON pm_epic_contracts
            USING (organization_id = current_setting('app.tenant_id', true)::uuid)
            WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);
    END IF;
END
$rls$;

COMMENT ON TABLE pm_epic_contracts IS
    'The contract of one epic: outcome, 3 to 7 checks, scope, risk tier, appetite (SP-8a). '
    'Only gateway/routes/projects/epic_contract.py writes it. '
    'project-docs/specs/project_management_app.md §13.3.';

COMMIT;
