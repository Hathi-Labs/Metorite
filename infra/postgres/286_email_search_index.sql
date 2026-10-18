-- ============================================================================
-- 286_email_search_index.sql — mail search that uses its index under RLS.
-- ============================================================================
--
-- ⚠️ The number was taken from the directory at BUILD time (R1). It was 284
--    until 2026-10-19, when #1154 took 284 on main. #1151 holds 283 and the
--    hotfix branch `mail-handoff-refused` holds 285. Check it again at merge.
--    `tests/unit/test_email_search_index_r8.py` finds this file by CONTENT
--    (`email_message_search`), never by number, so a renumber costs nothing.
--
-- What: one GIN expression index and one function.
--
--   idx_email_messages_fts_all
--       GIN over the search vector: subject, body, the sender, and the
--       names and addresses of To and Cc.
--   public.email_message_search(p_query text, p_accounts uuid[],
--                               p_limit integer) RETURNS SETOF uuid
--       The ids of the mail of the BOUND organization, in the mailboxes
--       p_accounts, that match p_query in the `websearch_to_tsquery` grammar.
--       Newest first, at most p_limit (the route asks 20 001 and shows
--       20 000).
--
-- Why:  incident 2026-10-18, `project-docs/specs/email_app_master_plan.md`
--       §18 "Incident 2026-10-18: search speed", board WS-17. On WhatsApp seven of
--       twelve `query_inbox` calls of one member died on the 30 s statement
--       timeout. Each one ran the COUNT(*) of `GET /email/messages` with a
--       full-text match.
--
-- ── The cause: `@@` is not LEAKPROOF, so RLS keeps it out of the index ─────
--
-- Migration 72 built `idx_email_messages_fts_body`, and its expression is
-- byte-for-byte the predicate of the route. The planner never used it for the
-- app role. Under row level security a qual on the table may become an index
-- qual only when its function is LEAKPROOF, because a leaky function could
-- see a row of another tenant before the policy removes it. `ts_match_vq`
-- (the `@@` of tsvector and tsquery) is not LEAKPROOF. So for `acb_app` the
-- planner reads every row of the organization and runs `to_tsvector` over
-- each body. Measured on a private ladder database, 20 002 mails of one
-- mailbox, PG 16:
--
--   as the superuser   Bitmap Index Scan on idx_email_messages_fts_body   0.5 ms
--   as the app role    Filter: to_tsvector(...) @@ ...  (10 001 rows/worker)  6 815 ms
--
-- Only a superuser can mark a function LEAKPROOF, and Supabase gives us none.
--
-- ── The fix: one narrow SECURITY DEFINER read ───────────────────────────────
--
-- The function runs as its owner. On production that is `postgres`, which is
-- BYPASSRLS (read from pg_roles on 2026-10-18). For that role the match is a
-- plain qual, so the planner uses the GIN index. The function takes the
-- organization from `app.tenant_id`, the same GUC the policy reads, and it
-- writes `organization_id = <that org>` itself. The gateway then reads the
-- rows by primary key under RLS as before, so every row still passes the
-- policy. The rules of 222_chat_session_exists.sql and 263 hold:
--
--   * It returns ids of the bound organization only, never a row.
--   * It reads only the mailboxes in p_accounts. The route passes the
--     mailboxes of the member (`core._owned_accounts_sql`), never request
--     input. So the cost is bounded by the mailboxes of the member, never by
--     the organization (fix round 1: a word in each mail of the org cost
--     18.3 s when the function filtered on the org alone).
--   * No organization bound, no ids. This fails closed, as the policy does.
--   * `SET search_path = pg_catalog, pg_temp`, and the table is qualified with
--     `public.`. A caller cannot plant an `email_messages` of its own.
--   * EXECUTE goes to the app role only. PUBLIC and the three Supabase roles
--     lose it by name.
--   * An owner that cannot see through RLS still gets the right ids, because
--     the policy reads the same GUC. Only the speed is lost. So a box where
--     the owner is not BYPASSRLS is slow, never wrong.
--   * The ladder shape (no `organization_id`, no row security) has no tenant
--     to keep apart, and every caller sees every row there. The function
--     then matches without the organization. Any other shape with no
--     column returns nothing.
--
-- ── The vector gained To and Cc ─────────────────────────────────────────────
--
-- The search of the incident was "Sanjeev Goel Payal Dave". Payal Dave was
-- the RECIPIENT. The old vector held only the sender, so the search could
-- not match even when it was fast. The new vector adds every value of each
-- To and Cc entry, which is the name and the address. The four words still
-- AND together across the fields, so the sender in From and the person in
-- To match one mail.
--
-- ── R6, expand/contract ─────────────────────────────────────────────────────
--
-- ADDITIVE only. One new index, one new function. No column, no rename. Old
-- code does not name either one and does not need them. The gateway that
-- calls the function ships in the same release, after this file applies.
-- `idx_email_messages_fts_body` (72) and `idx_email_messages_fts` (17) stay.
-- `/email/search` still ranks with the vector of 72. Their drop is a later
-- contract step (email_app_master_plan.md §18.6).
--
-- No new table, so R5 has nothing to key. Idempotent: `IF NOT EXISTS` and
-- `CREATE OR REPLACE`.
--
-- ── Locking ─────────────────────────────────────────────────────────────────
--
-- Plain `CREATE INDEX`, as every other migration here. NOT `CONCURRENTLY`,
-- for two reasons. 226 and 170 give the first: a failed concurrent build
-- leaves an INVALID index, and `IF NOT EXISTS` then skips it forever. The
-- second: `tests/unit/_tenant_ladder.py` applies the whole ladder in ONE
-- transaction, and CONCURRENTLY cannot run inside a transaction block.
--
-- The cost is a SHARE lock for the build: reads go on, and writes to
-- `email_messages` wait. Measured on the private ladder database, 28 002
-- mails with 130 MB of body text: 27.8 s (64 MB maintenance_work_mem) and
-- 28.1 s (128 MB). Memory does not help, because the time is `to_tsvector`
-- over each body. Production held about 44 000 mails (653 MB with toast) on
-- 2026-10-18, so expect one minute or more. The daily release runs at 21:30
-- UTC, which is 03:00 in India, where the mail of customer zero lives.
--
-- ⚠️ PRODUCTION BUILDS THIS INDEX BY HAND, with `CREATE INDEX CONCURRENTLY`,
-- before the release. Then `IF NOT EXISTS` below skips the build and takes no
-- lock. The by-hand statement and its validity check are in
-- email_app_master_plan.md §18.7. ⚠️ The definition below and the by-hand
-- statement must stay the same, character for character. An index with this
-- name and another expression would satisfy `IF NOT EXISTS` and serve nothing.
--
-- ⚠️ The server default `statement_timeout` on production is 120 s, and the
-- migration role has no override. A build past it fails the deploy. So this
-- session lifts the timeout for this file only. The runner gives each file
-- its own psql session, so the SET ends with the file.
-- ============================================================================

