-- ============================================================================
-- 400_pm_projects_work_style.sql — the work style of a project node (WS-39
-- SP-3a, Metorite task 962, D195).
--
-- What: one column on `pm_projects`.
--       work_style  TEXT NOT NULL DEFAULT 'flow', with the named CHECK
--                   `pm_projects_work_style` (1 to 40 characters). It holds
--                   the style of one node: a space, a folder or a project.
--                   The picker "How this team works" writes it. A node with
--                   the style `sprints` shows the Backlog canvas and the cycle
--                   UI, while `PROJECTS_SPRINTS_ORGS` holds its org.
-- Why:  project-docs/specs/project_management_app.md §13.1 (the catalogue)
--       and the row SP-3a of §13.6. The style sits on any node, set by hand,
--       with no inheritance.
--
-- The values live in code, never in this CHECK:
--   `gateway/routes/projects/core.py::WORK_STYLES` holds the five styles of
--   §13.1 (flow, sprints, shape_up, plan, personal), and `LIVE_WORK_STYLES`
--   holds the two that a member can pick today (flow, sprints). The CHECK
--   holds only the length, so a new style is a code change and not a
--   migration. The route refuses an unknown style and a style that is not
--   live with 422 before any SQL runs.
--
-- A change never deletes (§13.1 rule 2). A change of style writes this one
--   column. It hides blocks. It deletes no cycle and no membership row, so a
--   change back shows the data again.
--
-- Who writes it: only `PATCH /projects/nodes/{id}` and `POST /projects/nodes`
--   (`gateway/routes/projects/tree.py`), through `core.validate_work_style`.
--
-- Expand only (R6): one new column, no rename, no drop and no backfill. The
--   old code never names the column, and its INSERT gets the default. A
--   constant default changes only the catalog on PG 11 and later, so the
--   ALTER does not rewrite the table. Each row from before this file reads
--   `flow`, the default of §13.1.
-- The CHECK is added NOT VALID and then validated, as 372 does. The ALTER
--   TABLE takes the ACCESS EXCLUSIVE lock of `pm_projects`, and the file holds
--   it until COMMIT. VALIDATE reads each row of `pm_projects` once, and the
--   table is small. The file sets lock_timeout first, and the runner tries
--   the whole file again on a lock timeout.
-- Idempotent per infra/postgres/README.md: ADD COLUMN IF NOT EXISTS, and the
--   CHECK only while `pg_constraint` lacks its name. VALIDATE on a valid
--   CHECK does nothing.
--
-- Tenancy (R5): `pm_projects` is tenant-scoped, with the tenant
--   `organization_id` and its policy. The column changes neither. So this
--   file adds no table, no policy and no `gen_tenant_migration.EXEMPT` row.
--   The column holds no member email, so `routes/admin/members.py` does not
--   change.
--
-- The number is taken at build time (R1). On 2026-11-09 origin/main held 393
--   as its highest file. Branches held 396, 397 and 398, and the local branch
--   of SP-8 held 399. So this file is 400. Check it again at merge.
-- Pinned by tests/unit/test_project_work_style.py and
--   tests/unit/test_project_work_style_r8.py (R8), which find this file by
--   CONTENT, never by number (R1).
-- ============================================================================

BEGIN;

SET LOCAL lock_timeout = '5s';

ALTER TABLE pm_projects
    ADD COLUMN IF NOT EXISTS work_style TEXT NOT NULL DEFAULT 'flow';

DO $check$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conname = 'pm_projects_work_style'
           AND conrelid = 'pm_projects'::regclass
    ) THEN
        ALTER TABLE pm_projects ADD CONSTRAINT pm_projects_work_style
            CHECK (char_length(work_style) BETWEEN 1 AND 40) NOT VALID;
    END IF;
END
$check$;

ALTER TABLE pm_projects VALIDATE CONSTRAINT pm_projects_work_style;

COMMENT ON COLUMN pm_projects.work_style IS
    'How the team of this node works (WS-39 SP-3a, D195): flow (the default) or sprints. '
    'core.WORK_STYLES holds the values. project-docs/specs/project_management_app.md §13.1.';

COMMIT;
