-- ============================================================================
-- 461_chat_join_log.sql - the shadow record of the join match (WS-CO CO-4,
-- task 3503).
--
-- What: one new table. `chat_join_log` holds one row for each new chat whose
--       first message the match read. The row holds the outcome that
--       chat_organization.md §7.1 would take (join, carry, ask or clean), the
--       chat that it would pick, the score and its band, a reason code, and
--       the first bar of §7.2 that stopped a join. The member sees nothing.
--       gateway/chat_join_shadow.py is the one module that runs SQL on the
--       table. The member purge (gateway/routes/admin/members.py,
--       `chat_join_rows`) deletes rows too.
-- Why:  chat_organization.md §10 and §11 row CO-4. One week of rows tells the
--       owner which outcomes the match would take, and lets CO-4 tune the 14
--       days and the score bands before CO-5 shows a chip (D163.7).
-- Later: CO-5 (task 3504) writes `override_kind` and `overridden_at` when a
--       member presses the chip. This file adds them as NULL columns, so CO-5
--       needs no new migration for them.
-- Depends on: 02_chat_history.sql (chat_session), 130_org_access_control.sql
--             (organization).
--
-- NO TEXT OF THE MEMBER. No column holds the first message, a title, a
-- summary or any text of a chat. `reason` and `would_join_blocked_by` are
-- codes from a fixed list (the CHECKs). `candidate_session_id` is an id
-- only. tests/unit/test_chat_join_shadow.py reads the column list of this file
-- and refuses a column that could carry text.
--
-- THE LIFE OF A ROW. The first message of a new chat inserts the row, and a
-- second insert for the same chat does nothing (the key). A Delete of the new
-- chat deletes the row (the cascade). A Delete of the candidate chat keeps
-- the row: `candidate_session_id` has no foreign key on purpose, so the week
-- of data stays whole, and the id of a deleted chat names nothing. The member
-- purge deletes each row of the member in the bound org.
--
-- The foreign key to the chat is the plain `chat_session (id)` with ON DELETE
-- CASCADE, as 450 does. A foreign key check skips RLS. So the one writer never
-- names a chat by its id alone: its INSERT selects the chat from
-- `chat_session` under the bound tenant (FORCE RLS), as the member's own
-- chat. A chat of another org, or of another member, gives no row.
-- tests/unit/test_chat_join_shadow_r8.py pins both.
--
-- Expand only (R6): one new table and two indexes. No rename, no drop, and no
-- change to an old object. The old code never names the table. Idempotent:
-- CREATE TABLE IF NOT EXISTS, CREATE INDEX IF NOT EXISTS, and an RLS block
-- guarded on relforcerowsecurity. The foreign key to `chat_session` takes a
-- SHARE ROW EXCLUSIVE lock on that table until COMMIT, and each chat read
-- and write needs it. So the file sets `lock_timeout` first, as 450 does.
-- Tenant-scoped (R5): `organization_id` with ENABLE and FORCE row level
-- security, and one policy with USING and WITH CHECK, as 450 does. The org
-- of a new row comes from the DEFAULT, the bound tenant. The writer never
-- names it. `REFERENCES` comes before `DEFAULT`, because the tenancy ratchet
-- in tests/unit/test_tenancy_boundary.py matches the two with no comma
-- between. The generated phases carry the table.
--
-- The number is taken at build time (R1): 461 was booked for this task.
-- Check it again at merge. The fences find this file by CONTENT, never by
-- number (R1).
-- ============================================================================

BEGIN;

SET LOCAL lock_timeout = '5s';

