-- ============================================================================
-- 314_email_messages_fts_column.sql — WS-KG KG-3a: a stored search vector.
--
-- ⚠️ The number was taken from the directory at BUILD time (R1), and it is
--    re-checked at merge. `tests/unit/test_email_fts_column_r8.py` finds this
--    file by CONTENT (`email_messages_fts_fill`), never by number.
--
-- What: one nullable column, `email_messages.fts tsvector`, and one trigger
-- that fills it on each insert and on each change of the text it covers.
--
-- Why: `project-docs/specs/company_context.md` §12, row KG-3a, and
-- `docs/rls_and_indexes.md`. Under FORCE ROW LEVEL SECURITY the planner can
-- use a clause as an index condition only when each function in it is
-- LEAKPROOF. `to_tsvector`, `||`, `->>` and `@@` are not. So the GIN index
-- of migration 72 serves no search of the app role, and each search parsed
-- every body of the mailbox. Measured on staging: 2.5 to 5.0 s per COUNT.
-- A stored vector makes the per-row test cheap without an index.
--
-- The search reads `COALESCE(em.fts, <the expression>)`. A row that the
-- backfill has not reached yet gets the old expression, so the results are
-- the same before, during and after the backfill.
--
-- ── The expression ──────────────────────────────────────────────────────────
--
-- It MUST stay the same as `_FTS_VECTOR` in
-- `apps/services/gateway/gateway/routes/email/transport/search.py` and as the
-- index of migration 72: the same functions, the same operand order and the
-- same 'english' config. A drift gives a different result for a backfilled
-- row and for a row that is not. The R8 suite compares the two.
--
-- ── R6, expand/contract ─────────────────────────────────────────────────────
--
-- EXPAND only. The column is nullable with no default, so `ADD COLUMN` changes
-- only the catalog. It does not rewrite the table, and it holds its ACCESS
-- EXCLUSIVE lock for milliseconds. It is NOT a generated column: a STORED
-- generated column rewrites every row under that lock (16.5 s on 20 000 rows
-- on the scratch database). Old code does not read the column, and the
-- trigger fills it for each row that old code writes.
--
-- The BACKFILL is not here. `scripts/backfill_email_fts.py` fills the old
-- rows in short batches, one transaction each, through the tenant seam.
--
-- No GIN index on the column. The app role cannot use one under RLS, for the
-- reason above, and each sync write would pay for it. No new table, so R5
-- has nothing to key. Idempotent: a second run changes nothing.
-- ============================================================================

ALTER TABLE email_messages ADD COLUMN IF NOT EXISTS fts tsvector;

-- Fills NEW.fts on an insert, and on an update that changes the subject, the
-- body or the sender. An update of any other column keeps the stored value.
-- SECURITY INVOKER (the default): it writes only the row in hand.
CREATE OR REPLACE FUNCTION email_messages_fts_fill() RETURNS trigger
LANGUAGE plpgsql AS $fn$
BEGIN
    IF TG_OP = 'INSERT'
       OR NEW.fts IS NULL
       OR NEW.subject IS DISTINCT FROM OLD.subject
       OR NEW.body_text IS DISTINCT FROM OLD.body_text
       OR NEW.from_address IS DISTINCT FROM OLD.from_address THEN
        NEW.fts := to_tsvector('english',
            coalesce(NEW.subject, '') || ' ' ||
            coalesce(NEW.body_text, '') || ' ' ||
            coalesce(NEW.from_address->>'name', '') || ' ' ||
            coalesce(NEW.from_address->>'email', ''));
    END IF;
    RETURN NEW;
END
$fn$;

DROP TRIGGER IF EXISTS email_messages_fts_fill ON email_messages;
CREATE TRIGGER email_messages_fts_fill
    BEFORE INSERT OR UPDATE OF subject, body_text, from_address
    ON email_messages
    FOR EACH ROW EXECUTE FUNCTION email_messages_fts_fill();
