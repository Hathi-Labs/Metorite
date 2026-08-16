-- ============================================================================
-- 146_projects.sql — the native project-management spine: departments,
--                    projects, subprojects, tasks, subtasks, grants,
--                    statuses-as-data, one activity timeline, per-view order.
--
-- What: spec project-docs/specs/project_management_app.md §3.1–§3.10
--   (WS-27a). Paca's shape — the whole hierarchy is TWO self-referencing
--   foreign keys (pm_projects.parent_project_id, pm_tasks.parent_task_id) and
--   task types are rows, not levels. There is deliberately no department table,
--   no epic/story/subtask table, and no depth column.
--
-- Why: Metorite becomes the system of record for work management and
--   ClickUp becomes an import source, then retires (§7). The personal GTD store
--   (gtd_projects/gtd_items, migration 48) is per-user by construction — 27
--   `user_id = :` predicates in routes/tasks/items.py, a per-user task_accounts
--   sync, and a GTD overlay whose contract is "never clobbered" — so it is NOT
--   extended into an org store: D-PM-1. Assigned tasks reach a member's
--   personal app by mirroring (§6.1, WS-27e), not by sharing rows.
--
-- Three decisions this file encodes, each recorded in §8:
--   * D-PM-3 — visibility is grant-based from day one. pm_project_grants uses
--     the SHIPPED subject vocabulary (email | group:<slug> | org) from
--     tenancy_and_visibility.md §3.2; inventing a second one is forbidden.
--   * D-PM-4 — actors and assignees are strings (an email or `agent:<name>`),
--     never a member-row FK, so a human and an agent are the same kind of
--     assignee with no special-casing anywhere downstream.
--   * D-PM-5 — ordering is PER VIEW, in pm_view_task_positions, by fractional
--     index. There is no position/rank column on pm_tasks: the People Center
--     board and a Center slice must be able to order the same task differently.
--
-- Idempotent per infra/postgres/README.md: every CREATE TABLE / CREATE INDEX
-- carries IF NOT EXISTS and every seed INSERT carries ON CONFLICT. Pinned by
-- tests/unit/test_projects_migration.py, which reads this file as TEXT — §10
-- runs no database, so an idempotency claim that is only true by inspection is
-- not a claim.
--
-- Depends on: 130_org_access_control.sql (feature_catalog, org_group).
-- ============================================================================

-- ── Projects — departments, projects and subprojects, one self-FK ──────── §3.1
--
-- A department is simply a ROOT project whose grant names a Center's group.
-- There is no department table because the Center IS the department
-- (department_centers.md §1) and the grant is what makes a subtree belong to it.
--
-- parent_project_id CASCADEs: deleting a project deletes the subtree beneath it,
-- and the API reports the counts (R7/R8) rather than letting the FK do it
-- silently.

