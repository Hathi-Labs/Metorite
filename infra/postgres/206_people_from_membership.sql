-- ============================================================================
-- 206_people_from_membership.sql — a member of the organization IS in the
-- directory. Always, and with nobody pressing anything.
--
-- What: a trigger on `app_user` that gives every active or invited member a
--       `gtd_people` row, plus a one-time backfill for the members who
--       already exist.
-- Why:  owner directive, 2026-09-20 — *"shouldn't it automatically sync
--       because we already have people in our organization? Why do I have to
--       explicitly sync?"* The answer is that they should not.
--
-- ⚠️ **THIS REPLACES A BUTTON, AND THE BUTTON WAS THE DEFECT.** PR #326 added
--   `POST /people/sync-members` and a "Sync members" control. It worked, and
--   it was the wrong shape: it made an INVARIANT — *a member is in the
--   directory* — into a chore somebody has to remember. An invariant a human
--   maintains by hand is an invariant that is false most of the time, and
--   silently: nothing anywhere said the directory had drifted. The endpoint
--   and the control are deleted in the same change as this migration.
--
-- **Why a trigger and not application code.** There are FIVE writers of
--   `app_user`: `admin/_common.provision_member` (invite AND access-request
--   approval), `acb_auth.access._BOOTSTRAP_OWNER_SQL` (the founder), and the
--   org-provisioning functions in migrations 179, 180 and 201. PR #306 taught
--   two of them to write the directory row and the other three still do not.
--   A rule enforced at five call sites is a rule with five chances to be
--   forgotten, and the sixth writer — somebody running SQL on the box during
--   an incident — can never be taught at all. In the database it cannot be
--   bypassed. `pm_organization_from_parent` (migration 161) is the precedent.
--
-- **`app_user` holds humans.** A service identity authenticates with a token
--   and has no row here (`acb_auth/deps.py`), so this trigger cannot
--   manufacture a directory entry for a machine. `people_center_app.md` §2's
--   other half still holds too: a contractor may have a directory row with no
--   login, and this touches none of them.
--
-- ⚠️ **Suspended and removed members get NO row**, and an existing row is
--   never removed or rewritten. The directory's status vocabulary (migration
--   148: active/contractor/alumni/invited) and `app_user.status`
--   (invited/active/suspended/removed) are different tuples, and only two
--   values map. Off-boarding is D63's seal-don't-inherit question (H-49) and
--   is NOT settled here — writing a guess would put a former colleague back
--   into the assignee picker.
--
-- ⚠️ **The trigger reads the schema at RUN time, so promoting the tenancy
--   layer cannot break it.** `gtd_people.organization_id` comes from
--   `infra/postgres/generated/`, which `apply_migrations.sh` does not replay
--   (H-104). See section 1 for what the first version of this file got wrong
--   and how a real database caught it.
--
-- 📌 **After promoting the tenancy layer, re-run this file** — not for the
--   trigger, which needs nothing, but for the backfill: rows created while
--   the column was absent carry no `organization_id`. Re-running is free.
--   Everything here is idempotent.
--
-- ⚠️ **`source_key` is keyed on the ADDRESS, not the name.** Migration 148
--   backfills that column as `<source>:<lower(name)>`, so two members who
--   share a name collide on its unique index and abort the ladder replay. The
--   address is what is actually unique for this source. Found on a real
--   database (PR #306) and invisible to any fake.
--
-- Depends on: 49_gtd_people.sql, 130_org_access_control.sql (app_user),
--             148_people_key_shape.sql (the lower(email) unique index and the
--             status CHECK), 176_people_skills.sql.
-- Idempotent: CREATE OR REPLACE FUNCTION/TRIGGER, and a backfill whose
--             NOT EXISTS makes the second run match nothing.
-- Pinned by tests/unit/test_people_from_membership.py.
-- ============================================================================

BEGIN;

-- ── 1. The trigger function ─────────────────────────────────────────────────
--
-- ⚠️ **THE COLUMN LIST IS BUILT AT RUN TIME, NOT AT MIGRATION TIME**, and the
-- first version of this file got that wrong in a way a real database caught.
--
-- `gtd_people.organization_id` comes from `infra/postgres/generated/`, which
-- `apply_migrations.sh` does not replay (H-104), so this file cannot know
-- whether the column is there. Building the statement once, around the schema
-- present on the day, produces a function that is correct until somebody
-- promotes the tenancy layer — and then names no `organization_id` against a
-- NOT NULL column, so EVERY member write fails. `test_h3_rls_promotion_
-- rehearsal.py` reproduced exactly that: 36 errors, org provisioning among
-- them. A caveat in a comment would not have been good enough, because the
-- person promoting the tenancy layer is not reading this file.
--
-- `EXECUTE` re-plans per call, so the shape is re-read after any schema
-- change. It costs one catalog lookup per member write, and member writes are
-- invites — rare, and not a hot path.
--
-- ⚠️ **IT ALSO FAILS OPEN.** A directory row is a convenience. Membership is
-- the product. A trigger that can refuse an `INSERT INTO app_user` can refuse
-- a SIGNUP, which is the worst outage this system has, and it would arrive
-- from a table nobody would think to look at. So anything unexpected becomes
-- a WARNING in the Postgres log and the member is still created. Re-running
-- this migration backfills whatever was missed — which is the whole reason
-- section 2 is written to be re-runnable.

CREATE OR REPLACE FUNCTION people_row_from_member() RETURNS trigger
LANGUAGE plpgsql AS $body$
DECLARE
    has_org_col boolean;
    stmt        text;
BEGIN
    -- Only the two statuses that map onto the directory's own vocabulary. A
    -- suspended or removed member is deliberately not a directory entry, and
    -- is never deleted from one either.
    IF NEW.status NOT IN ('active', 'invited') THEN
        RETURN NEW;
    END IF;
    IF NEW.email IS NULL OR btrim(NEW.email) = '' THEN
        RETURN NEW;
    END IF;

    SELECT EXISTS (
        SELECT 1 FROM pg_attribute
         WHERE attrelid = 'gtd_people'::regclass
           AND attname = 'organization_id'
           AND NOT attisdropped
    ) INTO has_org_col;

    stmt := format($sql$
        INSERT INTO gtd_people
            (id, name, email, status, skills, source, source_key,
             updated_by, updated_at%1$s)
        VALUES
            (gen_random_uuid(), $1, $2, $3, ARRAY[]::text[], 'member',
             'member:' || $2, 'member-trigger', now()%2$s)
        ON CONFLICT (lower(email)) WHERE email IS NOT NULL
        DO NOTHING
    $sql$,
        CASE WHEN has_org_col THEN ', organization_id' ELSE '' END,
        CASE WHEN has_org_col THEN ', $4' ELSE '' END);

    BEGIN
        EXECUTE stmt USING
            -- The address's local part is a poor name and a present one. The
            -- person renames themselves on their own profile, which is the
            -- surface that exists for it.
            COALESCE(NULLIF(btrim(NEW.display_name), ''),
                     split_part(lower(btrim(NEW.email)), '@', 1)),
            lower(btrim(NEW.email)),
            CASE WHEN NEW.status = 'invited' THEN 'invited' ELSE 'active' END,
            -- ⚠️ The MEMBER's organization, never
            -- `current_setting('app.tenant_id')`. The org-provisioning
            -- functions (migrations 179/180/201) insert the founder with no
            -- tenant GUC bound, so the column DEFAULT would resolve NULL and
            -- a NOT NULL or a FORCE RLS `WITH CHECK` would then refuse the
            -- row — turning this trigger into a failed signup.
            NEW.organization_id;
    EXCEPTION WHEN OTHERS THEN
        RAISE WARNING
            'people_row_from_member: no directory row for % (%): %',
            NEW.email, SQLSTATE, SQLERRM;
    END;

    RETURN NEW;
END
$body$;

-- AFTER, not BEFORE: the member row must exist before anything claims to
-- describe them, and an AFTER trigger cannot change what was written to
-- `app_user`. ON UPDATE as well as ON INSERT, so a member who is invited
-- first and activated later, or reinstated, still arrives in the directory.
--
-- `CREATE OR REPLACE TRIGGER` (Postgres 14+) is what makes this idempotent.
-- Plain `CREATE TRIGGER` has no `IF NOT EXISTS` and fails on the second
-- deploy — the deploy nobody watches.
CREATE OR REPLACE TRIGGER trg_app_user_directory_row
    AFTER INSERT OR UPDATE OF email, display_name, status, organization_id
    ON app_user
    FOR EACH ROW EXECUTE FUNCTION people_row_from_member();


-- ── 2. The members who already exist ────────────────────────────────────────
--
-- ⚠️ ROW LEVEL SECURITY IS SUSPENDED FOR THIS BLOCK, and restored exactly as
-- found. A backfill is the one operation that legitimately crosses every
-- tenant, and it runs from a connection that binds no `app.tenant_id` — so
-- under FORCE RLS the read would return zero rows and the write would be
-- refused, both SILENTLY. That is the "green job that shipped nothing"
-- failure CLAUDE.md §3 rule 8 names, and a backfill is exactly where it would
-- go unnoticed.
--
-- This is safe because it is inside the migration's transaction: ALTER TABLE
-- takes an ACCESS EXCLUSIVE lock, so no other session reads these tables
-- while the policy is off, and nothing is visible until COMMIT.
--
-- ENABLE and FORCE are captured and restored SEPARATELY. Re-enabling RLS does
-- not restore FORCE, and losing FORCE would silently let the table owner read
-- every tenant — a hole that would look exactly like nothing.

DO $backfill$
DECLARE
    has_org_col  boolean;
    people_rls   boolean := false;
    people_force boolean := false;
    users_rls    boolean := false;
    users_force  boolean := false;
    made         bigint  := 0;
BEGIN
    SELECT EXISTS (
        SELECT 1 FROM information_schema.columns
         WHERE table_name = 'gtd_people' AND column_name = 'organization_id'
    ) INTO has_org_col;

    SELECT relrowsecurity, relforcerowsecurity
      INTO people_rls, people_force
      FROM pg_class WHERE oid = 'gtd_people'::regclass;
    SELECT relrowsecurity, relforcerowsecurity
      INTO users_rls, users_force
      FROM pg_class WHERE oid = 'app_user'::regclass;

    IF people_rls THEN ALTER TABLE gtd_people DISABLE ROW LEVEL SECURITY; END IF;
    IF users_rls  THEN ALTER TABLE app_user  DISABLE ROW LEVEL SECURITY; END IF;

    IF has_org_col THEN
        INSERT INTO gtd_people
            (id, name, email, status, skills, source, source_key,
             organization_id, updated_by, updated_at)
        SELECT gen_random_uuid(),
               COALESCE(NULLIF(btrim(u.display_name), ''),
                        split_part(lower(btrim(u.email)), '@', 1)),
               lower(btrim(u.email)),
               CASE WHEN u.status = 'invited' THEN 'invited' ELSE 'active' END,
               ARRAY[]::text[], 'member',
               'member:' || lower(btrim(u.email)),
               u.organization_id, 'member-backfill', now()
          FROM app_user u
         WHERE u.status IN ('active', 'invited')
           AND u.email IS NOT NULL AND btrim(u.email) <> ''
           AND NOT EXISTS (SELECT 1 FROM gtd_people p
                            WHERE lower(p.email) = lower(btrim(u.email)))
        ON CONFLICT (lower(email)) WHERE email IS NOT NULL DO NOTHING;
    ELSE
        INSERT INTO gtd_people
            (id, name, email, status, skills, source, source_key,
             updated_by, updated_at)
        SELECT gen_random_uuid(),
               COALESCE(NULLIF(btrim(u.display_name), ''),
                        split_part(lower(btrim(u.email)), '@', 1)),
               lower(btrim(u.email)),
               CASE WHEN u.status = 'invited' THEN 'invited' ELSE 'active' END,
               ARRAY[]::text[], 'member',
               'member:' || lower(btrim(u.email)),
               'member-backfill', now()
          FROM app_user u
         WHERE u.status IN ('active', 'invited')
           AND u.email IS NOT NULL AND btrim(u.email) <> ''
           AND NOT EXISTS (SELECT 1 FROM gtd_people p
                            WHERE lower(p.email) = lower(btrim(u.email)))
        ON CONFLICT (lower(email)) WHERE email IS NOT NULL DO NOTHING;
    END IF;

    GET DIAGNOSTICS made = ROW_COUNT;

    IF people_rls   THEN ALTER TABLE gtd_people ENABLE ROW LEVEL SECURITY; END IF;
    IF people_force THEN ALTER TABLE gtd_people FORCE  ROW LEVEL SECURITY; END IF;
    IF users_rls    THEN ALTER TABLE app_user  ENABLE ROW LEVEL SECURITY; END IF;
    IF users_force  THEN ALTER TABLE app_user  FORCE  ROW LEVEL SECURITY; END IF;

    -- Said out loud. A backfill that reports nothing is a backfill nobody can
    -- tell apart from one that did nothing.
    RAISE NOTICE
        '206: % directory row(s) created from membership (organization_id column: %)',
        made, has_org_col;
END
$backfill$;

COMMIT;
