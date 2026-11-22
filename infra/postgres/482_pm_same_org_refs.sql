-- ============================================================================
-- 482_pm_same_org_refs.sql — a pm_* key that names a row of another tenant
-- is refused, for the 21 keys that had no org guard (Metorite task 3846).
--
-- What: one new check-only function `pm_same_org_ref(ref_table, ref_column)`
--       and 21 attachments of it. The function reads the row that the key
--       names and refuses the write with SQLSTATE 23503 (foreign_key_violation)
--       when it finds no row, or a row of another `organization_id`. It never
--       sets the org of the row. `pm_organization_from_parent()` of 161 and
--       471 stays the one place that derives an org, and this file does not
--       touch it.
-- Why:  A foreign key check ignores RLS. So a session bound to org A could
--       set one of the keys below to the id of a row of org B, and the write
--       passed. Each route loads the row through the visibility helper first,
--       so this is defence in depth (R5: never trust the request). 471 closed
--       the same gap for the 29 attachments of `pm_organization_from_parent`.
--
-- ── The keys that this file guards (21) ─────────────────────────────────────
--
--   child table               column                parent table          on delete
--   pm_projects               archived_root_id      pm_projects           SET NULL
--   pm_tasks                  parent_task_id        pm_tasks              SET NULL
--   pm_tasks                  merged_into_task_id   pm_tasks              SET NULL
--   pm_tasks                  status_id             pm_task_statuses      RESTRICT
--   pm_tasks                  type_id               pm_task_types         SET NULL
--   pm_tasks                  recurrence_id         pm_recurrences        SET NULL
--   pm_activities             parent_id             pm_activities         SET NULL
--   pm_notifications          activity_id           pm_activities         CASCADE
--   pm_task_refs              task_id               pm_tasks              CASCADE
--   pm_agent_runs             task_id               pm_tasks              CASCADE
--   pm_agent_runs             connection_id         agent_connection      SET NULL
--   pm_agent_runs             dispatched_by_run_id  pm_agent_runs         SET NULL
--   pm_agent_runs             parent_run_id         pm_agent_runs         CASCADE
--   pm_agent_run_activities   run_id                pm_agent_runs         CASCADE
--   pm_agent_run_activities   task_id               pm_tasks              CASCADE
--   pm_agent_run_activities   in_reply_to           pm_agent_run_activities SET NULL
--   pm_agent_run_activities   pm_activity_id        pm_activities         SET NULL
--   pm_agent_run_strands      run_id                pm_agent_runs         CASCADE
--   pm_agent_run_strands      task_id               pm_tasks              CASCADE
--   pm_agent_guidance         project_id            pm_projects           CASCADE
--   pm_agent_policies         project_id            pm_projects           CASCADE
--
-- ── The keys that this file leaves out, with the reason ─────────────────────
--
--   * `organization_id` of each pm_* table. It names the org itself.
--   * The 29 attachments of `pm_organization_from_parent` (161, 402 and 471).
--     One of them, `pm_epic_contracts.task_id`, also has its own guard (402).
--   * The four keys of SW-27a (477): `pm_project_sequences.project_id`, and
--     `pm_sequence_reservations.sequence_id`, `.task_id` and `.run_id`. 477
--     attaches `pm_organization_from_parent` to each of them.
--   * The keys that already have a named same-org guard: `pm_cycles.project_id`
--     and `pm_cycle_tasks.task_id` (372), `pm_project_authorities.project_id`
--     and `.page_id` (339), `pm_project_updates.project_id` (383), and
--     `pm_templates.source_project_id` and `pm_template_applies.template_id`
--     and `.project_id` (384).
--   * The composite keys `(organization_id, cycle_id)` of `pm_cycle_tasks`,
--     `pm_cycle_committed` and `pm_cycle_capacity` (372). The org is part of
--     the key, so the row is of the same org by construction.
--   * `wa_commitments.task_id` and `reminders.promoted_task_id` name a task,
--     but they are not pm_* tables. They are open items for their own owners.
--
-- The list came from `pg_constraint` and `pg_trigger` on a database with the
-- ladder applied (2026-11-21). It held 19 keys. SW-107 (472, task 3628) then
-- added `pm_agent_runs.dispatched_by_run_id`, a key to another run of the same
-- table with ON DELETE SET NULL. This file guards it as the twentieth key.
-- Its one writer, `agent_runs.set_run_dispatcher`, reads the named run in the
-- tenant session first. It runs the UPDATE in a savepoint that maps a 23503
-- to the `unknown` drop. So a refusal here drops the value, and the claim
-- still works.
--
-- SW-39 (475, task 3799) then added `pm_agent_runs.parent_run_id`: the open
-- run that a child run works under, with ON DELETE CASCADE. This file guards
-- it as the twenty-first key, with the same function and no special case. A
-- child has no task and no app, so the guard never reads `task_id`. It reads
-- only the parent run, and the parent's `organization_id` must equal the
-- child's. A NULL parent passes, and every run that is not a child has one.
-- The one writer, `agent_runs.start_child_run`, reads and locks the parent in
-- the tenant session first, then inserts one VALUES row. It has no handler
-- for 23503, because RLS already hides a run of another org. No code UPDATEs
-- the key. The fence below reads the same two catalogs on the full ladder,
-- so a later pm_* key with no guard fails it.
--
-- ── The rule (the rule of 471) ──────────────────────────────────────────────
--
-- The function checks only a write that sets the link: an INSERT, or an
-- UPDATE that changes the key or `organization_id`. A NULL key passes. An
-- UPDATE that keeps the link and the org passes with no lookup. That is the
-- path of a cascade: a delete runs `ON DELETE SET NULL` updates, and the row
-- that a sibling key names can already be gone in the same transaction. 471
-- proved that a refusal on each UPDATE breaks the delete of a project, a
-- folder and a pair of tasks. Each attachment also lists its columns
-- (`UPDATE OF <key>, organization_id`), as the guards of 339, 372, 383, 384
-- and 402 do, so an UPDATE of another column does not call the function.
-- The two checks are separate: the column list skips the call, and the body
-- skips a `SET <key> = <key>` of the same value.
--
-- The function compares the org of the named row with `NEW.organization_id`
-- for each role. It does not rely on RLS to hide the row, so a superuser or
-- a BYPASSRLS role gets the same refusal for a row of another org. It is NOT
-- `SECURITY DEFINER`, for the reason of 471: the plain function reads as the
-- caller on each box.
--
-- A row with `organization_id` NULL at this point passes the function. NOT
-- NULL and RLS WITH CHECK then refuse it, as before. Triggers fire in name
-- order, and each name here is `trg_<table>_same_org_<column>`. That sorts
-- after `trg_<table>_org_from_*`, so the 161 attachments fill a NULL org of
-- `pm_projects`, `pm_tasks`, `pm_activities` and `pm_notifications` first.
--
-- A BEFORE ROW trigger cannot see a row that the same statement inserts
-- later. The foreign key could, because it checks at the end of the
-- statement. The write paths read for this file insert one row in each
-- statement, and the row that a key names exists first. The task create
-- (`tasks.py`), the template apply, the file importer (`import_writer.py`)
-- and the recurrence spawn use `core.insert_row`, parents first. The
-- agent-run writes are one VALUES row each. The dispatcher write is an
-- UPDATE of one run that names a run that exists. The strand sweep is an INSERT ...
-- SELECT that reads runs that exist. A data-only restore that fires triggers
-- must load parents first. A full `pg_restore` makes the triggers after it
-- loads the data.
--
-- `pm_agent_guidance` and `pm_agent_policies` refuse each UPDATE of a saved
-- row with their own guard (340, 343 and 404). Their attachment here checks
-- the INSERT in practice.
--
-- The trade-off of 471 stays. A row that already links two organizations
-- stays writable on an UPDATE that does not touch the link. The one-time
-- count of such rows is a production item for the owner. This file changes
-- no data.
--
-- Expand only (R6): a new function and new triggers. No column, constraint,
-- policy or data changes. Each write that the app makes today names rows of
-- the bound org, so it still passes. Idempotent per infra/postgres/README.md:
-- CREATE OR REPLACE FUNCTION and CREATE OR REPLACE TRIGGER (the form of 402).
-- CREATE OR REPLACE TRIGGER takes SHARE ROW EXCLUSIVE, not the ACCESS
-- EXCLUSIVE of a DROP TRIGGER.
--
-- ── One transaction for each statement (no deadlock) ───────────────────────
--
-- The file is 22 blocks: the function first, then one block for each
-- attachment. Each block is `BEGIN; SET LOCAL lock_timeout = '5s'; <one
-- statement>; COMMIT;`. So the file holds the lock of one table at a time. A
-- single transaction held ten table locks at once. An agent activity write
-- takes `pm_agent_run_activities` and then `pm_activities`, in the other
-- order, so the deploy could deadlock (40P01). The runner retries only a lock
-- timeout, so a deadlock stops the release. The runner pipes the file to
-- `psql -v ON_ERROR_STOP=1` with no `--single-transaction`, so the blocks
-- commit in turn. On an error the file stops, and the blocks before it stay
-- committed. Each statement is idempotent, so the retry of the runner and
-- the next deploy run the whole file again with no harm.
--
-- No new table, so no `gen_tenant_migration.EXEMPT` row and no generated
-- policy change. No database connection site (R5).
--
-- The number is taken at build time (R1). The lane booked 472 on the home
-- box on 2026-11-21. SW-107 merged its own 472 first, so this file collided
-- with it. The orchestrator renumbered this file to 476 on 2026-11-22. Then
-- SW-27a (PR 2763, task 861) merged 477 first, so the file became 482 on
-- 2026-11-22. The home box ledger books 482 for task 3846 (PR 2737). Check
-- it again at merge.
--
-- Depends on: 161_projects_tenancy.sql, 471 (the rule), 255, 267, 305, 340,
--   404 (the tables), 472_agent_run_dispatched_by.sql (the twentieth key),
--   475_agent_run_child.sql (the twenty-first key).
-- Fence (R7): tests/unit/test_pm_fk_same_org_r8.py (R8). It probes each
-- attachment as a role that cannot bypass RLS, with two organizations, and
-- runs the cascade deletes over each key. Its test
-- `test_every_pm_key_has_a_guard` reads `pg_constraint` and `pg_trigger`, and
-- fails when a pm_* key has no guard. The file finds this migration by
-- CONTENT, never by number (R1).
-- ============================================================================

