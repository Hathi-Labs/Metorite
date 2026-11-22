-- ============================================================================
-- 473_notes_same_org_refs.sql — a notes key that names a row of another
-- tenant is refused, for the 10 keys between the notes tables (Metorite task
-- 3845).
--
-- What: one new check-only function `notes_same_org_ref(ref_table,
--       ref_column)` and 10 attachments of it. The function reads the row
--       that the key names and refuses the write with SQLSTATE 23503
--       (foreign_key_violation) when it finds no row, or a row of another
--       `organization_id`. It never sets the org of the row.
-- Why:  A foreign key check ignores RLS. So a session bound to org A could
--       insert a child row whose parent id is a meeting of org B. The child
--       took org A from the column DEFAULT, and RLS accepted it. Each route
--       loads the meeting under the bound tenant first, so this is defence
--       in depth (R5: never trust the request). 476 (task 3846) closes the
--       same gap for the pm_* keys. This file does not depend on 476, and
--       it does not call `pm_same_org_ref()`.
--
-- ── The keys that this file guards (10) ─────────────────────────────────────
--
--   child table          column        parent table        on delete
--   action_item          meeting_id    meeting             CASCADE
--   copilot_event        meeting_id    meeting             CASCADE
--   copilot_event        session_id    live_session        CASCADE
--   live_session         meeting_id    meeting             CASCADE
--   meeting_bot          meeting_id    meeting             CASCADE
--   meeting_note         meeting_id    meeting             CASCADE (the key)
--   meeting_recording    meeting_id    meeting             CASCADE
--   summary_run          meeting_id    meeting             CASCADE
--   transcript_segment   meeting_id    meeting             CASCADE
--   transcript_segment   recording_id  meeting_recording   SET NULL
--
-- ── The keys that this file leaves out, with the reason ─────────────────────
--
--   * `organization_id` of each notes table. It names the org itself.
--   * `action_item.assignee_id` and `transcript_segment.speaker_person_id`
--     name `person`, and `action_item.resulting_task_id` names the old
--     `task` table (01). They are not notes tables, and no route writes the
--     three keys today. They are open items for their owners.
--   * `meeting`, `notes_glossary` and `copilot_config` have no key to
--     another tenant row.
--
-- The list came from `pg_constraint` and `pg_trigger` on a database with the
-- ladder and the generated phases applied (2026-11-22). The fence below reads
-- the same two catalogs, so a later notes key with no guard fails it.
--
-- ── Why the org is read through jsonb, and why the column list holds only
--    the key ─────────────────────────────────────────────────────────────────
--
-- The notes tables get `organization_id` from the generated phases
-- (`generated/01_add_columns.sql`), and on a fresh ladder those phases run
-- AFTER the numbered files. So the column does not exist when this file runs
-- there. 450 records the same fact for `chat_session`. Two consequences:
--
--   * Each attachment is `BEFORE INSERT OR UPDATE OF <key>`, with no
--     `organization_id` in the list. A column list that names a missing
--     column fails at CREATE TRIGGER. The list also keeps the phase-2
--     backfill safe: it stamps `action_item` and `copilot_event` before
--     `meeting` and `live_session`, and a guard that fired on that UPDATE
--     would find a parent with no org yet.
--   * The body reads the orgs with `to_jsonb(NEW) ->> 'organization_id'`. A
--     static `NEW.organization_id` raises on a table with no such column.
--     With no org yet, the body lets the row by, and NOT NULL and RLS WITH
--     CHECK decide, as before.
--
-- The app role cannot change the org of a notes row: the policy has the org
-- in USING and in WITH CHECK, so the only org that it can write is the bound
-- one. So the column list does not open a path for the app. A superuser that
-- changes only the org of a child is not checked. That is the trade-off of
-- the list.
--
-- ── The rule (the rule of 471 and 476) ──────────────────────────────────────
--
-- The function checks only a write that sets the link: an INSERT, or an
-- UPDATE that names the key in its SET list (`UPDATE OF <key>`). An UPDATE
-- that changes only the org never calls it, and for the app role the
-- WITH CHECK of the org policy refuses that UPDATE. A NULL key passes. An
-- UPDATE that keeps the link and the org passes with no lookup. A cascade
-- runs UPDATEs that the function lets through: the delete of a recording
-- runs `ON DELETE SET NULL` as an UPDATE of its segments, and the NULL key
-- passes. Task 2324 proved that a refusal on each UPDATE breaks a
-- delete, and the R8 file proves it here too (its mutation M2b). The column
-- list skips the call for an UPDATE of another column, and the body skips a
-- `SET <key> = <key>` of the same value. So a row that already links two
-- orgs stays writable on an UPDATE that does not touch the link (M2a).
--
-- The delete of a meeting does not reach the guard. Postgres queues the
-- `SET NULL` of a recording after the `CASCADE` delete of the segments of the
-- same meeting, so that `SET NULL` finds no segment left.
--
-- The function compares the org of the named row with the org of the new row
-- for each role. It does not rely on RLS to hide the row, so a superuser or a
-- BYPASSRLS role gets the same refusal for a row of another org. It is NOT
-- `SECURITY DEFINER`: the plain function reads as the caller, as 471 and 476
-- do. The lookup names the schema (`public.%I`), so a search_path cannot
-- redirect it.
--
-- A BEFORE ROW trigger cannot see a row that the same statement inserts
-- later. The write paths of `gateway/routes/notes/` insert one row in each
-- statement, and the parent exists first: the meeting before its children,
-- and the live session before its copilot events. A data-only restore that
-- fires triggers must load parents first. A full `pg_restore` makes the
-- triggers after it loads the data.
--
-- A BEFORE ROW trigger also runs before RLS WITH CHECK. So an INSERT stamped
-- with org B, bound to org A, that names a parent of org A now gets 23503
-- from this guard and not 42501 from RLS. The row does not land either way.
--
-- The trade-off of 471 stays. A row that already links two organizations
-- stays writable on an UPDATE that does not touch the link. The one-time
-- count of such rows is a production item for the owner. This file changes
-- no data.
--
-- Expand only (R6): a new function and new triggers. No column, constraint,
-- policy or data changes. Each write that the app makes today names rows of
-- the bound org, so it still passes. Idempotent per infra/postgres/README.md:
-- CREATE OR REPLACE FUNCTION and CREATE OR REPLACE TRIGGER. CREATE OR REPLACE
-- TRIGGER takes SHARE ROW EXCLUSIVE, not the ACCESS EXCLUSIVE of a DROP
-- TRIGGER.
--
-- ── One transaction for each statement (no deadlock) ───────────────────────
--
-- The file is 11 blocks: the function first, then one block for each
-- attachment. Each block is `BEGIN; SET LOCAL lock_timeout = '5s'; <one
-- statement>; COMMIT;`. So the file holds the lock of one table at a time,
-- and a live write that takes two notes tables cannot deadlock with it. The
-- runner pipes the file to `psql -v ON_ERROR_STOP=1` with no
-- `--single-transaction`, so the blocks commit in turn. On an error the file
-- stops, and the blocks before it stay committed. Each statement is
-- idempotent, so the retry of the runner and the next deploy run the whole
-- file again with no harm.
--
-- No new table, so no `gen_tenant_migration.EXEMPT` row and no generated
-- policy change. No database connection site (R5).
--
-- The number is taken at build time (R1). The lane booked 473 for task 3845
-- on 2026-11-22. Task 3846 holds 476 (472 went to SW-107). Check it again at
-- merge.
--
-- Depends on: 01_schema.sql, 95, 118, 120, 121 (the tables), and the
--   generated phases for `organization_id`.
-- Fence (R7): tests/unit/test_notes_fk_same_org_r8.py (R8). It probes each
-- attachment as a role that cannot bypass RLS, with two organizations, and
-- runs the cascade deletes. Its test `test_every_notes_key_has_a_guard` reads
-- `pg_constraint` and `pg_trigger`, and fails when a notes key has no guard.
-- The file finds this migration by CONTENT, never by number (R1).
-- ============================================================================

