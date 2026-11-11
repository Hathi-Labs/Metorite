-- ============================================================================
-- 327_studio_object_constraints.sql — the constraints of a Studio object
-- (WS-STU C1b).
--
-- What: one new column, `studio_objects.constraints`, and its CHECK. It holds
--       the rules of one object as a JSON array. `objects_store.set_constraints`
--       is the one writer, and acb_common/studio_rules.py holds the grammar.
--       A record write of the store reads the column under the org lock.
-- Why:  ai_studio.md §14 C1b. A rule of an object runs on each write. A
--       database constraint cannot hold it, because no tenant gets its own
--       DDL (D146.4).
-- Depends on: 323_studio_objects.sql.
--
-- Expand only (R6): one column, NOT NULL with a constant DEFAULT, so old code
-- writes no value and each old row reads `[]`. Postgres keeps a constant
-- DEFAULT in the catalog and rewrites no row. No rename, no drop and no new
-- table. Old code names no new object.
-- Idempotent per infra/postgres/README.md: ADD COLUMN IF NOT EXISTS carries the
-- CHECK, so a second run skips both.
-- The ALTER TABLE takes the ACCESS EXCLUSIVE lock of `studio_objects`, and the
-- CHECK reads each row of the table under it. The table is small: an org holds
-- at most 200 objects. So the file sets lock_timeout first, as 323 does, and
-- the runner tries the whole file again on a lock timeout.
--
-- Tenant-scoped (R5): no new table. RLS and the policy of 323 cover the column.
--
-- The number is taken at build time (R1). On 2026-11-02 origin/main held 324,
-- and two open pull requests held 325 and 326. Check it again at merge.
-- Pinned by tests/unit/test_studio_objects_batch_r8.py, which finds this file
-- by CONTENT, never by number (R1).
-- ============================================================================

BEGIN;

SET LOCAL lock_timeout = '5s';

ALTER TABLE studio_objects
    ADD COLUMN IF NOT EXISTS constraints JSONB NOT NULL DEFAULT '[]'
    CONSTRAINT studio_objects_constraints_array CHECK (jsonb_typeof(constraints) = 'array');

COMMIT;
