-- ============================================================================
-- 435_studio_batches.sql — the ledger of a batch run (WS-STU E8a, task 3095).
--
-- What: two new tables. `studio_batches` holds one frozen list of one
--       package: the action, the list digest, the item count and the status.
--       `studio_batch_items` holds one item of that list: its key, its place,
--       its op, its status, the record that it wrote, and its digest.
--       gateway/studio/batch_store.py is the one writer of both.
-- Why:  ai_studio.md §14 E8a. A run of 300 writes must survive a crash and
--       must write no item twice. The status of an item is set in the SAME
--       transaction as the write of the item, so a crash leaves both or
--       neither. This slice is the ledger only: no route, no node, no flag.
-- Depends on: 130_org_access_control.sql (organization), and the
--             (organization_id, id) key on `apps`, which 323 uses too.
--
-- NO PAY DATA HERE. An item holds a digest of its writes and NOT the writes.
-- The caller passes the writes again to each chunk, and the store checks each
-- one against its digest. So no value of a restricted field (C9) lives
-- outside the object store.
--
-- Expand only (R6): two new tables. No rename, no drop, no change of an old
-- object. Idempotent: CREATE TABLE IF NOT EXISTS, CREATE INDEX IF NOT EXISTS,
-- and an RLS block guarded on relforcerowsecurity.
-- Tenant-scoped (R5): `organization_id`, ENABLE and FORCE row level security,
-- and one policy with USING and WITH CHECK, as 426 does.
-- `REFERENCES` comes before `DEFAULT`, because the tenancy ratchet in
-- tests/unit/test_tenancy_boundary.py matches the two with no comma between.
--
-- `run_id` and `node_id` are the later binding of the workflow node (E8b).
-- They carry NO foreign key, because the run table is not this slice's. Both
-- are set or both are NULL, and a pair is unique in one org.
-- The status of a batch may widen later (a CHECK widen, as 427 did).
--
-- The number is taken at build time (R1). Check it again at merge.
-- ============================================================================

BEGIN;

CREATE TABLE IF NOT EXISTS studio_batches (
    organization_id  UUID NOT NULL
                     REFERENCES organization (id) ON DELETE CASCADE
                     DEFAULT current_setting('app.tenant_id', true)::uuid,
    id               UUID NOT NULL DEFAULT gen_random_uuid(),
    -- The package: an `apps` row of the same org.
    package_id       UUID NOT NULL,
    run_id           UUID NULL,
    node_id          TEXT NULL,
    action           TEXT NOT NULL,
    -- sha256 of the ordered list of item keys and item digests.
    list_digest      TEXT NOT NULL,
    total            INTEGER NOT NULL,
    status           TEXT NOT NULL DEFAULT 'open',
    created_by       TEXT NOT NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (id),
    -- The target of the foreign key of the items.
    CONSTRAINT studio_batches_org_id_key UNIQUE (organization_id, id),
    CONSTRAINT studio_batches_package_fk FOREIGN KEY (organization_id, package_id)
        REFERENCES apps (organization_id, id) ON DELETE CASCADE,
    CONSTRAINT studio_batches_status CHECK (status IN ('open', 'done', 'failed')),
    CONSTRAINT studio_batches_digest CHECK (list_digest ~ '^[0-9a-f]{64}$'),
    CONSTRAINT studio_batches_total CHECK (total >= 1),
    CONSTRAINT studio_batches_action CHECK (action <> '' AND char_length(action) <= 120),
    CONSTRAINT studio_batches_node CHECK (
        (run_id IS NULL) = (node_id IS NULL)
        AND (node_id IS NULL OR (node_id <> '' AND char_length(node_id) <= 120))),
    CONSTRAINT studio_batches_created_by CHECK (
        created_by <> '' AND created_by = lower(created_by))
);

-- One batch for each node of each run. A retry of the park meets this key.
CREATE UNIQUE INDEX IF NOT EXISTS studio_batches_run_node_uq
    ON studio_batches (organization_id, run_id, node_id)
    WHERE run_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS studio_batches_package_idx
    ON studio_batches (organization_id, package_id, created_at DESC);

CREATE TABLE IF NOT EXISTS studio_batch_items (
    organization_id  UUID NOT NULL
                     REFERENCES organization (id) ON DELETE CASCADE
                     DEFAULT current_setting('app.tenant_id', true)::uuid,
    batch_id         UUID NOT NULL,
    -- The key of the item in its list. The caller names it, and it must hold
    -- no pay data (an id or a row number).
    item_key         TEXT NOT NULL,
    position         INTEGER NOT NULL,
    op               TEXT NOT NULL,
    -- The writes of the item: the record and its lines (C13). The cap of one
    -- chunk is 50 writes, so an item holds at most 50.
    write_count      INTEGER NOT NULL,
    status           TEXT NOT NULL DEFAULT 'pending',
    record_id        UUID NULL,
    -- A short code of the refusal. Never a message, because a message can hold data.
    error_code       TEXT NULL,
    -- sha256 of the canonical form of the writes of the item.
    item_digest      TEXT NOT NULL,
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (organization_id, batch_id, item_key),
    CONSTRAINT studio_batch_items_batch_fk FOREIGN KEY (organization_id, batch_id)
        REFERENCES studio_batches (organization_id, id) ON DELETE CASCADE,
    CONSTRAINT studio_batch_items_position_uq UNIQUE (organization_id, batch_id, position),
    -- No delete: a delete is not in a batch.
    CONSTRAINT studio_batch_items_op CHECK (op IN ('create', 'update')),
    CONSTRAINT studio_batch_items_status CHECK (status IN ('pending', 'done', 'failed')),
    CONSTRAINT studio_batch_items_key CHECK (item_key <> '' AND char_length(item_key) <= 120),
    CONSTRAINT studio_batch_items_position CHECK (position >= 0),
    CONSTRAINT studio_batch_items_write_count CHECK (write_count BETWEEN 1 AND 50),
    CONSTRAINT studio_batch_items_digest CHECK (item_digest ~ '^[0-9a-f]{64}$'),
    CONSTRAINT studio_batch_items_error CHECK (error_code IS NULL OR char_length(error_code) <= 80),
    -- A done item names its record, and a failed item names its code.
    CONSTRAINT studio_batch_items_done_has_record CHECK (status <> 'done' OR record_id IS NOT NULL),
    CONSTRAINT studio_batch_items_failed_has_code CHECK (status <> 'failed' OR error_code IS NOT NULL)
);

-- The next chunk reads the pending items of one batch in place order.
CREATE INDEX IF NOT EXISTS studio_batch_items_pending_idx
    ON studio_batch_items (organization_id, batch_id, position)
    WHERE status = 'pending';

DO $rls$
DECLARE
    t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY['studio_batches', 'studio_batch_items'] LOOP
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

COMMENT ON TABLE studio_batches IS
    'One frozen list of one package, run in chunks (WS-STU E8a). The digest '
    'binds the list. RLS covers the org.';
COMMENT ON TABLE studio_batch_items IS
    'One item of a batch. It holds the digest of its writes and never the '
    'writes (no pay data outside the object store). The status is set in the '
    'transaction of the write of the item.';

COMMIT;
