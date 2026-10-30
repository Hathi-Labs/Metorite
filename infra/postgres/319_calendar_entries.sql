-- ============================================================================
-- 319_calendar_entries.sql — the event store of the Calendar (WS-CAL CE-4,
-- D138).
--
-- What: two new tables. `calendar_entries` holds one time that is not a
--       to-do: an event that a member makes in Metorite (`kind = 'event'`),
--       or a read-only copy of a Google or Outlook event (`kind =
--       'external'`, written by the sync of CE-10). `calendar_entry_attendees`
--       holds one person on an entry. In CE-4 the only writer is
--       gateway/routes/tasks/entries.py, and it writes the organizer row of
--       the owner only. CE-9 adds the other attendees.
-- Why:  D138 (2026-10-29) answered CE-0 Q1 (one entry table, option C) and
--       Q2 (an attendee sees the entry, and nobody else). Spec
--       project-docs/specs/calendar_events_reminders.md §3.3, §3.4, §4.1,
--       §8 and §10 CE-4.
-- Depends on: 130_org_access_control.sql (organization),
--             245_integration_connections.sql (integration_connections).
--
-- Expand only (R6): two new tables and their indexes. No rename, no drop and
-- no change to an existing table. Old code names no new object. A NOT NULL
-- with no default stands only on a column of a NEW table that the one writer
-- always fills (`owner_email`, `title`, `entry_id`). This is the shape of 307.
-- Idempotent per infra/postgres/README.md: CREATE TABLE IF NOT EXISTS, CREATE
-- INDEX IF NOT EXISTS and an RLS block guarded on relforcerowsecurity.
-- Tenant-scoped (R5): `organization_id` with ENABLE and FORCE row level
-- security and one policy with USING and WITH CHECK, as 307 does.
-- `REFERENCES` comes before `DEFAULT`, because the tenancy ratchet in
-- tests/unit/test_tenancy_boundary.py matches the two with no comma between.
-- ⚠️ RLS covers the org only. The person is a second axis: the read and each
-- write of the routes also filter on the owner or on the attendee row (§4.1).
--
-- The columns for CE-10 and CE-11 land now, so the sync needs no second
-- migration: the unique key of a provider event, `external_series_id`,
-- `original_start_date`, `external_url` and the three write-out columns.
-- The sync cursor has NO column here. It lives on the connection row, in
-- `integration_connections.provider_meta` (245).
--
-- ⚠️ `connection_id` has NO cascade, and no SET NULL. A Metorite event that
-- CE-11 wrote out carries the connection of that account. A cascade would
-- delete the member's OWN events on a disconnect, and SET NULL breaks the
-- pair CHECK. So a direct delete of a connection that an entry names fails.
-- The disconnect of CE-10 deletes the `external` rows of the connection, then
-- clears the five provider columns of the `metorite` rows, and only then may
-- it delete the connection. An organization delete cascades through both
-- tables in one statement, so it still works.
--
-- The number is taken at build time (R1). On 2026-10-31 origin/main held 317
-- at the top and branch ws66-re9b-push held 318. Check it again at merge.
-- Pinned by tests/unit/test_calendar_entries_r8.py, which finds this file by
-- CONTENT, never by number (R1).
-- ============================================================================

BEGIN;