SET statement_timeout = '15min';

CREATE INDEX IF NOT EXISTS idx_email_messages_fts_all
    ON email_messages
    USING GIN (
        to_tsvector('english',
            coalesce(subject, '') || ' ' ||
            coalesce(body_text, '') || ' ' ||
            coalesce(from_address->>'name', '') || ' ' ||
            coalesce(from_address->>'email', '') || ' ' ||
            coalesce(jsonb_path_query_array(to_addresses, '$[*].*')::text, '') || ' ' ||
            coalesce(jsonb_path_query_array(cc_addresses, '$[*].*')::text, '')
        )
    );

-- A copy of this file before fix round 1 made a one-argument version. It was
-- never merged, so no box but a scratch database holds it.
DROP FUNCTION IF EXISTS public.email_message_search(text);

CREATE OR REPLACE FUNCTION public.email_message_search(
    p_query text, p_accounts uuid[], p_limit integer)
RETURNS SETOF uuid
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
-- The planner settings of the exact path below. Each one lives only for the
-- call. work_mem keeps the bitmap exact (a lossy page is rechecked with
-- to_tsvector).
SET enable_seqscan = off
SET work_mem = '16MB'
ROWS 100
AS $email_message_search$
DECLARE
    v_q tsquery;
    v_org uuid;
    v_has_org boolean;
    v_rls boolean;
    v_limit integer;
