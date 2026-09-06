-- ============================================================================
-- 196_projects_status_sets.sql — a project may own its statuses, not only a root
-- ============================================================================
-- Owner directive 2026-09-06. This REVERSES the decision of 2026-09-03 that
-- statuses are root-scoped and a per-project override does not exist.
--
-- What made the old answer right, and why it survives:
--
--   The 2026-09-03 reason was that `category` is the only vocabulary two spaces
--   share, and every cross-project number rests on it. That is still true and
--   is NOT an argument against local lane names. Every status in every set
--   still carries one of the six categories; completion, the roll-up, the
--   analytics and the personal lens key on the category and never on the name.
--   So the shared vocabulary is untouched while the lane names become local —
--   the same split the reference product draws when it groups custom statuses
--   under Not started / Active / Done / Closed.
--
-- The model, in one sentence: a project uses the status set of the NEAREST node
-- at or above it that owns one. A root always owns one, so the walk always
-- ends. `owns_statuses` is that flag.
--
-- ⚠️ A FLAG rather than "does it have rows". Switching a project back to
-- inheriting must not destroy the lanes it spent a year naming, so a set that
-- stops being used goes DORMANT and comes back if you switch again. Presence of
-- rows cannot express dormant.
--
-- The CHECK is the fence (R7): a root has nothing above it to inherit from, so
-- a root that owns nothing would resolve to no statuses at all. `tree.py` must
-- therefore stamp the flag when it creates a root and when a move promotes a
-- node to one — which is the behaviour we want and the reason this is a
-- constraint rather than a comment.
--
-- Also here, because it is the same directive: `projects:settings:write`, the
-- permission an admin assigns before anybody may reshape a project's settings.
--
-- Expand only (R6): the column is added with a default, backfilled, and then
-- constrained. Nothing is renamed and nothing is dropped. `pm_task_statuses.
-- is_default` stops being READ by this release and is dropped in a later one.
--
-- Idempotent. Depends on: 130_org_access_control.sql, 146_projects.sql,
-- 193_projects_node_kind.sql.
-- ============================================================================

-- ── 1 · Ownership ───────────────────────────────────────────────────────────

ALTER TABLE pm_projects
    ADD COLUMN IF NOT EXISTS owns_statuses BOOLEAN NOT NULL DEFAULT false;

COMMENT ON COLUMN pm_projects.owns_statuses IS
    'Does this node carry its own status set? Resolution walks up to the '
    'nearest node with this set. Always true on a root (CHECK below). False '
    'with rows present means a DORMANT set, kept so switching back restores it.';

-- Backfill: every root owns its set, which is exactly today's behaviour.
-- Nothing below a root owns one yet, so every existing tree resolves to the
-- same statuses it resolved to before this migration ran.
UPDATE pm_projects
   SET owns_statuses = true
 WHERE parent_project_id IS NULL
   AND owns_statuses IS DISTINCT FROM true;

-- ⚠️ **The CHECK that belongs here is DEFERRED to a later release, and the
-- reason is R6 written in blood.**
--
-- A `CHECK (parent_project_id IS NOT NULL OR owns_statuses)` was here, and it
-- reached production on 2026-09-06. The deploy applies migrations BEFORE it
-- restarts services — that ordering is the whole point of R6 — so for the
-- window between the two, the OLD code met the NEW constraint. The old code
-- does not stamp `owns_statuses`, so creating a space would have failed with a
-- constraint violation. It was dropped by hand the same hour.
--
-- Expand now, tighten later: once the code that stamps the flag is serving,
-- a follow-up migration can add the constraint safely. A constraint that only
-- the NEW code satisfies cannot ship in the SAME release as that code.
--
-- The invariant it would have expressed is still real and is held in code:
-- `tree.create_project` stamps the flag on a root, and `tree.move_node` stamps
-- it on a node promoted to one.

-- Resolution walks ancestors and asks this question at each step.
CREATE INDEX IF NOT EXISTS idx_pm_projects_owns_statuses
    ON pm_projects (id) WHERE owns_statuses;

