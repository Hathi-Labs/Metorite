-- ============================================================================
-- live_ws39_personal_tree.sql — migration 191, against REAL Postgres (R8).
--
-- The claim 191 makes is that ONE index change makes TWO surfaces correct at
-- once: the Projects app stops showing a member's private categories, and the
-- Tasks app starts showing them. Both halves are queries that already exist and
-- were not edited, so the only honest way to check them is to run them.
--
-- The queries below are lifted from the code they defend:
--   * `routes/projects/tree.py:152`     — the Projects app's project list
--   * `routes/projects/personal.py:664` — MY_TASKS_FROM's ownership arm
--   * `routes/projects/personal.py:195` — _load_personal_project
--
-- Run:  docker exec -i tenant-scratch psql -U acb -d acb_tenant \
--         -v ON_ERROR_STOP=1 < tests/live/live_ws39_personal_tree.sql
-- ============================================================================

\set ON_ERROR_STOP on

CREATE OR REPLACE FUNCTION ptree_check(label text, got anyelement, want anyelement)
RETURNS void LANGUAGE plpgsql AS $$
BEGIN
    IF got IS DISTINCT FROM want THEN
        RAISE EXCEPTION 'FAIL % — got %, want %', label, got, want;
    END IF;
    RAISE NOTICE 'ok   %', label;
END; $$;

BEGIN;

INSERT INTO organization (id, slug, display_name)
VALUES ('c7000000-0000-0000-0000-0000000000c7','ptree-org','Ptree Ltd')
ON CONFLICT DO NOTHING;

INSERT INTO app_user (email, display_name, organization_id) VALUES
  ('nia@ptree.invalid','Nia','c7000000-0000-0000-0000-0000000000c7'),
  ('omar@ptree.invalid','Omar','c7000000-0000-0000-0000-0000000000c7');

-- Nia's private tree: a root, two categories, one project under a category.
INSERT INTO pm_projects (id, organization_id, name, personal_owner, created_by)
VALUES ('c7100000-0000-0000-0000-000000000001','c7000000-0000-0000-0000-0000000000c7',
        'My Tasks','nia@ptree.invalid','nia@ptree.invalid');
INSERT INTO pm_projects (id, organization_id, name, personal_owner, created_by,
                         parent_project_id)
VALUES ('c7100000-0000-0000-0000-000000000002','c7000000-0000-0000-0000-0000000000c7',
        'Work','nia@ptree.invalid','nia@ptree.invalid','c7100000-0000-0000-0000-000000000001'),
       ('c7100000-0000-0000-0000-000000000003','c7000000-0000-0000-0000-0000000000c7',
        'Home','nia@ptree.invalid','nia@ptree.invalid','c7100000-0000-0000-0000-000000000001'),
       ('c7100000-0000-0000-0000-000000000004','c7000000-0000-0000-0000-0000000000c7',
        'Kitchen reno','nia@ptree.invalid','nia@ptree.invalid','c7100000-0000-0000-0000-000000000003');

-- An ordinary TEAM project, for contrast.
INSERT INTO pm_projects (id, organization_id, name, created_by)
VALUES ('c7100000-0000-0000-0000-00000000000a','c7000000-0000-0000-0000-0000000000c7',
        'Sales pipeline','nia@ptree.invalid');

-- ── 1. The tree exists, and depth is what distinguishes root from category ──
SELECT ptree_check('1a Nia has four private projects',
    (SELECT count(*) FROM pm_projects
      WHERE lower(personal_owner) = 'nia@ptree.invalid'), 4::bigint);
SELECT ptree_check('1b exactly one of them is a ROOT',
    (SELECT count(*) FROM pm_projects
      WHERE lower(personal_owner) = 'nia@ptree.invalid'
        AND parent_project_id IS NULL), 1::bigint);
SELECT ptree_check('1c nesting goes two deep (category -> project)',
    (SELECT count(*) FROM pm_projects
      WHERE lower(personal_owner) = 'nia@ptree.invalid'
        AND parent_project_id = 'c7100000-0000-0000-0000-000000000003'), 1::bigint);

-- ── 2. The root is still unique — 191 WIDENED, it did not remove ────────────
DO $t$
DECLARE v_msg text;
BEGIN
    INSERT INTO pm_projects (organization_id, name, personal_owner, created_by)
    VALUES ('c7000000-0000-0000-0000-0000000000c7','Second inbox',
            'nia@ptree.invalid','nia@ptree.invalid');
    RAISE EXCEPTION 'FAIL 2a — a member was allowed TWO personal roots';
EXCEPTION WHEN unique_violation THEN
    RAISE NOTICE 'ok   2a a second personal ROOT is still refused';
WHEN OTHERS THEN
    GET STACKED DIAGNOSTICS v_msg = MESSAGE_TEXT;
    IF v_msg LIKE 'FAIL 2a%' THEN RAISE; END IF;
    RAISE EXCEPTION 'FAIL 2b — refused for the wrong reason: %', v_msg;
END $t$;

SELECT ptree_check('2c ...but a SECOND CATEGORY is fine',
    (SELECT count(*) FROM pm_projects WHERE id IN
       ('c7100000-0000-0000-0000-000000000002','c7100000-0000-0000-0000-000000000003')), 2::bigint);

