-- ============================================================================
-- 233_people_status_follows_member.sql — an activated member is ACTIVE in the
-- directory, not stuck at `invited`.
--
-- What: `people_row_from_member` (migrations 206 and 209) now also brings an
--       EXISTING directory row up to date when its member changes:
--         1. `invited` becomes `active` when the member becomes `active`.
--         2. A placeholder name (the address, or its local part) becomes the
--            member's display name, when the member has one.
--       A one-time backfill repairs the rows that already drifted.
-- Why:  the ClickUp import (owner report, 2026-10-08) found 1 of 51 people.
--       Its directory read (`imports.DIRECTORY_SQL`) takes only
--       `people.status = 'active'`, and every Fracktal colleague except the
--       founder was still `invited` there, though Organisation showed them
--       active. Production held 20 such rows across tenants on 2026-10-08.
--
-- ⚠️ **THE DEFECT WAS `ON CONFLICT DO NOTHING`, and it was silent.** An
--   invite creates the row as `invited`. Activation fires the trigger again,
--   the INSERT meets the row that exists, and does nothing. 206's own comment
--   said "a member who is invited first and activated later still arrives in
--   the directory". The member arrived, and stayed `invited` for good.
--
-- **What this does NOT change, on purpose.**
--   * Only `invited -> active`. A `contractor` or `alumni` row is somebody's
--     decision in People, and a member write never overrides it.
--   * A suspended or removed member still changes nothing. That is D63's
--     seal-don't-inherit question (H-49), and 206 explains why it is open.
--   * A name somebody chose is never rewritten. The name changes only while
--     it is still a placeholder the trigger itself wrote:
--     `test_a_second_update_does_not_duplicate_or_overwrite` still holds.
--
-- **The tenant.** When `people.organization_id` exists, the UPDATE matches
--   the member's own organization, never `app.tenant_id`, for the reason 206
--   gives. Migration 209 made the address unique per tenant, so the same
--   address at another customer is a different row and is not touched.
--
-- ⚠️ **It still fails open.** Anything unexpected is a WARNING, and the
--   member write succeeds. Re-running this file repairs what was missed.
--
-- ⚠️ **RE-RUN THIS FILE, NOT 206 OR 209.** 206's header says to re-run 206
--   after the tenancy layer is promoted. 206 and 209 each replace this same
--   function with the INSERT-only version, so re-running either one silently
--   brings the defect back. If you re-run one of them, re-run this file after
--   it. 206 is not edited to say so: a changed checksum makes the next deploy
--   re-apply all of 206.
--
-- Depends on: 206_people_from_membership.sql, 209_people_email_unique_per_tenant.sql.
-- Idempotent: CREATE OR REPLACE FUNCTION, and a backfill whose predicates
--             match nothing on a second run.
-- Pinned by tests/unit/test_people_status_follows_member.py.
-- ============================================================================

BEGIN;

-- ── 1. The trigger function ─────────────────────────────────────────────────
--
-- The INSERT half is 209's, byte for byte. The UPDATE half is new. Both build
-- their column list per call, for the reason 206 section 1 gives (H-104).

CREATE OR REPLACE FUNCTION people_row_from_member() RETURNS trigger
LANGUAGE plpgsql AS $body$
DECLARE
    has_org_col boolean;
    stmt        text;
    addr        text;
    display     text;
