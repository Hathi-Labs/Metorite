-- ============================================================================
-- 282_github_app.sql — the columns, the index and the lookup of the Metorite
-- GitHub App.
--
-- What: (1) `pm_task_refs` gains eight nullable columns: `head_sha`,
--       `head_ref`, `checks_state`, `checks_summary`, `checks_fetched_at`,
--       `pr_updated_at`, `merged_at` and `unreadable_since`. (2) The CHECK of `pm_task_refs.via`
--       takes one more value, `github`. (3) Two indexes on `pm_task_refs` for
--       the webhook reads. (4) One partial unique index on
--       `integration_connections`, so one GitHub installation maps to one
--       organization, after a pre-check that names the duplicates. (5) One
--       SECURITY DEFINER function, `github_installation_org(bigint)`. It
--       returns the organization of an installation, across every tenant.
-- Why:  spec `project-docs/specs/github_integration.md` §9.1 to §9.3 and
--       §8.3, slice GH-2 · board WS-54. GitHub calls `POST /webhooks/github`
--       with no member session, so no tenant is bound. The route cannot bind
--       the tenant first, because the tenant is the answer to this lookup.
--       This file copies the shape of 230 (`wa_account_for_phone_number_id`).
--
-- **Expand only (R6).** Every new column is nullable with no default, so the
-- code that runs now meets the new schema. The CHECK of `via` only gets
-- WIDER: every value that the old CHECK took, the new one takes. It is added
-- NOT VALID and validated in its own guarded block, as 148 does. No column is
-- renamed and nothing is dropped except the old CHECK, in the same step that
-- adds its wider copy.
--
-- ── Why SECURITY DEFINER, and why it is safe here ───────────────────────────
--
-- It holds the rules of 230:
--
--   * It returns an ORGANIZATION ID. The gateway can already bind any tenant
--     (`bind_tenant`), so the id gives the gateway nothing that it could not
--     take. The webhook route verifies GitHub's HMAC signature BEFORE it calls
--     this function, and it never returns the id to GitHub.
--   * `SET search_path = pg_catalog, pg_temp`, and every name is qualified
--     with `public.`.
--   * FORCE RLS binds the OWNER too, unless it is a superuser or BYPASSRLS. An
--     owner that cannot see through RLS returns no row and raises a WARNING
--     into the server log.
--   * EXECUTE goes to `acb_app` only. The file revokes it from PUBLIC and from
--     `anon`, `authenticated` and `service_role` by name.
--   * plpgsql, not LANGUAGE sql, for the reason that 230 gives: a plpgsql body
--     resolves its names when it runs.
--
-- **One installation, one org (§9.3).** A unique index holds ACROSS tenants
-- under RLS, so org B cannot take the installation of org A. A row in
-- `disconnected` leaves the index, so an org that disconnected frees the
-- installation for another org.
--
-- Depends on: 245_integration_connections.sql (`integration_connections`),
--       255_pm_task_refs.sql (`pm_task_refs`). Idempotent: ADD COLUMN IF NOT
--       EXISTS, DROP CONSTRAINT IF EXISTS before each ADD CONSTRAINT, a
--       pre-check that passes on a replay, CREATE INDEX IF NOT EXISTS, CREATE
--       OR REPLACE FUNCTION. No new table, so `gen_tenant_migration.py` and its
--       EXEMPT list do not change. No CONCURRENTLY: both tables are small, and
--       a failed concurrent build leaves an INVALID index behind.
--
-- Fences: `tests/unit/test_github_migration_r8.py` (R8, as a role that is not
-- a superuser, not the owner and not BYPASSRLS).
-- ============================================================================

SET lock_timeout = '5s';

BEGIN;

-- ── 1. Eight columns on pm_task_refs (§9.1) ─────────────────────────────────

ALTER TABLE pm_task_refs
    ADD COLUMN IF NOT EXISTS head_sha TEXT NULL
        CONSTRAINT pm_task_refs_head_sha_shape
        CHECK (head_sha IS NULL OR head_sha ~ '^[0-9a-f]{40}$'),
    ADD COLUMN IF NOT EXISTS head_ref TEXT NULL
        CONSTRAINT pm_task_refs_head_ref_length
        CHECK (head_ref IS NULL OR char_length(head_ref) <= 255),
    ADD COLUMN IF NOT EXISTS checks_state TEXT NULL
        CONSTRAINT pm_task_refs_checks_state_known
        CHECK (checks_state IS NULL
               OR checks_state IN ('pending', 'passing', 'failing', 'none')),
    ADD COLUMN IF NOT EXISTS checks_summary JSONB NULL,
    ADD COLUMN IF NOT EXISTS checks_fetched_at TIMESTAMPTZ NULL,
    ADD COLUMN IF NOT EXISTS pr_updated_at TIMESTAMPTZ NULL,
    ADD COLUMN IF NOT EXISTS merged_at TIMESTAMPTZ NULL,
    ADD COLUMN IF NOT EXISTS unreadable_since TIMESTAMPTZ NULL;

COMMENT ON COLUMN pm_task_refs.head_sha IS
    'GH-2: the head commit of the pull request. The checks read writes only '
    'when this still equals its sha. github_integration.md §8.5, §9.1.';
COMMENT ON COLUMN pm_task_refs.checks_state IS
    'GH-2: the folded CI state, pending, passing, failing or none. NULL means '
    'never read. github_integration.md §8.5.';
COMMENT ON COLUMN pm_task_refs.pr_updated_at IS
    'GH-2: GitHub''s updated_at of the pull request. An older event writes '
    'nothing (github_integration.md §8.6).';
COMMENT ON COLUMN pm_task_refs.unreadable_since IS
    'GH-6: when an App read of this pull request was first refused (404, 410, '
    '403 or 301). A good read clears it. A ref refused for an hour stops '
    'blocking merge-to-Done (github_integration.md §12.3 step 4).';


