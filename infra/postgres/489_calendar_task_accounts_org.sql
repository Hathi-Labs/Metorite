-- ============================================================================
-- 489_calendar_task_accounts_org.sql - the tenant column of three member
-- tables of the task routes, on the numbered ladder. Expand only (H-312b).
-- ============================================================================
-- Spec: HANDOFF H-312 · Metorite task 1646 · R1 · R5 · R6 · R8.
-- R1: origin/main held 475 as its highest file on 2026-11-22. Task 1646
-- booked 489. Check the number again at merge.
--
-- What: `organization_id` on `calendar_day_state`, `calendar_rollover_log`
--       and `task_accounts`, only where it is missing, and a backfill of
--       each row that has no organization.
--
-- **The fault.** Ten statements in `gateway/routes/tasks` named the member
-- only (`WHERE user_id = :uid`). Under the owner role RLS does not apply, so
-- a member of two organizations read and changed the rows of the other
-- organization too. The routes now also name the bound organization
-- (`settings._BOUND_ORG`). The numbered ladder never replays `generated/`
-- (H-104), so a fresh database had no column for them to name. This file
-- gives it one, as 209, 223 and 354 did for one table each.
--
-- **Two starting points, one file** (the same as 223 and 354).
--   * Production: `generated/01..04` are applied. The column exists, it is
--     NOT NULL, it has its foreign key, and RLS is forced. `ADD COLUMN IF NOT
--     EXISTS` then skips the whole column clause, the inline REFERENCES too,
--     so no second foreign key appears. The backfill finds no NULL row.
--   * A fresh ladder database: no tenancy phase ran. The column is created
--     here, with the cascade that `test_org_purge_tenant` requires.
--
-- **Expand only (R6).** The column is nullable. No NOT NULL, no key change,
-- no policy, no index and no drop. The old code names no organization, so it
-- runs on this schema as before. The default reads the bound tenant, so an
-- old INSERT inside a request still lands in the right organization.
--
-- **The backfill is conservative.** `app_user` is the authority on the
-- organization of an address (D-MT-1 (a)). `user_id` holds the address of
-- the member in all three tables. An address that `app_user` holds in
-- exactly one organization gives its rows to that organization. With one
-- organization in the database, each other row goes to it. With more than
-- one, a row that no member matches stays NULL, and a WARNING names the
-- count. A guess there could put a member's row in the wrong customer. The
-- file names no slug (the default-slug ratchet in test_org_provisioning.py).
--
-- ⚠️ **A NULL row is hidden from now on.** Each route now filters on the
-- bound organization, and NULL matches none. Such a row belongs to an
-- address that is no member of any organization, for example a member who
-- left. The owner accepted this on 2026-11-22 (task 1646).
--
-- ⚠️ **The key of `calendar_day_state` stays `(user_id, day)`.** So one
-- address holds one row for each day across all its organizations. In a
-- second organization, a save of a day that the first organization holds
-- changes nothing. The org filter keeps the write off the row of the first
-- organization, and the save answers the empty day. The re-key to
-- `(organization_id, user_id, day)` is a later contract step, not this file.
--
-- Depends on: 48_task_manager_gtd.sql (task_accounts),
--             78_gtd_calendar_rollover.sql (calendar_rollover_log),
--             92_gtd_day_state.sql (calendar_day_state),
--             130_org_access_control.sql (app_user, organization),
--             162_app_user_email_case.sql (one row for each address).
-- Idempotent: ADD COLUMN IF NOT EXISTS, and a backfill whose WHERE empties.
-- Pinned by tests/unit/test_task_routes_per_org_r8.py, which finds this file
-- by content, never by number (R1).
-- ============================================================================

SET lock_timeout = '5s';

BEGIN;

-- ── 1. The column, only where the tenancy phases did not add it ─────────────
--
-- Same type and default as `generated/01_add_columns.sql`, plus the cascade
-- that the purge path needs. `REFERENCES` precedes `DEFAULT`, because the
-- tenancy ratchet matches the two with no comma between them (see 209).

ALTER TABLE calendar_day_state
    ADD COLUMN IF NOT EXISTS organization_id UUID
    REFERENCES organization (id) ON DELETE CASCADE
    DEFAULT current_setting('app.tenant_id', true)::uuid;

ALTER TABLE calendar_rollover_log
    ADD COLUMN IF NOT EXISTS organization_id UUID
    REFERENCES organization (id) ON DELETE CASCADE
    DEFAULT current_setting('app.tenant_id', true)::uuid;

ALTER TABLE task_accounts
    ADD COLUMN IF NOT EXISTS organization_id UUID
    REFERENCES organization (id) ON DELETE CASCADE
    DEFAULT current_setting('app.tenant_id', true)::uuid;


-- ── 2. Who owns each existing row ───────────────────────────────────────────

DO $h312b_backfill$
DECLARE
    tbl       text;
    only_org  uuid;
    by_member bigint;
    adopted   bigint;
    orphans   bigint;
BEGIN
    -- One organization in the database makes the answer plain. With more,
    -- `only_org` stays NULL and no row is adopted.
    IF (SELECT count(*) FROM organization) = 1 THEN
        SELECT id INTO only_org FROM organization;
    END IF;

    FOREACH tbl IN ARRAY
        ARRAY['calendar_day_state', 'calendar_rollover_log', 'task_accounts']
    LOOP
        -- (a) The member's own organization. An address that `app_user`
        --     holds in two organizations matches nothing here, so the row
        --     falls to (b) or stays NULL.
        EXECUTE format($sql$
            UPDATE %I AS r
               SET organization_id = m.org
              FROM (SELECT lower(btrim(u.email)) AS addr,
                           (array_agg(DISTINCT u.organization_id))[1] AS org
                      FROM app_user u
                     WHERE u.email IS NOT NULL
                       AND u.organization_id IS NOT NULL
                     GROUP BY lower(btrim(u.email))
                    HAVING count(DISTINCT u.organization_id) = 1) AS m
             WHERE r.organization_id IS NULL
               AND r.user_id IS NOT NULL
               AND lower(btrim(r.user_id)) = m.addr
        $sql$, tbl);
        GET DIAGNOSTICS by_member = ROW_COUNT;

        -- (b) No member matches, and the database holds one organization.
        adopted := 0;
        IF only_org IS NOT NULL THEN
            EXECUTE format(
                'UPDATE %I SET organization_id = $1 WHERE organization_id IS NULL',
                tbl) USING only_org;
            GET DIAGNOSTICS adopted = ROW_COUNT;
        END IF;

        EXECUTE format('SELECT count(*) FROM %I WHERE organization_id IS NULL', tbl)
            INTO orphans;

        RAISE NOTICE
            '489: %: % row(s) tenanted from app_user, % adopted by the only '
            'organization, % left NULL', tbl, by_member, adopted, orphans;

        IF orphans > 0 THEN
            RAISE WARNING
                '489: % has % row(s) that match no member and keep '
                'organization_id NULL. The task routes filter on the bound '
                'organization, so no route reads them. Set them by hand.',
                tbl, orphans;
        END IF;
    END LOOP;
END
$h312b_backfill$;

COMMIT;