-- ── 2 · Who may reshape a project ───────────────────────────────────────────
--
-- Owner directive 2026-09-06: *"you need to have the appropriate permissions
-- set in the organization by an admin to be able to make these high-level
-- changes to the projects and the settings of the project (so that it is not
-- mismanaged by the team)"*.
--
--   projects:settings:write   choose which status set a project uses, and edit
--                             the lanes in it — every act that moves other
--                             people's tasks between lanes, or that can mark
--                             them complete.
--
-- Why this is the boundary. Creating and moving TASKS stays open to anyone with
-- the project, because that is the work. Reshaping the lanes those tasks live
-- in changes everybody's board at once and can stamp `completed_at` across a
-- whole category, which is an administrative act wearing an editor's clothes.
--
-- Named `projects:settings:*` rather than `projects:statuses:*` on purpose: the
-- custom-field definitions and the lifecycle policy are the same kind of act and
-- adopt this same permission rather than minting a second one. Tags deliberately
-- stay open — a tag is additive, and a vocabulary nobody may add to is a
-- vocabulary nobody uses.
--
-- NARROWING, and that is the point: before this, anyone who could see a space
-- could reshape its statuses. `owner` already holds '*'.
--
-- ⚠️ **The GRANT ITSELF is not written here. It is in 179's seed.**
--
-- The first draft of this file inserted straight into `org_role_permission` for
-- every existing organization, and two fences in `test_org_provisioning.py`
-- caught it between them:
--
--   * `…reproduces_the_default_orgs_grant_set_exactly` — `provision_org_roles`
--     is what a NEWLY provisioned org is built from, so a hand-written grant
--     gives the permission to today's customers and silently withholds it from
--     tomorrow's. M1 is "a second org can exist safely"; a role seed that drifts
--     per-org is how that stops being true.
--   * `…seeding_callables_are_defined_exactly_once` (D43-A) — redefining the
--     function here instead would be a competing copy of the doctrine, which is
--     the thing that rule exists to refuse.
--
-- Both are answered by editing the ONE home, 179, and replaying it. That is not
-- a rewrite of an applied migration: `scripts/apply_migrations.sh` replays the
-- whole ladder on every deploy ("safe to execute on every deploy"), and the seed
-- is `CREATE OR REPLACE` over `ON CONFLICT DO NOTHING`. 179 runs before this
-- file in the same replay, so by the time the loop below calls it, the function
-- already carries the new permission.

-- ⚠️ **The rows are written here, NOT by calling `provision_org_roles`.**
--
-- Calling it was the obvious shape and it FAILED on production:
--
--     null value in column "organization_id" of relation
--     "org_role_permission" violates not-null constraint
--
-- That seed's INSERT names only `(role_id, permission)`. On production the
-- tenancy work added `org_role_permission.organization_id` and made it NOT
-- NULL — and those files live in `infra/postgres/generated/`, which the ladder
-- does NOT replay (it globs `NN_*.sql` in `infra/postgres` only). So the seed
-- cannot insert on a tenancy-applied database at all.
--
-- ⚠️ That is a defect BIGGER than this migration: `provision_org_roles` is what
-- a NEW organization is built from, so **provisioning a second organization on
-- production is currently broken** — an M1 blocker. It is recorded in
-- `project-docs/HANDOFF.md` rather than fixed quietly here, because a fix
-- belongs with the people who own the tenancy phase and needs its own test.
--
-- What this file does instead is write exactly the rows it means, for the roles
-- 179's seed now names, and it carries `organization_id` when the column is
-- there. The `IF EXISTS` is not defensive noise: the ladder alone (CI's replay,
-- a fresh developer database) has no such column, and production does.

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
         WHERE table_name = 'org_role_permission'
           AND column_name = 'organization_id'
    ) THEN
        EXECUTE $grant$
            INSERT INTO org_role_permission (role_id, permission, organization_id)
            SELECT r.id, 'projects:settings:write', r.organization_id
              FROM org_role r
             WHERE r.slug IN ('admin', 'manager', 'agent_service')
            ON CONFLICT DO NOTHING
        $grant$;
    ELSE
        INSERT INTO org_role_permission (role_id, permission)
        SELECT r.id, 'projects:settings:write'
          FROM org_role r
         WHERE r.slug IN ('admin', 'manager', 'agent_service')
        ON CONFLICT DO NOTHING;
    END IF;
END $$;