CREATE TABLE IF NOT EXISTS chat_join_log (
    organization_id        UUID NOT NULL
                           REFERENCES organization (id) ON DELETE CASCADE
                           DEFAULT current_setting('app.tenant_id', true)::uuid,
    -- The NEW chat, whose first message the match read. A Delete of the chat
    -- deletes the row. One row for each new chat.
    session_id             TEXT NOT NULL REFERENCES chat_session (id) ON DELETE CASCADE,
    -- The member, in lower case.
    member_email           TEXT NOT NULL,
    -- What §7.1 would do.
    outcome                TEXT NOT NULL,
    -- The chat that the outcome names, an id only. NULL for `clean`.
    candidate_session_id   TEXT,
    -- How many of the member's chats the match read.
    candidate_count        INTEGER NOT NULL DEFAULT 0,
    -- The score of the best chat, from 0 to 1, and its band.
    score                  NUMERIC(4, 3),
    band                   TEXT NOT NULL,
    -- True when the best chat made a record that the first message names
    -- (D163.4: the same record outranks similar words).
    record_hit             BOOLEAN NOT NULL DEFAULT false,
    -- The hours from the last activity of the best chat to the match, so
    -- CO-4 can tune the 14 days.
    candidate_idle_hours   INTEGER,
    -- Why, as a code.
    reason                 TEXT NOT NULL,
    -- The first bar of §7.2 that stopped a join of the best chat, as a code.
    would_join_blocked_by  TEXT,
    -- CO-5 writes these when the member overrides the chip.
    override_kind          TEXT,
    overridden_at          TIMESTAMPTZ,
    created_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (session_id),
    CONSTRAINT chat_join_log_email CHECK (
        member_email <> '' AND member_email = lower(member_email)),
    CONSTRAINT chat_join_log_outcome CHECK (
        outcome IN ('join', 'carry', 'ask', 'clean')),
    CONSTRAINT chat_join_log_candidate CHECK (
        (outcome = 'clean') = (candidate_session_id IS NULL)),
    CONSTRAINT chat_join_log_candidate_shape CHECK (
        candidate_session_id IS NULL
        OR (candidate_session_id <> '' AND char_length(candidate_session_id) <= 200)),
    CONSTRAINT chat_join_log_count CHECK (candidate_count >= 0),
    CONSTRAINT chat_join_log_score CHECK (score IS NULL OR (score >= 0 AND score <= 1)),
    CONSTRAINT chat_join_log_band CHECK (
        band IN ('strong', 'unsure', 'related', 'none')),
    CONSTRAINT chat_join_log_idle CHECK (
        candidate_idle_hours IS NULL OR candidate_idle_hours >= 0),
    CONSTRAINT chat_join_log_reason CHECK (
        reason IN ('no_candidates', 'no_match', 'same_record', 'same_words',
                   'unsure', 'related', 'older', 'blocked')),
    CONSTRAINT chat_join_log_blocked_by CHECK (
        would_join_blocked_by IS NULL
        OR would_join_blocked_by IN ('not_solo', 'archived', 'live_run', 'open_ask',
                                     'context_full', 'held', 'not_eligible')),
    CONSTRAINT chat_join_log_override CHECK (
        override_kind IS NULL
        OR override_kind IN ('dismissed', 'started_new', 'accepted')),
    CONSTRAINT chat_join_log_override_time CHECK (
        (override_kind IS NULL) = (overridden_at IS NULL))
);

-- The member purge, and a read of one member.
CREATE INDEX IF NOT EXISTS chat_join_log_member_idx
    ON chat_join_log (organization_id, member_email);
-- The week of data: the rows of one org, newest first.
CREATE INDEX IF NOT EXISTS chat_join_log_created_idx
    ON chat_join_log (organization_id, created_at DESC);

DO $rls$
DECLARE
    t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY['chat_join_log'] LOOP
        IF NOT EXISTS (
            SELECT 1 FROM pg_class
             WHERE oid = to_regclass(t)
               AND relrowsecurity
               AND relforcerowsecurity
        ) THEN
            EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
            EXECUTE format('ALTER TABLE %I FORCE  ROW LEVEL SECURITY', t);
            EXECUTE format('DROP POLICY IF EXISTS %I ON %I', t || '_tenant_isolation', t);
            EXECUTE format(
                'CREATE POLICY %I ON %I '
                'USING      (organization_id = current_setting(''app.tenant_id'', true)::uuid) '
                'WITH CHECK (organization_id = current_setting(''app.tenant_id'', true)::uuid)',
                t || '_tenant_isolation', t);
        END IF;
    END LOOP;
END
$rls$;

COMMENT ON TABLE chat_join_log IS
    'The shadow record of the join match (WS-CO CO-4). One row for each new '
    'chat: the outcome that the match would take, codes and ids only, and no '
    'text of the member. A Delete of the chat and the member purge delete the '
    'row. RLS covers the org.';

COMMIT;
