-- ============================================================================
-- 372_agent_box_health.sql — the heartbeat of an agent box (WS-MCP SW-77,
-- Metorite task 1923, agent_coordination.md §3.11.4 and SW-77, D186).
--
-- What: one new table. `agent_box_health` holds the latest heartbeat of each
--       box connection: the time, the free memory, the load, the sessions,
--       the sign-in state, the account state, whether the CI lane is busy and
--       the lease counts. One row for each box connection: each heartbeat
--       replaces the row (an upsert), so the table never grows past the box
--       connections of the org. `gateway/routes/agent_box_health.py` is the
--       one writer and the one reader.
-- Why:  The runner on a box (SW-78) posts a heartbeat each minute. The
--       Machines tab (SW-81) draws it, and the queue of SW-68 reads it. A row
--       older than 3 minutes reads "offline". The route computes that, so no
--       column holds it.
-- Depends on: 130_org_access_control.sql (organization), 248 (agent_connection).
--
-- Expand only (R6): one new table and one index. No rename, no drop, no
-- change to an existing table. No running code reads or writes the table
-- before this release.
-- Idempotent per infra/postgres/README.md: CREATE TABLE IF NOT EXISTS, CREATE
-- INDEX IF NOT EXISTS, and an RLS block guarded on relforcerowsecurity.
-- Tenant-scoped (R5), in the house shape of 340 (pm_agent_guidance):
-- `organization_id` with ENABLE and FORCE row level security and one policy
-- with USING and WITH CHECK. `REFERENCES` comes before `DEFAULT`, because the
-- tenancy ratchet in tests/unit/test_tenancy_boundary.py matches the two with
-- no comma between. The org of a new row comes from the DEFAULT, the bound
-- tenant. The writer never names it. No GRANT: the roles of 130 hold the
-- default privileges.
--
-- Untrusted values: the box reports each value, so each one is display data.
-- The CHECKs hold the ranges and the two closed lists, and the route checks
-- them first (422). `account_note` holds at most 200 characters, and the route
-- strips each control character first. The account state is best effort.
--
-- `connection_id`: the box connection, with ON DELETE CASCADE. The key is
-- (organization_id, connection_id). `member_email` is the member of the
-- connection, copied from the connection row at each write, so the read of a
-- member needs no join for its filter.
--
-- The number (R1): the builder takes the number at build time, and
-- scripts/premerge.sh checks it again at the merge. On 2026-11-08 origin/main
-- held 366 and the open pull requests held numbers up to 371, so this file
-- took 372. Pinned by tests/unit/test_agent_box_health_r8.py, which finds
-- this file by CONTENT, never by number (R1).
-- ============================================================================

BEGIN;

CREATE TABLE IF NOT EXISTS agent_box_health (
    organization_id      UUID NOT NULL
                         REFERENCES organization (id) ON DELETE CASCADE
                         DEFAULT current_setting('app.tenant_id', true)::uuid,
    connection_id        UUID NOT NULL
                         REFERENCES agent_connection (id) ON DELETE CASCADE,
    -- The member of the connection. From the connection row, never a body.
    member_email         TEXT NOT NULL CHECK (length(member_email) BETWEEN 1 AND 320),
    -- The server time of the latest heartbeat. Never a time that the box sent.
    reported_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    mem_free_mb          INTEGER NULL CHECK (mem_free_mb BETWEEN 0 AND 16777216),
    load_1m              NUMERIC(8, 2) NULL CHECK (load_1m BETWEEN 0 AND 10000),
    sessions             INTEGER NULL CHECK (sessions BETWEEN 0 AND 10000),
    sign_in_state        TEXT NOT NULL DEFAULT 'unknown'
                         CHECK (sign_in_state IN ('signed_in', 'signed_out', 'unknown')),
    -- Best effort: the box reads it from its own client (§3.11.10).
    account_state        TEXT NOT NULL DEFAULT 'unknown'
                         CHECK (account_state IN ('ok', 'rate_limited', 'unknown')),
    account_note         TEXT NULL CHECK (length(account_note) <= 200),
    ci_lane_busy         BOOLEAN NULL,
    -- The lease counts of SW-80, as {kind: count}. The route holds the keys
    -- and the counts to a fixed shape.
    leases               JSONB NOT NULL DEFAULT '{}'::jsonb
                         CHECK (jsonb_typeof(leases) = 'object'),
    PRIMARY KEY (organization_id, connection_id)
);

-- The read of a member: the boxes of one member of the org.
CREATE INDEX IF NOT EXISTS agent_box_health_member
    ON agent_box_health (organization_id, member_email);

-- The cascade of a connection delete reads this column.
CREATE INDEX IF NOT EXISTS agent_box_health_connection
    ON agent_box_health (connection_id);

DO $rls$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_class
         WHERE oid = to_regclass('agent_box_health')
           AND relrowsecurity
           AND relforcerowsecurity
    ) THEN
        ALTER TABLE agent_box_health ENABLE ROW LEVEL SECURITY;
        ALTER TABLE agent_box_health FORCE  ROW LEVEL SECURITY;
        DROP POLICY IF EXISTS agent_box_health_tenant_isolation ON agent_box_health;
        CREATE POLICY agent_box_health_tenant_isolation ON agent_box_health
            USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
            WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);
    END IF;
END
$rls$;

COMMENT ON TABLE agent_box_health IS
    'The latest heartbeat of each agent box connection. One row for each box. '
    'Untrusted display data. WS-MCP SW-77, agent_coordination.md §3.11.4 and SW-77.';

COMMIT;
