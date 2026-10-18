-- ============================================================================
-- 285_whatsapp_act_home.sql — where a WhatsApp Confirm finds the parked tool.
--
-- What: `whatsapp_pending_acts.act_home`, a nullable TEXT. NULL is the act of
--       WAC-4: a Projects tool that `whatsapp_acts.ALLOWED_ACTS` names by
--       its module. `agent:<name>` is an agent's own tool, which the Confirm
--       loads through the one agent loader. `platform` is a platform tool
--       whose untrusted-answer card parked. A CHECK keeps those three shapes.
-- Why:  WS-17 incident 2026-10-18, `project-docs/specs/email_app_master_plan.md`
--       12.3.13.8, and the owner rule of 2026-10-18 that WhatsApp has the
--       full function of the chat. Every card that a chat run asks now parks
--       for the member's Confirm on WhatsApp, not only the Projects acts.
-- Depends on: 242_whatsapp_pending_acts.sql.
--
-- Expand only (R6): one nullable column with no default, and one CHECK that
-- every existing row passes (each one is NULL). No rename, no drop, no
-- UPDATE. Old code names no new column, and it never reads a row with a
-- home: its Confirm finds no tool for such a row, and the act fails closed.
-- Idempotent per infra/postgres/README.md: a second run changes nothing.
-- Tenant-scoped (R5): the table keeps its `organization_id`, FORCE row level
-- security and its policy. No new table, so no new tenant-coverage row.
-- Pinned by tests/unit/test_mail_handoff_incident.py (R8), which finds this
-- file by CONTENT, never by number (R1).
-- ============================================================================

BEGIN;

ALTER TABLE whatsapp_pending_acts ADD COLUMN IF NOT EXISTS act_home TEXT;

DO $home$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conname = 'whatsapp_pending_acts_act_home_shape'
           AND conrelid = 'whatsapp_pending_acts'::regclass
    ) THEN
        ALTER TABLE whatsapp_pending_acts
            ADD CONSTRAINT whatsapp_pending_acts_act_home_shape
            CHECK (act_home IS NULL
                   OR act_home = 'platform'
                   OR act_home ~ '^agent:[a-z][a-z0-9-]{0,63}$');
    END IF;
END
$home$;

COMMIT;
