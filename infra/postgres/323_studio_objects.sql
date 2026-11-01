-- ============================================================================
-- 323_studio_objects.sql — the typed object store of AI Studio (WS-STU C1).
--
-- What: four new tables, one trigger and one new key on `apps`.
--       `studio_objects` holds one object of one package (an `apps` row).
--       `studio_object_fields` holds one field of an object: its key, its
--       type, its spec and `hot`. `studio_records` holds one record of an
--       object, as JSONB. `studio_record_hot` is the index side table: one
--       row for each value of a hot field.
--       gateway/studio/objects_store.py is the one writer of all four.
-- Why:  ai_studio.md §3.1 and D146.4. Records live on shared tables with
--       FORCE RLS, and no tenant gets its own DDL. This slice adds the store
--       only: no route, no query (C2), no flag.
-- Depends on: 114_custom_apps.sql (apps), 130_org_access_control.sql
--             (organization).
--
-- Expand only (R6): four new tables, their keys, three indexes, one trigger
-- function and one trigger. On `apps`, one nullable column (only where it is
-- missing) and one UNIQUE key. No rename, no drop, and no change to a row.
-- Old code names no new object.
-- Idempotent per infra/postgres/README.md: the column, the tables and the
-- indexes each take IF NOT EXISTS, a DO block guards the key on
-- pg_constraint, CREATE OR REPLACE FUNCTION, DROP TRIGGER IF EXISTS then
-- CREATE, and the RLS block is guarded on relforcerowsecurity.
--
-- ⚠️ Blocks 1 and 2 are copies of blocks 1 and 2 of 321_package_registry.sql
-- (WS-PKG PKG-4), byte for byte. That pull request is open, and this file
-- must not wait for it. Each block is idempotent, so the two files apply in
-- either order, and the second one does nothing. The key has the same name,
-- `apps_org_id_key`, in both files. 321 explains the ladder column:
-- `generated/01_add_columns.sql` declares `apps.organization_id`, and the
-- ladder never replays `generated/` (H-104). So a fresh ladder database has
-- no column for the key, and the block adds it, as 209 and 223 do. Production
-- has the column. There, the IF NOT EXISTS guard skips the change, but the
-- ALTER TABLE still takes the ACCESS EXCLUSIVE lock of `apps` first. ADD
-- CONSTRAINT builds the index under the same lock. So the file sets
-- lock_timeout first, and the timeout covers both statements. The table is
-- small, and the runner tries the whole file again on a lock timeout.
--
-- Tenant-scoped (R5): each new table has `organization_id` with ENABLE and
-- FORCE row level security and one policy with USING and WITH CHECK, in the
-- shape of 320. `REFERENCES` comes before `DEFAULT`, because the tenancy
-- ratchet in tests/unit/test_tenancy_boundary.py matches the two with no
-- comma between. The org of a new row comes from the DEFAULT, the bound
-- tenant. The writer never names it.
-- Every foreign key is a pair that starts with `organization_id`: an object
-- to its package, a field and a record to their object, a hot row to its
-- record and to its field. A foreign key check skips RLS, so the pair is
-- what keeps each row inside its org. Each one cascades: a deleted package
-- takes its objects, and a deleted object takes its fields, records and hot
-- rows. So no record outlives its package.
--
-- The index budget (D146.4). No tenant gets an index of its own, because
-- that is DDL for each tenant. A hot field fills `studio_record_hot`, and its
-- three fixed indexes serve every org. The writer fills the hot rows in the
-- statement that writes the record, so the two cannot drift. An object holds
-- at most 8 hot fields, and an org at most 24. The trigger
-- `studio_object_fields_hot_budget` refuses a 9th and a 25th, for each role,
-- under the advisory lock of the org that the store takes too. So a second
-- writer waits, counts again, and is refused. The store refuses first, with
-- the same numbers (acb_common/studio_fields.py).
-- `v_text` holds the first 256 characters of a text value, because a btree
-- entry has a size limit. C2 checks a match against `data`.
--
-- The size caps. `data` holds at most 65536 bytes (64 KiB) of JSON text. The
-- store refuses first, with the same number, and it refuses a float that
-- Postgres writes longer (1e-05 becomes 0.00001), so the two counts agree.
-- A spec holds at most 65536 bytes too. The caps of 60 fields for each
-- object, 50 objects for each package and 200 for each org are in the store.
--
-- Records are company data, as a `pm_tasks` row is (D63). The member purge
-- (gateway/routes/admin/members.py) deletes no row of these tables, and an
-- email in a person field stays. tests/unit/test_admin_member_purge.py pins
-- that decision.
--
-- The number is taken at build time (R1). On 2026-11-01 origin/main held 320.
-- An open pull request claimed 321, and slice A3, which is in build at the
-- same time, holds 322. Check it again at merge.
-- Pinned by tests/unit/test_studio_objects_r8.py, which finds this file by
-- CONTENT, never by number (R1).
-- ============================================================================

