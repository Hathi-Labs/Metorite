-- ============================================================================
-- 356_package_field_value.sql — the values of package extension fields
-- (WS-PKG PKG-13, Metorite task 1251).
--
-- What: one new table. `package_field_value` holds one value of one extension
--       field on one host record. A package declares the field as a claim at
--       the point `fields` (package_host.md §4 and §8.3). The value belongs to
--       the install that declares the field.
-- Why:  package_host.md §5.1 and §8.3. A field extension is a row, never a
--       column (§0 rule 7). The one writer is
--       apps/services/gateway/gateway/package_host/field_values.py. This
--       slice adds no route, no UI and no flag. PKG-14 adds the host door.
-- Depends on: 324_package_registry.sql (package_install),
--             130_org_access_control.sql (organization).
--
-- Expand only (R6): one new table, its keys and one index. No change to an
-- existing table, no rename, no drop, and no change to a row. Old code names
-- no new object.
-- Idempotent per infra/postgres/README.md: the table and the index each take
-- IF NOT EXISTS, and the RLS block is guarded on relforcerowsecurity.
--
-- Tenant-scoped (R5): `organization_id` with ENABLE and FORCE row level
-- security and one policy with USING and WITH CHECK, in the shape of 324.
-- `REFERENCES` comes before `DEFAULT`, because the tenancy ratchet in
-- tests/unit/test_tenancy_boundary.py matches the two with no comma between.
-- The inline foreign key gets the default name of Postgres. The generated
-- phase 3 adds `package_field_value_org_fk` later, so this file never adds a
-- constraint of that name. The org of a new row comes from the DEFAULT, the
-- bound tenant. The writer never names it.
--
-- A value points at its install by (organization_id, install_id). So a value
-- cannot point at an install of another org. A foreign key check skips RLS,
-- and the pair closes that gap. The key cascades: a removal of the install
-- deletes each of its values (§5.3). A pause or an archive keeps them.
--
-- The CHECKs hold the facts of acb_common/studio_fields.py: the key pattern
-- `NAME_PATTERN` and the sixteen types of `FIELD_TYPES`. PKG-13 refuses the
-- types `relation` and `file` in the writer, and the CHECK still takes all
-- sixteen, so a later slice needs no migration. The record kind has the shape
-- of `HOST_RECORD_KINDS` in field_values.py. The list itself lives in code.
--
-- No column names a member, so the member purge has no row for this table.
-- A deletion of the org cascades.
--
-- The number was 356 at build time: main ended at 355, and open pull
-- requests held 357 and 358. Check it again at merge (R1).
-- tests/unit/test_package_field_values_r8.py finds this file by CONTENT,
-- never by number.
-- ============================================================================

BEGIN;

SET LOCAL lock_timeout = '5s';

CREATE TABLE IF NOT EXISTS package_field_value (
    organization_id   UUID NOT NULL
                      REFERENCES organization (id) ON DELETE CASCADE
                      DEFAULT current_setting('app.tenant_id', true)::uuid,
    install_id        UUID NOT NULL,
    -- A kind of HOST_RECORD_KINDS: `task` or `crm.<table>`.
    record_kind       TEXT NOT NULL,
    -- The id of the host row.
    record_id         UUID NOT NULL,
    field_key         TEXT NOT NULL,
    -- The type of the declaration when the value was written. A read leaves
    -- out a value whose type differs from the current declaration.
    field_type        TEXT NOT NULL,
    value             JSONB NOT NULL,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (organization_id, install_id, record_kind, record_id, field_key),
    CONSTRAINT package_field_value_install_fk FOREIGN KEY (organization_id, install_id)
        REFERENCES package_install (organization_id, id) ON DELETE CASCADE,
    CONSTRAINT package_field_value_record_kind CHECK (
        record_kind ~ '^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)?$'
        AND length(record_kind) <= 64),
    -- studio_fields.NAME_PATTERN.
    CONSTRAINT package_field_value_field_key CHECK (
        field_key ~ '^[a-z][a-z0-9_]{0,47}$'),
    -- The sixteen types of studio_fields.FIELD_TYPES.
    CONSTRAINT package_field_value_field_type CHECK (field_type IN (
        'text', 'longText', 'number', 'money', 'date', 'datetime', 'checkbox',
        'select', 'multiSelect', 'person', 'relation', 'file', 'url', 'email',
        'phone', 'geo')),
    -- 65536 bytes (64 KiB) of JSON text.
    CONSTRAINT package_field_value_value_size CHECK (
        octet_length(value::text) <= 65536)
);

-- The read of one host record (get_values and delete_values).
CREATE INDEX IF NOT EXISTS package_field_value_record_idx
    ON package_field_value (organization_id, record_kind, record_id);

-- ── Row level security ──────────────────────────────────────────────────────
DO $rls$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_class
         WHERE oid = to_regclass('package_field_value')
           AND relrowsecurity
           AND relforcerowsecurity
    ) THEN
        ALTER TABLE package_field_value ENABLE ROW LEVEL SECURITY;
        ALTER TABLE package_field_value FORCE  ROW LEVEL SECURITY;
        DROP POLICY IF EXISTS package_field_value_tenant_isolation ON package_field_value;
        CREATE POLICY package_field_value_tenant_isolation ON package_field_value
            USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
            WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);
    END IF;
END
$rls$;

COMMENT ON TABLE package_field_value IS
    'One value of one package extension field on one host record (WS-PKG PKG-13). '
    'project-docs/specs/package_host.md §5.1 and §8.3.';

COMMIT;
