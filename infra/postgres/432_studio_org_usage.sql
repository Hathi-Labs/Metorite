-- ============================================================================
-- 432_studio_org_usage.sql — the gauge of the Studio records of each org
-- (WS-STU C10a).
--
-- What: one new table, `studio_org_usage`, with one row for each org. It
--       holds `records`, the count of the rows of `studio_records` of the org,
--       and `bytes`, the sum of octet_length(data::text) of those rows. Three
--       statement triggers on `studio_records` keep the two counts exact, and
--       this file fills the table from the rows that exist.
-- Why:  ai_studio.md §14 C10a and §3.1. An org holds at most 1,000,000
--       records and 2 GiB of record JSON (acb_common/studio_fields.py,
--       MAX_RECORDS_PER_ORG and MAX_RECORD_BYTES_PER_ORG). The store reads
--       this row under the org lock, before and after each batch, and refuses
--       a batch that takes the org past a cap. A count(*) or a sum over the
--       records of the org at each write is too slow at 1,000,000 rows.
--       The gauge counts the JSON of a record only. A history row does not
--       count. That is an open owner decision (§14 C10b).
--       The gauge is no ledger and moves no credit, so D19.3 and D32.5 do not
--       apply.
-- Depends on: 323_studio_objects.sql (studio_records),
--             130_org_access_control.sql (organization).
--
-- Why triggers, and not the store. A trigger sees each writer: the store, the
-- purge of a package (A5c-1), the cascade of a package or an object delete,
-- raw SQL, and old code during the deploy window (R6). So the gauge stays
-- exact, and the store holds no second count.
--   * Each trigger is FOR EACH STATEMENT with a transition table, so a batch
--     or a purge of 500 rows makes one change to the gauge, not 500.
--   * Postgres lets a trigger with a transition table name one event only,
--     so there are three triggers: INSERT, UPDATE and DELETE.
--   * The INSERT trigger upserts, so the first record of an org makes its row.
--   * The DELETE trigger only UPDATEs. The delete of an org cascades to
--     `studio_records` and to this table, in an order that Postgres picks.
--     A plain UPDATE of a row that the cascade deleted first changes no row.
--     An upsert there would put the row back, and the delete of the org would
--     then fail on the foreign key.
--   * The UPDATE trigger only UPDATEs too. An update needs a record, and the
--     record made the row when it came in, or the backfill below made it.
--   * Each statement groups by `organization_id` of the transition rows, so
--     a statement that touches two orgs (only raw SQL as a superuser can)
--     moves each gauge by its own rows.
--   * The functions are SECURITY INVOKER. So FORCE RLS binds them as it binds
--     the statement that fired them. A writer that can write a record of an
--     org can write the gauge of that org, and no other.
--
-- Expand only (R6): one new table, its RLS, three trigger functions and three
-- triggers. No rename, no drop, no change to a column of another table. Old
-- code names no new object. The triggers add one small statement to each
-- statement that writes `studio_records`.
-- Idempotent per infra/postgres/README.md: CREATE TABLE IF NOT EXISTS,
-- CREATE OR REPLACE FUNCTION, DROP TRIGGER IF EXISTS then CREATE, the RLS
-- block is guarded on relforcerowsecurity, and the backfill sets each row to
-- the exact count again. A second run changes nothing.
--
-- The backfill. `LOCK TABLE studio_records IN SHARE MODE` comes first. It
-- lets a read run and makes a writer wait to the end of this transaction. So
-- no write comes between the count and the triggers. The file sets
-- lock_timeout, and the runner tries the whole file again on a lock timeout.
-- On production the table is small (C10a ships dark), so the count is fast.
-- TWO ARMS, on the role of the runner. The runner of the box is a superuser
-- and skips row security. Then one statement fills every org, and the file
-- binds no tenant. A runner that owns the table and is not a superuser meets
-- FORCE RLS. Then the file binds each org in turn, as 221 and 413 do, and the
-- bind is local to this transaction. ⚠️ A bind leaves `app.tenant_id` as ''
-- for the rest of the session. A later DEFAULT of the bound tenant in that
-- session then fails its cast to uuid. The test ladder runs every file and
-- the generated phases in one session, so the superuser arm binds nothing.
--
-- Tenant-scoped (R5): `organization_id` is the primary key, with the bound
-- tenant DEFAULT, ENABLE and FORCE row level security and one policy with
-- USING and WITH CHECK, in the shape of 323. `REFERENCES` comes before
-- `DEFAULT`, for the tenancy ratchet of tests/unit/test_tenancy_boundary.py.
-- The delete of an org takes its row by the cascade.
--
-- The number is taken at build time (R1). On 2026-11-13 origin/main held 427,
-- and merged PR 2158 took 430 after that. Open pull requests held 423, 424,
-- 428 and 429. PR 2209 (C4a-1) moved from 430 to 431. This file took 431
-- first, and moved to 432 the same day. Check it again at merge
-- (scripts/premerge.sh).
-- Pinned by tests/unit/test_studio_org_caps_r8.py and
-- tests/unit/test_studio_org_caps.py, which find this file by CONTENT, never
-- by number (R1).
-- ============================================================================

