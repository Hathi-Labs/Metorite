-- ============================================================================
-- 292_kb_page_proposals.sql — the proposed edits of a Knowledge page.
--
-- What: one new table and one partial index.
--       `kb_page_proposals` — one row for each edit that the assistant
--                          proposed for a page, at the version it read
--                          (`base_version`). A person accepts it in the app,
--                          and the edit applies only while the page is still
--                          at that version. A proposed NEW page is not here:
--                          it is a draft in `kb_pages` with `origin.kind =
--                          'agent_proposal'`, because a page has one state,
--                          and a draft of a published page would unpublish it.
--       `idx_kb_page_proposals_pending` — the Review list: the pending rows
--                          of one organization, oldest first. KB-5c's
--                          14-day expiry reads the same index.
-- Why:  WS-61 KB-5a, spec project-docs/specs/knowledge_base.md §5.3, §8.1
--       (F1, F2) and §12.
-- Depends on: 130_org_access_control.sql (organization),
--       272_knowledge_pages.sql (kb_pages).
--
-- Expand only (R6): one new table and one index. No rename, no drop, and no
-- row changes. The old gateway never names the table, so it meets no
-- refusal while the deploy runs.
-- Idempotent per infra/postgres/README.md: CREATE TABLE IF NOT EXISTS,
-- CREATE INDEX IF NOT EXISTS, and an RLS block guarded on
-- relforcerowsecurity. A second run changes nothing.
-- Tenant-scoped (R5): `organization_id` with ENABLE and FORCE row level
-- security and one policy with USING and WITH CHECK, in the shape of
-- 272_knowledge_pages.sql and 291_kb_page_icon_favorites.sql. `REFERENCES`
-- comes before `DEFAULT`, because the tenancy ratchet in
-- tests/unit/test_tenancy_boundary.py matches the two with no comma between.
-- The foreign key is inline, never `ADD CONSTRAINT <table>_org_fk`, because
-- generated phase 3 adds that name. The gateway takes `proposer_email` and
-- `decided_by` from the session (`core.viewer_email`), never from a body.
-- The page key cascades, so a page that is deleted leaves no proposal.
-- The number is taken at build time (R1). On 2026-10-22 main held 291, and
-- no open pull request held a 29x migration. Check it again at merge. The
-- tests find this file by CONTENT, never by number.
-- ============================================================================

SET lock_timeout = '5s';

BEGIN;

CREATE TABLE IF NOT EXISTS kb_page_proposals (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id  UUID NOT NULL
                     REFERENCES organization (id) ON DELETE CASCADE
                     DEFAULT current_setting('app.tenant_id', true)::uuid,
    page_id          UUID NOT NULL REFERENCES kb_pages (id) ON DELETE CASCADE,
    -- The page's newest version when the proposal was made. Accept applies
    -- the edit only while the page is still at it, else 409.
    base_version     INTEGER NOT NULL CHECK (base_version >= 1),
    title            TEXT NOT NULL
                     CHECK (char_length(title) BETWEEN 1 AND 300),
    body_md          TEXT NOT NULL DEFAULT ''
                     CHECK (char_length(body_md) <= 1000000),
    -- What the edit changes, in the assistant's words, for the card.
    summary          TEXT NOT NULL DEFAULT ''
                     CHECK (char_length(summary) <= 1000),
    proposer_email   TEXT NOT NULL CHECK (proposer_email <> ''),
    -- §5.4: through what the proposal came.
    via              TEXT NOT NULL DEFAULT 'chat'
                     CHECK (via IN ('web', 'chat', 'mcp', 'sync', 'artifact')),
    status           TEXT NOT NULL DEFAULT 'pending'
                     CHECK (status IN ('pending', 'accepted', 'rejected', 'expired')),
    decided_by       TEXT NULL,
    decided_at       TIMESTAMPTZ NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK ((status = 'pending') = (decided_at IS NULL))
);

CREATE INDEX IF NOT EXISTS idx_kb_page_proposals_pending
    ON kb_page_proposals (organization_id, status, created_at)
    WHERE status = 'pending';

DO $rls$
DECLARE
    t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY['kb_page_proposals'] LOOP
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

COMMENT ON TABLE kb_page_proposals IS
    'WS-61 KB-5a: an edit of a Knowledge page that the assistant proposed, '
    'at base_version. A person accepts it in the app (via web). Each read '
    'joins kb_pages under core.visible_pages_clause. knowledge_base.md §12.';

COMMIT;