BEGIN;
SET LOCAL lock_timeout = '5s';
CREATE OR REPLACE FUNCTION pm_same_org_ref() RETURNS trigger
LANGUAGE plpgsql AS $pm_ref$
DECLARE
    ref_table CONSTANT TEXT := TG_ARGV[0];
    ref_column CONSTANT TEXT := TG_ARGV[1];
    ref_id UUID;
    ref_org UUID;
    ref_rows BIGINT;
BEGIN
    -- `to_jsonb(NEW) ->> …`, because plpgsql has no syntax for "the column
    -- named by this variable" on a record (the idiom of 161 and 471).
    ref_id := (to_jsonb(NEW) ->> ref_column)::uuid;
    IF ref_id IS NULL OR NEW.organization_id IS NULL THEN
        -- No link, or no org yet: NOT NULL and RLS WITH CHECK decide.
        RETURN NEW;
    END IF;

    IF TG_OP = 'UPDATE'
       AND (to_jsonb(OLD) ->> ref_column) IS NOT DISTINCT FROM ref_id::text
       AND NEW.organization_id IS NOT DISTINCT FROM OLD.organization_id THEN
        -- The write keeps the link and the org: the path of a cascade.
        RETURN NEW;
    END IF;

    EXECUTE format('SELECT organization_id FROM public.%I WHERE id = $1', ref_table)
        INTO ref_org USING ref_id;
    GET DIAGNOSTICS ref_rows = ROW_COUNT;

    IF ref_rows = 0 OR ref_org IS DISTINCT FROM NEW.organization_id THEN
        -- No row the caller can read (under FORCE RLS a row of another org,
        -- or no row at all), or a row of another org that a bypass role reads.
        RAISE EXCEPTION '%.% names no % row of this organization',
            TG_TABLE_NAME, ref_column, ref_table
            USING ERRCODE = 'foreign_key_violation';
    END IF;
    RETURN NEW;
