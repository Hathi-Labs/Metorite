-- ============================================================================
-- 466_failure_store.sql — the one failure store (WS-TOF TOF-2b, task 3543).
--
-- What: five tables and five functions.
--       Tenant tables, each with the tenant `organization_id` and FORCE row
--       level security:
--         failure_events    one row for each recorded failure, up to the
--                           sample cap. It holds no argument, no result and
--                           no exception message.
--         failure_org_days  one row for each tenant, fingerprint and UTC day.
--                           The band of organizations comes from it.
--       Platform tables, with no organization id and no tenant text:
--         failure_fingerprints  one row for each fingerprint: its four code
--                               fields, its counts and the state of its task.
--         failure_daily         the counts of one fingerprint on one day.
--         failure_config        one row that names the internal org.
--       Functions, each SECURITY DEFINER with a fixed search_path:
--         failure_tally         the count of the bound tenant (every tenant).
--         failure_org_band      the band of organizations of a fingerprint.
--         failure_due           the platform rows that the sync job reads.
--         failure_set           the state columns of one fingerprint.
--         failure_prune_daily   the delete of old failure_daily rows.
--       The last four run for the internal org only.
-- Why:  project-docs/specs/tool_failure_log.md §5 and §6 (D225). A failed
--       tool call writes one row in its own tenant, and adds one count to a
--       platform row. Only counts and code facts cross a tenant (§10.1).
-- Depends on: 130_org_access_control.sql (organization).
--
-- ── Why the platform tables are safe (§5.2, §10) ────────────────────────────
--
--   1. No app role touches them. The file revokes every table privilege from
--      PUBLIC, from the three Supabase roles and from `acb_app`, as 276 does.
--      The five functions are the only way in.
--   2. A platform row holds no organization id and no tenant text. Each text
--      column has a CHECK on the grammar of §3.2, so the database refuses a
--      name, a message or a path that the normaliser would never make.
--   3. `failure_tally` reads the tenant from `app.tenant_id`, never from an
--      argument (R5 e). With no tenant it raises. It computes the v1 hash
--      again from the four fields and refuses a mismatch, so no caller can
--      attach a made-up fingerprint. A CHECK on the table holds the same
--      rule.
--   4. The four job functions raise unless `app.tenant_id` equals
--      `failure_config.internal_org`, and they raise while the table has no
--      row. A session of any other tenant gets a refusal from the database.
--   5. `failure_org_band` returns a count only at 3 or more, `<3` for 1 or 2,
--      and `0` for none, over a fixed window of 30 days. It returns NULL when
--      its owner cannot see every row of `failure_org_days`, as 222 does.
--   6. No function deletes a tenant row. An org purge cascades the tenant
--      rows, and the platform rows hold nothing to remove.
--
-- **The owner and RLS.** FORCE RLS binds the table owner too, unless it is a
-- superuser or BYPASSRLS. `failure_tally` writes `failure_org_days` for the
-- bound tenant only, so the WITH CHECK holds either way.
--
-- **The counts are atomic.** Each count is one INSERT ... ON CONFLICT DO
-- UPDATE, so two tallies at once of one new fingerprint count 2. A count at
-- the top of BIGINT stays there and never raises.
--
-- Grants follow 263 and 276: REVOKE ALL from PUBLIC and the three Supabase
-- roles by name, and EXECUTE to `acb_app` only, when the role exists.
--
-- Expand only (R6): five new tables, their indexes, five new functions. No
-- rename, and no change of an old object. The one DROP is of this file's own
-- `failure_due`, before it is made again (its return type grew in TOF-2e).
-- Old code names no new object. Idempotent: CREATE TABLE IF NOT EXISTS, ADD
-- COLUMN IF NOT EXISTS, CREATE INDEX IF NOT EXISTS, CREATE OR REPLACE
-- FUNCTION, DROP FUNCTION IF EXISTS, an RLS block guarded on
-- relforcerowsecurity, and REVOKE and GRANT, which a second run repeats with
-- no change. The foreign keys to `organization` take a SHARE ROW EXCLUSIVE
-- lock on it until COMMIT, so the file sets `lock_timeout` first.
-- `REFERENCES` comes before `DEFAULT`, because the tenancy ratchet in
-- tests/unit/test_tenancy_boundary.py matches the two with no comma between.
--
-- The number is taken at build time (R1): 466 was booked for task 3543 (it was 462 until 2026-11-20, below the ledger of main) with
-- ~/lanes/reserve_migration.sh. Check it again at merge.
-- Fences: tests/unit/test_failure_store_r8.py (R8: P3, P4, P5) and
-- tests/unit/test_tenant_coverage.py (P8), which find this file by CONTENT,
-- never by number (R1).
-- ============================================================================

