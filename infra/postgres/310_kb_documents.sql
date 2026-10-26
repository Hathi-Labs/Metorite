-- ============================================================================
-- 310_kb_documents.sql — the text of each indexed Drive document, and the
-- folders a member chose to index.
--
-- What: two new tables, one function, one trigger, two indexes and one widened
--       CHECK.
--       `kb_documents` — one row for each indexed node: the extracted `text`
--                   (empty for a refused file), `sha` (the PROVIDER stamp of
--                   the node, never a hash of the text), `extracted_at`,
--                   `parser` (the reader that made the text, or `none`),
--                   `truncated`, and `summary` (NULL until a later slice).
--                   UNIQUE on `node_id`. The foreign key to kb_source_nodes
--                   cascades, so a disconnect, a member purge and a discovery
--                   that clears the tree take the rows with their nodes.
--                   It has NO vector column: the vector of a document lives
--                   in kb_chunks only (tests/unit/test_kb_one_index.py).
--       `kb_source_includes` — one row for each folder that a member chose
--                   to index, keyed by the PROVIDER id of the folder
--                   (`folder_ext_id`), never by a node row, because a capped
--                   discovery deletes every node. UNIQUE on (organization_id,
--                   source_id, folder_ext_id). The foreign key to kb_sources
--                   cascades.
--       `kb_documents_delete_chunks` — an AFTER DELETE trigger function. A
--                   chunk of a document is a kb_chunks row with `source_kind
--                   = 'document'` and `source_id` = the document id.
--                   kb_chunks.source_id has no foreign key, so the trigger
--                   removes the chunks with their document, on every path.
--       `kb_sync_runs_kind_check` — adds `index`. Every old value stays
--                   legal. The cursor kinds do not change.
-- Why:  WS-61 KC-5a, spec project-docs/specs/knowledge_curation.md §7.2
--       step 4, §7.3, §16 and §22. Who may read the text is decided by
--       visible_documents_clause (KC-4a), never by these rows.
-- Depends on: 130_org_access_control.sql (organization),
--       273_kb_chunks.sql (kb_chunks),
--       300_kb_sources.sql (kb_sources, kb_source_nodes),
--       308_kb_sync.sql (kb_sync_runs), 309_kb_node_access.sql.
--
-- Expand only (R6): two new tables, one new function and trigger, two new
-- indexes, and one CHECK that accepts MORE values than before. No rename, no
-- drop of data, no UPDATE, and no row changes. The old gateway writes only the
-- old kinds, and they stay legal.
-- The widened CHECK: the DO block drops it and adds it back NOT VALID, then
-- validates it, in the one transaction of this file, as 309 does. It runs
-- only while pg_get_constraintdef lacks 'index', so a second run changes
-- nothing.
-- Idempotent per infra/postgres/README.md: CREATE TABLE IF NOT EXISTS,
-- CREATE INDEX IF NOT EXISTS, CREATE OR REPLACE FUNCTION, DROP TRIGGER IF
-- EXISTS then CREATE TRIGGER, the guarded CHECK block, and an RLS block
-- guarded on relforcerowsecurity.
-- Tenant-scoped (R5): each table has `organization_id` with ENABLE and FORCE
-- row level security and one policy with USING and WITH CHECK, in the shape
-- of 309_kb_node_access.sql. `REFERENCES` comes before `DEFAULT`, because the
-- tenancy ratchet in tests/unit/test_tenancy_boundary.py matches the two
-- with no comma between. The foreign key is inline, never
-- `ADD CONSTRAINT <table>_org_fk`, because generated phase 3 adds that name.
-- The trigger deletes chunks of its own organization only, so row level
-- security and the explicit predicate agree.
-- ⚠️ `text` holds the words of a company document. The cascade from
-- kb_source_nodes removes it, so the member purge needs no new path.
-- The number is taken at build time (R1): 310 was free on main and on every
-- remote branch on 2026-10-26. Check it again at merge. The tests find this
-- file by CONTENT, never by number.
--
-- Fences: tests/unit/test_kc_index_r8.py (FORCE RLS as a role that is not a
-- superuser, not the owner and not BYPASSRLS, the widened CHECK, the trigger
-- on each delete path, and the replay), tests/unit/test_tenant_coverage.py.
-- ============================================================================

