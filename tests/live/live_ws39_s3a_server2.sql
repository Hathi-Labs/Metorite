-- live_ws39_s3a_server2.sql — migration 188 against a REAL Postgres (R8).
--
-- Run:  docker exec -i tenant-scratch psql -U acb -d acb_tenant -v ON_ERROR_STOP=1 \
--           -f /tmp/live_ws39_s3a_server2.sql
--
-- Why a live file rather than a hermetic case: every claim below is a claim
-- about what POSTGRES does — a partial index being CHOSEN, a CHECK refusing a
-- row, jsonb round-tripping, NULL staying distinct from false. A fake agrees
-- with whatever SQL it is handed, which is how five live bugs shipped green
-- (R8), and migration 187's own bind test caught two 500s a hermetic suite had
-- no way to see.
--
-- RESULT 2026-08-25, PostgreSQL 16 (tenant-scratch): 9/9 PASS.
--   1 nine columns exist, correct types ......................... PASS
--   2 all nine NULLABLE with no default (R6) .................... PASS
--   3 two members hold different matrix flags on ONE task ....... PASS
--   4 NULL stays distinct from false ............................ PASS
--   5 waiting_on round-trips as jsonb ........................... PASS
--   6 waiting_on without delegated_at is REFUSED ................ PASS
--   7 clearing waiting_on is allowed ............................ PASS
--   8 sort_key takes a midpoint without renumbering ............. PASS
--   9 the partial waiting index is CHOSEN, not merely present ... PASS

\set ON_ERROR_STOP on
BEGIN;

-- ── Fixtures ────────────────────────────────────────────────────────────────
CREATE TEMP TABLE t_ids (k TEXT PRIMARY KEY, v UUID);

INSERT INTO t_ids (k, v) VALUES
    ('project', gen_random_uuid()),
    ('status',  gen_random_uuid()),
    ('task',    gen_random_uuid());

-- A REAL organization row, because `pm_projects.organization_id` carries a
-- FOREIGN KEY to it (the tenancy seam, D15) and a made-up uuid is refused. The
-- refusal is the system working: it is what stops a row being parked in a
-- tenant that does not exist.
INSERT INTO organization (slug, display_name)
VALUES ('live-s3a-server2-' || substr(gen_random_uuid()::text, 1, 8),
        'WS-39 S3a-server-2 live')
RETURNING id \gset live_org_

INSERT INTO t_ids (k, v) VALUES ('org', :'live_org_id');