CREATE TABLE IF NOT EXISTS pm_projects (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name                TEXT NOT NULL,
    description         TEXT,
    -- NULL = a root project. Arbitrary depth; cycles are refused in code by a
    -- depth-bounded ancestor walk (Paca's rule — a closure table would be write
    -- amplification nothing here needs).
    parent_project_id   UUID REFERENCES pm_projects (id) ON DELETE CASCADE,
    -- Root projects only: the human-readable task id prefix, e.g. 'RND' → RND-42.
    task_prefix         TEXT,
    status              TEXT NOT NULL DEFAULT 'active'
                            CHECK (status IN ('active', 'on_hold', 'done', 'archived')),
    -- Assignment, NOT an ACL. Who is accountable for this project; visibility is
    -- decided by pm_project_grants alone (D-PM-3).
    lead                TEXT,
    -- Sibling order within one parent. Fractional, same arithmetic as
    -- pm_view_task_positions, so inserting between two siblings never renumbers.
    position            DOUBLE PRECISION,
    source              TEXT NOT NULL DEFAULT 'manual'
                            CHECK (source IN ('manual', 'import', 'agent')),
    clickup_id          TEXT UNIQUE,
    -- Which ClickUp container this came from. A Space, a Folder and a List all
    -- become pm_projects rows at different depths — the importer FLATTENS
    -- ClickUp's container zoo into the one self-FK (§7.1) and this column is
    -- what remembers the original shape for the sync and for retirement.
    clickup_kind        TEXT CHECK (clickup_kind IN ('space', 'folder', 'list')),
    created_by          TEXT NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    archived_at         TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_pm_projects_parent_project_id
    ON pm_projects (parent_project_id);
CREATE INDEX IF NOT EXISTS idx_pm_projects_status
    ON pm_projects (status);
-- The predicate is lower(lead) = :who (R10), so the index must fold case too —
-- a plain column index can never serve it.
CREATE INDEX IF NOT EXISTS idx_pm_projects_lead
    ON pm_projects (lower(lead));
CREATE INDEX IF NOT EXISTS idx_pm_projects_clickup_id
    ON pm_projects (clickup_id);

-- ── Project grants — the scoping primitive ─────────────────────────────── §3.2
--
-- `subject` is EXACTLY the shipped vocabulary: an email address, `group:<slug>`
-- for an org_group, or the literal `org`. tenancy_and_visibility.md §3.2 is
-- binding on this — the rooms table already speaks it, and a second vocabulary
-- would mean two answers to "who can see this".
--
-- A grant applies to the project's whole SUBTREE. Granting a root project to
-- `group:sales` is what projects that department into the Sales Center; that is
-- the entire mechanism, and it is why WS-27b's importer applies grants rather
-- than writing a Center column.
--
-- Sibling of gtd_project_grant (D13, WS-14 C1) on the same vocabulary — NOT a
-- replacement for it. C1 owns the personal Tasks app's slice; this owns the
-- org store's.

CREATE TABLE IF NOT EXISTS pm_project_grants (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id          UUID NOT NULL REFERENCES pm_projects (id) ON DELETE CASCADE,
    subject             TEXT NOT NULL,
    created_by          TEXT NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (project_id, subject)
);

CREATE INDEX IF NOT EXISTS idx_pm_project_grants_subject
    ON pm_project_grants (subject);
CREATE INDEX IF NOT EXISTS idx_pm_project_grants_project_id
    ON pm_project_grants (project_id);

-- ── Task statuses — statuses as DATA, not an enum ──────────────────────── §3.3
--
-- Scoped to a ROOT project; the subtree inherits. The importer has to be able to
-- represent ClickUp's real per-list status names, and the owner has to be able
-- to reshape a workflow without a deploy — the same argument as D-CRM-2, and
-- Paca reached it independently.
--
-- `category` is the machine-readable half. Name, colour and position are free
-- text the owner controls; category is what completion, the personal-mirror
-- disposition mapping (§6.1) and automation gates (§6.3) actually key off.

CREATE TABLE IF NOT EXISTS pm_task_statuses (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id          UUID NOT NULL REFERENCES pm_projects (id) ON DELETE CASCADE,
    name                TEXT NOT NULL,
    color               TEXT NOT NULL DEFAULT 'gray',
    position            INT NOT NULL DEFAULT 0,
    category            TEXT NOT NULL
                            CHECK (category IN ('backlog', 'todo', 'in_progress',
                                                'done', 'cancelled')),
    is_default          BOOLEAN NOT NULL DEFAULT false,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (project_id, name)
);

CREATE INDEX IF NOT EXISTS idx_pm_task_statuses_project_id
    ON pm_task_statuses (project_id, position);

-- ── Task types — semantics, while hierarchy stays structural ───────────── §3.4
--
-- 'Epic' is the one system type, and its only rule is that an Epic-typed task
-- cannot have a parent (enforced in code) — so Epic is structurally the root
-- level. There is deliberately NO 'Subtask' type: a subtask is just a task with
-- a parent. Paca shipped a Subtask system type and then demoted it, having
-- found the hierarchy needed no help from the type system.

CREATE TABLE IF NOT EXISTS pm_task_types (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id          UUID NOT NULL REFERENCES pm_projects (id) ON DELETE CASCADE,
    name                TEXT NOT NULL,
    icon                TEXT,
    color               TEXT,
    is_default          BOOLEAN NOT NULL DEFAULT false,
    is_system           BOOLEAN NOT NULL DEFAULT false,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (project_id, name)
);

CREATE INDEX IF NOT EXISTS idx_pm_task_types_project_id
    ON pm_task_types (project_id);

-- ── Task counters — one atomic sequence per root project ───────────────── §3.5
--
-- A real sequence per project is not workable (they are DDL, and projects are
-- created by an API call), so the counter is a row and the increment is a single
-- UPDATE … RETURNING under the caller's transaction.

CREATE TABLE IF NOT EXISTS pm_task_counters (
    project_id          UUID PRIMARY KEY REFERENCES pm_projects (id) ON DELETE CASCADE,
    last_value          BIGINT NOT NULL DEFAULT 0
);

-- ── Tasks — and subtasks, on one self-FK ───────────────────────────────── §3.5

CREATE TABLE IF NOT EXISTS pm_tasks (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    -- The project this task lives in — any node in the tree, not just a root.
    project_id          UUID NOT NULL REFERENCES pm_projects (id) ON DELETE CASCADE,
    -- The root of that project's tree. Denormalized and maintained by code (it
    -- is re-stamped when a task moves) because the counter, the status/type
    -- scope and every subtree read need it, and walking the parent chain per row
    -- would put a recursive CTE on the hot path.
    root_project_id     UUID NOT NULL REFERENCES pm_projects (id) ON DELETE CASCADE,
    task_number         BIGINT NOT NULL,
    -- NULL = a top-level task. Arbitrary depth; SET NULL rather than CASCADE so
    -- deleting a parent promotes its subtasks instead of destroying work nobody
    -- asked to delete. The API reports the promotion (R7/R8).
    parent_task_id      UUID REFERENCES pm_tasks (id) ON DELETE SET NULL,
    type_id             UUID REFERENCES pm_task_types (id) ON DELETE SET NULL,
    -- RESTRICT: a status still in use cannot be deleted out from under its
    -- tasks. The admin route answers 409 naming the count instead.
    status_id           UUID NOT NULL REFERENCES pm_task_statuses (id) ON DELETE RESTRICT,
    title               TEXT NOT NULL,
    -- Markdown — the platform's format everywhere else. Deliberately not a
    -- block-JSON document format: agents write Markdown, and the notes app
    -- already owns rich documents.
    description         TEXT,
    importance          SMALLINT,
    estimate_mins       INT,
    start_date          DATE,
    due_at              TIMESTAMPTZ,
    -- Stamped when the task's status crosses INTO a done-category status and
    -- cleared when it crosses back out. One helper does all three effects.
    completed_at        TIMESTAMPTZ,
    tags                TEXT[] NOT NULL DEFAULT '{}',
    created_by          TEXT NOT NULL,
    source              TEXT NOT NULL DEFAULT 'manual'
                            CHECK (source IN ('manual', 'import', 'email',
                                              'agent', 'automation')),
    clickup_id          TEXT UNIQUE,
    -- The last-synced ClickUp field state. This is the BASE of the three-way
    -- merge (§7.2/D-PM-7): without it, "both sides changed" is indistinguishable
    -- from "one side changed", and a two-way sync degrades to last-writer-wins.
    clickup_snapshot    JSONB,
    clickup_synced_at   TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    archived_at         TIMESTAMPTZ,
    UNIQUE (root_project_id, task_number)
);