CREATE TABLE IF NOT EXISTS calendar_entries (
    organization_id  UUID NOT NULL
                     REFERENCES organization (id) ON DELETE CASCADE
                     DEFAULT current_setting('app.tenant_id', true)::uuid,
    id               UUID NOT NULL DEFAULT gen_random_uuid(),
    -- The member who owns the entry, lower case. For `external`, the member
    -- whose account it came from. The session decides it, never a body.
    owner_email      TEXT NOT NULL,
    kind             TEXT NOT NULL DEFAULT 'event',
    title            TEXT NOT NULL,
    notes            TEXT NULL,
    location         TEXT NULL,
    -- A timed entry. NULL for an all-day entry.
    starts_at        TIMESTAMPTZ NULL,
    ends_at          TIMESTAMPTZ NULL,
    -- An all-day entry. NULL for a timed entry. A date never moves with a
    -- zone. `end_date` is EXCLUSIVE: a one-day entry on 8 March has
    -- start_date 2026-03-08 and end_date 2026-03-09, as Google and Outlook
    -- send it.
    start_date       DATE NULL,
    end_date         DATE NULL,
    -- The IANA zone of the entry. A repeat rule expands in it (CE-8). The
    -- read stores it and never applies it.
    tz               TEXT NULL,
    -- An RFC 5545 RRULE, or NULL. Stored as text. CE-8 expands it.
    recurrence       TEXT NULL,
    -- The Metorite master of a changed or cancelled instance, and the start
    -- that the instance replaces. `original_start_date` names the instance
    -- of an all-day series, because `original_start` is an instant.
    series_id        UUID NULL,
    original_start   TIMESTAMPTZ NULL,
    original_start_date DATE NULL,
    -- The master at the provider: Google recurringEventId, Outlook
    -- seriesMasterId. `series_id` cannot hold it.
    external_series_id TEXT NULL,
    status           TEXT NOT NULL DEFAULT 'confirmed',
    busy             BOOLEAN NOT NULL DEFAULT true,
    conference_url   TEXT NULL,
    -- Links. Plain columns with no foreign key. A later release adds the
    -- keys (expand, then contract).
    task_id          UUID NULL,
    crm_deal_id      UUID NULL,
    crm_contact_id   UUID NULL,
    meeting_id       UUID NULL,
    source           TEXT NOT NULL DEFAULT 'metorite',
    -- The account of a synced or written-out entry. NO ACTION on delete, on
    -- purpose (see the header and the column comment).
    connection_id    UUID NULL REFERENCES integration_connections (id),
    external_id      TEXT NULL,
    etag             TEXT NULL,
    external_url     TEXT NULL,
    -- The write-out of CE-11. NULL means the switch is off.
    push_to_provider TEXT NULL,
    provider_sync_state TEXT NULL,
    provider_sync_error TEXT NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (id),
    CONSTRAINT calendar_entries_owner_email CHECK (
        owner_email <> '' AND owner_email = lower(owner_email)),
    CONSTRAINT calendar_entries_title CHECK (
        char_length(title) BETWEEN 1 AND 200),
    CONSTRAINT calendar_entries_kind CHECK (kind IN ('event', 'external')),
    CONSTRAINT calendar_entries_source CHECK (
        source IN ('metorite', 'google', 'outlook')),
    CONSTRAINT calendar_entries_status CHECK (
        status IN ('confirmed', 'tentative', 'cancelled')),
    -- Exactly one pair: a timed pair, or an all-day pair with an exclusive
    -- end.
    CONSTRAINT calendar_entries_time CHECK (
        (starts_at IS NOT NULL AND ends_at IS NOT NULL AND ends_at >= starts_at
         AND start_date IS NULL AND end_date IS NULL)
        OR (start_date IS NOT NULL AND end_date IS NOT NULL AND end_date > start_date
            AND starts_at IS NULL AND ends_at IS NULL)),
    -- A copy of a provider event names its provider, its account and its id.
    CONSTRAINT calendar_entries_external CHECK (
        kind <> 'external'
        OR (source IN ('google', 'outlook')
            AND connection_id IS NOT NULL AND external_id IS NOT NULL)),
    -- An event made in Metorite stays `metorite`. It may still carry a
    -- connection and an external id, after CE-11 writes it out.
    CONSTRAINT calendar_entries_event_source CHECK (
        kind <> 'event' OR source = 'metorite'),
    CONSTRAINT calendar_entries_external_pair CHECK (
        (connection_id IS NULL) = (external_id IS NULL)),
    CONSTRAINT calendar_entries_push_to_provider CHECK (
        push_to_provider IS NULL OR push_to_provider IN ('google', 'outlook')),
    CONSTRAINT calendar_entries_provider_sync_state CHECK (
        provider_sync_state IS NULL
        OR provider_sync_state IN ('pending', 'sent', 'failed')),
    CONSTRAINT calendar_entries_provider_sync_error CHECK (
        char_length(provider_sync_error) <= 500),
    -- A write-out state needs a provider to write to.
    CONSTRAINT calendar_entries_sync_needs_push CHECK (
        provider_sync_state IS NULL OR push_to_provider IS NOT NULL),
    -- `sent` means the provider gave an id back.
    CONSTRAINT calendar_entries_sent_has_id CHECK (
        provider_sync_state IS DISTINCT FROM 'sent' OR external_id IS NOT NULL),
    -- A copy of a provider event is never written back.
    CONSTRAINT calendar_entries_external_no_push CHECK (
        kind <> 'external'
        OR (push_to_provider IS NULL AND provider_sync_state IS NULL))
);

