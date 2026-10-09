-- 237_chat_pending_ask.sql — one row for each question that a chat run asks a
-- member, so the question outlives the process that asked it.
--
-- What: `chat_pending_ask`. A run that parks on a card (ask_user,
--       ask_questions, request_confirmation, a blocking generative UI) writes
--       one row. The row names the request id, the thread, the run and the
--       member who started the run (`actor_email`). It holds the kind of the
--       card, its question and the card's own event value (`payload`).
--       States:
--         open     — the run waits on the card in this process.
--         parked   — the run waited too long, saved its reply and ended. An
--                    answer starts a NEW run (WS-51 S2 item 3).
--         answered — the answer arrived. `answer` holds it.
--         closed   — the card closed with no answer (a timeout, a Stop).
-- Why:  spec `project-docs/specs/chat_run_continuity.md` §4 S2 · board WS-51.
--       Before this, a pending question lived only in an in-process Future
--       (`executor._pending_user_input`), so a restart lost it, and nothing
--       outside an open chat could say "needs your answer".
-- Depends on: 130_org_access_control.sql (`organization`).
--
-- ── No foreign key to chat_session ─────────────────────────────────────────
--
-- A run can start before the browser saves its chat row (the "no row yet"
-- case of `GET /chat/active-sessions`). A foreign key would then refuse the
-- row of the very first question. Every reader joins `chat_session` under
-- `SESSION_VISIBLE_SQL`, so a row of a deleted chat is never listed, and
-- `expires_at` bounds how long it stays.
--
-- ── The payload is data ────────────────────────────────────────────────────
--
-- `payload` is the card event's value, as the run emitted it. The client
-- draws it as the same card, and no reader treats it as an instruction. The
-- late answer reaches the model through `chat_recovery.compose_card_answer`,
-- which quotes the question.
--
-- ── Tenancy ────────────────────────────────────────────────────────────────
--
-- The guarded block below is the block of 228_maf_agent_session.sql: ENABLE
-- and FORCE ROW LEVEL SECURITY, then the policy
-- `chat_pending_ask_tenant_isolation` with USING and WITH CHECK.
-- `scripts/gen_tenant_migration.py` adds the table to `generated/`, and
-- `tests/unit/test_tenant_coverage.py` checks it (R5a). Every read and write
-- goes through `acb_graph.tenant_session(org)`. The one module that does so is
-- `apps/services/orchestrator/orchestrator/pending_ask.py`.
--
-- R6 EXPAND only: one new table, five CHECKs and two partial indexes. Nothing
-- that exists changes, so old code meets this schema without noticing it.
--
-- Idempotent per infra/postgres/README.md. Pinned by
-- tests/unit/test_pending_ask_store.py (R8, FORCE RLS, as the NOBYPASSRLS app
-- role), which finds this file by CONTENT, never by number (R1).

BEGIN;

CREATE TABLE IF NOT EXISTS chat_pending_ask (
    organization_id UUID        NOT NULL REFERENCES organization (id) ON DELETE CASCADE,
    request_id      TEXT        NOT NULL,
    thread_id       TEXT        NOT NULL,
    run_id          TEXT,
    actor_email     TEXT        NOT NULL DEFAULT '',
    agent_name      TEXT,
    kind            TEXT        NOT NULL,
    question        TEXT        NOT NULL DEFAULT '',
    payload         JSONB       NOT NULL DEFAULT '{}'::jsonb,
    state           TEXT        NOT NULL DEFAULT 'open',
    answer          TEXT,
    asked_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    parked_at       TIMESTAMPTZ,
    answered_at     TIMESTAMPTZ,
    expires_at      TIMESTAMPTZ NOT NULL DEFAULT (now() + interval '7 days'),
    PRIMARY KEY (organization_id, request_id),
    CONSTRAINT chat_pending_ask_request_id_shape
        CHECK (request_id ~ '^[0-9a-f]{32}$'),
    CONSTRAINT chat_pending_ask_kind_known
        CHECK (kind IN ('ask_user', 'questions', 'confirmation', 'generative_ui')),
    CONSTRAINT chat_pending_ask_state_known
        CHECK (state IN ('open', 'parked', 'answered', 'closed')),
    CONSTRAINT chat_pending_ask_payload_is_object
        CHECK (jsonb_typeof(payload) = 'object'),
    CONSTRAINT chat_pending_ask_answered_has_time
        CHECK (state <> 'answered' OR answered_at IS NOT NULL)
);

-- The two reads: the open asks of one thread (the chat re-shows its card),
-- and the open asks of one member (the "needs you" badge).
CREATE INDEX IF NOT EXISTS idx_chat_pending_ask_thread_waiting
    ON chat_pending_ask (organization_id, thread_id)
    WHERE state IN ('open', 'parked');
CREATE INDEX IF NOT EXISTS idx_chat_pending_ask_actor_waiting
    ON chat_pending_ask (organization_id, actor_email)
    WHERE state IN ('open', 'parked');

DO $rls$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_class
         WHERE oid = 'chat_pending_ask'::regclass
           AND relrowsecurity
           AND relforcerowsecurity
    ) THEN
        EXECUTE 'ALTER TABLE chat_pending_ask ENABLE ROW LEVEL SECURITY';
        EXECUTE 'ALTER TABLE chat_pending_ask FORCE  ROW LEVEL SECURITY';
        EXECUTE 'DROP POLICY IF EXISTS chat_pending_ask_tenant_isolation '
                'ON chat_pending_ask';
        EXECUTE 'CREATE POLICY chat_pending_ask_tenant_isolation '
                'ON chat_pending_ask '
                '    USING      (organization_id = '
                '        current_setting(''app.tenant_id'', true)::uuid) '
                '    WITH CHECK (organization_id = '
                '        current_setting(''app.tenant_id'', true)::uuid)';
    END IF;
END
$rls$;

COMMENT ON TABLE chat_pending_ask IS
    'One row per question a chat run asks a member. Outlives the process that '
    'asked. chat_run_continuity.md §4 S2 · WS-51.';

COMMIT;
