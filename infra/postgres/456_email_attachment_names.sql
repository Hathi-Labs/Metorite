-- ============================================================================
-- 456_email_attachment_names.sql — LOOP-19 (task 3572): attachment file names
-- on the message, so a search can find "the NDA I sent to Mahesh".
--
-- ⚠️ The number was booked with ~/lanes/reserve_migration.sh (R1) and is
--    re-checked at merge. `tests/unit/test_email_search_attachment_names_r8.py`
--    finds this file by CONTENT (`email_attachment_names_fill`), never by number.
--
-- What: one nullable column, `email_messages.attachment_names text`, and one
-- trigger on `email_attachments` that keeps it equal to the file names of the
-- message, joined by a space.
--
-- Why: `project-docs/specs/proactive_loop.md` §4.9.1. `email_attachments` holds
-- the names, one row each. A search by file name must read them inside the one
-- mailbox access seam, as the member. A name on the message row means the
-- search reads the same row that the mailbox predicate already guards, and no
-- second table needs its own tenant and owner check.
--
-- The search reads `COALESCE(em.attachment_names, <the sub-select>)`. A row
-- that no trigger has reached yet gets the sub-select, so the results are the
-- same before, during and after a backfill (the pattern of migration 314).
--
-- ── R6, expand/contract ─────────────────────────────────────────────────────
-- EXPAND only. A nullable column with no default changes only the catalog. Old
-- code ignores it. The trigger fires only on a write to `email_attachments`.
-- The backfill is not here: the sub-select above covers an old row. Idempotent.
-- R5: no new table. The trigger is SECURITY INVOKER, so it writes only the
-- message row that the writing session can already update under RLS.
-- No index: under FORCE RLS the app role cannot use one (docs/rls_and_indexes.md).
-- ============================================================================

ALTER TABLE email_messages ADD COLUMN IF NOT EXISTS attachment_names text;

CREATE OR REPLACE FUNCTION email_attachment_names_fill() RETURNS trigger
LANGUAGE plpgsql AS $fn$
DECLARE
    mid uuid;
BEGIN
    -- The message of the new row, and the message of the old row when an
    -- attachment moved or went away. Each gets its names recomputed once.
    FOR mid IN
        SELECT DISTINCT m FROM (VALUES (NEW.message_id), (OLD.message_id)) v(m)
        WHERE m IS NOT NULL
    LOOP
        UPDATE email_messages em
           SET attachment_names = (
                SELECT string_agg(ea.filename, ' ' ORDER BY ea.filename)
                  FROM email_attachments ea WHERE ea.message_id = mid)
         WHERE em.id = mid;
    END LOOP;
    RETURN NULL;
END
$fn$;

DROP TRIGGER IF EXISTS email_attachment_names_fill ON email_attachments;
CREATE TRIGGER email_attachment_names_fill
    AFTER INSERT OR DELETE OR UPDATE OF filename, message_id
    ON email_attachments
    FOR EACH ROW EXECUTE FUNCTION email_attachment_names_fill();
