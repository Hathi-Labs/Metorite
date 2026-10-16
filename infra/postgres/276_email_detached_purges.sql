-- ============================================================================
-- 276_email_detached_purges.sql — the Mem0 purges that an org purge owes.
--
-- What: one new table, `email_detached_purges`, three SECURITY DEFINER
--       functions that are the ONLY way in, and one deferred trigger.
--       `DELETE /internal/operator/organizations/{slug}` calls
--       `record_detached_purges(org)` in the SAME transaction as its
--       `DELETE FROM organization`. The function writes one row for each
--       mailbox of the org, and it carries each open `email_mailbox_purges`
--       row of the org, because the cascade deletes those rows. The sweeper
--       (`routes/email/memory_purge.py`) takes a row with
--       `claim_detached_purge`, runs the Mem0 pass, and records the outcome
--       with `settle_detached_purge`.
-- Why:  WS-17 EM-G11f1c (PR-G7c), spec
--       project-docs/specs/email_app_master_plan.md §12.3.13.9.2. A row of
--       `email_mailbox_purges` has `organization_id ... ON DELETE CASCADE`, so
--       the org delete deletes it in the same statement, and no sweep can find
--       it after. Without this table, the Mem0 memory of each mailbox of a
--       purged org stays for ever.
-- Depends on: 130_org_access_control.sql (organization), 17_email_accounts.sql
--       and 223 (email_accounts.organization_id), 264_email_mailbox_purges.sql.
--
-- ⚠️ The table has NO `organization_id` and NO row level security, on
-- purpose. The tenant of each row no longer exists, so no session can bind it.
-- `purged_org_id` is a plain column with no foreign key, because the
-- organization row is gone. `account_id` has no foreign key either, for the
-- reason of migration 264. `scripts/gen_tenant_migration.py` lists the table
-- in `EXEMPT` with that reason (R5 (a)), and that entry is the security
-- review.
--
-- ── Why the keys are safe (security review, fix round 1) ────────────────────
--
-- The sweeper deletes the Mem0 key `<owner>#acct:<account_id>` of each row,
-- with no tenant bound. So a row must never name a LIVE mailbox. Four rules
-- hold that, and none of them rests on the good conduct of app code:
--
--   1. No app role touches the table. The file revokes every table privilege
--      from PUBLIC, from the three Supabase roles and from `acb_app`. The app
--      reaches the table only through the three functions below.
--   2. `record_detached_purges(p_org)` takes rows ONLY from `email_accounts`
--      and the open `email_mailbox_purges` of `p_org`, and only when the
--      caller's transaction binds that same org (`app.tenant_id`). A caller
--      bound to org A cannot record org B.
--   3. A deferred constraint trigger checks each new row at COMMIT. When the
--      org of the row still exists, the commit fails. So a row commits only
--      in the transaction that deletes its org. The mailboxes of that org
--      cascade from it (migration 223), so no row can name a mailbox that
--      still exists after the commit.
--   4. `claim_detached_purge` and `settle_detached_purge` read and write only
--      the state of a row. They never write an owner or a mailbox id.
--
-- **How the record call works inside the org delete.** The purge binds the
-- org with `SET LOCAL app.tenant_id` before it calls the function. The
-- function runs as its owner. FORCE RLS binds the owner too, unless it is a
-- superuser or BYPASSRLS. A bound owner sees the rows of the bound org, and
-- the function asks for that org only, so it reads the same rows that the
-- caller sees. An owner that bypasses RLS still reads one org, because each
-- statement names `p_org`. Either way the read is exactly the org's rows.
--
-- **No address stays.** When a row closes (`done` or `refused`), the settle
-- function writes an empty `owner_email`. A closed row keeps a mailbox id and
-- its counts, and no address.
--
-- Grants follow migration 263: `REVOKE ALL ... FROM PUBLIC`, the three
-- Supabase roles by name, and EXECUTE to `acb_app` only, when it exists. Each
-- function has a fixed `search_path`, and every name is qualified.
--
-- Expand only (R6): one new table, three new functions and one trigger on the
-- new table. No rename, no drop, and no change to an existing table. Old code
-- names no new object.
-- Idempotent per infra/postgres/README.md: CREATE TABLE IF NOT EXISTS, CREATE
-- INDEX IF NOT EXISTS, CREATE OR REPLACE FUNCTION, DROP TRIGGER IF EXISTS then
-- CREATE, and REVOKE and GRANT, which a second run repeats with no change.
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
    'because its tenant no longer exists. No app grant: the functions '
    'record_detached_purges, claim_detached_purge and settle_detached_purge '
    'are the only way in. email_app_master_plan.md §12.3.13.9.2.';

