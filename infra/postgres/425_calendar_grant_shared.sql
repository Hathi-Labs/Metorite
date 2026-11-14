-- 425_calendar_grant_shared.sql — a live Google Calendar connection holds the Google grant too
--
-- What: the two grant-shared functions of 300_kb_sources.sql, replaced. They
--       now also count a live `google_calendar` integration_connections row
--       (WS-CAL CE-10a, task 62). Same names, same arguments, same return type.
-- Why:  Metorite uses ONE Google OAuth client for Gmail, Drive and Calendar
--       (D-EM-5). Google keeps ONE grant for each account and client, and a
--       revoke ends all of it. With `include_granted_scopes` (D215 point 1),
--       the calendar token shares the grant of the mailbox. A Gmail or Drive
--       disconnect used to see no other holder, and it revoked. The next
--       calendar refresh then failed with `invalid_grant`. Opus review of
--       PR 2172, P1.
-- Expand only (R6): CREATE OR REPLACE keeps the owner and the grants. No table,
--       no column. The number is taken at build time (R1): main held 422, and
--       the open PRs 2137 and 2163 held 423 and 424. Check it again at merge.
-- The rules of 263 and 300 bind unchanged: SECURITY DEFINER, a fixed
--       search_path, a boolean and never a row, NULL when the owner cannot see
--       through RLS.

CREATE OR REPLACE FUNCTION public.kb_google_drive_grant_shared(p_email text, p_exclude uuid)
RETURNS boolean
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $kb_google_drive_grant_shared$
DECLARE
    sees_all boolean;
BEGIN
    IF p_email IS NULL OR pg_catalog.btrim(p_email) = '' THEN
        RETURN NULL;   -- cannot tell; the caller skips the revoke
    END IF;

    SELECT r.rolsuper OR r.rolbypassrls
            OR NOT (c.relrowsecurity OR c.relforcerowsecurity)
      INTO sees_all
      FROM pg_catalog.pg_roles r, pg_catalog.pg_class c
     WHERE r.rolname = current_user
       AND c.oid = 'public.integration_connections'::pg_catalog.regclass;

    IF NOT COALESCE(sees_all, false) THEN
        RAISE WARNING
            'kb_google_drive_grant_shared: the owner % cannot see through '
            'row level security on integration_connections, so no revoke runs',
            current_user;
        RETURN NULL;   -- cannot tell; the caller skips the revoke
    END IF;

    RETURN EXISTS (
        SELECT 1 FROM public.integration_connections ic
         WHERE ic.provider IN ('google_drive', 'google_calendar')
           AND ic.status <> 'disconnected'
           AND pg_catalog.lower(ic.provider_meta ->> 'account_email')
               = pg_catalog.lower(pg_catalog.btrim(p_email))
           AND (p_exclude IS NULL OR ic.id <> p_exclude)
    );
END
$kb_google_drive_grant_shared$;

COMMENT ON FUNCTION public.kb_google_drive_grant_shared(text, uuid) IS
    'True when another live google_drive integration_connections row with '
    'this account address exists in any tenant. One bit, never a row. NULL '
    'when the owner cannot see through RLS. WS-61 KC-1a.';

-- ── The 263 function, replaced: a live Drive connection counts too ──────────
--
-- Same name, same arguments, same return type (R6). CREATE OR REPLACE keeps
-- the owner and the grants. The Gmail half is the body of 263, unchanged.
-- The owner must see through RLS on BOTH tables, or the answer is NULL.

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
    -- superuser or a BYPASSRLS owner. Both tables must show every row.
    SELECT pg_catalog.bool_and(r.rolsuper OR r.rolbypassrls
            OR NOT (c.relrowsecurity OR c.relforcerowsecurity))
      INTO sees_all
      FROM pg_catalog.pg_roles r, pg_catalog.pg_class c
     WHERE r.rolname = current_user
       AND c.oid IN ('public.email_accounts'::pg_catalog.regclass,
                     'public.integration_connections'::pg_catalog.regclass);

    IF NOT COALESCE(sees_all, false) THEN
        RAISE WARNING
            'email_gmail_grant_shared: the owner % cannot see through '
            'row level security on email_accounts or integration_connections, '
            'so no revoke runs',
            current_user;
        RETURN NULL;   -- cannot tell; the caller skips the revoke
    END IF;

    RETURN EXISTS (
        SELECT 1 FROM public.email_accounts a
         WHERE a.provider = 'gmail'
           AND pg_catalog.lower(a.email_address) = pg_catalog.lower(p_email)
           AND (p_exclude IS NULL OR a.id <> p_exclude)
    ) OR EXISTS (
        -- WS-61 KC-1a: a live Drive connection of the same Google account
        -- holds the same grant. A Gmail disconnect must not end it.
        SELECT 1 FROM public.integration_connections ic
         WHERE ic.provider IN ('google_drive', 'google_calendar')
           AND ic.status <> 'disconnected'
           AND pg_catalog.lower(ic.provider_meta ->> 'account_email')
               = pg_catalog.lower(pg_catalog.btrim(p_email))
           AND (p_exclude IS NULL OR ic.id <> p_exclude)
    );
END
$email_gmail_grant_shared$;

COMMENT ON FUNCTION public.email_gmail_grant_shared(text, uuid) IS
    'True when another gmail email_accounts row, or a live google_drive '
    'integration_connections row, with this address exists in any tenant. '
    'One bit, never a row. NULL when the owner cannot see through RLS. '
    'WS-17 EM-G11f1 and WS-61 KC-1a.';