BEGIN;

SET LOCAL lock_timeout = '5s';

-- ── Tenant tables (§5.1) ────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS failure_events (
    id               UUID NOT NULL DEFAULT gen_random_uuid(),
    organization_id  UUID NOT NULL
                     REFERENCES organization (id) ON DELETE CASCADE
                     DEFAULT current_setting('app.tenant_id', true)::uuid,
    occurred_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    fingerprint      TEXT NOT NULL,
    source           TEXT NOT NULL,
    surface          TEXT NOT NULL,
    error_code       TEXT NOT NULL,
    -- The platform fields of §3.
    tool             TEXT NOT NULL,
    exc_type         TEXT NOT NULL,
    code_site        TEXT NOT NULL,
    -- The real name when `tool` is a placeholder. Tenant text.
    tool_name        TEXT,
    -- Pointers into agent_run, chat_message and the request log.
    member_email     TEXT,
    run_id           TEXT,
    session_id       TEXT,
    request_id       TEXT,
    model            TEXT,
    release          TEXT,
    -- TOF-2g sets it when a member sends a report.
    report_id        UUID,
    PRIMARY KEY (id),
    CONSTRAINT failure_events_fingerprint CHECK (fingerprint ~ '^[0-9a-f]{16}$'),
    CONSTRAINT failure_events_source CHECK (
        source IN ('chat_tool', 'mcp_call', 'door_5xx', 'issue_report', 'sandbox')),
    CONSTRAINT failure_events_surface CHECK (
        surface IN ('chat', 'whatsapp', 'projects_assistant', 'automation', 'mcp_edge',
                    'studio', 'app', 'other')),
    CONSTRAINT failure_events_error_code CHECK (
        error_code IN ('exception', 'bad_arguments', 'unknown_tool', 'auth_401',
                       'unhandled_500', 'route_500', 'route_upstream',
                       'gateway_unreachable', 'outside_unreachable', 'mcp_exception',
                       'mcp_timeout', 'mcp_unreachable', 'mcp_refused', 'mcp_too_large',
                       'mcp_tool_error')),
    CONSTRAINT failure_events_tool CHECK (
        tool ~ '^[a-z][a-z0-9_]{0,79}$'
        OR tool ~ '^route:(other|(GET|HEAD|POST|PUT|PATCH|DELETE|OPTIONS) /[A-Za-z0-9_./{}:-]{0,200})$'),
    CONSTRAINT failure_events_exc_type CHECK (
        exc_type = '-' OR exc_type ~ '^[A-Za-z_][A-Za-z0-9_.]{0,120}$'),
    CONSTRAINT failure_events_code_site CHECK (
        code_site = '-'
        OR code_site ~ '^[A-Za-z_][A-Za-z0-9_.]{0,150}:[A-Za-z_][A-Za-z0-9_]{0,80}$'),
    CONSTRAINT failure_events_text_lengths CHECK (
        coalesce(char_length(tool_name), 0) <= 200
        AND coalesce(char_length(member_email), 0) <= 320
        AND coalesce(char_length(run_id), 0) <= 200
        AND coalesce(char_length(session_id), 0) <= 200
        AND coalesce(char_length(request_id), 0) <= 200
        AND coalesce(char_length(model), 0) <= 200),
    CONSTRAINT failure_events_release CHECK (release IS NULL OR release ~ '^[0-9a-f]{7,40}$')
);

CREATE INDEX IF NOT EXISTS idx_failure_events_org_time
    ON failure_events (organization_id, occurred_at);
CREATE INDEX IF NOT EXISTS idx_failure_events_org_fp
    ON failure_events (organization_id, fingerprint, occurred_at);
CREATE INDEX IF NOT EXISTS idx_failure_events_org_member
    ON failure_events (organization_id, member_email, occurred_at);

