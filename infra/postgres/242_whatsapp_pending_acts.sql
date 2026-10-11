-- ============================================================================
-- 242_whatsapp_pending_acts.sql — a write from WhatsApp that waits for the
-- member's Confirm, and the message that a tap answers.
--
-- What: `whatsapp_pending_acts`, one row for each act that a WhatsApp run
--       parks. The row holds the org, the member, the phone (`wa_id`) that
--       got the Confirm and Cancel buttons, the thread, and the link row
--       that was current. It also holds the tool name, the exact arguments,
--       the card's text, the state and the expiry. A row starts `pending`.
--       Confirm makes it `running` under a row lock, then `done`, `failed`
--       or `void`. Cancel makes it `cancelled`. Any other message makes it
--       `void`, and a Confirm after the expiry makes it `expired`.
--       `whatsapp_bot_messages.context_wamid` is the message that an inbound
--       tap or swipe reply answers (Meta's `context.id`).
-- Why:  WS-47 WAC-4, `project-docs/specs/whatsapp_assistant_channel.md` §14
--       (14.3 the record, 14.5 the expiry, the phone and the org).
-- Depends on: 02_chat_history.sql (chat_session),
--             130_org_access_control.sql (organization) and
--             235_whatsapp_member_links.sql (whatsapp_member_links).
--
-- ── The rules the table holds ───────────────────────────────────────────────
--
-- * One live act for each phone in each thread
--   (`uq_whatsapp_pending_acts_one_pending`). The park voids the earlier
--   pending row first, in the same transaction, and the index is the backstop.
-- * An act counts only once it is OFFERED: `offered_at` and `offered_wamid`
--   are set after Meta took the part that carries the Confirm and Cancel
--   buttons, and they are set together. Until then nothing can confirm it.
--   A tap must name `offered_wamid` (`context_wamid`), and a typed Confirm
--   must arrive after `offered_at`.
-- * `running` is the claim. Only the UPDATE that moves a row from `pending`
--   to `running` runs the act, so a second Confirm or a redelivered message
--   writes nothing. A row that a crash leaves `running` never runs again.
-- * `state` and `wa_id` carry CHECKs, because the vocabulary is
--   load-bearing: the Confirm path reads `state`.
-- * `link_id` is the link row that was current when the act parked. Confirm
--   runs the act only while that row is still the phone's current link. A
--   revoked row never becomes current again, and a new link is a new row,
--   so a lost link or an org switch voids the act.
--
-- Expand only (R6): one new table with three indexes, and one nullable column
-- with no default on `whatsapp_bot_messages`. No rename, no drop, no UPDATE.
-- Old code names no new object, and a NULL `context_wamid` is every message
-- that answers nothing.
-- Idempotent per infra/postgres/README.md: a second run changes nothing.
-- Tenant-scoped (R5): `organization_id`, ENABLE + FORCE row level security and
-- a policy with USING and WITH CHECK, in the shape of 238_whatsapp_bot_messages.sql.
-- The generated phase files carry the same table (`generated/`).
-- Pinned by tests/unit/test_wac_writes_r8.py, which finds this file by
-- CONTENT, never by number (R1).
-- ============================================================================

BEGIN;

CREATE TABLE IF NOT EXISTS whatsapp_pending_acts (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id  UUID NOT NULL REFERENCES organization (id) ON DELETE CASCADE,
    member_email     TEXT NOT NULL,
    wa_id            TEXT NOT NULL,
    chat_session_id  TEXT NOT NULL REFERENCES chat_session (id) ON DELETE CASCADE,
    link_id          UUID NOT NULL REFERENCES whatsapp_member_links (id) ON DELETE CASCADE,
    tool_name        TEXT NOT NULL,
    arguments        JSONB NOT NULL,
    card_text        TEXT NOT NULL,
    state            TEXT NOT NULL DEFAULT 'pending',
    close_code       TEXT,
    offered_at       TIMESTAMPTZ,
    offered_wamid    TEXT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at       TIMESTAMPTZ NOT NULL,
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT whatsapp_pending_acts_state_known CHECK (
        state IN ('pending', 'running', 'done', 'failed', 'cancelled', 'void',
                  'expired')
    ),
    CONSTRAINT whatsapp_pending_acts_wa_id_digits CHECK (
        wa_id ~ '^[0-9]{6,20}$'
    ),
    CONSTRAINT whatsapp_pending_acts_tool_name_shape CHECK (
        tool_name ~ '^[a-z][a-z0-9_]{0,63}$'
    ),
    CONSTRAINT whatsapp_pending_acts_arguments_object CHECK (
        jsonb_typeof(arguments) = 'object'
    ),
    CONSTRAINT whatsapp_pending_acts_offered_together CHECK (
        (offered_at IS NULL) = (offered_wamid IS NULL)
    ),
    CONSTRAINT whatsapp_pending_acts_expiry_after_create CHECK (
        expires_at > created_at
    )
);

-- A database that ran an earlier draft of this file holds the table without
-- the offer columns, and CREATE TABLE IF NOT EXISTS skips it. These guards
-- bring that table level (review, 2026-10-11). Each one is a no-op on a
-- fresh install. Nullable with no default, so no row is rewritten (R6).
ALTER TABLE whatsapp_pending_acts ADD COLUMN IF NOT EXISTS offered_at    TIMESTAMPTZ;
ALTER TABLE whatsapp_pending_acts ADD COLUMN IF NOT EXISTS offered_wamid TEXT;
DO $offer$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conname = 'whatsapp_pending_acts_offered_together'
           AND conrelid = 'whatsapp_pending_acts'::regclass
    ) THEN
        ALTER TABLE whatsapp_pending_acts
            ADD CONSTRAINT whatsapp_pending_acts_offered_together
            CHECK ((offered_at IS NULL) = (offered_wamid IS NULL));
    END IF;