CREATE INDEX IF NOT EXISTS idx_pm_tasks_project_id
    ON pm_tasks (project_id);
CREATE INDEX IF NOT EXISTS idx_pm_tasks_root_status
    ON pm_tasks (root_project_id, status_id);
CREATE INDEX IF NOT EXISTS idx_pm_tasks_parent_task_id
    ON pm_tasks (parent_task_id);
CREATE INDEX IF NOT EXISTS idx_pm_tasks_due_at
    ON pm_tasks (due_at) WHERE completed_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_pm_tasks_clickup_id
    ON pm_tasks (clickup_id);
CREATE INDEX IF NOT EXISTS idx_pm_tasks_tags
    ON pm_tasks USING GIN (tags);
CREATE INDEX IF NOT EXISTS idx_pm_tasks_fts
    ON pm_tasks USING GIN (
        to_tsvector('english', coalesce(title, '') || ' ' || coalesce(description, ''))
    );

-- ── Assignees — humans and agents in ONE vocabulary ────────────────────── §3.6
--
-- `assignee` is an email address (case-folded on write) or `agent:<name>`.
-- D-PM-4: no member-row indirection. The platform already attributes actions
-- this way (crm_activities.created_by, pending_actions.actor), and a join table
-- onto app_user would exclude agents by construction — which is precisely the
-- thing this app must not do, since assigning an agent IS how work is handed to
-- one (§6.4).

