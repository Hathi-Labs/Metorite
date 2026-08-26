-- ============================================================================
-- live_ws39_s3b.sql — WS-39 S3b proven against REAL Postgres (R8).
--
-- §12.8 states the acceptance in one sentence: S3b must be proven **two-org**,
-- and the specific thing to prove is that a mis-mapped `member_email` cannot
-- publish one member's private task into another member's lens.
--
-- So this is not a smoke test. Every check below is a claim the backfill makes
-- about somebody's privacy, their data surviving, or the move being safe to run
-- twice. A hermetic fake agrees with whatever SQL it is handed (R8) — this runs
-- the real function against the real schema.
--
-- Run:  docker exec -i tenant-scratch psql -U acb -d acb_tenant \
--         -v ON_ERROR_STOP=1 < tests/live/live_ws39_s3b.sql
--
-- Self-cleaning: it drops its own fixtures first, so it is re-runnable.
-- ============================================================================

\set ON_ERROR_STOP on
\timing off

-- ── Teardown from any previous run ──────────────────────────────────────────
DELETE FROM pm_task_personal WHERE member_email LIKE '%@s3btest.invalid';
DELETE FROM pm_task_assignees WHERE assignee LIKE '%@s3btest.invalid';
DELETE FROM pm_tasks         WHERE created_by LIKE '%@s3btest.invalid';
DELETE FROM pm_task_counters WHERE project_id IN
    (SELECT id FROM pm_projects WHERE created_by LIKE '%@s3btest.invalid');
DELETE FROM pm_task_statuses WHERE project_id IN
    (SELECT id FROM pm_projects WHERE created_by LIKE '%@s3btest.invalid');
DELETE FROM pm_project_grants WHERE subject LIKE '%@s3btest.invalid';
DELETE FROM pm_projects      WHERE created_by LIKE '%@s3btest.invalid';
-- ⚠️ The unowned fixtures are matched by ID, not by `user_id`: one of them is
-- the literal 'anonymous', which no `%s3btest%` pattern can reach. Deleting
-- only by pattern left it behind and the second run died on a primary-key
-- collision — so "self-cleaning" was a claim this file did not honour until
-- the mutation run exposed it.
DELETE FROM gtd_waiting      WHERE item_id IN
    (SELECT id FROM gtd_items WHERE user_id LIKE '%s3btest%')
   OR item_id IN ('f1000000-0000-0000-0000-00000000f101',
                  'f1000000-0000-0000-0000-00000000f102');
DELETE FROM gtd_items        WHERE user_id LIKE '%s3btest%'
   OR id IN ('f1000000-0000-0000-0000-00000000f101',
             'f1000000-0000-0000-0000-00000000f102');
DELETE FROM gtd_projects     WHERE user_id LIKE '%s3btest%';
DELETE FROM app_user         WHERE email LIKE '%@s3btest.invalid';
DELETE FROM organization     WHERE slug LIKE 's3btest-%';

-- ── Two tenants, two people ─────────────────────────────────────────────────
INSERT INTO organization (id, slug, display_name) VALUES
  ('a5100000-0000-0000-0000-0000000000a1','s3btest-alpha','Alpha Ltd'),
  ('b5100000-0000-0000-0000-0000000000b1','s3btest-beta','Beta Ltd');

INSERT INTO app_user (email, display_name, organization_id) VALUES
  ('dana@s3btest.invalid','Dana','a5100000-0000-0000-0000-0000000000a1'),
  ('erin@s3btest.invalid','Erin','b5100000-0000-0000-0000-0000000000b1');

-- Dana keeps a LOCAL project and a SYNCED one (connector residue, D52).
INSERT INTO gtd_projects (id, user_id, source, outcome) VALUES
  ('d0000000-0000-0000-0000-00000000d001','dana@s3btest.invalid','LOCAL','Kitchen Reno');
INSERT INTO gtd_projects (id, user_id, source, outcome, provider_ref) VALUES
  ('d0000000-0000-0000-0000-00000000d002','dana@s3btest.invalid','SYNCED','Dead ClickUp List','x1');

-- Dana's items: one plain, one in the LOCAL project, one in the SYNCED one,
-- one finished, one delegated (open Waiting-For).
INSERT INTO gtd_items (id, user_id, title, description, disposition, context,
                       energy, time_estimate_mins, project_id, due_at, is_two_minute)
