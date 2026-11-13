-- ============================================================================
-- 432_studio_hot_num_f8.sql — WS-STU STU-C2c: a float8 shadow of v_num.
--
-- ⚠️ The number was taken from the directory at BUILD time (R1), and it is
--    re-checked at merge. `tests/unit/test_studio_hot_num_f8.py`
--    finds this file by CONTENT (`studio_record_hot_num_f8_fill`), never by
--    number.
--
-- What: one nullable column `studio_record_hot.v_num_f8 double precision`,
-- one trigger that fills it from `v_num`, and one btree index on
-- `(organization_id, object_id, field_key, v_num_f8)`, and one small partial
-- index for the readiness check.
--
-- Why: `project-docs/specs/ai_studio.md` §14 row C2c and
-- `docs/rls_and_indexes.md`. Under FORCE ROW LEVEL SECURITY the planner uses
-- a clause as an index condition only when each function in it is LEAKPROOF.
-- No `numeric` comparison is leakproof in Postgres 16, so the app role cannot
-- use `studio_record_hot_num_idx` for a number filter. The comparisons of
-- `float8` ARE leakproof. A cast of `v_num` to float8 inside an index
-- expression does not help, because `float8(numeric)` is not leakproof. So
-- the value is stored in a plain column, and the filter tests that column.
-- A superuser could mark a function LEAKPROOF, but a Supabase project has no
-- superuser. A bigint column would overflow.
--
-- float8 is lossy, so the store adds the float8 test IN ADDITION to the exact
-- numeric test, with the non-strict operator (`<=` for `lt`, `>=` for `gt`).
-- The exact test stays as a filter. The results do not change.
--
-- A row from before this migration has a NULL copy, and a NULL copy drops out
-- of a float8 test. So the store honours the flag for an org only when the
-- org has no row with a `v_num` and a NULL copy. The partial index
-- `studio_record_hot_num_f8_todo_idx` holds only such rows. It is empty after
-- the backfill, and the check reads it in microseconds. (An `OR v_num_f8 IS
-- NULL` in the query made the planner skip the main index, because the hot
-- table holds a NULL copy for every row of another type.)
--
-- ── R6, expand/contract ─────────────────────────────────────────────────────
--
-- EXPAND only. The column is nullable with no default, so `ADD COLUMN` changes
-- only the catalog. Old code does not read it, and the trigger fills it for
-- each row that old code writes. The trigger fires on an insert and on an
-- update of `v_num`. The upsert of the store sets `v_num` in its update list,
-- so it fires. The BACKFILL is not here:
-- `scripts/backfill_studio_num_f8.py` fills the old rows in short batches.
-- The backfill sets only `v_num_f8`, so the trigger does not fire.
--
-- The index builds with CONCURRENTLY, so it blocks no write. The runner
-- (`scripts/apply_migrations.sh`) pipes this file to psql with no single
-- transaction, so this file has NO BEGIN. A failed CONCURRENTLY build leaves
-- an INVALID index, and IF NOT EXISTS would then skip it. The DO block drops
-- such an index first, so a second run builds it again.
--
-- R5: the table already has `organization_id` and FORCE ROW LEVEL SECURITY.
-- The column and the index change no policy. No new table. Idempotent.
-- Depends on: 323_studio_objects.sql.
-- ============================================================================

ALTER TABLE studio_record_hot ADD COLUMN IF NOT EXISTS v_num_f8 double precision;

-- SECURITY INVOKER (the default): it writes only the row in hand.
CREATE OR REPLACE FUNCTION studio_record_hot_num_f8_fill() RETURNS trigger
LANGUAGE plpgsql AS $fn$
BEGIN
    -- The cast of a numeric beyond the float8 range raises: above 1.8e308 it
    -- overflows, and below 4.9e-324 it underflows. So a value above 1e300 takes
    -- the infinity of its sign, and a value with |v| < 1e-300 takes 0.0. Each
    -- clamp is a non-decreasing map, so x <= y gives f(x) <= f(y), and a
    -- non-strict float8 test keeps every row that the exact test keeps.
    -- This is out of the API range (the API caps |v| below 1e15 and takes no
    -- exponent), so the clamp guards direct SQL only. Any other value casts.
    NEW.v_num_f8 := CASE
        WHEN NEW.v_num > 1e300 THEN 'Infinity'::float8
        WHEN NEW.v_num < -1e300 THEN '-Infinity'::float8
        WHEN abs(NEW.v_num) < 1e-300 THEN 0.0::float8
        ELSE NEW.v_num::float8
    END;
    RETURN NEW;
END
$fn$;

DROP TRIGGER IF EXISTS studio_record_hot_num_f8_fill ON studio_record_hot;
CREATE TRIGGER studio_record_hot_num_f8_fill
    BEFORE INSERT OR UPDATE OF v_num
    ON studio_record_hot
    FOR EACH ROW EXECUTE FUNCTION studio_record_hot_num_f8_fill();

-- Drop the leftover of a failed CONCURRENTLY build, for each index.
DO $do$
DECLARE
    leftover text;
BEGIN
    FOR leftover IN
        SELECT c.relname FROM pg_index i
        JOIN pg_class c ON c.oid = i.indexrelid
        WHERE c.relname IN ('studio_record_hot_num_f8_idx', 'studio_record_hot_num_f8_todo_idx')
          AND c.relnamespace = current_schema()::regnamespace
          AND NOT i.indisvalid
    LOOP
        EXECUTE format('DROP INDEX %I', leftover);
    END LOOP;
END
$do$;

-- CONCURRENTLY waits for every older transaction in the database, and that
-- wait obeys lock_timeout. The runner sets lock_timeout to 5s, so a long open
-- transaction (an email sync block) could fail the release. Raise it to 10
-- minutes for these statements. It stays finite, so an idle-in-transaction
-- session cannot stall a release forever. psql runs with no transaction, so
-- SET holds for the session.
SET lock_timeout = '10min';
CREATE INDEX CONCURRENTLY IF NOT EXISTS studio_record_hot_num_f8_idx
    ON studio_record_hot (organization_id, object_id, field_key, v_num_f8);
CREATE INDEX CONCURRENTLY IF NOT EXISTS studio_record_hot_num_f8_todo_idx
    ON studio_record_hot (organization_id) WHERE v_num IS NOT NULL AND v_num_f8 IS NULL;
RESET lock_timeout;
