-- ============================================================================
-- 445_studio_coded_value.sql — the `coded` field type (WS-STU C23a, task 2354).
--
-- What: two wider CHECKs and no table.
--       `studio_object_fields_type` (migration 323) and
--       `package_field_value_field_type` (migration 366) gain `coded`. Each
--       now holds the 24 types of studio_fields.FIELD_TYPES, in that order.
-- Why:  ai_studio.md §14 C23a. A coded field holds {system, code}. The system
--       is a key of the field spec, and the code is text. The value check is
--       pure, so this slice has no list table and no data of a code system.
-- Depends on: 323_studio_objects.sql, 366_package_field_value.sql,
--             444_studio_autonumber.sql (the type list that this file widens).
--
-- Expand only (R6): each CHECK allows MORE values. Old code writes no new
-- type, so every row of old code passes. Idempotent per
-- infra/postgres/README.md: a CHECK is replaced only while its definition does
-- not yet name `coded`. The ALTER TABLE takes an ACCESS EXCLUSIVE lock, so the
-- file sets lock_timeout first, as 422 and 444 do. The hot-type CHECK stays as
-- it is, because a coded field is never hot.
--
-- The number is taken at build time (R1): 445 was booked for this task.
-- Check it again at merge. Pinned by tests/unit/test_studio_objects_r8.py and
-- tests/unit/test_package_field_values.py, which find the latest file that
-- defines each CHECK by CONTENT, never by number (R1).
-- ============================================================================

BEGIN;

SET LOCAL lock_timeout = '5s';

DO $checks$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = to_regclass('studio_object_fields')
           AND conname = 'studio_object_fields_type'
           AND pg_get_constraintdef(oid) LIKE '%''coded''%'
    ) THEN
        ALTER TABLE studio_object_fields DROP CONSTRAINT IF EXISTS studio_object_fields_type;
        -- The types of studio_fields.FIELD_TYPES.
        ALTER TABLE studio_object_fields
            ADD CONSTRAINT studio_object_fields_type CHECK (type IN (
                'text', 'longText', 'number', 'money', 'date', 'datetime', 'checkbox',
                'select', 'multiSelect', 'person', 'relation', 'file', 'url', 'email',
                'phone', 'geo', 'richText', 'rating', 'duration', 'quantity', 'percent',
                'barcode', 'autonumber', 'coded'));
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = to_regclass('package_field_value')
           AND conname = 'package_field_value_field_type'
           AND pg_get_constraintdef(oid) LIKE '%''coded''%'
    ) THEN
        ALTER TABLE package_field_value DROP CONSTRAINT IF EXISTS package_field_value_field_type;
        -- The types of studio_fields.FIELD_TYPES.
        ALTER TABLE package_field_value
            ADD CONSTRAINT package_field_value_field_type CHECK (field_type IN (
                'text', 'longText', 'number', 'money', 'date', 'datetime', 'checkbox',
                'select', 'multiSelect', 'person', 'relation', 'file', 'url', 'email',
                'phone', 'geo', 'richText', 'rating', 'duration', 'quantity', 'percent',
                'barcode', 'autonumber', 'coded'));
    END IF;
END
$checks$;

COMMIT;
