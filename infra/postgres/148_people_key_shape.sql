-- 148_people_key_shape.sql — fix people's key shape (People Center P-1/P-2).
--
-- What: (1) a stable per-source upsert key (`source_key`) so the HR importer
--       keeps working once names stop being unique; (2) a partial UNIQUE on
--       lower(email) so the email→person join is unambiguous, with conflicting
--       addresses QUARANTINED rather than dropped; (3) UNIQUE(name) removed so
--       two people may share a name; (4) a status vocabulary CHECK.
-- Why:  migration 49 made `name` UNIQUE and left `email` unconstrained. That is
--       backwards for a directory that has to join on email: two real people
--       cannot share a name (they do), and nothing stopped two rows carrying the
--       same address — so an email join could silently attribute one person's
--       capacity to another. The People Center is about to make this table the
--       assignment source, so the shape is fixed before anything relies on it.
--       Spec: project-docs/specs/people_center_app.md §2, §5 (P-1/P-2), §7.
-- Depends on: 49_gtd_people.sql, 74_gtd_people_editable_and_resumes.sql.
--
-- ⚠️ NOTHING HERE MAY BLOCK A DEPLOY. `apply_migrations.sh` runs under
--    `set -euo pipefail` + `ON_ERROR_STOP=1`, so a statement that fails against
--    real data stops the whole deploy — twice already this month (the
--    lock_timeout prelude, the backup-prune dir). Both new constraints could
--    plausibly fail on live rows, so both are written to make the data legal
--    first, non-destructively, instead of failing and demanding a human.
--
-- Idempotent: IF NOT EXISTS everywhere; the backfills are guarded so a re-run
--       is a no-op; the quarantine cannot re-quarantine an already-cleared row.

-- ── 1. A per-source upsert key ──────────────────────────────────────────────
-- `scripts/import_hr_people.py` upserts `ON CONFLICT (name)`. Dropping
-- UNIQUE(name) without giving it another key would break the importer at run
-- time with "no unique or exclusion constraint matching the ON CONFLICT
-- specification" — the consequence P-1 did not name.
--
-- The honest key is per-SOURCE, not per-person: the HR snapshot is a JSON
-- object keyed by name, so names are unique *within that file* whether or not
-- they are unique among humans. `source_key` says exactly that and nothing more.
ALTER TABLE people ADD COLUMN IF NOT EXISTS source_key TEXT;

-- Backfilled BEFORE UNIQUE(name) is dropped, while name is still guaranteed
-- distinct — which is what makes the backfill collision-free by construction.
UPDATE people
   SET source_key = COALESCE(NULLIF(btrim(source), ''), 'manual') || ':' || lower(btrim(name))
 WHERE source_key IS NULL AND name IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS uq_gtd_people_source_key
    ON people (source_key) WHERE source_key IS NOT NULL;

-- ── 2. Email: normalise, quarantine, then constrain ─────────────────────────
-- Where a duplicate address is found the LOSER's address is moved aside rather
-- than deleted. Losing an address silently would be worse than the ambiguity
-- this migration exists to remove, and a deploy that aborts because two rows
-- share an address would be worse than both.
ALTER TABLE people ADD COLUMN IF NOT EXISTS email_conflict TEXT;

COMMENT ON COLUMN people.email_conflict IS
    'An address moved aside because another row already claimed it (migration 148). '
    'Non-null means a human still has to decide which row is the real person.';

-- '' is not NULL, so two blank emails would collide under the unique index.
UPDATE people SET email = NULL
 WHERE email IS NOT NULL AND btrim(email) = '';

-- A trailing space makes two identical addresses look distinct to a human and
-- to `lower(email)` alike.
UPDATE people SET email = btrim(email)
 WHERE email IS NOT NULL AND email <> btrim(email);