CREATE TABLE IF NOT EXISTS failure_org_days (
    organization_id  UUID NOT NULL
                     REFERENCES organization (id) ON DELETE CASCADE
                     DEFAULT current_setting('app.tenant_id', true)::uuid,
    fingerprint      TEXT NOT NULL,
    -- The UTC day.
    day              DATE NOT NULL,
    PRIMARY KEY (organization_id, fingerprint, day),
    CONSTRAINT failure_org_days_fingerprint CHECK (fingerprint ~ '^[0-9a-f]{16}$')
);

-- The band reads one fingerprint over a window of days.
CREATE INDEX IF NOT EXISTS idx_failure_org_days_fp_day
    ON failure_org_days (fingerprint, day);

DO $rls$
DECLARE
    t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY['failure_events', 'failure_org_days'] LOOP
        IF NOT EXISTS (
            SELECT 1 FROM pg_class
             WHERE oid = to_regclass(t)
               AND relrowsecurity
               AND relforcerowsecurity
        ) THEN
            EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
            EXECUTE format('ALTER TABLE %I FORCE  ROW LEVEL SECURITY', t);
            EXECUTE format('DROP POLICY IF EXISTS %I ON %I', t || '_tenant_isolation', t);
            EXECUTE format(
                'CREATE POLICY %I ON %I '
                'USING      (organization_id = current_setting(''app.tenant_id'', true)::uuid) '
                'WITH CHECK (organization_id = current_setting(''app.tenant_id'', true)::uuid)',
                t || '_tenant_isolation', t);
        END IF;
    END LOOP;
END
$rls$;

COMMENT ON TABLE failure_events IS
    'One recorded tool failure of one org, up to the sample cap (WS-TOF TOF-2b, '
    'tool_failure_log.md §5.1). No argument, result or message. RLS covers the org. '
    'Only acb_common.failure_log writes it.';
COMMENT ON TABLE failure_org_days IS
    'One row for each org, fingerprint and UTC day (tool_failure_log.md §5.1). '
    'failure_org_band counts the orgs from it. RLS covers the org.';

-- ── Platform tables (§5.2) ──────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS failure_fingerprints (
    fingerprint    TEXT PRIMARY KEY,
    fp_version     TEXT NOT NULL DEFAULT 'v1',
    tool           TEXT NOT NULL,
    error_code     TEXT NOT NULL,
    exc_type       TEXT NOT NULL,
    code_site      TEXT NOT NULL,
    sources        TEXT[] NOT NULL DEFAULT '{}',
    surfaces       TEXT[] NOT NULL DEFAULT '{}',
    -- Cut to the UTC hour.
    first_seen_at  TIMESTAMPTZ NOT NULL,
    last_seen_at   TIMESTAMPTZ NOT NULL,
    first_release  TEXT,
    last_release   TEXT,
    -- The hour of the newest count that named a release (TOF-2e). A count
    -- with no release moves `last_seen_at` only, so it never reads as a
    -- count of `last_release` (§7.4: a NULL release never holds the fix).
    last_release_at TIMESTAMPTZ,
    total          BIGINT NOT NULL DEFAULT 0,
    state          TEXT NOT NULL DEFAULT 'new',
    -- A task of the internal org. No foreign key: pm_tasks is a tenant table.
    task_id        UUID,
    fix_merged_at  TIMESTAMPTZ,
    fix_live_at    TIMESTAMPTZ,
    closed_at      TIMESTAMPTZ,
    fix_release    TEXT,
    reopened       INTEGER NOT NULL DEFAULT 0,
    -- The total that the task shows now.
    shown_total    BIGINT NOT NULL DEFAULT 0,
    CONSTRAINT failure_fingerprints_fingerprint CHECK (fingerprint ~ '^[0-9a-f]{16}$'),
    CONSTRAINT failure_fingerprints_version CHECK (fp_version ~ '^v[0-9]{1,3}$'),
    CONSTRAINT failure_fingerprints_tool CHECK (
        tool ~ '^[a-z][a-z0-9_]{0,79}$'
        OR tool ~ '^route:(other|(GET|HEAD|POST|PUT|PATCH|DELETE|OPTIONS) /[A-Za-z0-9_./{}:-]{0,200})$'),
    CONSTRAINT failure_fingerprints_error_code CHECK (
        error_code IN ('exception', 'bad_arguments', 'unknown_tool', 'auth_401',
                       'unhandled_500', 'route_500', 'route_upstream',
                       'gateway_unreachable', 'outside_unreachable', 'mcp_exception',
                       'mcp_timeout', 'mcp_unreachable', 'mcp_refused', 'mcp_too_large',
                       'mcp_tool_error')),
    CONSTRAINT failure_fingerprints_exc_type CHECK (
        exc_type = '-' OR exc_type ~ '^[A-Za-z_][A-Za-z0-9_.]{0,120}$'),
    CONSTRAINT failure_fingerprints_code_site CHECK (
        code_site = '-'
        OR code_site ~ '^[A-Za-z_][A-Za-z0-9_.]{0,150}:[A-Za-z_][A-Za-z0-9_]{0,80}$'),
    -- A code that holds its meaning keeps no type and no site (§3.2 rule 2).
    CONSTRAINT failure_fingerprints_dash_codes CHECK (
        error_code IN ('exception', 'unhandled_500', 'mcp_exception')
        OR (exc_type = '-' AND code_site = '-')),
    -- The v1 hash of §3.1, so no row holds a made-up fingerprint.
    CONSTRAINT failure_fingerprints_v1_hash CHECK (
        fp_version <> 'v1'
        OR fingerprint = left(encode(sha256(convert_to(
               'v1|' || tool || '|' || error_code || '|' || exc_type || '|' || code_site,
               'UTF8')), 'hex'), 16)),
    CONSTRAINT failure_fingerprints_sources CHECK (
        sources <@ ARRAY['chat_tool', 'mcp_call', 'door_5xx', 'issue_report', 'sandbox']::text[]),
    CONSTRAINT failure_fingerprints_surfaces CHECK (
        surfaces <@ ARRAY['chat', 'whatsapp', 'projects_assistant', 'automation', 'mcp_edge',
                          'studio', 'app', 'other']::text[]),
    CONSTRAINT failure_fingerprints_releases CHECK (
        (first_release IS NULL OR first_release ~ '^[0-9a-f]{7,40}$')
        AND (last_release IS NULL OR last_release ~ '^[0-9a-f]{7,40}$')
        AND (fix_release IS NULL OR fix_release ~ '^[0-9a-f]{7,40}$')),
    CONSTRAINT failure_fingerprints_state CHECK (
        state IN ('new', 'open', 'fix_merged', 'fix_live', 'closed', 'muted')),
    CONSTRAINT failure_fingerprints_counts CHECK (
        total >= 0 AND shown_total >= 0 AND reopened >= 0)
);

