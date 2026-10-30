-- ============================================================================
-- 317_assistant_profile_interview_stopped.sql — a stop of the interview from
-- before PZ-32 gets its `stopped_at` (WS-63 PZ-32, repair round 2)
-- ============================================================================
-- Spec: project-docs/specs/personalization.md §14.4 "PZ-32 as built" item 8
-- · R5 · R6 · R8.
--
-- What: a data backfill of the key `interview` of
--   `user_settings.assistant_profile`. No schema change.
--
-- Why: before PZ-32 a stop ("stop asking") marked each open area of the ten
--   areas `never`, and it wrote nothing else. PZ-32 adds the areas `name`
--   and `language`, and a stop now writes `stopped_at`.
--   `assistant_profile.interview_stopped` reads ONLY `stopped_at`. So a stop
--   from before PZ-32 must get `stopped_at` once, or the chat asks a member
--   who said stop "What should I call you?".
--
-- The rows: an `interview` with no `stopped_at`, whose ten old areas
--   (`assistant_profile.OLD_AREAS`) are each `answered` or `never`, with at
--   least one `never`. That is the shape that the old stop wrote. A member
--   who marked some areas `never` one by one and answered the rest has the
--   same shape, and reads as stopped too. A reset in Settings opens an area
--   again. No other row changes.
--
-- The change, the same as `assistant_profile.stop_interview`:
--   * `stopped_at` is the row's `last_asked_at` when it is a stored time, or
--     now;
--   * `name` and `language` get `{"state": "never", "at": <stopped_at>}`
--     when they are not `answered` or `never`;
--   * every other key and area stays.
--
-- The deploy window (R6): the deploy applies this file before it restarts
--   the services. For those minutes the old code runs. It does not know
--   `stopped_at`, `name` or `language`. An old-code write of the interview
--   (a mark or a stop) rebuilds the key from the parts that it knows, so it
--   drops them again. A stop in that window keeps the old shape, as today.
--   Such a write is rare for a member who said stop, and the window is a few
--   minutes. No old-code read breaks on the new keys.
--
-- It runs once: the file ends by writing `PZ-32 stop backfill done` as the
--   comment of the TABLE `user_settings`, in the same transaction. A later
--   run finds that text, and it changes nothing. A replay that runs 294 first
--   changes nothing too. ⚠️ This guard is load-bearing. The runner re-applies
--   a file whose checksum changes, and REPLAY_ALL re-applies each file. After
--   the first run the old shape proves nothing: a member who reset an area
--   and answered it matches, and so does a member who never said stop. A
--   second backfill would stop each of them. The mark is not on the column,
--   because 294 writes the comment of the column with no condition, and a
--   replay would clear it. No other file writes the comment of this table.
--
-- The race: `Reset the interview` or Forget-all can hold a row while the
--   tenant pass reads it. Postgres then re-reads the newest row for the
--   UPDATE, and it does not check `legacy` again. So the CTE ends with
--   `FOR UPDATE OF us`, which checks the row again after the wait. With no
--   `interview`, a strict `jsonb_set` gives NULL for the WHOLE column, so the
--   UPDATE also refuses a row with no `interview` object, as a second wall.
--
-- ── FORCE RLS ───────────────────────────────────────────────────────────────
--
-- `user_settings` carries FORCE ROW LEVEL SECURITY once `generated/04` has
-- run, and the runner connects as the table owner, which FORCE binds too.
-- So, when the table has row security, the file binds each tenant in turn
-- with a local `set_config('app.tenant_id', ...)`, as 221, 297 and 316 do. A
-- table with no row security (the shape of a ladder replay) gets one UPDATE
-- with no tenant bound. `organization` has no policy, so the loop can read
-- it.
--
-- No new table, so `test_tenant_coverage.py` changes nothing.
--
-- Depends on: 239_user_settings_per_org.sql,
--   294_user_settings_assistant_profile.sql.
-- Pinned by: tests/unit/test_interview_stop_backfill_r8.py (R8).
-- ============================================================================

BEGIN;

