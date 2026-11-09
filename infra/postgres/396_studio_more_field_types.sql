-- ============================================================================
-- 396_studio_more_field_types.sql — six more Studio field types (WS-STU C14).
--
-- What: two wider CHECKs. `studio_object_fields_type` (migration 323) and
--       `package_field_value_field_type` (migration 366) gain `richText`,
--       `rating`, `duration`, `quantity`, `percent` and `barcode`.
--       `acb_common/studio_fields.FIELD_TYPES` is the list of record. No new
--       table, no new column and no new index. None of the six may be hot, so
--       the hot-type CHECK stays as it is.
-- Why:  ai_studio.md §14 C14. The value check of each type is in
--       `studio_fields.py`. The spec of a field holds `min`, `max`, `units`
--       and `format` in its existing JSONB column.
-- Depends on: 323_studio_objects.sql, 366_package_field_value.sql.
--
-- Expand only (R6): each CHECK allows MORE values. Old code writes no new
-- type, so every row of old code passes. The file drops the old CHECK and adds
-- the wider one under the same name, in one transaction. Idempotent per
-- infra/postgres/README.md: a CHECK is replaced only while its definition does
-- not yet name `barcode`. A replay of 323 or 366 leaves these CHECKs alone,
-- because those files create the tables only when they are missing.
-- The ALTER TABLE takes an ACCESS EXCLUSIVE lock, and the new CHECK reads each
-- row under it. The file sets lock_timeout first, as 323 does, and the runner
-- tries the whole file again on a lock timeout.
--
-- Tenant-scoped (R5): no new table. RLS and the policies of 323 and 366 cover
-- both tables.
--
-- The number is taken at build time (R1). On 2026-11-09 origin/main held 390, and open
-- branches held up to 394. This file takes 396. Check it again at merge.
-- Pinned by tests/unit/test_studio_objects_r8.py and test_package_field_values.py,
-- which find the latest file that defines each CHECK by CONTENT (R1).
-- ============================================================================

BEGIN;

SET LOCAL lock_timeout = '5s';

DO $checks$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = to_regclass('studio_object_fields')
           AND conname = 'studio_object_fields_type'
           AND pg_get_constraintdef(oid) LIKE '%barcode%'
    ) THEN
        ALTER TABLE studio_object_fields DROP CONSTRAINT IF EXISTS studio_object_fields_type;
        -- The types of studio_fields.FIELD_TYPES.
        ALTER TABLE studio_object_fields
            ADD CONSTRAINT studio_object_fields_type CHECK (type IN (
                'text', 'longText', 'number', 'money', 'date', 'datetime', 'checkbox',
                'select', 'multiSelect', 'person', 'relation', 'file', 'url', 'email',
                'phone', 'geo', 'richText', 'rating', 'duration', 'quantity', 'percent',
                'barcode'));
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = to_regclass('package_field_value')
           AND conname = 'package_field_value_field_type'
           AND pg_get_constraintdef(oid) LIKE '%barcode%'
    ) THEN
        ALTER TABLE package_field_value DROP CONSTRAINT IF EXISTS package_field_value_field_type;
        -- The types of studio_fields.FIELD_TYPES.
        ALTER TABLE package_field_value
            ADD CONSTRAINT package_field_value_field_type CHECK (field_type IN (
                'text', 'longText', 'number', 'money', 'date', 'datetime', 'checkbox',
                'select', 'multiSelect', 'person', 'relation', 'file', 'url', 'email',
                'phone', 'geo', 'richText', 'rating', 'duration', 'quantity', 'percent',
                'barcode'));
    END IF;
END
$checks$;

COMMIT;
