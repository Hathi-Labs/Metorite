-- Seed a usable Projects board in the LOCAL scratch tenant, for UI review.
--
-- Why this file exists
-- ====================
-- The scratch tenant loses its Projects data periodically: `organization`
-- churns, and `pm_projects.organization_id` is ON DELETE CASCADE, so a
-- cleared organization row silently takes every project, task, lane and tag
-- with it. Measured twice on 2026-09-20 and once on 2026-09-21. H-139 holds
-- the finding and it is not yet attributed.
--
-- Re-seeding by hand took twenty minutes the first time, mostly spent
-- rediscovering that `organization.slug` is NOT NULL, that
-- `app_user.role` is CHECKed, and that `org_membership.user_id` references
-- `user_identity` rather than `app_user`. That is what this file is for.
--
-- Idempotent: run it as often as the data disappears.
--
--   docker exec -i metorite-scratch-tenant psql -U acb -d acb_tenant \
--     < scripts/seed_projects_local.sql
--
-- ⚠️ LOCAL SCRATCH ONLY. It writes fixed uuids and deletes rows that carry
-- them. Never point it at production.

BEGIN;

-- ── The tenant, and the identity chain that makes the app usable ─────────
-- All three are needed. With only `app_user` the nav renders but Projects is
-- missing; with none of them the app shows the invite wall.
INSERT INTO organization (id, slug, display_name)
VALUES ('32f18e2f-e3ce-499b-bb28-9ecd6030ad43', 'fracktal-works', 'Fracktal Works')
ON CONFLICT (id) DO UPDATE SET display_name = EXCLUDED.display_name;

INSERT INTO user_identity (email, display_name)
VALUES ('dev@fracktal.in', 'Dev')
ON CONFLICT DO NOTHING;

DELETE FROM app_user WHERE email = 'dev@fracktal.in';
INSERT INTO app_user (email, organization_id, display_name, role, status)
VALUES ('dev@fracktal.in', '32f18e2f-e3ce-499b-bb28-9ecd6030ad43', 'Dev',
        'executive', 'active');

INSERT INTO org_membership (organization_id, user_id, status, joined_at)
SELECT '32f18e2f-e3ce-499b-bb28-9ecd6030ad43', ui.id, 'active', now()
  FROM user_identity ui WHERE ui.email = 'dev@fracktal.in'
ON CONFLICT DO NOTHING;

-- ── Roles, or the nav renders without Projects in it ────────────────────
-- `build_access` reads permissions from org_role -> org_role_permission, via
-- user_role. With an identity but no role the member signs in, sees Personal
-- Center and Admin, and Projects is simply absent — which reads as the app
-- being broken rather than as a missing grant.
--
-- Copied from whichever organization the provisioning path already built, so
-- the permission set is the real one and not a hand-guessed list.
DELETE FROM org_role WHERE organization_id = '32f18e2f-e3ce-499b-bb28-9ecd6030ad43';

INSERT INTO org_role (organization_id, slug, display_name, description, is_system, rank)
SELECT '32f18e2f-e3ce-499b-bb28-9ecd6030ad43', r.slug, r.display_name,
       r.description, r.is_system, r.rank
  FROM org_role r
 WHERE r.organization_id = (
     SELECT organization_id FROM org_role
      GROUP BY organization_id ORDER BY count(*) DESC LIMIT 1);

INSERT INTO org_role_permission (role_id, permission)
SELECT mine.id, p.permission
  FROM org_role mine
  JOIN org_role theirs
    ON theirs.slug = mine.slug
   AND theirs.organization_id = (
       SELECT organization_id FROM org_role
        WHERE organization_id <> '32f18e2f-e3ce-499b-bb28-9ecd6030ad43'
        GROUP BY organization_id ORDER BY count(*) DESC LIMIT 1)
  JOIN org_role_permission p ON p.role_id = theirs.id
 WHERE mine.organization_id = '32f18e2f-e3ce-499b-bb28-9ecd6030ad43'
ON CONFLICT DO NOTHING;

INSERT INTO user_role (user_id, role_id, user_identity_id)
SELECT au.id, r.id, ui.id
  FROM app_user au
  JOIN user_identity ui ON ui.email = au.email
  JOIN org_role r ON r.organization_id = au.organization_id AND r.slug = 'admin'
 WHERE au.email = 'dev@fracktal.in'
ON CONFLICT DO NOTHING;

-- ── The tree ─────────────────────────────────────────────────────────────
DELETE FROM pm_projects WHERE id IN (
  'a0000000-0000-0000-0000-000000000001',
  'a0000000-0000-0000-0000-000000000002',
  'a0000000-0000-0000-0000-000000000003',
  'a0000000-0000-0000-0000-000000000004');