VALUES
  ('d1000000-0000-0000-0000-00000000d101','dana@s3btest.invalid','Dana plain',
   'notes here','NEXT','@computer','high',25,NULL,'2026-09-01 10:00+00',false),
  ('d1000000-0000-0000-0000-00000000d102','dana@s3btest.invalid','Dana local-proj',
   NULL,'NEXT',NULL,NULL,NULL,'d0000000-0000-0000-0000-00000000d001',NULL,false),
  ('d1000000-0000-0000-0000-00000000d103','dana@s3btest.invalid','Dana synced-proj',
   NULL,'SOMEDAY',NULL,NULL,NULL,'d0000000-0000-0000-0000-00000000d002',NULL,false),
  ('d1000000-0000-0000-0000-00000000d104','dana@s3btest.invalid','Dana finished',
   NULL,'DONE',NULL,NULL,NULL,NULL,NULL,false),
  ('d1000000-0000-0000-0000-00000000d105','dana@s3btest.invalid','Dana delegated',
   NULL,'WAITING',NULL,NULL,NULL,NULL,NULL,false);

UPDATE gtd_items SET completed_at = '2026-08-01 09:00+00'
 WHERE id = 'd1000000-0000-0000-0000-00000000d104';

-- A task Dana DELETED. Soft delete is undoable and hidden from every view;
-- if the move resurrected it, it would reappear in the app she uses most.
INSERT INTO gtd_items (id, user_id, title, disposition, deleted_at) VALUES
  ('d1000000-0000-0000-0000-00000000d106','dana@s3btest.invalid','Dana deleted this',
   'NEXT','2026-08-10 12:00+00');

INSERT INTO gtd_waiting (item_id, waiting_on, delegated_at, expected_by) VALUES
  ('d1000000-0000-0000-0000-00000000d105',
   '{"name":"Priya","email":"priya@s3btest.invalid"}'::jsonb,
   '2026-08-20 08:00+00','2026-08-30 17:00+00');

-- Erin, in the OTHER tenant, with a deliberately similar-looking task.
INSERT INTO gtd_items (id, user_id, title, disposition) VALUES
  ('e1000000-0000-0000-0000-00000000e101','erin@s3btest.invalid','Erin private salary review','NEXT'),
  ('e1000000-0000-0000-0000-00000000e102','erin@s3btest.invalid','Erin second','INBOX');

-- The row nobody owns. `_uid` writes this literal for an unauthenticated
-- capture, and it must be REFUSED rather than handed to somebody.
INSERT INTO gtd_items (id, user_id, title, disposition) VALUES
  ('f1000000-0000-0000-0000-00000000f101','anonymous','Orphan capture','INBOX');

-- A row whose email looks like a member but is not one.
INSERT INTO gtd_items (id, user_id, title, disposition) VALUES
  ('f1000000-0000-0000-0000-00000000f102','ghost@s3btest.invalid','Ghost capture','INBOX');


-- ════════════════════════════════════════════════════════════════════════════
--  CHECKS
-- ════════════════════════════════════════════════════════════════════════════
CREATE OR REPLACE FUNCTION s3b_check(label text, got anyelement, want anyelement)
RETURNS void LANGUAGE plpgsql AS $$
BEGIN
    IF got IS DISTINCT FROM want THEN
        RAISE EXCEPTION 'FAIL % — got %, want %', label, got, want;
    END IF;
    RAISE NOTICE 'ok   %', label;
END; $$;

-- ── 1. The dry run writes nothing ───────────────────────────────────────────
SELECT * FROM gtd_backfill_to_pm(false);

SELECT s3b_check('1a dry run created no tasks',
    (SELECT count(*) FROM pm_tasks WHERE created_by LIKE '%@s3btest.invalid'), 0::bigint);
SELECT s3b_check('1b dry run created no projects',
    (SELECT count(*) FROM pm_projects WHERE created_by LIKE '%@s3btest.invalid'), 0::bigint);
SELECT s3b_check('1c dry run stamped nothing',
    (SELECT count(*) FROM gtd_items
      WHERE user_id LIKE '%s3btest%' AND migrated_task_id IS NOT NULL), 0::bigint);