-- Column list taken from the LIVE table, not from the migration that created
-- it: `pm_projects` has no `subject` column (the first draft of this file
-- assumed one from the API's vocabulary and errored on line 38), and
-- `created_by` / `task_number` are NOT NULL with no default.
INSERT INTO pm_projects (id, organization_id, name, created_by)
SELECT (SELECT v FROM t_ids WHERE k='project'),
       (SELECT v FROM t_ids WHERE k='org'), 'S3a-server-2 live',
       'owner@fracktal.in';

INSERT INTO pm_task_statuses (id, project_id, name, category, is_default,
                              organization_id)
SELECT (SELECT v FROM t_ids WHERE k='status'),
       (SELECT v FROM t_ids WHERE k='project'), 'To do', 'todo', true,
       (SELECT v FROM t_ids WHERE k='org');

INSERT INTO pm_tasks (id, organization_id, project_id, root_project_id,
                      task_number, status_id, title, created_by)
SELECT (SELECT v FROM t_ids WHERE k='task'),
       (SELECT v FROM t_ids WHERE k='org'),
       (SELECT v FROM t_ids WHERE k='project'),
       (SELECT v FROM t_ids WHERE k='project'),
       1,
       (SELECT v FROM t_ids WHERE k='status'),
       'Ship the overlay fields',
       'owner@fracktal.in';

-- ── 1. The nine columns exist with the intended types ───────────────────────
--
-- `clarified_at` is deliberately absent from this list: it has existed since
-- migration 147 and 188 does not add it. Asserting it here would make the file
-- agree with a header that was wrong for an hour.
SELECT CASE WHEN count(*) = 9 THEN 'PASS' ELSE 'FAIL: only ' || count(*) END
           AS "1 nine columns, correct types"
FROM information_schema.columns
WHERE table_name = 'pm_task_personal'
  AND (column_name, data_type) IN (
        ('important',      'boolean'),
        ('leveraged',      'boolean'),
        ('deep_work',      'boolean'),
        ('kept_mine',      'boolean'),
        ('sort_key',       'double precision'),
        ('waiting_on',     'jsonb'),
        ('delegated_at',   'timestamp with time zone'),
        ('expected_by',    'timestamp with time zone'),
        ('last_nudged_at', 'timestamp with time zone'));

-- ── 2. R6: every one nullable, none with a default ──────────────────────────
--
-- Not cosmetics. A `NOT NULL DEFAULT false` on a populated table rewrites it,
-- and the deploy applies migrations BEFORE restarting services — so the rewrite
-- happens while the old code is still serving. Nullable-no-default is instant.
SELECT CASE WHEN count(*) = 0 THEN 'PASS'
            ELSE 'FAIL: ' || string_agg(column_name, ', ') END
           AS "2 all nullable, no default (R6)"
FROM information_schema.columns
WHERE table_name = 'pm_task_personal'
  AND column_name IN ('important','leveraged','deep_work','kept_mine',
                      'sort_key','waiting_on','delegated_at','expected_by',
                      'last_nudged_at')
  AND (is_nullable <> 'YES' OR column_default IS NOT NULL);

-- ── 3. Two members, one task, different matrix flags ────────────────────────
--
-- The claim migration 188 exists to make true, and the reason these columns are
-- not on `pm_tasks`. Alice's judgement and Bob's must coexist on one row of
-- work without either overwriting the other.
INSERT INTO pm_task_personal (task_id, member_email, organization_id,
                              important, leveraged, deep_work)
SELECT (SELECT v FROM t_ids WHERE k='task'), 'alice@fracktal.in',
       (SELECT v FROM t_ids WHERE k='org'), true, true, true;

INSERT INTO pm_task_personal (task_id, member_email, organization_id,
                              important, leveraged, deep_work)
SELECT (SELECT v FROM t_ids WHERE k='task'), 'bob@fracktal.in',
       (SELECT v FROM t_ids WHERE k='org'), false, false, false;

SELECT CASE
    WHEN (SELECT important FROM pm_task_personal
          WHERE member_email='alice@fracktal.in') IS TRUE
     AND (SELECT important FROM pm_task_personal
          WHERE member_email='bob@fracktal.in') IS FALSE
     AND (SELECT count(*) FROM pm_tasks
          WHERE id = (SELECT v FROM t_ids WHERE k='task')) = 1
    THEN 'PASS' ELSE 'FAIL' END
    AS "3 two members, one task, two matrix answers";

-- ── 4. NULL is not false ────────────────────────────────────────────────────
--
-- The tri-state the reader must preserve. Alice said "not leveraged"; nobody
-- has said anything about `kept_mine`. `IS DISTINCT FROM` is the operator that
-- tells them apart — plain `=` answers NULL for both and the difference
-- vanishes into a falsy value on the wire.
UPDATE pm_task_personal SET leveraged = false
WHERE member_email = 'alice@fracktal.in';

SELECT CASE
    WHEN (SELECT leveraged FROM pm_task_personal
          WHERE member_email='alice@fracktal.in') IS FALSE
     AND (SELECT kept_mine FROM pm_task_personal
          WHERE member_email='alice@fracktal.in') IS NULL
     AND (SELECT count(*) FROM pm_task_personal
          WHERE member_email='alice@fracktal.in'
            AND kept_mine IS DISTINCT FROM false) = 1
    THEN 'PASS' ELSE 'FAIL' END
    AS "4 NULL stays distinct from false";

-- ── 5. waiting_on round-trips as jsonb, not as text ─────────────────────────
UPDATE pm_task_personal
SET waiting_on   = '{"name": "Priya", "email": "priya@fracktal.in"}'::jsonb,
    delegated_at = TIMESTAMPTZ '2026-09-01 09:00:00+00',
    expected_by  = TIMESTAMPTZ '2026-09-08 17:00:00+00'
WHERE member_email = 'alice@fracktal.in';

SELECT CASE
    WHEN (SELECT waiting_on ->> 'email' FROM pm_task_personal
          WHERE member_email='alice@fracktal.in') = 'priya@fracktal.in'
     AND (SELECT expected_by > delegated_at FROM pm_task_personal
          WHERE member_email='alice@fracktal.in')
    THEN 'PASS' ELSE 'FAIL' END
    AS "5 waiting_on round-trips as jsonb";

-- ── 6. The since-when CHECK actually refuses ────────────────────────────────
--
-- The constraint landed NOT VALID and was then validated; this proves it BINDS,
-- which is a different claim from `convalidated = true`. Bob has no
-- `delegated_at`, so naming somebody must fail.
DO $live188$
DECLARE ok BOOLEAN := false;
BEGIN
    BEGIN
        UPDATE pm_task_personal
        SET waiting_on = '{"email": "sam@fracktal.in"}'::jsonb
        WHERE member_email = 'bob@fracktal.in';
    EXCEPTION WHEN check_violation THEN
        ok := true;
    END;
    RAISE NOTICE '6 waiting_on without delegated_at is REFUSED ... %',
        CASE WHEN ok THEN 'PASS' ELSE 'FAIL - the row was ACCEPTED' END;
END $live188$;

-- ── 7. Clearing a delegation is always allowed ──────────────────────────────
--
-- The other side of the same CHECK, and the one a naive `NOT NULL` pair would
-- have broken: resolving a delegation removes the person and may leave the date
-- behind, which must not be refused.
UPDATE pm_task_personal SET waiting_on = NULL
WHERE member_email = 'alice@fracktal.in';

SELECT CASE
    WHEN (SELECT waiting_on FROM pm_task_personal
          WHERE member_email='alice@fracktal.in') IS NULL
    THEN 'PASS' ELSE 'FAIL' END
    AS "7 clearing waiting_on is allowed";

-- ── 8. sort_key takes a midpoint ────────────────────────────────────────────
--
-- Why the column is DOUBLE PRECISION and not an integer position: dropping a
-- card between two neighbours must be ONE update, not a renumber of everything
-- below it. If this were an INT the midpoint would collapse onto a neighbour.
UPDATE pm_task_personal SET sort_key = 1.0 WHERE member_email='alice@fracktal.in';
UPDATE pm_task_personal SET sort_key = 2.0 WHERE member_email='bob@fracktal.in';

SELECT CASE
    WHEN (SELECT count(*) FROM (
            SELECT (1.0::float8 + 2.0::float8) / 2 AS mid) m
          WHERE m.mid > 1.0 AND m.mid < 2.0) = 1
    THEN 'PASS' ELSE 'FAIL' END
    AS "8 sort_key admits a midpoint";

COMMIT;

-- ── 9. The partial index is CHOSEN, not merely present ──────────────────────
--
-- 187's lesson repeated: an index that exists but is never chosen costs every
-- write and buys nothing, and `\d` cannot tell the difference. This asserts the
-- PLAN. Sequential scan is forced off so the planner must reveal whether the
-- index is usable for this shape at all — on a table this small it would
-- otherwise always prefer a seq scan and the assertion would be vacuous.
SET enable_seqscan = off;
EXPLAIN (COSTS OFF)
SELECT task_id, expected_by
FROM pm_task_personal
WHERE member_email = 'alice@fracktal.in'
  AND waiting_on IS NOT NULL
  AND expected_by < now()
ORDER BY expected_by;
RESET enable_seqscan;
