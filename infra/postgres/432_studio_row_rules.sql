-- ============================================================================
-- 432_studio_row_rules.sql — app roles and row rules of a Studio object
-- (WS-STU C8a).
--
-- What: two expand-only changes, and no new table.
--   1. `studio_object_fields_hot_type` (migrations 323 and 338) gains
--      `person`. A hot person field fills one `studio_record_hot` row for each
--      record, with the lower-case address in `v_text`.
--      `acb_common/studio_fields.HOT_TYPES` is the list of record.
--   2. `studio_objects.access` is a nullable JSONB with no default: the grant
--      of each role of the app on the object. NULL means an open object, as
--      every object was before C8a. The CHECK `studio_objects_access_shape`
--      holds a list of 1 to 16 grants of at most 16 KiB. Only the sync of a
--      publish (`gateway/studio/objects_store.set_access`) writes it, so a row
--      rule binds the published manifest and never the draft.
--      `acb_common/studio_access.py` is the one grammar.
-- Why:  ai_studio.md §19.7 and the §14 C8a row. A row rule `<person field> =
--       $me` reads the hot row of a person field, so the field must be hot.
-- Depends on: 323_studio_objects.sql, 338_studio_hot_relation.sql.
--
-- Expand only (R6): a CHECK that allows MORE values, and a nullable column
-- with no default. Old code turns no person field hot, and it never reads or
-- writes `access`, so every row of old code passes. Idempotent per
-- infra/postgres/README.md: the hot CHECK is replaced only while its
-- definition does not name `person`, `ADD COLUMN IF NOT EXISTS` skips a column
-- that exists, and the shape CHECK goes in only while `pg_constraint` lacks its
-- name. A replay of 323 or 338 leaves both CHECKs alone.
-- The ALTER TABLE takes the ACCESS EXCLUSIVE lock of each table, and a new
-- CHECK reads each row under it. Both tables are small: an org holds at most
-- 200 objects of 60 fields. So the file sets lock_timeout first, as 338 does,
-- and the runner tries the whole file again on a lock timeout.
--
-- Tenant-scoped (R5): no new table. RLS and the policy of 323 cover both
-- tables, and `access` holds no tenant id.
--
-- R1 re-check 2026-11-13: origin/main held up to 430, and 431 (2209) is ahead of this file, so this file is 432.
-- The number is taken at build time (R1). On 2026-11-11 origin/main held 401,
-- and open branches held 396 and 398 to 408 (405 to 408 came during the
-- build). Check it again at merge. Pinned by tests/unit/test_studio_objects_r8.py and
-- test_studio_row_rules_r8.py, which find the latest file that defines each
-- CHECK by CONTENT, never by number (R1).
-- ============================================================================

BEGIN;

SET LOCAL lock_timeout = '5s';

DO $checks$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = to_regclass('studio_object_fields')
           AND conname = 'studio_object_fields_hot_type'
           AND pg_get_constraintdef(oid) LIKE '%person%'
    ) THEN
        ALTER TABLE studio_object_fields DROP CONSTRAINT IF EXISTS studio_object_fields_hot_type;
        -- The types of studio_fields.HOT_TYPES, and one value for each record.
        ALTER TABLE studio_object_fields
            ADD CONSTRAINT studio_object_fields_hot_type CHECK (
                NOT hot OR (type IN ('text', 'select', 'number', 'money', 'date', 'datetime',
                                     'checkbox', 'email', 'relation', 'person')
                            AND (spec -> 'many') IS DISTINCT FROM 'true'::jsonb));
    END IF;
END
$checks$;

ALTER TABLE studio_objects ADD COLUMN IF NOT EXISTS access JSONB;

DO $access$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = to_regclass('studio_objects')
           AND conname = 'studio_objects_access_shape'
    ) THEN
        -- studio_access.MAX_ROLES grants at most, and 16 KiB of JSON text. The
        -- CASE runs jsonb_array_length on an array only, because AND does not
        -- fix an order and the function raises on a scalar.
        ALTER TABLE studio_objects
            ADD CONSTRAINT studio_objects_access_shape CHECK (
                access IS NULL
                OR CASE WHEN jsonb_typeof(access) = 'array'
                        THEN jsonb_array_length(access) BETWEEN 1 AND 16
                             AND octet_length(access::text) <= 16384
                        ELSE false END);
    END IF;
END
$access$;

COMMIT;
