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

-- The fence. Added after the backfill so it validates against real rows.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'pm_projects_root_owns_statuses'
    ) THEN
        ALTER TABLE pm_projects
            ADD CONSTRAINT pm_projects_root_owns_statuses
            CHECK (parent_project_id IS NOT NULL OR owns_statuses);
    END IF;
END $$;

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
-- in changes everybody''s board at once and can stamp `completed_at` across a
-- whole category, which is an administrative act wearing an editor's clothes.
--
-- Named `projects:settings:*` rather than `projects:statuses:*` on purpose: the
-- custom-field definitions and the lifecycle policy are the same kind of act and
-- adopt this same permission rather than minting a second one. Tags deliberately
-- stay open — a tag is additive, and a vocabulary nobody may add to is a
-- vocabulary nobody uses.
--
-- NARROWING, and that is the point: before this, anyone who could see a space
-- could reshape its statuses. owner already holds '*'.

DO $$
DECLARE
    org_id UUID;
    rid    UUID;
    role_slug TEXT;
BEGIN
    FOR org_id IN SELECT id FROM organization LOOP
        -- admin runs the platform; manager owns the processes these lanes
        -- describe. member and guest do the work inside them.
        FOREACH role_slug IN ARRAY ARRAY['admin', 'manager', 'agent_service'] LOOP
            SELECT id INTO rid FROM org_role
             WHERE organization_id = org_id AND org_role.slug = role_slug;
            IF rid IS NOT NULL THEN
                INSERT INTO org_role_permission (role_id, permission)
                VALUES (rid, 'projects:settings:write')
                ON CONFLICT DO NOTHING;
            END IF;
        END LOOP;
    END LOOP;
END $$;
