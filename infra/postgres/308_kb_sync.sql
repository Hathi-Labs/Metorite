-- ============================================================================
-- 308_kb_sync.sql — the sync loop of a Knowledge file source: cursors, runs,
-- and the time of the last new node.
--
-- What: two new tables, three indexes and one new nullable column.
--       `kb_sync_cursors` — one row for each source and kind. `changes` holds
--                   the provider's page token of the change feed in
--                   `cursor_state`. `reconcile` holds the time of the last
--                   daily relist in `last_run_at`. UNIQUE on
--                   (organization_id, source_id, kind).
--       `kb_sync_runs` — one row for each run of one source: `discover`,
--                   `changes` or `reconcile`, with `status` `running`, `ok`,
--                   `failed` or `stopped`, a heartbeat, the counts as JSON,
--                   and a short safe code in `error` (64 characters at most).
--                   A partial unique index allows ONE `running` row for each
--                   source. That index is the run lock, and it works across
--                   gateway workers, as 246_crm_sync_runs.sql does for an org.
--       `kb_sources.last_new_at` — when a run last INSERTED a node. The CX-5
--                   health view reads it for Knowledge.
-- Why:  WS-61 KC-3, spec project-docs/specs/knowledge_curation.md §7.2 step 5,
--       §16 and §22, and knowledge_base.md §7.1. KC-3 keeps the METADATA tree
--       current. It reads no text. KC-5 compares kb_source_nodes.content_hash
--       with its own document hash.
-- Depends on: 130_org_access_control.sql (organization),
--       300_kb_sources.sql (kb_sources).
--
-- Expand only (R6): two new tables, three new indexes and one nullable column
-- with no default. No rename, no drop, no UPDATE, and no row changes. The old
-- gateway names no new object.
-- Idempotent per infra/postgres/README.md: CREATE TABLE IF NOT EXISTS,
-- CREATE INDEX IF NOT EXISTS, ADD COLUMN IF NOT EXISTS, and an RLS block
-- guarded on relforcerowsecurity. A second run changes nothing.
-- Tenant-scoped (R5): each table has `organization_id` with ENABLE and FORCE
-- row level security and one policy with USING and WITH CHECK, in the shape
-- of 300_kb_sources.sql. `REFERENCES` comes before `DEFAULT`, because the
-- tenancy ratchet in tests/unit/test_tenancy_boundary.py matches the two
-- with no comma between. The foreign key is inline, never
-- `ADD CONSTRAINT <table>_org_fk`, because generated phase 3 adds that name.
-- ⚠️ Neither table holds a person column. The cascade from kb_sources removes
-- both with the source, so the member purge needs no new path.
-- The number is taken at build time (R1). It was 305 until the merge of
-- 2026-10-25: then main held 300 to 304, 306 and 307, and the open PR #1333
-- held 305. Check it again at merge. The tests find this file by CONTENT,
-- never by number.
--
-- Fences: tests/unit/test_kc_sync_r8.py (FORCE RLS as a role that is not a
-- superuser, not the owner and not BYPASSRLS, the run lock on the real
-- index, and the replay), tests/unit/test_tenant_coverage.py.
-- ============================================================================

SET lock_timeout = '5s';

BEGIN;

CREATE TABLE IF NOT EXISTS kb_sync_cursors (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id   UUID NOT NULL
                      REFERENCES organization (id) ON DELETE CASCADE
                      DEFAULT current_setting('app.tenant_id', true)::uuid,
    source_id         UUID NOT NULL REFERENCES kb_sources (id) ON DELETE CASCADE,
    kind              TEXT NOT NULL
                      CONSTRAINT kb_sync_cursors_kind_check
                      CHECK (kind IN ('changes', 'reconcile')),
    cursor_state      JSONB NULL,
    last_run_at       TIMESTAMPTZ NULL,
    last_status       TEXT NULL
                      CONSTRAINT kb_sync_cursors_last_status_length
                      CHECK (last_status IS NULL OR char_length(last_status) <= 32),
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT kb_sync_cursors_source_kind_key UNIQUE (organization_id, source_id, kind)
);

CREATE TABLE IF NOT EXISTS kb_sync_runs (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id   UUID NOT NULL
                      REFERENCES organization (id) ON DELETE CASCADE
                      DEFAULT current_setting('app.tenant_id', true)::uuid,
    source_id         UUID NOT NULL REFERENCES kb_sources (id) ON DELETE CASCADE,
    kind              TEXT NOT NULL
                      CONSTRAINT kb_sync_runs_kind_check
                      CHECK (kind IN ('discover', 'changes', 'reconcile')),
    status            TEXT NOT NULL DEFAULT 'running'
                      CONSTRAINT kb_sync_runs_status_check
                      CHECK (status IN ('running', 'ok', 'failed', 'stopped')),
    started_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at       TIMESTAMPTZ NULL,
    heartbeat_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    counts            JSONB NOT NULL DEFAULT '{}',
    error             TEXT NULL
                      CONSTRAINT kb_sync_runs_error_length
                      CHECK (error IS NULL OR char_length(error) <= 64)
);

-- The run lock: one running row for each source.
CREATE UNIQUE INDEX IF NOT EXISTS kb_sync_runs_one_running_idx
    ON kb_sync_runs (source_id)
    WHERE status = 'running';

-- The due check of the loop: the newest runs of one source.
CREATE INDEX IF NOT EXISTS kb_sync_runs_source_started_idx
    ON kb_sync_runs (source_id, started_at DESC);

-- When a run last added a node. NULL until a run inserts one.
ALTER TABLE kb_sources ADD COLUMN IF NOT EXISTS last_new_at TIMESTAMPTZ NULL;

DO $rls$
DECLARE
    t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY['kb_sync_cursors', 'kb_sync_runs'] LOOP
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

COMMENT ON TABLE kb_sync_cursors IS
    'WS-61 KC-3: where the sync of one source stands. changes holds the page '
    'token of the provider change feed. reconcile holds the last daily relist. '
    'A disconnect deletes the rows of its source. knowledge_curation.md §16.';

COMMENT ON TABLE kb_sync_runs IS
    'WS-61 KC-3: one row for each sync run of one source. The partial unique '
    'index on running rows is the run lock. knowledge_base.md §7.1.';

COMMIT;