INSERT INTO pm_projects (id, organization_id, name, parent_project_id, source, created_by, owns_statuses) VALUES
 ('a0000000-0000-0000-0000-000000000001','32f18e2f-e3ce-499b-bb28-9ecd6030ad43','Hathi Labs Projects',NULL,'manual','dev@fracktal.in',true),
 ('a0000000-0000-0000-0000-000000000002','32f18e2f-e3ce-499b-bb28-9ecd6030ad43','Metorite','a0000000-0000-0000-0000-000000000001','manual','dev@fracktal.in',false),
 ('a0000000-0000-0000-0000-000000000003','32f18e2f-e3ce-499b-bb28-9ecd6030ad43','Bugs','a0000000-0000-0000-0000-000000000002','manual','dev@fracktal.in',false),
 ('a0000000-0000-0000-0000-000000000004','32f18e2f-e3ce-499b-bb28-9ecd6030ad43','Projects/Tasks App','a0000000-0000-0000-0000-000000000002','manual','dev@fracktal.in',false);

INSERT INTO pm_project_grants (project_id, subject, created_by)
SELECT id, 'dev@fracktal.in', 'dev@fracktal.in' FROM pm_projects
ON CONFLICT DO NOTHING;

-- ── Lanes, on the root that owns them ────────────────────────────────────
-- ⚠️ Deliberately NO cancelled lane on this root. That is the case D-PM-34
-- was ruled for: a project where the old archive guard had no legal way to
-- be satisfied. Keep it that way, so the review board exercises it.
INSERT INTO pm_task_statuses (id, project_id, name, position, category, is_default, color) VALUES
 ('b0000000-0000-0000-0000-000000000001','a0000000-0000-0000-0000-000000000001','Backlog',10,'backlog',false,'gray'),
 ('b0000000-0000-0000-0000-000000000002','a0000000-0000-0000-0000-000000000001','To do',20,'todo',true,'blue'),
 ('b0000000-0000-0000-0000-000000000003','a0000000-0000-0000-0000-000000000001','In progress',30,'in_progress',false,'amber'),
 ('b0000000-0000-0000-0000-000000000004','a0000000-0000-0000-0000-000000000001','Done',40,'done',false,'green');

INSERT INTO pm_tags (organization_id, project_id, name, color, created_by) VALUES
 ('32f18e2f-e3ce-499b-bb28-9ecd6030ad43','a0000000-0000-0000-0000-000000000001','bug','red','dev@fracktal.in'),
 ('32f18e2f-e3ce-499b-bb28-9ecd6030ad43','a0000000-0000-0000-0000-000000000001','deploy','blue','dev@fracktal.in'),
 ('32f18e2f-e3ce-499b-bb28-9ecd6030ad43','a0000000-0000-0000-0000-000000000001','shipping','violet','dev@fracktal.in'),
 ('32f18e2f-e3ce-499b-bb28-9ecd6030ad43','a0000000-0000-0000-0000-000000000001','ui','amber','dev@fracktal.in')
ON CONFLICT DO NOTHING;

