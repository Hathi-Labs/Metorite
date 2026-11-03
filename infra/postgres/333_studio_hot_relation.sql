-- ============================================================================
-- 333_studio_hot_relation.sql — a relation field of a Studio object may be hot
-- (WS-STU C7b).
--
-- What: one wider CHECK on `studio_object_fields` (migration 323).
--       `studio_object_fields_hot_type` gains `relation`. A hot relation field
--       fills one `studio_record_hot` row for each record, with the id in
--       `v_text`. `acb_common/studio_fields.HOT_TYPES` is the list of record.
--       No new table, no new column and no new index: the text index of 323
--       serves the join of a child to its parent.
-- Why:  ai_studio.md §14 C7b. A roll-up reads the child records of one parent
--       through a hot relation field, because a read of `data` is capped at
--       5,000 records and a ledger holds more.
-- Depends on: 323_studio_objects.sql.
--
-- Expand only (R6): one CHECK that allows MORE values. Old code turns no
-- relation field hot, so every row of old code passes. The file drops the old
-- CHECK and adds the wider one under the same name, in one transaction.
-- Idempotent per infra/postgres/README.md: the CHECK is replaced only while
-- its definition does not yet name `relation`. A second run changes nothing,
-- and a replay of 323 leaves this CHECK alone, because 323 creates the table
-- only when it is missing.
-- The ALTER TABLE takes the ACCESS EXCLUSIVE lock of `studio_object_fields`,
-- and the new CHECK reads each row of the table under it. The table is small:
-- an org holds at most 200 objects of 60 fields. So the file sets lock_timeout
-- first, as 323 does, and the runner tries the whole file again on a lock
-- timeout.
--
-- Tenant-scoped (R5): no new table. RLS and the policy of 323 cover the table.
--
-- The number is taken at build time (R1). On 2026-11-03 origin/main held 331,
-- and open pull requests held 310 and 329. Check it again at merge.
-- Pinned by tests/unit/test_studio_objects_r8.py, which finds the latest file
-- that defines the CHECK by CONTENT, never by number (R1).
-- ============================================================================

BEGIN;

SET LOCAL lock_timeout = '5s';

DO $checks$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = to_regclass('studio_object_fields')
           AND conname = 'studio_object_fields_hot_type'
           AND pg_get_constraintdef(oid) LIKE '%relation%'
    ) THEN
        ALTER TABLE studio_object_fields DROP CONSTRAINT IF EXISTS studio_object_fields_hot_type;
        -- The types of studio_fields.HOT_TYPES, and one value for each record.
        ALTER TABLE studio_object_fields
            ADD CONSTRAINT studio_object_fields_hot_type CHECK (
                NOT hot OR (type IN ('text', 'select', 'number', 'money', 'date', 'datetime',
                                     'checkbox', 'email', 'relation')
                            AND (spec -> 'many') IS DISTINCT FROM 'true'::jsonb));
    END IF;
END
$checks$;

COMMIT;
