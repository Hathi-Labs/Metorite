-- 48_task_manager_gtd.sql — canonical GTD store for the Task Manager app.
--
-- What: the seven task-manager tables from project-docs/specs/task_manager_app.md §4 —
--       task_accounts (connected PM-tool workspaces, multi-account/multi-provider, like
--       email_accounts), gtd_contexts, my_tasks_horizons, gtd_projects, gtd_items, gtd_waiting,
--       my_tasks_reviews.
-- Why:  the /tasks gateway API + agent-task-manager operate on a canonical Postgres store
--       with a GTD-semantic overlay (dual-source: LOCAL rows we own; SYNCED rows mirror a
--       connected provider — ClickUp first).
-- Deltas vs the spec listing (spec updated to match):
--       · gtd_items/gtd_projects gain user_id — LOCAL rows have no account_id to scope
--         through, and every route is user-scoped (same posture as email_messages→accounts).
--       · gtd_items gains defer_until (tickler/snooze, spec §2.1 C13) and sync_state
--         ('local'|'pending'|'synced' — clarified-but-not-yet-pushed, spec §2.2 P8).
--       · task_accounts gains schema_cache — the provider schema fetched beforehand
--         (projects/members/statuses, spec §2.2.1) so Clarify pickers are instant.
-- Depends on: nothing outside this file (self-contained; no FKs to earlier tables).
-- Idempotent: every statement is IF NOT EXISTS / ON CONFLICT-safe — apply_migrations.sh
--             re-runs all 02+ migrations on every deploy.

-- ── Connected PM-tool workspaces ────────────────────────────────────────────
-- One row per (user, provider, workspace): several ClickUp workspaces/companies
-- coexist as separate rows, each with its own encrypted credentials.
CREATE TABLE IF NOT EXISTS task_accounts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id TEXT NOT NULL,
    provider TEXT NOT NULL,              -- free string: 'clickup' | 'asana' | 'jira' | 'linear' | …
    connector_kind TEXT NOT NULL DEFAULT 'api', -- 'api' (REST adapter) | 'mcp' (tool's MCP server)
    workspace_id TEXT NOT NULL,          -- provider-native workspace/team id
    label TEXT,                          -- display name e.g. 'Fracktal ClickUp'
    credentials_encrypted TEXT NOT NULL, -- key-store-encrypted JSON blob (api token / oauth / mcp auth)
    capabilities JSONB DEFAULT '{}',     -- what this backend supports (create, assign, members, webhooks…)
    field_map JSONB DEFAULT '{}',        -- canonical GTD field ↔ native field mapping
    schema_cache JSONB DEFAULT '{}',     -- fetched-beforehand provider schema: {projects, members, statuses}
    sync_enabled BOOLEAN DEFAULT true,
    sync_interval_secs INTEGER DEFAULT 300,
    sync_status TEXT DEFAULT 'idle',     -- 'idle' | 'syncing' | 'error'
    sync_error TEXT,
    last_synced_at TIMESTAMPTZ,
    last_delta_token TEXT,               -- provider incremental cursor
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now(),
    UNIQUE(user_id, provider, workspace_id)
);
CREATE INDEX IF NOT EXISTS idx_task_accounts_user ON task_accounts(user_id);

-- ── Contexts (the @ lists) ──────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS gtd_contexts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id TEXT NOT NULL,
    name TEXT NOT NULL,                  -- '@computer' | '@calls' | …
    icon TEXT,
    sort_order INT DEFAULT 0,
    UNIQUE(user_id, name)
);

