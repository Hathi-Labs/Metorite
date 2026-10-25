-- ============================================================================
-- 304_pm_tasks_fields_at.sql — WS-55 MCP-G7: a comment or a link no longer
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
-- The trigger fires on INSERT, where `fields_at` takes `updated_at`, and on
-- EVERY UPDATE:
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
--   2. If no column other than `updated_at` and `fields_at` differs, the
--      trigger stops there. That is the satellite bump (`touch_task`) and a
--      write of the same values. `fields_at` keeps its value. (The backfill
--      runs before the trigger exists. On a replay it matches no row.)
--   3. A field changed, and the row carries this transaction's stamp:
--      `updated_at` moved, or it equals `now()`. Then `fields_at` takes the
--      new `updated_at`.
--   4. A field changed, and `updated_at` did not move. That is a sweep with
--      no stamp: the tag rename and the tag delete, the custom-field delete,
--      the status remap and the status delete, the recurrence detach, the
--      root move of `tree.py`, `touch=False`, and an FK `ON DELETE SET NULL`
--      (`type_id`, `parent_task_id`). Then `fields_at` takes
--      GREATEST(`now()`, old stamp plus one microsecond, old `fields_at`),
--      and `updated_at` stays. `fields_at` above `updated_at` means "equality
--      only". A token equal to the unmoved `updated_at` still passes, as
--      before this file. After a later bump, that token is below `fields_at`
--      and gets 412.
--      ADVISORY (R7): the old `fields_at` term only keeps `fields_at`
--      monotonic. It is an equivalent mutant, because the precondition reads
--      no difference without it, and no test fences it.
--
-- ⚠️ Why "equals `now()`" counts as stamped in step 3. One request often
-- bumps first and writes a field second (the claim adds the assignee, then
-- moves the status). Both statements stamp the same `now()`, so the second
-- one does NOT move `updated_at`. A test of "the value moved" alone treated
-- it as a sweep. The first draft (`UPDATE OF updated_at` with that test)
-- gave two 200s for one version in the claim race of `test_mcp_edge_claim.py`.
-- A sweep that comes AFTER a bump in one transaction also counts as stamped,
-- which is right: the row's token is then this transaction's stamp.
--
-- ⚠️ Why every UPDATE and not `UPDATE OF updated_at` (fix round 2). A sweep
-- does not SET `updated_at`. Under `UPDATE OF updated_at` it did not fire the
-- trigger, so `fields_at` stayed behind. A later comment then moved the token,
-- and a token from before the sweep passed. Main gave it 412. Fence:
-- `test_a_tag_rename_then_a_comment_gives_412`.
--
-- The promote paths (`core.lift_subtasks_to_grandparent`, the two UPDATEs of
-- `merge._reparent_children`, the kept subtasks of `tasks.delete_task_in`)
-- stamp `updated_at` in the same statement. Step 4 now covers an unstamped
-- move too, so the stamps are defence in depth. Each path has an R8 case in
-- `test_task_precondition_fields_at.py` (M5 to M8), and each case goes red
-- when its stamp AND step 4 are both removed.
--
-- An FK cascade fires this trigger too. The function reads no table, so row
-- security cannot refuse it, whoever runs the cascade.
--
-- ⚠️ Known limit: the lift pins a FUTURE stamp. After a restore or a clock
-- skew leaves `updated_at` ahead of the clock, each later `now()` write moves
-- it by one microsecond only, and `SET updated_at = now()` is lifted too. The
-- repair is an explicit time that is not `now()`, written to both columns:
--     UPDATE pm_tasks
--        SET updated_at = now() - interval '1 microsecond',
--            fields_at  = now() - interval '1 microsecond'
--      WHERE updated_at > now();
-- The trigger keeps an explicit time as written, and it compares no field, so
-- `fields_at` keeps the value the repair wrote. A client that holds a token
-- from before the repair then gets 412 once, and reads the task again.
-- Fence: `test_a_future_stamp_is_repaired_by_an_explicit_time`.
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
-- column. The backfill sets `fields_at = updated_at` on each existing row, and
-- it does not SET `updated_at`. No contract phase is owed. The code treats
-- NULL as "no narrowing", so a row the backfill missed keeps the old rule: a
-- token passes only when it equals `updated_at`.
--
-- ── FORCE RLS, and why the backfill loops per tenant ───────────────────────
--
-- `pm_tasks` carries FORCE ROW LEVEL SECURITY once `generated/04` has run. The
-- production runner connects as the table owner, which is not a superuser,
-- and FORCE binds the owner too. With no tenant bound, the policy shows that
-- role NO row, so a bare UPDATE backfills nothing and every existing task keeps
-- a NULL `fields_at`. 221 and 297 record the same trap. So the backfill runs
-- twice, in the shape of 297, and BEFORE the trigger is created, in the same
-- transaction. Once with no tenant bound, which serves the
-- ladder shape that has no policy yet. Then, only when the table has row
-- security, it binds each tenant in turn with a local
-- `set_config('app.tenant_id', ...)` and runs the UPDATE for that tenant.
-- `organization` has no policy, so the loop can read it. Fence:
-- `test_task_fields_at_backfill_r8.py`, which runs this file as a
-- NOSUPERUSER NOBYPASSRLS owner of `pm_tasks` under FORCE.
--
-- R5: a column on a table that already carries `organization_id` and its
-- policies. No new table, policy or exemption.
--
-- The number was taken at build time (R1), and taken again on 2026-10-25:
-- this file was 303 until `303_kb_page_verification.sql` (#1316) merged first.
-- Then main held 301 and 303, and open branches held 300 (`kc-1a-drive`) and
-- 302 (`sw7-session-key`). Check it again at merge.
--
-- Idempotent: ADD COLUMN IF NOT EXISTS, CREATE OR REPLACE FUNCTION/TRIGGER,
-- and a backfill whose WHERE matches nothing on the second run, in each pass.
--
-- Fences: `tests/unit/test_task_precondition_fields_at.py` (R8: a link and a
-- comment keep the old token good, a change of each user-visible column still
-- gives 412, a sweep then a comment gives 412, each promote path gives 412,
-- and a bump leaves `fields_at`), `tests/unit/test_task_fields_at_backfill_r8.py`
-- (R8: the backfill under FORCE, run by a NOSUPERUSER NOBYPASSRLS owner) and
-- `tests/unit/test_projects_precondition.py` (the pure rule).
-- ============================================================================

SET lock_timeout = '5s';

BEGIN;

ALTER TABLE pm_tasks ADD COLUMN IF NOT EXISTS fields_at TIMESTAMPTZ;

COMMENT ON COLUMN pm_tasks.fields_at IS
    'The updated_at of the last write that changed a field of this row. A '
    'satellite bump (core.touch_task) leaves it. A field write that did not move '
    'updated_at sets it above updated_at, which means equality only. Read only by '
    'core.precondition_holds (WS-55 MCP-G7). Kept by trg_pm_tasks_stamp_fields.';

-- The backfill, in the shape of 297 (see the header). It runs BEFORE the
-- trigger exists. The ACCESS EXCLUSIVE lock of the ADD COLUMN above holds until
-- COMMIT, so no writer comes between the backfill and the trigger. It does not
-- SET `updated_at`, so it needs no trigger, and without one a big table fills
-- about five times faster (measured: 50k rows with a 2 KB description). On a
-- replay the trigger exists, and the WHERE matches no row.
DO $pm_fill$
DECLARE
    org uuid;
BEGIN
    -- No tenant bound. This pass serves a database with no policy on the
    -- table. Under the policy, `current_setting` is NULL and it sees no row.
    UPDATE pm_tasks SET fields_at = updated_at WHERE fields_at IS NULL;

    -- Only when the table has row security. A replay of the ladder runs in
    -- one transaction and has no policy yet. A bind there would stay set for
    -- the rest of that transaction and then read as '' in the next one.
    IF EXISTS (SELECT 1 FROM pg_class
                WHERE oid = 'pm_tasks'::regclass AND relrowsecurity) THEN
        FOR org IN SELECT id FROM organization LOOP
            PERFORM set_config('app.tenant_id', org::text, true);
            UPDATE pm_tasks SET fields_at = updated_at WHERE fields_at IS NULL;
        END LOOP;
    END IF;
END $pm_fill$;

CREATE OR REPLACE FUNCTION pm_tasks_stamp_fields() RETURNS trigger
LANGUAGE plpgsql AS $pm_fields$
BEGIN
    IF TG_OP = 'INSERT' THEN
        NEW.fields_at := NEW.updated_at;
        RETURN NEW;
    END IF;

    -- 1. Monotonic, for the `now()` idiom only (see the header). An equal old
    --    stamp is this transaction's own, and it stays.
    IF NEW.updated_at = now() AND OLD.updated_at > NEW.updated_at THEN
        NEW.updated_at := OLD.updated_at + interval '1 microsecond';
    END IF;

    -- 2. No field changed: a bump, the backfill, or the same values. `to_jsonb`
    --    compares every column type, where a record comparison needs an
    --    equality operator for each one.
    IF (to_jsonb(NEW) - 'updated_at' - 'fields_at')
       IS NOT DISTINCT FROM (to_jsonb(OLD) - 'updated_at' - 'fields_at') THEN
        RETURN NEW;
    END IF;

    IF NEW.updated_at IS DISTINCT FROM OLD.updated_at OR NEW.updated_at = now() THEN
        -- 3. A field changed, with this transaction's stamp.
        NEW.fields_at := NEW.updated_at;
    ELSE
        -- 4. A field changed, and the stamp did not move: equality only.
        NEW.fields_at := GREATEST(
            now(), OLD.updated_at + interval '1 microsecond', OLD.fields_at
        );
    END IF;
    RETURN NEW;
END;
$pm_fields$;

CREATE OR REPLACE TRIGGER trg_pm_tasks_stamp_fields
    BEFORE INSERT OR UPDATE ON pm_tasks
    FOR EACH ROW EXECUTE FUNCTION pm_tasks_stamp_fields();

COMMIT;
