-- ============================================================================
-- 350_reach_signal_log_rating.sql - the rule and the rating of one alert
-- (WS-BRIEF BR-5 slice 2b-1)
-- ============================================================================
-- Spec: project-docs/specs/core_brief.md §6 "The rating of an alert" and
-- §10 acceptance row 12 · Metorite task 590 · R5 · R6 · R8.
-- Owner decision 2026-11-06 07:17 UTC: the rating lives on reach_signal_log.
-- R1: origin/main held 348 as its highest file on 2026-11-06. The open PR of
-- kg4-structural-edges holds 346. The branch mt-1170f52b-cc2-today holds 349,
-- and the worktree of sw1045a holds 351. So this file is 350. Check the
-- number again at merge.
--
-- What: three nullable columns on `reach_signal_log`.
--   rule_id      TEXT. The exception rule that matched the card, one row of
--                `gateway.reach.exceptions.RULES`. The loop writes it on each
--                log row of a card that a rule let through. NULL on a row
--                from before this file, on a baseline row and on a row of the
--                member's own request. No CHECK: the rules are rows of code,
--                and `outbox.rate_alert` refuses an unknown id before SQL.
--   feedback     TEXT. `useful` or `not_useful`, with the named CHECK
--                `reach_signal_log_feedback_known`. NULL until the member
--                rates the alert.
--   feedback_at  TIMESTAMPTZ. When the member last rated it. A second rating
--                replaces the first, so this holds the newest time.
--
-- Who writes them: only `gateway/reach/outbox.py`. `log` writes `rule_id`.
--   `rate_alert` writes `feedback` and `feedback_at`. A rating only stores:
--   it mutes no rule and changes no budget.
--
-- Expand only (R6): three new nullable columns with no default, no rename,
--   no backfill and no change to the key. The old code names each column of
--   its INSERT, so it never writes the new ones, and they stay NULL. The
--   ALTER changes only the catalog. The CHECK reads each row once, and each
--   row holds NULL. Idempotent: ADD COLUMN IF NOT EXISTS, and the CHECK only
--   while `pg_constraint` lacks its name. A second run changes nothing.
--
-- Tenancy (R5): `reach_signal_log` is tenant-scoped, with FORCE RLS and the
--   policy `reach_signal_log_tenant_isolation` (266). The columns change
--   neither. So this file adds no table, no policy and no
--   `gen_tenant_migration.EXEMPT` row.
--
-- Never wait for the lock: `ALTER TABLE` takes ACCESS EXCLUSIVE, and the
--   Reach loop writes this table each minute. So the file sets
--   `lock_timeout`, as 348 does. On a lock timeout the runner tries the whole
--   file again, and the file is idempotent.
--
-- Depends on: 266_reach_outbox.sql (the table).
-- Pinned by: tests/unit/test_reach_exceptions_feedback_r8.py (R8), which
--   finds this file by CONTENT, never by number (R1).
-- ============================================================================

SET lock_timeout = '5s';

-- One statement, so the table takes its ACCESS EXCLUSIVE lock once.
ALTER TABLE reach_signal_log
    ADD COLUMN IF NOT EXISTS rule_id TEXT,
    ADD COLUMN IF NOT EXISTS feedback TEXT,
    ADD COLUMN IF NOT EXISTS feedback_at TIMESTAMPTZ;

DO $checks$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conname = 'reach_signal_log_feedback_known'
           AND conrelid = 'reach_signal_log'::regclass
    ) THEN
        ALTER TABLE reach_signal_log ADD CONSTRAINT reach_signal_log_feedback_known
            CHECK (feedback IS NULL OR feedback IN ('useful', 'not_useful'));
    END IF;
END
$checks$;

COMMENT ON COLUMN reach_signal_log.rule_id IS
    'BR-5 slice 2b-1: the exception rule that matched the card (reach.exceptions.RULES). NULL on a row with no rule. Only gateway/reach/outbox.py writes it.';

COMMENT ON COLUMN reach_signal_log.feedback IS
    'BR-5 slice 2b-1: the member rating of this alert, useful or not_useful. A second rating replaces the first. It mutes no rule and changes no budget.';

COMMENT ON COLUMN reach_signal_log.feedback_at IS
    'BR-5 slice 2b-1: when the member last rated this alert.';
