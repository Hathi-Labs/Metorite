-- ============================================================================
-- 303_pm_tasks_fields_at.sql — WS-55 MCP-G7: a comment or a link no longer
--                              breaks a pending task write.
--
-- What: one column, `pm_tasks.fields_at`, and one BEFORE trigger that keeps
--       it. `fields_at` is the `updated_at` of the last write that changed a
--       FIELD of the task row. A write that moves only `updated_at` (the
--       satellite bump, `core.touch_task`) leaves it alone.
-- Why:  `If-Match` and the MCP `version` are `pm_tasks.updated_at` (D-PM-20,
--       spec `project_management_app.md` §9.10). `touch_task` moves that stamp
--       for every comment, link, attachment and assignee, because the delta
--       feed needs it. So a comment on task A, or a `#mention` of A in another
--       task, broke every pending write to A with 412. Measured live on
--       2026-10-25. `core.require_precondition` now also passes a token that
--       is not older than `fields_at`: no field changed since the client read
--       the row. `updated_at` stays the token and the feed stamp. `fields_at`
--       only narrows what a mismatch means (spec §9.10.3).
-- Depends on: 146_projects.sql (`pm_tasks.updated_at`), 161_projects_tenancy.sql
--       (the two BEFORE triggers on `pm_tasks`).
--
-- ── The trigger, and why it is a trigger ───────────────────────────────────
--
-- `pm_tasks` has many writers: `core.update_row`, the status transition, the
-- bulk edit, the recurrence spawn, the merge, the personal lens, the tag and
-- custom-field sweeps. A trigger covers each of them, and a new writer cannot
-- forget it. Its name sorts after `trg_pm_tasks_org_from_*`, so it runs last of
-- the BEFORE triggers and sees the final row.
--
-- On UPDATE, the trigger fires only when the statement SETs `updated_at`
-- (`UPDATE OF updated_at`), whatever the value:
--
--   1. The stamp is monotonic. `now()` is the START of the transaction, so a
--      writer that waited on the row lock would stamp a time OLDER than the
--      stamp it overwrites, and older than a token a client already holds.
--      Then that client would pass the `fields_at` arm across a real change.
--      So a `now()` stamp below the old stamp becomes the old stamp plus one
--      microsecond. A stamp EQUAL to the old one is kept: it is this
--      transaction's own earlier stamp, so the stamp of one request stays one
--      instant. A writer that sets an explicit or older time (a test fixture, a
--      repair) keeps the value it wrote.
--   2. If any column other than `updated_at` and `fields_at` differs, then
--      `fields_at` takes the new `updated_at`.
--
-- ⚠️ Why `UPDATE OF updated_at` and not "the value moved". One request often
-- bumps first and writes a field second (the claim adds the assignee, then
-- moves the status). Both statements stamp the same `now()`, so the second one
-- does NOT move `updated_at`. A value test skipped it, and the claim race of
-- `test_mcp_edge_claim.py` then gave two 200s for one version. Measured
-- 2026-10-25.
--
-- A write that does not SET `updated_at` (`touch=False`, the tag rename, the
-- status remap) does not fire the trigger and does not move `fields_at`. Such
-- a write never changed the token before this file, so it must not start to
-- break a pending edit now. On INSERT, `fields_at` takes `updated_at`.
--
-- `SECURITY DEFINER` is not used. The trigger runs as the writer, and it reads
-- no other table.
--
-- ── R6, R5 and R1 ──────────────────────────────────────────────────────────
--
-- Expand only. The column is nullable with no default, so the ADD is a catalog
-- change. The old gateway runs against this schema for a moment during the
-- deploy. It never names `fields_at`, and the trigger fills the column for each
-- of its writes. `row_to_model` maps by model field, so no reply carries the
-- column. The backfill sets `fields_at = updated_at`, which keeps the old rule
-- exact for every row: a token passes if and only if it equals `updated_at`.
-- No contract phase is owed. The code treats NULL as "no narrowing".
--
-- R5: a column on a table that already carries `organization_id` and its
-- policies. No new table, policy or exemption.
--
-- The number was taken at build time (R1). On 2026-10-25 main held 301, and
-- open branches held 300 (`kc-1a-drive`) and 302 (`kb-ai-4-verified`). Check
-- it again at merge.
--
-- Idempotent: ADD COLUMN IF NOT EXISTS, CREATE OR REPLACE FUNCTION/TRIGGER,
-- and a backfill whose WHERE matches nothing on the second run.
--
-- Fences: `tests/unit/test_task_precondition_fields_at.py` (R8: a link and a
-- comment keep the old token good, a field change still gives 412, and the
-- trigger leaves `fields_at` alone on a bump) and
-- `tests/unit/test_projects_precondition.py` (the pure rule).
-- ============================================================================

SET lock_timeout = '5s';

BEGIN;

ALTER TABLE pm_tasks ADD COLUMN IF NOT EXISTS fields_at TIMESTAMPTZ;

COMMENT ON COLUMN pm_tasks.fields_at IS
    'The updated_at of the last write that changed a field of this row. '
    'A satellite bump (core.touch_task) leaves it. Read only by '
    'core.require_precondition (WS-55 MCP-G7). Kept by trg_pm_tasks_stamp_fields.';

CREATE OR REPLACE FUNCTION pm_tasks_stamp_fields() RETURNS trigger
LANGUAGE plpgsql AS $pm_fields$
BEGIN
    IF TG_OP = 'INSERT' THEN
        NEW.fields_at := NEW.updated_at;
        RETURN NEW;
    END IF;

    -- The statement SETs updated_at (`UPDATE OF updated_at` below).
    -- 1. Monotonic, for the `now()` idiom only (see the header). An equal old
    --    stamp is this transaction's own, and it stays.
    IF NEW.updated_at = now() AND OLD.updated_at > NEW.updated_at THEN
        NEW.updated_at := OLD.updated_at + interval '1 microsecond';
    END IF;

    -- 2. A field changed. `to_jsonb` compares every column type, where a
    --    record comparison needs an equality operator for each one.
    IF (to_jsonb(NEW) - 'updated_at' - 'fields_at')
       IS DISTINCT FROM (to_jsonb(OLD) - 'updated_at' - 'fields_at') THEN
        NEW.fields_at := NEW.updated_at;
    END IF;
    RETURN NEW;
END;
$pm_fields$;

CREATE OR REPLACE TRIGGER trg_pm_tasks_stamp_fields
    BEFORE INSERT OR UPDATE OF updated_at ON pm_tasks
    FOR EACH ROW EXECUTE FUNCTION pm_tasks_stamp_fields();

-- The backfill. It does not SET `updated_at`, so the trigger does not fire.
UPDATE pm_tasks SET fields_at = updated_at WHERE fields_at IS NULL;

COMMIT;
