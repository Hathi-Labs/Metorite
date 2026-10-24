-- ============================================================================
-- 293_kb_gaps.sql — the questions that Knowledge could not answer.
--
-- What: one new table and two indexes.
--       `kb_gaps` — one row for each normalised question that a member asked
--                   through `search_company` and that Knowledge answered with
--                   no hit. `askers` holds 20 member addresses at most, and
--                   `ask_count` counts the different askers of the current
--                   30-day window. `status` is open, answered or dismissed.
--                   `answered_page_id` names the page that answers it. The
--                   key is SET NULL, so a page delete leaves the gap.
--       `kb_gaps_org_norm_key` — one row for each question of an org.
--       `idx_kb_gaps_open` — the list and the My Day read:
--                   (organization_id, status, last_asked_at DESC).
-- Why:  WS-61 KB-9a, spec project-docs/specs/knowledge_base.md §8.4 door 3,
--       §9 and §12. A question that two members asked and nobody wrote down
--       becomes a My Day card for a manager of Knowledge.
-- Depends on: 130_org_access_control.sql (organization),
--       272_knowledge_pages.sql (kb_pages).
--
-- Expand only (R6): one new table and its indexes. No rename, no drop, and
-- no row changes. The old gateway never names the table, so it meets no
-- refusal during the deploy.
-- Idempotent per infra/postgres/README.md: CREATE TABLE IF NOT EXISTS,
-- CREATE INDEX IF NOT EXISTS, and an RLS block guarded on
-- relforcerowsecurity. A second run changes nothing.
-- Tenant-scoped (R5): `organization_id` with ENABLE and FORCE row level
-- security and one policy with USING and WITH CHECK, in the shape of
-- 272_knowledge_pages.sql and 291_kb_page_icon_favorites.sql. `REFERENCES`
-- comes before `DEFAULT`, because the tenancy ratchet in
-- tests/unit/test_tenancy_boundary.py matches the two with no comma between.
-- The foreign key is inline, never `ADD CONSTRAINT <table>_org_fk`, because
-- generated phase 3 adds that name. The gateway takes each address from the
-- session (`core.viewer_email`), never from a request body.
-- The number is taken at build time (R1). On 2026-10-22 main held 291, and
-- the unmerged KB-5a branch held 292. Check it again at merge. The tests
-- find this file by CONTENT, never by number.
-- ============================================================================

SET lock_timeout = '5s';

BEGIN;

CREATE TABLE IF NOT EXISTS kb_gaps (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id   UUID NOT NULL
                      REFERENCES organization (id) ON DELETE CASCADE
                      DEFAULT current_setting('app.tenant_id', true)::uuid,
    norm_query        TEXT NOT NULL
                      CONSTRAINT kb_gaps_norm_query_length
                      CHECK (char_length(norm_query) BETWEEN 1 AND 300),
    sample_query      TEXT NOT NULL
                      CONSTRAINT kb_gaps_sample_query_length
                      CHECK (char_length(sample_query) BETWEEN 1 AND 300),
    askers            TEXT[] NOT NULL DEFAULT '{}'
                      CONSTRAINT kb_gaps_askers_cap
                      CHECK (coalesce(array_length(askers, 1), 0) <= 20),
    ask_count         INTEGER NOT NULL DEFAULT 1
                      CONSTRAINT kb_gaps_ask_count_positive CHECK (ask_count >= 1),
    first_asked_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_asked_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    status            TEXT NOT NULL DEFAULT 'open'
                      CONSTRAINT kb_gaps_status_check
                      CHECK (status IN ('open', 'answered', 'dismissed')),
    answered_page_id  UUID NULL REFERENCES kb_pages (id) ON DELETE SET NULL,
    dismissed_by      TEXT NULL,
    dismissed_at      TIMESTAMPTZ NULL,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT kb_gaps_org_norm_key UNIQUE (organization_id, norm_query)
);

CREATE INDEX IF NOT EXISTS idx_kb_gaps_open
    ON kb_gaps (organization_id, status, last_asked_at DESC);

-- The SET NULL of a page delete.
CREATE INDEX IF NOT EXISTS idx_kb_gaps_answered_page
    ON kb_gaps (answered_page_id) WHERE answered_page_id IS NOT NULL;

DO $rls$
DECLARE
    t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY['kb_gaps'] LOOP
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

COMMENT ON TABLE kb_gaps IS
    'WS-61 KB-9a: a question that search_company asked and Knowledge did not '
    'answer. The gateway writes it after the search answers, behind '
    'KNOWLEDGE_GAPS. knowledge_base.md §8.4 door 3.';

COMMENT ON COLUMN kb_gaps.askers IS
    'WS-61 KB-9a: the member addresses of the current 30-day window, 20 at '
    'most. The member purge removes an address (routes/admin/members.py).';

COMMIT;
