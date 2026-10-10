-- ============================================================================
-- 145_crm_zoho_sync.sql — the schema the two-way Zoho sync needs: per-row
--                         dirty tracking, delete tombstones, pull cursors.
--
-- What: spec project-docs/specs/crm_app.md §7.1 (WS-26b done-when 3).
--   Three additions, no changes to anything 144 created:
--     1. `zoho_dirty` + `zoho_synced_at` on the FOUR record tables. A native
--        write sets `zoho_dirty`; the push phase clears it and stamps
--        `zoho_synced_at`. A pull-applied write does NEITHER — that is the
--        echo suppression rule, and it is why the column pair is the sync's
--        whole state machine rather than a log.
--     2. `crm_zoho_tombstones` — a native delete of a zoho-linked row records
--        what to delete upstream, INSIDE the delete transaction. A tombstone
--        cannot be a column on a row that no longer exists.
--     3. `crm_sync_cursors` — one row per Zoho module carrying the incremental
--        pull watermark. Without a PERSISTED cursor an "incremental" pull
--        re-reads the whole tenant after every gateway restart.
--
-- Why: coexistence with Zoho is a faithful TWO-WAY sync until cutover
--   (D-CRM-7, owner-directed 2026-08-05), and every push routes through the
--   Action-Broker gate (D-CRM-8). None of that is expressible without somewhere
--   to record "this row changed here", "this row was deleted here", and "we
--   have read Zoho up to here".
--
-- Note on `source`: pulled rows carry the EXISTING 'import' value — the CHECK
--   vocabulary in 144 needs no new member and no ALTER.
--
-- Idempotent per infra/postgres/README.md: every CREATE TABLE / CREATE INDEX
-- carries IF NOT EXISTS, and every ALTER adds columns with ADD COLUMN IF NOT
-- EXISTS (which, unlike ADD CONSTRAINT, Postgres supports directly — so no
-- guarded DO $$ block is needed here). Pinned by tests/unit/test_crm_migration.py,
-- which reads this file as text: the unit suite runs no database.
--
-- Depends on: 144_crm.sql (the four record tables this alters).
-- ============================================================================

-- ── Dirty tracking on the four record tables ───────────────────────── §7.1

-- `zoho_dirty`: set by a NATIVE write (core.insert_row / core.update_row —
-- the one choke point every CRM write already passes through), cleared by the
-- push phase once Zoho has accepted the row. NOT NULL DEFAULT false so every
-- pre-existing row starts clean: a backfill of the whole table into Zoho is
-- not what applying this migration should mean.
-- `zoho_synced_at`: the last instant this row and its Zoho twin agreed. NULL
-- means "never pushed", which is also the honest state of a row that is dirty
-- and has not had a cycle yet.

-- The retry trio is the same shape on both queues (here and on
-- crm_zoho_tombstones below). Without it a row Zoho will NEVER accept — an
-- amount that overflows its field, a picklist value it rejects — is retried
-- every ten minutes forever, and because both queues are read oldest-first
-- under a LIMIT, a handful of those starve everything behind them.
--   `*_attempts`        counts consecutive failures; the reader skips rows
--                       past the give-up threshold (loudly, once).
--   `*_error`           the last upstream message, so the give-up is
--                       diagnosable without reading a week of logs.
--   `*_next_attempt_at` exponential backoff; NULL = eligible now.

ALTER TABLE crm_companies
    ADD COLUMN IF NOT EXISTS zoho_dirty           BOOLEAN NOT NULL DEFAULT false,
    ADD COLUMN IF NOT EXISTS zoho_synced_at       TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS zoho_push_attempts   INT NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS zoho_push_error      TEXT,
    ADD COLUMN IF NOT EXISTS zoho_next_attempt_at TIMESTAMPTZ;

ALTER TABLE crm_contacts
    ADD COLUMN IF NOT EXISTS zoho_dirty           BOOLEAN NOT NULL DEFAULT false,
    ADD COLUMN IF NOT EXISTS zoho_synced_at       TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS zoho_push_attempts   INT NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS zoho_push_error      TEXT,
    ADD COLUMN IF NOT EXISTS zoho_next_attempt_at TIMESTAMPTZ;

ALTER TABLE crm_leads
    ADD COLUMN IF NOT EXISTS zoho_dirty           BOOLEAN NOT NULL DEFAULT false,
    ADD COLUMN IF NOT EXISTS zoho_synced_at       TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS zoho_push_attempts   INT NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS zoho_push_error      TEXT,
    ADD COLUMN IF NOT EXISTS zoho_next_attempt_at TIMESTAMPTZ;

