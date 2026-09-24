-- live_member_seal.sql — a departed member's personal tree seals (D63, H-49)
--
-- Run:  docker exec -i metorite-scratch-tenant psql -U acb -d acb_tenant \
--         -v ON_ERROR_STOP=1 -q < tests/live/live_member_seal.sql
--
-- R8. The hermetic suite can prove the code ASKS for the seal. Only a real
-- Postgres proves the two claims the feature rests on, and both are about how
-- a recursive CTE behaves:
--
--   1. sealing a ROOT removes its whole subtree from the grant closure, and
--   2. the ASSIGNEE arm survives that, which IS D63's hand-over.
--
-- A fake answers whatever the dispatcher was taught. These two are properties
-- of the SQL, so they are tested where the SQL runs.

BEGIN;

CREATE OR REPLACE FUNCTION seal_check(label text, got anyelement, want anyelement)
RETURNS void LANGUAGE plpgsql AS $$
BEGIN
    IF got IS NOT DISTINCT FROM want THEN
        RAISE NOTICE 'ok   %', label;
    ELSE
        RAISE EXCEPTION 'FAIL %: got % want %', label, got, want;
    END IF;
END $$;

INSERT INTO organization (id, slug, display_name)
VALUES ('5ea10000-0000-0000-0000-0000000000a0','seal-org','Seal Ltd')
ON CONFLICT DO NOTHING;

-- Ada is leaving. Bo stays, and holds one task Ada had assigned outward.
-- The root is Ada's personal project; `Errands` is an Area beneath it.
INSERT INTO pm_projects (id, organization_id, name, created_by, owns_statuses,
                         personal_owner)
VALUES ('5ea10000-0000-0000-0000-000000000001',
        '5ea10000-0000-0000-0000-0000000000a0','Ada','ada@seal.invalid', true,
        'Ada@Seal.invalid');
INSERT INTO pm_projects (id, organization_id, name, created_by, owns_statuses,
                         parent_project_id)
VALUES ('5ea10000-0000-0000-0000-000000000002',
        '5ea10000-0000-0000-0000-0000000000a0','Errands','ada@seal.invalid',
        false, '5ea10000-0000-0000-0000-000000000001');
-- A team project, to prove the seal is narrow.
INSERT INTO pm_projects (id, organization_id, name, created_by, owns_statuses)
VALUES ('5ea10000-0000-0000-0000-000000000003',
        '5ea10000-0000-0000-0000-0000000000a0','Company','bo@seal.invalid', true);

INSERT INTO pm_project_grants (organization_id, project_id, subject, created_by)
VALUES ('5ea10000-0000-0000-0000-0000000000a0',
        '5ea10000-0000-0000-0000-000000000001','ada@seal.invalid','ada@seal.invalid'),
       ('5ea10000-0000-0000-0000-0000000000a0',
        '5ea10000-0000-0000-0000-000000000003','org','bo@seal.invalid')
ON CONFLICT DO NOTHING;

-- Statuses hang off the root, which is the project that `owns_statuses`.
INSERT INTO pm_task_statuses (id, project_id, name, category, position,
                              is_default, organization_id)
VALUES ('5ea10000-0000-0000-0000-0000000000e1',
        '5ea10000-0000-0000-0000-000000000001','Inbox','backlog',10,true,
        '5ea10000-0000-0000-0000-0000000000a0'),
       ('5ea10000-0000-0000-0000-0000000000e2',
        '5ea10000-0000-0000-0000-000000000003','Inbox','backlog',10,true,
        '5ea10000-0000-0000-0000-0000000000a0');

-- ⚠️ `root_project_id` is NOT NULL and it is the ROOT, not the immediate
-- parent: both tasks sit in the Area (…002) whose root is Ada's personal
-- project (…001). Seeding it as the Area would make the tree shallow and the
-- section-3 check meaningless.
INSERT INTO pm_tasks (id, organization_id, project_id, root_project_id,
                      task_number, status_id, title, created_by)
VALUES ('5ea10000-0000-0000-0000-0000000000f1',
        '5ea10000-0000-0000-0000-0000000000a0',
        '5ea10000-0000-0000-0000-000000000002',
        '5ea10000-0000-0000-0000-000000000001',9001,'5ea10000-0000-0000-0000-0000000000e1','Ada only','ada@seal.invalid'),
       ('5ea10000-0000-0000-0000-0000000000f2',
        '5ea10000-0000-0000-0000-0000000000a0',
        '5ea10000-0000-0000-0000-000000000002',
        '5ea10000-0000-0000-0000-000000000001',9002,'5ea10000-0000-0000-0000-0000000000e1','Bo helps','ada@seal.invalid');
