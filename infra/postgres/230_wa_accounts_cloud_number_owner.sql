-- ============================================================================
-- 230_wa_accounts_cloud_number_owner.sql — one Cloud API number, one owner,
-- and the read that tells the webhook which tenant owns it.
--
-- What: (1) a partial unique index on `wa_accounts (phone_number_id)` for the
--       rows with `provider = 'cloud_api'`, after a pre-check that refuses
--       the migration when duplicates exist. (2) one SECURITY DEFINER function,
--       `wa_account_for_phone_number_id(p_phone_number_id text)`. It returns
--       `(account_id, organization_id)` for the `cloud_api` row with that
--       Meta phone number id, across every tenant.
-- Why:  spec `project-docs/specs/whatsapp_message_manager.md` §12.3 F1 and
--       F7, §12.4 slice WA-C1 · board WS-20.
--
-- **F1, the dropped batch.** Meta calls `POST /whatsapp/webhook` with no
-- member session, so no tenant is bound. Production runs every `wa_*` table
-- with FORCE RLS. The old route read `wa_accounts` on an unbound session, saw
-- no row, logged `whatsapp.webhook.unknown_number` and answered 200. So every
-- inbound batch was dropped with no error. The route cannot bind the tenant
-- first, because the tenant is the answer to this read. This function gives
-- that answer, and the route then binds it.
--
-- **F7, the shared number.** Migration 102 made a number unique per member,
-- `UNIQUE (user_id, phone_number_id)`. Two members, in one organization or in
-- two, can connect one number. The webhook then has two owners for one batch.
-- The index below makes a Cloud API number unique on the platform. The
-- whatsmeow rows keep their synthetic `bridge-<uuid>` ids and stay out of it.
--
-- ── Why SECURITY DEFINER, and why it is safe here ───────────────────────────
--
-- It holds the four rules of 222 (`chat_session_exists`), and it breaks one
-- on purpose:
--
--   * It returns an ORGANIZATION ID, which 222's one-bit rule forbids. The
--     reason: the gateway can already bind any tenant (`bind_tenant`), so
--     the org id gives the gateway nothing it could not take. The caller is
--     the gateway alone. The webhook route verifies Meta's HMAC signature
--     before it calls this function, and it never returns the org id to Meta.
--   * `SET search_path = pg_catalog, pg_temp`, and every name is qualified
--     with `public.`. So a caller cannot plant a `wa_accounts` of its own in
--     a schema that it controls.
--   * FORCE RLS binds the OWNER too, unless it is a superuser or BYPASSRLS
--     (see 221 and 222). An owner that cannot see through RLS would answer
--     "no such number" for every tenant but its own. So the function then
--     returns no row and raises a WARNING into the server log. Measured on
--     production 2026-09-29: the runner role `postgres` owns the tables with
--     rolbypassrls = t.
--   * EXECUTE goes to `acb_app` only. Supabase gives a new function to
--     `anon`, `authenticated` and `service_role` by default privileges, and
--     PostgREST publishes it as an RPC. So the file revokes it from PUBLIC
--     and from those three by name.
--
-- **plpgsql, not LANGUAGE sql.** The ladder never applies `generated/`, so
-- `wa_accounts.organization_id` does not exist on a fresh ladder database. A
-- LANGUAGE sql body is checked when the function is created, and it fails
-- there. A plpgsql body resolves its names when it runs. On the ladder shape
-- a call raises `undefined_column`. That is loud, and the ladder shape has
-- no tenant to bind in any case.
--
-- Depends on: 102_whatsapp.sql (`wa_accounts`), 112_whatsapp_provider.sql
--       (`provider`). Works on the ladder shape and on the tenancy shape
--       (generated/01..04). Idempotent: a pre-check that passes on a replay,
--       CREATE UNIQUE INDEX IF NOT EXISTS, CREATE OR REPLACE FUNCTION.
-- No CONCURRENTLY: the table is small, and a failed concurrent build leaves
-- an INVALID index behind.
--
-- Fences: `tests/unit/test_whatsapp_webhook_under_rls.py` (FORCE RLS, as a
-- role that is not a superuser, not the owner and not BYPASSRLS).
-- ============================================================================

SET lock_timeout = '5s';

BEGIN;

