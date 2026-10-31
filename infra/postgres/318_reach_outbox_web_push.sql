-- ============================================================================
-- 318_reach_outbox_web_push.sql — the outbox of Reach takes a web push.
--
-- What: two wider CHECKs on `reach_outbox` (migration 266, widened by 271).
--       `reach_outbox_channel_known` gains `web_push`. `reach_outbox_kind_known`
--       gains the three reminder card kinds: `reminder_due`, `reminder_check`
--       and `reminder_unconfirmed`. The reminder loop plans one web push row
--       for each step of a reminder, and a send step after the commit sends it
--       (gateway/reach/push_channel.py). Only gateway/reach/outbox.py writes
--       the table. One new partial index, `reach_outbox_push_planned_idx`,
--       serves the list of planned push rows that each tick reads for each
--       org (`outbox._PLANNED_PUSHES_SQL`). No new table and no new column.
-- Why:  WS-66 RE-9b, spec project-docs/specs/calendar_events_reminders.md §5.6
--       "Settled 2026-10-30 (RE-9)", decisions 2, 3 and 5.
-- Depends on: 266_reach_outbox.sql, 271_reach_requests.sql.
--
-- Expand only (R6): two CHECKs that allow MORE values, and one index. Old
-- code writes only `whatsapp` and the kinds `brief`, `interrupt` and
-- `request`, so every row of old code passes both CHECKs. No rename and no
-- drop of a column.
-- Idempotent per infra/postgres/README.md: each CHECK is replaced only while
-- its definition does not yet name the new value, and the index is
-- `CREATE INDEX IF NOT EXISTS`. A second run changes nothing.
-- The index (repair round 1 of RE-9b): nothing deletes old outbox rows, and
-- with the indexes of 266 the list read is a scan of the whole table. EXPLAIN
-- on a copy of the ladder with 200,000 old push rows and 5 planned rows
-- showed a parallel seq scan before, and an index scan on this index after.
-- The index holds only the planned web push rows, so it stays small.
-- `reach_outbox_state_known` already allows planned, sent, skipped and
-- failed, and the table already has FORCE row level security.
-- The number is taken at build time (R1). On 2026-10-31 main held 317, and no
-- open branch held 318. Check it again at merge.
-- Pinned by tests/unit/test_reach_push.py, which finds this file by CONTENT,
-- never by number (R1).
-- ============================================================================

BEGIN;

DO $checks$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = to_regclass('reach_outbox')
           AND conname = 'reach_outbox_channel_known'
           AND pg_get_constraintdef(oid) LIKE '%web_push%'
    ) THEN
        ALTER TABLE reach_outbox DROP CONSTRAINT IF EXISTS reach_outbox_channel_known;
        ALTER TABLE reach_outbox
            ADD CONSTRAINT reach_outbox_channel_known CHECK (
                channel IN ('whatsapp', 'web_push')
            );
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = to_regclass('reach_outbox')
           AND conname = 'reach_outbox_kind_known'
           AND pg_get_constraintdef(oid) LIKE '%reminder_unconfirmed%'
    ) THEN
        ALTER TABLE reach_outbox DROP CONSTRAINT IF EXISTS reach_outbox_kind_known;
        ALTER TABLE reach_outbox
            ADD CONSTRAINT reach_outbox_kind_known CHECK (
                kind IN ('brief', 'interrupt', 'request', 'reminder_due',
                         'reminder_check', 'reminder_unconfirmed')
            );
    END IF;
END
$checks$;

CREATE INDEX IF NOT EXISTS reach_outbox_push_planned_idx
    ON reach_outbox (organization_id, member_email, created_at)
    WHERE channel = 'web_push' AND state = 'planned';

COMMENT ON COLUMN reach_outbox.channel IS
    'WS-66 RE-9b: whatsapp or web_push. A web_push row is one push of one '
    'reminder step. Its body holds the fixed text "reminder", never the title. '
    'While a send step holds it, reason is "sending" and sent_at is the claim '
    'time. calendar_events_reminders.md §5.6.';

COMMIT;
