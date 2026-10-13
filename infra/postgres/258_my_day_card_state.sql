-- ============================================================================
-- 258_my_day_card_state.sql — the state of a My Day card, for one member.
--
-- What: one new table, `my_day_card_state`. A row exists only for a signal
--       that the member saw or touched: seen, snoozed, dismissed, "Not
--       useful", or an act that ran from the card. The signals themselves are
--       never stored, because the providers compute them again on each read.
--       Only gateway/routes/shell/state.py writes it, through
--       `POST /shell/feed/state`. `GET /shell/feed` reads it.
-- Why:  WS-58 MD-2a, spec project-docs/specs/my_day_feed.md §6.8 and §13.1.
--       `visits` counts the visits of ONE local day, and `visits_day` holds
--       that day, so the rules of §6.3 run at read time with no sweep.
-- Depends on: 130_org_access_control.sql (organization).
--
-- Expand only (R6): one new table. No rename, no drop, and no change to an
-- existing table. Old code names no new object.
-- Idempotent per infra/postgres/README.md: CREATE TABLE IF NOT EXISTS and an
-- RLS block guarded on relforcerowsecurity. A second run changes nothing.
-- Tenant-scoped (R5): `organization_id` with ENABLE and FORCE row level
-- security and one policy with USING and WITH CHECK, in the shape of
-- 250_crm_views.sql. `REFERENCES` comes before `DEFAULT`, because the tenancy
-- ratchet in tests/unit/test_tenancy_boundary.py matches the two with no
-- comma between. The foreign key is inline, never
-- `ADD CONSTRAINT <table>_org_fk`, because generated phase 3 adds that name.
-- The member comes from the session, never from a request body (R5e).
-- The number is taken at build time (R1). On 2026-10-13 main held 257, and an
-- open branch held 259. Check it again at merge.
-- Pinned by tests/unit/test_my_day_state_r8.py, which finds this file by
-- CONTENT, never by number (R1).
-- ============================================================================

BEGIN;

CREATE TABLE IF NOT EXISTS my_day_card_state (
    organization_id   UUID NOT NULL
                      REFERENCES organization (id) ON DELETE CASCADE
                      DEFAULT current_setting('app.tenant_id', true)::uuid,
    member_email      TEXT NOT NULL,
    signal_id         TEXT NOT NULL,
    kind              TEXT NOT NULL,
    state             TEXT NOT NULL,
    dismiss_reason    TEXT NULL,
    fingerprint       TEXT NULL,
    visits            INT NOT NULL DEFAULT 0,
    visits_day        DATE NULL,
    first_seen_at     TIMESTAMPTZ NULL,
    last_seen_at      TIMESTAMPTZ NULL,
    acted_at          TIMESTAMPTZ NULL,
    not_useful_at     TIMESTAMPTZ NULL,
    snoozed_until     TIMESTAMPTZ NULL,
    snooze_on_change  BOOLEAN NOT NULL DEFAULT false,
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (organization_id, member_email, signal_id),
    CONSTRAINT my_day_card_state_state_known CHECK (
        state IN ('seen', 'later', 'acted', 'snoozed', 'dismissed')
    ),
    CONSTRAINT my_day_card_state_reason_known CHECK (
        dismiss_reason IN ('dismiss', 'not_useful')
    )
);

DO $rls$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_class
         WHERE oid = to_regclass('my_day_card_state')
           AND relrowsecurity
           AND relforcerowsecurity
    ) THEN
        ALTER TABLE my_day_card_state ENABLE ROW LEVEL SECURITY;
        ALTER TABLE my_day_card_state FORCE  ROW LEVEL SECURITY;
        DROP POLICY IF EXISTS my_day_card_state_tenant_isolation ON my_day_card_state;
        CREATE POLICY my_day_card_state_tenant_isolation ON my_day_card_state
            USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
            WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);
    END IF;
END
$rls$;

COMMENT ON TABLE my_day_card_state IS
    'WS-58 MD-2a: the state of one My Day card for one member: seen, later, '
    'acted, snoozed or dismissed. Only routes/shell/state.py writes it. '
    'my_day_feed.md §6.8, §13.1.';

COMMIT;