CREATE TABLE IF NOT EXISTS pm_task_assignees (
    task_id             UUID NOT NULL REFERENCES pm_tasks (id) ON DELETE CASCADE,
    assignee            TEXT NOT NULL,
    assigned_by         TEXT NOT NULL,
    assigned_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (task_id, assignee)
);

-- "assigned to me" (§4's read model, §6.1's personal mirror) is
-- lower(assignee) = :email, so the index folds case for the same reason as
-- pm_projects.lead above.
CREATE INDEX IF NOT EXISTS idx_pm_task_assignees_assignee
    ON pm_task_assignees (lower(assignee));

-- ── Task links ─────────────────────────────────────────────────────────── §3.7

CREATE TABLE IF NOT EXISTS pm_task_links (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_task_id      UUID NOT NULL REFERENCES pm_tasks (id) ON DELETE CASCADE,
    target_task_id      UUID NOT NULL REFERENCES pm_tasks (id) ON DELETE CASCADE,
    link_type           TEXT NOT NULL
                            CHECK (link_type IN ('blocks', 'relates_to', 'duplicates')),
    created_by          TEXT NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (source_task_id, target_task_id, link_type),
    CHECK (source_task_id <> target_task_id)
);

CREATE INDEX IF NOT EXISTS idx_pm_task_links_target_task_id
    ON pm_task_links (target_task_id);

-- ── Activities — the single timeline spine ─────────────────────────────── §3.8
--
-- Comments and system events are the SAME table, discriminated by `type`. Paca
-- and trycompai both converged on this, and crm_activities is already its
-- sibling here. `meta` carries the type-specific payload:
--
--   field_change → {"changes":[{"field":…,"old":…,"new":…}]}   (powers revert)
--   agent_run    → {"run_id":…, "agent":…}
--   sync         → {"field":…, "ours":…, "theirs":…, "taken":…} (D-PM-7)
--
-- The CHECK requiring a target mirrors crm_activities: a timeline row that
-- points at nothing is unreachable from every read path, so it is refused at
-- the schema rather than discovered as a gap in a report.

CREATE TABLE IF NOT EXISTS pm_activities (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    task_id             UUID REFERENCES pm_tasks (id) ON DELETE CASCADE,
    project_id          UUID REFERENCES pm_projects (id) ON DELETE CASCADE,
    type                TEXT NOT NULL
                            CHECK (type IN ('comment', 'status_change', 'field_change',
                                            'link', 'assignment', 'agent_run',
                                            'sync', 'system')),
    body                TEXT,
    meta                JSONB,
    -- An email, `agent:<name>`, `system:sync`, or `system:workflow:<id>`. Taken
    -- from the authenticated context only (R3) — never from the request body,
    -- which would let a caller write history in somebody else's name.
    created_by          TEXT NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- Comments only; system events are not editable and not deletable.
    deleted_at          TIMESTAMPTZ,
    CHECK (task_id IS NOT NULL OR project_id IS NOT NULL)
);

