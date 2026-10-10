-- ============================================================================
-- 238_whatsapp_bot_messages.sql — the record of each message on the bot
-- number, and the channel marker of a chat thread.
--
-- What: `whatsapp_bot_messages`, one row for each message that a linked
--       member sends to the bot number (`direction = 'in'`), and one row for
--       each reply that the bot sends (`direction = 'out'`). An inbound row
--       starts as `received`. The run makes it `running`, then `sending`
--       just before the first part of a reply goes out, then `replied`,
--       `refused` or `failed`. A `sending` row is never run again, so a
--       reply goes out at most once. The sweep makes a row older than 24 hours
--       `expired`. `chat_session.channel` marks the thread of the channel,
--       and WAC-3 writes `whatsapp` there.
-- Why:  WS-47 WAC-3, `project-docs/specs/whatsapp_assistant_channel.md` §5.4
--       ("The bot message record, as WAC-3 builds it"), §5.5 ("The thread")
--       and §5.9. BO-20, the durable queue, has not landed (WS-4 row), so
--       this record is what makes a crash between the 200 and the run
--       visible to the sweep.
-- Depends on: 02_chat_history.sql (chat_session) and
--             130_org_access_control.sql (organization).
--
-- ── The rules the table holds ───────────────────────────────────────────────
--
-- * No column holds message text. The text lives only in the chat thread
--   (`chat_message`), which the member can read and delete (§5.9).
-- * One `wamid`, one row (`whatsapp_bot_messages_wamid_key`). A unique index
--   ignores row level security, so a redelivery of a message that another
--   tenant recorded inserts nothing too. Meta's message ids are global.
-- * `direction`, `state` and `wa_id` carry CHECKs, because the vocabulary is
--   load-bearing: the sweep reads `state`.
--
-- Expand only (R6): one new table with three indexes, and one nullable column
-- with no default on `chat_session`. No rename, no drop, no UPDATE. Old code
-- names no new object, and a NULL `channel` is every web chat.
-- Idempotent per infra/postgres/README.md: a second run changes nothing.
-- Tenant-scoped (R5): `organization_id`, ENABLE + FORCE row level security and
-- a policy with USING and WITH CHECK, in the shape of 235_whatsapp_member_links.sql.
-- The generated phase files carry the same table (`generated/`).
-- Pinned by tests/unit/test_wac_bot_run_r8.py, which finds this file by
-- CONTENT, never by number (R1).
-- ============================================================================

BEGIN;

CREATE TABLE IF NOT EXISTS whatsapp_bot_messages (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id  UUID NOT NULL REFERENCES organization (id) ON DELETE CASCADE,
    member_email     TEXT NOT NULL,
    wa_id            TEXT NOT NULL,
    wamid            TEXT NOT NULL,
    direction        TEXT NOT NULL DEFAULT 'in',
    state            TEXT NOT NULL DEFAULT 'received',
    chat_session_id  TEXT REFERENCES chat_session (id) ON DELETE SET NULL,
    tries            INT  NOT NULL DEFAULT 0,
    error_code       TEXT,
    received_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT whatsapp_bot_messages_wamid_key UNIQUE (wamid),
    CONSTRAINT whatsapp_bot_messages_direction_known CHECK (
        direction IN ('in', 'out')
    ),
    CONSTRAINT whatsapp_bot_messages_state_known CHECK (
        state IN ('received', 'running', 'sending', 'replied', 'refused',
                  'failed', 'expired')
    ),
    CONSTRAINT whatsapp_bot_messages_wa_id_digits CHECK (
        wa_id ~ '^[0-9]{6,20}$'
    ),
    CONSTRAINT whatsapp_bot_messages_tries_bounded CHECK (
        tries BETWEEN 0 AND 100
    )
);

-- The sweep's read: the inbound rows that still wait, in one org.
CREATE INDEX IF NOT EXISTS idx_whatsapp_bot_messages_waiting
    ON whatsapp_bot_messages (organization_id, updated_at)
    WHERE direction = 'in' AND state IN ('received', 'running');

CREATE INDEX IF NOT EXISTS idx_whatsapp_bot_messages_member
    ON whatsapp_bot_messages (organization_id, member_email, received_at DESC);

CREATE INDEX IF NOT EXISTS idx_whatsapp_bot_messages_session
    ON whatsapp_bot_messages (chat_session_id)
    WHERE chat_session_id IS NOT NULL;

DO $rls$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_class
         WHERE oid = 'whatsapp_bot_messages'::regclass
           AND relrowsecurity
           AND relforcerowsecurity
    ) THEN
        EXECUTE 'ALTER TABLE whatsapp_bot_messages ENABLE ROW LEVEL SECURITY';
        EXECUTE 'ALTER TABLE whatsapp_bot_messages FORCE  ROW LEVEL SECURITY';
        EXECUTE 'DROP POLICY IF EXISTS whatsapp_bot_messages_tenant_isolation '
                'ON whatsapp_bot_messages';
        EXECUTE 'CREATE POLICY whatsapp_bot_messages_tenant_isolation '
                'ON whatsapp_bot_messages '
                '    USING      (organization_id = '
                '        current_setting(''app.tenant_id'', true)::uuid) '
                '    WITH CHECK (organization_id = '
                '        current_setting(''app.tenant_id'', true)::uuid)';
    END IF;
END
$rls$;

COMMENT ON TABLE whatsapp_bot_messages IS
    'WAC-3: one row for each message to or from the WhatsApp bot number. '
    'Holds no message text. whatsapp_assistant_channel.md §5.4.';

-- The thread of a channel. NULL is a web chat. Nullable with no default, so
-- the ALTER rewrites no row (R6).
ALTER TABLE chat_session ADD COLUMN IF NOT EXISTS channel TEXT;

COMMENT ON COLUMN chat_session.channel IS
    'WAC-3: the channel that owns this thread. ''whatsapp'' or NULL (the web).';

COMMIT;