-- ── Rule 3: a row commits only with the delete of its org ──────────────────

CREATE OR REPLACE FUNCTION public.email_detached_purges_org_gone()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, pg_temp
AS $org_gone$
BEGIN
    IF EXISTS (SELECT 1 FROM public.organization o WHERE o.id = NEW.purged_org_id) THEN
        RAISE EXCEPTION
            'email_detached_purges: organization % still exists at commit',
            NEW.purged_org_id
            USING ERRCODE = '23514';
    END IF;
    RETURN NULL;
END
$org_gone$;

DROP TRIGGER IF EXISTS email_detached_purges_org_gone ON email_detached_purges;
CREATE CONSTRAINT TRIGGER email_detached_purges_org_gone
    AFTER INSERT ON email_detached_purges
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION public.email_detached_purges_org_gone();

-- ── Rule 2: the record, in the transaction of the org delete ───────────────

CREATE OR REPLACE FUNCTION public.record_detached_purges(p_org uuid)
RETURNS integer
LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $record$
DECLARE
    bound    uuid;
    recorded integer;
BEGIN
    IF p_org IS NULL THEN
        RAISE EXCEPTION 'record_detached_purges: no organization'
            USING ERRCODE = '22004';
    END IF;
    bound := NULLIF(pg_catalog.current_setting('app.tenant_id', true), '')::uuid;
    IF bound IS DISTINCT FROM p_org THEN
        RAISE EXCEPTION
            'record_detached_purges: the transaction does not bind organization %',
            p_org
            USING ERRCODE = '42501';
    END IF;

    -- One row for each mailbox of the org. The owner is the user_id of the
    -- row, the key of the Mem0 writers. The lease is 0.
    INSERT INTO public.email_detached_purges
           (purged_org_id, owner_email, account_id, next_try_at)
    SELECT a.organization_id, a.user_id, a.id, pg_catalog.now()
      FROM public.email_accounts a
     WHERE a.organization_id = p_org
    ON CONFLICT (purged_org_id, account_id) DO NOTHING;
    GET DIAGNOSTICS recorded = ROW_COUNT;

    -- Each open purge row of the org, which the cascade would delete.
    INSERT INTO public.email_detached_purges
           (purged_org_id, owner_email, account_id, passes, tries, next_try_at)
    SELECT p.organization_id, p.owner_email, p.account_id, p.passes, p.tries,
           p.next_try_at
      FROM public.email_mailbox_purges p
     WHERE p.state = 'open' AND p.organization_id = p_org
    ON CONFLICT (purged_org_id, account_id) DO NOTHING;

    RETURN recorded;
END
$record$;

-- ── Rule 4: the sweeper's claim and settle ─────────────────────────────────

CREATE OR REPLACE FUNCTION public.claim_detached_purge(p_lease_s double precision)
RETURNS TABLE (id uuid, owner_email text, account_id uuid, passes smallint,
               tries integer)
LANGUAGE sql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $claim$
    UPDATE public.email_detached_purges p
       SET tries = p.tries + 1, updated_at = pg_catalog.now(),
           next_try_at = pg_catalog.now() + pg_catalog.make_interval(secs => p_lease_s)
     WHERE p.id IN (
           SELECT d.id FROM public.email_detached_purges d
            WHERE d.state = 'open' AND d.next_try_at <= pg_catalog.now()
            ORDER BY d.next_try_at
            LIMIT 1
            FOR UPDATE SKIP LOCKED)
    RETURNING p.id, p.owner_email, p.account_id, p.passes, p.tries;