-- A database that ran an earlier copy of this file gains the column too.
ALTER TABLE failure_fingerprints ADD COLUMN IF NOT EXISTS last_release_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_failure_fingerprints_state
    ON failure_fingerprints (state, last_seen_at);

CREATE TABLE IF NOT EXISTS failure_daily (
    fingerprint  TEXT NOT NULL
                 REFERENCES failure_fingerprints (fingerprint) ON DELETE CASCADE,
    -- The UTC day.
    day          DATE NOT NULL,
    occurrences  BIGINT NOT NULL DEFAULT 0,
    sources      TEXT[] NOT NULL DEFAULT '{}',
    surfaces     TEXT[] NOT NULL DEFAULT '{}',
    -- Each release seen that day, 64 at most.
    releases     TEXT[] NOT NULL DEFAULT '{}',
    PRIMARY KEY (fingerprint, day),
    CONSTRAINT failure_daily_occurrences CHECK (occurrences >= 0),
    CONSTRAINT failure_daily_sources CHECK (
        sources <@ ARRAY['chat_tool', 'mcp_call', 'door_5xx', 'issue_report', 'sandbox']::text[]),
    CONSTRAINT failure_daily_surfaces CHECK (
        surfaces <@ ARRAY['chat', 'whatsapp', 'projects_assistant', 'automation', 'mcp_edge',
                          'studio', 'app', 'other']::text[]),
    CONSTRAINT failure_daily_releases CHECK (
        cardinality(releases) <= 64
        AND array_to_string(releases, ',') ~ '^([0-9a-f]{7,40}(,[0-9a-f]{7,40})*)?$')
);

CREATE INDEX IF NOT EXISTS idx_failure_daily_day ON failure_daily (day);

CREATE TABLE IF NOT EXISTS failure_config (
    only_row      BOOLEAN PRIMARY KEY DEFAULT true,
    -- The org that may run the job functions. Only TOF-2f writes it, with an
    -- owner-gated SQL on production.
    internal_org  UUID NOT NULL REFERENCES organization (id) ON DELETE CASCADE,
    CONSTRAINT failure_config_one_row CHECK (only_row)
);