-- The read of §3.4: my timed entries, and my all-day entries, by start.
CREATE INDEX IF NOT EXISTS calendar_entries_owner_start_idx
    ON calendar_entries (organization_id, owner_email, starts_at);
CREATE INDEX IF NOT EXISTS calendar_entries_owner_date_idx
    ON calendar_entries (organization_id, owner_email, start_date);
-- The expander of CE-8 needs every series, an open-ended one too.
CREATE INDEX IF NOT EXISTS calendar_entries_recurring_idx
    ON calendar_entries (organization_id, owner_email)
    WHERE recurrence IS NOT NULL;
-- The rows of one connection, for the sync and the disconnect of CE-10.
CREATE INDEX IF NOT EXISTS calendar_entries_connection_idx
    ON calendar_entries (organization_id, connection_id);
-- One row for each provider event of each connection, for EVERY source, so
-- a second sync adds nothing and a written-out event gets no twin.
CREATE UNIQUE INDEX IF NOT EXISTS calendar_entries_external_uq
    ON calendar_entries (organization_id, connection_id, external_id)
    WHERE connection_id IS NOT NULL AND external_id IS NOT NULL;
-- The write-outs that wait or failed, for the retry of CE-11.
CREATE INDEX IF NOT EXISTS calendar_entries_provider_retry_idx
    ON calendar_entries (organization_id, provider_sync_state)
    WHERE provider_sync_state IN ('pending', 'failed');

CREATE TABLE IF NOT EXISTS calendar_entry_attendees (
    organization_id  UUID NOT NULL
                     REFERENCES organization (id) ON DELETE CASCADE
                     DEFAULT current_setting('app.tenant_id', true)::uuid,
    id               UUID NOT NULL DEFAULT gen_random_uuid(),
    entry_id         UUID NOT NULL REFERENCES calendar_entries (id) ON DELETE CASCADE,
    -- Set when the person is a member of this organization, lower case.
    -- NULL for a guest. Only this column gives a member the entry (§4.1).
    member_email     TEXT NULL,
    -- The address and the name of a guest.
    email            TEXT NULL,
    display_name     TEXT NULL,
    role             TEXT NOT NULL DEFAULT 'attendee',
    rsvp             TEXT NOT NULL DEFAULT 'needs_action',
    -- This member's own lead time. NULL reads the member default (CE-6).
    lead_minutes     INTEGER NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (id),
    CONSTRAINT calendar_entry_attendees_role CHECK (
        role IN ('organizer', 'attendee')),
    CONSTRAINT calendar_entry_attendees_rsvp CHECK (
        rsvp IN ('needs_action', 'accepted', 'tentative', 'declined')),
    CONSTRAINT calendar_entry_attendees_who CHECK (
        member_email IS NOT NULL OR email IS NOT NULL),
    CONSTRAINT calendar_entry_attendees_member_email CHECK (
        member_email IS NULL
        OR (member_email <> '' AND member_email = lower(member_email))),
    CONSTRAINT calendar_entry_attendees_email CHECK (
        email IS NULL OR email <> ''),
    CONSTRAINT calendar_entry_attendees_lead CHECK (
        lead_minutes IS NULL OR lead_minutes >= 0)
);