END;
$pm_ref$;
COMMIT;

BEGIN;
SET LOCAL lock_timeout = '5s';
CREATE OR REPLACE TRIGGER trg_pm_projects_same_org_archived_root_id
    BEFORE INSERT OR UPDATE OF archived_root_id, organization_id ON pm_projects
    FOR EACH ROW EXECUTE FUNCTION pm_same_org_ref('pm_projects', 'archived_root_id');
COMMIT;

BEGIN;
SET LOCAL lock_timeout = '5s';
CREATE OR REPLACE TRIGGER trg_pm_tasks_same_org_parent_task_id
    BEFORE INSERT OR UPDATE OF parent_task_id, organization_id ON pm_tasks
    FOR EACH ROW EXECUTE FUNCTION pm_same_org_ref('pm_tasks', 'parent_task_id');
COMMIT;

BEGIN;
SET LOCAL lock_timeout = '5s';
CREATE OR REPLACE TRIGGER trg_pm_tasks_same_org_merged_into_task_id
    BEFORE INSERT OR UPDATE OF merged_into_task_id, organization_id ON pm_tasks
    FOR EACH ROW EXECUTE FUNCTION pm_same_org_ref('pm_tasks', 'merged_into_task_id');
COMMIT;

BEGIN;
SET LOCAL lock_timeout = '5s';
CREATE OR REPLACE TRIGGER trg_pm_tasks_same_org_status_id
    BEFORE INSERT OR UPDATE OF status_id, organization_id ON pm_tasks
    FOR EACH ROW EXECUTE FUNCTION pm_same_org_ref('pm_task_statuses', 'status_id');
