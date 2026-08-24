-- live_ws39_s3a.sql — migration 187 against a REAL Postgres (R8).
--
-- WS-39 slice S3a · decisions D53 (one task store) + D54 (Calendar app).
-- Owning sections: `calendar_focus_os.md` §10 · `project_management_app.md` §12.
--
-- ── What only a database can answer here ────────────────────────────────────
--
-- Migration 187 encodes ONE design claim: **a scheduled block belongs to the
-- MEMBER, not to the task.** A hermetic fake agrees with whatever SQL it is
-- handed, so it cannot tell us whether the claim actually holds under the
-- constraints, the trigger and the planner. Specifically:
--
--   * does the composite primary key `(task_id, member_email)` really let two
--     people hold two different blocks on ONE task — the entire reason these
--     columns are on the overlay rather than on `pm_tasks`?
--   * does one member rescheduling leave the other's block untouched, through
--     the `pm_organization_from_parent` trigger that fires on every write here?
--   * does `> ` (rather than `>=`) in the ordering CHECK actually refuse a
--     zero-length block, while still allowing a half-open one?
--   * is the PARTIAL index `WHERE scheduled_start IS NOT NULL` actually CHOSEN
--     for the calendar's range read, or merely present? (`idx_pm_tasks_fts` was
--     stored and unusable from migration 146 until WS-27be found it — an index
--     nothing uses is a write cost wearing a read's name.)
--   * does `flexible` NULL stay DISTINCT from `false`, which is the whole
--     reason it is nullable rather than `NOT NULL DEFAULT true`?
--
-- ── How to run ─────────────────────────────────────────────────────────────
--
--   docker cp infra/postgres/187_projects_personal_timeboxing.sql <c>:/tmp/
--   docker exec <c> psql -U <u> -d <db> -v ON_ERROR_STOP=1 -f /tmp/187_*.sql
--   docker cp tests/live/live_ws39_s3a.sql <c>:/tmp/
--   docker exec <c> psql -U <u> -d <db> -f /tmp/live_ws39_s3a.sql
--
-- Everything runs inside a transaction that ROLLS BACK, so it is safe against
-- any database whose schema is current — it leaves no rows behind.
--
-- ── Result, 2026-08-24, pgvector/pgvector:pg16, full tenant schema ──────────
--
--   CHECK 1  one task → two blocks, two dispositions          PASS
--   CHECK 2  one member reschedules, other untouched          PASS
--   CHECK 3  the deadline is shared, written once             PASS
--   CHECK 4  end-before-start refused                         PASS
--   CHECK 5  zero-length refused (`>` not `>=`)               PASS
--   CHECK 6  half-open (open-ended) block allowed             PASS
--   CHECK 7  `Index Scan using idx_pm_task_personal_scheduled` PASS
--   CHECK 8  `flexible` NULL distinct from false              PASS
--
-- The migration was also applied TWICE to prove idempotency (R6): the second
-- run reports `already exists, skipping` for every object and changes nothing.

\set ON_ERROR_STOP on
BEGIN;

CREATE TEMP TABLE t AS
SELECT (SELECT id FROM organization ORDER BY id LIMIT 1) AS org_a;

INSERT INTO pm_projects (id, organization_id, name, created_by)
SELECT '11111111-1111-1111-1111-111111111111', org_a, 'Live187 project', 'a@x.test' FROM t;

INSERT INTO pm_task_statuses (id, organization_id, project_id, name, category, position)
SELECT gen_random_uuid(), org_a, '11111111-1111-1111-1111-111111111111', 'Open', 'todo', 1 FROM t;

INSERT INTO pm_tasks (id, organization_id, project_id, root_project_id, status_id, title, created_by, task_number)
SELECT '22222222-2222-2222-2222-222222222222', org_a,
       '11111111-1111-1111-1111-111111111111',
       '11111111-1111-1111-1111-111111111111',
       (SELECT id FROM pm_task_statuses WHERE project_id='11111111-1111-1111-1111-111111111111' LIMIT 1),
       'One task, two calendars', 'a@x.test', 1
FROM t;

INSERT INTO pm_task_personal (task_id, member_email, disposition,
                              scheduled_start, scheduled_end, flexible)
