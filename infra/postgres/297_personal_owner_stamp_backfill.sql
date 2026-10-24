-- ============================================================================
-- 297_personal_owner_stamp_backfill.sql — each node of a private tree carries
-- the owner (WS-64, task #304)
-- ============================================================================
-- Spec: project-docs/specs/one_projects_app.md OP-3 · R5 · R6 · R8.
--
-- What: stamps `personal_owner` on every DESCENDANT of a personal root that
--   does not carry it. The value is the root's own `personal_owner`. No other
--   row changes, and no other column changes.
--
-- Why: `POST /projects/nodes` under a personal parent did not copy the owner
--   to the child. Each read tests the node's OWN column: `tree.py`'s
--   `_visible_projects`, `core.py`'s `_AGENT_NOT_PERSONAL_SQL` and
--   `assert_project_move_keeps_privacy`. So an unstamped child read as a team
--   node. It showed as an orphan root in each `data:org:read` tree, an agent
--   with `include_personal=false` (D-EA-4) could read it, and a move could
--   put it in a team space. The gateway now stamps a new child
--   (`personal_tree_owner`). This migration repairs the rows written before.
--
-- Safety: expand only (R6). It writes a value only where the column IS NULL,
--   so a row that carries an owner keeps it. A team tree has no personal root,
--   so the walk never starts in a team tree. The trigger of 161 keeps a child
--   in its parent's organization, so the walk stays inside one tenant.
--
-- ⚠️ The walk STOPS at a team node. The old `move_node` read only the direct
--   parent's column, so a team root T (with an `org` or `group:` grant) could
--   be moved under an unstamped legacy child X, where both read NULL. A stamp
--   on T would hide it from its team, put it in the owner's My Tasks, and the
--   move guard would then lock it there. So an UNSTAMPED node that carries a
--   grant to any subject other than the root's owner is not stamped, and the
--   walk does not go below it. T and its subtree stay as they are. A RAISE
--   NOTICE names the count of skipped nodes per tenant, and their ids when
--   there are 20 or fewer, so the deploy log shows them. The owner or a
--   Projects lead then moves T out to a team space, which the guard allows.
--
-- Idempotent: a second run finds no NULL inside a private tree, and so
--   updates no row. Old code meets this schema as before: it never reads the
--   column of a child to decide that the child is team work.
--
-- ── FORCE RLS ───────────────────────────────────────────────────────────────
--
-- `pm_projects` carries FORCE ROW LEVEL SECURITY once `generated/04` has run,
-- and the runner connects as the table owner, which FORCE binds too. So the
-- file runs the UPDATE twice, as 221 does. It runs once with no tenant bound,
-- which serves the ladder shape that has no policy yet. Under the policy that
-- pass sees no row. Then, only when the table has row security, it binds
-- each tenant in turn with a local `set_config('app.tenant_id', ...)` and
-- runs the UPDATE for that tenant.
-- `organization` has no policy, so the loop can read it.
--
-- No new table, so `test_tenant_coverage.py` changes nothing.
--
-- Depends on: 147_projects_personal.sql (personal_owner),
--   191_personal_project_tree.sql (the column means private at any depth).
-- Pinned by: tests/unit/test_personal_owner_stamp.py.
-- ============================================================================

SET lock_timeout = '5s';

DO $$
DECLARE
    org uuid;
    rec record;
    -- The walk. A node joins it when it is stamped, or when it carries no
    -- grant to a subject other than the root's owner. An unstamped node with
    -- such a grant is team work, so the walk stops there.
    walk text := $q$
        WITH RECURSIVE tree AS (
            SELECT id, personal_owner AS owner
              FROM pm_projects
             WHERE parent_project_id IS NULL
               AND personal_owner IS NOT NULL
            UNION ALL
            SELECT c.id, t.owner
              FROM pm_projects c
              JOIN tree t ON c.parent_project_id = t.id
             WHERE c.personal_owner IS NOT NULL
                OR NOT EXISTS (
                       SELECT 1 FROM pm_project_grants g
                        WHERE g.project_id = c.id
                          AND lower(g.subject) <> lower(t.owner))
        )
    $q$;
    stamp text;
    skipped text;
BEGIN
    stamp := walk || $q$
        UPDATE pm_projects p
           SET personal_owner = tree.owner
          FROM tree
         WHERE p.id = tree.id
           AND p.personal_owner IS NULL
    $q$;
    -- The nodes the walk stopped at: the unstamped children of a walked node
    -- that are not in the walk themselves.
    skipped := walk || $q$
        SELECT c.organization_id AS org_id,
               count(*) AS n,
               CASE WHEN count(*) <= 20
                    THEN string_agg(c.id::text, ', ' ORDER BY c.id) END AS ids
          FROM pm_projects c
          JOIN tree t ON c.parent_project_id = t.id
         WHERE c.personal_owner IS NULL
           AND NOT EXISTS (SELECT 1 FROM tree x WHERE x.id = c.id)
         GROUP BY c.organization_id
    $q$;

    -- No tenant bound. This pass serves a database with no policy on the
    -- table. Under the policy, `current_setting` is NULL and it sees no row.
    EXECUTE stamp;
    FOR rec IN EXECUTE skipped LOOP
        RAISE NOTICE '297: tenant % kept % team node(s) under a personal tree unstamped: %',
            rec.org_id, rec.n, coalesce(rec.ids, '(more than 20)');
    END LOOP;

    -- Only when the table has row security. A replay of the ladder runs in
    -- one transaction and has no policy yet. A bind there would stay set for
    -- the rest of that transaction and then read as '' in the next one.
    IF EXISTS (SELECT 1 FROM pg_class
                WHERE oid = 'pm_projects'::regclass AND relrowsecurity) THEN
        FOR org IN SELECT id FROM organization LOOP
            PERFORM set_config('app.tenant_id', org::text, true);
            EXECUTE stamp;
            -- A role that bypasses RLS sees every tenant here, so report only
            -- the bound one.
            FOR rec IN EXECUTE skipped LOOP
                IF rec.org_id = org THEN
                    RAISE NOTICE '297: tenant % kept % team node(s) under a personal tree unstamped: %',
                        rec.org_id, rec.n, coalesce(rec.ids, '(more than 20)');
                END IF;
            END LOOP;
        END LOOP;
    END IF;
END $$;