-- ── Tasks, including two on the shelf ────────────────────────────────────
-- One archived from Done (the ordinary case) and one archived while still
-- OPEN (the case D-PM-34 opened up), so the Archived view shows both kinds.
INSERT INTO pm_tasks (id, organization_id, project_id, root_project_id, status_id, title, source, created_by, task_number, tags, archived_at) VALUES
 ('c0000000-0000-0000-0000-000000000001','32f18e2f-e3ce-499b-bb28-9ecd6030ad43','a0000000-0000-0000-0000-000000000002','a0000000-0000-0000-0000-000000000001','b0000000-0000-0000-0000-000000000002','This pop-up keeps showing up on the projects page','manual','dev@fracktal.in',1,ARRAY['deploy','shipping'],NULL),
 ('c0000000-0000-0000-0000-000000000002','32f18e2f-e3ce-499b-bb28-9ecd6030ad43','a0000000-0000-0000-0000-000000000002','a0000000-0000-0000-0000-000000000001','b0000000-0000-0000-0000-000000000002','All side panels have inconsistent opening and closing animations','manual','dev@fracktal.in',7,ARRAY['bug'],NULL),
 ('c0000000-0000-0000-0000-000000000005','32f18e2f-e3ce-499b-bb28-9ecd6030ad43','a0000000-0000-0000-0000-000000000003','a0000000-0000-0000-0000-000000000001','b0000000-0000-0000-0000-000000000003','If a tag already exists it is not able to be reused','manual','dev@fracktal.in',10,ARRAY[]::text[],NULL),
 ('c0000000-0000-0000-0000-000000000006','32f18e2f-e3ce-499b-bb28-9ecd6030ad43','a0000000-0000-0000-0000-000000000004','a0000000-0000-0000-0000-000000000001','b0000000-0000-0000-0000-000000000003','Add ability to move tasks from one project or subproject to another','manual','dev@fracktal.in',9,ARRAY[]::text[],NULL),
 ('c0000000-0000-0000-0000-000000000007','32f18e2f-e3ce-499b-bb28-9ecd6030ad43','a0000000-0000-0000-0000-000000000002','a0000000-0000-0000-0000-000000000001','b0000000-0000-0000-0000-000000000004','Notification engine for projects','manual','dev@fracktal.in',12,ARRAY[]::text[],NULL),
 ('c0000000-0000-0000-0000-000000000008','32f18e2f-e3ce-499b-bb28-9ecd6030ad43','a0000000-0000-0000-0000-000000000002','a0000000-0000-0000-0000-000000000001','b0000000-0000-0000-0000-000000000004','Ship the flat pricing page','manual','dev@fracktal.in',11,ARRAY['shipping'],NULL),
 ('c0000000-0000-0000-0000-000000000009','32f18e2f-e3ce-499b-bb28-9ecd6030ad43','a0000000-0000-0000-0000-000000000002','a0000000-0000-0000-0000-000000000001','b0000000-0000-0000-0000-000000000004','On mobile view the menu button does not work on the projects page','manual','dev@fracktal.in',8,ARRAY['bug','ui'],NULL),
 ('c0000000-0000-0000-0000-00000000000a','32f18e2f-e3ce-499b-bb28-9ecd6030ad43','a0000000-0000-0000-0000-000000000002','a0000000-0000-0000-0000-000000000001','b0000000-0000-0000-0000-000000000004','Old release checklist, kept for reference','manual','dev@fracktal.in',13,ARRAY['deploy'],now() - interval '9 days'),
 ('c0000000-0000-0000-0000-00000000000b','32f18e2f-e3ce-499b-bb28-9ecd6030ad43','a0000000-0000-0000-0000-000000000003','a0000000-0000-0000-0000-000000000001','b0000000-0000-0000-0000-000000000002','Spike: evaluate a second search backend','manual','dev@fracktal.in',14,ARRAY[]::text[],now() - interval '2 days');

-- ── People, and who is holding what ───────────────────────────
-- ⚠️ TWO people called "Priya Sharma", on purpose. A shared name is the
-- case `labelPeople` exists for, and a review board that cannot produce one
-- cannot show whether the disambiguation works.
--
-- The fourth assignee is deliberately somebody the directory has NEVER heard
-- of, so the fallback to the address is on screen too.
DELETE FROM people WHERE email IN
  ('priya@fracktal.in','p.sharma@fracktal.in','ada@fracktal.in','dev@fracktal.in');
INSERT INTO people (name, email, role, department, status) VALUES
 ('Priya Sharma','priya@fracktal.in','Engineer','Engineering','active'),
 ('Priya Sharma','p.sharma@fracktal.in','Designer','Design','active'),
 ('Ada Lovelace','ada@fracktal.in','Engineer','Engineering','active'),
 ('Dev','dev@fracktal.in','Owner','Leadership','active');

INSERT INTO pm_task_assignees (task_id, organization_id, assignee, assigned_by) VALUES
 ('c0000000-0000-0000-0000-000000000001','32f18e2f-e3ce-499b-bb28-9ecd6030ad43','ada@fracktal.in','dev@fracktal.in'),
 ('c0000000-0000-0000-0000-000000000002','32f18e2f-e3ce-499b-bb28-9ecd6030ad43','priya@fracktal.in','dev@fracktal.in'),
 ('c0000000-0000-0000-0000-000000000005','32f18e2f-e3ce-499b-bb28-9ecd6030ad43','p.sharma@fracktal.in','dev@fracktal.in'),
 ('c0000000-0000-0000-0000-000000000006','32f18e2f-e3ce-499b-bb28-9ecd6030ad43','nobody@elsewhere.test','dev@fracktal.in')
ON CONFLICT DO NOTHING;

COMMIT;

SELECT 'projects' AS what, count(*)::text AS n FROM pm_projects
UNION ALL SELECT 'tasks (live)', count(*)::text FROM pm_tasks WHERE archived_at IS NULL
UNION ALL SELECT 'tasks (archived)', count(*)::text FROM pm_tasks WHERE archived_at IS NOT NULL
UNION ALL SELECT 'identity rows', count(*)::text FROM app_user WHERE email = 'dev@fracktal.in';