SET lock_timeout = '5s';

BEGIN;

CREATE TABLE IF NOT EXISTS kb_documents (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id   UUID NOT NULL
                      REFERENCES organization (id) ON DELETE CASCADE
                      DEFAULT current_setting('app.tenant_id', true)::uuid,
    node_id           UUID NOT NULL UNIQUE
                      REFERENCES kb_source_nodes (id) ON DELETE CASCADE,
    text              TEXT NOT NULL DEFAULT '',
    -- The provider stamp of the node (content_hash, else "m:<modified_at>").
    -- It is NOT a hash of the text.
    sha               TEXT NOT NULL
                      CONSTRAINT kb_documents_sha_length
                      CHECK (char_length(sha) BETWEEN 1 AND 200),
    extracted_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    parser            TEXT NOT NULL
                      CONSTRAINT kb_documents_parser_length
                      CHECK (char_length(parser) BETWEEN 1 AND 32),
    truncated         BOOLEAN NOT NULL DEFAULT false,
    summary           TEXT NULL,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS kb_source_includes (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id   UUID NOT NULL
                      REFERENCES organization (id) ON DELETE CASCADE
                      DEFAULT current_setting('app.tenant_id', true)::uuid,
    source_id         UUID NOT NULL REFERENCES kb_sources (id) ON DELETE CASCADE,
    folder_ext_id     TEXT NOT NULL
                      CONSTRAINT kb_source_includes_ext_id_length
                      CHECK (char_length(folder_ext_id) BETWEEN 1 AND 200),
    included_by       TEXT NOT NULL
                      CONSTRAINT kb_source_includes_by_length
                      CHECK (char_length(included_by) BETWEEN 1 AND 320),
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT kb_source_includes_key
        UNIQUE (organization_id, source_id, folder_ext_id)
);

-- The cascade from kb_sources deletes by source_id alone.
CREATE INDEX IF NOT EXISTS kb_source_includes_source_idx
    ON kb_source_includes (source_id);

-- The pending query of the index pass reads documents by node.
CREATE INDEX IF NOT EXISTS kb_documents_org_node_idx
    ON kb_documents (organization_id, node_id);

-- The run kind of the index pass.
DO $widen$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = 'kb_sync_runs'::regclass
           AND conname = 'kb_sync_runs_kind_check'
           AND pg_get_constraintdef(oid) LIKE '%''index''%'
    ) THEN
        ALTER TABLE kb_sync_runs DROP CONSTRAINT IF EXISTS kb_sync_runs_kind_check;
        ALTER TABLE kb_sync_runs ADD CONSTRAINT kb_sync_runs_kind_check
            CHECK (kind IN ('discover', 'changes', 'reconcile', 'access', 'index'))
            NOT VALID;
        ALTER TABLE kb_sync_runs VALIDATE CONSTRAINT kb_sync_runs_kind_check;
    END IF;
END
$widen$;

-- The chunks of a document go with the document (KC-5a, D6).
CREATE OR REPLACE FUNCTION kb_documents_delete_chunks() RETURNS trigger
LANGUAGE plpgsql AS $fn$
BEGIN
    DELETE FROM kb_chunks
     WHERE organization_id = OLD.organization_id
       AND source_kind = 'document'
       AND source_id = OLD.id;
    RETURN OLD;
END
$fn$;

DROP TRIGGER IF EXISTS kb_documents_delete_chunks ON kb_documents;
CREATE TRIGGER kb_documents_delete_chunks
    AFTER DELETE ON kb_documents
    FOR EACH ROW EXECUTE FUNCTION kb_documents_delete_chunks();

DO $rls$
DECLARE
    t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY['kb_documents', 'kb_source_includes'] LOOP
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

COMMENT ON TABLE kb_documents IS
    'WS-61 KC-5a: the extracted text of one indexed Drive document. sha is '
    'the provider stamp, not a text hash. No vector here. '
    'knowledge_curation.md §7.3.';

COMMENT ON TABLE kb_source_includes IS
    'WS-61 KC-5a: a folder that a member chose to index, keyed by the '
    'provider id. A file under it is in scope. knowledge_curation.md §7.2 '
    'step 4.';

COMMIT;