BEGIN
    IF p_query IS NULL OR btrim(p_query) = ''
       OR p_accounts IS NULL OR cardinality(p_accounts) = 0 THEN
        RETURN;
    END IF;
    -- The cap. The caller asks for one more than it shows, to see a cut.
    v_limit := LEAST(GREATEST(COALESCE(p_limit, 20001), 0), 50000);

    SELECT EXISTS (
               SELECT 1 FROM pg_attribute a
                WHERE a.attrelid = 'public.email_messages'::regclass
                  AND a.attname = 'organization_id'
                  AND NOT a.attisdropped),
           c.relrowsecurity OR c.relforcerowsecurity
      INTO v_has_org, v_rls
      FROM pg_class c
     WHERE c.oid = 'public.email_messages'::regclass;

    IF NOT v_has_org THEN
        -- The ladder shape: no tenant column and no policy. Every caller sees
        -- every row there, so there is nothing to keep apart. A table with
        -- row security and no tenant column is not a shape we know: nothing.
        IF v_rls THEN
            RETURN;
        END IF;
        RETURN QUERY
            SELECT m.id
              FROM public.email_messages m
             WHERE m.account_id = ANY(p_accounts)
               AND to_tsvector('english',
                   coalesce(m.subject, '') || ' ' ||
                   coalesce(m.body_text, '') || ' ' ||
                   coalesce(m.from_address->>'name', '') || ' ' ||
                   coalesce(m.from_address->>'email', '') || ' ' ||
                   coalesce(jsonb_path_query_array(m.to_addresses, '$[*].*')::text, '') || ' ' ||
                   coalesce(jsonb_path_query_array(m.cc_addresses, '$[*].*')::text, ''))
               @@ websearch_to_tsquery('english', p_query)
             ORDER BY m.received_at DESC NULLS LAST, m.id DESC
             LIMIT v_limit;
        RETURN;
    END IF;

    -- The same GUC and the same NULL rule as the tenant policy: no bound
    -- organization, no rows.
    v_org := NULLIF(current_setting('app.tenant_id', true), '')::uuid;
    IF v_org IS NULL THEN
        RETURN;
    END IF;

    v_q := websearch_to_tsquery('english', p_query);
    IF numnode(v_q) = 0 THEN
        RETURN;   -- only stop words: nothing to match
    END IF;

    IF strpos(v_q::text, '!') = 0 AND strpos(v_q::text, '<') = 0 THEN
        -- THE EXACT PATH. With no NOT and no phrase, the GIN bitmap is exact,
        -- so the heap scan runs no to_tsvector at all. The planner cannot see
        -- that: it charges the vector to every row of every bitmap path, so
        -- for a word in each mail it took the org and mailbox btrees and ran
        -- to_tsvector over the whole mailbox (7.1 s on 20 000 mails). Here
        -- the org and the mailboxes are checked on the heap row in a form no
        -- btree serves (`IS TRUE`), and `enable_seqscan = off` above leaves
        -- the GIN index as the one path. The checks still bind each row.
        RETURN QUERY
            SELECT m.id
              FROM public.email_messages m
             WHERE to_tsvector('english',
                   coalesce(m.subject, '') || ' ' ||
                   coalesce(m.body_text, '') || ' ' ||
                   coalesce(m.from_address->>'name', '') || ' ' ||
                   coalesce(m.from_address->>'email', '') || ' ' ||
                   coalesce(jsonb_path_query_array(m.to_addresses, '$[*].*')::text, '') || ' ' ||
                   coalesce(jsonb_path_query_array(m.cc_addresses, '$[*].*')::text, ''))
                   @@ v_q
               AND (m.organization_id = v_org) IS TRUE
               AND (m.account_id = ANY(p_accounts)) IS TRUE
             ORDER BY m.received_at DESC NULLS LAST, m.id DESC
             LIMIT v_limit;
        RETURN;
    END IF;

    -- THE SCOPED PATH. A NOT or a phrase makes GIN recheck each candidate
    -- with to_tsvector, so the candidates must be the mailboxes of the
    -- member, never the whole table. The worst case is one scan of those
    -- mailboxes, as on main before the fix.
    RETURN QUERY
        SELECT m.id
          FROM public.email_messages m
         WHERE m.organization_id = v_org
           AND m.account_id = ANY(p_accounts)
           AND to_tsvector('english',
                   coalesce(m.subject, '') || ' ' ||
                   coalesce(m.body_text, '') || ' ' ||
                   coalesce(m.from_address->>'name', '') || ' ' ||
                   coalesce(m.from_address->>'email', '') || ' ' ||
                   coalesce(jsonb_path_query_array(m.to_addresses, '$[*].*')::text, '') || ' ' ||
                   coalesce(jsonb_path_query_array(m.cc_addresses, '$[*].*')::text, ''))
               @@ v_q
         ORDER BY m.received_at DESC NULLS LAST, m.id DESC
         LIMIT v_limit;
END
$email_message_search$;

COMMENT ON FUNCTION public.email_message_search(text, uuid[], integer) IS
    'Ids of the mail of the organization in app.tenant_id, in the mailboxes '
    'p_accounts, that match the websearch_to_tsquery text. Newest first, at '
    'most p_limit. Returns ids, never a row. SECURITY DEFINER so the GIN index '
    'idx_email_messages_fts_all serves it under RLS. WS-17, incident '
    '2026-10-18, email_app_master_plan.md §18.';

REVOKE ALL ON FUNCTION public.email_message_search(text, uuid[], integer) FROM PUBLIC;

DO $$
DECLARE
    r text;
BEGIN
    -- Supabase's default privileges hand every new function to these roles.
    FOREACH r IN ARRAY ARRAY['anon', 'authenticated', 'service_role'] LOOP
        IF EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = r) THEN
            EXECUTE format(
                'REVOKE ALL ON FUNCTION public.email_message_search(text, uuid[], integer) FROM %I', r);
        END IF;
    END LOOP;
    -- The application role of saas_multitenancy_handover.md §H3.
    IF EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = 'acb_app') THEN
        GRANT EXECUTE ON FUNCTION public.email_message_search(text, uuid[], integer) TO acb_app;
    END IF;
END
$$;
