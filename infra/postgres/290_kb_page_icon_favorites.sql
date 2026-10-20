-- ============================================================================
-- 290_kb_page_icon_favorites.sql — the icon of a Knowledge page, and the
-- favourites of a member.
--
-- What: one new column, one new table and two indexes.
--       `kb_pages.icon`  — one emoji, or NULL. A CHECK keeps it to 16
--                          characters. It is NOT the icon NAME of
--                          `kb_spaces.icon`, which holds a Lucide name.
--       `kb_favorites`   — one row for each page that a member starred. The
--                          primary key is (organization_id, member_email,
--                          page_id). The page key cascades, so a page that
--                          is deleted leaves no favourite.
--       `idx_kb_pages_recent` — the order of "Recently updated":
--                          (organization_id, updated_at DESC, id DESC).
--       `idx_kb_favorites_page` — the cascade of a page delete.
-- Why:  WS-61 KB-2f, spec project-docs/specs/knowledge_base.md §21.7. The
--       Knowledge home lists the pages that changed, the member's
--       favourites and the member's own pages, and each page shows its
--       icon in the tree, the title, search and the home lists.
-- Depends on: 130_org_access_control.sql (organization),
--       272_knowledge_pages.sql (kb_pages).
--
-- Expand only (R6): one nullable column with no default, one new table and
-- two indexes. No rename, no drop, and no row changes. The old gateway runs
-- against this schema for a moment during the deploy. It never names the
-- new column or the new table, so it meets no refusal.
-- Idempotent per infra/postgres/README.md: ADD COLUMN IF NOT EXISTS, CREATE
-- TABLE IF NOT EXISTS, CREATE INDEX IF NOT EXISTS, and an RLS block guarded
-- on relforcerowsecurity. A second run changes nothing.
-- Tenant-scoped (R5): `kb_favorites.organization_id` with ENABLE and FORCE
-- row level security and one policy with USING and WITH CHECK, in the shape
-- of 272_knowledge_pages.sql. `REFERENCES` comes before `DEFAULT`, because
-- the tenancy ratchet in tests/unit/test_tenancy_boundary.py matches the two
-- with no comma between. The foreign key is inline, never `ADD CONSTRAINT
-- <table>_org_fk`, because generated phase 3 adds that name. The gateway
-- takes `member_email` from the session (`core.viewer_email`), never from a
-- request body.
-- The number is taken at build time (R1). On 2026-10-20 main held 288, PR
-- #1192 held 289, PR #1162 held 286, and a branch held 285. The spec named
-- 287, which main took first. Check it again at merge. The tests find this
-- file by CONTENT, never by number.
-- ============================================================================

SET lock_timeout = '5s';

BEGIN;

-- ── 1. The icon of a page ───────────────────────────────────────────────────

ALTER TABLE kb_pages
    ADD COLUMN IF NOT EXISTS icon TEXT NULL
        CONSTRAINT kb_pages_icon_length CHECK (char_length(icon) <= 16);

CREATE INDEX IF NOT EXISTS idx_kb_pages_recent
    ON kb_pages (organization_id, updated_at DESC, id DESC);

-- ── 2. The favourites of a member ───────────────────────────────────────────

CREATE TABLE IF NOT EXISTS kb_favorites (
    organization_id  UUID NOT NULL
                     REFERENCES organization (id) ON DELETE CASCADE
                     DEFAULT current_setting('app.tenant_id', true)::uuid,
    member_email     TEXT NOT NULL,
    page_id          UUID NOT NULL REFERENCES kb_pages (id) ON DELETE CASCADE,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (organization_id, member_email, page_id)
);

CREATE INDEX IF NOT EXISTS idx_kb_favorites_page
    ON kb_favorites (page_id);

DO $rls$
DECLARE
    t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY['kb_favorites'] LOOP
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

COMMENT ON COLUMN kb_pages.icon IS
    'WS-61 KB-2f: one emoji for the page, or NULL. Not the Lucide icon name '
    'of kb_spaces.icon. A change of only the icon writes no version and '
    'keeps updated_at. knowledge_base.md §21.7.';

COMMENT ON TABLE kb_favorites IS
    'WS-61 KB-2f: the pages that one member starred. Each read joins '
    'kb_pages under core.visible_pages_clause, so a page that becomes '
    'hidden drops out. knowledge_base.md §21.7.';

COMMIT;