-- ── 1. No duplicate Cloud API number may exist before the index ─────────────
--
-- The migration runner is the owner, and it sees every row. A duplicate is
-- a real fault: a batch for that number has two owners. The index build would
-- fail on it with a bare unique violation, so this names the count first.
-- Remove the extra rows by hand, then apply the migration again.

DO $precheck$
DECLARE
    dup_numbers bigint;
    dup_rows    bigint;
BEGIN
    SELECT count(*), COALESCE(sum(n), 0)
      INTO dup_numbers, dup_rows
      FROM (SELECT count(*) AS n
              FROM public.wa_accounts
             WHERE provider = 'cloud_api'
             GROUP BY phone_number_id
            HAVING count(*) > 1) d;

    IF dup_numbers > 0 THEN
        RAISE EXCEPTION
            '230: % Cloud API phone_number_id value(s) are connected more than '
            'once (% rows). Each webhook batch for such a number has two '
            'owners. Keep one wa_accounts row for each number, then apply '
            '230 again.', dup_numbers, dup_rows;
    END IF;
END
$precheck$;


-- ── 2. One Cloud API number on the platform ─────────────────────────────────

CREATE UNIQUE INDEX IF NOT EXISTS uq_wa_accounts_cloud_phone_number_id
    ON wa_accounts (phone_number_id)
    WHERE provider = 'cloud_api';

COMMENT ON INDEX uq_wa_accounts_cloud_phone_number_id IS
    'WA-C1 (F7): a Cloud API number connects once on the platform, so a '
    'webhook batch has one owner. Whatsmeow rows carry synthetic ids and stay '
    'out of it.';


-- ── 3. Which account and which tenant own a Cloud API number ────────────────

CREATE OR REPLACE FUNCTION public.wa_account_for_phone_number_id(
    p_phone_number_id text
)
RETURNS TABLE (account_id uuid, organization_id uuid)
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $wa_account_for_phone_number_id$
#variable_conflict use_column
DECLARE
    sees_all boolean;
BEGIN
    IF p_phone_number_id IS NULL OR p_phone_number_id = '' THEN
        RETURN;
    END IF;

    -- current_user is the OWNER inside a DEFINER function. A table with no
    -- RLS shows every row to it. A table with RLS shows every row only to a
    -- superuser or a BYPASSRLS owner.
    SELECT r.rolsuper OR r.rolbypassrls
            OR NOT (c.relrowsecurity OR c.relforcerowsecurity)
      INTO sees_all
      FROM pg_catalog.pg_roles r, pg_catalog.pg_class c
     WHERE r.rolname = current_user
       AND c.oid = 'public.wa_accounts'::pg_catalog.regclass;

    IF NOT COALESCE(sees_all, false) THEN
        RAISE WARNING
            'wa_account_for_phone_number_id: the owner % cannot see through '
            'row level security on wa_accounts, so no number resolves',
            current_user;
        RETURN;
    END IF;

    RETURN QUERY
        SELECT a.id, a.organization_id
          FROM public.wa_accounts a
         WHERE a.phone_number_id = p_phone_number_id
           AND a.provider = 'cloud_api';
END
$wa_account_for_phone_number_id$;

COMMENT ON FUNCTION public.wa_account_for_phone_number_id(text) IS
    'The Cloud API account and its tenant for a Meta phone_number_id, across '
    'every tenant. No row when the owner cannot see through RLS. Only acb_app '
    'may run it. WS-20 WA-C1, whatsapp_message_manager.md §12.4.';

REVOKE ALL ON FUNCTION public.wa_account_for_phone_number_id(text) FROM PUBLIC;

DO $grants$
DECLARE
    r text;
BEGIN
    -- Supabase's default privileges hand every new function to these roles.
    FOREACH r IN ARRAY ARRAY['anon', 'authenticated', 'service_role'] LOOP
        IF EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = r) THEN
            EXECUTE format(
                'REVOKE ALL ON FUNCTION '
                'public.wa_account_for_phone_number_id(text) FROM %I', r);
        END IF;
    END LOOP;
    -- The application role of saas_multitenancy_handover.md §H3.
    IF EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = 'acb_app') THEN
        GRANT EXECUTE ON FUNCTION public.wa_account_for_phone_number_id(text)
            TO acb_app;
    END IF;
END
$grants$;

COMMIT;