DO $$
DECLARE
    org uuid;
    n bigint;
    total bigint := 0;
    -- One statement, run once with no tenant or once for each tenant.
    stmt CONSTANT text := $q$
        WITH legacy AS (
            SELECT us.organization_id, us.user_id, s.stamp
              FROM user_settings us
             CROSS JOIN LATERAL (
                SELECT CASE
                    WHEN (us.assistant_profile->'interview'->>'last_asked_at')
                         ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$'
                    THEN us.assistant_profile->'interview'->>'last_asked_at'
                    ELSE to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"')
                END AS stamp) s
             WHERE jsonb_typeof(us.assistant_profile) = 'object'
               AND jsonb_typeof(us.assistant_profile->'interview') = 'object'
               AND NOT (us.assistant_profile->'interview' ? 'stopped_at')
               AND jsonb_typeof(us.assistant_profile->'interview'->'areas') = 'object'
               AND NOT EXISTS (
                    SELECT 1 FROM unnest(ARRAY['role', 'goals', 'good_day', 'decisions',
                                               'detail', 'rhythm', 'channels', 'energy',
                                               'peeves', 'people']) AS a(area)
                     WHERE COALESCE(us.assistant_profile->'interview'->'areas'->a.area->>'state', '')
                           NOT IN ('answered', 'never'))
               AND EXISTS (
                    SELECT 1 FROM unnest(ARRAY['role', 'goals', 'good_day', 'decisions',
                                               'detail', 'rhythm', 'channels', 'energy',
                                               'peeves', 'people']) AS a(area)
                     WHERE us.assistant_profile->'interview'->'areas'->a.area->>'state' = 'never')
               FOR UPDATE OF us
        )
        UPDATE user_settings t
           SET assistant_profile = jsonb_set(
                   t.assistant_profile, '{interview}',
                   (t.assistant_profile->'interview')
                   || jsonb_build_object(
                       'stopped_at', l.stamp,
                       'areas', (t.assistant_profile->'interview'->'areas')
                                || COALESCE((
                                    SELECT jsonb_object_agg(
                                               n.area, jsonb_build_object('state', 'never',
                                                                          'at', l.stamp))
                                      FROM unnest(ARRAY['name', 'language']) AS n(area)
                                     WHERE COALESCE(
                                               t.assistant_profile->'interview'->'areas'->n.area->>'state',
                                               '') NOT IN ('answered', 'never')),
                                    '{}'::jsonb))),
               updated_at = now()
          FROM legacy l
         WHERE t.organization_id = l.organization_id
           AND t.user_id = l.user_id
           AND jsonb_typeof(t.assistant_profile->'interview') = 'object'
           AND jsonb_typeof(t.assistant_profile->'interview'->'areas') = 'object'
    $q$;
BEGIN
    -- Run once. After the first run the old shape proves no stop, and a
    -- replay must keep each member's own state.
    IF COALESCE(obj_description('user_settings'::regclass, 'pg_class'), '')
            LIKE '%PZ-32 stop backfill done%' THEN
        RAISE NOTICE '317: the stop backfill ran already, so nothing changes';
        RETURN;
    END IF;

    IF EXISTS (SELECT 1 FROM pg_class
                WHERE oid = 'user_settings'::regclass AND relrowsecurity) THEN
        -- Under the policy an unbound UPDATE sees no row. So bind each
        -- tenant in turn. A role that bypasses RLS does all rows in the
        -- first pass, and the next passes find none, because a row that
        -- changed has `stopped_at`.
        FOR org IN SELECT id FROM organization LOOP
            PERFORM set_config('app.tenant_id', org::text, true);
            EXECUTE stmt;
            GET DIAGNOSTICS n = ROW_COUNT;
            total := total + n;
        END LOOP;
    ELSE
        EXECUTE stmt;
        GET DIAGNOSTICS n = ROW_COUNT;
        total := total + n;
    END IF;

    RAISE NOTICE '317: % interview(s) from before PZ-32 got stopped_at', total;
END $$;

-- The mark of the run-once guard, in the transaction of the backfill.
COMMENT ON TABLE user_settings IS
    'The settings of one member in one organization. PZ-32 stop backfill done.';

COMMIT;
