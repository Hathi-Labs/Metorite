-- ============================================================================
-- 197_projects_status_sets_tighten.sql — the CHECK 196 was too early for
-- ============================================================================
-- R6's second half, done on purpose this time.
--
-- Migration 196 originally carried
-- `CHECK (parent_project_id IS NOT NULL OR owns_statuses)` and it reached
-- production on 2026-09-06 in the SAME release as the code that satisfies it.
-- The deploy applies migrations BEFORE it restarts services — that ordering is
-- the entire point of R6 — so for the window between the two, the OLD code met
-- the NEW constraint. The old code does not stamp `owns_statuses`, so creating
-- a space would have failed a check constraint. 196 was amended to stop adding
-- it and this file adds it once the code is serving.
--
-- ⚠️ **It is already present on production**, added by that first failed
-- deploy and never successfully removed — a `DROP CONSTRAINT` issued through
-- the Supabase SQL runner did not persist, which is worth knowing about that
-- tool. So this file exists as much to make the LADDER agree with production as
-- to add anything: without it, a fresh database would lack a constraint the
-- live one has, and that drift is the kind that is discovered years later by
-- something else breaking.
--
-- Why the invariant is worth a constraint at all: a root has nothing above it
-- to inherit statuses from, so a root that owns none resolves to no statuses,
-- and `core.status_owner_id` answers 404 for every read under it. The code
-- holds this — `tree.create_project` stamps a root, `tree.move_node` stamps a
-- node promoted to one — and the constraint is what stops a future writer
-- quietly forgetting.
--
-- Idempotent. Depends on: 196_projects_status_sets.sql.
-- ============================================================================

DO $$
BEGIN
    -- Belt and braces before a constraint that cannot be rolled back: bring any
    -- root that predates the flag up to it. 196's backfill already did this, so
    -- this is expected to touch nothing and costs one indexed scan.
    UPDATE pm_projects
       SET owns_statuses = true
     WHERE parent_project_id IS NULL
       AND owns_statuses IS DISTINCT FROM true;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'pm_projects_root_owns_statuses'
    ) THEN
        ALTER TABLE pm_projects
            ADD CONSTRAINT pm_projects_root_owns_statuses
            CHECK (parent_project_id IS NOT NULL OR owns_statuses);
    END IF;
END $$;