BEGIN;

SET LOCAL lock_timeout = '5s';

-- ── 1. The table ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS studio_org_usage (
    organization_id   UUID NOT NULL
                      REFERENCES organization (id) ON DELETE CASCADE
                      DEFAULT current_setting('app.tenant_id', true)::uuid,
    -- The count of the rows of studio_records of the org.
    records           BIGINT NOT NULL DEFAULT 0,
    -- The sum of octet_length(data::text) of those rows.
    bytes             BIGINT NOT NULL DEFAULT 0,
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (organization_id)
);

-- ── 2. Row level security ───────────────────────────────────────────────────
DO $rls$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_class
         WHERE oid = to_regclass('studio_org_usage')
           AND relrowsecurity
           AND relforcerowsecurity
    ) THEN
        ALTER TABLE studio_org_usage ENABLE ROW LEVEL SECURITY;
        ALTER TABLE studio_org_usage FORCE  ROW LEVEL SECURITY;
        DROP POLICY IF EXISTS studio_org_usage_tenant_isolation ON studio_org_usage;
        CREATE POLICY studio_org_usage_tenant_isolation ON studio_org_usage
            USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
            WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);
    END IF;
END
$rls$;

-- ── 3. The lock: no write between the count and the triggers ────────────────
LOCK TABLE studio_records IN SHARE MODE;

-- ── 4. The three triggers ───────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION studio_org_usage_insert() RETURNS trigger
LANGUAGE plpgsql AS $fn$
BEGIN
    INSERT INTO studio_org_usage AS u (organization_id, records, bytes)
    SELECT n.organization_id, count(*), sum(octet_length(n.data::text))
      FROM studio_org_usage_new n
     GROUP BY n.organization_id
    ON CONFLICT (organization_id) DO UPDATE
       SET records = u.records + EXCLUDED.records,
           bytes = u.bytes + EXCLUDED.bytes,
           updated_at = now();
    RETURN NULL;
END
$fn$;

CREATE OR REPLACE FUNCTION studio_org_usage_update() RETURNS trigger
LANGUAGE plpgsql AS $fn$
BEGIN
    UPDATE studio_org_usage u
       SET records = u.records + d.records,
           bytes = u.bytes + d.bytes,
           updated_at = now()
      FROM (
        SELECT x.organization_id, sum(x.records) AS records, sum(x.bytes) AS bytes
          FROM (
            SELECT n.organization_id, 1 AS records, octet_length(n.data::text) AS bytes
              FROM studio_org_usage_new n
            UNION ALL
            SELECT o.organization_id, -1, -octet_length(o.data::text)
              FROM studio_org_usage_old o
          ) x
         GROUP BY x.organization_id
      ) d
     WHERE u.organization_id = d.organization_id
       AND (d.records <> 0 OR d.bytes <> 0);
    RETURN NULL;