BEGIN;
SET LOCAL lock_timeout = '5s';
CREATE OR REPLACE FUNCTION notes_same_org_ref() RETURNS trigger
LANGUAGE plpgsql AS $notes_ref$
DECLARE
    ref_table CONSTANT TEXT := TG_ARGV[0];
    ref_column CONSTANT TEXT := TG_ARGV[1];
    new_row JSONB := to_jsonb(NEW);
    old_row JSONB;
    ref_id UUID;
    new_org UUID;
    ref_org UUID;
    ref_rows BIGINT;
BEGIN
    -- jsonb, because the generated phases add `organization_id` after the
    -- numbered files on a fresh ladder, and plpgsql has no syntax for "the
    -- column named by this variable" on a record.
    ref_id := (new_row ->> ref_column)::uuid;
    new_org := (new_row ->> 'organization_id')::uuid;
    IF ref_id IS NULL OR new_org IS NULL THEN
        -- No link, or no org yet: NOT NULL and RLS WITH CHECK decide.
        RETURN NEW;
    END IF;

    IF TG_OP = 'UPDATE' THEN
        old_row := to_jsonb(OLD);
        IF (old_row ->> ref_column) IS NOT DISTINCT FROM (new_row ->> ref_column)
           AND (old_row ->> 'organization_id')
               IS NOT DISTINCT FROM (new_row ->> 'organization_id') THEN
            -- The write keeps the link and the org: the path of a cascade.
            RETURN NEW;
        END IF;
    END IF;

    EXECUTE format(
        'SELECT (to_jsonb(p) ->> ''organization_id'')::uuid '
        'FROM public.%I p WHERE p.id = $1', ref_table)
        INTO ref_org USING ref_id;
    GET DIAGNOSTICS ref_rows = ROW_COUNT;

    IF ref_rows = 0 OR ref_org IS DISTINCT FROM new_org THEN
        -- No row the caller can read (under FORCE RLS a row of another org,
        -- or no row at all), or a row of another org that a bypass role reads.
        RAISE EXCEPTION '%.% names no % row of this organization',
            TG_TABLE_NAME, ref_column, ref_table
            USING ERRCODE = 'foreign_key_violation';
    END IF;
    RETURN NEW;
