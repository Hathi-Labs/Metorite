-- ============================================================================
-- 235_whatsapp_member_links.sql — the link from a member to their WhatsApp
-- phone, for the assistant channel.
--
-- What: `whatsapp_member_links`, one row for each link code that a member
--       asks for, and for each link that a code makes. A row starts as
--       `pending` with the hash of a code and an expiry. WAC-2 makes it
--       `active` when the code arrives from a phone, and writes the sender id
--       (`wa_id`). A new code, a removal or an admin act makes it `revoked`.
-- Why:  WS-47 WAC-1, `project-docs/specs/whatsapp_assistant_channel.md` §5.2,
--       §5.3 and §5.9. Decisions D-WAC-1 and D-WAC-2 (amended 2026-10-09).
-- Depends on: 130_org_access_control.sql (organization).
--
-- ── The rules the table holds ───────────────────────────────────────────────
--
-- * No column holds the plain code. `code_hash` is the SHA-256 of the code,
--   in hex. A code has 10 characters from a 32-character alphabet (50 bits),
--   and it expires in 15 minutes (`code_expires_at`).
-- * A member has at most ONE pending code in an organization
--   (`uq_whatsapp_member_links_one_pending`). The issue route revokes the old
--   pending row first, under an advisory lock, and the index is the backstop.
-- * One phone has at most one active link in an organization
--   (`uq_whatsapp_member_links_active_phone`).
-- * One phone has at most one CURRENT link over all organizations
--   (`uq_whatsapp_member_links_current_phone`). A unique index ignores row
--   level security, so this index holds across tenants. That is the point.
-- * A pending row has a hash and an expiry. An active row has a sender. Only
--   an active row can be current. Three CHECKs hold these.
--
-- ⚠️ The webhook knows no organization when a message arrives. WAC-2 adds
-- SECURITY DEFINER functions for its reads by `wa_id` and by `code_hash`
-- (§5.3). This file adds none, and it adds no connection site (R5).
--
-- Expand only (R6): one new table with five indexes. No rename, no drop, no
-- UPDATE of another table. Old code names no new object.
-- Idempotent per infra/postgres/README.md: a second run changes nothing.
-- Tenant-scoped (R5): `organization_id`, ENABLE + FORCE row level security and
-- a policy with USING and WITH CHECK, in the shape of 231_email_insights.sql.
-- The generated phase files carry the same table (`generated/`).
-- Pinned by tests/unit/test_wac_link_table.py, which finds this file by
-- CONTENT, never by number (R1).
-- ============================================================================

BEGIN;

CREATE TABLE IF NOT EXISTS whatsapp_member_links (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id  UUID NOT NULL REFERENCES organization (id) ON DELETE CASCADE,
    member_email     TEXT NOT NULL,
    wa_id            TEXT,
    status           TEXT NOT NULL DEFAULT 'pending',
    code_hash        TEXT,
    code_expires_at  TIMESTAMPTZ,
    opted_in_at      TIMESTAMPTZ,
    linked_at        TIMESTAMPTZ,
    last_inbound_at  TIMESTAMPTZ,
    revoked_at       TIMESTAMPTZ,
    is_current       BOOLEAN NOT NULL DEFAULT false,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT whatsapp_member_links_status_known CHECK (
        status IN ('pending', 'active', 'revoked')
    ),
    CONSTRAINT whatsapp_member_links_wa_id_digits CHECK (
        wa_id IS NULL OR wa_id ~ '^[0-9]{6,20}$'
    ),
    CONSTRAINT whatsapp_member_links_pending_has_code CHECK (
        status <> 'pending'
        OR (code_hash IS NOT NULL AND code_expires_at IS NOT NULL)
    ),
    CONSTRAINT whatsapp_member_links_active_has_sender CHECK (
        status <> 'active' OR wa_id IS NOT NULL
    ),
    CONSTRAINT whatsapp_member_links_current_is_active CHECK (
        NOT is_current OR status = 'active'
    )
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_whatsapp_member_links_active_phone
    ON whatsapp_member_links (wa_id, organization_id)
    WHERE status = 'active';

CREATE UNIQUE INDEX IF NOT EXISTS uq_whatsapp_member_links_current_phone
    ON whatsapp_member_links (wa_id)
    WHERE is_current;

CREATE UNIQUE INDEX IF NOT EXISTS uq_whatsapp_member_links_one_pending
    ON whatsapp_member_links (organization_id, member_email)
    WHERE status = 'pending';

CREATE INDEX IF NOT EXISTS idx_whatsapp_member_links_pending_code
    ON whatsapp_member_links (code_hash)
    WHERE status = 'pending';

CREATE INDEX IF NOT EXISTS idx_whatsapp_member_links_member
    ON whatsapp_member_links (organization_id, member_email);

DO $rls$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_class
         WHERE oid = 'whatsapp_member_links'::regclass
           AND relrowsecurity
           AND relforcerowsecurity
    ) THEN
        EXECUTE 'ALTER TABLE whatsapp_member_links ENABLE ROW LEVEL SECURITY';
        EXECUTE 'ALTER TABLE whatsapp_member_links FORCE  ROW LEVEL SECURITY';
        EXECUTE 'DROP POLICY IF EXISTS whatsapp_member_links_tenant_isolation '
                'ON whatsapp_member_links';
        EXECUTE 'CREATE POLICY whatsapp_member_links_tenant_isolation '
                'ON whatsapp_member_links '
                '    USING      (organization_id = '
                '        current_setting(''app.tenant_id'', true)::uuid) '
                '    WITH CHECK (organization_id = '
                '        current_setting(''app.tenant_id'', true)::uuid)';
    END IF;
END
$rls$;

COMMENT ON TABLE whatsapp_member_links IS
    'WAC-1: a member''s link to their WhatsApp phone for the assistant '
    'channel. Holds only the SHA-256 of a link code, never the code. '
    'whatsapp_assistant_channel.md §5.3.';

COMMIT;
