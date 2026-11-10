-- ============================================================================
-- 236_whatsapp_member_link_lookups.sql — the two cross-tenant reads of the
-- WhatsApp assistant channel.
--
-- What: two SECURITY DEFINER read functions over `whatsapp_member_links`.
--       * `whatsapp_member_links_for_phone(wa_id)` gives every ACTIVE link
--         of one phone, as (organization_id, member_email, is_current).
--       * `whatsapp_member_link_for_code(code_hash, wa_id)` gives the row of
--         one link code, as (id, organization_id, member_email, status,
--         code_expires_at). It gives the row only when the row is `pending`
--         and its code has not expired, or when the row is `active` for the
--         SAME phone that asks (a redelivered link message).
-- Why:  WS-47 WAC-2, `project-docs/specs/whatsapp_assistant_channel.md` §5.3
--       and §5.4. The webhook knows no organization when a message arrives,
--       and the table has FORCE row level security (R5). So each read that
--       finds the organization is a SECURITY DEFINER function, granted to
--       `acb_app` only, in the shape of 230_wa_accounts_cloud_number_owner.sql.
-- Depends on: 235_whatsapp_member_links.sql (the table).
--
-- ── The rules the functions hold ────────────────────────────────────────────
--
-- * Neither function writes. The webhook binds the organization that a
--   function returns, and it writes inside `tenant_session` under RLS.
-- * The code function never returns a row of another phone. An active row
--   comes back only when its `wa_id` equals the caller's `wa_id`. A pending
--   row has no `wa_id` yet. A revoked row never comes back.
-- * A function whose owner cannot see through RLS returns no row and raises
--   a WARNING. The channel then fails closed: no link, the failure reply.
--
-- Expand only (R6): two new functions. No table, no column, no index.
-- Idempotent per infra/postgres/README.md: CREATE OR REPLACE, and the grants
-- are the same on each run.
-- Tenant-scoped (R5): the functions read a tenant table that keeps FORCE RLS.
-- They add no table, so they add no `gen_tenant_migration.EXEMPT` row and no
-- connection site.
-- Pinned by tests/unit/test_wac_bot_link_r8.py, which finds this file by
-- CONTENT, never by number (R1).
-- ============================================================================

BEGIN;


-- ── 1. Every active link of one phone ───────────────────────────────────────

CREATE OR REPLACE FUNCTION public.whatsapp_member_links_for_phone(
    p_wa_id text
)
RETURNS TABLE (organization_id uuid, member_email text, is_current boolean)
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $whatsapp_member_links_for_phone$
#variable_conflict use_column
DECLARE
    sees_all boolean;
BEGIN
    IF p_wa_id IS NULL OR p_wa_id = '' THEN
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
       AND c.oid = 'public.whatsapp_member_links'::pg_catalog.regclass;

    IF NOT COALESCE(sees_all, false) THEN
        RAISE WARNING
            'whatsapp_member_links_for_phone: the owner % cannot see through '
            'row level security on whatsapp_member_links, so no phone resolves',
            current_user;
        RETURN;
    END IF;

    RETURN QUERY
        SELECT l.organization_id, l.member_email, l.is_current
          FROM public.whatsapp_member_links l
         WHERE l.wa_id = p_wa_id
           AND l.status = 'active'
         ORDER BY l.is_current DESC, l.linked_at;
END
$whatsapp_member_links_for_phone$;

COMMENT ON FUNCTION public.whatsapp_member_links_for_phone(text) IS
    'WAC-2: every active link of one WhatsApp phone, across every tenant, as '
    '(organization_id, member_email, is_current). No row when the owner '
    'cannot see through RLS. Only acb_app may run it. '
    'whatsapp_assistant_channel.md §5.3.';


-- ── 2. The row of one link code ─────────────────────────────────────────────

CREATE OR REPLACE FUNCTION public.whatsapp_member_link_for_code(
    p_code_hash text,
    p_wa_id text
)
RETURNS TABLE (
    id uuid,
    organization_id uuid,
    member_email text,
    status text,
    code_expires_at timestamptz
)
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $whatsapp_member_link_for_code$
#variable_conflict use_column
DECLARE
    sees_all boolean;
BEGIN
    IF p_code_hash IS NULL OR p_code_hash = ''
       OR p_wa_id IS NULL OR p_wa_id = '' THEN
        RETURN;
    END IF;

    SELECT r.rolsuper OR r.rolbypassrls
            OR NOT (c.relrowsecurity OR c.relforcerowsecurity)
      INTO sees_all
      FROM pg_catalog.pg_roles r, pg_catalog.pg_class c
     WHERE r.rolname = current_user
       AND c.oid = 'public.whatsapp_member_links'::pg_catalog.regclass;

    IF NOT COALESCE(sees_all, false) THEN
        RAISE WARNING
            'whatsapp_member_link_for_code: the owner % cannot see through '
            'row level security on whatsapp_member_links, so no code resolves',
            current_user;
        RETURN;
    END IF;

    -- A pending row: the code is live. An active row: the same phone sent
    -- the same code again, so the caller answers success and changes
    -- nothing. Any other row, and an active row of another phone, is no row.
    RETURN QUERY
        SELECT l.id, l.organization_id, l.member_email, l.status,
               l.code_expires_at
          FROM public.whatsapp_member_links l
         WHERE l.code_hash = p_code_hash
           AND (   (l.status = 'pending' AND l.code_expires_at > pg_catalog.now())
                OR (l.status = 'active' AND l.wa_id = p_wa_id));
END
$whatsapp_member_link_for_code$;

COMMENT ON FUNCTION public.whatsapp_member_link_for_code(text, text) IS
    'WAC-2: the row of one link code, across every tenant. A pending row with '
    'a live code, or an active row of the SAME phone (a redelivery). Never a '
    'row of another phone. Only acb_app may run it. '
    'whatsapp_assistant_channel.md §5.3 and §5.4.';


-- ── 3. Only acb_app may run them ────────────────────────────────────────────

REVOKE ALL ON FUNCTION public.whatsapp_member_links_for_phone(text) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.whatsapp_member_link_for_code(text, text)
    FROM PUBLIC;

DO $grants$
DECLARE
    r text;
    f text;
BEGIN
    FOREACH f IN ARRAY ARRAY[
        'public.whatsapp_member_links_for_phone(text)',
        'public.whatsapp_member_link_for_code(text, text)'
    ] LOOP
        -- Supabase's default privileges hand every new function to these.
        FOREACH r IN ARRAY ARRAY['anon', 'authenticated', 'service_role'] LOOP
            IF EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = r) THEN
                EXECUTE format('REVOKE ALL ON FUNCTION %s FROM %I', f, r);
            END IF;
        END LOOP;
        -- The application role of saas_multitenancy_handover.md §H3.
        IF EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = 'acb_app')
        THEN
            EXECUTE format('GRANT EXECUTE ON FUNCTION %s TO acb_app', f);
        END IF;
    END LOOP;
END
$grants$;

COMMIT;
