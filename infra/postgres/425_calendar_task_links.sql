-- ============================================================================
-- 425_calendar_task_links.sql — the provider event of a task block (WS-CAL
-- CE-13, D215).
--
-- What: one new table. `calendar_task_links` holds, for one timed task block
--       of one member, the event that Metorite wrote into the calendar
--       "Metorite" of one connected Google or Outlook account. A member with
--       Google and Outlook has two rows for one block.
-- Why:  D215 point 4 and spec calendar_events_reminders.md §8.1a point 8: the
--       provider id does NOT go on `pm_task_personal` (two providers), and NOT
--       on `calendar_entries` (a task block is not an entry).
-- Depends on: 130_org_access_control.sql (organization),
--             245_integration_connections.sql (integration_connections),
--             331_calendar_entries.sql (the (organization_id, id) key on
--             integration_connections).
--
-- Expand only (R6): a new table. No rename, no drop, no change of an old
-- object. Old code names nothing new. Idempotent: CREATE TABLE IF NOT EXISTS,
-- CREATE INDEX IF NOT EXISTS, and an RLS block guarded on relforcerowsecurity.
-- Tenant-scoped (R5): `organization_id`, ENABLE and FORCE row level security,
-- and one policy with USING and WITH CHECK, as 331 does.
-- `REFERENCES` comes before `DEFAULT`, because the tenancy ratchet in
-- tests/unit/test_tenancy_boundary.py matches the two with no comma between.
--
-- The key is (organization_id, task_id, member_email, connection_id). There is
-- NO foreign key to `pm_tasks`: a deleted task must leave the link behind,
-- so the writer can delete the provider event (the loop reads the link, finds
-- no task, and deletes the event). The connection key CASCADES on a row DELETE
-- (a purged member). `disconnect_connection` only sets the row `disconnected`,
-- so it deletes the links itself. The provider events stay in the "Metorite"
-- calendar of the member, who owns that calendar.
--
-- ⚠️ RLS covers the org only. The member is a second axis: every read and
-- write of calendar_sync/task_out.py also filters on `member_email`.
--
-- The number is taken at build time (R1). Check it again at merge: the
-- ledger held 422 on 2026-11-12 (424 taken on a branch).
-- Pinned by tests/unit/test_calendar_sync_tasks.py, which finds this file by
-- CONTENT, never by number (R1).
-- ============================================================================

BEGIN;

CREATE TABLE IF NOT EXISTS calendar_task_links (
    organization_id  UUID NOT NULL
                     REFERENCES organization (id) ON DELETE CASCADE
                     DEFAULT current_setting('app.tenant_id', true)::uuid,
    task_id          UUID NOT NULL,
    member_email     TEXT NOT NULL,
    connection_id    UUID NOT NULL,
    -- The provider event. NULL while the first write has not returned.
    external_id      TEXT NULL,
    etag             TEXT NULL,
    -- The `updated` time the provider reported for the last write or read of
    -- the event. The last writer wins by this against the block's updated_at.
    provider_updated TIMESTAMPTZ NULL,
    -- A hash of what the provider holds (title, start, end). A block whose
    -- hash differs has changed in Metorite and goes out again.
    synced_block_hash TEXT NULL,
    sync_state       TEXT NOT NULL DEFAULT 'pending',
    sync_error       TEXT NULL,
    attempts         INTEGER NOT NULL DEFAULT 0,
    next_attempt_at  TIMESTAMPTZ NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (organization_id, task_id, member_email, connection_id),
    -- The connection of the SAME org. CASCADE: a disconnect drops the links.
    CONSTRAINT calendar_task_links_connection_fk
        FOREIGN KEY (organization_id, connection_id)
        REFERENCES integration_connections (organization_id, id) ON DELETE CASCADE,
    CONSTRAINT calendar_task_links_member_email CHECK (
        member_email <> '' AND member_email = lower(member_email)),
    CONSTRAINT calendar_task_links_sync_state CHECK (
        sync_state IN ('pending', 'sent', 'failed')),
    CONSTRAINT calendar_task_links_sync_error CHECK (
        char_length(sync_error) <= 500),
    -- `sent` means the provider gave an id back.
    CONSTRAINT calendar_task_links_sent_has_id CHECK (
        sync_state <> 'sent' OR external_id IS NOT NULL)
);

-- One link for each provider event of each connection. The read sync asks
-- "is this event a task block of ours" by this key.
CREATE UNIQUE INDEX IF NOT EXISTS calendar_task_links_external_uq
    ON calendar_task_links (organization_id, connection_id, external_id)
    WHERE external_id IS NOT NULL;
-- The loop reads the links of one connection.
CREATE INDEX IF NOT EXISTS calendar_task_links_connection_idx
    ON calendar_task_links (organization_id, connection_id);
-- The write-outs that wait or failed, for the retry.
CREATE INDEX IF NOT EXISTS calendar_task_links_retry_idx
    ON calendar_task_links (organization_id, next_attempt_at)
    WHERE sync_state IN ('pending', 'failed');

DO $rls$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_class
         WHERE oid = to_regclass('calendar_task_links')
           AND relrowsecurity
           AND relforcerowsecurity
    ) THEN
        ALTER TABLE calendar_task_links ENABLE ROW LEVEL SECURITY;
        ALTER TABLE calendar_task_links FORCE  ROW LEVEL SECURITY;
        DROP POLICY IF EXISTS calendar_task_links_tenant_isolation
            ON calendar_task_links;
        CREATE POLICY calendar_task_links_tenant_isolation ON calendar_task_links
            USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
            WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);
    END IF;
END
$rls$;

COMMENT ON TABLE calendar_task_links IS
    'The provider event of a timed task block (CE-13, D215). One row for each '
    'task, member and connection. The id lives here and not on '
    'pm_task_personal (two providers) or calendar_entries (a task block is not '
    'an entry). RLS covers the org. Each read and write also filters on '
    'member_email. calendar_events_reminders.md §8.1a point 8.';
COMMENT ON COLUMN calendar_task_links.synced_block_hash IS
    'A hash of the title, start and end that the provider holds. A different '
    'hash on the block means Metorite changed it, and the event goes out again.';

COMMIT;
