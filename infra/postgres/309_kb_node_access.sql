-- ============================================================================
-- 309_kb_node_access.sql — the readers of each Drive document, kept from the
-- provider, and the time of the last full access pass.
--
-- What: one new table, two indexes, one new nullable column and two widened
--       CHECKs.
--       `kb_node_access` — one row for each reader of one node at the
--                   provider, in the provider's words: `principal_kind`
--                   (`user`, `group`, `domain` or `anyone`), `principal`
--                   (a lower-case email or domain, `*` for anyone), the
--                   provider `role` (32 characters at most) and `synced_at`.
--                   UNIQUE on (organization_id, node_id, principal_kind,
--                   principal). The foreign key to kb_source_nodes cascades,
--                   so a disconnect, a member purge and a discovery that
--                   clears the tree take the rows with their nodes.
--       `kb_sources.access_read_at` — when the last FULL access pass of the
--                   source ended. NULL until one ends. A pass that stops at
--                   its call cap leaves it as it was.
--       `kb_sync_runs_kind_check` and `kb_sync_cursors_kind_check` — each
--                   adds `access`. Every old value stays legal.
-- Why:  WS-61 KC-4a, spec project-docs/specs/knowledge_curation.md §7.2 step 6,
--       §8, §16 and §22. A resolver turns these rows into proven readers.
--       The rows themselves are never a grant.
-- Depends on: 130_org_access_control.sql (organization),
--       300_kb_sources.sql (kb_sources, kb_source_nodes),
--       308_kb_sync.sql (kb_sync_runs, kb_sync_cursors).
--
-- Expand only (R6): one new table, two new indexes, one nullable column with
-- no default, and two CHECKs that accept MORE values than before. No rename,
-- no drop of data, no UPDATE, and no row changes. The old gateway writes
-- only the old kinds, and they stay legal.
-- The widened CHECK: the DO block drops it and adds it back NOT VALID, then
-- validates it, in the one transaction of this file, as 275 does. It runs
-- only while pg_get_constraintdef lacks 'access', so a second run changes
-- nothing.
-- Idempotent per infra/postgres/README.md: CREATE TABLE IF NOT EXISTS,
-- CREATE INDEX IF NOT EXISTS, ADD COLUMN IF NOT EXISTS, the guarded CHECK
-- block, and an RLS block guarded on relforcerowsecurity.
-- Tenant-scoped (R5): the table has `organization_id` with ENABLE and FORCE
-- row level security and one policy with USING and WITH CHECK, in the shape
-- of 308_kb_sync.sql. `REFERENCES` comes before `DEFAULT`, because the
-- tenancy ratchet in tests/unit/test_tenancy_boundary.py matches the two
-- with no comma between. The foreign key is inline, never
-- `ADD CONSTRAINT <table>_org_fk`, because generated phase 3 adds that name.
-- ⚠️ `principal` holds a reader's email, and the row is a child of a node.
-- The cascade from kb_source_nodes removes it, so the member purge needs no
-- new path.
-- The number is taken at build time (R1): 309 was free on main and on every
-- remote branch on 2026-10-25. Check it again at merge. The tests find this
-- file by CONTENT, never by number.
--
-- Fences: tests/unit/test_kc_access_r8.py (FORCE RLS as a role that is not a
-- superuser, not the owner and not BYPASSRLS, the widened CHECKs, and the
-- replay), tests/unit/test_tenant_coverage.py.
-- ============================================================================

SET lock_timeout = '5s';

BEGIN;

CREATE TABLE IF NOT EXISTS kb_node_access (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id   UUID NOT NULL
                      REFERENCES organization (id) ON DELETE CASCADE
                      DEFAULT current_setting('app.tenant_id', true)::uuid,
    node_id           UUID NOT NULL REFERENCES kb_source_nodes (id) ON DELETE CASCADE,
    principal_kind    TEXT NOT NULL
                      CONSTRAINT kb_node_access_kind_check
                      CHECK (principal_kind IN ('user', 'group', 'domain', 'anyone')),
    principal         TEXT NOT NULL
                      CONSTRAINT kb_node_access_principal_check
                      CHECK (char_length(principal) BETWEEN 1 AND 320
                             AND principal = lower(principal)),
    role              TEXT NOT NULL
                      CONSTRAINT kb_node_access_role_length
                      CHECK (char_length(role) BETWEEN 1 AND 32),
    synced_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT kb_node_access_principal_key
        UNIQUE (organization_id, node_id, principal_kind, principal)
);

-- The reader lookup of the visibility clause: one principal, every node.
CREATE INDEX IF NOT EXISTS kb_node_access_principal_idx
    ON kb_node_access (organization_id, principal_kind, principal);

-- The cascade from kb_source_nodes deletes by node_id alone.
CREATE INDEX IF NOT EXISTS kb_node_access_node_idx
    ON kb_node_access (node_id);

-- When the last full access pass ended. NULL until one ends.
ALTER TABLE kb_sources ADD COLUMN IF NOT EXISTS access_read_at TIMESTAMPTZ NULL;

-- The run kind and the cursor kind of the access pass.
DO $widen$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = 'kb_sync_runs'::regclass
           AND conname = 'kb_sync_runs_kind_check'
           AND pg_get_constraintdef(oid) LIKE '%''access''%'
    ) THEN
        ALTER TABLE kb_sync_runs DROP CONSTRAINT IF EXISTS kb_sync_runs_kind_check;
        ALTER TABLE kb_sync_runs ADD CONSTRAINT kb_sync_runs_kind_check
            CHECK (kind IN ('discover', 'changes', 'reconcile', 'access')) NOT VALID;
        ALTER TABLE kb_sync_runs VALIDATE CONSTRAINT kb_sync_runs_kind_check;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = 'kb_sync_cursors'::regclass
           AND conname = 'kb_sync_cursors_kind_check'
           AND pg_get_constraintdef(oid) LIKE '%''access''%'
    ) THEN
        ALTER TABLE kb_sync_cursors DROP CONSTRAINT IF EXISTS kb_sync_cursors_kind_check;
        ALTER TABLE kb_sync_cursors ADD CONSTRAINT kb_sync_cursors_kind_check
            CHECK (kind IN ('changes', 'reconcile', 'access')) NOT VALID;
        ALTER TABLE kb_sync_cursors VALIDATE CONSTRAINT kb_sync_cursors_kind_check;
    END IF;
END
$widen$;

DO $rls$
DECLARE
    t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY['kb_node_access'] LOOP
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

COMMENT ON TABLE kb_node_access IS
    'WS-61 KC-4a: the readers of one node at the provider, in the provider '
    'words. A resolver proves a member from a row. A row is never a grant. '
    'knowledge_curation.md §8.';

COMMENT ON COLUMN kb_sources.access_read_at IS
    'WS-61 KC-4a: when the last full access pass of the source ended. NULL '
    'until one ends. knowledge_curation.md §7.2 step 6.';

COMMIT;