CREATE INDEX IF NOT EXISTS idx_pm_activities_task_id_created_at
    ON pm_activities (task_id, created_at);
CREATE INDEX IF NOT EXISTS idx_pm_activities_project_id_created_at
    ON pm_activities (project_id, created_at);

-- ── Views ──────────────────────────────────────────────────────────────── §3.9
--
-- One view resource serves list and board (Paca's lesson: one view table + one
-- task-list endpoint, extended via config rather than by new route families).
-- `config` splits PRESENTATION at the top level (fields, column_by, sort_by,
-- swimlanes) from QUERY CONSTRAINTS under config.filters — keeping the two
-- apart is what stops a saved view from silently changing which rows a member
-- may see.

CREATE TABLE IF NOT EXISTS pm_views (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id          UUID NOT NULL REFERENCES pm_projects (id) ON DELETE CASCADE,
    name                TEXT NOT NULL,
    view_type           TEXT NOT NULL DEFAULT 'list'
                            CHECK (view_type IN ('list', 'board')),
    config              JSONB NOT NULL DEFAULT '{}'::jsonb,
    position            DOUBLE PRECISION,
    created_by          TEXT NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_pm_views_project_id
    ON pm_views (project_id, position);

-- ── Per-view task order — fractional indexing ─────────────────────────── §3.10
--
-- D-PM-5. There is NO position column on pm_tasks: the People Center's master
-- board and a Center slice must be able to order the same task differently, and
-- a single rank column cannot serve two views without them fighting.
--
-- `position` is a float; inserting between two neighbours is (prev + next) / 2,
-- so one drag writes exactly one row and nothing is renumbered. `group_key`
-- records which board column/swimlane the task sits in, so a cross-column drag
-- is still one upsert. A task with no row here sorts last by created_at; the
-- first drag into that zone materialises positions for the whole group.

CREATE TABLE IF NOT EXISTS pm_view_task_positions (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    view_id             UUID NOT NULL REFERENCES pm_views (id) ON DELETE CASCADE,
    task_id             UUID NOT NULL REFERENCES pm_tasks (id) ON DELETE CASCADE,
    position            DOUBLE PRECISION NOT NULL,
    group_key           TEXT,
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (view_id, task_id)
);

CREATE INDEX IF NOT EXISTS idx_pm_view_task_positions_view
    ON pm_view_task_positions (view_id, group_key, position);

-- ── Feature registration ────────────────────────────────────────────────────
--
-- sort_order 56 puts Projects immediately after CRM (55), which sits beside
-- Tasks (50) — the three work surfaces together rather than defaulting to last.
--
-- is_default false is deliberate and matches the CRM's posture: `feature:projects`
-- reaches `*`-holders (the owner) and `admin` (`feature:*`) until an admin grants
-- it, because migration 130 enumerates the manager/member feature grants.
--
-- The matching FEATURES entry lives in packages/acb_auth/acb_auth/permissions.py.
-- A catalog row with no FEATURES entry is invisible to /auth/me even for an owner
-- holding `*` — the wildcard is only ever evaluated against those literals — which
-- is the defect migration 140 shipped and why tests/unit/test_org_access_control.py
-- now pins the two against each other in BOTH directions.

INSERT INTO feature_catalog (slug, label, description, nav_href, category, sort_order, is_default) VALUES
    ('projects', 'Projects', 'Departments, projects and team tasks', '/projects', 'apps', 56, false)
ON CONFLICT (slug) DO UPDATE
    SET label       = EXCLUDED.label,
        description = EXCLUDED.description,
        nav_href    = EXCLUDED.nav_href,
        category    = EXCLUDED.category,
        sort_order  = EXCLUDED.sort_order;
        -- is_default is intentionally NOT overwritten (same rule as 130 and 144):
        -- an admin may have retuned the default set and a redeploy must not stomp it.
