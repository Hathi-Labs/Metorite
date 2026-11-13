-- ============================================================================
-- 430_studio_record_changes.sql — the change history of Studio records
-- (WS-STU C4a-1).
--
-- What: one new table, `studio_record_changes`. A row holds one create, one
--       update or one delete of one record of a Studio object: the actor, the
--       way the write came (`via_kind`, `via_id`), and the changed keys with
--       their old and new values. gateway/studio/objects_store.py is the one
--       writer, in the SAVEPOINT of the batch that writes the record.
-- Why:  ai_studio.md §14 C4a-1. A member reads who changed a record, and what
--       changed. `app_audit` holds no value by design, so the values go here.
--       This slice adds the store side only: no route reads the table (C4a-2),
--       and no UI shows it (C4b).
-- Depends on: 323_studio_objects.sql (studio_objects),
--             130_org_access_control.sql (organization).
--
-- Expand only (R6): one new table, its keys and one index. No rename, no drop,
-- and no change to a row of another table. Old code names no new object.
-- Idempotent per infra/postgres/README.md: the table and the index take
-- IF NOT EXISTS, and the RLS block is guarded on relforcerowsecurity.
--
-- Tenant-scoped (R5): `organization_id` with ENABLE and FORCE row level
-- security and one policy with USING and WITH CHECK, in the shape of 323.
-- `REFERENCES` comes before `DEFAULT`, because the tenancy ratchet in
-- tests/unit/test_tenancy_boundary.py matches the two with no comma between.
-- The org of a new row comes from the DEFAULT, the bound tenant. The writer
-- never names it.
--
-- The keys. `record_id` has no foreign key, so the history of a record stays
-- after the record goes. `(organization_id, object_id)` is a foreign key to
-- the object, ON DELETE CASCADE, so no value outlives its object or its
-- package. The store purges the rows of a package in chunks first
-- (`objects_store.purge_record_changes`), so the cascade finds none.
--
-- The values. `changes` holds only the keys that changed, as
-- {key: {"old": ..., "new": ...}}. A create holds "old": null. A delete holds
-- each old value with "new": null. An update that changes no key writes no
-- row. Each value is stored, a restricted one too. The reader (C4a-2) hides
-- what the C8 hide list hides.
--
-- The actor is an email, as `studio_records.updated_by` is. Records and their
-- history are company data (D63). The member purge
-- (gateway/routes/admin/members.py) deletes no row of this table.
-- tests/unit/test_admin_member_purge.py pins that decision.
--
-- The number is taken at build time (R1). On 2026-11-13 origin/main held 426,
-- and the open branches held up to 428. The worktree of another lane held
-- 429 (studio_hot_num_f8), so this file takes 430. Check it again at merge.
-- Fences: tests/unit/test_studio_record_history.py and
-- tests/unit/test_studio_record_history_r8.py (R8).
-- ============================================================================

BEGIN;

CREATE TABLE IF NOT EXISTS studio_record_changes (
    organization_id   UUID NOT NULL
                      REFERENCES organization (id) ON DELETE CASCADE
                      DEFAULT current_setting('app.tenant_id', true)::uuid,
    id                BIGSERIAL PRIMARY KEY,
    object_id         UUID NOT NULL,
    -- No foreign key: the history stays after the record goes.
    record_id         UUID NOT NULL,
    op                TEXT NOT NULL,
    actor             TEXT NOT NULL,
    via_kind          TEXT NULL,
    via_id            TEXT NULL,
    changes           JSONB NOT NULL,
    at                TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT studio_record_changes_object_fk FOREIGN KEY (organization_id, object_id)
        REFERENCES studio_objects (organization_id, id) ON DELETE CASCADE,
    CONSTRAINT studio_record_changes_op CHECK (op IN ('create', 'update', 'delete')),
    CONSTRAINT studio_record_changes_actor CHECK (actor <> '' AND actor = lower(actor)),
    CONSTRAINT studio_record_changes_via_kind CHECK (
        via_kind IS NULL OR via_kind IN ('workflow_run', 'agent')),
    -- A kind and an id come together, or neither comes.
    CONSTRAINT studio_record_changes_via_pair CHECK ((via_kind IS NULL) = (via_id IS NULL)),
    CONSTRAINT studio_record_changes_via_id CHECK (
        via_id IS NULL OR (via_id <> '' AND char_length(via_id) <= 200)),
    CONSTRAINT studio_record_changes_object CHECK (jsonb_typeof(changes) = 'object')
);

-- The history of one record in order, and the purge of one object.
CREATE INDEX IF NOT EXISTS idx_studio_record_changes_record
    ON studio_record_changes (organization_id, object_id, record_id, id);

DO $rls$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_class
         WHERE oid = to_regclass('studio_record_changes')
           AND relrowsecurity
           AND relforcerowsecurity
    ) THEN
        ALTER TABLE studio_record_changes ENABLE ROW LEVEL SECURITY;
        ALTER TABLE studio_record_changes FORCE  ROW LEVEL SECURITY;
        DROP POLICY IF EXISTS studio_record_changes_tenant_isolation ON studio_record_changes;
        CREATE POLICY studio_record_changes_tenant_isolation ON studio_record_changes
            USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
            WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);
    END IF;
END
$rls$;

COMMENT ON TABLE studio_record_changes IS
    'The change history of Studio records: actor, via and changed values (WS-STU C4a-1). '
    'Only gateway/studio/objects_store.py writes it. project-docs/specs/ai_studio.md §14 C4a-1.';

COMMIT;
