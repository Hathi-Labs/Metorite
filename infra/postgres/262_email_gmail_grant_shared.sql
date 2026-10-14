-- 262_email_gmail_grant_shared.sql — does another Metorite mailbox use this Gmail grant?
--
-- What: one function, `email_gmail_grant_shared(p_email text, p_exclude uuid)
--       RETURNS boolean`. It answers only whether ANOTHER `email_accounts`
--       row with `provider = 'gmail'` and the same address (case-insensitive)
--       exists, in any tenant. It never returns the row, the owner or the
--       organization id.
-- Why:  spec `project-docs/specs/email_app_master_plan.md` §12.3.13.9, board
--       WS-17 EM-G11f1, review round 1 (P1). A disconnect revokes the Google
--       grant of the mailbox. Google keeps ONE grant for each Google account
--       and each OAuth client, so the revoke also ends the grant of every
--       other Metorite row of that Gmail account. A mailbox row is unique per
--       organization, member, provider and address (migration 223). So two
--       members who share `support@`, or one person in two organizations,
--       can each connect the same Gmail. Without this check, a disconnect by
--       one of them makes the next refresh of the other fail with
--       `invalid_grant`. The gateway calls this function before the revoke,
--       and it skips the revoke unless the answer is `false`.
-- Depends on: 17_email_accounts.sql (`email_accounts`). Works on the ladder
--       shape (no RLS) and on the tenancy shape (generated/01..04). Idempotent.
--
-- Expand only (R6): one new function. No table, no column, no rename. Old code
-- names no new object. The number is taken at build time (R1). On 2026-10-14
-- main held 260 and the open PR #1002 held 261. Check it again at merge.
--
-- ── Why SECURITY DEFINER, and why it is safe here ───────────────────────────
--
-- The rules of 222_chat_session_exists.sql, unchanged:
--
--   * It returns a boolean, never a row. The gateway passes the address of
--     the row that it has just deleted, never a value from the request.
--   * `SET search_path = pg_catalog, pg_temp`, and every name is qualified
--     with `public.`. So a caller cannot plant an `email_accounts` of its own.
--   * FORCE RLS binds the OWNER too, unless it is a superuser or BYPASSRLS.
--     When the owner cannot see every row, the function returns NULL. The
--     gateway reads NULL as "cannot tell" and skips the revoke. That fails
--     safe toward the other mailbox: a missed revoke leaves a grant that the
--     member can remove at Google, and a wrong revoke breaks a live mailbox.
--   * EXECUTE goes to the app role only. The file revokes it from PUBLIC and
--     from the three Supabase roles by name, and grants it to `acb_app` when
--     that role exists.
--
-- Fences: `tests/unit/test_email_disconnect_order.py`
-- (`TestTheDisconnectUnderForceRLS`: FORCE RLS, as a role that is not a
-- superuser, not the owner and not BYPASSRLS) and
-- `tests/unit/test_email_mailbox_teardown.py` (the gateway half).

SET lock_timeout = '5s';

CREATE OR REPLACE FUNCTION public.email_gmail_grant_shared(p_email text, p_exclude uuid)
RETURNS boolean
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $email_gmail_grant_shared$
DECLARE
    sees_all boolean;
BEGIN
    IF p_email IS NULL OR pg_catalog.btrim(p_email) = '' THEN
        RETURN NULL;   -- cannot tell; the caller skips the revoke
    END IF;

    -- current_user is the OWNER inside a DEFINER function. A table with no
    -- RLS shows every row to it. A table with RLS shows every row only to a
    -- superuser or a BYPASSRLS owner.
    SELECT r.rolsuper OR r.rolbypassrls
            OR NOT (c.relrowsecurity OR c.relforcerowsecurity)
      INTO sees_all
      FROM pg_catalog.pg_roles r, pg_catalog.pg_class c
     WHERE r.rolname = current_user
       AND c.oid = 'public.email_accounts'::pg_catalog.regclass;

    IF NOT COALESCE(sees_all, false) THEN
        RETURN NULL;   -- cannot tell; the caller skips the revoke
    END IF;

    RETURN EXISTS (
        SELECT 1 FROM public.email_accounts a
         WHERE a.provider = 'gmail'
           AND pg_catalog.lower(a.email_address) = pg_catalog.lower(p_email)
           AND (p_exclude IS NULL OR a.id <> p_exclude)
    );
END
$email_gmail_grant_shared$;

COMMENT ON FUNCTION public.email_gmail_grant_shared(text, uuid) IS
    'True when another gmail email_accounts row with this address exists in '
    'any tenant. Returns one bit, never a row. NULL when the owner cannot see '
    'through RLS. WS-17 EM-G11f1, email_app_master_plan.md §12.3.13.9.';

REVOKE ALL ON FUNCTION public.email_gmail_grant_shared(text, uuid) FROM PUBLIC;

DO $$
DECLARE
    r text;
BEGIN
    -- Supabase's default privileges hand every new function to these roles.
    FOREACH r IN ARRAY ARRAY['anon', 'authenticated', 'service_role'] LOOP
        IF EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = r) THEN
            EXECUTE format(
                'REVOKE ALL ON FUNCTION public.email_gmail_grant_shared(text, uuid) FROM %I', r);
        END IF;
    END LOOP;
    -- The application role of saas_multitenancy_handover.md §H3.
    IF EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = 'acb_app') THEN
        GRANT EXECUTE ON FUNCTION public.email_gmail_grant_shared(text, uuid) TO acb_app;
    END IF;
END
$$;