END
$offer$;

CREATE UNIQUE INDEX IF NOT EXISTS uq_whatsapp_pending_acts_one_pending
    ON whatsapp_pending_acts (organization_id, wa_id, chat_session_id)
    WHERE state = 'pending';

CREATE INDEX IF NOT EXISTS idx_whatsapp_pending_acts_member
    ON whatsapp_pending_acts (organization_id, member_email, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_whatsapp_pending_acts_link
    ON whatsapp_pending_acts (link_id);

DO $rls$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_class
         WHERE oid = 'whatsapp_pending_acts'::regclass
           AND relrowsecurity
           AND relforcerowsecurity
    ) THEN
        EXECUTE 'ALTER TABLE whatsapp_pending_acts ENABLE ROW LEVEL SECURITY';
        EXECUTE 'ALTER TABLE whatsapp_pending_acts FORCE  ROW LEVEL SECURITY';
        EXECUTE 'DROP POLICY IF EXISTS whatsapp_pending_acts_tenant_isolation '
                'ON whatsapp_pending_acts';
        EXECUTE 'CREATE POLICY whatsapp_pending_acts_tenant_isolation '
                'ON whatsapp_pending_acts '
                '    USING      (organization_id = '
                '        current_setting(''app.tenant_id'', true)::uuid) '
                '    WITH CHECK (organization_id = '
                '        current_setting(''app.tenant_id'', true)::uuid)';
    END IF;
END
$rls$;

COMMENT ON TABLE whatsapp_pending_acts IS
    'WAC-4: a write from WhatsApp that waits for the member''s Confirm. '
    'whatsapp_assistant_channel.md §14.';

-- The message that an inbound tap or swipe reply answers. NULL answers
-- nothing. Nullable with no default, so the ALTER rewrites no row (R6).
ALTER TABLE whatsapp_bot_messages ADD COLUMN IF NOT EXISTS context_wamid TEXT;

COMMENT ON COLUMN whatsapp_bot_messages.context_wamid IS
    'WAC-4: the message that this inbound tap or reply answers (Meta context.id).';

COMMIT;
