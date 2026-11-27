-- ============================================================================
-- 441_studio_saved_views.sql — the views that a member saves on a Studio app
-- (WS-STU AF-10a, task 3158).
--
-- What: one new table. `studio_saved_views` holds one saved view of one
--       object of one package: its name, its owner, the share flag and the
--       spec (a filter list, a sort, a layout and the columns).
--       gateway/studio/views_store.py is the one module that runs SQL on it.
-- Why:  ai_studio.md §14 AF-10a and §5.5. A member saves the filters of a
--       list as a view. A private view reads for its owner only. The owner
--       of the package shares a view with every member who reads the app.
--       This slice is the store and the routes: no front end (AF-10b).
-- Depends on: 130_org_access_control.sql (organization), and the
--             (organization_id, id) key `apps_org_id_key` on `apps`, which
--             323 adds.
--
-- A VIEW HOLDS A QUERY, NEVER DATA. The filter values are what the author
-- typed. The read of a viewer drops each condition, the sort and each column
-- that names a key that the viewer may not read, so a value about such a key
-- never reaches that viewer (views_store.py and the route).
--
-- Expand only (R6): one new table and two indexes. No rename, no drop, and
-- no change of an old object. Idempotent: CREATE TABLE IF NOT EXISTS,
-- CREATE INDEX IF NOT EXISTS, and an RLS block guarded on
-- relforcerowsecurity.
-- Tenant-scoped (R5): `organization_id`, ENABLE and FORCE row level
-- security, and one policy with USING and WITH CHECK, as 438 does.
-- `REFERENCES` comes before `DEFAULT`, because the tenancy ratchet in
-- tests/unit/test_tenancy_boundary.py matches the two with no comma between.
-- The org of a new row comes from the DEFAULT, the bound tenant. The writer
-- never names it.
-- The foreign key to the package is the pair that starts with
-- `organization_id`, because a foreign key check skips RLS. It cascades, so
-- a deleted package takes its views.
--
-- The member purge (gateway/routes/admin/members.py) deletes the private
-- views of the member and keeps the shared views with the address (D63).
--
-- The number is taken at build time (R1): 441 was booked for this task.
-- Check it again at merge. Pinned by tests/unit/test_studio_saved_views.py
-- and tests/unit/test_studio_saved_views_r8.py, which find this file by
-- CONTENT, never by number (R1).
-- ============================================================================

BEGIN;

CREATE TABLE IF NOT EXISTS studio_saved_views (
    organization_id  UUID NOT NULL
                     REFERENCES organization (id) ON DELETE CASCADE
                     DEFAULT current_setting('app.tenant_id', true)::uuid,
    id               UUID NOT NULL DEFAULT gen_random_uuid(),
    -- The package: an `apps` row of the same org.
    package_id       UUID NOT NULL,
    -- The object of the view, by its name in the manifest.
    object_name      TEXT NOT NULL,
    -- The member who made the view, in lower case.
    owner_email      TEXT NOT NULL,
    name             TEXT NOT NULL,
    shared           BOOLEAN NOT NULL DEFAULT false,
    -- {filter, sort, layout, columns}. The route checks the shape.
    spec             JSONB NOT NULL DEFAULT '{}',
    position         INTEGER NOT NULL DEFAULT 0,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (id),
    CONSTRAINT studio_saved_views_package_fk FOREIGN KEY (organization_id, package_id)
        REFERENCES apps (organization_id, id) ON DELETE CASCADE,
    -- The `_NAME` pattern of the A2 manifest, as `studio_objects.name`.
    CONSTRAINT studio_saved_views_object CHECK (object_name ~ '^[a-z][a-z0-9_]{0,47}$'),
    CONSTRAINT studio_saved_views_owner CHECK (
        owner_email <> '' AND owner_email = lower(owner_email)),
    CONSTRAINT studio_saved_views_name CHECK (char_length(name) BETWEEN 1 AND 120),
    CONSTRAINT studio_saved_views_spec CHECK (
        jsonb_typeof(spec) = 'object' AND octet_length(spec::text) <= 8000),
    CONSTRAINT studio_saved_views_position CHECK (position >= 0)
);

-- One name for each member, object and package. `owner_email` is lower case.
CREATE UNIQUE INDEX IF NOT EXISTS studio_saved_views_name_uq
    ON studio_saved_views (organization_id, package_id, object_name, owner_email, lower(name));
-- The read of the views of one object, and the counts of the caps.
CREATE INDEX IF NOT EXISTS studio_saved_views_object_idx
    ON studio_saved_views (organization_id, package_id, object_name);

DO $rls$
DECLARE
    t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY['studio_saved_views'] LOOP
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

COMMENT ON TABLE studio_saved_views IS
    'A view that a member saves on an object of a Studio app (WS-STU AF-10a). '
    'It holds a query, never data. A private view reads for its owner only. '
    'RLS covers the org.';

COMMIT;