-- Winner = most recently updated, then most recently created, then lowest id.
-- Deterministic on purpose: an arbitrary winner would make a re-run against a
-- restored backup pick differently and move a different row's address.
WITH ranked AS (
    SELECT id,
           row_number() OVER (
               PARTITION BY lower(email)
               ORDER BY updated_at DESC NULLS LAST,
                        created_at DESC NULLS LAST,
                        id
           ) AS rn
      FROM people
     WHERE email IS NOT NULL
)
UPDATE people p
   SET email_conflict = COALESCE(p.email_conflict, p.email),
       email = NULL
  FROM ranked r
 WHERE r.id = p.id AND r.rn > 1;

CREATE UNIQUE INDEX IF NOT EXISTS uq_gtd_people_email_lower
    ON people (lower(email)) WHERE email IS NOT NULL;

-- ── 3. Names stop being unique ──────────────────────────────────────────────
-- Looked up by SHAPE rather than assuming Postgres's `gtd_people_name_key`
-- default, so a database whose constraint was created under another name is
-- still cleaned up instead of silently keeping it.
DO $$
DECLARE
    cname text;
BEGIN
    SELECT c.conname INTO cname
      FROM pg_constraint c
      JOIN pg_class t ON t.oid = c.conrelid
     WHERE t.relname = 'people'
       AND c.contype = 'u'
       AND (
            -- attname is type `name`; without the cast the aggregate is
            -- name[] and `name[] = text[]` has no operator — this exact line
            -- aborted EVERY deploy from #374's merge until 2026-08-07, and
            -- with it every migration after 148. CI cannot see this class:
            -- nothing replays the real SQL against a real Postgres.
            SELECT array_agg(a.attname::text ORDER BY a.attname)
              FROM unnest(c.conkey) AS k
              JOIN pg_attribute a ON a.attrelid = t.oid AND a.attnum = k
           ) = ARRAY['name']
     LIMIT 1;
    IF cname IS NOT NULL THEN
        EXECUTE format('ALTER TABLE people DROP CONSTRAINT %I', cname);
    END IF;
END $$;

-- Name lookups are still common (the directory searches by name), and the
-- dropped constraint took its implicit index with it.
CREATE INDEX IF NOT EXISTS idx_gtd_people_name_lower ON people (lower(name));

-- ── 4. The status vocabulary ────────────────────────────────────────────────
-- Migration 49 documented `'active' | 'inactive' | …` — the "…" is the problem.
-- Known legacy spellings are mapped; anything else is left alone rather than
-- rewritten, because silently reclassifying a status nobody anticipated is a
-- data loss disguised as a cleanup.
UPDATE people SET status = 'active'
 WHERE status IS NULL OR btrim(status) = '';
UPDATE people SET status = 'alumni'
 WHERE lower(btrim(status)) IN ('inactive', 'former', 'left');
UPDATE people SET status = lower(btrim(status))
 WHERE status <> lower(btrim(status));

-- Added NOT VALID, then validated separately. NOT VALID enforces every future
-- INSERT and UPDATE while tolerating a pre-existing row outside the vocabulary,
-- so an unanticipated legacy value cannot stop the deploy. If the data is clean
-- the VALIDATE below promotes it to a fully checked constraint in the same run.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint c
          JOIN pg_class t ON t.oid = c.conrelid
         WHERE t.relname = 'people' AND c.conname = 'gtd_people_status_check'
    ) THEN
        ALTER TABLE people
          ADD CONSTRAINT gtd_people_status_check
          CHECK (status IN ('active', 'contractor', 'alumni', 'invited')) NOT VALID;
    END IF;
END $$;

DO $$
BEGIN
    ALTER TABLE people VALIDATE CONSTRAINT gtd_people_status_check;
EXCEPTION WHEN check_violation THEN
    -- Enforced for every new write; existing offenders stay readable and are
    -- listed by the directory's own data-quality panel rather than by a failed
    -- deploy at 3am.
    RAISE NOTICE 'gtd_people_status_check left NOT VALID: existing rows carry a status outside the vocabulary';
END $$;
