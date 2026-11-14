-- ============================================================================
-- 442_studio_record_files.sql — the file store of a record of a Studio app
-- (WS-STU STU-D13b, task 1421).
--
-- What: one new table. `studio_record_files` holds the bytes of one file that
--       belongs to one record of one object of one package, and the name, the
--       type and the size of the file.
--       gateway/studio/files_store.py is the one module that runs SQL on it.
-- Why:  ai_studio.md §14 D13b. A `file` field of a record names a file by
--       its id. The old `attachments` table has no `organization_id` and no
--       RLS, and its route serves by `user_id`, so Studio does not use it.
--       This table is tenant-scoped, and the serve route
--       (GET /studio/packages/{id}/objects/{name}/records/{rid}/files/{fid})
--       reads it behind the door order of the action door.
-- Depends on: 130_org_access_control.sql (organization), the key
--             `apps_org_id_key` on `apps` and the key
--             `studio_records_object_id_key` on `studio_records`, which 323
--             adds.
--
-- Expand only (R6): one new table and one index. No rename, no drop, and no
-- change of an old object. Idempotent: CREATE TABLE IF NOT EXISTS,
-- CREATE INDEX IF NOT EXISTS, and an RLS block guarded on
-- relforcerowsecurity.
-- Tenant-scoped (R5): `organization_id`, ENABLE and FORCE row level
-- security, and one policy with USING and WITH CHECK, as 441 does.
-- `REFERENCES` comes before `DEFAULT`, because the tenancy ratchet in
-- tests/unit/test_tenancy_boundary.py matches the two with no comma between.
-- The org of a new row comes from the DEFAULT, the bound tenant. The writer
-- never names it.
-- Both foreign keys start with `organization_id`, because a foreign key check
-- skips RLS. The key to the record cascades, so a deleted record takes its
-- files. The key to the package cascades too.
--
-- The CHECKs hold the same caps as the code: 10 MB (MAX_DOCUMENT_PDF_BYTES),
-- a short list of types, and the key grammar of the manifest.
--
-- The number is taken at build time (R1): 442 was booked for this task.
-- Check it again at merge. Pinned by tests/unit/test_studio_record_files.py
-- and tests/unit/test_studio_record_files_r8.py, which find this file by
-- CONTENT, never by number (R1).
-- ============================================================================

BEGIN;

CREATE TABLE IF NOT EXISTS studio_record_files (
    organization_id  UUID NOT NULL
                     REFERENCES organization (id) ON DELETE CASCADE
                     DEFAULT current_setting('app.tenant_id', true)::uuid,
    id               UUID NOT NULL DEFAULT gen_random_uuid(),
    -- The package: an `apps` row of the same org.
    package_id       UUID NOT NULL,
    -- The object and the record that own the file.
    object_id        UUID NOT NULL,
    record_id        UUID NOT NULL,
    -- The key of the `file` field that names this file.
    field_key        TEXT NOT NULL,
    name             TEXT NOT NULL,
    mime             TEXT NOT NULL,
    size_bytes       INTEGER NOT NULL,
    content          BYTEA NOT NULL,
    -- The member who made the file, in lower case.
    created_by       TEXT NOT NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (id),
    CONSTRAINT studio_record_files_package_fk FOREIGN KEY (organization_id, package_id)
        REFERENCES apps (organization_id, id) ON DELETE CASCADE,
    CONSTRAINT studio_record_files_record_fk FOREIGN KEY (organization_id, object_id, record_id)
        REFERENCES studio_records (organization_id, object_id, id) ON DELETE CASCADE,
    -- The `NAME_PATTERN` of the A2 manifest, as the key of a field.
    CONSTRAINT studio_record_files_field_key CHECK (field_key ~ '^[a-z][a-z0-9_]{0,47}$'),
    CONSTRAINT studio_record_files_name CHECK (char_length(name) BETWEEN 1 AND 255),
    CONSTRAINT studio_record_files_mime CHECK (
        mime IN ('application/pdf', 'image/png', 'image/jpeg')),
    -- 10 MB: MAX_DOCUMENT_PDF_BYTES in gateway/pdf_render.py.
    CONSTRAINT studio_record_files_size CHECK (
        size_bytes BETWEEN 0 AND 10000000 AND size_bytes = octet_length(content)),
    CONSTRAINT studio_record_files_created_by CHECK (
        created_by <> '' AND created_by = lower(created_by))
);

-- The read of the files of one record, and the cascade of a record delete.
CREATE INDEX IF NOT EXISTS studio_record_files_record_idx
    ON studio_record_files (organization_id, record_id);

DO $rls$
DECLARE
    t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY['studio_record_files'] LOOP
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

COMMENT ON TABLE studio_record_files IS
    'The bytes of a file that a record of a Studio app holds (WS-STU D13b). '
    'The serve route reads it behind the door order. RLS covers the org.';

COMMIT;