-- One row for each person on an entry. It also serves the cascade of the
-- foreign key, which would otherwise scan the table.
CREATE UNIQUE INDEX IF NOT EXISTS calendar_entry_attendees_person_uq
    ON calendar_entry_attendees (entry_id, (COALESCE(member_email, email)));
-- The entries that I attend.
CREATE INDEX IF NOT EXISTS calendar_entry_attendees_member_idx
    ON calendar_entry_attendees (organization_id, member_email, entry_id);

DO $rls$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_class
         WHERE oid = to_regclass('calendar_entries')
           AND relrowsecurity
           AND relforcerowsecurity
    ) THEN
        ALTER TABLE calendar_entries ENABLE ROW LEVEL SECURITY;
        ALTER TABLE calendar_entries FORCE  ROW LEVEL SECURITY;
        DROP POLICY IF EXISTS calendar_entries_tenant_isolation ON calendar_entries;
        CREATE POLICY calendar_entries_tenant_isolation ON calendar_entries
            USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
            WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_class
         WHERE oid = to_regclass('calendar_entry_attendees')
           AND relrowsecurity
           AND relforcerowsecurity
    ) THEN
        ALTER TABLE calendar_entry_attendees ENABLE ROW LEVEL SECURITY;
        ALTER TABLE calendar_entry_attendees FORCE  ROW LEVEL SECURITY;
        DROP POLICY IF EXISTS calendar_entry_attendees_tenant_isolation
            ON calendar_entry_attendees;
        CREATE POLICY calendar_entry_attendees_tenant_isolation ON calendar_entry_attendees
            USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
            WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);
    END IF;
END
$rls$;

COMMENT ON TABLE calendar_entries IS
    'A time that is not a to-do (D138): an event made in Metorite, or a '
    'read-only copy of a provider event. RLS covers the org. Each read and '
    'write also filters on owner_email or on the attendee row of the member. '
    'calendar_events_reminders.md §3.3.';
COMMENT ON TABLE calendar_entry_attendees IS
    'One person on a calendar entry. A row with member_email gives that '
    'member the entry. A guest row (member_email NULL) gives nobody the '
    'entry. calendar_events_reminders.md §3.3 and §4.1.';
COMMENT ON COLUMN calendar_entries.end_date IS
    'EXCLUSIVE. A one-day entry on 8 March ends on 9 March, as Google and '
    'Outlook send it, so the sync of CE-10 copies it with no change.';
COMMENT ON COLUMN calendar_entries.connection_id IS
    'The integration_connections row of a synced or written-out entry. NO '
    'ACTION on delete, on purpose: a cascade would delete the own events of '
    'a member on a disconnect. The disconnect of CE-10 deletes the external '
    'rows of the connection, then clears connection_id, external_id, '
    'push_to_provider, provider_sync_state and provider_sync_error on the '
    'metorite rows, and only then may it delete the connection.';
COMMENT ON COLUMN calendar_entries.external_id IS
    'Unique per connection and external id for EVERY source '
    '(calendar_entries_external_uq). A Metorite event written out keeps '
    'source=metorite and gets its provider id here. The read sync updates '
    'it, never copies it. Google singleEvents=true gives each series '
    'instance its own id, so the key holds for instances.';
COMMENT ON COLUMN calendar_entries.push_to_provider IS
    'The provider whose connection this event is written to (CE-11). NULL '
    'means the switch is off.';
COMMENT ON COLUMN calendar_entries.provider_sync_state IS
    'pending, sent or failed. NULL means no write-out is wanted. A failed '
    'write keeps the row, so the event is never lost.';
COMMENT ON COLUMN calendar_entries.provider_sync_error IS
    'The last write-out error, at most 500 characters. It holds no token.';
COMMENT ON COLUMN calendar_entries.busy IS
    'Default true. Google transparency=transparent and Graph showAs=free '
    'set busy=false.';
COMMENT ON COLUMN calendar_entries.status IS
    'confirmed, tentative or cancelled. Google status=cancelled and Graph '
    '@removed set status=cancelled. The read returns a cancelled entry with '
    'its status (CE-8 hides it).';

COMMIT;
