-- ============================================================================
-- 279_crm_note_vectors.sql — the vector of one CRM note, for search by meaning.
--
-- What: one new table, `crm_note_vectors`. After a sync run, a background step
--       sends the text of each new or changed note of a listed org to the
--       embed door (`gateway/kb_index/embed.py`) and keeps one row for each
--       note: the md5 of the text that it embedded, the vector and the time.
--       A note whose vector came back as `None` gets a row with a NULL vector
--       and `failures` + 1, so it goes to the back of the queue and parks
--       after 3 failures. The table holds NO tenant text: a key, an md5, a
--       vector and numbers only. Only gateway/crm_sync/note_vectors.py writes
--       it, and only `GET /crm/notes/search` reads it.
-- Why:  WS-53 CRM-Q27, spec project-docs/specs/crm_platform.md §18.11.2.
--       `kb_chunks` (273) cannot hold a CRM note: its `source_kind` takes only
--       `page`, `file` and `document`, and each read of it must join
--       `kb_pages` (tests/unit/test_kb_visibility_one_seam.py). `body_md5` is
--       how the step finds a changed note, because `crm_activities` has no
--       `updated_at`. The daily cap counts requests in Redis, not rows here.
-- Depends on: 130_org_access_control.sql (organization), 144_crm.sql
--       (crm_activities), 73_email_embeddings.sql (the `vector` extension).
--
-- Expand only (R6): one new table and its index. No rename, no drop, and no
-- change to an existing table. Old code names no new object.
-- Idempotent per infra/postgres/README.md: CREATE TABLE IF NOT EXISTS, CREATE
-- INDEX IF NOT EXISTS and an RLS block guarded on relforcerowsecurity. A
-- second run changes nothing.
-- Tenant-scoped (R5): `organization_id` with ENABLE and FORCE row level
-- security and one policy with USING and WITH CHECK, in the shape of
-- 273_kb_chunks.sql. `REFERENCES` comes before `DEFAULT`, because the tenancy
-- ratchet in tests/unit/test_tenancy_boundary.py matches the two with no comma
-- between. The foreign key is inline, never `ADD CONSTRAINT <table>_org_fk`,
-- because generated phase 3 adds that name.
-- The HNSW index is partial on `embedding IS NOT NULL`: a failed note keeps a
-- NULL vector, and the step never stores a zero vector.
-- The number is taken at build time (R1). On 2026-10-16 origin/main held 278
-- (#1100). Check it again at merge.
-- Pinned by tests/unit/test_crm_note_vectors_r8.py, which finds this file by
-- CONTENT, never by number (R1).
-- ============================================================================

BEGIN;

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS crm_note_vectors (
    organization_id  UUID NOT NULL
                     REFERENCES organization (id) ON DELETE CASCADE
                     DEFAULT current_setting('app.tenant_id', true)::uuid,
    activity_id      UUID NOT NULL
                     REFERENCES crm_activities (id) ON DELETE CASCADE,
    -- md5(coalesce(subject, '') || coalesce(body, '')) of the embedded text.
    body_md5         TEXT NOT NULL,
    -- NULL until a vector lands, and NULL after a failure.
    embedding        VECTOR(1536) NULL,
    embedded_at      TIMESTAMPTZ NULL,
    failures         SMALLINT NOT NULL DEFAULT 0,
    failed_at        TIMESTAMPTZ NULL,
    PRIMARY KEY (activity_id),
    CONSTRAINT crm_note_vectors_failures_range CHECK (failures >= 0)
);

CREATE INDEX IF NOT EXISTS crm_note_vectors_embedding_idx
    ON crm_note_vectors USING hnsw (embedding vector_cosine_ops)
    WHERE embedding IS NOT NULL;

DO $rls$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_class
         WHERE oid = to_regclass('crm_note_vectors')
           AND relrowsecurity
           AND relforcerowsecurity
    ) THEN
        ALTER TABLE crm_note_vectors ENABLE ROW LEVEL SECURITY;
        ALTER TABLE crm_note_vectors FORCE  ROW LEVEL SECURITY;
        DROP POLICY IF EXISTS crm_note_vectors_tenant_isolation ON crm_note_vectors;
        CREATE POLICY crm_note_vectors_tenant_isolation ON crm_note_vectors
            USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
            WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);
    END IF;
END
$rls$;

COMMENT ON TABLE crm_note_vectors IS
    'WS-53 CRM-Q27: the vector of one CRM note, for search by meaning. A key, '
    'an md5, a vector and numbers only, never tenant text. Only gateway/'
    'crm_sync/note_vectors.py writes it. crm_platform.md §18.11.2.';

COMMIT;