COMMIT;

BEGIN;
SET LOCAL lock_timeout = '5s';
CREATE OR REPLACE TRIGGER trg_pm_tasks_same_org_type_id
    BEFORE INSERT OR UPDATE OF type_id, organization_id ON pm_tasks
    FOR EACH ROW EXECUTE FUNCTION pm_same_org_ref('pm_task_types', 'type_id');
COMMIT;

BEGIN;
SET LOCAL lock_timeout = '5s';
CREATE OR REPLACE TRIGGER trg_pm_tasks_same_org_recurrence_id
    BEFORE INSERT OR UPDATE OF recurrence_id, organization_id ON pm_tasks
    FOR EACH ROW EXECUTE FUNCTION pm_same_org_ref('pm_recurrences', 'recurrence_id');
COMMIT;

BEGIN;
SET LOCAL lock_timeout = '5s';
CREATE OR REPLACE TRIGGER trg_pm_activities_same_org_parent_id
    BEFORE INSERT OR UPDATE OF parent_id, organization_id ON pm_activities
    FOR EACH ROW EXECUTE FUNCTION pm_same_org_ref('pm_activities', 'parent_id');
COMMIT;

BEGIN;
SET LOCAL lock_timeout = '5s';
CREATE OR REPLACE TRIGGER trg_pm_notifications_same_org_activity_id
    BEFORE INSERT OR UPDATE OF activity_id, organization_id ON pm_notifications
    FOR EACH ROW EXECUTE FUNCTION pm_same_org_ref('pm_activities', 'activity_id');
COMMIT;

BEGIN;
SET LOCAL lock_timeout = '5s';
CREATE OR REPLACE TRIGGER trg_pm_task_refs_same_org_task_id
    BEFORE INSERT OR UPDATE OF task_id, organization_id ON pm_task_refs
    FOR EACH ROW EXECUTE FUNCTION pm_same_org_ref('pm_tasks', 'task_id');
COMMIT;

BEGIN;
SET LOCAL lock_timeout = '5s';
CREATE OR REPLACE TRIGGER trg_pm_agent_runs_same_org_task_id
    BEFORE INSERT OR UPDATE OF task_id, organization_id ON pm_agent_runs
    FOR EACH ROW EXECUTE FUNCTION pm_same_org_ref('pm_tasks', 'task_id');
COMMIT;

BEGIN;
SET LOCAL lock_timeout = '5s';
CREATE OR REPLACE TRIGGER trg_pm_agent_runs_same_org_connection_id
    BEFORE INSERT OR UPDATE OF connection_id, organization_id ON pm_agent_runs
    FOR EACH ROW EXECUTE FUNCTION pm_same_org_ref('agent_connection', 'connection_id');