COMMENT ON TABLE failure_fingerprints IS
    'One row for each failure fingerprint: the four code fields of '
    'tool_failure_log.md §3, the counts and the state of its task. No org id and '
    'no tenant text. RLS-EXEMPT, no app grant: the failure_* functions are the only '
    'way in (WS-TOF TOF-2b).';
COMMENT ON TABLE failure_daily IS
    'The counts of one fingerprint on one UTC day, with its sources, surfaces and '
    'releases. No org id. RLS-EXEMPT, no app grant (tool_failure_log.md §5.2).';
COMMENT ON TABLE failure_config IS
    'One row that names the internal org, which runs the failure job functions. '
    'No customer data. RLS-EXEMPT, no app grant (tool_failure_log.md §5.2).';

-- ── The functions (§5.3) ────────────────────────────────────────────────────

-- 1. The count of the bound tenant. Open to each tenant, for its own counts.
CREATE OR REPLACE FUNCTION public.failure_tally(
    p_fingerprint text, p_tool text, p_error_code text, p_exc_type text,
    p_code_site text, p_source text, p_surface text, p_release text, p_n integer)
RETURNS void
LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $tally$
DECLARE
    v_org  uuid;
    v_hour timestamptz := pg_catalog.date_trunc('hour', pg_catalog.now() AT TIME ZONE 'UTC')
                          AT TIME ZONE 'UTC';
    v_day  date := (pg_catalog.now() AT TIME ZONE 'UTC')::date;
    v_top  bigint := 9223372036854775807;
BEGIN
    v_org := NULLIF(pg_catalog.current_setting('app.tenant_id', true), '')::uuid;
    IF v_org IS NULL THEN
        RAISE EXCEPTION 'failure_tally: no tenant is bound' USING ERRCODE = '42501';
    END IF;
    IF p_n IS NULL OR p_n < 1 OR p_n > 1000 THEN
        RAISE EXCEPTION 'failure_tally: p_n must be 1 to 1000' USING ERRCODE = '22023';
    END IF;
    IF p_source IS NULL OR p_surface IS NULL THEN
        RAISE EXCEPTION 'failure_tally: a source and a surface are necessary'
            USING ERRCODE = '22004';
    END IF;
    IF p_fingerprint IS NULL OR p_fingerprint IS DISTINCT FROM pg_catalog.left(
           pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to(
               'v1|' || p_tool || '|' || p_error_code || '|' || p_exc_type || '|'
               || p_code_site, 'UTF8')), 'hex'), 16) THEN
        RAISE EXCEPTION 'failure_tally: the fingerprint does not match its fields'
            USING ERRCODE = '22023';
    END IF;

    INSERT INTO public.failure_fingerprints AS f
           (fingerprint, tool, error_code, exc_type, code_site, sources, surfaces,
            first_seen_at, last_seen_at, first_release, last_release, last_release_at,
            total)
    VALUES (p_fingerprint, p_tool, p_error_code, p_exc_type, p_code_site,
            ARRAY[p_source], ARRAY[p_surface], v_hour, v_hour, p_release, p_release,
            CASE WHEN p_release IS NULL THEN NULL ELSE v_hour END, p_n)
    ON CONFLICT (fingerprint) DO UPDATE
       SET sources = CASE WHEN p_source = ANY (f.sources) THEN f.sources
                          ELSE f.sources || p_source END,
           surfaces = CASE WHEN p_surface = ANY (f.surfaces) THEN f.surfaces
                           ELSE f.surfaces || p_surface END,
           last_seen_at = GREATEST(f.last_seen_at, EXCLUDED.last_seen_at),
           first_release = COALESCE(f.first_release, EXCLUDED.first_release),
           last_release = COALESCE(EXCLUDED.last_release, f.last_release),
           last_release_at = CASE WHEN p_release IS NULL THEN f.last_release_at
                                  ELSE GREATEST(f.last_release_at, v_hour) END,
           total = LEAST(f.total, v_top - p_n) + p_n;

    INSERT INTO public.failure_daily AS d
           (fingerprint, day, occurrences, sources, surfaces, releases)
    VALUES (p_fingerprint, v_day, p_n, ARRAY[p_source], ARRAY[p_surface],
            CASE WHEN p_release IS NULL THEN '{}'::text[] ELSE ARRAY[p_release] END)
    ON CONFLICT (fingerprint, day) DO UPDATE
       SET occurrences = LEAST(d.occurrences, v_top - p_n) + p_n,
           sources = CASE WHEN p_source = ANY (d.sources) THEN d.sources
                          ELSE d.sources || p_source END,
           surfaces = CASE WHEN p_surface = ANY (d.surfaces) THEN d.surfaces
                           ELSE d.surfaces || p_surface END,
           releases = CASE WHEN p_release IS NULL OR p_release = ANY (d.releases)
                                OR pg_catalog.cardinality(d.releases) >= 64
                           THEN d.releases ELSE d.releases || p_release END;

    INSERT INTO public.failure_org_days (organization_id, fingerprint, day)
    VALUES (v_org, p_fingerprint, v_day)
    ON CONFLICT DO NOTHING;