BEGIN;

SET LOCAL lock_timeout = '5s';

-- ── 1. The tenant column of `apps`, where it is missing ──────────────────────
ALTER TABLE apps
    ADD COLUMN IF NOT EXISTS organization_id UUID
    REFERENCES organization (id) ON DELETE CASCADE
    DEFAULT current_setting('app.tenant_id', true)::uuid;

-- ── 2. The key on `apps`, the target of the install's foreign key ───────────
DO $apps_key$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = 'apps'::regclass
           AND conname = 'apps_org_id_key'
    ) THEN
        ALTER TABLE apps ADD CONSTRAINT apps_org_id_key UNIQUE (organization_id, id);
    END IF;
END
$apps_key$;

-- ── 3. The four tables ──────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS studio_objects (
    organization_id   UUID NOT NULL
                      REFERENCES organization (id) ON DELETE CASCADE
                      DEFAULT current_setting('app.tenant_id', true)::uuid,
    id                UUID NOT NULL DEFAULT gen_random_uuid(),
    -- The package: an `apps` row of the same org.
    package_id        UUID NOT NULL,
    name              TEXT NOT NULL,
    label             TEXT NULL,
    created_by        TEXT NOT NULL,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (id),
    -- The target of the foreign keys of fields and records.
    CONSTRAINT studio_objects_org_id_key UNIQUE (organization_id, id),
    -- One name in each package. The key also serves the count of a package.
    CONSTRAINT studio_objects_name_key UNIQUE (organization_id, package_id, name),
    CONSTRAINT studio_objects_package_fk FOREIGN KEY (organization_id, package_id)
        REFERENCES apps (organization_id, id) ON DELETE CASCADE,
    -- The `_NAME` pattern of the A2 manifest.
    CONSTRAINT studio_objects_name CHECK (name ~ '^[a-z][a-z0-9_]{0,47}$'),
    CONSTRAINT studio_objects_label CHECK (label IS NULL OR char_length(label) <= 120),
    CONSTRAINT studio_objects_created_by CHECK (
        created_by <> '' AND created_by = lower(created_by))
);

CREATE TABLE IF NOT EXISTS studio_object_fields (
    organization_id   UUID NOT NULL
                      REFERENCES organization (id) ON DELETE CASCADE
                      DEFAULT current_setting('app.tenant_id', true)::uuid,
    object_id         UUID NOT NULL,
    key               TEXT NOT NULL,
    type              TEXT NOT NULL,
    -- label, required, many, options and to (studio_fields.FieldSpec).
    spec              JSONB NOT NULL DEFAULT '{}',
    hot               BOOLEAN NOT NULL DEFAULT false,
    -- The order of the fields in a form.
    position          INTEGER NOT NULL DEFAULT 0,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (organization_id, object_id, key),
    CONSTRAINT studio_object_fields_object_fk FOREIGN KEY (organization_id, object_id)
        REFERENCES studio_objects (organization_id, id) ON DELETE CASCADE,
    CONSTRAINT studio_object_fields_key CHECK (key ~ '^[a-z][a-z0-9_]{0,47}$'),
    -- The sixteen types of studio_fields.FIELD_TYPES.
    CONSTRAINT studio_object_fields_type CHECK (type IN (
        'text', 'longText', 'number', 'money', 'date', 'datetime', 'checkbox',
        'select', 'multiSelect', 'person', 'relation', 'file', 'url', 'email',
        'phone', 'geo')),
    CONSTRAINT studio_object_fields_spec_object CHECK (jsonb_typeof(spec) = 'object'),
    CONSTRAINT studio_object_fields_spec_size CHECK (octet_length(spec::text) <= 65536),
    -- The types of studio_fields.HOT_TYPES, and one value for each record.
    CONSTRAINT studio_object_fields_hot_type CHECK (
        NOT hot OR (type IN ('text', 'select', 'number', 'money', 'date', 'datetime',
                             'checkbox', 'email')
                    AND (spec -> 'many') IS DISTINCT FROM 'true'::jsonb)),
    CONSTRAINT studio_object_fields_position CHECK (position >= 0)
);

