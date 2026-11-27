-- ============================================================================
-- 463_studio_skill_share.sql - the share of a skill with the org (WS-STU J2c,
-- WS-TOF TOF-4a, Metorite task 3421).
--
-- What: seven nullable columns on `studio_skills`, the skill row of 320.
--       `shared_version` is the ONE version that the members of the org
--       load, after a holder of `studio:publish` approved it. The other six
--       hold the newest ask and its decision: `share_state` (pending,
--       approved or rejected), `share_version` (the version that the owner
--       asked for), `share_requested_by`, `share_requested_at`,
--       `share_decided_by` and `share_decided_at`.
--       gateway/studio/skills_store.py is the one writer of each of them.
-- Why:  ai_studio.md section 14, row J2c. A skill is private by default. It
--       becomes `org` only after an approval, and a later version of the
--       owner reaches no other member until a holder approves that version.
--       `skills_store.list_loadable` reads `shared_version` for each member
--       who is not the owner. The owner still loads the newest version.
-- Depends on: 320_studio_skills.sql (the table, its RLS and its policy).
--
-- THE LIFE OF A SHARE. The owner asks for one version: `share_state`
-- becomes 'pending', with the version, the owner and the time. A holder who
-- is not the owner approves: `share_state` becomes 'approved',
-- `visibility` becomes 'org' and `shared_version` takes the asked version.
-- A reject sets 'rejected' and changes no visibility, so an older approved
-- version stays shared. An unshare by the owner or by a holder sets
-- `visibility` to 'private' and `shared_version` to NULL. It clears a
-- pending or an approved ask, and it keeps a rejected one. No statement
-- sets 'system': the CHECK `studio_skills_visibility` of 320 holds it out,
-- and only a reviewed migration can change that CHECK.
--
-- THE CHECKS. `shared_version` is NULL, or a version of 1 or more of a skill
-- with `visibility` 'org', and at most `current_version`. `share_state` is
-- NULL or one of the three states, and a state names its version.
-- `studio_skill_versions` holds no new column: a version stays immutable.
--
-- Expand only (R6): seven nullable columns with no default, four CHECKs and
-- one partial index. No rename, no drop, no data, and the old code never
-- names a new column. The old INSERT and UPDATE statements of 320 leave each
-- new column NULL, and NULL passes each CHECK. Idempotent: ADD COLUMN IF NOT
-- EXISTS, CREATE INDEX IF NOT EXISTS, and each constraint in a DO block
-- guarded on pg_constraint. An ADD CONSTRAINT takes an ACCESS EXCLUSIVE lock
-- until COMMIT, so the file sets `lock_timeout` first, as 450 and 452 do.
-- The tenancy is the table's own (320, FORCE ROW LEVEL SECURITY). This file
-- adds no table, so the generated phases do not change, as for 452.
--
-- The number is taken at build time (R1): 463 was booked for this task on
-- 2026-11-18, and the branch held 459 as its highest file. Check it again at
-- merge. Pinned by tests/unit/test_studio_skill_share_r8.py, which finds this
-- file by CONTENT, never by number (R1).
-- ============================================================================

BEGIN;

SET LOCAL lock_timeout = '5s';

ALTER TABLE studio_skills
    ADD COLUMN IF NOT EXISTS shared_version     INTEGER,
    ADD COLUMN IF NOT EXISTS share_state        TEXT,
    ADD COLUMN IF NOT EXISTS share_version      INTEGER,
    ADD COLUMN IF NOT EXISTS share_requested_by TEXT,
    ADD COLUMN IF NOT EXISTS share_requested_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS share_decided_by   TEXT,
    ADD COLUMN IF NOT EXISTS share_decided_at   TIMESTAMPTZ;

DO $j2c$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = 'studio_skills'::regclass
           AND conname = 'studio_skills_shared_version'
    ) THEN
        ALTER TABLE studio_skills ADD CONSTRAINT studio_skills_shared_version CHECK (
            shared_version IS NULL OR (
                shared_version >= 1 AND visibility = 'org'
                AND current_version IS NOT NULL AND shared_version <= current_version));
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = 'studio_skills'::regclass
           AND conname = 'studio_skills_share_state'
    ) THEN
        ALTER TABLE studio_skills ADD CONSTRAINT studio_skills_share_state CHECK (
            share_state IS NULL OR (
                share_state IN ('pending', 'approved', 'rejected')
                AND share_version IS NOT NULL));
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = 'studio_skills'::regclass
           AND conname = 'studio_skills_share_version'
    ) THEN
        ALTER TABLE studio_skills ADD CONSTRAINT studio_skills_share_version CHECK (
            share_version IS NULL OR share_version >= 1);
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = 'studio_skills'::regclass
           AND conname = 'studio_skills_share_people'
    ) THEN
        ALTER TABLE studio_skills ADD CONSTRAINT studio_skills_share_people CHECK (
            (share_requested_by IS NULL OR (
                share_requested_by <> '' AND share_requested_by = lower(share_requested_by)))
            AND (share_decided_by IS NULL OR (
                share_decided_by <> '' AND share_decided_by = lower(share_decided_by))));
    END IF;
END
$j2c$;

-- The list of a holder reads the pending asks of one org.
CREATE INDEX IF NOT EXISTS studio_skills_share_pending_idx
    ON studio_skills (organization_id, share_requested_at)
    WHERE share_state = 'pending';

COMMENT ON COLUMN studio_skills.shared_version IS
    'The one version that the other members of the org load (WS-STU J2c). '
    'Only gateway/studio/skills_store.py writes it, after an approval.';

COMMIT;
