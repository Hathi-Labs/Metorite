-- ============================================================================
-- 467_failure_store_sync.sql — the sync job reads more of the failure store
-- (WS-TOF TOF-2e, task 3546). It follows 466_failure_store.sql (TOF-2b), which
-- is on main, so the file of 466 stays as it merged.
--
-- What: one new nullable column and two function bodies.
--   failure_fingerprints.last_release_at  The hour of the newest count that
--       named a release. A count with no release moves `last_seen_at` only, so
--       it never reads as a count of `last_release` (§7.4: a NULL release never
--       holds the fix).
--   failure_tally   Writes `last_release_at`. Same signature, so CREATE OR
--       REPLACE keeps the grants.
--   failure_due_v2  The new reader of the sync job. It lists the watched rows
--       first, then the rows with new counts, then the muted rows, so a flood
--       of rows that wait under the limit of §7.2 never starves a task that the
--       job watches. It returns `last_release_at`. It is a NEW function with a
--       new name. A return type cannot change under CREATE OR REPLACE, and a
--       replay of 466 (the ladder runs each file again) would try to make the
--       old `failure_due` again. So `failure_due` stays as 466 made it, and no
--       code reads it after this change.
--
-- Expand only (R6): a nullable column, no rename, no change of a tenant table.
-- Old code does not read the column or the new return column. Idempotent: ADD
-- COLUMN IF NOT EXISTS, CREATE OR REPLACE FUNCTION,
-- REVOKE and GRANT.
-- Number: 467 booked for task 3546 with ~/lanes/reserve_migration.sh (R1).
-- Fence: tests/unit/test_tool_failure_tasks_r8.py (R8) and
-- tests/unit/test_failure_store_r8.py.
-- ============================================================================

BEGIN;

SET LOCAL lock_timeout = '5s';

ALTER TABLE failure_fingerprints ADD COLUMN IF NOT EXISTS last_release_at TIMESTAMPTZ;

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

-- 2. The platform rows that the sync job reads, with their counts.
-- This is a NEW function. The return type of `failure_due` of 466 cannot change
-- under CREATE OR REPLACE, and the ladder replays 466 after this file. So this
-- file never drops or changes `failure_due`.
CREATE OR REPLACE FUNCTION public.failure_due_v2(p_limit integer)
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
        RAISE EXCEPTION 'failure_due_v2: failure_config has no row' USING ERRCODE = '42501';
    END IF;
    IF NULLIF(pg_catalog.current_setting('app.tenant_id', true), '')::uuid
           IS DISTINCT FROM v_internal THEN
        RAISE EXCEPTION 'failure_due_v2: only the internal org runs it' USING ERRCODE = '42501';
    END IF;
    IF p_limit IS NULL OR p_limit < 1 OR p_limit > 1000 THEN
        RAISE EXCEPTION 'failure_due_v2: p_limit must be 1 to 1000' USING ERRCODE = '22023';
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

-- ── Grants (the same as 466 for these two functions) ────────────────────────

REVOKE ALL ON FUNCTION public.failure_tally(
    text, text, text, text, text, text, text, text, integer) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.failure_due_v2(integer) FROM PUBLIC;

DO $$
DECLARE
    r text;
    f text;
BEGIN
    FOREACH r IN ARRAY ARRAY['anon', 'authenticated', 'service_role'] LOOP
        IF EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = r) THEN
            FOREACH f IN ARRAY ARRAY[
                'public.failure_tally(text, text, text, text, text, text, text, text, integer)',
                'public.failure_due_v2(integer)'] LOOP
                EXECUTE format('REVOKE ALL ON FUNCTION %s FROM %I', f, r);
            END LOOP;
        END IF;
    END LOOP;
    IF EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = 'acb_app') THEN
        GRANT EXECUTE ON FUNCTION public.failure_tally(
            text, text, text, text, text, text, text, text, integer) TO acb_app;
        GRANT EXECUTE ON FUNCTION public.failure_due_v2(integer) TO acb_app;
    END IF;
END
$$;

COMMIT;
