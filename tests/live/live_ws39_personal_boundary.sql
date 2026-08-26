-- ============================================================================
-- live_ws39_personal_boundary.sql — the personal/team boundary, on REAL
-- Postgres (R8).
--
-- Two guards land together because they are the same boundary seen from each
-- side, and the interesting part of both is what they must NOT refuse:
--
--   assert_move_keeps_privacy  — a task may only move INTO a personal project
--                                it already lives in
--   assert_assignable_here     — assigning somebody else needs a real project
--
-- ⚠️ The rules live in Python, so what this file proves is the DATA half: that
-- the queries those guards run return the facts the guards believe they do,
-- against the real schema. The refusal logic itself is unit-tested. Both halves
-- are needed — a guard that reads the wrong row refuses the wrong thing, and no
-- amount of unit testing over a fake would show it (R8).
--
-- Run:  docker exec -i tenant-scratch psql -U acb -d acb_tenant \
--         -v ON_ERROR_STOP=1 < tests/live/live_ws39_personal_boundary.sql
-- ============================================================================

\set ON_ERROR_STOP on

CREATE OR REPLACE FUNCTION pb_check(label text, got anyelement, want anyelement)
RETURNS void LANGUAGE plpgsql AS $$
BEGIN
    IF got IS DISTINCT FROM want THEN
        RAISE EXCEPTION 'FAIL % — got %, want %', label, got, want;
    END IF;
    RAISE NOTICE 'ok   %', label;
END; $$;

BEGIN;

INSERT INTO organization (id, slug, display_name)
VALUES ('d8000000-0000-0000-0000-0000000000d8','pb-org','Boundary Ltd')
ON CONFLICT DO NOTHING;

INSERT INTO app_user (email, display_name, organization_id) VALUES
  ('rae@pb.invalid','Rae','d8000000-0000-0000-0000-0000000000d8'),
  ('sam@pb.invalid','Sam','d8000000-0000-0000-0000-0000000000d8');

-- Rae's private tree: root + two Areas.
INSERT INTO pm_projects (id, organization_id, name, personal_owner, created_by) VALUES
  ('d8100000-0000-0000-0000-000000000001','d8000000-0000-0000-0000-0000000000d8',
   'My Tasks','rae@pb.invalid','rae@pb.invalid');
INSERT INTO pm_projects (id, organization_id, name, personal_owner, created_by,
                         parent_project_id) VALUES
  ('d8100000-0000-0000-0000-000000000002','d8000000-0000-0000-0000-0000000000d8',
   'Home','rae@pb.invalid','rae@pb.invalid','d8100000-0000-0000-0000-000000000001'),
  ('d8100000-0000-0000-0000-000000000003','d8000000-0000-0000-0000-0000000000d8',
   'Health','rae@pb.invalid','rae@pb.invalid','d8100000-0000-0000-0000-000000000001');

-- Sam's private root, and one ordinary TEAM project.
INSERT INTO pm_projects (id, organization_id, name, personal_owner, created_by) VALUES
  ('d8100000-0000-0000-0000-000000000004','d8000000-0000-0000-0000-0000000000d8',
   'My Tasks','sam@pb.invalid','sam@pb.invalid');
INSERT INTO pm_projects (id, organization_id, name, created_by) VALUES
  ('d8100000-0000-0000-0000-00000000000a','d8000000-0000-0000-0000-0000000000d8',
   'Q3 launch','rae@pb.invalid');

-- ── The five move combinations, as the guard sees them ──────────────────────
--
-- The guard reads `personal_owner` for the OLD and NEW project in one query and
-- decides from those two values alone. So the facts to pin are those two values
-- for each combination — if the schema ever stops answering this way, the guard
-- silently starts permitting or refusing the wrong thing.

CREATE OR REPLACE FUNCTION pb_verdict(old_pid uuid, new_pid uuid)
RETURNS text LANGUAGE plpgsql AS $$
DECLARE v_old text; v_new text;
BEGIN
    -- Mirrors assert_move_keeps_privacy's own query and decision, so this test
    -- fails if the DATA stops supporting the rule, not merely if someone edits
    -- the Python.
    SELECT lower(personal_owner) INTO v_old FROM pm_projects WHERE id = old_pid;
    SELECT lower(personal_owner) INTO v_new FROM pm_projects WHERE id = new_pid;
    IF v_new IS NULL THEN RETURN 'allowed'; END IF;
    IF v_old IS NOT DISTINCT FROM v_new THEN RETURN 'allowed'; END IF;
    RETURN 'refused';
