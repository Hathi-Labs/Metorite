-- 176_people_skills.sql — structured skills and credentials (People Center P-4 / WS-28h).
--
-- Spec: project-docs/specs/people_center_app.md §3.3 · D-PC-6.
--
-- `skills TEXT[]` answers "do they know Python" and nothing else. The
-- assignment questions that actually come up are HOW WELL (a mentor and a
-- beginner are not interchangeable), HOW RECENTLY (five years stale is a
-- different answer), and ON WHAT EVIDENCE (they said so / the CV says so /
-- they shipped it). Three parallel arrays or a second JSONB map would be three
-- things that must agree; one row per (person, skill) is one thing.
--
-- ⚠️ **`people.skills[]` does not go away — it becomes a maintained
-- projection** (D-PC-6). Four live consumers read the array today — the GIN
-- index, `_match_capability()`, `fetch_people_for_clarify()`, and the
-- directory's skill filters — and R6 forbids breaking running code. Every
-- writer of these tables rewrites `skills` and `skills_source` IN THE SAME
-- TRANSACTION via `gateway.person_skills.project()`; the child table is the
-- source, the array is the cache. The fence is a route test asserting the
-- array equals the table after every write path, plus the live harness doing
-- the same against this schema — never a paragraph asking people to remember.
--
-- Expand-only (R6): two new tables, nothing rewritten, nothing renamed.
-- Both are tenant-scoped from day one (R5) — `organization_id` defaults from
-- the session GUC so no call site passes it, and an unbound insert fails the
-- NOT NULL: fail closed, never "the usual org". ⚠️ `REFERENCES` before
-- `DEFAULT`, comma-free — the tenancy ratchet matches the column to its FK
-- with no comma between them, and every form of `current_setting(...)`
-- contains one; written the other way round, a table that IS scoped reads as
-- unscoped (the WS-28k lesson, kept).

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
-- WARNING: this block names the gtd_person_skills table, so it must survive a rename
-- sweep. The first attempt added it BEFORE sweeping the tree, and the sweep
-- rewrote `ALTER TABLE gtd_person_skills RENAME TO people_skills` into `ALTER TABLE people_skills RENAME TO
-- people_skills` -- a silent no-op that left an upgraded database on the old tables
-- with new empty ones beside them. Caught only by rebuilding a real
-- pre-change database and upgrading it, which is now
-- tests/unit/test_people_rename_upgrade.py. The old name is spelled ONCE
-- here, as a quoted literal passed to format().

DO $rename_people_skills$
DECLARE
    old_name CONSTANT text := 'gtd_person_skills';
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
         WHERE c.relname = old_name AND c.relkind = 'r'
           AND n.nspname = current_schema()
    ) AND to_regclass('public.people_skills') IS NULL THEN
        EXECUTE format('ALTER TABLE %I RENAME TO %I', old_name, 'people_skills');
        RAISE NOTICE 'renamed % -> people_skills', old_name;
    END IF;
END
$rename_people_skills$;

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
-- WARNING: this block names the gtd_person_credentials table, so it must survive a rename
-- sweep. The first attempt added it BEFORE sweeping the tree, and the sweep
-- rewrote `ALTER TABLE gtd_person_credentials RENAME TO people_credentials` into `ALTER TABLE people_credentials RENAME TO
-- people_credentials` -- a silent no-op that left an upgraded database on the old tables
-- with new empty ones beside them. Caught only by rebuilding a real
-- pre-change database and upgrading it, which is now
-- tests/unit/test_people_rename_upgrade.py. The old name is spelled ONCE
-- here, as a quoted literal passed to format().

DO $rename_people_credentials$
DECLARE
    old_name CONSTANT text := 'gtd_person_credentials';
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
         WHERE c.relname = old_name AND c.relkind = 'r'
           AND n.nspname = current_schema()
    ) AND to_regclass('public.people_credentials') IS NULL THEN
        EXECUTE format('ALTER TABLE %I RENAME TO %I', old_name, 'people_credentials');
        RAISE NOTICE 'renamed % -> people_credentials', old_name;
    END IF;
END
$rename_people_credentials$;

CREATE TABLE IF NOT EXISTS people_skills (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id     UUID NOT NULL REFERENCES organization (id) ON DELETE CASCADE
                            DEFAULT NULLIF(
                                current_setting('app.tenant_id', true), '')::uuid,
    person_id           UUID NOT NULL REFERENCES people (id) ON DELETE CASCADE,
    -- Stored as typed; uniqueness folds case, exactly as 148 did for email.
    -- "Python" and "python" are one skill, and two rows for it would be two
    -- levels for one fact.
    skill               TEXT NOT NULL CHECK (length(trim(skill)) > 0),
    -- NULL = not assessed. A CHECK is safe here where P-3 refused one: this
    -- table starts empty, so there is no live data to be wrong about.
    level               TEXT CHECK (level IN ('learning', 'working',
                                             'proficient', 'expert')),
    years               REAL CHECK (years >= 0 AND years <= 60),
    -- A year, not a date: "when did you last use Fusion 360" is honestly
    -- answerable to the year and dishonestly precise beyond it.
    last_used_year      SMALLINT CHECK (last_used_year BETWEEN 1970 AND 2100),
    -- Provenance. 'manual' = the person (or an admin) typed it — the value the
    -- existing skills_source map already uses; 'resume' = the parser found it;
    -- 'observed' = derived from shipped work (no writer yet — the CHECK admits
    -- it so the vocabulary needs no migration when one arrives).
    evidence            TEXT NOT NULL DEFAULT 'manual'
                            CHECK (evidence IN ('manual', 'resume', 'observed')),
    updated_by          TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_gtd_person_skills_person_skill
    ON people_skills (person_id, lower(skill));
CREATE INDEX IF NOT EXISTS idx_gtd_person_skills_person
    ON people_skills (person_id);
-- The capability search (§5.5) asks "who knows X" across the org.
CREATE INDEX IF NOT EXISTS idx_gtd_person_skills_skill
    ON people_skills (lower(skill));

-- "Is this person actually qualified to sign this off" — education,
-- certifications and prior roles, extracted from the CV or typed. Facts about
-- history, so rows are appended and edited, never derived.
CREATE TABLE IF NOT EXISTS people_credentials (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id     UUID NOT NULL REFERENCES organization (id) ON DELETE CASCADE
                            DEFAULT NULLIF(
                                current_setting('app.tenant_id', true), '')::uuid,
    person_id           UUID NOT NULL REFERENCES people (id) ON DELETE CASCADE,
    kind                TEXT NOT NULL
                            CHECK (kind IN ('education', 'certification',
                                            'prior_role')),
    title               TEXT NOT NULL CHECK (length(trim(title)) > 0),
    -- The institution, issuer, or employer — whichever the kind makes it.
    issuer              TEXT,
    year_from           SMALLINT CHECK (year_from BETWEEN 1950 AND 2100),
    year_to             SMALLINT CHECK (year_to BETWEEN 1950 AND 2100),
    detail              TEXT,
    source              TEXT NOT NULL DEFAULT 'manual'
                            CHECK (source IN ('manual', 'resume')),
    created_by          TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (year_to IS NULL OR year_from IS NULL OR year_to >= year_from)
);

CREATE INDEX IF NOT EXISTS idx_gtd_person_credentials_person
    ON people_credentials (person_id);