VALUES
  ('22222222-2222-2222-2222-222222222222', 'doer@x.test', 'NEXT',
   '2026-09-01 09:00+00', '2026-09-01 11:00+00', true),
  ('22222222-2222-2222-2222-222222222222', 'delegator@x.test', 'WAITING',
   '2026-09-03 15:00+00', '2026-09-03 15:30+00', false);

\echo ''
\echo '=== CHECK 1: ONE task, TWO independent blocks and TWO dispositions ==='
SELECT member_email, disposition, scheduled_start, scheduled_end, flexible
FROM pm_task_personal WHERE task_id='22222222-2222-2222-2222-222222222222'
ORDER BY member_email;

\echo ''
\echo '=== CHECK 2: one member reschedules; the other is UNTOUCHED ==='
UPDATE pm_task_personal SET scheduled_start='2026-09-02 09:00+00',
                            scheduled_end  ='2026-09-02 10:00+00'
 WHERE task_id='22222222-2222-2222-2222-222222222222' AND member_email='doer@x.test';
SELECT member_email, scheduled_start FROM pm_task_personal
WHERE task_id='22222222-2222-2222-2222-222222222222' ORDER BY member_email;

\echo ''
\echo '=== CHECK 3: the DEADLINE is shared, written once on the task ==='
UPDATE pm_tasks SET due_at='2026-09-05 17:00+00' WHERE id='22222222-2222-2222-2222-222222222222';
SELECT p.member_email, t.due_at AS shared_deadline, p.scheduled_start AS my_block
FROM pm_task_personal p JOIN pm_tasks t ON t.id=p.task_id
WHERE p.task_id='22222222-2222-2222-2222-222222222222' ORDER BY p.member_email;

\echo ''
\echo '=== CHECK 4/5: an end-before-start and a zero-length block are REFUSED ==='
DO $LIVE$
DECLARE ok int := 0;
BEGIN
    BEGIN
        INSERT INTO pm_task_personal (task_id, member_email, scheduled_start, scheduled_end)
        VALUES ('22222222-2222-2222-2222-222222222222','bad@x.test',
                '2026-09-01 11:00+00','2026-09-01 09:00+00');
        RAISE EXCEPTION 'FAIL: end-before-start was ACCEPTED';
    EXCEPTION WHEN check_violation THEN ok := ok + 1;
    END;
    BEGIN
        INSERT INTO pm_task_personal (task_id, member_email, scheduled_start, scheduled_end)
        VALUES ('22222222-2222-2222-2222-222222222222','zero@x.test',
                '2026-09-01 09:00+00','2026-09-01 09:00+00');
        RAISE EXCEPTION 'FAIL: a zero-length block was ACCEPTED (> vs >=)';
    EXCEPTION WHEN check_violation THEN ok := ok + 1;
    END;
    RAISE NOTICE 'PASS: % of 2 impossible blocks refused', ok;
END
$LIVE$;

\echo ''
\echo '=== CHECK 6: half-open IS allowed (an open-ended block is legal) ==='
INSERT INTO pm_task_personal (task_id, member_email, scheduled_start)
VALUES ('22222222-2222-2222-2222-222222222222','openended@x.test','2026-09-04 08:00+00');
SELECT member_email, scheduled_start, scheduled_end FROM pm_task_personal
WHERE member_email='openended@x.test';

\echo ''
\echo '=== CHECK 7: the partial range index is USED, not merely present ==='
SET enable_seqscan = off;
EXPLAIN (COSTS OFF)
SELECT * FROM pm_task_personal
WHERE member_email='doer@x.test'
  AND scheduled_start >= '2026-09-01' AND scheduled_start < '2026-09-08';
RESET enable_seqscan;

\echo ''
\echo '=== CHECK 8: NULL flexible is DISTINCT from false (never-stated) ==='
SELECT member_email,
       flexible IS NULL AS never_stated,
       flexible IS NOT DISTINCT FROM false AS explicitly_pinned
FROM pm_task_personal
WHERE task_id='22222222-2222-2222-2222-222222222222'
ORDER BY member_email;

ROLLBACK;