CREATE TABLE IF NOT EXISTS studio_records (
    organization_id   UUID NOT NULL
                      REFERENCES organization (id) ON DELETE CASCADE
                      DEFAULT current_setting('app.tenant_id', true)::uuid,
    object_id         UUID NOT NULL,
    id                UUID NOT NULL DEFAULT gen_random_uuid(),
    -- The values, under the field keys. The store checks each one.
    data              JSONB NOT NULL,
    created_by        TEXT NOT NULL,
    updated_by        TEXT NOT NULL,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (id),
    -- The target of the hot rows' foreign key. The key also serves the
    -- records of one object.
    CONSTRAINT studio_records_object_id_key UNIQUE (organization_id, object_id, id),
    CONSTRAINT studio_records_object_fk FOREIGN KEY (organization_id, object_id)
        REFERENCES studio_objects (organization_id, id) ON DELETE CASCADE,
    CONSTRAINT studio_records_data_object CHECK (jsonb_typeof(data) = 'object'),
    -- 65536 bytes (64 KiB) of JSON text. The store holds the same number.
    CONSTRAINT studio_records_data_size CHECK (octet_length(data::text) <= 65536),
    CONSTRAINT studio_records_created_by CHECK (
        created_by <> '' AND created_by = lower(created_by)),
    CONSTRAINT studio_records_updated_by CHECK (
        updated_by <> '' AND updated_by = lower(updated_by))
);

CREATE TABLE IF NOT EXISTS studio_record_hot (
    organization_id   UUID NOT NULL
                      REFERENCES organization (id) ON DELETE CASCADE
                      DEFAULT current_setting('app.tenant_id', true)::uuid,
    object_id         UUID NOT NULL,
    record_id         UUID NOT NULL,
    field_key         TEXT NOT NULL,
    v_text            TEXT NULL,
    v_num             NUMERIC NULL,
    v_ts              TIMESTAMPTZ NULL,
    v_bool            BOOLEAN NULL,
    PRIMARY KEY (organization_id, record_id, field_key),
    CONSTRAINT studio_record_hot_record_fk FOREIGN KEY (organization_id, object_id, record_id)
        REFERENCES studio_records (organization_id, object_id, id) ON DELETE CASCADE,
    CONSTRAINT studio_record_hot_field_fk FOREIGN KEY (organization_id, object_id, field_key)
        REFERENCES studio_object_fields (organization_id, object_id, key) ON DELETE CASCADE,
    -- One value column for each row.
    CONSTRAINT studio_record_hot_one_value CHECK (num_nonnulls(v_text, v_num, v_ts, v_bool) = 1),
    CONSTRAINT studio_record_hot_text_size CHECK (v_text IS NULL OR char_length(v_text) <= 256)
);

-- The fixed indexes of the budget. C2 reads them. No partial index and no
-- index for each org.
CREATE INDEX IF NOT EXISTS studio_record_hot_num_idx
    ON studio_record_hot (organization_id, object_id, field_key, v_num);
CREATE INDEX IF NOT EXISTS studio_record_hot_text_idx
    ON studio_record_hot (organization_id, object_id, field_key, v_text);
CREATE INDEX IF NOT EXISTS studio_record_hot_ts_idx
    ON studio_record_hot (organization_id, object_id, field_key, v_ts);

-- ── 4. The hot budget ───────────────────────────────────────────────────────
-- A field that turns hot takes the advisory lock of its org, then counts the
-- hot fields of the org and of the object. A second writer waits on the lock,
-- and its count then sees the first one's row (READ COMMITTED: each statement
-- of a VOLATILE function takes a new snapshot). The store takes the same
-- lock, on the same key, before its own count. Fence:
-- tests/unit/test_studio_objects_r8.py.
CREATE OR REPLACE FUNCTION studio_object_fields_hot_budget() RETURNS trigger
LANGUAGE plpgsql AS $fn$
DECLARE
    org_count integer;
    object_count integer;
