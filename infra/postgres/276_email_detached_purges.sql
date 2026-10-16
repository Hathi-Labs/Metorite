-- ============================================================================
-- 276_email_detached_purges.sql — the Mem0 purges that an org purge owes.
--
-- What: one new table, `email_detached_purges`. `DELETE /internal/operator/
--       organizations/{slug}` writes one row for each mailbox of the org, in
--       the SAME transaction as its `DELETE FROM organization`. It also
--       carries each open `email_mailbox_purges` row of the org, because the
--       cascade deletes those rows. The sweeper
--       (`routes/email/memory_purge.py`) runs the two Mem0 passes later and
--       closes each row, as it does for `email_mailbox_purges`.
-- Why:  WS-17 EM-G11f1c (PR-G7c), spec
--       project-docs/specs/email_app_master_plan.md §12.3.13.9.2. A row of
--       `email_mailbox_purges` has `organization_id ... ON DELETE CASCADE`, so
--       the org delete deletes it in the same statement, and no sweep can find
--       it after. Without this table, the Mem0 memory of each mailbox of a
--       purged org stays for ever.
-- Depends on: nothing. The table names no other table.
--
-- ⚠️ The table has NO `organization_id` and NO row level security, on
-- purpose. The tenant of each row no longer exists, so no session can bind it.
-- `purged_org_id` is a plain column with no foreign key, because the
-- organization row is gone. `account_id` has no foreign key either, for the
-- reason of migration 264. `scripts/gen_tenant_migration.py` lists the table
-- in `EXEMPT` with that reason (R5 (a)), and that entry is the security
-- review.
-- What the table holds: an owner address and a mailbox id, and no mail.
-- Its grants follow migration 263: the file revokes every privilege from
-- PUBLIC and from the three Supabase roles by name, and grants SELECT, INSERT
-- and UPDATE to `acb_app` when that role exists. With no RLS, a Supabase role
-- with a grant could read each address through the REST API, so the revoke
-- is load-bearing.
--
-- Expand only (R6): one new table. No rename, no drop, and no change to an
-- existing table. Old code names no new object.
-- Idempotent per infra/postgres/README.md: CREATE TABLE IF NOT EXISTS, CREATE
-- INDEX IF NOT EXISTS, and REVOKE and GRANT, which a second run repeats with
-- no change.
-- The number is taken at build time (R1). On 2026-10-16 main held 273 and
-- 275, and no open pull request held a migration. 274 is a gap that main
-- skipped, and production applied 275 already, so 274 would run out of
-- order. Check it again at merge.
-- Pinned by tests/unit/test_tenant_coverage.py, which finds this file by
-- CONTENT, never by number (R1), and by the R8 cases of
-- tests/unit/test_email_purge_wiring.py.
-- ============================================================================

BEGIN;

CREATE TABLE IF NOT EXISTS email_detached_purges (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    purged_org_id    UUID NOT NULL,
    owner_email      TEXT NOT NULL,
    account_id       UUID NOT NULL,
    state            TEXT NOT NULL DEFAULT 'open',
    passes           SMALLINT NOT NULL DEFAULT 0,
    tries            INTEGER NOT NULL DEFAULT 0,
    last_error       TEXT NULL,
    next_try_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    done_at          TIMESTAMPTZ NULL,
    CONSTRAINT email_detached_purges_one_per_mailbox UNIQUE (purged_org_id, account_id),
    CONSTRAINT email_detached_purges_state_known
        CHECK (state IN ('open', 'done', 'refused')),
    CONSTRAINT email_detached_purges_passes_range CHECK (passes BETWEEN 0 AND 2)
);

-- The sweeper reads the open rows that are due, across every purged org.
CREATE INDEX IF NOT EXISTS idx_email_detached_purges_open_due
    ON email_detached_purges (next_try_at)
    WHERE state = 'open';

COMMENT ON TABLE email_detached_purges IS
    'WS-17 EM-G11f1c: one row for each Mem0 purge that an org purge owes. '
    'Written in the transaction of DELETE FROM organization. RLS-EXEMPT, '
    'because its tenant no longer exists. routes/email/memory_purge.py runs '
    'and closes it. email_app_master_plan.md §12.3.13.9.2.';

REVOKE ALL ON TABLE email_detached_purges FROM PUBLIC;

DO $$
DECLARE
    r text;
BEGIN
    -- Supabase's default privileges hand every new table to these roles.
    FOREACH r IN ARRAY ARRAY['anon', 'authenticated', 'service_role'] LOOP
        IF EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = r) THEN
            EXECUTE format('REVOKE ALL ON TABLE email_detached_purges FROM %I', r);
        END IF;
    END LOOP;
    -- The application role of saas_multitenancy_handover.md §H3. Its default
    -- privileges give it DELETE too. No code deletes a row, so it loses that.
    IF EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = 'acb_app') THEN
        REVOKE ALL ON TABLE email_detached_purges FROM acb_app;
        GRANT SELECT, INSERT, UPDATE ON TABLE email_detached_purges TO acb_app;
    END IF;
END
$$;

COMMIT;