$claim$;

CREATE OR REPLACE FUNCTION public.settle_detached_purge(
    p_id uuid, p_outcome text, p_passes smallint, p_delay_s double precision,
    p_error text)
RETURNS boolean
LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $settle$
BEGIN
    IF p_outcome = 'pass' THEN
        -- A good pass. The second one closes the row and drops the address.
        UPDATE public.email_detached_purges d
           SET passes = p_passes, last_error = NULL, updated_at = pg_catalog.now(),
               state = CASE WHEN p_passes >= 2 THEN 'done' ELSE 'open' END,
               done_at = CASE WHEN p_passes >= 2 THEN pg_catalog.now() END,
               owner_email = CASE WHEN p_passes >= 2 THEN '' ELSE d.owner_email END,
               next_try_at = pg_catalog.now() + pg_catalog.make_interval(secs => p_delay_s)
         WHERE d.id = p_id AND d.state = 'open';
    ELSIF p_outcome = 'failed' THEN
        -- A failed pass. The row stays open, and waits for the back-off.
        UPDATE public.email_detached_purges d
           SET last_error = pg_catalog.left(p_error, 64), updated_at = pg_catalog.now(),
               next_try_at = pg_catalog.now() + pg_catalog.make_interval(secs => p_delay_s)
         WHERE d.id = p_id AND d.state = 'open';
    ELSIF p_outcome = 'refused' THEN
        -- A key that no purge can delete. It closes, and drops the address.
        UPDATE public.email_detached_purges d
           SET state = 'refused', last_error = 'scope_refused', owner_email = '',
               updated_at = pg_catalog.now()
         WHERE d.id = p_id AND d.state = 'open';
    ELSE
        RAISE EXCEPTION 'settle_detached_purge: unknown outcome %', p_outcome
            USING ERRCODE = '22023';
    END IF;
    RETURN FOUND;
END
$settle$;

-- ── Grants ──────────────────────────────────────────────────────────────────

REVOKE ALL ON TABLE email_detached_purges FROM PUBLIC;
REVOKE ALL ON FUNCTION public.record_detached_purges(uuid) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.claim_detached_purge(double precision) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.settle_detached_purge(uuid, text, smallint, double precision, text)
    FROM PUBLIC;
REVOKE ALL ON FUNCTION public.email_detached_purges_org_gone() FROM PUBLIC;

DO $$
DECLARE
    r text;
BEGIN
    -- Supabase's default privileges hand every new table and function to
    -- these roles.
    FOREACH r IN ARRAY ARRAY['anon', 'authenticated', 'service_role'] LOOP
        IF EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = r) THEN
            EXECUTE format('REVOKE ALL ON TABLE email_detached_purges FROM %I', r);
            EXECUTE format(
                'REVOKE ALL ON FUNCTION public.record_detached_purges(uuid) FROM %I', r);
            EXECUTE format(
                'REVOKE ALL ON FUNCTION public.claim_detached_purge(double precision) FROM %I', r);
            EXECUTE format(
                'REVOKE ALL ON FUNCTION public.settle_detached_purge'
                '(uuid, text, smallint, double precision, text) FROM %I', r);
            EXECUTE format(
                'REVOKE ALL ON FUNCTION public.email_detached_purges_org_gone() FROM %I', r);
        END IF;
    END LOOP;
    -- The application role of saas_multitenancy_handover.md §H3. Its default
    -- privileges give it the table. It loses each privilege, and keeps only
    -- EXECUTE on the three functions.
    IF EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = 'acb_app') THEN
        REVOKE ALL ON TABLE email_detached_purges FROM acb_app;
        GRANT EXECUTE ON FUNCTION public.record_detached_purges(uuid) TO acb_app;
        GRANT EXECUTE ON FUNCTION public.claim_detached_purge(double precision) TO acb_app;
        GRANT EXECUTE ON FUNCTION public.settle_detached_purge
            (uuid, text, smallint, double precision, text) TO acb_app;
    END IF;
END
$$;

COMMIT;
