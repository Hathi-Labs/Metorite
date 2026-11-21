-- ============================================================================
-- 470_pm_org_from_parent_refuses_hidden_parent.sql — the pm_* tenant trigger
-- refuses a parent that it cannot read (Metorite task 2324).
--
-- What: one CREATE OR REPLACE of the function `pm_organization_from_parent()`
--       of 161_projects_tenancy.sql. The signature, the arguments and each
--       attachment stay the same. One branch changes. The parent column holds
--       an id, the lookup finds no row, and the write sets the link. Then the
--       function now raises SQLSTATE 23503 (foreign_key_violation). The old
--       body returned in silence. A write sets the link when it is an INSERT,
--       or an UPDATE that changes the parent column or `organization_id`.
-- Why:  Under FORCE row level security a parent of another organization is a
--       row that the lookup cannot read. The old body then returned NEW, the
--       child took the bound tenant from its column DEFAULT, and the foreign
--       key passed, because a foreign key check ignores RLS. So a session of
--       org A could store a child row that names a task or a project of
--       org B. Each route loads the parent through the visibility helper
--       first, so this is defence in depth (R5: never trust the request).
--       This one change guards every attachment at once: 29 on 2026-11-21.
--
-- The function is NOT `SECURITY DEFINER`. A definer bypasses RLS only when
-- its owner is a superuser or has BYPASSRLS, and that differs across boxes.
-- The plain function reads the parent as the caller, for each role. A
-- superuser or a BYPASSRLS role sees every parent, so for those roles the
-- function behaves as before. The guards of 339 and 402 are plain for the
-- same reason.
--
-- "No row" and "a row with a NULL organization_id" are two cases.
-- `EXECUTE ... INTO` does not set FOUND, so `GET DIAGNOSTICS ... ROW_COUNT`
-- tells them apart. A row with a NULL organization keeps the old silent
-- return. That row cannot exist today: `organization_id` is NOT NULL on each
-- parent table (`pm_projects`, `pm_tasks`, `pm_views` and `pm_reports`). The
-- branch stays for a later parent table that is not NOT NULL.
--
-- An UPDATE that keeps the link does not raise. It keeps the old silent
-- return. That is the path of a cascade. A delete of a project runs the
-- `ON DELETE SET NULL` updates of seven keys on attached tables, for example
-- `pm_intake.duplicate_of_task_id` and `pm_tasks.type_id`. The parent of that
-- row can already be gone in the same transaction, so the lookup finds no
-- row. A refusal there made the delete of a project, a folder or a pair of
-- tasks fail. The R8 file keeps six such deletes as controls.
--
-- The trade-off. A row that already links two organizations before 470 stays
-- writable on an UPDATE that does not touch the link. The one-time count of
-- such rows is a production item for the owner. This file changes no data.
--
-- A parent id that names no row at all also raises 23503 now. The foreign
-- key of each attachment raised the same SQLSTATE for that row before, at
-- the end of the statement. Each of the 29 parent columns has a plain,
-- NOT DEFERRABLE foreign key, so no write that worked before depends on a
-- parent that comes later in the transaction.
--
-- Expand only (R6): no column, no constraint and no data changes. Old code
-- meets the same function name and the same arguments. Idempotent per
-- infra/postgres/README.md: CREATE OR REPLACE FUNCTION. No trigger is
-- re-created, because a trigger names the function and not its body.
--
-- The number is taken at build time (R1). On 2026-11-21 origin/main held 468
-- and a branch held 469, so the lane booked 470. Check it again at merge.
--
-- Fence (R7): tests/unit/test_pm_org_from_parent_r8.py. It probes each
-- attachment as a role that cannot bypass RLS, with two organizations. Its
-- test `test_the_probes_cover_every_attachment` reads `pg_trigger` and fails
-- when an attachment has no probe. The file finds this migration by CONTENT,
-- never by number (R1).
-- ============================================================================

BEGIN;

CREATE OR REPLACE FUNCTION pm_organization_from_parent() RETURNS trigger
LANGUAGE plpgsql AS $pm_org$
DECLARE
    parent_table CONSTANT TEXT := TG_ARGV[0];
    parent_column CONSTANT TEXT := TG_ARGV[1];
    parent_id UUID;
    parent_org UUID;
    parent_rows BIGINT;
BEGIN
    -- `to_jsonb(NEW) ->> …` rather than a dynamic field reference, because
    -- plpgsql has no syntax for "the column named by this variable" on a record.
    parent_id := (to_jsonb(NEW) ->> parent_column)::uuid;
    IF parent_id IS NULL THEN
        -- A root project, or an activity attached to a project rather than a
        -- task. Its OTHER trigger (or NOT NULL) decides.
        RETURN NEW;
    END IF;

    EXECUTE format('SELECT organization_id FROM %I WHERE id = $1', parent_table)
        INTO parent_org USING parent_id;
    GET DIAGNOSTICS parent_rows = ROW_COUNT;

    IF parent_rows = 0 THEN
        -- The caller cannot read the parent. Under FORCE RLS that is a row of
        -- another organization, or a row that does not exist. Refuse both
        -- with the SQLSTATE of a foreign key, when this write sets the link.
        -- An UPDATE that keeps the link (a cascade SET NULL of another
        -- column) keeps the old silent return. See the header.
        IF TG_OP = 'INSERT'
           OR (to_jsonb(OLD) ->> parent_column) IS DISTINCT FROM parent_id::text
           OR NEW.organization_id IS DISTINCT FROM OLD.organization_id THEN
            RAISE EXCEPTION '%: % % is not a row of this organization',
                TG_TABLE_NAME, parent_table, parent_column
                USING ERRCODE = 'foreign_key_violation';
        END IF;
        RETURN NEW;
    END IF;

    IF parent_org IS NULL THEN
        -- The parent exists with no organization (data from before 161). The
        -- four parent tables are NOT NULL today, so this does not happen.
        RETURN NEW;
    END IF;

    IF NEW.organization_id IS NULL THEN
        NEW.organization_id := parent_org;
    ELSIF NEW.organization_id <> parent_org THEN
        RAISE EXCEPTION
            '%.organization_id (%) does not match %.organization_id (%)',
            TG_TABLE_NAME, NEW.organization_id, parent_table, parent_org
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END;
$pm_org$;

COMMIT;