END; $$;

SELECT pb_check('1a team -> team is allowed',
    pb_verdict('d8100000-0000-0000-0000-00000000000a','d8100000-0000-0000-0000-00000000000a'),
    'allowed');
SELECT pb_check('1b personal -> team is allowed (this is PROMOTION, D53.4)',
    pb_verdict('d8100000-0000-0000-0000-000000000002','d8100000-0000-0000-0000-00000000000a'),
    'allowed');
SELECT pb_check('1c team -> personal is REFUSED',
    pb_verdict('d8100000-0000-0000-0000-00000000000a','d8100000-0000-0000-0000-000000000002'),
    'refused');
SELECT pb_check('1d Rae''s Area -> Rae''s other Area is allowed',
    pb_verdict('d8100000-0000-0000-0000-000000000002','d8100000-0000-0000-0000-000000000003'),
    'allowed');
SELECT pb_check('1e Rae''s Area -> SAM''s tree is REFUSED',
    pb_verdict('d8100000-0000-0000-0000-000000000002','d8100000-0000-0000-0000-000000000004'),
    'refused');
SELECT pb_check('1f Rae''s root -> Rae''s Area is allowed (root and Area agree)',
    pb_verdict('d8100000-0000-0000-0000-000000000001','d8100000-0000-0000-0000-000000000002'),
    'allowed');

-- ── The fact the assign guard reads ─────────────────────────────────────────
SELECT pb_check('2a an Area reports its owner',
    (SELECT lower(personal_owner) FROM pm_projects
      WHERE id = 'd8100000-0000-0000-0000-000000000002'), 'rae@pb.invalid');
SELECT pb_check('2b a team project reports NULL — assign anyone',
    (SELECT personal_owner FROM pm_projects
      WHERE id = 'd8100000-0000-0000-0000-00000000000a'), NULL::text);

-- ── ⚠️ What must STILL work: assignment as task-level visibility (WS-27j) ────
--
-- The assign guard must not become a visibility rule. Assignment already grants
-- access to the task itself through the second arm of `task_visibility_clause`,
-- and Case C — assigning across a boundary in a TEAM project — is meant to keep
-- working. This is the check that fails if somebody "tightens" it later.
INSERT INTO pm_task_statuses (id, project_id, name, category, position, is_default,
                              organization_id)
VALUES ('d8200000-0000-0000-0000-000000000001','d8100000-0000-0000-0000-00000000000a',
        'Todo','todo',10,true,'d8000000-0000-0000-0000-0000000000d8');
INSERT INTO pm_tasks (id, project_id, root_project_id, task_number, status_id,
                      title, created_by, organization_id)
VALUES ('d8300000-0000-0000-0000-000000000001','d8100000-0000-0000-0000-00000000000a',
        'd8100000-0000-0000-0000-00000000000a',1,'d8200000-0000-0000-0000-000000000001',
        'Ship the launch page','rae@pb.invalid','d8000000-0000-0000-0000-0000000000d8');
INSERT INTO pm_task_assignees (task_id, assignee, assigned_by, organization_id)
VALUES ('d8300000-0000-0000-0000-000000000001','sam@pb.invalid','rae@pb.invalid',
        'd8000000-0000-0000-0000-0000000000d8');

-- task_visibility_clause's second arm, for a member with NO grant on the project.
SELECT pb_check('3a an assignee with no project grant can still see the TASK',
    (SELECT count(*) FROM pm_tasks t
      WHERE t.id = 'd8300000-0000-0000-0000-000000000001'
        AND t.organization_id = 'd8000000-0000-0000-0000-0000000000d8'
        AND EXISTS (SELECT 1 FROM pm_task_assignees a
                     WHERE a.task_id = t.id
                       AND lower(a.assignee) = 'sam@pb.invalid')), 1::bigint);
SELECT pb_check('3b ...and holds no grant on that project, so it IS the second arm',
    (SELECT count(*) FROM pm_project_grants g
      WHERE g.project_id = 'd8100000-0000-0000-0000-00000000000a'
        AND lower(g.subject) = 'sam@pb.invalid'), 0::bigint);

DROP FUNCTION pb_verdict(uuid, uuid);
ROLLBACK;

\echo ''
\echo '════════════════════════════════════════════════════════'
\echo '  Personal/team boundary: all checks passed.'
\echo '════════════════════════════════════════════════════════'
