-- live_activity_order.sql — the activity spine orders deterministically (H-159)
--
-- Run:  docker exec -i metorite-scratch-tenant psql -U acb -d acb_tenant \
--         -v ON_ERROR_STOP=1 -q < tests/live/live_activity_order.sql
--
-- R8. The hermetic suite proves the code ASKS for the right order. Only a real
-- Postgres proves the claim the whole fix rests on: that `now()` is the
-- transaction timestamp, so same-transaction rows tie and a sequence is what
-- separates them.

BEGIN;

CREATE OR REPLACE FUNCTION aorder_check(label text, got anyelement, want anyelement)
RETURNS void LANGUAGE plpgsql AS $$
BEGIN
    IF got IS NOT DISTINCT FROM want THEN
        RAISE NOTICE 'ok   %', label;
    ELSE
        RAISE EXCEPTION 'FAIL %: got % want %', label, got, want;
    END IF;
END $$;

INSERT INTO organization (id, slug, display_name)
VALUES ('a0000000-0000-0000-0000-0000000000a0','aorder-org','Aorder Ltd')
ON CONFLICT DO NOTHING;

INSERT INTO pm_projects (id, organization_id, name, created_by, owns_statuses)
VALUES ('a0100000-0000-0000-0000-000000000001',
        'a0000000-0000-0000-0000-0000000000a0','Spine','a@aorder.invalid', true);

-- ── 1. The column exists, and it is a sequence ─────────────────────────────
SELECT aorder_check('1a pm_activities.seq exists',
    (SELECT count(*) FROM information_schema.columns
      WHERE table_name = 'pm_activities' AND column_name = 'seq'), 1::bigint);
SELECT aorder_check('1b it is NOT NULL and defaulted from a sequence',
    (SELECT column_default LIKE 'nextval%' AND is_nullable = 'NO'
       FROM information_schema.columns
      WHERE table_name = 'pm_activities' AND column_name = 'seq'), true);

-- ── 2. now() is the TRANSACTION timestamp — the premise of the whole fix ───
--
-- Three rows, one statement apart, inside ONE transaction. If this returns
-- more than 1 the premise is wrong and the tie-break is unnecessary.
INSERT INTO pm_activities (project_id, type, body, created_by)
VALUES ('a0100000-0000-0000-0000-000000000001','field_change','v2','a@aorder.invalid');
INSERT INTO pm_activities (project_id, type, body, created_by)
VALUES ('a0100000-0000-0000-0000-000000000001','comment','looks good','a@aorder.invalid');
INSERT INTO pm_activities (project_id, type, body, created_by)
VALUES ('a0100000-0000-0000-0000-000000000001','field_change','v3','a@aorder.invalid');

SELECT aorder_check('2a three rows in one transaction share ONE created_at',
    (SELECT count(DISTINCT created_at) FROM pm_activities
      WHERE project_id = 'a0100000-0000-0000-0000-000000000001'), 1::bigint);

-- ── 3. seq separates them, and in insertion order ──────────────────────────
SELECT aorder_check('3a the newest row is the last INSERTED, not a random uuid',
    (SELECT body FROM pm_activities
      WHERE project_id = 'a0100000-0000-0000-0000-000000000001'
      ORDER BY created_at DESC, seq DESC LIMIT 1), 'v3');

-- This is the read `_coalescible_prior` makes. The intervening comment must be
-- what it finds, because that is what BREAKS the coalescing run. Before 213
-- the answer here was decided by `gen_random_uuid()`.
SELECT aorder_check('3b the row before the newest is the INTERVENING comment',
    (SELECT body FROM pm_activities
      WHERE project_id = 'a0100000-0000-0000-0000-000000000001'
      ORDER BY created_at DESC, seq DESC LIMIT 1 OFFSET 1), 'looks good');

-- ── 4. The paged read is stable across two queries ─────────────────────────
--
-- Two pages are two statements. An unstable sort shows a row twice or skips
-- one, and no single query can demonstrate that.
SELECT aorder_check('4a page 1 then page 2 cover all three rows exactly once',
    (SELECT count(DISTINCT body) FROM (
        (SELECT body FROM pm_activities
          WHERE project_id = 'a0100000-0000-0000-0000-000000000001'
          ORDER BY created_at DESC, seq DESC LIMIT 2 OFFSET 0)
        UNION ALL
        (SELECT body FROM pm_activities
          WHERE project_id = 'a0100000-0000-0000-0000-000000000001'
          ORDER BY created_at DESC, seq DESC LIMIT 2 OFFSET 2)
     ) pages), 3::bigint);

-- ── 5. An older row with an EXPLICIT created_at still sorts by its own time ─
--
-- `seq` settles ties. It must not override an imported history's timestamp,
-- which is why `created_at` stays the first key.
INSERT INTO pm_activities (project_id, type, body, created_by, created_at)
VALUES ('a0100000-0000-0000-0000-000000000001','comment','imported',
        'a@aorder.invalid', now() - interval '30 days');
SELECT aorder_check('5a the imported row sorts LAST despite the highest seq',
    (SELECT body FROM pm_activities
      WHERE project_id = 'a0100000-0000-0000-0000-000000000001'
      ORDER BY created_at DESC, seq DESC LIMIT 1 OFFSET 3), 'imported');

ROLLBACK;

\echo ''
\echo '════════════════════════════════════════════════════════'
\echo '  Activity order (mig 213): all checks passed.'
\echo '════════════════════════════════════════════════════════'