BEGIN
    IF TG_OP = 'UPDATE' AND OLD.hot
       AND OLD.organization_id = NEW.organization_id
       AND OLD.object_id = NEW.object_id AND OLD.key = NEW.key THEN
        RETURN NEW;
    END IF;
    PERFORM pg_advisory_xact_lock(
        hashtextextended('studio_objects:' || NEW.organization_id::text, 0));
    SELECT count(*) INTO org_count FROM studio_object_fields
     WHERE organization_id = NEW.organization_id AND hot
       AND NOT (object_id = NEW.object_id AND key = NEW.key);
    IF org_count >= 24 THEN
        RAISE EXCEPTION 'studio_object_fields_hot_budget: an organization holds at most 24 hot fields'
            USING ERRCODE = 'check_violation';
    END IF;
    SELECT count(*) INTO object_count FROM studio_object_fields
     WHERE organization_id = NEW.organization_id AND object_id = NEW.object_id
       AND hot AND key <> NEW.key;
    IF object_count >= 8 THEN
        RAISE EXCEPTION 'studio_object_fields_hot_budget: an object holds at most 8 hot fields'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END
$fn$;

DROP TRIGGER IF EXISTS studio_object_fields_hot_budget ON studio_object_fields;
CREATE TRIGGER studio_object_fields_hot_budget
    BEFORE INSERT OR UPDATE ON studio_object_fields
    FOR EACH ROW WHEN (NEW.hot)
    EXECUTE FUNCTION studio_object_fields_hot_budget();

-- ── 5. Row level security ───────────────────────────────────────────────────
DO $rls$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_class
         WHERE oid = to_regclass('studio_objects')
           AND relrowsecurity
           AND relforcerowsecurity
    ) THEN
        ALTER TABLE studio_objects ENABLE ROW LEVEL SECURITY;
        ALTER TABLE studio_objects FORCE  ROW LEVEL SECURITY;
        DROP POLICY IF EXISTS studio_objects_tenant_isolation ON studio_objects;
        CREATE POLICY studio_objects_tenant_isolation ON studio_objects
            USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
            WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_class
         WHERE oid = to_regclass('studio_object_fields')
           AND relrowsecurity
           AND relforcerowsecurity
    ) THEN
        ALTER TABLE studio_object_fields ENABLE ROW LEVEL SECURITY;
        ALTER TABLE studio_object_fields FORCE  ROW LEVEL SECURITY;
        DROP POLICY IF EXISTS studio_object_fields_tenant_isolation ON studio_object_fields;
        CREATE POLICY studio_object_fields_tenant_isolation ON studio_object_fields
            USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
            WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_class
         WHERE oid = to_regclass('studio_records')
           AND relrowsecurity
           AND relforcerowsecurity
    ) THEN
        ALTER TABLE studio_records ENABLE ROW LEVEL SECURITY;
        ALTER TABLE studio_records FORCE  ROW LEVEL SECURITY;
        DROP POLICY IF EXISTS studio_records_tenant_isolation ON studio_records;
        CREATE POLICY studio_records_tenant_isolation ON studio_records
            USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
            WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_class
         WHERE oid = to_regclass('studio_record_hot')
           AND relrowsecurity
           AND relforcerowsecurity
    ) THEN
        ALTER TABLE studio_record_hot ENABLE ROW LEVEL SECURITY;
        ALTER TABLE studio_record_hot FORCE  ROW LEVEL SECURITY;
        DROP POLICY IF EXISTS studio_record_hot_tenant_isolation ON studio_record_hot;
        CREATE POLICY studio_record_hot_tenant_isolation ON studio_record_hot
            USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
            WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);
    END IF;
END
$rls$;

COMMENT ON TABLE studio_objects IS
    'One object of one Studio package (WS-STU C1). '
    'Only gateway/studio/objects_store.py writes it. project-docs/specs/ai_studio.md §3.1.';
COMMENT ON TABLE studio_object_fields IS
    'One field of a Studio object: key, type, spec and hot (WS-STU C1). '
    'Only gateway/studio/objects_store.py writes it. project-docs/specs/ai_studio.md §3.1.';
COMMENT ON TABLE studio_records IS
    'One record of a Studio object, as JSONB (WS-STU C1). '
    'Only gateway/studio/objects_store.py writes it. project-docs/specs/ai_studio.md §3.1.';
COMMENT ON TABLE studio_record_hot IS
    'The index side table of the hot fields of Studio objects (WS-STU C1). '
    'Only gateway/studio/objects_store.py writes it. project-docs/specs/ai_studio.md §3.1.';

COMMIT;