-- ── 2. The preview refuses what it cannot resolve ───────────────────────────
SELECT s3b_check('2a anonymous is unmappable',
    (SELECT verdict FROM gtd_backfill_plan WHERE item_id = 'f1000000-0000-0000-0000-00000000f101'),
    'unmappable: no app_user with this email');
SELECT s3b_check('2b non-member is unmappable',
    (SELECT verdict FROM gtd_backfill_plan WHERE item_id = 'f1000000-0000-0000-0000-00000000f102'),
    'unmappable: no app_user with this email');
SELECT s3b_check('2c Dana resolves to Alpha',
    (SELECT resolved_org FROM gtd_backfill_plan WHERE item_id = 'd1000000-0000-0000-0000-00000000d101'),
    'a5100000-0000-0000-0000-0000000000a1'::uuid);
SELECT s3b_check('2d Erin resolves to Beta',
    (SELECT resolved_org FROM gtd_backfill_plan WHERE item_id = 'e1000000-0000-0000-0000-00000000e101'),
    'b5100000-0000-0000-0000-0000000000b1'::uuid);

-- ── 3. Apply ────────────────────────────────────────────────────────────────
SELECT * FROM gtd_backfill_to_pm(true);

SELECT s3b_check('3a Dana moved 6 items',
    (SELECT count(*) FROM gtd_items
      WHERE user_id = 'dana@s3btest.invalid' AND migrated_task_id IS NOT NULL), 6::bigint);
SELECT s3b_check('3b Erin moved 2 items',
    (SELECT count(*) FROM gtd_items
      WHERE user_id = 'erin@s3btest.invalid' AND migrated_task_id IS NOT NULL), 2::bigint);
SELECT s3b_check('3c the unowned rows were NOT moved',
    (SELECT count(*) FROM gtd_items
      WHERE user_id IN ('anonymous','ghost@s3btest.invalid')
        AND migrated_task_id IS NOT NULL), 0::bigint);
SELECT s3b_check('3d ...and were NOT deleted either',
    (SELECT count(*) FROM gtd_items WHERE user_id IN ('anonymous','ghost@s3btest.invalid')), 2::bigint);

-- ── 4. THE TENANCY PROOF — §12.8's named failure ────────────────────────────
--
-- Every task must sit in the organization of the person who owned it, on BOTH
-- the task row and the overlay row. A single mis-mapped one is the bug.
SELECT s3b_check('4a every Dana task is in Alpha',
    (SELECT count(*) FROM pm_tasks t
      JOIN gtd_items i ON i.migrated_task_id = t.id
     WHERE i.user_id = 'dana@s3btest.invalid'
       AND t.organization_id <> 'a5100000-0000-0000-0000-0000000000a1'), 0::bigint);
SELECT s3b_check('4b every Erin task is in Beta',
    (SELECT count(*) FROM pm_tasks t
      JOIN gtd_items i ON i.migrated_task_id = t.id
     WHERE i.user_id = 'erin@s3btest.invalid'
       AND t.organization_id <> 'b5100000-0000-0000-0000-0000000000b1'), 0::bigint);
SELECT s3b_check('4c no overlay row names the wrong member',
    (SELECT count(*) FROM pm_task_personal p
      JOIN gtd_items i ON i.migrated_task_id = p.task_id
     WHERE p.member_email <> lower(i.user_id)), 0::bigint);
SELECT s3b_check('4d no overlay row is in the wrong tenant',
    (SELECT count(*) FROM pm_task_personal p
      JOIN pm_tasks t ON t.id = p.task_id
     WHERE p.organization_id <> t.organization_id), 0::bigint);
SELECT s3b_check('4e Dana and Erin got DIFFERENT personal ROOTS',
    (SELECT count(DISTINCT id) FROM pm_projects
      WHERE lower(personal_owner) LIKE '%@s3btest.invalid'
        AND parent_project_id IS NULL), 2::bigint);

-- ── 4h-4j. The backfilled category is PRIVATE (mig 191) ─────────────────────
--
-- Owner directive 2026-08-26: personal projects "do not show up in the project
-- management app but show up in the tasks app". A sub-project created with
-- `personal_owner = NULL` — which is what the old index forced — would have
-- appeared on the company board beside Sales and Operations.
SELECT s3b_check('4h the backfilled category carries personal_owner',
    (SELECT lower(personal_owner) FROM pm_projects WHERE name = 'Kitchen Reno'),
    'dana@s3btest.invalid');
