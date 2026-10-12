-- ============================================================================
-- 252_integration_connections_needs_reset.sql — a connection that waits for a
-- start over.
--
-- What: one more value for `integration_connections.status`, `needs_reset`.
--       The Zoho callback of the CRM writes it when the new consent is for a
--       different Zoho org than the one the CRM records came from. The sync
--       does not run for that status. The admin starts the CRM over, or
--       connects the first Zoho org again.
-- Why:  WS-53 CRM-X0, spec project-docs/specs/crm_platform.md §19.11 item 3.
--       Records of two Zoho accounts must never mix.
-- Depends on: 245_integration_connections.sql (the table and the CHECK).
--
-- Expand only (R6): the CHECK gets wider. No rename, no column, no UPDATE.
-- Old code never writes the new value, and only new code reads it.
-- Idempotent per infra/postgres/README.md: the DO block changes the CHECK only
-- when it does not name `needs_reset` yet. A second run changes nothing.
-- Tenant-scoped (R5): no new table. The table keeps its FORCE row level
-- security from 245.
-- Pinned by tests/unit/test_crm_reset_r8.py, which finds this file by CONTENT,
-- never by number (R1).
-- ============================================================================

BEGIN;

DO $needs_reset$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'integration_connections_status_known'
          AND pg_get_constraintdef(oid) LIKE '%needs_reset%'
    ) THEN
        ALTER TABLE integration_connections
            DROP CONSTRAINT IF EXISTS integration_connections_status_known;
        ALTER TABLE integration_connections
            ADD CONSTRAINT integration_connections_status_known CHECK (
                status IN ('connected', 'needs_reconnect', 'needs_reset',
                           'disconnected')
            );
    END IF;
END
$needs_reset$;

COMMIT;
