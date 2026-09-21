-- 74_gtd_people_editable_and_resumes.sql — make the HR/people layer EDITABLE
-- in-app + store uploaded résumés (spec: task_manager_hr_planning_and_memory.md §3-4).
--
-- What: (1) extra editable columns on people so the Task Manager app — not
--       just the agent-project-manager seed — can own roles/skills/manager;
--       (2) people_resumes to hold uploaded CVs (PDF/DOCX) + their parsed
--       skills, linked to a person.
-- Why:  Phase 1 of the HR epic — "update HR structure, edit skills, ingest
--       résumés to auto-update skills". The app becomes the source of truth for
--       people data (user decision 2026-07-16); people.source distinguishes
--       provenance ('agent-project-manager' seed | 'manual' | 'clickup' | 'resume').
-- Depends on: 49_gtd_people.sql.
-- Idempotent: IF NOT EXISTS everywhere — apply_migrations.sh re-runs 02+ on deploy.

-- ── Editable / structured HR fields ──────────────────────────────────────────
ALTER TABLE people ADD COLUMN IF NOT EXISTS title TEXT;
-- Structured hierarchy: the free-text reports_to (org-chart display name) stays;
-- manager_id is the resolvable FK the UI edits. Backfilled name→id separately.
ALTER TABLE people ADD COLUMN IF NOT EXISTS manager_id UUID REFERENCES people(id);
-- Per-skill provenance: {"python": "resume", "leadership": "manual", ...} so the
-- UI can show where a skill came from and a re-import won't clobber manual edits.
ALTER TABLE people ADD COLUMN IF NOT EXISTS skills_source JSONB DEFAULT '{}'::jsonb;
-- Audit: who last edited this row (user email) — the app is now authoritative.
ALTER TABLE people ADD COLUMN IF NOT EXISTS updated_by TEXT;

CREATE INDEX IF NOT EXISTS idx_gtd_people_manager ON people(manager_id);

-- ── Uploaded résumés (one person may have several versions) ───────────────────
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
-- WARNING: this block names the gtd_person_resumes table, so it must survive a rename
-- sweep. The first attempt added it BEFORE sweeping the tree, and the sweep
-- rewrote `ALTER TABLE gtd_person_resumes RENAME TO people_resumes` into `ALTER TABLE people_resumes RENAME TO
-- people_resumes` -- a silent no-op that left an upgraded database on the old tables
-- with new empty ones beside them. Caught only by rebuilding a real
-- pre-change database and upgrading it, which is now
-- tests/unit/test_people_rename_upgrade.py. The old name is spelled ONCE
-- here, as a quoted literal passed to format().

DO $rename_people_resumes$
DECLARE
    old_name CONSTANT text := 'gtd_person_resumes';
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
         WHERE c.relname = old_name AND c.relkind = 'r'
           AND n.nspname = current_schema()
    ) AND to_regclass('public.people_resumes') IS NULL THEN
        EXECUTE format('ALTER TABLE %I RENAME TO %I', old_name, 'people_resumes');
        RAISE NOTICE 'renamed % -> people_resumes', old_name;
    END IF;
END
$rename_people_resumes$;

CREATE TABLE IF NOT EXISTS people_resumes (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    person_id UUID NOT NULL REFERENCES people(id) ON DELETE CASCADE,
    filename TEXT NOT NULL,
    mime TEXT,
    size_bytes INT,
    storage_path TEXT,          -- file on disk (GTD_ATTACHMENTS_DIR), owner-checked on serve
    parsed_text TEXT,           -- extracted plain text (for re-parse / audit)
    extracted JSONB,            -- {skills[], experience_summary, years_experience, domain}
    uploaded_by TEXT,           -- user email
    uploaded_at TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_gtd_person_resumes_person
    ON people_resumes(person_id);