BEGIN
    IF NEW.status NOT IN ('active', 'invited') THEN
        RETURN NEW;
    END IF;
    IF NEW.email IS NULL OR btrim(NEW.email) = '' THEN
        RETURN NEW;
    END IF;

    addr    := lower(btrim(NEW.email));
    display := NULLIF(btrim(NEW.display_name), '');

    SELECT EXISTS (
        SELECT 1 FROM pg_attribute
         WHERE attrelid = 'people'::regclass
           AND attname = 'organization_id'
           AND NOT attisdropped
    ) INTO has_org_col;

    stmt := format($sql$
        INSERT INTO people
            (id, name, email, status, skills, source, source_key,
             updated_by, updated_at%1$s)
        VALUES
            (gen_random_uuid(), $1, $2, $3, ARRAY[]::text[], 'member',
             'member:' || $2, 'member-trigger', now()%2$s)
        ON CONFLICT DO NOTHING
    $sql$,
        CASE WHEN has_org_col THEN ', organization_id' ELSE '' END,
        CASE WHEN has_org_col THEN ', $4' ELSE '' END);

    BEGIN
        EXECUTE stmt USING
            COALESCE(display, split_part(addr, '@', 1)),
            addr,
            CASE WHEN NEW.status = 'invited' THEN 'invited' ELSE 'active' END,
            NEW.organization_id;
    EXCEPTION WHEN OTHERS THEN
        RAISE WARNING
            'people_row_from_member: no directory row for % (%): %',
            NEW.email, SQLSTATE, SQLERRM;
    END;

    -- The row existed already, so the INSERT above did nothing. Bring the
    -- two facts the member owns up to date, and nothing else.
    --   $1 the member's status   $2 the address
    --   $3 the display name      $4 the member's organization
    stmt := format($sql$
        UPDATE people
           SET status = CASE WHEN $1 = 'active' AND status = 'invited'
                             THEN 'active' ELSE status END,
               name   = CASE WHEN $3 IS NOT NULL
                              AND name IN ($2, split_part($2, '@', 1))
                             THEN $3 ELSE name END,
               updated_by = 'member-trigger',
               updated_at = now()
         WHERE lower(email) = $2%1$s
           AND (   ($1 = 'active' AND status = 'invited')
                OR ($3 IS NOT NULL AND name IN ($2, split_part($2, '@', 1))
                    AND name IS DISTINCT FROM $3))
    $sql$,
        CASE WHEN has_org_col THEN ' AND organization_id = $4' ELSE '' END);

    BEGIN
        EXECUTE stmt USING NEW.status, addr, display, NEW.organization_id;
    EXCEPTION WHEN OTHERS THEN
        RAISE WARNING
            'people_row_from_member: directory row for % not updated (%): %',
            NEW.email, SQLSTATE, SQLERRM;
    END;

    RETURN NEW;
END
$body$;


-- ── 2. The rows that already drifted ────────────────────────────────────────
--
-- Row level security is suspended for this block and restored exactly as
-- found, for the reasons 206 section 2 gives: a backfill crosses every tenant
-- from a connection that binds none, and under FORCE RLS it would match zero
-- rows SILENTLY.

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
         WHERE table_name = 'people' AND column_name = 'organization_id'
    ) INTO has_org_col;

    SELECT relrowsecurity, relforcerowsecurity
      INTO people_rls, people_force
      FROM pg_class WHERE oid = 'people'::regclass;
    SELECT relrowsecurity, relforcerowsecurity
      INTO users_rls, users_force
      FROM pg_class WHERE oid = 'app_user'::regclass;

    IF people_rls THEN ALTER TABLE people   DISABLE ROW LEVEL SECURITY; END IF;
    IF users_rls  THEN ALTER TABLE app_user DISABLE ROW LEVEL SECURITY; END IF;

    EXECUTE format($sql$
        UPDATE people p
           SET status = CASE WHEN u.status = 'active' AND p.status = 'invited'
                             THEN 'active' ELSE p.status END,
               name   = CASE WHEN NULLIF(btrim(u.display_name), '') IS NOT NULL
                              AND p.name IN (lower(btrim(u.email)),
                                             split_part(lower(btrim(u.email)), '@', 1))
                             THEN btrim(u.display_name) ELSE p.name END,
               updated_by = 'member-backfill',
               updated_at = now()
          FROM app_user u
         WHERE lower(p.email) = lower(btrim(u.email))%1$s
           AND u.status IN ('active', 'invited')
           AND (   (u.status = 'active' AND p.status = 'invited')
                OR (NULLIF(btrim(u.display_name), '') IS NOT NULL
                    AND p.name IN (lower(btrim(u.email)),
                                   split_part(lower(btrim(u.email)), '@', 1))
                    AND p.name IS DISTINCT FROM btrim(u.display_name)))
    $sql$,
        CASE WHEN has_org_col
             THEN ' AND p.organization_id = u.organization_id' ELSE '' END);

    GET DIAGNOSTICS made = ROW_COUNT;

    IF people_rls   THEN ALTER TABLE people   ENABLE ROW LEVEL SECURITY; END IF;
    IF people_force THEN ALTER TABLE people   FORCE  ROW LEVEL SECURITY; END IF;
    IF users_rls    THEN ALTER TABLE app_user ENABLE ROW LEVEL SECURITY; END IF;
    IF users_force  THEN ALTER TABLE app_user FORCE  ROW LEVEL SECURITY; END IF;

    RAISE NOTICE
        '233: % directory row(s) brought up to date from membership (organization_id column: %)',
        made, has_org_col;
END
$backfill$;

COMMIT;