INSERT INTO pm_task_assignees (task_id, assignee, assigned_by, organization_id)
VALUES ('5ea10000-0000-0000-0000-0000000000f2','bo@seal.invalid',
        'ada@seal.invalid','5ea10000-0000-0000-0000-0000000000a0');

-- ── 1. The column exists, and it is nullable with no default (R6) ─────────
SELECT seal_check('1a pm_projects.sealed_at exists',
    (SELECT count(*) FROM information_schema.columns
      WHERE table_name = 'pm_projects' AND column_name = 'sealed_at'), 1::bigint);
SELECT seal_check('1b nullable, no default — every existing row reads "live"',
    (SELECT is_nullable = 'YES' AND column_default IS NULL
       FROM information_schema.columns
      WHERE table_name = 'pm_projects' AND column_name = 'sealed_at'), true);

-- ── 2. Before the seal, Ada sees her own tree ─────────────────────────────
--
-- This is `_VISIBLE_PROJECTS_SQL` with the seal filter, inlined. It is kept in
-- step with core.py by `test_member_seal.py`, which asserts the real constant
-- carries both filters — so this file cannot quietly test a different query.
CREATE OR REPLACE FUNCTION seal_visible(who text) RETURNS bigint
LANGUAGE sql AS $$
    WITH RECURSIVE granted AS (
        SELECT DISTINCT g.project_id AS id
        FROM pm_project_grants g
        JOIN pm_projects gp ON gp.id = g.project_id
        WHERE g.organization_id = '5ea10000-0000-0000-0000-0000000000a0'
          AND gp.sealed_at IS NULL
          AND (g.subject = 'org' OR lower(g.subject) = who)
        UNION
        SELECT p.id FROM pm_projects p JOIN granted a ON p.parent_project_id = a.id
        WHERE p.organization_id = '5ea10000-0000-0000-0000-0000000000a0'
          AND p.sealed_at IS NULL
    )
    SELECT count(*) FROM granted
$$;

SELECT seal_check('2a Ada sees 3 — her root, her Area, and the org project',
    seal_visible('ada@seal.invalid'), 3::bigint);
SELECT seal_check('2b Bo sees 1 — the org project only',
    seal_visible('bo@seal.invalid'), 1::bigint);

-- ── 3. Seal the ROOT ONLY, and the whole subtree must go ──────────────────
--
-- ⚠️ The sharpest check in the file. The recursion descends from granted
-- seeds, so a filter on the seed alone would hide the root and LEAVE THE AREA
-- REACHABLE through it — no, worse: it would orphan the Area out of the
-- closure while the root vanished. Sealing one row must remove two.
UPDATE pm_projects SET sealed_at = now()
 WHERE id = '5ea10000-0000-0000-0000-000000000001';

SELECT seal_check('3a Ada now sees 1 — the org project, not her own tree',
    seal_visible('ada@seal.invalid'), 1::bigint);
SELECT seal_check('3b the Area went with the root, though nothing stamped it',
    (SELECT count(*) FROM pm_projects
      WHERE id = '5ea10000-0000-0000-0000-000000000002'
        AND sealed_at IS NULL), 1::bigint);
SELECT seal_check('3c the team project is untouched',
    seal_visible('bo@seal.invalid'), 1::bigint);

-- ── 4. THE HAND-OVER: the assignee arm survives the seal ──────────────────
--
-- D63's first rule, and the reason it needs no data movement. Bo keeps the
-- task Ada assigned him even though its project is sealed, because
-- `task_visibility_clause`'s second arm never consults the project.
CREATE OR REPLACE FUNCTION seal_tasks(who text) RETURNS bigint
LANGUAGE sql AS $$
    SELECT count(*) FROM pm_tasks t
     WHERE t.organization_id = '5ea10000-0000-0000-0000-0000000000a0'
       AND (t.project_id IN (
                WITH RECURSIVE granted AS (
                    SELECT DISTINCT g.project_id AS id
                    FROM pm_project_grants g
                    JOIN pm_projects gp ON gp.id = g.project_id
                    WHERE g.organization_id = '5ea10000-0000-0000-0000-0000000000a0'
                      AND gp.sealed_at IS NULL
                      AND (g.subject = 'org' OR lower(g.subject) = who)
                    UNION
                    SELECT p.id FROM pm_projects p
                    JOIN granted a ON p.parent_project_id = a.id
                    WHERE p.organization_id = '5ea10000-0000-0000-0000-0000000000a0'
                      AND p.sealed_at IS NULL
                )
                SELECT id FROM granted)
            OR EXISTS (SELECT 1 FROM pm_task_assignees a
                        WHERE a.task_id = t.id AND lower(a.assignee) = who))
