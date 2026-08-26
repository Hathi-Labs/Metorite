-- ============================================================================
-- live_ws39_s3c.sql — WS-39 S3c proven against REAL Postgres (R8).
--
-- S3c is a DESTRUCTIVE migration on a ladder that cannot roll back (R6), so
-- almost all of its behaviour is refusal. A test that only proved it can drop
-- would be testing the one path nobody is worried about.
--
-- All four states, in one transaction that ROLLS BACK — Postgres DDL is
-- transactional, so the `DROP TABLE` this exercises is undone at the end and
-- the scratch database is left exactly as it was found.
--
-- Run:  docker exec -i tenant-scratch psql -U acb -d acb_tenant \
--         -v ON_ERROR_STOP=1 < tests/live/live_ws39_s3c.sql
-- ============================================================================

\set ON_ERROR_STOP on

CREATE OR REPLACE FUNCTION s3c_check(label text, got anyelement, want anyelement)
RETURNS void LANGUAGE plpgsql AS $$
BEGIN
    IF got IS DISTINCT FROM want THEN
        RAISE EXCEPTION 'FAIL % — got %, want %', label, got, want;
    END IF;
    RAISE NOTICE 'ok   %', label;
END; $$;

BEGIN;

-- A row that has NOT been migrated. Its presence is what should stop the drop.
INSERT INTO gtd_items (id, user_id, title, disposition)
VALUES ('c1000000-0000-0000-0000-00000000c101','s3ctest@example.invalid','Unmoved','INBOX')
ON CONFLICT (id) DO UPDATE SET migrated_task_id = NULL;

DELETE FROM gtd_retirement_arm;

-- ── 1. UNARMED — the normal state of every deploy ───────────────────────────
SELECT gtd_retirement_drop();
SELECT s3c_check('1a unarmed: gtd_items survives',
    (to_regclass('public.gtd_items') IS NOT NULL), true);
SELECT s3c_check('1b unarmed: gtd_waiting survives',
    (to_regclass('public.gtd_waiting') IS NOT NULL), true);

-- ── 2. ARMED but a row is unaccounted for — must REFUSE, loudly ─────────────
INSERT INTO gtd_retirement_arm (armed_by, note)
VALUES ('test@example.invalid', 'live_ws39_s3c');

DO $t$
DECLARE v_msg text;
BEGIN
    PERFORM gtd_retirement_drop();
    RAISE EXCEPTION 'FAIL 2a — the drop proceeded with an unmigrated row present';
EXCEPTION WHEN OTHERS THEN
    GET STACKED DIAGNOSTICS v_msg = MESSAGE_TEXT;
    IF v_msg LIKE 'FAIL 2a%' THEN RAISE; END IF;
    IF v_msg NOT LIKE 'S3c REFUSED%' THEN
        RAISE EXCEPTION 'FAIL 2b — refused for the wrong reason: %', v_msg;
    END IF;
    RAISE NOTICE 'ok   2a armed + unmigrated row: REFUSED';
    RAISE NOTICE 'ok   2b ...and the message names the real cause';
END $t$;

SELECT s3c_check('2c the refusal left the table intact',
    (to_regclass('public.gtd_items') IS NOT NULL), true);

-- ── 3. ARMED and everything accounted for — now it may drop ─────────────────
UPDATE gtd_items SET migrated_task_id = gen_random_uuid() WHERE migrated_task_id IS NULL;

SELECT gtd_retirement_drop();
SELECT s3c_check('3a armed + accounted for: gtd_items dropped',
    (to_regclass('public.gtd_items') IS NULL), true);
SELECT s3c_check('3b ...and gtd_waiting with it',
    (to_regclass('public.gtd_waiting') IS NULL), true);

-- ── 4. Idempotent — a second deploy must not error ──────────────────────────
SELECT gtd_retirement_drop();
SELECT s3c_check('4a re-running after the drop is a no-op',
    (to_regclass('public.gtd_items') IS NULL), true);

-- ── 5. D53.6 AND THE NEIGHBOURS — what must NOT have been swept ─────────────
--
-- The `gtd_` prefix is shared by four unrelated things. This is the check that
-- stops a future "tidy up the gtd_ tables" from taking the Calendar's state,
-- the People directory, WS-21's Horizons or WS-18's reviews with it.
SELECT s3c_check('5a gtd_settings survives (D53.6 — Calendar)',
    (to_regclass('public.gtd_settings') IS NOT NULL), true);
SELECT s3c_check('5b gtd_day_state survives (D53.6 — Calendar)',
    (to_regclass('public.gtd_day_state') IS NOT NULL), true);
SELECT s3c_check('5c gtd_rollover_log survives (D53.6 — Calendar)',
    (to_regclass('public.gtd_rollover_log') IS NOT NULL), true);
SELECT s3c_check('5d gtd_people survives (People directory, not tasks)',
    (to_regclass('public.gtd_people') IS NOT NULL), true);
SELECT s3c_check('5e gtd_person_skills survives (People directory)',
    (to_regclass('public.gtd_person_skills') IS NOT NULL), true);
SELECT s3c_check('5f gtd_horizons survives (WS-21 owns Horizons)',
    (to_regclass('public.gtd_horizons') IS NOT NULL), true);
SELECT s3c_check('5g gtd_reviews survives (WS-18 owns Weekly Review)',
    (to_regclass('public.gtd_reviews') IS NOT NULL), true);
SELECT s3c_check('5h gtd_projects survives (local tree — waits on slice 5)',
    (to_regclass('public.gtd_projects') IS NOT NULL), true);
SELECT s3c_check('5i gtd_spaces survives (local tree — waits on slice 5)',
    (to_regclass('public.gtd_spaces') IS NOT NULL), true);
SELECT s3c_check('5j gtd_folders survives (local tree — waits on slice 5)',
    (to_regclass('public.gtd_folders') IS NOT NULL), true);
SELECT s3c_check('5k gtd_contexts survives (waits on slice 5)',
    (to_regclass('public.gtd_contexts') IS NOT NULL), true);
SELECT s3c_check('5l gtd_attachments survives (waits on slice 5)',
    (to_regclass('public.gtd_attachments') IS NOT NULL), true);

-- ── Undo everything, including the DROP ─────────────────────────────────────
ROLLBACK;

SELECT s3c_check('6a the rollback restored gtd_items',
    (to_regclass('public.gtd_items') IS NOT NULL), true);
SELECT s3c_check('6b the rollback restored gtd_waiting',
    (to_regclass('public.gtd_waiting') IS NOT NULL), true);

\echo ''
\echo '════════════════════════════════════════════════════════'
\echo '  S3c: all checks passed against real Postgres.'
\echo '════════════════════════════════════════════════════════'
