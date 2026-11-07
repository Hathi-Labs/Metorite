-- ============================================================================
-- 346_kb_links_graph_edges.sql - kb_links holds an edge between any two
-- records (WS-KG KG-4, D128)
-- ============================================================================
-- Spec: project-docs/specs/knowledge_graph.md §3.2, §4.1, §4.4 and the KG-4
-- row of §9 · Metorite task 789 · R5 · R6 · R8.
-- R1: origin/main held 345 (`345_chat_session_continued_from.sql`) as its
-- highest file on 2026-11-05. No branch holds 346. So this file is 346.
-- Check the number again at merge.
--
-- What: migration 272 made `kb_links` for the links of a Knowledge page. This
-- file widens it, so one row can also join two records of any app.
--   page_id      NOT NULL is dropped. A graph row has page_id NULL, also
--                when its source is a page. The page rows keep their form.
--   source_kind, source_id
--                The other end of a graph row. NULL on a page row.
--   relation     One name of `RELATIONS` in gateway/kb_entities/graph.py.
--                No CHECK: the code list is the list (§4.1).
--   provenance   EXTRACTED, INFERRED or AMBIGUOUS (§4.2).
--   confidence   0 to 1.
--   removed_at, removed_by
--                The removal by an admin (§4.4). The row stays, so a sweep
--                finds the key and never writes the edge again.
--   target_kind  The CHECK also takes the node kinds of §3.1. Each old value
--                stays valid.
--
-- Two shape rules, as CHECKs. A row with no page_id names its source, its
-- relation, its provenance and its confidence. Only a row with no page_id
-- can be removed, because `sync_page_links` writes the page rows again on
-- each save. Each page row passes both rules as it is.
--
-- Indexes: the partial unique index `kb_links_graph_key` makes a second
-- sweep write nothing. Its first three columns are (organization_id,
-- source_kind, source_id), so it also serves the neighbour read of §3.2. A
-- second index on those three columns would hold the same keys, so this file
-- does not make one. `idx_kb_links_target` (272) serves the other direction.
--
-- Who writes a graph row: only `gateway/kb_entities/graph.py` (the sweep of
-- §4.1). Who removes one: `graph.remove_edge`. The page-row writer is still
-- `routes/knowledge/backlinks.sync_page_links`. The three page reads
-- (`backlinks.py`, `core.read_links`, `proposal_expiry.py`) key on page_id,
-- so a graph row never reaches them.
--
-- Expand only (R6): new columns are nullable with no default. A dropped NOT
--   NULL and a wider CHECK accept each row that the old code writes. The old
--   code names its columns, so it never reads a new one. No backfill. The
--   tightening half (provenance on the page rows) is a later file.
--   Idempotent: ADD COLUMN IF NOT EXISTS, DROP CONSTRAINT IF EXISTS before
--   each ADD CONSTRAINT, CREATE INDEX IF NOT EXISTS.
--
-- Tenancy (R5): `kb_links` keeps organization_id NOT NULL, its column
--   DEFAULT from `app.tenant_id`, FORCE RLS and the policy
--   `kb_links_tenant_isolation` (272). This file changes none of them, and
--   adds no table and no `gen_tenant_migration.EXEMPT` row.
--
-- Never wait for the lock: `ALTER TABLE` takes ACCESS EXCLUSIVE on
--   `kb_links`, and each page save writes it. So the file sets
--   `lock_timeout` itself, as 337 and 344 do. On a lock timeout the runner
--   tries the whole file again, and the file is idempotent.
--
-- Depends on: 272_knowledge_pages.sql (the table).
-- Pinned by: tests/unit/test_kg_links_r8.py (R8) and
--   tests/unit/test_kg_links_writer.py, which find this file by content.
-- ============================================================================

SET lock_timeout = '5s';

ALTER TABLE kb_links
    ALTER COLUMN page_id DROP NOT NULL,
    ADD COLUMN IF NOT EXISTS source_kind TEXT,
    ADD COLUMN IF NOT EXISTS source_id   TEXT,
    ADD COLUMN IF NOT EXISTS relation    TEXT,
    ADD COLUMN IF NOT EXISTS provenance  TEXT,
    ADD COLUMN IF NOT EXISTS confidence  REAL,
    ADD COLUMN IF NOT EXISTS removed_at  TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS removed_by  TEXT;

-- kb-graph-target-kinds: the old values of 272, then the node kinds of §3.1.
ALTER TABLE kb_links DROP CONSTRAINT IF EXISTS kb_links_target_kind_check;
ALTER TABLE kb_links ADD CONSTRAINT kb_links_target_kind_check
    CHECK (target_kind IN ('page', 'task', 'project', 'crm_record',
                           'email_thread', 'meeting', 'artifact', 'url',
                           'email', 'person', 'lead', 'deal', 'contact',
                           'company', 'whatsapp', 'entity'));

-- kb-graph-source-kinds: the node kinds of §3.1.
ALTER TABLE kb_links DROP CONSTRAINT IF EXISTS kb_links_source_kind_check;
ALTER TABLE kb_links ADD CONSTRAINT kb_links_source_kind_check
    CHECK (source_kind IS NULL
           OR source_kind IN ('task', 'email', 'person', 'lead', 'deal',
                              'contact', 'company', 'artifact', 'whatsapp',
                              'page', 'entity', 'meeting'));

ALTER TABLE kb_links DROP CONSTRAINT IF EXISTS kb_links_provenance_check;
ALTER TABLE kb_links ADD CONSTRAINT kb_links_provenance_check
    CHECK (provenance IS NULL
           OR provenance IN ('EXTRACTED', 'INFERRED', 'AMBIGUOUS'));

ALTER TABLE kb_links DROP CONSTRAINT IF EXISTS kb_links_confidence_check;
ALTER TABLE kb_links ADD CONSTRAINT kb_links_confidence_check
    CHECK (confidence IS NULL OR (confidence >= 0 AND confidence <= 1));

-- A graph row names its source, relation, provenance and confidence.
ALTER TABLE kb_links DROP CONSTRAINT IF EXISTS kb_links_graph_row_check;
ALTER TABLE kb_links ADD CONSTRAINT kb_links_graph_row_check
    CHECK (page_id IS NOT NULL
           OR (source_kind IS NOT NULL AND source_id IS NOT NULL
               AND relation IS NOT NULL AND provenance IS NOT NULL
               AND confidence IS NOT NULL));

-- Only a graph row can be removed (§4.4).
ALTER TABLE kb_links DROP CONSTRAINT IF EXISTS kb_links_removal_check;
ALTER TABLE kb_links ADD CONSTRAINT kb_links_removal_check
    CHECK (removed_at IS NULL OR page_id IS NULL);

-- One row for each edge. A removed row keeps its key, so the sweep's
-- ON CONFLICT DO NOTHING never writes it again.
CREATE UNIQUE INDEX IF NOT EXISTS kb_links_graph_key
    ON kb_links (organization_id, source_kind, source_id, relation,
                 target_kind, target_id)
    WHERE page_id IS NULL;

COMMENT ON TABLE kb_links IS
    'WS-61 KB-1: a typed reference from a page to another thing. WS-KG KG-4: '
    'a row with page_id NULL is a graph edge between two records. A link is '
    'never a grant. knowledge_base.md §5.5, knowledge_graph.md §3.2.';
