-- ============================================================================
-- 459_chat_card_record.sql - the card record: each approved write card of a
-- chat, with no model text (WS-CO CO-17, task 3516).
--
-- What: one new table. `chat_card_record` holds one row for each write card
--       that a member approved, in one chat: the card id, the member, the
--       outcome key, the act kind, the link of the record that the act
--       made and the id of the message that a send made. orchestrator/card_record.py is the one module that runs SQL on
--       the table. The member purge (gateway/routes/admin/members.py,
--       `chat_card_rows`) deletes the rows of a leaver.
-- Why:  chat_organization.md §16.3. The condense note (CO-16) may use an
--       approved card, but main kept no durable record of one. The card title
--       and detail are text that the model can shape, so the row holds none.
-- Depends on: 02_chat_history.sql (chat_session), 130_org_access_control.sql
--             (organization).
--
-- THE ROW HAS NO FREE TEXT. `outcome_key` is one word of ask_tools
--       CARD_OUTCOMES, and the CHECK allows `approved` only: a declined card
--       writes no row. `act_kind` is the module and function name of the tool
--       that drew the card, as the CHECK shows (lower-case words, digits, dot
--       and underscore). `record_link` is NULL or an app path that starts with
--       `/` and holds no space, up to 300 characters. No column holds a title,
--       a detail, a body or a tool output.
--
-- THE LIFE OF A ROW. An approve inserts the row, with no link. The tool that
-- made a record sets `record_link` once (a later set changes nothing). A
-- second insert of the same card changes nothing. A Delete of the chat
-- deletes the row (the cascade). The member purge deletes each row of the
-- member in the bound org.
--
-- THE READER. Only the member who approved the card reads the row. Each read
-- filters on `member_email` and on the visibility rule of the chat
-- (gateway/rooms.py), beside RLS. A colleague in the same room reads none.
--
-- The foreign key to the chat is the plain `chat_session (id)` with ON DELETE
-- CASCADE, as 450 does, and the one writer selects the chat under the bound
-- tenant, so a chat of another org gives no row. A foreign key check skips
-- RLS, so no statement names a chat by its id alone.
--
-- Expand only (R6): one new table and two indexes. No rename, no drop, no
-- change to an old object. The old code never names the table. Idempotent.
-- The foreign key takes a SHARE ROW EXCLUSIVE lock on `chat_session`, so the
-- file sets `lock_timeout` first, as 450 does. Tenant-scoped (R5):
-- `organization_id` with ENABLE and FORCE row level security and one policy
-- with USING and WITH CHECK. `REFERENCES` comes before `DEFAULT` (the
-- tenancy ratchet).
--
-- The number is taken at build time (R1): 459 was booked for this task. Check
-- it again at merge. Pinned by tests/unit/test_card_record.py and
-- tests/unit/test_card_record_r8.py, which find this file by CONTENT.
-- ============================================================================

BEGIN;

SET LOCAL lock_timeout = '5s';

CREATE TABLE IF NOT EXISTS chat_card_record (
    organization_id  UUID NOT NULL
                     REFERENCES organization (id) ON DELETE CASCADE
                     DEFAULT current_setting('app.tenant_id', true)::uuid,
    id               UUID NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
    -- The chat. A Delete of the chat deletes the row.
    session_id       TEXT NOT NULL REFERENCES chat_session (id) ON DELETE CASCADE,
    -- The member who approved, in lower case.
    member_email     TEXT NOT NULL,
    -- The id of the card (32 hex digits). One row for each card.
    request_id       TEXT NOT NULL,
    -- One key of CARD_OUTCOMES. Only `approved` is stored.
    outcome_key      TEXT NOT NULL DEFAULT 'approved',
    -- The tool that drew the card: module.function. Never model text.
    act_kind         TEXT NOT NULL,
    -- The app path of the record that the act made, or NULL.
    record_link      TEXT,
    -- The id of the message that a send card sent (a token), or NULL. It is
    -- the answer to "was it sent" (task 3594). Never mail text.
    message_id       TEXT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT chat_card_record_email CHECK (
        member_email <> '' AND member_email = lower(member_email)),
    CONSTRAINT chat_card_record_outcome CHECK (outcome_key = 'approved'),
    CONSTRAINT chat_card_record_act CHECK (act_kind ~ '^[a-z0-9_.]{1,120}$'),
    CONSTRAINT chat_card_record_request CHECK (request_id ~ '^[0-9a-f]{32}$'),
    CONSTRAINT chat_card_record_link CHECK (
        record_link IS NULL
        OR (record_link ~ '^/[^[:space:]]*$' AND length(record_link) <= 300)),
    CONSTRAINT chat_card_record_message CHECK (
        message_id IS NULL OR message_id ~ '^[A-Za-z0-9_.:@<>+=-]{1,200}$'),
    CONSTRAINT chat_card_record_once UNIQUE (session_id, request_id)
);

-- The read of one member's cards in a chat, and the member purge.
CREATE INDEX IF NOT EXISTS chat_card_record_member_idx
    ON chat_card_record (organization_id, member_email, session_id);

DO $rls$
DECLARE
    t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY['chat_card_record'] LOOP
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

COMMENT ON TABLE chat_card_record IS
    'The card record (WS-CO CO-17): one row for each write card that a member '
    'approved in a chat. No model text: the outcome key, the act kind and the '
    'link of the record only. Only the member reads it. A Delete of the chat '
    'and the member purge delete the row. RLS covers the org.';

COMMIT;
