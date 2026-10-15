-- ============================================================================
-- 266_reach_outbox.sql — the Reach outbox and its signal log.
--
-- What: two new tables. `reach_outbox` holds one row for each message that
--       Reach planned for a member: a morning brief or an interrupt. Its
--       `dedupe_key` makes one plan send once. `body` is the message text, so
--       it is tenant text under RLS. `reach_signal_log` holds one row for each
--       card and fingerprint that Reach looked at, with its outcome, so a card
--       never interrupts twice. Only gateway/reach/outbox.py writes them.
-- Why:  WS-59 RE-1a, spec project-docs/specs/proactive_reach.md §6 and §7.
-- Depends on: 130_org_access_control.sql (organization).
--
-- Expand only (R6): two new tables. No rename, no drop, and no change to an
-- existing table. Old code names no new object.
-- Idempotent per infra/postgres/README.md: CREATE TABLE IF NOT EXISTS, CREATE
-- INDEX IF NOT EXISTS and an RLS block guarded on relforcerowsecurity. A
-- second run changes nothing.
-- Tenant-scoped (R5): `organization_id` with ENABLE and FORCE row level
-- security and one policy with USING and WITH CHECK, in the shape of
-- 262_my_day_judgments.sql. `REFERENCES` comes before `DEFAULT`, because the
-- tenancy ratchet in tests/unit/test_tenancy_boundary.py matches the two with
-- no comma between. The foreign key is inline, never `ADD CONSTRAINT
-- <table>_org_fk`, because generated phase 3 adds that name.
-- The number is taken at build time (R1). On 2026-10-14 main held 264, and
-- the open branch ws55-ea6d held 265. Check it again at merge.
-- Pinned by tests/unit/test_reach_r8.py, which finds this file by CONTENT,
-- never by number (R1).
-- ============================================================================

BEGIN;

CREATE TABLE IF NOT EXISTS reach_outbox (
    id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id      UUID NOT NULL
                         REFERENCES organization (id) ON DELETE CASCADE
                         DEFAULT current_setting('app.tenant_id', true)::uuid,
    member_email         TEXT NOT NULL,
    channel              TEXT NOT NULL DEFAULT 'whatsapp',
    kind                 TEXT NOT NULL,
    signal_ids           TEXT[] NOT NULL DEFAULT '{}',
    dedupe_key           TEXT NOT NULL,
    body                 TEXT NOT NULL,
    state                TEXT NOT NULL DEFAULT 'planned',
    regime               TEXT NULL,
    reason               TEXT NULL,
    provider_message_id  TEXT NULL,
    local_date           DATE NULL,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    sent_at              TIMESTAMPTZ NULL,
    CONSTRAINT reach_outbox_dedupe_key UNIQUE (organization_id, dedupe_key),
    CONSTRAINT reach_outbox_channel_known CHECK (channel IN ('whatsapp')),
    CONSTRAINT reach_outbox_kind_known CHECK (kind IN ('brief', 'interrupt')),
    CONSTRAINT reach_outbox_state_known CHECK (
        state IN ('planned', 'sent', 'skipped', 'failed')
    ),
    CONSTRAINT reach_outbox_regime_known CHECK (
        regime IS NULL OR regime IN ('free_text', 'template')
    )
);

-- The budget read: a member's interrupts of one day.
CREATE INDEX IF NOT EXISTS reach_outbox_member_created_idx
    ON reach_outbox (organization_id, member_email, created_at);

CREATE TABLE IF NOT EXISTS reach_signal_log (
    organization_id  UUID NOT NULL
                     REFERENCES organization (id) ON DELETE CASCADE
                     DEFAULT current_setting('app.tenant_id', true)::uuid,
    member_email     TEXT NOT NULL,
    signal_id        TEXT NOT NULL,
    fingerprint      TEXT NOT NULL,
    outcome          TEXT NOT NULL,
    outbox_id        UUID NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (organization_id, member_email, signal_id, fingerprint),
    CONSTRAINT reach_signal_log_outcome_known CHECK (
        outcome IN ('baseline', 'sent', 'deferred', 'quiet', 'capped', 'skipped')
    )
);

DO $rls$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_class
         WHERE oid = to_regclass('reach_outbox')
           AND relrowsecurity
           AND relforcerowsecurity
    ) THEN
        ALTER TABLE reach_outbox ENABLE ROW LEVEL SECURITY;
        ALTER TABLE reach_outbox FORCE  ROW LEVEL SECURITY;
        DROP POLICY IF EXISTS reach_outbox_tenant_isolation ON reach_outbox;
        CREATE POLICY reach_outbox_tenant_isolation ON reach_outbox
            USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
            WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_class
         WHERE oid = to_regclass('reach_signal_log')
           AND relrowsecurity
           AND relforcerowsecurity
    ) THEN
        ALTER TABLE reach_signal_log ENABLE ROW LEVEL SECURITY;
        ALTER TABLE reach_signal_log FORCE  ROW LEVEL SECURITY;
        DROP POLICY IF EXISTS reach_signal_log_tenant_isolation ON reach_signal_log;
        CREATE POLICY reach_signal_log_tenant_isolation ON reach_signal_log
            USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
            WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);
    END IF;
END
$rls$;

COMMENT ON TABLE reach_outbox IS
    'WS-59 RE-1a: one row for each message Reach planned for a member, a brief '
    'or an interrupt. dedupe_key sends one plan once. body is tenant text. Only '
    'gateway/reach/outbox.py writes it. proactive_reach.md §7.';

COMMENT ON TABLE reach_signal_log IS
    'WS-59 RE-1a: one row for each card and fingerprint Reach looked at, with '
    'its outcome, so a card interrupts once. Only gateway/reach/outbox.py '
    'writes it. proactive_reach.md §6.';

COMMIT;