$$;

SELECT seal_check('4a Bo still sees the task Ada handed him',
    seal_tasks('bo@seal.invalid'), 1::bigint);
SELECT seal_check('4b Ada sees neither of her own sealed tasks',
    seal_tasks('ada@seal.invalid'), 0::bigint);

-- ── 5. The seal is case-folded on the owner (R10) ─────────────────────────
--
-- `personal_owner` here is 'Ada@Seal.invalid'. A seal seeded on the raw column
-- would miss a caller who typed the address in lower case, which is how every
-- other read in this package spells it.
UPDATE pm_projects SET sealed_at = NULL
 WHERE id = '5ea10000-0000-0000-0000-000000000001';

WITH RECURSIVE tree AS (
    SELECT p.id FROM pm_projects p
     WHERE lower(p.personal_owner) = 'ada@seal.invalid'
       AND p.organization_id = '5ea10000-0000-0000-0000-0000000000a0'
    UNION
    SELECT c.id FROM pm_projects c JOIN tree t ON c.parent_project_id = t.id
)
UPDATE pm_projects SET sealed_at = now()
 WHERE id IN (SELECT id FROM tree) AND sealed_at IS NULL;

SELECT seal_check('5a folding the owner finds the tree, and stamps BOTH rows',
    (SELECT count(*) FROM pm_projects
      WHERE id IN ('5ea10000-0000-0000-0000-000000000001',
                   '5ea10000-0000-0000-0000-000000000002')
        AND sealed_at IS NOT NULL), 2::bigint);

-- ── 6. Idempotence: a retry must not move the timestamp ───────────────────
--
-- The timestamp is evidence of when the workspace closed. `sealed_at IS NULL`
-- in the WHERE is what keeps a second call from quietly rewriting it.
CREATE TEMP TABLE seal_before AS
    SELECT id, sealed_at FROM pm_projects
     WHERE id = '5ea10000-0000-0000-0000-000000000001';

WITH RECURSIVE tree AS (
    SELECT p.id FROM pm_projects p
     WHERE lower(p.personal_owner) = 'ada@seal.invalid'
       AND p.organization_id = '5ea10000-0000-0000-0000-0000000000a0'
    UNION
    SELECT c.id FROM pm_projects c JOIN tree t ON c.parent_project_id = t.id
)
UPDATE pm_projects SET sealed_at = now()
 WHERE id IN (SELECT id FROM tree) AND sealed_at IS NULL;

SELECT seal_check('6a the second seal changed nothing',
    (SELECT count(*) FROM pm_projects p JOIN seal_before b ON b.id = p.id
      WHERE p.sealed_at IS DISTINCT FROM b.sealed_at), 0::bigint);

-- ── 7. Nothing was deleted. D63 is emphatic about this ────────────────────
SELECT seal_check('7a both projects are still on disk',
    (SELECT count(*) FROM pm_projects
      WHERE id IN ('5ea10000-0000-0000-0000-000000000001',
                   '5ea10000-0000-0000-0000-000000000002')), 2::bigint);
SELECT seal_check('7b both tasks are still on disk',
    (SELECT count(*) FROM pm_tasks
      WHERE id IN ('5ea10000-0000-0000-0000-0000000000f1',
                   '5ea10000-0000-0000-0000-0000000000f2')), 2::bigint);

-- ── 8. Unsealing restores exactly what the seal removed ───────────────────
UPDATE pm_projects SET sealed_at = NULL
 WHERE id IN ('5ea10000-0000-0000-0000-000000000001',
              '5ea10000-0000-0000-0000-000000000002');
SELECT seal_check('8a Ada is back to 3',
    seal_visible('ada@seal.invalid'), 3::bigint);

ROLLBACK;