-- Omar, a different member, still gets his own root.
INSERT INTO pm_projects (organization_id, name, personal_owner, created_by)
VALUES ('c7000000-0000-0000-0000-0000000000c7','My Tasks',
        'omar@ptree.invalid','omar@ptree.invalid');
SELECT ptree_check('2d a DIFFERENT member still gets a root',
    (SELECT count(*) FROM pm_projects
      WHERE lower(personal_owner) = 'omar@ptree.invalid'), 1::bigint);

-- ── 3. THE PROJECTS APP — tree.py:152's filter, verbatim ────────────────────
--
-- "these do not show up in the project management app". The filter was NOT
-- edited; 191 is what makes it correct for the whole tree.
SELECT ptree_check('3a the Projects app shows the team project',
    (SELECT count(*) FROM pm_projects
      WHERE organization_id = 'c7000000-0000-0000-0000-0000000000c7'
        AND personal_owner IS NULL), 1::bigint);
SELECT ptree_check('3b ...and NONE of Nia''s private tree — not even the categories',
    (SELECT count(*) FROM pm_projects
      WHERE organization_id = 'c7000000-0000-0000-0000-0000000000c7'
        AND personal_owner IS NULL
        AND id::text LIKE 'c7100000-0000-0000-0000-00000000000_'
        AND id <> 'c7100000-0000-0000-0000-00000000000a'), 0::bigint);

-- ── 4. THE TASKS APP — MY_TASKS_FROM's ownership arm, verbatim ──────────────
--
-- "but show up in the tasks app". A task in a CATEGORY must be found by the
-- project arm alone, with no assignee row — that is what proves the tree is
-- visible to the lens rather than just the root.
INSERT INTO pm_task_statuses (id, project_id, name, category, position, is_default,
                              organization_id)
VALUES ('c7200000-0000-0000-0000-000000000001','c7100000-0000-0000-0000-000000000001',
        'Inbox','backlog',10,true,'c7000000-0000-0000-0000-0000000000c7');

INSERT INTO pm_tasks (id, project_id, root_project_id, task_number, status_id,
                      title, created_by, organization_id)
VALUES ('c7300000-0000-0000-0000-000000000001','c7100000-0000-0000-0000-000000000004',
        'c7100000-0000-0000-0000-000000000001',1,'c7200000-0000-0000-0000-000000000001',
        'Order the worktop','nia@ptree.invalid','c7000000-0000-0000-0000-0000000000c7'),
       ('c7300000-0000-0000-0000-000000000002','c7100000-0000-0000-0000-000000000001',
        'c7100000-0000-0000-0000-000000000001',2,'c7200000-0000-0000-0000-000000000001',
        'Loose inbox thought','nia@ptree.invalid','c7000000-0000-0000-0000-0000000000c7');

SELECT ptree_check('4a a task in a CATEGORY is in Nia''s lens, with no assignee row',
    (SELECT count(*) FROM pm_tasks t
       LEFT JOIN pm_projects proj ON proj.id = t.project_id
      WHERE t.organization_id = 'c7000000-0000-0000-0000-0000000000c7'
        AND (EXISTS (SELECT 1 FROM pm_task_assignees a
                      WHERE a.task_id = t.id AND lower(a.assignee) = 'nia@ptree.invalid')
             OR lower(proj.personal_owner) = 'nia@ptree.invalid')
        AND t.id = 'c7300000-0000-0000-0000-000000000001'), 1::bigint);
SELECT ptree_check('4b both her tasks are in her lens',
    (SELECT count(*) FROM pm_tasks t
       LEFT JOIN pm_projects proj ON proj.id = t.project_id
      WHERE t.organization_id = 'c7000000-0000-0000-0000-0000000000c7'
        AND (EXISTS (SELECT 1 FROM pm_task_assignees a
                      WHERE a.task_id = t.id AND lower(a.assignee) = 'nia@ptree.invalid')
             OR lower(proj.personal_owner) = 'nia@ptree.invalid')), 2::bigint);
SELECT ptree_check('4c and NONE of them in Omar''s',
    (SELECT count(*) FROM pm_tasks t
       LEFT JOIN pm_projects proj ON proj.id = t.project_id
      WHERE t.organization_id = 'c7000000-0000-0000-0000-0000000000c7'
        AND (EXISTS (SELECT 1 FROM pm_task_assignees a
                      WHERE a.task_id = t.id AND lower(a.assignee) = 'omar@ptree.invalid')
             OR lower(proj.personal_owner) = 'omar@ptree.invalid')), 0::bigint);

-- ── 5. _load_personal_project returns the ROOT, not a category ──────────────
--
-- Its result is a WRITE TARGET. Without `parent_project_id IS NULL` this
-- returns an arbitrary node and a quick capture lands in "Kitchen reno".
SELECT ptree_check('5a the root lookup returns the ROOT',
    (SELECT id FROM pm_projects
      WHERE lower(personal_owner) = 'nia@ptree.invalid'
        AND parent_project_id IS NULL),
    'c7100000-0000-0000-0000-000000000001'::uuid);
SELECT ptree_check('5b the OLD lookup would now be ambiguous — 4 rows, not 1',
    (SELECT count(*) FROM pm_projects
      WHERE lower(personal_owner) = 'nia@ptree.invalid'), 4::bigint);

ROLLBACK;

\echo ''
\echo '════════════════════════════════════════════════════════'
\echo '  Personal tree (mig 191): all checks passed.'
\echo '════════════════════════════════════════════════════════'
