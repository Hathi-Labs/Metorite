-- ============================================================================
-- 211_pm_tasks_origin.sql — provenance moves onto the one task store.
-- ============================================================================
-- Spec: project-docs/specs/my_tasks_cutover.md §4.4 and §5 S6d · D73 ·
-- board WS-39 slice S6d.
--
-- What: two nullable columns.
--   * `pm_tasks.origin JSONB` — where a task came from. Email capture, reply
--     capture and WhatsApp capture write `{kind, email_id, thread_id,
--     wa_message_id, ...}` here. Three expression indexes serve the three
--     idempotency lookups those captures perform.
--   * `wa_commitments.task_id UUID` — the `pm_tasks` row a WhatsApp promise
--     was captured into. It REPLACES `gtd_item_id`, which points at the
--     retiring `gtd_items` table.
--
-- Why `origin` lives on the TASK and not on the member's overlay (§4.4):
--   provenance is a fact about the work, not about one member's week. Two
--   people assigned an emailed task share one source email. `pm_tasks.source`
--   is a CHECKed TEXT and cannot carry an id, so the id goes beside it.
--
-- ⚠️ Partial indexes, WHERE origin IS NOT NULL. Most tasks are typed by hand
--   and carry no origin. The three lookups are equality on one key, and each
--   runs once per capture, so a small partial index is the whole cost.
--
-- ⚠️ `wa_commitments.task_id` is the EXPAND half of a rename (R6). Readers
--   take `coalesce(task_id, gtd_item_id)` until S8. The CONTRACT half —
--   copying any surviving `gtd_item_id` values across and dropping the old
--   column — is migration 212 (my_tasks_cutover.md §5 S8), after the
--   gateway serves the pm arm alone. Nothing is dropped or renamed here.
--
-- ON DELETE SET NULL, not CASCADE: deleting the task must not delete the
--   record that a promise was made. It only stops pointing at the task.
--
-- R5: no new table, no new connection site. `pm_tasks` has carried
--   `organization_id` since 161, and every reader of `origin` composes on
--   `MY_TASKS_FROM`, whose tenant clause sits above both membership arms.
--   `wa_commitments` reaches its tenant through `wa_accounts`, as before.
--
-- R6 EXPAND: both columns nullable, no default, no backfill. NULL means
--   "captured by hand" on `pm_tasks` and "not captured" on `wa_commitments`,
--   which is the honest value for every row written before today. Old code
--   never selects either column, so this applies safely before the restart.
--
-- Idempotent per infra/postgres/README.md. Depends on 146_projects.sql
--   (pm_tasks) and 105_whatsapp_commitments.sql (wa_commitments).
-- Fence: tests/unit/test_tasks_ai_source.py, tests/live/live_ws39_s6d.py.
-- ============================================================================

ALTER TABLE pm_tasks ADD COLUMN IF NOT EXISTS origin JSONB;

COMMENT ON COLUMN pm_tasks.origin IS
    'Where this task came from: {kind: email|whatsapp, email_id, thread_id, '
    'wa_message_id, wa_chat_id, subject, from_name, ...}. NULL = captured by '
    'hand. Email and WhatsApp capture key their idempotency on the three '
    'indexed keys. Moved from gtd_items.origin by WS-39 S6d (§4.4).';

CREATE INDEX IF NOT EXISTS idx_pm_tasks_origin_email_id
    ON pm_tasks ((origin->>'email_id'))
    WHERE origin IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_pm_tasks_origin_thread_id
    ON pm_tasks ((origin->>'thread_id'))
    WHERE origin IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_pm_tasks_origin_wa_message_id
    ON pm_tasks ((origin->>'wa_message_id'))
    WHERE origin IS NOT NULL;

-- The EXPAND half of `gtd_item_id` -> `task_id`. S8 (migration 212) copies
-- the surviving values across and drops `gtd_item_id`. Until then readers
-- take `coalesce(task_id, gtd_item_id)`.
ALTER TABLE wa_commitments
    ADD COLUMN IF NOT EXISTS task_id UUID REFERENCES pm_tasks (id) ON DELETE SET NULL;

COMMENT ON COLUMN wa_commitments.task_id IS
    'The pm_tasks row this promise was captured into. Replaces gtd_item_id, '
    'which points at the retiring gtd_items table. S8 (migration 212) copies '
    'and drops the old column. Readers coalesce(task_id, gtd_item_id) until then.';
