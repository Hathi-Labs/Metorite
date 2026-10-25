-- ============================================================================
-- 306_push_subscriptions.sql — one web push subscription of one device.
--
-- What: one new table, `push_subscriptions`. One row is one browser or one
--       installed web app of one member, as the W3C Push API gives it:
--       the push service `endpoint`, the two keys `p256dh` and `auth`, and
--       `expiration_time`. The sender (gateway/push/sender.py) reads the
--       rows of a member and sends to each one. A 404 or a 410 from the push
--       service deletes the row. Only gateway/push/store.py writes it.
-- Why:  WS-66 PN-1, the transport of chat_run_continuity.md §S7 (item 6).
--       Spec calendar_events_reminders.md §5.9 and §10.2.
-- Depends on: 130_org_access_control.sql (organization).
--
-- Expand only (R6): one new table. No rename, no drop, and no change to an
-- existing table. Old code names no new object.
-- Idempotent per infra/postgres/README.md: CREATE TABLE IF NOT EXISTS and an
-- RLS block guarded on relforcerowsecurity.
-- Tenant-scoped (R5): `organization_id` with ENABLE and FORCE row level
-- security and one policy with USING and WITH CHECK, in the shape of
-- 290_email_scheduled_sends.sql. `REFERENCES` comes before `DEFAULT`, because
-- the tenancy ratchet in tests/unit/test_tenancy_boundary.py matches the two
-- with no comma between.
-- ⚠️ The unique key is (organization_id, member_email, endpoint), never
-- `endpoint` alone. A browser has one endpoint, and two accounts of two orgs
-- can hold it. A key on `endpoint` alone makes the insert of org B fail on a
-- row that org B cannot see.
-- The number is taken at build time (R1). On 2026-10-25 origin/main held
-- 300 to 303, open PR #1317 held 304 (304_pm_tasks_fields_at.sql), and RM-1
-- held 305, so this takes 306. It took 304 first, and the review of PN-1
-- moved it. Check it again at merge.
-- Pinned by tests/unit/test_push_subscriptions.py, which finds this file by
-- CONTENT, never by number (R1).
-- ============================================================================

BEGIN;

CREATE TABLE IF NOT EXISTS push_subscriptions (
    organization_id  UUID NOT NULL
                     REFERENCES organization (id) ON DELETE CASCADE
                     DEFAULT current_setting('app.tenant_id', true)::uuid,
    id               UUID NOT NULL DEFAULT gen_random_uuid(),
    -- The member, from the session only, in lower case.
    member_email     TEXT NOT NULL,
    -- The push service URL of the device (PushSubscription.endpoint).
    endpoint         TEXT NOT NULL,
    -- The device's P-256 public key and its auth secret, base64url.
    p256dh           TEXT NOT NULL,
    auth             TEXT NOT NULL,
    -- PushSubscription.expirationTime. NULL means no end that we know.
    expiration_time  TIMESTAMPTZ NULL,
    -- The browser that subscribed, as a label for the member. Not identity.
    user_agent       TEXT NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_success_at  TIMESTAMPTZ NULL,
    -- Sends that failed in a row, for a reason other than 404 or 410. A
    -- success sets it to 0.
    failure_count    INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (id),
    CONSTRAINT push_subscriptions_member_endpoint
        UNIQUE (organization_id, member_email, endpoint),
    CONSTRAINT push_subscriptions_member_lower CHECK (
        member_email = lower(member_email) AND member_email <> ''),
    CONSTRAINT push_subscriptions_endpoint_https CHECK (
        endpoint LIKE 'https://%' AND char_length(endpoint) <= 2048),
    CONSTRAINT push_subscriptions_keys_size CHECK (
        char_length(p256dh) BETWEEN 1 AND 200
        AND char_length(auth) BETWEEN 1 AND 100),
    CONSTRAINT push_subscriptions_user_agent_size CHECK (
        user_agent IS NULL OR char_length(user_agent) <= 300),
    CONSTRAINT push_subscriptions_failures CHECK (failure_count >= 0)
);

DO $rls$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_class
         WHERE oid = to_regclass('push_subscriptions')
           AND relrowsecurity
           AND relforcerowsecurity
    ) THEN
        ALTER TABLE push_subscriptions ENABLE ROW LEVEL SECURITY;
        ALTER TABLE push_subscriptions FORCE  ROW LEVEL SECURITY;
        DROP POLICY IF EXISTS push_subscriptions_tenant_isolation ON push_subscriptions;
        CREATE POLICY push_subscriptions_tenant_isolation ON push_subscriptions
            USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
            WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);
    END IF;
END
$rls$;

COMMENT ON TABLE push_subscriptions IS
    'Web push: one subscription of one device of one member. Only '
    'gateway/push/store.py writes it. calendar_events_reminders.md §5.9.';

COMMIT;
