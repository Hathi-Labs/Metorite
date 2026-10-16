-- ============================================================================
-- 273_kb_chunks.sql — the one retrieval index of the Knowledge app.
--
-- What: one new table.
--       `kb_chunks` — one row for each part of a published page (and, from
--                     KB-7, of a file or a document). Each row holds the
--                     text, the heading path, a generated `tsv` for the
--                     lexical half, and a nullable `embedding` for the
--                     semantic half. `content_sha` is the hash of the text,
--                     so a page edit replaces only the parts that changed.
-- Why:  WS-61 KB-3, spec project-docs/specs/knowledge_base.md §6.1 to §6.4.
--       Before this, nothing could search the body of a page.
-- Depends on: 130_org_access_control.sql (organization),
--       73_email_embeddings.sql (the `vector` extension),
--       272_knowledge_pages.sql (kb_pages and kb_spaces, which the reads
--       join; no foreign key names them, see below).
--
-- Expand only (R6): one new table and its indexes. No rename, no drop, and
-- no change to an existing table. Old code names no new object.
-- Idempotent per infra/postgres/README.md: CREATE TABLE IF NOT EXISTS,
-- CREATE INDEX IF NOT EXISTS, and an RLS block guarded on
-- relforcerowsecurity. A second run changes nothing.
-- Tenant-scoped (R5): `organization_id` with ENABLE and FORCE row level
-- security and one policy with USING and WITH CHECK, in the shape of
-- 272_knowledge_pages.sql. `REFERENCES` comes before `DEFAULT`, because the
-- tenancy ratchet in tests/unit/test_tenancy_boundary.py matches the two
-- with no comma between. The foreign key is inline, never
-- `ADD CONSTRAINT <table>_org_fk`, because generated phase 3 adds that name.
-- `space_id` and `source_id` carry no foreign key on purpose. `source_id`
-- names a page, an artifact or a document by `source_kind`, and one key
-- cannot name three tables. `space_id` follows the source. The indexer
-- writes and deletes a page's rows in the transaction of the page write.
-- `tsv` is GENERATED with the regconfig constant 'english', so the
-- expression is immutable and the lexical half cannot drift from the text.
-- The HNSW index is partial on `embedding IS NOT NULL`: a row waits with a
-- NULL vector until the embed job runs, and HNSW needs no training rows.
-- The number is taken at build time (R1). On 2026-10-16 main held 272, and
-- no remote branch held 273. Check it again at merge. The tests find this
-- file by CONTENT, never by number.
-- ============================================================================

BEGIN;

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS kb_chunks (
    id               BIGSERIAL PRIMARY KEY,
    organization_id  UUID NOT NULL
                     REFERENCES organization (id) ON DELETE CASCADE
                     DEFAULT current_setting('app.tenant_id', true)::uuid,
    space_id         UUID NOT NULL,
    source_kind      TEXT NOT NULL
                     CHECK (source_kind IN ('page', 'file', 'document')),
    -- kb_pages.id, artifacts.id or kb_documents.id, by source_kind.
    source_id        UUID NOT NULL,
    -- The page that a file hangs off, or NULL.
    page_id          UUID NULL,
    ordinal          INTEGER NOT NULL CHECK (ordinal >= 0),
    -- "Returns policy > Refund window": the page title, then the headings.
    heading_path     TEXT NULL,
    raw              TEXT NOT NULL,
    -- NULL until KB-8 distills the chunk.
    distilled        TEXT NULL,
    tsv              TSVECTOR GENERATED ALWAYS AS (
                         to_tsvector('english',
                             coalesce(heading_path, '') || ' ' || coalesce(distilled, raw))
                     ) STORED,
    -- NULL until the embed job writes it, and NULL for good when the embed
    -- door answers a zero vector (knowledge_base.md §6.4).
    embedding        VECTOR(1536) NULL,
    content_sha      TEXT NOT NULL,
    -- The date this text first entered the index. The age decay reads it.
    valid_from       DATE NULL,
    metadata         JSONB NOT NULL DEFAULT '{}',
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS kb_chunks_tsv_idx
    ON kb_chunks USING GIN (tsv);
CREATE INDEX IF NOT EXISTS kb_chunks_embedding_idx
    ON kb_chunks USING hnsw (embedding vector_cosine_ops)
    WHERE embedding IS NOT NULL;
CREATE INDEX IF NOT EXISTS kb_chunks_source_idx
    ON kb_chunks (organization_id, source_kind, source_id);
-- The upsert key of the indexer. A part with the same text on the same
-- source is one row, so an edit that keeps a part keeps its vector.
CREATE UNIQUE INDEX IF NOT EXISTS kb_chunks_source_sha_key
    ON kb_chunks (organization_id, source_kind, source_id, content_sha);

DO $rls$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_class
         WHERE oid = to_regclass('kb_chunks')
           AND relrowsecurity
           AND relforcerowsecurity
    ) THEN
        ALTER TABLE kb_chunks ENABLE ROW LEVEL SECURITY;
        ALTER TABLE kb_chunks FORCE  ROW LEVEL SECURITY;
        DROP POLICY IF EXISTS kb_chunks_tenant_isolation ON kb_chunks;
        CREATE POLICY kb_chunks_tenant_isolation ON kb_chunks
            USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
            WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);
    END IF;
END
$rls$;

COMMENT ON TABLE kb_chunks IS
    'WS-61 KB-3: the one retrieval index of Knowledge. Every read joins '
    'kb_pages under gateway.routes.knowledge.core.visible_pages_clause. '
    'knowledge_base.md §6.1.';

COMMIT;
