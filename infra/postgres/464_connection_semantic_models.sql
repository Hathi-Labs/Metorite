-- ============================================================================
-- 464_connection_semantic_models.sql - the store of the model of a connection
-- (WS-SEM SEM-12, task 3742).
--
-- What: one new table. `connection_semantic_models` holds one row for each
--       version of the semantic model of one connection of the registry
--       (`integration_connections`). A row holds the body (the parts of
--       semantic_model.md §3.2, in the three sections `code`, `ai` and
--       `admin` of §10.4), the version, the state (`draft`, `approved` or
--       `archived`), the hash of the shape that the profiler read, the
--       highest writer kind in the body, who approved it and when, and
--       whether the shape came from an untrusted origin (a local link).
--       gateway/connections/semantic_model.py is the one module that runs
--       SQL on the table.
-- Why:  semantic_model.md §3.4, §10.2 row SEM-12 and §10.4 (D-SEM-2).
-- Depends on: 130_org_access_control.sql (organization),
--             245_integration_connections.sql (integration_connections).
--
-- NO DATA OF THE SOURCE. The body is schema, never data: keys, labels,
-- options, counts, shares, dates, closed words, and the admin's short text.
-- The writer runs `crm_sync/catalogue.shape_only` before each write, so no
-- amount, no record value and no free text of the source or of the model
-- reaches the body. `shape_hash` is a hex hash (the CHECK). `approved_role`
-- is a role slug (the CHECK). No column holds text that a stranger wrote.
--
-- ONE APPROVED VERSION. The partial unique index
-- `connection_semantic_models_one_approved` holds at most one `approved` row
-- for each connection. The approve of the writer archives the old row and
-- then approves the draft, in one transaction and in that order, so the
-- index never trips. Three CHECKs hold the approval: `approved_role` and
-- `approved_at` are both NULL or both set, a draft has both NULL, and an
-- approved row has both set. An archived row keeps the two values.
--
-- THE CONNECTION. The foreign key is the plain
-- `integration_connections (id)` with ON DELETE CASCADE, so a delete of the
-- connection deletes its versions, and a delete of the org deletes both. A
-- foreign key check skips RLS. So the one writer never names a connection
-- by its id alone: it selects and locks the connection under the bound
-- tenant (FORCE RLS) first, and its INSERT selects the connection again
-- under the bound tenant. A connection of another org gives no row, and the
-- same refusal as a missing one.
-- tests/unit/test_connection_semantic_model_r8.py pins it.
--
-- THE MEMBER PURGE. A row belongs to the org and names no member. So the
-- purge (gateway/routes/admin/members.py) has no clause for it. The cascade
-- of the connection and of the org removes the rows.
--
-- Expand only (R6): one new table and one partial index. No rename, no
-- drop, and no change to an old object. The old code never names the
-- table. Idempotent: CREATE TABLE IF NOT EXISTS, CREATE UNIQUE INDEX IF NOT
-- EXISTS, and an RLS block guarded on relforcerowsecurity. The foreign key
-- to `integration_connections` takes a SHARE ROW EXCLUSIVE lock on that
-- table until COMMIT. So the file sets `lock_timeout` first, as 451 does.
-- Tenant-scoped (R5): `organization_id` with ENABLE and FORCE row level
-- security, and one policy with USING and WITH CHECK, as 461 does. The org
-- of a new row comes from the DEFAULT, the bound tenant. The writer never
-- names it. `REFERENCES` comes before `DEFAULT`, because the tenancy ratchet
-- in tests/unit/test_tenancy_boundary.py matches the two with no comma
-- between. The generated phases carry the table.
--
-- The number is taken at build time (R1): 464 was booked for this task.
-- Check it again at merge. The fences find this file by CONTENT, never by
-- number (R1).
-- ============================================================================

BEGIN;

SET LOCAL lock_timeout = '5s';

CREATE TABLE IF NOT EXISTS connection_semantic_models (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id    UUID NOT NULL
                       REFERENCES organization (id) ON DELETE CASCADE
                       DEFAULT current_setting('app.tenant_id', true)::uuid,
    -- The connection of the registry. A delete of the connection deletes
    -- each version of its model.
    connection_id      UUID NOT NULL
                       REFERENCES integration_connections (id) ON DELETE CASCADE,
    -- The writer sets it: the highest version of the connection plus 1.
    version            INTEGER NOT NULL,
    state              TEXT NOT NULL DEFAULT 'draft',
    -- The parts of the model (semantic_model.md §10.4). Shape only.
    body               JSONB NOT NULL,
    -- The hash of the tool list and of the discovered shape, in hex.
    shape_hash         TEXT NOT NULL,
    -- The highest writer kind in the body: code, then ai, then admin.
    made_by            TEXT NOT NULL,
    -- The role slug of the admin who approved the version, and when.
    approved_role      TEXT,
    approved_at        TIMESTAMPTZ,
    -- True when the shape came through a local link (rule 10).
    untrusted_origin   BOOLEAN NOT NULL DEFAULT false,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT connection_semantic_models_version_uq
        UNIQUE (organization_id, connection_id, version),
    CONSTRAINT connection_semantic_models_version_positive CHECK (version > 0),
    CONSTRAINT connection_semantic_models_state CHECK (
        state IN ('draft', 'approved', 'archived')),
    CONSTRAINT connection_semantic_models_made_by CHECK (
        made_by IN ('code', 'ai', 'admin')),
    CONSTRAINT connection_semantic_models_body_shape CHECK (
        jsonb_typeof(body) = 'object' AND octet_length(body::text) <= 262144),
    CONSTRAINT connection_semantic_models_shape_hash CHECK (
        shape_hash ~ '^[0-9a-f]{16,128}$'),
    CONSTRAINT connection_semantic_models_role_shape CHECK (
        approved_role IS NULL OR approved_role ~ '^[a-z][a-z0-9_-]{0,39}$'),
    -- Both NULL, or both set.
    CONSTRAINT connection_semantic_models_approval_pair CHECK (
        (approved_role IS NULL) = (approved_at IS NULL)),
    -- A draft has no approval.
    CONSTRAINT connection_semantic_models_draft_unapproved CHECK (
        state <> 'draft' OR (approved_role IS NULL AND approved_at IS NULL)),
    -- An approved row has its approval.
    CONSTRAINT connection_semantic_models_approved_set CHECK (
        state <> 'approved' OR (approved_role IS NOT NULL AND approved_at IS NOT NULL))
);

-- At most one approved version for each connection.
CREATE UNIQUE INDEX IF NOT EXISTS connection_semantic_models_one_approved
    ON connection_semantic_models (organization_id, connection_id)
    WHERE state = 'approved';

DO $rls$
DECLARE
    t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY['connection_semantic_models'] LOOP
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

COMMENT ON TABLE connection_semantic_models IS
    'The semantic model of one connection, one row for each version (WS-SEM '
    'SEM-12). Shape only, never data. At most one approved row for each '
    'connection. A delete of the connection deletes its versions. One module '
    'writes it: gateway/connections/semantic_model.py. RLS covers the org.';

COMMIT;
