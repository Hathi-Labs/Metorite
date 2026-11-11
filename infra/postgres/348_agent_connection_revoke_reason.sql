-- ============================================================================
-- 348_agent_connection_revoke_reason.sql - the revoke reason, the credential
-- and the address columns of an outside agent's connection (WS-55 EA-19a,
-- D-EA-5)
-- ============================================================================
-- Spec: project-docs/specs/external_agent_access.md §10.9 question 6 and the
-- EA-19a row of §15 · Metorite task 1838 · R5 · R6 · R8.
-- R1: origin/main held 345 as its highest file on 2026-11-06. Branch
-- kg4-structural-edges holds 346 and branch stu-j1-gate holds 347. So this
-- file is 348. Check the number again at merge.
--
-- What: four columns on `agent_connection`.
--   credential       TEXT NOT NULL DEFAULT 'oauth', with the named CHECK
--                    `agent_connection_credential` (oauth, box). A box
--                    connection (EA-19b) has no refresh token. Every row from
--                    before this file is an OAuth connection.
--   revoked_reason   TEXT, nullable, with NO CHECK. Why the connection was
--                    revoked. NULL on each row from before this file. A CHECK
--                    would let one wrong string roll back a revoke, and a
--                    reuse revoke must never fail. So the set lives in
--                    `acb_auth.agent_tokens.REVOKE_REASONS`, and the store
--                    refuses an unknown reason before it runs any SQL.
--   last_seen_addr   TEXT, nullable. The last client address of a box token
--                    (EA-19b). Nothing writes it in EA-19a.
--   addr_changed_at  TIMESTAMPTZ, nullable. When that address last changed,
--                    the new-address mark (EA-19b). Nothing writes it in
--                    EA-19a.
--
-- Who writes them: only `acb_auth.agent_tokens`. EA-19a writes
--   `revoked_reason` in each revoke of a connection. `credential` keeps its
--   default until EA-19b.
--
-- Expand only (R6): four new columns, no rename, no backfill. The old code
--   names each column by name, so it never reads the new ones, and its
--   INSERT gets the default. Its revoke leaves `revoked_reason` NULL. A
--   constant default changes only the catalog on PG 11 and later, so the
--   ALTER does not rewrite the table. The CHECK reads each row once, which
--   is cheap on this small table. Idempotent: ADD COLUMN IF NOT EXISTS, and
--   the CHECK only while `pg_constraint` lacks its name.
--
-- Tenancy (R5): `agent_connection` is tenant-scoped, with FORCE RLS and the
--   policy `agent_connection_tenant_isolation` (248). The columns change
--   neither. So this file adds no table, no policy and no
--   `gen_tenant_migration.EXEMPT` row.
--
-- Never wait for the lock: `ALTER TABLE` takes ACCESS EXCLUSIVE on
--   `agent_connection`, and each agent call reads that table. So the file
--   sets `lock_timeout` itself, as 344 does. On a lock timeout the runner
--   tries the whole file again, and the file is idempotent.
--
-- Depends on: 248_agent_access_tokens.sql (the table).
-- Pinned by: tests/unit/test_agent_revoke_reason.py (R8).
-- ============================================================================

SET lock_timeout = '5s';

-- One statement, so the table takes its ACCESS EXCLUSIVE lock once.
ALTER TABLE agent_connection
    ADD COLUMN IF NOT EXISTS credential TEXT NOT NULL DEFAULT 'oauth',
    ADD COLUMN IF NOT EXISTS revoked_reason TEXT,
    ADD COLUMN IF NOT EXISTS last_seen_addr TEXT,
    ADD COLUMN IF NOT EXISTS addr_changed_at TIMESTAMPTZ;

DO $checks$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conname = 'agent_connection_credential'
           AND conrelid = 'agent_connection'::regclass
    ) THEN
        ALTER TABLE agent_connection ADD CONSTRAINT agent_connection_credential
            CHECK (credential IN ('oauth', 'box'));
    END IF;
END
$checks$;

COMMENT ON COLUMN agent_connection.credential IS
    'How the agent holds the connection (WS-55 EA-19a, D-EA-5): oauth, or box for a per-box token with no refresh token.';

COMMENT ON COLUMN agent_connection.revoked_reason IS
    'Why the connection was revoked (EA-19a). One of acb_auth.agent_tokens.REVOKE_REASONS. No CHECK, so a wrong string never rolls back a revoke. NULL on a row from before EA-19a.';

COMMENT ON COLUMN agent_connection.last_seen_addr IS
    'The last client address of a box token (EA-19b). NULL until EA-19b writes it.';

COMMENT ON COLUMN agent_connection.addr_changed_at IS
    'When the client address of a box token last changed, the new-address mark (EA-19b). NULL until EA-19b writes it.';