END
$tally$;

-- 2. The band of organizations of one fingerprint, over a fixed 30 days.
CREATE OR REPLACE FUNCTION public.failure_org_band(p_fp text)
RETURNS text
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $band$
DECLARE
    v_internal uuid;
    v_sees_all boolean;
    v_count    bigint;
BEGIN
    SELECT c.internal_org INTO v_internal FROM public.failure_config c;
    IF v_internal IS NULL THEN
        RAISE EXCEPTION 'failure_org_band: failure_config has no row' USING ERRCODE = '42501';
    END IF;
    IF NULLIF(pg_catalog.current_setting('app.tenant_id', true), '')::uuid
           IS DISTINCT FROM v_internal THEN
        RAISE EXCEPTION 'failure_org_band: only the internal org runs it'
            USING ERRCODE = '42501';
    END IF;

    -- current_user is the OWNER here. A table with RLS shows every row only
    -- to a superuser or a BYPASSRLS owner. Else: cannot tell.
    SELECT r.rolsuper OR r.rolbypassrls
            OR NOT (c.relrowsecurity OR c.relforcerowsecurity)
      INTO v_sees_all
      FROM pg_catalog.pg_roles r, pg_catalog.pg_class c
     WHERE r.rolname = current_user
       AND c.oid = 'public.failure_org_days'::pg_catalog.regclass;
    IF NOT COALESCE(v_sees_all, false) THEN
        RETURN NULL;
    END IF;

    SELECT pg_catalog.count(DISTINCT o.organization_id) INTO v_count
      FROM public.failure_org_days o
     WHERE o.fingerprint = p_fp
       AND o.day > (pg_catalog.now() AT TIME ZONE 'UTC')::date - 30;
    RETURN CASE WHEN v_count >= 3 THEN v_count::text
                WHEN v_count >= 1 THEN '<3'
                ELSE '0' END;
END
$band$;

-- 3. The platform rows that the sync job reads, with their counts.
-- The return type gained `last_release_at` (TOF-2e), and CREATE OR REPLACE
-- cannot change a return type. So the file drops its own function first. No
-- other object depends on it, and the grants below run again.
DROP FUNCTION IF EXISTS public.failure_due(integer);
CREATE FUNCTION public.failure_due(p_limit integer)
RETURNS TABLE (
    fingerprint text, fp_version text, tool text, error_code text, exc_type text,
    code_site text, sources text[], surfaces text[], first_seen_at timestamptz,
    last_seen_at timestamptz, first_release text, last_release text,
    last_release_at timestamptz, total bigint,
    state text, task_id uuid, fix_merged_at timestamptz, fix_live_at timestamptz,
    closed_at timestamptz, fix_release text, reopened integer, shown_total bigint,
    n_1d bigint, n_7d bigint, n_30d bigint, days jsonb)
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $due$
#variable_conflict use_column
DECLARE
    v_internal uuid;
    v_today    date := (pg_catalog.now() AT TIME ZONE 'UTC')::date;
