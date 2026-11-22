-- ============================================================================
-- 444_studio_autonumber.sql — the `autonumber` field type: its counter table
-- (WS-STU C15a, task 2345).
--
-- What: two wider CHECKs and one new table.
--       `studio_object_fields_type` (migration 323) and
--       `package_field_value_field_type` (migration 366) gain `autonumber`.
--       `studio_object_counters` holds the last number that the store gave
--       for one autonumber field of one object, for one period. The period
--       is the empty text for a field that never resets, and a day
--       (YYYY-MM-DD) for a field that resets each day.
--       `gateway/studio/objects_store.py` is the one module that runs SQL on it.
-- Why:  ai_studio.md §14 C15a. The store gives each new record the next
--       number in the one writer of a record, with one atomic statement:
--       INSERT ... ON CONFLICT DO UPDATE ... RETURNING. The statement runs
--       in the SAVEPOINT of the batch, so a refused batch rolls the counter
--       back too, and a refusal leaves no gap. A SEQUENCE does not fit: it
--       needs DDL for each object, `nextval` does not roll back, and row
--       level security does not bind it.
-- Depends on: 130_org_access_control.sql (organization), 323_studio_objects.sql
--             (the (organization_id, id) key of studio_objects), 366_package_field_value.sql.
--
-- Expand only (R6): each CHECK allows MORE values, and the table is new. Old
-- code writes no new type, so every row of old code passes. Idempotent per
-- infra/postgres/README.md: a CHECK is replaced only while its definition does
-- not yet name `autonumber`, the table is created only when it is missing,
-- and the RLS block is guarded on relforcerowsecurity. The ALTER TABLE takes
-- an ACCESS EXCLUSIVE lock, so the file sets lock_timeout first, as 422 does.
-- The hot-type CHECK stays as it is, because an autonumber field is never hot.
--
-- Tenant-scoped (R5): `organization_id`, ENABLE and FORCE row level
-- security, and one policy with USING and WITH CHECK, as 441 does.
-- `REFERENCES` comes before `DEFAULT`, because the tenancy ratchet in
-- tests/unit/test_tenancy_boundary.py matches the two with no comma between.
-- The org of a new row comes from the DEFAULT, the bound tenant. The writer
-- never names it. The foreign key to the object is the pair that starts with
-- `organization_id`, because a foreign key check skips RLS. It cascades, so
-- a deleted object takes its counters.
--
-- The primary key is the fence of the number: no UNIQUE index on the value of
-- a record exists, because a daily reset repeats a token by design.
--
-- The number is taken at build time (R1): 444 was booked for this task.
-- Check it again at merge. Pinned by tests/unit/test_studio_autonumber_r8.py,
-- test_studio_objects_r8.py and test_package_field_values.py, which find the
-- latest file that defines each CHECK by CONTENT, never by number (R1).
-- ============================================================================

BEGIN;

SET LOCAL lock_timeout = '5s';

DO $checks$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = to_regclass('studio_object_fields')
           AND conname = 'studio_object_fields_type'
           AND pg_get_constraintdef(oid) LIKE '%autonumber%'
    ) THEN
        ALTER TABLE studio_object_fields DROP CONSTRAINT IF EXISTS studio_object_fields_type;
        -- The types of studio_fields.FIELD_TYPES.
        ALTER TABLE studio_object_fields
            ADD CONSTRAINT studio_object_fields_type CHECK (type IN (
                'text', 'longText', 'number', 'money', 'date', 'datetime', 'checkbox',
                'select', 'multiSelect', 'person', 'relation', 'file', 'url', 'email',
                'phone', 'geo', 'richText', 'rating', 'duration', 'quantity', 'percent',
                'barcode', 'autonumber'));
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = to_regclass('package_field_value')
           AND conname = 'package_field_value_field_type'
           AND pg_get_constraintdef(oid) LIKE '%autonumber%'
    ) THEN
        ALTER TABLE package_field_value DROP CONSTRAINT IF EXISTS package_field_value_field_type;
        -- The types of studio_fields.FIELD_TYPES.
        ALTER TABLE package_field_value
            ADD CONSTRAINT package_field_value_field_type CHECK (field_type IN (
                'text', 'longText', 'number', 'money', 'date', 'datetime', 'checkbox',
                'select', 'multiSelect', 'person', 'relation', 'file', 'url', 'email',
                'phone', 'geo', 'richText', 'rating', 'duration', 'quantity', 'percent',
                'barcode', 'autonumber'));
    END IF;
END
$checks$;

CREATE TABLE IF NOT EXISTS studio_object_counters (
    organization_id  UUID NOT NULL
                     REFERENCES organization (id) ON DELETE CASCADE
                     DEFAULT current_setting('app.tenant_id', true)::uuid,
    object_id        UUID NOT NULL,
    -- The key of the autonumber field, as in `studio_object_fields.key`.
    field_key        TEXT NOT NULL,
    -- '' for a field that never resets. YYYY-MM-DD, a day in the zone of the
    -- org, for a field that resets each day.
    period           TEXT NOT NULL DEFAULT '',
    -- The last number given. The first number is 1.
    last_value       BIGINT NOT NULL,
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (organization_id, object_id, field_key, period),
    CONSTRAINT studio_object_counters_object_fk FOREIGN KEY (organization_id, object_id)
        REFERENCES studio_objects (organization_id, id) ON DELETE CASCADE,
    CONSTRAINT studio_object_counters_key CHECK (field_key ~ '^[a-z][a-z0-9_]{0,47}$'),
    CONSTRAINT studio_object_counters_period CHECK (period = '' OR period ~ '^\d{4}-\d{2}-\d{2}$'),
    -- A number is below 10^15, so a browser holds it exactly.
    CONSTRAINT studio_object_counters_value CHECK (last_value BETWEEN 1 AND 999999999999999)
);

DO $rls$
DECLARE
    t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY['studio_object_counters'] LOOP
        IF NOT EXISTS (
            SELECT 1 FROM pg_class
             WHERE oid = to_regclass(t)
               AND relrowsecurity
               AND relforcerowsecurity
        ) THEN
            EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
            EXECUTE format('ALTER TABLE %I FORCE  ROW LEVEL SECURITY', t);
            EXECUTE format('DROP POLICY IF EXISTS %I ON %I', t || '_tenant_isolation', t);
            EXECUTE format(
                'CREATE POLICY %I ON %I '
                'USING      (organization_id = current_setting(''app.tenant_id'', true)::uuid) '
                'WITH CHECK (organization_id = current_setting(''app.tenant_id'', true)::uuid)',
                t || '_tenant_isolation', t);
        END IF;
    END LOOP;
END
$rls$;

COMMENT ON TABLE studio_object_counters IS
    'The last number of one autonumber field of one Studio object, for one '
    'period (WS-STU C15a). The store writes it in the SAVEPOINT of the record '
    'write. RLS covers the org.';

COMMIT;