ALTER TABLE crm_deals
    ADD COLUMN IF NOT EXISTS zoho_dirty           BOOLEAN NOT NULL DEFAULT false,
    ADD COLUMN IF NOT EXISTS zoho_synced_at       TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS zoho_push_attempts   INT NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS zoho_push_error      TEXT,
    ADD COLUMN IF NOT EXISTS zoho_next_attempt_at TIMESTAMPTZ;

-- The activity push is a THIRD queue with the same failure mode: its rows are
-- selected by `zoho_id IS NULL` and drained by stamping one, so a note Zoho
-- keeps rejecting sits at the front of an oldest-first LIMIT forever. Same
-- trio, same contract. (No `zoho_dirty` here: an activity is a log entry, so
-- "has it changed" and "has it been pushed" are the same question — §7.1.)
ALTER TABLE crm_activities
    ADD COLUMN IF NOT EXISTS zoho_push_attempts   INT NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS zoho_push_error      TEXT,
    ADD COLUMN IF NOT EXISTS zoho_next_attempt_at TIMESTAMPTZ;

-- Partial: the push phase asks exactly one question — "what is dirty" — and
-- the dirty set is a handful of rows against a table that is almost entirely
-- clean. A full index on a boolean would be read by nothing.
CREATE INDEX IF NOT EXISTS idx_crm_organizations_zoho_dirty
    ON crm_companies (zoho_dirty) WHERE zoho_dirty;
CREATE INDEX IF NOT EXISTS idx_crm_contacts_zoho_dirty
    ON crm_contacts (zoho_dirty) WHERE zoho_dirty;
CREATE INDEX IF NOT EXISTS idx_crm_leads_zoho_dirty
    ON crm_leads (zoho_dirty) WHERE zoho_dirty;
CREATE INDEX IF NOT EXISTS idx_crm_deals_zoho_dirty
    ON crm_deals (zoho_dirty) WHERE zoho_dirty;

-- ── Delete tombstones ──────────────────────────────────────────────── §7.1

-- Deliberately NOT a foreign key to anything: the row it describes is gone by
-- the time anybody reads this, which is the entire point. `module` is the Zoho
-- module name to issue the DELETE against; `entity_type` is what it was on our
-- side, so the sync report can say "2 deals, 1 contact" rather than a count of
-- opaque ids.

CREATE TABLE IF NOT EXISTS crm_zoho_tombstones (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    module      TEXT NOT NULL,
    zoho_id     TEXT NOT NULL,
    entity_type TEXT NOT NULL
                    CHECK (entity_type IN
                        ('lead', 'deal', 'contact', 'organization', 'activity')),
    -- The person whose delete this was, for the audit trail the push writes.
    deleted_by  TEXT NOT NULL,
    deleted_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- NULL until the delete has actually been accepted by Zoho. The push phase
    -- reads `WHERE pushed_at IS NULL`, so a failed push is simply retried next
    -- cycle instead of being lost.
    pushed_at   TIMESTAMPTZ,
    -- The retry trio, same contract as the record tables above. A tombstone
    -- for a record Zoho 404s is NOT one of these: an absent record means the
    -- delete already achieved its goal, and the writer reports it as success.
    attempts        INT NOT NULL DEFAULT 0,
    last_error      TEXT,
    next_attempt_at TIMESTAMPTZ
);

-- The only query this table serves: the unpushed backlog, oldest first.
CREATE INDEX IF NOT EXISTS idx_crm_zoho_tombstones_unpushed
    ON crm_zoho_tombstones (deleted_at) WHERE pushed_at IS NULL;

-- ── Pull cursors ───────────────────────────────────────────────────── §7.1

-- One row per Zoho module. `module` is the primary key because a second cursor
-- for the same module is not a second opinion, it is a bug: two rows would let
-- the pull silently rewind.

CREATE TABLE IF NOT EXISTS crm_sync_cursors (
    module         TEXT PRIMARY KEY,
    -- The watermark sent as If-Modified-Since on the next pull. NULL = never
    -- pulled, which correctly means "read everything once".
    last_pulled_at TIMESTAMPTZ,
    -- When a cycle last TOUCHED this module, successful or not, so a module
    -- that is erroring every cycle is visible without reading logs.
    last_run_at    TIMESTAMPTZ,
    last_status    TEXT
);