BEGIN
    SELECT c.internal_org INTO v_internal FROM public.failure_config c;
    IF v_internal IS NULL THEN
        RAISE EXCEPTION 'failure_due: failure_config has no row' USING ERRCODE = '42501';
    END IF;
    IF NULLIF(pg_catalog.current_setting('app.tenant_id', true), '')::uuid
           IS DISTINCT FROM v_internal THEN
        RAISE EXCEPTION 'failure_due: only the internal org runs it' USING ERRCODE = '42501';
    END IF;
    IF p_limit IS NULL OR p_limit < 1 OR p_limit > 1000 THEN
        RAISE EXCEPTION 'failure_due: p_limit must be 1 to 1000' USING ERRCODE = '22023';
    END IF;

    -- A row is due when it has counts that the job has not seen yet, while
    -- the job watches its task (`open`, `fix_merged`, `fix_live`), and while
    -- its task is muted (a person can move it back to Open). The watched rows
    -- come first, then the rows with new counts, then the muted rows. So a
    -- flood of rows that wait under the limit of §7.2 never starves a task
    -- that the job watches (TOF-2e review).
    RETURN QUERY
    SELECT f.fingerprint, f.fp_version, f.tool, f.error_code, f.exc_type, f.code_site,
           f.sources, f.surfaces, f.first_seen_at, f.last_seen_at, f.first_release,
           f.last_release, f.last_release_at, f.total, f.state, f.task_id,
           f.fix_merged_at, f.fix_live_at,
           f.closed_at, f.fix_release, f.reopened, f.shown_total,
           COALESCE(s.n_1d, 0), COALESCE(s.n_7d, 0), COALESCE(s.n_30d, 0),
           COALESCE(s.days, '[]'::jsonb)
      FROM public.failure_fingerprints f
      LEFT JOIN LATERAL (
           SELECT (pg_catalog.sum(d.occurrences) FILTER (WHERE d.day = v_today))::bigint
                      AS n_1d,
                  (pg_catalog.sum(d.occurrences) FILTER (WHERE d.day > v_today - 7))::bigint
                      AS n_7d,
                  pg_catalog.sum(d.occurrences)::bigint AS n_30d,
                  pg_catalog.jsonb_agg(pg_catalog.jsonb_build_object(
                      'day', d.day, 'n', d.occurrences,
                      'releases', pg_catalog.to_jsonb(d.releases)) ORDER BY d.day DESC)
                      AS days
             FROM public.failure_daily d
            WHERE d.fingerprint = f.fingerprint AND d.day > v_today - 30
      ) s ON true
     WHERE f.state IN ('open', 'fix_merged', 'fix_live')
        OR (f.state = 'muted' AND f.task_id IS NOT NULL)
        OR f.total <> f.shown_total
     ORDER BY CASE WHEN f.state IN ('open', 'fix_merged', 'fix_live') THEN 0
                   WHEN f.total <> f.shown_total THEN 1
                   ELSE 2 END,
              f.last_seen_at DESC, f.fingerprint
     LIMIT p_limit;
END
$due$;

-- 4. The state columns of one fingerprint. It never writes a field of §3.
CREATE OR REPLACE FUNCTION public.failure_set(
    p_fp text, p_state text, p_task_id uuid, p_fix_merged_at timestamptz,
    p_fix_live_at timestamptz, p_closed_at timestamptz, p_fix_release text,
    p_shown_total bigint, p_reopened integer)
RETURNS boolean
LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $set$
DECLARE
    v_internal uuid;
    v_task     uuid;
BEGIN
    SELECT c.internal_org INTO v_internal FROM public.failure_config c;
    IF v_internal IS NULL THEN
        RAISE EXCEPTION 'failure_set: failure_config has no row' USING ERRCODE = '42501';
    END IF;
    IF NULLIF(pg_catalog.current_setting('app.tenant_id', true), '')::uuid
           IS DISTINCT FROM v_internal THEN
        RAISE EXCEPTION 'failure_set: only the internal org runs it' USING ERRCODE = '42501';
    END IF;

    SELECT f.task_id INTO v_task
      FROM public.failure_fingerprints f
     WHERE f.fingerprint = p_fp
       FOR UPDATE;
    IF NOT FOUND THEN
        RETURN false;
    END IF;
    -- One fingerprint, one task, always (§7.4).
    IF v_task IS NOT NULL AND p_task_id IS DISTINCT FROM v_task THEN
        RAISE EXCEPTION 'failure_set: a fingerprint keeps its one task'
            USING ERRCODE = '22023';
    END IF;

    UPDATE public.failure_fingerprints f
       SET state = p_state, task_id = p_task_id, fix_merged_at = p_fix_merged_at,
           fix_live_at = p_fix_live_at, closed_at = p_closed_at,
           fix_release = p_fix_release, shown_total = p_shown_total,
           reopened = p_reopened
     WHERE f.fingerprint = p_fp;
    RETURN true;