SELECT s3b_check('4i ...so the Projects app (tree.py:152) does not show it',
    (SELECT count(*) FROM pm_projects
      WHERE name = 'Kitchen Reno' AND personal_owner IS NULL), 0::bigint);
SELECT s3b_check('4j ...and it is a CHILD, so it did not take the root''s slot',
    (SELECT parent_project_id IS NOT NULL FROM pm_projects
      WHERE name = 'Kitchen Reno'), true);

-- The lens itself. This is MY_TASKS_FROM's ownership arm from
-- routes/projects/personal.py, run as each member. Erin's private salary
-- review must not appear for Dana — that is the sentence §12.8 asks us to prove.
SELECT s3b_check('4f Erin''s private task is invisible in Dana''s lens',
    (SELECT count(*) FROM pm_tasks t
       LEFT JOIN pm_projects proj ON proj.id = t.project_id
      WHERE t.organization_id = 'a5100000-0000-0000-0000-0000000000a1'
        AND (EXISTS (SELECT 1 FROM pm_task_assignees a
                      WHERE a.task_id = t.id AND lower(a.assignee) = 'dana@s3btest.invalid')
             OR lower(proj.personal_owner) = 'dana@s3btest.invalid')
        AND t.title LIKE 'Erin%'), 0::bigint);
SELECT s3b_check('4g Dana''s lens shows her five LIVE tasks, not the deleted one',
    (SELECT count(*) FROM pm_tasks t
       LEFT JOIN pm_projects proj ON proj.id = t.project_id
      WHERE t.organization_id = 'a5100000-0000-0000-0000-0000000000a1'
        AND (EXISTS (SELECT 1 FROM pm_task_assignees a
                      WHERE a.task_id = t.id AND lower(a.assignee) = 'dana@s3btest.invalid')
             OR lower(proj.personal_owner) = 'dana@s3btest.invalid')
        AND t.archived_at IS NULL), 5::bigint);

-- ── 5. Nothing was lost in the move ─────────────────────────────────────────
SELECT s3b_check('5a disposition survived verbatim',
    (SELECT p.disposition FROM pm_task_personal p
      JOIN gtd_items i ON i.migrated_task_id = p.task_id
     WHERE i.id = 'd1000000-0000-0000-0000-00000000d103'), 'SOMEDAY');
SELECT s3b_check('5b context/energy/estimate survived',
    (SELECT p.context || '/' || p.energy || '/' || p.time_estimate_mins
       FROM pm_task_personal p JOIN gtd_items i ON i.migrated_task_id = p.task_id
      WHERE i.id = 'd1000000-0000-0000-0000-00000000d101'), '@computer/high/25');
SELECT s3b_check('5c due_at stayed on the TASK (D53.7 — shared fact)',
    (SELECT t.due_at FROM pm_tasks t JOIN gtd_items i ON i.migrated_task_id = t.id
      WHERE i.id = 'd1000000-0000-0000-0000-00000000d101'), '2026-09-01 10:00+00'::timestamptz);
SELECT s3b_check('5d description survived',
    (SELECT t.description FROM pm_tasks t JOIN gtd_items i ON i.migrated_task_id = t.id
      WHERE i.id = 'd1000000-0000-0000-0000-00000000d101'), 'notes here');
SELECT s3b_check('5e the Waiting-For quartet landed on the OVERLAY (D53.8)',
    (SELECT (p.waiting_on->>'name') || '/' || to_char(p.expected_by,'YYYY-MM-DD')
       FROM pm_task_personal p JOIN gtd_items i ON i.migrated_task_id = p.task_id
      WHERE i.id = 'd1000000-0000-0000-0000-00000000d105'), 'Priya/2026-08-30');
SELECT s3b_check('5f completed_at survived',
    (SELECT t.completed_at FROM pm_tasks t JOIN gtd_items i ON i.migrated_task_id = t.id
      WHERE i.id = 'd1000000-0000-0000-0000-00000000d104'), '2026-08-01 09:00+00'::timestamptz);

