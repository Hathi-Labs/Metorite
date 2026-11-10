-- ============================================================================
-- 271_reach_requests.sql — "Tell me on WhatsApp": the requests of Reach.
--
-- What: one new table, and two wider CHECKs on the tables of 266.
--       `reach_requests` holds one row for each request that an app, a rule,
--       a workflow or the assistant recorded for ONE member: "tell me this on
--       WhatsApp". `title` and `body` are tenant text under RLS. The Reach
--       loop turns each open request into a card of kind `notify_me`, with
--       the signal `request:<id>`. A request is delivered when
--       `reach_signal_log` holds a row for that signal, so this table has no
--       state column. Only gateway/reach/requests.py writes it.
--       `reach_signal_log.outcome` gains `expired`: a request that expired
--       before it could go. `reach_outbox.kind` gains `request`: a message
--       that holds only the member's own requests, which the daily cap does
--       not count (proactive_reach.md §11.3).
-- Why:  WS-59 RE-5, spec project-docs/specs/proactive_reach.md §11.
-- Depends on: 130_org_access_control.sql (organization), 266_reach_outbox.sql.
--
-- Expand only (R6): a new table, and two CHECKs that allow MORE values. Old
-- code writes no new value, so every row of old code passes both CHECKs. No
-- rename and no drop of a column.
-- Idempotent per infra/postgres/README.md: CREATE TABLE IF NOT EXISTS, CREATE
-- INDEX IF NOT EXISTS, an RLS block guarded on relforcerowsecurity, and each
-- CHECK replaced only when its definition does not yet name the new value. A
-- second run changes nothing.
-- Tenant-scoped (R5): `organization_id` with ENABLE and FORCE row level
-- security and one policy with USING and WITH CHECK, in the shape of
-- 266_reach_outbox.sql. `REFERENCES` comes before `DEFAULT`, because the
-- tenancy ratchet in tests/unit/test_tenancy_boundary.py matches the two with
-- no comma between. The foreign key is inline, never `ADD CONSTRAINT
-- <table>_org_fk`, because generated phase 3 adds that name.
-- The number is taken at build time (R1). On 2026-10-15 main held 268, and no
-- open branch held 271. It was 269, then 270: open PR #1053 took 269, and
-- 270_knowledge_feature.sql reached main first. Check it again at merge.
-- Pinned by tests/unit/test_reach_requests.py, which finds this file by
-- CONTENT, never by number (R1).
-- ============================================================================

BEGIN;

CREATE TABLE IF NOT EXISTS reach_requests (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id  UUID NOT NULL
                     REFERENCES organization (id) ON DELETE CASCADE
                     DEFAULT current_setting('app.tenant_id', true)::uuid,
    member_email     TEXT NOT NULL,
    source           TEXT NOT NULL,
    title            TEXT NOT NULL,
    body             TEXT NULL,
    href             TEXT NULL,
    entity_type      TEXT NULL,
    entity_id        TEXT NULL,
    urgency          TEXT NOT NULL DEFAULT 'normal',
    requested_by     TEXT NOT NULL,
    dedupe_key       TEXT NOT NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at       TIMESTAMPTZ NOT NULL DEFAULT now() + interval '48 hours',
    CONSTRAINT reach_requests_dedupe_key UNIQUE (organization_id, dedupe_key),
    CONSTRAINT reach_requests_urgency_known CHECK (urgency IN ('normal', 'urgent')),
    CONSTRAINT reach_requests_title_length CHECK (
        char_length(title) BETWEEN 1 AND 300
    ),
    CONSTRAINT reach_requests_body_length CHECK (
        body IS NULL OR char_length(body) <= 1000
    ),
    CONSTRAINT reach_requests_href_relative CHECK (
        href IS NULL OR (href LIKE '/%' AND href NOT LIKE '//%')
    )
);

-- The read of the loop: one member's requests, oldest first.
CREATE INDEX IF NOT EXISTS reach_requests_member_created_idx
    ON reach_requests (organization_id, member_email, created_at);

DO $rls$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_class
         WHERE oid = to_regclass('reach_requests')
           AND relrowsecurity
           AND relforcerowsecurity
    ) THEN
        ALTER TABLE reach_requests ENABLE ROW LEVEL SECURITY;
        ALTER TABLE reach_requests FORCE  ROW LEVEL SECURITY;
        DROP POLICY IF EXISTS reach_requests_tenant_isolation ON reach_requests;
        CREATE POLICY reach_requests_tenant_isolation ON reach_requests
            USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
            WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);
    END IF;
END
$rls$;

-- The two wider CHECKs. Each one is replaced only while its definition does
-- not name the new value, so a second run does nothing.
DO $checks$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = to_regclass('reach_signal_log')
           AND conname = 'reach_signal_log_outcome_known'
           AND pg_get_constraintdef(oid) LIKE '%expired%'
    ) THEN
        ALTER TABLE reach_signal_log
            DROP CONSTRAINT IF EXISTS reach_signal_log_outcome_known;
        ALTER TABLE reach_signal_log
            ADD CONSTRAINT reach_signal_log_outcome_known CHECK (
                outcome IN ('baseline', 'sent', 'deferred', 'quiet', 'capped',
                            'skipped', 'expired')
            );
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = to_regclass('reach_outbox')
           AND conname = 'reach_outbox_kind_known'
           AND pg_get_constraintdef(oid) LIKE '%request%'
    ) THEN
        ALTER TABLE reach_outbox DROP CONSTRAINT IF EXISTS reach_outbox_kind_known;
        ALTER TABLE reach_outbox
            ADD CONSTRAINT reach_outbox_kind_known CHECK (
                kind IN ('brief', 'interrupt', 'request')
            );
    END IF;
END
$checks$;

COMMENT ON TABLE reach_requests IS
    'WS-59 RE-5: one row for each "tell me on WhatsApp" request for one member. '
    'title and body are tenant text. Delivered when reach_signal_log holds the '
    'signal request:<id>. Only gateway/reach/requests.py writes it. '
    'proactive_reach.md §11.';

COMMIT;
