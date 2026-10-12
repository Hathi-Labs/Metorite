-- ============================================================================
-- 252_crm_start_over.sql — a connection that waits for a start over, and the
-- time of the last start over.
--
-- What: two expand-only changes.
--         * `integration_connections.status` takes one more value,
--           `needs_reset`. The Zoho callback of the CRM writes it when the new
--           consent is for a different Zoho org than the one the CRM records
--           came from. The sync does not run for that status. The admin starts
--           the CRM over, or connects the first Zoho org again.
--         * `crm_settings.reset_at`, the time of the last start over
--           (`POST /crm/reset`). The sync reads only the runs after it, so a
--           new import does not carry on the counts or the credits of the old
--           account. NULL means the CRM never started over.
-- Why:  WS-53 CRM-X0, spec project-docs/specs/crm_platform.md §19.11 items 3
--       and 6. Records of two Zoho accounts must never mix.
-- Depends on: 243_crm_settings.sql (crm_settings),
--             245_integration_connections.sql (the table and the CHECK).
--
-- Expand only (R6): the CHECK gets wider, and one nullable column with no
-- default. No rename, no UPDATE. Old code never writes the new value and
-- never names the new column.
-- Idempotent per infra/postgres/README.md: the DO block changes the CHECK only
-- when it does not name `needs_reset` yet, and ADD COLUMN IF NOT EXISTS. A
-- second run changes nothing.
-- Tenant-scoped (R5): no new table. Both tables keep their FORCE row level
-- security.
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

ALTER TABLE crm_settings ADD COLUMN IF NOT EXISTS reset_at TIMESTAMPTZ NULL;

COMMIT;