-- ── 2. `via` takes `github` (§9.2) ──────────────────────────────────────────
--
-- 255 wrote the CHECK inline, so Postgres named it `pm_task_refs_via_check`.
-- The new CHECK holds every old value. NOT VALID adds it with no scan, and the
-- block below promotes it.

ALTER TABLE pm_task_refs DROP CONSTRAINT IF EXISTS pm_task_refs_via_check;
ALTER TABLE pm_task_refs ADD CONSTRAINT pm_task_refs_via_check
    CHECK (via ~ '^(ui|chat|text|github|mcp:[a-z0-9:_.-]{1,60})$') NOT VALID;

DO $validate_via$
BEGIN
    ALTER TABLE pm_task_refs VALIDATE CONSTRAINT pm_task_refs_via_check;
EXCEPTION WHEN check_violation THEN
    -- No old row can fail a wider CHECK. If one does, the constraint still
    -- refuses every new bad row, and the notice says why it stays NOT VALID.
    RAISE NOTICE 'pm_task_refs_via_check left NOT VALID: a row carries a via '
                 'outside the vocabulary';
END
$validate_via$;


-- ── 3. Two indexes for the webhook reads (§9.2) ─────────────────────────────

CREATE INDEX IF NOT EXISTS pm_task_refs_provider_ref
    ON pm_task_refs (organization_id, provider, ref);

CREATE INDEX IF NOT EXISTS pm_task_refs_head_sha
    ON pm_task_refs (organization_id, head_sha)
    WHERE head_sha IS NOT NULL;


-- ── 4. No installation may be live in two orgs before the index (§9.3) ──────
--
-- The migration runner is the owner, and it sees every row. A duplicate is a
-- real fault: a webhook for that installation has two owners.

DO $precheck$
DECLARE
    dup_installations bigint;
    dup_rows          bigint;
BEGIN
    SELECT count(*), COALESCE(sum(n), 0)
      INTO dup_installations, dup_rows
      FROM (SELECT count(*) AS n
              FROM public.integration_connections
             WHERE provider = 'github'
               AND provider_meta ? 'installation_id'
               AND status <> 'disconnected'
             GROUP BY provider_meta->>'installation_id'
            HAVING count(*) > 1) d;

    IF dup_installations > 0 THEN
        RAISE EXCEPTION
            '282: % GitHub installation id(s) are connected more than once '
            '(% rows). Each webhook for such an installation has two owners. '
            'Set every extra row to disconnected, then apply 282 again.',
            dup_installations, dup_rows;
    END IF;
END
$precheck$;

CREATE UNIQUE INDEX IF NOT EXISTS integration_connections_github_installation
    ON integration_connections ((provider_meta->>'installation_id'))
    WHERE provider = 'github'
      AND provider_meta ? 'installation_id'
      AND status <> 'disconnected';

COMMENT ON INDEX integration_connections_github_installation IS
    'GH-2: one live GitHub installation maps to one organization, across '
    'every tenant. A disconnected row leaves the index. '
    'github_integration.md §9.3.';


-- ── 5. Which org owns a GitHub installation (§8.3) ──────────────────────────

CREATE OR REPLACE FUNCTION public.github_installation_org(
    p_installation_id bigint
)
RETURNS TABLE (organization_id uuid)
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $github_installation_org$
#variable_conflict use_column
DECLARE
    sees_all boolean;
BEGIN
    IF p_installation_id IS NULL OR p_installation_id <= 0 THEN
        RETURN;
    END IF;

    -- current_user is the OWNER inside a DEFINER function. A table with RLS
    -- shows every row only to a superuser or a BYPASSRLS owner.
    SELECT r.rolsuper OR r.rolbypassrls
            OR NOT (c.relrowsecurity OR c.relforcerowsecurity)
      INTO sees_all
      FROM pg_catalog.pg_roles r, pg_catalog.pg_class c
     WHERE r.rolname = current_user
       AND c.oid = 'public.integration_connections'::pg_catalog.regclass;

    IF NOT COALESCE(sees_all, false) THEN
        RAISE WARNING
            'github_installation_org: the owner % cannot see through row '
            'level security on integration_connections, so no installation '
            'resolves', current_user;
        RETURN;
    END IF;

    RETURN QUERY
        SELECT c.organization_id
          FROM public.integration_connections c
         WHERE c.provider = 'github'
           AND c.provider_meta ? 'installation_id'
           AND c.status <> 'disconnected'
           AND c.provider_meta->>'installation_id' = p_installation_id::text;
END
$github_installation_org$;

COMMENT ON FUNCTION public.github_installation_org(bigint) IS
    'The organization of a live GitHub installation, across every tenant. No '
    'row for an unknown or disconnected installation, or when the owner '
    'cannot see through RLS. Only acb_app may run it. WS-54 GH-2, '
    'github_integration.md §8.3.';

REVOKE ALL ON FUNCTION public.github_installation_org(bigint) FROM PUBLIC;

DO $grants$
DECLARE
    r text;
BEGIN
    -- Supabase's default privileges hand every new function to these roles.
    FOREACH r IN ARRAY ARRAY['anon', 'authenticated', 'service_role'] LOOP
        IF EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = r) THEN
            EXECUTE format(
                'REVOKE ALL ON FUNCTION '
                'public.github_installation_org(bigint) FROM %I', r);
        END IF;
    END LOOP;
    -- The application role of saas_multitenancy_handover.md §H3.
    IF EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = 'acb_app') THEN
        GRANT EXECUTE ON FUNCTION public.github_installation_org(bigint)
            TO acb_app;
    END IF;
END
$grants$;

COMMIT;
