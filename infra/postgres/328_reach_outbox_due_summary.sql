-- ============================================================================
-- 328_reach_outbox_due_summary.sql — the outbox of Reach takes the morning
-- summary of due tasks.
--
-- What: one wider CHECK on `reach_outbox` (migration 266, widened by 271 and
--       318). `reach_outbox_kind_known` gains `due_summary`: one web push row
--       for one member and one local day, the morning summary of the tasks
--       that are due today or overdue. gateway/reach/due_push.py plans the
--       row, and the send step of gateway/reach/push_channel.py sends it.
--       Only gateway/reach/outbox.py writes the table. No new table, no new
--       column and no new index: the UNIQUE of (organization_id, dedupe_key)
--       holds one row for each member and day, because the key names both.
-- Why:  RX-5, owner directive 2026-11-01, spec
--       project-docs/specs/calendar_events_reminders.md §5.11.
-- Depends on: 266_reach_outbox.sql, 271_reach_requests.sql,
--             318_reach_outbox_web_push.sql.
--
-- Expand only (R6): one CHECK that allows MORE values. Old code writes none
-- of the new kind, so every row of old code passes. Old send code that meets
-- a row of the new kind ends it as `skipped`, with the reason `unknown_kind`.
-- No rename and no drop of a column.
-- Idempotent per infra/postgres/README.md: the CHECK is replaced only while
-- its definition does not yet name the new value. A second run changes
-- nothing. The guard of 318 looks for `reminder_unconfirmed`, and this CHECK
-- keeps that value, so a replay of 318 leaves this CHECK alone.
-- The number is taken at build time (R1). On 2026-11-02 main held 325, and
-- an open pull request held 326. Check it again at merge.
-- Pinned by tests/unit/test_due_push_r8.py, which finds this file by CONTENT,
-- never by number (R1).
-- ============================================================================

BEGIN;

DO $checks$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = to_regclass('reach_outbox')
           AND conname = 'reach_outbox_kind_known'
           AND pg_get_constraintdef(oid) LIKE '%due_summary%'
    ) THEN
        ALTER TABLE reach_outbox DROP CONSTRAINT IF EXISTS reach_outbox_kind_known;
        ALTER TABLE reach_outbox
            ADD CONSTRAINT reach_outbox_kind_known CHECK (
                kind IN ('brief', 'interrupt', 'request', 'reminder_due',
                         'reminder_check', 'reminder_unconfirmed', 'due_summary')
            );
    END IF;
END
$checks$;

COMMENT ON COLUMN reach_outbox.kind IS
    'RX-5: brief, interrupt and request go on WhatsApp. The three reminder '
    'kinds and due_summary go by web push. A due_summary row is the morning '
    'summary of one member and one local day. Its body holds the fixed text '
    '"due_summary", never a task title. calendar_events_reminders.md §5.11.';

COMMIT;