END
$fn$;

CREATE OR REPLACE FUNCTION studio_org_usage_delete() RETURNS trigger
LANGUAGE plpgsql AS $fn$
BEGIN
    UPDATE studio_org_usage u
       SET records = u.records - d.records,
           bytes = u.bytes - d.bytes,
           updated_at = now()
      FROM (
        SELECT o.organization_id, count(*) AS records,
               sum(octet_length(o.data::text)) AS bytes
          FROM studio_org_usage_old o
         GROUP BY o.organization_id
      ) d
     WHERE u.organization_id = d.organization_id;
    RETURN NULL;
END
$fn$;

DROP TRIGGER IF EXISTS studio_org_usage_insert ON studio_records;
CREATE TRIGGER studio_org_usage_insert
    AFTER INSERT ON studio_records
    REFERENCING NEW TABLE AS studio_org_usage_new
    FOR EACH STATEMENT EXECUTE FUNCTION studio_org_usage_insert();

DROP TRIGGER IF EXISTS studio_org_usage_update ON studio_records;
CREATE TRIGGER studio_org_usage_update
    AFTER UPDATE ON studio_records
    REFERENCING OLD TABLE AS studio_org_usage_old NEW TABLE AS studio_org_usage_new
    FOR EACH STATEMENT EXECUTE FUNCTION studio_org_usage_update();

DROP TRIGGER IF EXISTS studio_org_usage_delete ON studio_records;
CREATE TRIGGER studio_org_usage_delete
    AFTER DELETE ON studio_records
    REFERENCING OLD TABLE AS studio_org_usage_old
    FOR EACH STATEMENT EXECUTE FUNCTION studio_org_usage_delete();

-- ── 5. The backfill: the exact count of each org, bound in turn ─────────────
-- One row for each org that exists, also an org with no record. A second run
-- sets each row to the same count again.
DO $fill$
DECLARE
    org uuid;
    bypass boolean;
BEGIN
    SELECT r.rolsuper OR r.rolbypassrls INTO bypass
      FROM pg_roles r WHERE r.rolname = current_user;
    IF bypass THEN
        -- One statement, and no bind: row security does not bind this role.
        INSERT INTO studio_org_usage AS u (organization_id, records, bytes)
        SELECT o.id, count(r.id), coalesce(sum(octet_length(r.data::text)), 0)
          FROM organization o
          LEFT JOIN studio_records r ON r.organization_id = o.id
         GROUP BY o.id
        ON CONFLICT (organization_id) DO UPDATE
           SET records = EXCLUDED.records,
               bytes = EXCLUDED.bytes,
               updated_at = now()
         WHERE u.records IS DISTINCT FROM EXCLUDED.records
            OR u.bytes IS DISTINCT FROM EXCLUDED.bytes;
        RETURN;
    END IF;
    FOR org IN SELECT o.id FROM organization o LOOP
        PERFORM set_config('app.tenant_id', org::text, true);
        INSERT INTO studio_org_usage AS u (organization_id, records, bytes)
        SELECT org, count(*), coalesce(sum(octet_length(r.data::text)), 0)
          FROM studio_records r
         WHERE r.organization_id = org
        ON CONFLICT (organization_id) DO UPDATE
           SET records = EXCLUDED.records,
               bytes = EXCLUDED.bytes,
               updated_at = now()
         WHERE u.records IS DISTINCT FROM EXCLUDED.records
            OR u.bytes IS DISTINCT FROM EXCLUDED.bytes;
    END LOOP;
END
$fill$;

COMMENT ON TABLE studio_org_usage IS
    'The records and the bytes of record JSON of one org (WS-STU C10a). '
    'Only the triggers on studio_records write it, and objects_store.py reads it. '
    'project-docs/specs/ai_studio.md §3.1.';

COMMIT;
