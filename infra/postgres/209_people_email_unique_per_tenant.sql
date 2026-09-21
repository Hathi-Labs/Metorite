-- ============================================================================
-- 209_people_email_unique_per_tenant.sql — one address may work for two
-- customers.
--
-- What: `gtd_people.organization_id` on the NUMBERED ladder, backfilled, and
--       migration 148's global `lower(email)` unique index replaced with a
--       per-tenant one.
-- Why:  H-125. Two organizations cannot currently hold the same address.
--
-- **The bug, and it is silent.** Migration 148 added
--   `uq_gtd_people_email_lower ON gtd_people (lower(email))`
-- with no tenant in it. `people_row_from_member` (migration 206) writes
-- `ON CONFLICT DO NOTHING`, so when customer B invites somebody who already
-- has a directory row at customer A, **the write is skipped and nothing is
-- said**. Row level security then hides A's row, so B's administrator sees a
-- member with no directory row, no error, and no way to find out why.
--
-- A contractor who works for two customers is the ordinary case this product
-- is sold for. It is the same case `people_center_app.md` §2 names when it
-- explains why the directory exists at all.
--
-- ⚠️ **THIS TAKES `gtd_people.organization_id` OFF THE H-104 CRITICAL PATH.**
-- The column is declared by `infra/postgres/generated/01_add_columns.sql`,
-- which `apply_migrations.sh` does not replay — so production may not have
-- it, and H-125 was ordered behind H-104 for exactly that reason. Waiting is
-- the wrong trade: H-104 is a whole-schema question about 143 tables, and
-- this is one column on one table that a live defect needs now.
--
-- Declared here with the same type and default the generated file uses,
-- plus the `ON DELETE CASCADE` that `test_org_purge_tenant` requires and
-- the generated file omits. `ADD COLUMN IF NOT EXISTS` means promoting that
-- phase later is still a no-op here. One column joins the ladder. The other
-- 142 tables are still H-104's.
--
-- ⚠️ **The index is REPLACED, not added beside.** Leaving the global one
-- would keep refusing B's row while the new one permitted it — the stricter
-- constraint always wins, so a second index would look like a fix and change
-- nothing. Dropping first is safe in the other direction: per-tenant is
-- weaker than global, so every row that satisfies the old one satisfies the
-- new one.
--
-- 📌 **The backfill is conservative and says what it could not do.** A row is
-- matched to a tenant through `app_user` on the lowered address. A directory
-- row with no member — a contractor — has no such link, so it is left to the
-- single-organization case and otherwise reported and skipped. A guess there
-- would put a real person in the wrong customer's directory.
--
-- Depends on: 49_gtd_people.sql, 130_org_access_control.sql (app_user,
--             organization), 148_people_key_shape.sql (the index replaced
--             here), 206_people_from_membership.sql (the trigger that starts
--             filling the column once it exists).
-- Idempotent: ADD COLUMN IF NOT EXISTS, DROP INDEX IF EXISTS, CREATE UNIQUE
--             INDEX IF NOT EXISTS, and a backfill whose WHERE empties.
-- Pinned by tests/unit/test_people_email_per_tenant.py.
-- ============================================================================

BEGIN;

-- ── 1. The column, exactly as the generated phase declares it ───────────────
--
-- Same type and same default, so promoting `generated/01_add_columns.sql`
-- later finds nothing to do here. The default reads the bound tenant, which
-- is what makes an ordinary INSERT inside a request land in the right
-- organization without naming the column.

-- ⚠️ `REFERENCES` precedes `DEFAULT`, and the order is load-bearing: the
-- tenancy ratchet matches the two with no comma between them, and
-- `current_setting('app.tenant_id', true)` has one. Migration 174's header
-- records the same trap.
--
-- ⚠️ **ON DELETE CASCADE, which the generated file does NOT declare.**
-- `test_org_purge_tenant` requires it and is right to: without the cascade,
-- purging a customer leaves their whole directory behind as orphan rows
-- carrying names, addresses and résumé summaries. This is therefore NOT
-- byte-identical to `generated/01_add_columns.sql` — it is that column plus
-- the constraint the purge path needs, and `ADD COLUMN IF NOT EXISTS` means
-- promoting the generated phase later still finds nothing to do.
ALTER TABLE gtd_people
    ADD COLUMN IF NOT EXISTS organization_id UUID
    REFERENCES organization (id) ON DELETE CASCADE
    DEFAULT current_setting('app.tenant_id', true)::uuid;


-- ── 2. Who each existing row belongs to ─────────────────────────────────────