-- ── 5g/5h. A DELETED task must not come back ────────────────────────────────
--
-- `gtd_items.deleted_at` is a soft delete (items.py:389, "vanishes from every
-- view", and undoable). Carrying it over as an ordinary task would resurrect
-- every task the member had thrown away, on a ladder that cannot roll back.
SELECT s3b_check('5g a soft-deleted item moved as ARCHIVED',
    (SELECT t.archived_at FROM pm_tasks t JOIN gtd_items i ON i.migrated_task_id = t.id
      WHERE i.id = 'd1000000-0000-0000-0000-00000000d106'), '2026-08-10 12:00+00'::timestamptz);
SELECT s3b_check('5h ...so it does NOT appear in the lens',
    (SELECT count(*) FROM pm_tasks t
      WHERE t.organization_id = 'a5100000-0000-0000-0000-0000000000a1'
        AND t.title = 'Dana deleted this' AND t.archived_at IS NULL), 0::bigint);
SELECT s3b_check('5i ...but it was NOT destroyed either',
    (SELECT count(*) FROM pm_tasks t WHERE t.title = 'Dana deleted this'), 1::bigint);

-- ── 6. The three lenses agree — a finished task reads finished on the BOARD ──
SELECT s3b_check('6a DONE landed in a done-category status',
    (SELECT s.category FROM pm_tasks t
       JOIN pm_task_statuses s ON s.id = t.status_id
       JOIN gtd_items i ON i.migrated_task_id = t.id
      WHERE i.id = 'd1000000-0000-0000-0000-00000000d104'), 'done');
SELECT s3b_check('6b an open task did not land in done',
    (SELECT s.category FROM pm_tasks t
       JOIN pm_task_statuses s ON s.id = t.status_id
       JOIN gtd_items i ON i.migrated_task_id = t.id
      WHERE i.id = 'd1000000-0000-0000-0000-00000000d101'), 'backlog');

-- ── 7. The LOCAL tree became sub-projects; the SYNCED one did not ───────────
SELECT s3b_check('7a the LOCAL gtd_project became a sub-project',
    (SELECT count(*) FROM pm_projects
      WHERE name = 'Kitchen Reno' AND parent_project_id IS NOT NULL), 1::bigint);
SELECT s3b_check('7b the SYNCED gtd_project did NOT (D52 — no connector)',
    (SELECT count(*) FROM pm_projects WHERE name = 'Dead ClickUp List'), 0::bigint);
SELECT s3b_check('7c the SYNCED project''s ITEM still moved, to the root',
    (SELECT (t.project_id = t.root_project_id) FROM pm_tasks t
       JOIN gtd_items i ON i.migrated_task_id = t.id
      WHERE i.id = 'd1000000-0000-0000-0000-00000000d103'), true);
SELECT s3b_check('7d the sub-projected task is still assigned (else it vanishes)',
    (SELECT count(*) FROM pm_task_assignees a
       JOIN gtd_items i ON i.migrated_task_id = a.task_id
      WHERE i.id = 'd1000000-0000-0000-0000-00000000d102'), 1::bigint);

-- ── 8. Re-running is a no-op — the property the cutover depends on ──────────
SELECT * FROM gtd_backfill_to_pm(true);

SELECT s3b_check('8a re-run created no duplicate tasks',
    (SELECT count(*) FROM pm_tasks WHERE created_by LIKE '%@s3btest.invalid'), 8::bigint);
SELECT s3b_check('8b re-run created no duplicate projects',
    (SELECT count(*) FROM pm_projects WHERE created_by LIKE '%@s3btest.invalid'), 3::bigint);

-- ── 9. A row written AFTER the first pass is swept by the second ────────────
--
-- This is why the function is re-runnable rather than one-shot: the owner moves
-- the bulk, flips the flags, and sweeps whatever the app wrote in between.
INSERT INTO gtd_items (id, user_id, title, disposition) VALUES
  ('d1000000-0000-0000-0000-00000000d199','dana@s3btest.invalid','Written mid-cutover','INBOX');
SELECT * FROM gtd_backfill_to_pm(true);
SELECT s3b_check('9a the straggler moved',
    (SELECT count(*) FROM gtd_items
      WHERE id = 'd1000000-0000-0000-0000-00000000d199' AND migrated_task_id IS NOT NULL), 1::bigint);
SELECT s3b_check('9b and only it — no duplicates of the first eight',
    (SELECT count(*) FROM pm_tasks WHERE created_by LIKE '%@s3btest.invalid'), 9::bigint);

\echo ''
\echo '════════════════════════════════════════════════════════'
\echo '  S3b: all checks passed against real Postgres.'
\echo '════════════════════════════════════════════════════════'
