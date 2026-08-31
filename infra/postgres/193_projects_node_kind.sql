-- ============================================================================
-- 193_projects_node_kind.sql — a node in the projects tree can be a FOLDER.
--
-- What: `pm_projects.kind` ('project' | 'folder'). A folder is a grouping
--   node. It holds projects (under a space) or subprojects (under a
--   project). It holds no tasks, it has no run state of its own, and it is
--   never a root.
--
-- Why: owner directive 2026-08-31. The tree had one node type and unlimited
--   depth, and real trees grew five levels of identical rows. The new
--   grammar caps depth and makes each level mean something:
--
--     space (root) → [folder] → project → [folder] → subproject
--
--   Projects count toward depth (space=1, project=2, subproject=3, cap 3).
--   Folders are TRANSPARENT to depth and do not nest. The grammar is
--   enforced in the gateway (`gateway/routes/projects/core.py::
--   assert_node_grammar`), not in SQL: the rules need the parent chain, and
--   a CHECK cannot walk one.
--
-- ⚠️ NULLABLE with a default, per R6. Old rows never carry the column until
--   touched, and readers resolve `coalesce(kind, 'project')` — "never
--   stated" and "stated project" mean the same thing. The CHECK below
--   passes NULL for the same reason.
--
-- Idempotent per infra/postgres/README.md.
--
-- Depends on: 146_projects.sql (pm_projects).
-- Pinned by: tests/unit/test_projects_node_kind.py.
-- ============================================================================

ALTER TABLE pm_projects
    ADD COLUMN IF NOT EXISTS kind TEXT DEFAULT 'project';

DO $$ BEGIN
    ALTER TABLE pm_projects
        ADD CONSTRAINT pm_projects_kind_check
        CHECK (kind IN ('project', 'folder'));
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

COMMENT ON COLUMN pm_projects.kind IS
    'project = a level in the work hierarchy (space when root, then project, '
    'then subproject — depth cap 3). folder = a grouping node, transparent to '
    'depth, never a root, never a task holder, never nested in a folder. NULL '
    'reads as project (R6 expand). Grammar lives in the gateway: '
    'core.assert_node_grammar.';