DO $backfill$
DECLARE
    by_member bigint;
    orphans   bigint;
    only_org  uuid;
    adopted   bigint := 0;
BEGIN
    -- (a) The rows that have a member. `app_user` is the authority on which
    --     tenant an address belongs to (D-MT-1 (a)), so this is a lookup and
    --     not a guess.
    UPDATE gtd_people p
       SET organization_id = u.organization_id
      FROM app_user u
     WHERE p.organization_id IS NULL
       AND p.email IS NOT NULL
       AND lower(btrim(p.email)) = lower(btrim(u.email))
       AND u.organization_id IS NOT NULL;
    GET DIAGNOSTICS by_member = ROW_COUNT;

    SELECT count(*) INTO orphans
      FROM gtd_people WHERE organization_id IS NULL;

    -- (b) A directory row with no member — a contractor, or a row that
    --     predates membership. There is no link to follow, so the only safe
    --     answer is the one a single-organization deployment makes obvious.
    IF orphans > 0 AND (SELECT count(*) FROM organization) = 1 THEN
        SELECT id INTO only_org FROM organization;
        UPDATE gtd_people SET organization_id = only_org
         WHERE organization_id IS NULL;
        GET DIAGNOSTICS adopted = ROW_COUNT;
        orphans := 0;
    END IF;

    RAISE NOTICE
        '209: % row(s) tenanted from app_user, % adopted by the only '
        'organization, % left NULL',
        by_member, adopted, orphans;

    IF orphans > 0 THEN
        -- Said loudly rather than guessed. A NULL here is not fatal: the
        -- partial index below simply does not constrain those rows, and an
        -- administrator can set the owner from the person page.
        RAISE WARNING
            '209: % directory row(s) could not be matched to an organization '
            'and keep organization_id NULL. They are rows with no member. '
            'Set them by hand, or they stay visible to no tenant once row '
            'level security is enforced.', orphans;
    END IF;
END
$backfill$;


-- ── 3. The index, per tenant ────────────────────────────────────────────────
--
-- Dropped and recreated rather than added beside: two unique indexes mean the
-- stricter one decides, so leaving 148's global index in place would refuse
-- the second customer's row exactly as before while a new index sat next to
-- it looking like a fix.

DROP INDEX IF EXISTS uq_gtd_people_email_lower;

CREATE UNIQUE INDEX IF NOT EXISTS uq_gtd_people_org_email_lower
    ON gtd_people (organization_id, lower(email))
    WHERE email IS NOT NULL;

COMMENT ON INDEX uq_gtd_people_org_email_lower IS
    'H-125: an address is unique WITHIN an organization. Two customers may '
    'each hold a row for the same contractor. Replaced migration 148''s '
    'uq_gtd_people_email_lower, which was global and silently refused the '
    'second one.';


-- ── 4. The trigger, restated so it names no index ───────────────────────────
--
-- Migration 206 defined `people_row_from_member` with
-- `ON CONFLICT (lower(email))`, which pins the statement to the index this
-- file just dropped. A database that already ran 206 holds that definition,
-- so it must be replaced here or the next invite raises 42P10 at PLAN time —
-- the failure migration 162 caused for `app_user`, where every invite and
-- every sign-in approval broke at once.
--
-- 206's own text is corrected too, for a fresh install. This restatement is
-- for the databases where it has already run.
--
-- Byte-identical to 206's function except for the conflict clause. See that
-- file for why the column list is built per call and why it fails open.

CREATE OR REPLACE FUNCTION people_row_from_member() RETURNS trigger
LANGUAGE plpgsql AS $body$
DECLARE
    has_org_col boolean;
    stmt        text;
BEGIN
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
        ON CONFLICT DO NOTHING
    $sql$,
        CASE WHEN has_org_col THEN ', organization_id' ELSE '' END,
        CASE WHEN has_org_col THEN ', $4' ELSE '' END);

    BEGIN
        EXECUTE stmt USING
            COALESCE(NULLIF(btrim(NEW.display_name), ''),
                     split_part(lower(btrim(NEW.email)), '@', 1)),
            lower(btrim(NEW.email)),
            CASE WHEN NEW.status = 'invited' THEN 'invited' ELSE 'active' END,
            NEW.organization_id;
    EXCEPTION WHEN OTHERS THEN
        RAISE WARNING
            'people_row_from_member: no directory row for % (%): %',
            NEW.email, SQLSTATE, SQLERRM;
    END;

    RETURN NEW;
END
$body$;

COMMIT;