-- ── Horizons of Focus (H2 Areas · H3 Goals · H4 Vision · H5 Purpose) ────────
CREATE TABLE IF NOT EXISTS my_tasks_horizons (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id TEXT NOT NULL,
    level INT NOT NULL,                  -- 2=Areas · 3=Goals · 4=Vision · 5=Purpose
    title TEXT NOT NULL,
    notes TEXT,
    created_at TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_gtd_horizons_user ON my_tasks_horizons(user_id, level);

-- ── GTD projects (first-class outcomes needing >1 action, dual-source §5.1) ─
CREATE TABLE IF NOT EXISTS gtd_projects (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'LOCAL',-- 'LOCAL' (we own it) | 'SYNCED' (mirrors a provider)
    account_id UUID REFERENCES task_accounts(id) ON DELETE CASCADE, -- NULL for LOCAL
    provider_ref TEXT,                   -- native project/list/epic id; NULL for LOCAL
    outcome TEXT NOT NULL,               -- the "wild success" statement
    purpose TEXT,                        -- natural-planning: why
    status TEXT DEFAULT 'ACTIVE',        -- ACTIVE | SOMEDAY | DONE | DROPPED
    horizon_id UUID REFERENCES my_tasks_horizons(id) ON DELETE SET NULL,
    has_next_action BOOLEAN DEFAULT false, -- the cardinal GTD health check
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_gtd_projects_user ON gtd_projects(user_id, status);
-- Provider projects are unique per account by native ref (mirror upserts key on this).
CREATE UNIQUE INDEX IF NOT EXISTS uq_gtd_projects_provider
    ON gtd_projects(account_id, provider_ref) WHERE source <> 'LOCAL';

-- ── The unified GTD item store (captures + clarified actions) ───────────────
CREATE TABLE IF NOT EXISTS gtd_items (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'LOCAL',-- 'LOCAL' | 'SYNCED'
    account_id UUID REFERENCES task_accounts(id) ON DELETE CASCADE, -- NULL for LOCAL
    provider_task_id TEXT,               -- native id; NULL for LOCAL (we are source of truth)
    provider_url TEXT,
    title TEXT NOT NULL,
    description TEXT,                    -- capture note / details
    -- GTD overlay (ours)
    disposition TEXT DEFAULT 'INBOX',    -- INBOX | NEXT | WAITING | SOMEDAY | PROJECT | REFERENCE | DONE | TRASH
                                         -- (no CALENDAR bucket — the Calendar is a VIEW over is_hard_date items)
    next_action TEXT,                    -- the clarified physical next action
    context TEXT,                        -- '@computer' | '@calls' | …
    energy TEXT,                         -- 'low' | 'medium' | 'high'
    time_estimate_mins INT,
    is_two_minute BOOLEAN DEFAULT false,
    project_id UUID REFERENCES gtd_projects(id) ON DELETE SET NULL,
    horizon_id UUID REFERENCES my_tasks_horizons(id) ON DELETE SET NULL,
    defer_until TIMESTAMPTZ,             -- tickler: hidden from the active inbox until this date
    sync_state TEXT DEFAULT 'local',     -- 'local' | 'pending' (queued push, Action-Broker-gated) | 'synced'
    -- Mirrored from provider (provider is source of truth for SYNCED)
    provider_status TEXT,                -- the tool's stage, e.g. 'Backlog' | 'To-do'
    assignee JSONB,                      -- {name, email, provider_user_id}
    is_mine BOOLEAN DEFAULT true,        -- false = someone else's task we monitor
    due_at TIMESTAMPTZ,
    is_hard_date BOOLEAN DEFAULT false,  -- true → belongs on the Calendar (hard landscape)
    completed_at TIMESTAMPTZ,
    clarified_at TIMESTAMPTZ,            -- when it left the inbox
    synced_at TIMESTAMPTZ DEFAULT now(),
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_gtd_items_provider
    ON gtd_items(account_id, provider_task_id) WHERE source <> 'LOCAL';
CREATE INDEX IF NOT EXISTS idx_gtd_items_user_disposition ON gtd_items(user_id, disposition, created_at);
CREATE INDEX IF NOT EXISTS idx_gtd_items_context ON gtd_items(context, disposition) WHERE disposition = 'NEXT';
CREATE INDEX IF NOT EXISTS idx_gtd_items_project ON gtd_items(project_id);
CREATE INDEX IF NOT EXISTS idx_gtd_items_search ON gtd_items
    USING GIN (to_tsvector('english', coalesce(title,'') || ' ' || coalesce(description,'')));

-- ── Waiting-For (delegation + monitoring core, §6) ──────────────────────────
CREATE TABLE IF NOT EXISTS gtd_waiting (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    item_id UUID NOT NULL REFERENCES gtd_items(id) ON DELETE CASCADE,
    waiting_on JSONB NOT NULL,           -- {name, email, provider_user_id}
    delegated_at TIMESTAMPTZ NOT NULL,
    expected_by TIMESTAMPTZ,
    last_nudged_at TIMESTAMPTZ,
    resolved BOOLEAN DEFAULT false,
    created_at TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_gtd_waiting_open ON gtd_waiting(resolved, expected_by) WHERE resolved = false;
CREATE INDEX IF NOT EXISTS idx_gtd_waiting_item ON gtd_waiting(item_id);

-- ── Weekly reviews ──────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS my_tasks_reviews (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id TEXT NOT NULL,
    ran_at TIMESTAMPTZ DEFAULT now(),
    summary JSONB,                       -- counts cleared, projects w/o next action, stale waiting-fors
    created_at TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_gtd_reviews_user ON my_tasks_reviews(user_id, ran_at DESC);