END;
$notes_ref$;
COMMIT;

BEGIN;
SET LOCAL lock_timeout = '5s';
CREATE OR REPLACE TRIGGER trg_action_item_same_org_meeting_id
    BEFORE INSERT OR UPDATE OF meeting_id ON action_item
    FOR EACH ROW EXECUTE FUNCTION notes_same_org_ref('meeting', 'meeting_id');
COMMIT;

BEGIN;
SET LOCAL lock_timeout = '5s';
CREATE OR REPLACE TRIGGER trg_copilot_event_same_org_meeting_id
    BEFORE INSERT OR UPDATE OF meeting_id ON copilot_event
    FOR EACH ROW EXECUTE FUNCTION notes_same_org_ref('meeting', 'meeting_id');
COMMIT;

BEGIN;
SET LOCAL lock_timeout = '5s';
CREATE OR REPLACE TRIGGER trg_copilot_event_same_org_session_id
    BEFORE INSERT OR UPDATE OF session_id ON copilot_event
    FOR EACH ROW EXECUTE FUNCTION notes_same_org_ref('live_session', 'session_id');
COMMIT;

BEGIN;
SET LOCAL lock_timeout = '5s';
CREATE OR REPLACE TRIGGER trg_live_session_same_org_meeting_id
    BEFORE INSERT OR UPDATE OF meeting_id ON live_session
    FOR EACH ROW EXECUTE FUNCTION notes_same_org_ref('meeting', 'meeting_id');
COMMIT;

BEGIN;
SET LOCAL lock_timeout = '5s';
CREATE OR REPLACE TRIGGER trg_meeting_bot_same_org_meeting_id
    BEFORE INSERT OR UPDATE OF meeting_id ON meeting_bot
    FOR EACH ROW EXECUTE FUNCTION notes_same_org_ref('meeting', 'meeting_id');
COMMIT;

BEGIN;
SET LOCAL lock_timeout = '5s';
CREATE OR REPLACE TRIGGER trg_meeting_note_same_org_meeting_id
    BEFORE INSERT OR UPDATE OF meeting_id ON meeting_note
    FOR EACH ROW EXECUTE FUNCTION notes_same_org_ref('meeting', 'meeting_id');
COMMIT;

BEGIN;
SET LOCAL lock_timeout = '5s';
CREATE OR REPLACE TRIGGER trg_meeting_recording_same_org_meeting_id
    BEFORE INSERT OR UPDATE OF meeting_id ON meeting_recording
    FOR EACH ROW EXECUTE FUNCTION notes_same_org_ref('meeting', 'meeting_id');
COMMIT;

BEGIN;
SET LOCAL lock_timeout = '5s';
CREATE OR REPLACE TRIGGER trg_summary_run_same_org_meeting_id
    BEFORE INSERT OR UPDATE OF meeting_id ON summary_run
    FOR EACH ROW EXECUTE FUNCTION notes_same_org_ref('meeting', 'meeting_id');
COMMIT;

BEGIN;
SET LOCAL lock_timeout = '5s';
CREATE OR REPLACE TRIGGER trg_transcript_segment_same_org_meeting_id
    BEFORE INSERT OR UPDATE OF meeting_id ON transcript_segment
    FOR EACH ROW EXECUTE FUNCTION notes_same_org_ref('meeting', 'meeting_id');
COMMIT;

BEGIN;
SET LOCAL lock_timeout = '5s';
CREATE OR REPLACE TRIGGER trg_transcript_segment_same_org_recording_id
    BEFORE INSERT OR UPDATE OF recording_id ON transcript_segment
    FOR EACH ROW EXECUTE FUNCTION notes_same_org_ref('meeting_recording', 'recording_id');
COMMIT;
