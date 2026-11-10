-- ============================================================================
-- 307_reminders.sql — the reminder store and its ledger (WS-66 RM-1, D114).
--
-- What: two new tables. `reminders` holds one private nudge of one member:
--       a time, one line of text, a kind, an optional link to one object,
--       and its state. `reminder_events` is the ledger: one row for each
--       change and each delivery. The routes of
--       gateway/routes/projects/reminders.py write both, and each change
--       writes its ledger row in the same transaction.
-- Why:  D114 (2026-10-25). A reminder is a nudge, not work, so it has its
--       own store and stays out of the task store. Spec
--       project-docs/specs/calendar_events_reminders.md §5.1 to §5.3, §10.3.
-- Depends on: 130_org_access_control.sql (organization),
--             146_projects.sql (pm_tasks).
--
-- Expand only (R6): two new tables and four indexes. No rename, no drop, and
-- no change to an existing table. Old code names no new object. A NOT NULL
-- with no default stands only on a column of a NEW table that the one writer
-- always fills (`member_email`, `title`, `fire_at`, `reminder_id`, `event`).
-- No running code writes these tables, so no old INSERT can miss it. This is
-- the shape of 290 and 296.
-- Idempotent per infra/postgres/README.md: CREATE TABLE IF NOT EXISTS, CREATE
-- INDEX IF NOT EXISTS and an RLS block guarded on relforcerowsecurity.
-- Tenant-scoped (R5): `organization_id` with ENABLE and FORCE row level
-- security and one policy with USING and WITH CHECK, in the shape of 290.
-- `REFERENCES` comes before `DEFAULT`, because the tenancy ratchet in
-- tests/unit/test_tenancy_boundary.py matches the two with no comma between.
-- ⚠️ RLS covers the org only. The member is a second axis: each read and each
-- write of the routes also filters on `member_email`. Only the owner of a
-- reminder reads it. A colleague and an admin do not (§5.1).
-- The number is taken at build time (R1). It was 305 at first. On 2026-10-25
-- origin/main held 300 to 304, two open branches claimed 305 (kc-3-sync and
-- sw10-stranded) and PN-1 held 306, so this file moved to 307 before merge.
-- Check it again at merge. Pinned by tests/unit/test_reminders_r8.py, which
-- finds this file by CONTENT, never by number (R1).
-- ============================================================================

BEGIN;

CREATE TABLE IF NOT EXISTS reminders (
    organization_id  UUID NOT NULL
                     REFERENCES organization (id) ON DELETE CASCADE
                     DEFAULT current_setting('app.tenant_id', true)::uuid,
    id               UUID NOT NULL DEFAULT gen_random_uuid(),
    -- The owner, lower case. The session decides it, never a request body.
    member_email     TEXT NOT NULL,
    title            TEXT NOT NULL,
    notes            TEXT NULL,
    -- `action` needs the member to confirm. `notice` closes when opened.
    kind             TEXT NOT NULL DEFAULT 'action',
    -- The optional link of §5.1. A pointer only: it copies nothing.
    subject_kind     TEXT NULL,
    subject_id       TEXT NULL,
    -- The next time the reminder fires, and the zone the member set it in.
    fire_at          TIMESTAMPTZ NOT NULL,
    tz               TEXT NULL,
    state            TEXT NOT NULL DEFAULT 'scheduled',
    -- When the loop (RM-2) next looks at a `due` row.
    next_check_at    TIMESTAMPTZ NULL,
    -- The first fire. The 72-hour limit of §5.4 counts from it.
    first_fired_at   TIMESTAMPTZ NULL,
    -- A move that Metorite makes adds to `cycles`. A move that the member
    -- asks for adds to `snoozes` (§5.4, "Who counts what").
    cycles           INTEGER NOT NULL DEFAULT 0,
    snoozes          INTEGER NOT NULL DEFAULT 0,
    estimate_minutes INTEGER NULL,
    important        BOOLEAN NOT NULL DEFAULT false,
    resolution       TEXT NULL,
    resolved_at      TIMESTAMPTZ NULL,
    -- The task that "Make it a task" made. SET NULL, never CASCADE: a deleted
    -- task must not delete the history of the reminder.
    promoted_task_id UUID NULL REFERENCES pm_tasks (id) ON DELETE SET NULL,
    source           TEXT NOT NULL DEFAULT 'app',
    -- The chat session that made it, for the open-in-chat flow (§5.7). The
    -- reminder does not depend on it.
    source_session_id TEXT NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (id),
    CONSTRAINT reminders_member_email CHECK (
        member_email <> '' AND member_email = lower(member_email)),
    CONSTRAINT reminders_title CHECK (
        char_length(title) BETWEEN 1 AND 200),
    CONSTRAINT reminders_kind CHECK (kind IN ('action', 'notice')),
    CONSTRAINT reminders_subject_kind CHECK (
        subject_kind IS NULL
        OR subject_kind IN ('task', 'email_thread', 'crm_contact', 'wa_chat', 'project')),
    CONSTRAINT reminders_subject_pair CHECK (
        (subject_kind IS NULL) = (subject_id IS NULL)),
    CONSTRAINT reminders_state CHECK (
        state IN ('scheduled', 'due', 'unconfirmed', 'done', 'dropped', 'cancelled')),
    CONSTRAINT reminders_counts CHECK (cycles >= 0 AND snoozes >= 0),
    CONSTRAINT reminders_estimate CHECK (
        estimate_minutes IS NULL OR estimate_minutes > 0),
    CONSTRAINT reminders_resolution CHECK (
        resolution IS NULL
        OR resolution IN ('done', 'dropped', 'evidence', 'promoted', 'gave_up')),
    CONSTRAINT reminders_source CHECK (
        source IN ('chat', 'app', 'whatsapp', 'api'))
);

