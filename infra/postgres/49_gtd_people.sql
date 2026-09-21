-- 49_gtd_people.sql — the org-knowledge layer for the Task Manager (spec §6.1).
--
-- What: people — the company's people with roles, departments, skills
--       (org chart + resume-extracted), capacity/load hours, and the person's
--       ClickUp user id (the real assignment target for delegation).
-- Why:  capability-aware processing of the GTD inbox: the assistant proposes
--       WHO should own a task by matching it to skills and availability, not
--       just by a name appearing in the text. Data ported from the
--       agent-project-manager repo (agent-data/hr_structure.json +
--       resume_profiles.json) via scripts/import_hr_people.py — that repo /
--       the HR system stays the source of truth; this table is a synced cache.
-- Sensitivity: HR-adjacent (roles, skills, capacity). Served only through the
--       authenticated gateway; personal phone numbers are deliberately NOT
--       imported.
-- Depends on: nothing (standalone; joined by name/email/clickup_user_id).
-- Idempotent: IF NOT EXISTS everywhere — apply_migrations.sh re-runs 02+ on
--       every deploy.

-- == The `gtd_` name is retired (owner directive, 2026-09-21) ================
--
-- **THE RENAME LIVES IN THE FILE THAT CREATES THE TABLE, and that is what
-- makes it safe.** The obvious alternative -- one migration at the end that
-- renames -- breaks every earlier file on replay: an ALTER TABLE addresses a
-- name that is gone, and CREATE TABLE IF NOT EXISTS happily builds an empty
-- duplicate beside the real one. Both shapes were measured on 2026-09-21 and
-- both were abandoned.
--
-- One file answers for one table in all three states:
--
--   * Fresh install -- nothing to rename, the CREATE below makes the new
--     name directly.
--   * A database that predates this -- the old table is renamed WITH ITS
--     ROWS, and the CREATE below then finds the name taken and skips.
--   * Replay -- already renamed, the guard matches nothing. Idempotent.
--
-- relkind = 'r' so a view wearing the old name is left alone rather than
-- renamed into the table's place.
--
-- Index and constraint names keep their old spelling. A rename does not
-- touch them, migration 148 looks some of them up BY NAME, and no query
-- names an index.
--
-- WARNING: this block names the gtd_people table, so it must survive a rename
-- sweep. The first attempt added it BEFORE sweeping the tree, and the sweep
-- rewrote `ALTER TABLE gtd_people RENAME TO people` into `ALTER TABLE people RENAME TO
-- people` -- a silent no-op that left an upgraded database on the old tables
-- with new empty ones beside them. Caught only by rebuilding a real
-- pre-change database and upgrading it, which is now
-- tests/unit/test_people_rename_upgrade.py. The old name is spelled ONCE
-- here, as a quoted literal passed to format().

DO $rename_people$
DECLARE
    old_name CONSTANT text := 'gtd_people';
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
         WHERE c.relname = old_name AND c.relkind = 'r'
           AND n.nspname = current_schema()
    ) AND to_regclass('public.people') IS NULL THEN
        EXECUTE format('ALTER TABLE %I RENAME TO %I', old_name, 'people');
        RAISE NOTICE 'renamed % -> people', old_name;
    END IF;
END
$rename_people$;

CREATE TABLE IF NOT EXISTS people (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    email TEXT,                          -- may be absent in the org chart
    role TEXT,
    department TEXT,
    team TEXT,
    reports_to TEXT,                     -- the department head (org chart)
    status TEXT DEFAULT 'active',        -- 'active' | 'inactive' | …
    skills TEXT[] DEFAULT '{}',          -- union: org-chart skills + resume-extracted skills
    resume_summary TEXT,                 -- experience summary from the resume profile
    years_experience INT,
    domain TEXT,                         -- resume-inferred domain
    capacity_hours_per_week INT,
    current_load_hours_per_week INT,
    available_hours_per_week INT,
    clickup_user_id TEXT,                -- provider assignment target
    source TEXT DEFAULT 'agent-project-manager',
    synced_at TIMESTAMPTZ DEFAULT now(),
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now(),
    UNIQUE(name)                         -- names are unique in the org chart; the import upserts on this
);
CREATE INDEX IF NOT EXISTS idx_gtd_people_status ON people(status);
CREATE INDEX IF NOT EXISTS idx_gtd_people_skills ON people USING GIN(skills);
