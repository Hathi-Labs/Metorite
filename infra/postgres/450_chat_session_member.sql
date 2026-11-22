-- ============================================================================
-- 450_chat_session_member.sql - the per-member row of a chat, and its pin
-- (WS-CO CO-11, task 3510).
--
-- What: one new table. `chat_session_member` holds one row for each chat and
--       each member. This file gives it one value: `pinned_at`, the time when
--       the member pinned the chat, or NULL. gateway/chat_session_member.py is
--       the one module that runs SQL on the table. The member purge
--       (gateway/routes/admin/members.py, `chat_member_rows`) deletes rows too.
-- Why:  chat_organization.md §10 and §18 (Pin, CO-11). A pin is per member.
--       A colleague in the same room sees no pin of the member, so the pin
--       cannot be a column of `chat_session`.
-- Later: CO-2 (task 3501) adds `topic_id` and `set_by` to this table as
--       nullable columns, in its own file (an expand step, R6). This file
--       does not add them, because no code writes them yet.
-- Depends on: 02_chat_history.sql (chat_session), 130_org_access_control.sql
--             (organization).
--
-- THE LIFE OF A ROW. A pin inserts the row, or sets `pinned_at` on it. A
-- second pin keeps the first time. An unpin sets `pinned_at` to NULL and
-- keeps the row, so the topic of CO-2 can live in the same row later. A
-- Delete of the chat deletes the row (the cascade). The member purge deletes
-- each row of the member in the bound org. An archive of the chat changes no
-- row: the read leaves out an archived chat (`chat_session.archived_at`), so
-- an archive takes the chat out of Pinned for each member at once.
--
-- The key. The spec names `(session_id, member_email)`. `chat_session.id` is
-- unique across each org, so the pair is unique too. `member_email` is the
-- lower-case address of the member (the CHECK), the column name that the
-- purge fences read.
--
-- The foreign key to the chat is the plain `chat_session (id)` with ON DELETE
-- CASCADE, as 138 and 139 do. `chat_session` has no UNIQUE
-- (organization_id, id), and its `organization_id` comes from the generated
-- phases, so a composite key or a same-org trigger is not possible on each
-- database. A foreign key check skips RLS. So the one writer never names a
-- chat by its id alone: its INSERT selects the chat from `chat_session`
-- under the bound tenant (FORCE RLS) and the visibility rule of
-- `gateway/rooms.py`. A chat of another org, or a chat that the member
-- cannot read, gives no row. tests/unit/test_chat_pin_r8.py pins both.
--
-- Expand only (R6): one new table and one index. No rename, no drop, and no
-- change to an old object. The old code never names the table. Idempotent:
-- CREATE TABLE IF NOT EXISTS, CREATE INDEX IF NOT EXISTS, and an RLS block
-- guarded on relforcerowsecurity. The foreign key to `chat_session` takes a
-- SHARE ROW EXCLUSIVE lock on that table until COMMIT, and each chat read
-- and write needs it. So the file sets `lock_timeout` first, as 449 does.
-- Tenant-scoped (R5): `organization_id` with ENABLE and FORCE row level
-- security, and one policy with USING and WITH CHECK, as 449 does. The org
-- of a new row comes from the DEFAULT, the bound tenant. The writer never
-- names it. `REFERENCES` comes before `DEFAULT`, because the tenancy ratchet
-- in tests/unit/test_tenancy_boundary.py matches the two with no comma
-- between. The generated phases carry the table.
--
-- The number is taken at build time (R1): 450 was booked for this task, and
-- main held 449 as its highest file on 2026-11-16. Check it again at merge.
-- Pinned by tests/unit/test_chat_pin.py and tests/unit/test_chat_pin_r8.py,
-- which find this file by CONTENT, never by number (R1).
-- ============================================================================

BEGIN;

SET LOCAL lock_timeout = '5s';

CREATE TABLE IF NOT EXISTS chat_session_member (
    organization_id  UUID NOT NULL
                     REFERENCES organization (id) ON DELETE CASCADE
                     DEFAULT current_setting('app.tenant_id', true)::uuid,
    -- The chat. A Delete of the chat deletes the row.
    session_id       TEXT NOT NULL REFERENCES chat_session (id) ON DELETE CASCADE,
    -- The member, in lower case.
    member_email     TEXT NOT NULL,
    -- When the member pinned the chat. NULL: the chat is not pinned.
    pinned_at        TIMESTAMPTZ,
    PRIMARY KEY (session_id, member_email),
    CONSTRAINT chat_session_member_email CHECK (
        member_email <> '' AND member_email = lower(member_email))
);

-- The read of the chat list (the pins of one member) and the member purge.
CREATE INDEX IF NOT EXISTS chat_session_member_member_idx
    ON chat_session_member (organization_id, member_email);

DO $rls$
DECLARE
    t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY['chat_session_member'] LOOP
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

COMMENT ON TABLE chat_session_member IS
    'The per-member row of a chat (WS-CO CO-11). pinned_at is the pin of one '
    'member. A colleague in the same room reads no pin of the member. A Delete '
    'of the chat and the member purge delete the row. RLS covers the org.';

COMMIT;
