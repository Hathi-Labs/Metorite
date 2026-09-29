-- 222_chat_session_exists.sql — does a chat session id exist in ANY tenant?
--
-- What: one function, `chat_session_exists(p_id text) RETURNS boolean`. It
--       answers only whether a `chat_session` row with that id exists,
--       across every tenant. It never returns the row, the owner or the
--       organization id.
-- Why:  spec `project-docs/specs/projects_ai_chat.md` §21.11 · board WS-27bm
--       slice S15, fix round 1 (the reviewer's P0). S15 bound `_load_room`
--       to the caller's tenant. Under FORCE RLS a session of ANOTHER tenant
--       then reads as "no row", and "no row" meant "a new thread, yours", so
--       the caller got the owner role. The stream relay keys runs by the bare
--       thread id. So a member of org A who held an id of org B could follow,
--       cancel and steer B's run, and attach rows to B's session id (the
--       `chat_message` foreign key ignores RLS). The gateway now asks this
--       function whenever its bound read finds nothing. When the id exists
--       elsewhere, the gateway denies the room and declines the write.
-- Depends on: 02_chat_history.sql (`chat_session`). Works on the ladder
--       shape (no RLS) and on the tenancy shape (generated/01..04). Idempotent.
--
-- ── Why SECURITY DEFINER, and why it is safe here ───────────────────────────
--
-- 179 and 185 refused SECURITY DEFINER on purpose, because a DEFINER function
-- that WRITES can outlive its reason. This one only reads, and it answers
-- one bit. It is the first DEFINER function in the ladder, so it holds four
-- rules:
--
--   * It returns a boolean, never a row. An org id or an owner leaks nothing
--     when it never leaves the function. A session id is a random UUID, so
--     the bit cannot enumerate sessions.
--   * `SET search_path = pg_catalog, pg_temp`, and every name is qualified
--     with `public.`. So a caller cannot plant a `chat_session` of its own in
--     a schema that it controls.
--   * FORCE RLS binds the OWNER too, unless it is a superuser or BYPASSRLS
--     (see 221). A DEFINER function owned by such a role would see only the
--     caller's rows, and answer "absent" for another tenant's id. That is the
--     exact hole this file closes. So when the owner cannot bypass RLS and
--     the table has RLS, the function returns NULL, and the gateway reads
--     NULL as "cannot tell" and denies. Measured on production 2026-09-29:
--     the runner role `postgres` owns `chat_session` with rolbypassrls = t.
--   * EXECUTE goes to the app role only. Supabase gives a new function to
--     `anon`, `authenticated` and `service_role` by default privileges, and
--     PostgREST publishes it as an RPC. So the file revokes it from PUBLIC
--     and from those three by name, and grants it to `acb_app` when that
--     role exists. A superuser or the owner can always run it.
--
-- Fences: `tests/unit/test_chat_write_under_rls.py` (FORCE RLS, as a role
-- that is not a superuser, not the owner and not BYPASSRLS) and
-- `tests/unit/test_rooms.py` (the ladder shape).

SET lock_timeout = '5s';

CREATE OR REPLACE FUNCTION public.chat_session_exists(p_id text)
RETURNS boolean
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $chat_session_exists$
DECLARE
    sees_all boolean;
BEGIN
    IF p_id IS NULL OR p_id = '' THEN
        RETURN false;
    END IF;

    -- current_user is the OWNER inside a DEFINER function. A table with no
    -- RLS shows every row to it. A table with RLS shows every row only to a
    -- superuser or a BYPASSRLS owner.
    SELECT r.rolsuper OR r.rolbypassrls
            OR NOT (c.relrowsecurity OR c.relforcerowsecurity)
      INTO sees_all
      FROM pg_catalog.pg_roles r, pg_catalog.pg_class c
     WHERE r.rolname = current_user
       AND c.oid = 'public.chat_session'::pg_catalog.regclass;

    IF NOT COALESCE(sees_all, false) THEN
        RETURN NULL;   -- cannot tell; the caller denies
    END IF;

    RETURN EXISTS (SELECT 1 FROM public.chat_session WHERE id = p_id);
END
$chat_session_exists$;

COMMENT ON FUNCTION public.chat_session_exists(text) IS
    'True when a chat_session with this id exists in any tenant. Returns one '
    'bit, never a row. NULL when the owner cannot see through RLS. WS-27bm '
    'S15, projects_ai_chat.md §21.11.';

REVOKE ALL ON FUNCTION public.chat_session_exists(text) FROM PUBLIC;

DO $$
DECLARE
    r text;
BEGIN
    -- Supabase's default privileges hand every new function to these roles.
    FOREACH r IN ARRAY ARRAY['anon', 'authenticated', 'service_role'] LOOP
        IF EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = r) THEN
            EXECUTE format(
                'REVOKE ALL ON FUNCTION public.chat_session_exists(text) FROM %I', r);
        END IF;
    END LOOP;
    -- The application role of saas_multitenancy_handover.md §H3.
    IF EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = 'acb_app') THEN
        GRANT EXECUTE ON FUNCTION public.chat_session_exists(text) TO acb_app;
    END IF;
END
$$;