END
$set$;

-- 5. The delete of failure_daily rows older than 400 days (§5.4).
CREATE OR REPLACE FUNCTION public.failure_prune_daily(p_before date)
RETURNS integer
LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $prune$
DECLARE
    v_internal uuid;
    v_gone     integer;
BEGIN
    SELECT c.internal_org INTO v_internal FROM public.failure_config c;
    IF v_internal IS NULL THEN
        RAISE EXCEPTION 'failure_prune_daily: failure_config has no row'
            USING ERRCODE = '42501';
    END IF;
    IF NULLIF(pg_catalog.current_setting('app.tenant_id', true), '')::uuid
           IS DISTINCT FROM v_internal THEN
        RAISE EXCEPTION 'failure_prune_daily: only the internal org runs it'
            USING ERRCODE = '42501';
    END IF;
    IF p_before IS NULL
       OR p_before > (pg_catalog.now() AT TIME ZONE 'UTC')::date - 400 THEN
        RAISE EXCEPTION 'failure_prune_daily: p_before must be 400 days ago or older'
            USING ERRCODE = '22023';
    END IF;

    DELETE FROM public.failure_daily d WHERE d.day < p_before;
    GET DIAGNOSTICS v_gone = ROW_COUNT;
    RETURN v_gone;
END
$prune$;

-- ── Grants ──────────────────────────────────────────────────────────────────

REVOKE ALL ON TABLE failure_fingerprints FROM PUBLIC;
REVOKE ALL ON TABLE failure_daily FROM PUBLIC;
REVOKE ALL ON TABLE failure_config FROM PUBLIC;
REVOKE ALL ON FUNCTION public.failure_tally(
    text, text, text, text, text, text, text, text, integer) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.failure_org_band(text) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.failure_due(integer) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.failure_set(
    text, text, uuid, timestamptz, timestamptz, timestamptz, text, bigint, integer) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.failure_prune_daily(date) FROM PUBLIC;

DO $$
DECLARE
    r text;
    t text;
    f text;
BEGIN
    -- Supabase's default privileges hand every new table and function to
    -- these roles.
    FOREACH r IN ARRAY ARRAY['anon', 'authenticated', 'service_role'] LOOP
        IF EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = r) THEN
            FOREACH t IN ARRAY ARRAY['failure_fingerprints', 'failure_daily',
                                     'failure_config'] LOOP
                EXECUTE format('REVOKE ALL ON TABLE %I FROM %I', t, r);
            END LOOP;
            FOREACH f IN ARRAY ARRAY[
                'public.failure_tally(text, text, text, text, text, text, text, text, integer)',
                'public.failure_org_band(text)',
                'public.failure_due(integer)',
                'public.failure_set(text, text, uuid, timestamptz, timestamptz, '
                    'timestamptz, text, bigint, integer)',
                'public.failure_prune_daily(date)'] LOOP
                EXECUTE format('REVOKE ALL ON FUNCTION %s FROM %I', f, r);
            END LOOP;
        END IF;
    END LOOP;
    -- The application role of saas_multitenancy_handover.md §H3. Its default
    -- privileges give it each new table. It loses each privilege on the
    -- platform tables, and keeps only EXECUTE on the five functions.
    IF EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = 'acb_app') THEN
        REVOKE ALL ON TABLE failure_fingerprints FROM acb_app;
        REVOKE ALL ON TABLE failure_daily FROM acb_app;
        REVOKE ALL ON TABLE failure_config FROM acb_app;
        GRANT EXECUTE ON FUNCTION public.failure_tally(
            text, text, text, text, text, text, text, text, integer) TO acb_app;
        GRANT EXECUTE ON FUNCTION public.failure_org_band(text) TO acb_app;
        GRANT EXECUTE ON FUNCTION public.failure_due(integer) TO acb_app;
        GRANT EXECUTE ON FUNCTION public.failure_set(
            text, text, uuid, timestamptz, timestamptz, timestamptz, text, bigint, integer)
            TO acb_app;
        GRANT EXECUTE ON FUNCTION public.failure_prune_daily(date) TO acb_app;
    END IF;
END
$$;

COMMIT;