COMMIT;

BEGIN;
SET LOCAL lock_timeout = '5s';
CREATE OR REPLACE TRIGGER trg_pm_agent_runs_same_org_dispatched_by_run_id
    BEFORE INSERT OR UPDATE OF dispatched_by_run_id, organization_id ON pm_agent_runs
    FOR EACH ROW EXECUTE FUNCTION pm_same_org_ref('pm_agent_runs', 'dispatched_by_run_id');
COMMIT;

BEGIN;
SET LOCAL lock_timeout = '5s';
CREATE OR REPLACE TRIGGER trg_pm_agent_runs_same_org_parent_run_id
    BEFORE INSERT OR UPDATE OF parent_run_id, organization_id ON pm_agent_runs
    FOR EACH ROW EXECUTE FUNCTION pm_same_org_ref('pm_agent_runs', 'parent_run_id');
COMMIT;

BEGIN;
SET LOCAL lock_timeout = '5s';
CREATE OR REPLACE TRIGGER trg_pm_agent_run_activities_same_org_run_id
    BEFORE INSERT OR UPDATE OF run_id, organization_id ON pm_agent_run_activities
    FOR EACH ROW EXECUTE FUNCTION pm_same_org_ref('pm_agent_runs', 'run_id');
COMMIT;

BEGIN;
SET LOCAL lock_timeout = '5s';
CREATE OR REPLACE TRIGGER trg_pm_agent_run_activities_same_org_task_id
    BEFORE INSERT OR UPDATE OF task_id, organization_id ON pm_agent_run_activities
    FOR EACH ROW EXECUTE FUNCTION pm_same_org_ref('pm_tasks', 'task_id');
COMMIT;

BEGIN;
SET LOCAL lock_timeout = '5s';
CREATE OR REPLACE TRIGGER trg_pm_agent_run_activities_same_org_in_reply_to
    BEFORE INSERT OR UPDATE OF in_reply_to, organization_id ON pm_agent_run_activities
    FOR EACH ROW EXECUTE FUNCTION pm_same_org_ref('pm_agent_run_activities', 'in_reply_to');
COMMIT;

BEGIN;
SET LOCAL lock_timeout = '5s';
CREATE OR REPLACE TRIGGER trg_pm_agent_run_activities_same_org_pm_activity_id
    BEFORE INSERT OR UPDATE OF pm_activity_id, organization_id ON pm_agent_run_activities
    FOR EACH ROW EXECUTE FUNCTION pm_same_org_ref('pm_activities', 'pm_activity_id');
COMMIT;

BEGIN;
SET LOCAL lock_timeout = '5s';
CREATE OR REPLACE TRIGGER trg_pm_agent_run_strands_same_org_run_id
    BEFORE INSERT OR UPDATE OF run_id, organization_id ON pm_agent_run_strands
    FOR EACH ROW EXECUTE FUNCTION pm_same_org_ref('pm_agent_runs', 'run_id');
COMMIT;

BEGIN;
SET LOCAL lock_timeout = '5s';
CREATE OR REPLACE TRIGGER trg_pm_agent_run_strands_same_org_task_id
    BEFORE INSERT OR UPDATE OF task_id, organization_id ON pm_agent_run_strands
    FOR EACH ROW EXECUTE FUNCTION pm_same_org_ref('pm_tasks', 'task_id');
COMMIT;

BEGIN;
SET LOCAL lock_timeout = '5s';
CREATE OR REPLACE TRIGGER trg_pm_agent_guidance_same_org_project_id
    BEFORE INSERT OR UPDATE OF project_id, organization_id ON pm_agent_guidance
    FOR EACH ROW EXECUTE FUNCTION pm_same_org_ref('pm_projects', 'project_id');
COMMIT;

BEGIN;
SET LOCAL lock_timeout = '5s';
CREATE OR REPLACE TRIGGER trg_pm_agent_policies_same_org_project_id
    BEFORE INSERT OR UPDATE OF project_id, organization_id ON pm_agent_policies
    FOR EACH ROW EXECUTE FUNCTION pm_same_org_ref('pm_projects', 'project_id');
COMMIT;
