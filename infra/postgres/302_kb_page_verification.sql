-- ============================================================================
-- 302_kb_page_verification.sql — the owner and the review interval of a page.
--
-- What: two new columns on `kb_pages` and two indexes.
--       `owner_email` — the person who answers for the page. NULL means the
--                   page has no owner. A CHECK refuses an empty string.
--       `review_every_days` — the days from a publish or a verify to the next
--                   review date. NULL means the default of 180 days.
--       `idx_kb_pages_owner` — the maintenance list and the verified filter.
--       `idx_kb_pages_review` — a partial index on published pages, for the
--                   expired and verified tests on `review_at`.
-- Why:  WS-61 KB-AI-4, spec project-docs/specs/knowledge_base.md §22.3. A
--       page is verified when it has an owner AND a review date in the
--       future. There is no backfill: an old page reads as unowned until a
--       person claims it.
-- Depends on: 272_knowledge_pages.sql (kb_pages).
--
-- Expand only (R6): two nullable columns and two indexes. No rename, no drop,
-- no new table, no new policy. Every statement is safe to run twice.
-- The number is taken at build time (R1). Check it again at merge.
-- ============================================================================

SET lock_timeout = '5s';

BEGIN;

ALTER TABLE kb_pages
    ADD COLUMN IF NOT EXISTS owner_email TEXT NULL
        CHECK (owner_email IS NULL OR owner_email <> '');

ALTER TABLE kb_pages
    ADD COLUMN IF NOT EXISTS review_every_days INTEGER NULL
        CHECK (review_every_days BETWEEN 1 AND 3650);

CREATE INDEX IF NOT EXISTS idx_kb_pages_owner
    ON kb_pages (organization_id, owner_email);

CREATE INDEX IF NOT EXISTS idx_kb_pages_review
    ON kb_pages (organization_id, review_at) WHERE state = 'published';

COMMIT;