-- The loop (RM-2) claims due rows by state and time.
CREATE INDEX IF NOT EXISTS reminders_fire_idx
    ON reminders (organization_id, state, fire_at);
CREATE INDEX IF NOT EXISTS reminders_check_idx
    ON reminders (organization_id, state, next_check_at);
-- The lists of one member.
CREATE INDEX IF NOT EXISTS reminders_member_idx
    ON reminders (organization_id, member_email, state);

CREATE TABLE IF NOT EXISTS reminder_events (
    organization_id  UUID NOT NULL
                     REFERENCES organization (id) ON DELETE CASCADE
                     DEFAULT current_setting('app.tenant_id', true)::uuid,
    id               UUID NOT NULL DEFAULT gen_random_uuid(),
    reminder_id      UUID NOT NULL REFERENCES reminders (id) ON DELETE CASCADE,
    at               TIMESTAMPTZ NOT NULL DEFAULT now(),
    event            TEXT NOT NULL,
    -- The channel of a delivery. NULL for a change.
    channel          TEXT NULL,
    -- For example the old and the new `fire_at`, the names of the fields
    -- that an edit changed, or the state before an answer (`from_state`),
    -- which a reopen reads back.
    detail           JSONB NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (id),
    -- The fourteen events of §5.2, `edited` and `reopened`. An edit changes
    -- text, the kind, the link, the estimate or `important`, and moves no
    -- state and no time. A reopen undoes a done or a drop (D-CF-5).
    -- Engineering decisions, 2026-10-25: 307 has not shipped, so the list
    -- widens here and not in a second migration.
    CONSTRAINT reminder_events_event CHECK (
        event IN ('created', 'fired', 'check_back', 'briefed', 'rescheduled',
                  'snoozed', 'opened', 'done', 'dropped', 'promoted',
                  'gave_up', 'evidence', 'delivered', 'failed', 'edited',
                  'reopened')),
    CONSTRAINT reminder_events_channel CHECK (
        channel IS NULL OR channel IN ('feed', 'push', 'chat', 'whatsapp'))
);

-- The ledger of one reminder, in order. It also serves the cascade of the
-- foreign key, which would otherwise scan the table.
CREATE INDEX IF NOT EXISTS reminder_events_reminder_idx
    ON reminder_events (reminder_id, at);

DO $rls$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_class
         WHERE oid = to_regclass('reminders')
           AND relrowsecurity
           AND relforcerowsecurity
    ) THEN
        ALTER TABLE reminders ENABLE ROW LEVEL SECURITY;
        ALTER TABLE reminders FORCE  ROW LEVEL SECURITY;
        DROP POLICY IF EXISTS reminders_tenant_isolation ON reminders;
        CREATE POLICY reminders_tenant_isolation ON reminders
            USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
            WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_class
         WHERE oid = to_regclass('reminder_events')
           AND relrowsecurity
           AND relforcerowsecurity
    ) THEN
        ALTER TABLE reminder_events ENABLE ROW LEVEL SECURITY;
        ALTER TABLE reminder_events FORCE  ROW LEVEL SECURITY;
        DROP POLICY IF EXISTS reminder_events_tenant_isolation ON reminder_events;
        CREATE POLICY reminder_events_tenant_isolation ON reminder_events
            USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
            WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);
    END IF;
END
$rls$;

COMMENT ON TABLE reminders IS
    'A private nudge of one member (D114). Only the owner reads it: each query '
    'filters on member_email beside RLS. Only gateway/routes/projects/'
    'reminders.py writes it in RM-1. calendar_events_reminders.md §5.2.';
COMMENT ON TABLE reminder_events IS
    'The ledger of reminders: one row for each change and each delivery, in '
    'the transaction of the change. calendar_events_reminders.md §5.2.';

COMMIT;
