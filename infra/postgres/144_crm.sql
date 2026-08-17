-- ============================================================================
-- 144_crm.sql — the native CRM spine: organizations, contacts, leads, deals,
--               statuses-as-data, one activity timeline, a status-change log.
--
-- What: spec project-docs/specs/crm_app.md §3.1–§3.10 (WS-26a). Four
--   entities on Frappe's lead → convert → deal+contact+organization shape,
--   trycompai's single activity spine + `source` provenance + the denormalized
--   `last_activity_at` column every write bumps.
--
-- Why: Metorite becomes the system of record for sales and Zoho becomes an
--   import source, then retires (§7). The Phase-0 graph tables
--   (`person`/`customer`/`deal`, migration 01) are a cross-system
--   entity-resolution MIRROR with different semantics — no statuses, no
--   activities, no leads — and are deliberately NOT extended: D-CRM-1. Statuses
--   are rows rather than an enum (D-CRM-2) because the importer has to be able
--   to represent Zoho's real stage names and the owner has to be able to
--   reshape the pipeline without a deploy.
--
-- Idempotent per infra/postgres/README.md: every CREATE TABLE / CREATE INDEX
-- carries IF NOT EXISTS, every seed INSERT carries ON CONFLICT, and the single
-- circular foreign key (crm_leads.converted_deal_id → crm_deals, which cannot
-- be declared inline because each table references the other) is added inside a
-- guarded DO $$ block. Pinned by tests/unit/test_crm_migration.py, which reads
-- this file as text — §10 runs no database, so an idempotency claim that is
-- only true by inspection is not a claim.
--
-- Depends on: 130_org_access_control.sql (feature_catalog).
-- ============================================================================

-- ── Organizations (Zoho: Accounts) ─────────────────────────────────────── §3.1

CREATE TABLE IF NOT EXISTS crm_organizations (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name                TEXT NOT NULL,
    website             TEXT,
    industry            TEXT,
    no_of_employees     INT,
    annual_revenue      NUMERIC(14,2),
    phone               TEXT,
    email               TEXT,
    address             JSONB,
    description         TEXT,
    linkedin_url        TEXT,
    owner_email         TEXT,
    -- Provenance. 'manual' = typed into this app; 'import' = came from Zoho;
    -- 'email'/'agent' = created by the platform on someone's behalf.
    source              TEXT NOT NULL DEFAULT 'manual'
                            CHECK (source IN ('manual', 'import', 'email', 'agent')),
    zoho_id             TEXT UNIQUE,
    last_activity_at    TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_crm_organizations_name
    ON crm_organizations (name);
-- The query predicate is lower(owner_email) = :owner (R10), so the index must
-- fold case too — a plain column index can never serve it.
CREATE INDEX IF NOT EXISTS idx_crm_organizations_owner_email
    ON crm_organizations (lower(owner_email));
CREATE INDEX IF NOT EXISTS idx_crm_organizations_last_activity_at
    ON crm_organizations (last_activity_at);

-- ── Contacts (Zoho: Contacts) ──────────────────────────────────────────── §3.2

CREATE TABLE IF NOT EXISTS crm_contacts (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    first_name          TEXT NOT NULL,
    last_name           TEXT,
    email               TEXT,
    phone               TEXT,
    mobile              TEXT,
    title               TEXT,
    organization_id     UUID REFERENCES crm_organizations (id) ON DELETE SET NULL,
    description         TEXT,
    linkedin_url        TEXT,
    owner_email         TEXT,
    source              TEXT NOT NULL DEFAULT 'manual'
                            CHECK (source IN ('manual', 'import', 'email', 'agent')),
    zoho_id             TEXT UNIQUE,
    last_activity_at    TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Deliberately NOT unique: the Zoho export has duplicate and blank addresses,
-- so a unique index would make the import fail rather than the data honest.
-- Email identifies a person only at CONVERSION time, where code enforces it
-- (§3.7 step 1).
CREATE INDEX IF NOT EXISTS idx_crm_contacts_email
    ON crm_contacts (lower(email));
CREATE INDEX IF NOT EXISTS idx_crm_contacts_organization_id
    ON crm_contacts (organization_id);
CREATE INDEX IF NOT EXISTS idx_crm_contacts_owner_email
    ON crm_contacts (lower(owner_email));
CREATE INDEX IF NOT EXISTS idx_crm_contacts_last_activity_at
    ON crm_contacts (last_activity_at);

-- ── Statuses as data, not as an enum ───────────────────────────────────── §3.6
--
-- `position` is the kanban lane order; `type` is the machine-readable class the
-- rules key off (entering a `lost` status requires a lost reason; entering
-- `won`/`lost` stamps closed_at). The importer appends Zoho's real stage names
-- to these rows rather than mapping them onto a fixed vocabulary.

CREATE TABLE IF NOT EXISTS crm_lead_statuses (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name        TEXT NOT NULL UNIQUE,
    color       TEXT NOT NULL DEFAULT 'gray',
    position    INT NOT NULL,
    type        TEXT NOT NULL
                    CHECK (type IN ('open', 'ongoing', 'on_hold', 'won', 'lost')),
    is_default  BOOLEAN NOT NULL DEFAULT false
);

CREATE TABLE IF NOT EXISTS crm_deal_statuses (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name        TEXT NOT NULL UNIQUE,
    color       TEXT NOT NULL DEFAULT 'gray',
    position    INT NOT NULL,
    type        TEXT NOT NULL
                    CHECK (type IN ('open', 'ongoing', 'on_hold', 'won', 'lost')),
    is_default  BOOLEAN NOT NULL DEFAULT false,
    -- The default win probability for a deal sitting in this stage; a deal with
    -- a NULL probability inherits it on entry.
    probability SMALLINT NOT NULL DEFAULT 0
);

-- ── Lost reasons ───────────────────────────────────────────────────────── §3.10

CREATE TABLE IF NOT EXISTS crm_lost_reasons (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    label       TEXT NOT NULL UNIQUE,
    position    INT NOT NULL DEFAULT 0
);

-- ── Leads (Zoho: Leads) ────────────────────────────────────────────────── §3.3
--
-- Person and company are denormalized inline and there are NO entity FKs until
-- conversion: a lead is an unqualified scrap of information, and forcing it to
-- point at an organization row would mint one per scrap.

CREATE TABLE IF NOT EXISTS crm_leads (
    id                          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    first_name                  TEXT,
    last_name                   TEXT,
    -- Display name, computed by the API on write from the fallback chain
    -- names → organization_name → email local-part → 'Unnamed lead'.
    lead_name                   TEXT NOT NULL,
    email                       TEXT,
    phone                       TEXT,
    mobile                      TEXT,
    -- Free text. Becomes a crm_organizations row only on conversion (§3.7).
    organization_name           TEXT,
    website                     TEXT,
    industry                    TEXT,
    no_of_employees             INT,
    annual_revenue              NUMERIC(14,2),
    -- RESTRICT: a status still holding leads cannot be deleted out from under
    -- them — admin.py surfaces that as a 409 rather than orphaning rows.
    status_id                   UUID NOT NULL
                                    REFERENCES crm_lead_statuses (id) ON DELETE RESTRICT,
    lead_source                 TEXT,
    owner_email                 TEXT,
    description                 TEXT,
    lost_reason_id              UUID REFERENCES crm_lost_reasons (id) ON DELETE SET NULL,
    lost_note                   TEXT,
    -- Conversion provenance (§3.7 step 4).
    converted_at                TIMESTAMPTZ,
    converted_contact_id        UUID REFERENCES crm_contacts (id) ON DELETE SET NULL,
    converted_organization_id   UUID REFERENCES crm_organizations (id) ON DELETE SET NULL,
    -- FK added in the guarded DO $$ below, once crm_deals exists: crm_deals
    -- references crm_leads for provenance, so the two cannot both be inline.
    converted_deal_id           UUID,
    source                      TEXT NOT NULL DEFAULT 'manual'
                                    CHECK (source IN ('manual', 'import', 'email', 'agent')),
    zoho_id                     TEXT UNIQUE,
    last_activity_at            TIMESTAMPTZ,
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at                  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_crm_leads_status_id
    ON crm_leads (status_id);
CREATE INDEX IF NOT EXISTS idx_crm_leads_owner_email
    ON crm_leads (lower(owner_email));
CREATE INDEX IF NOT EXISTS idx_crm_leads_email
    ON crm_leads (lower(email));
CREATE INDEX IF NOT EXISTS idx_crm_leads_last_activity_at
    ON crm_leads (last_activity_at);

-- ── Deals (Zoho: Deals) ────────────────────────────────────────────────── §3.4

CREATE TABLE IF NOT EXISTS crm_deals (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name                    TEXT NOT NULL,
    organization_id         UUID REFERENCES crm_organizations (id) ON DELETE SET NULL,
    status_id               UUID NOT NULL
                                REFERENCES crm_deal_statuses (id) ON DELETE RESTRICT,
    -- The stage-age clock: re-stamped by every status transition, so "how long
    -- has this sat in Proposal" is a subtraction rather than a log scan.
    status_changed_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    amount                  NUMERIC(14,2),
    -- INR only in v1 (§1 non-goals); the column exists so multi-currency is
    -- additive later rather than a migration of every row.
    currency                TEXT NOT NULL DEFAULT 'INR',
    -- NULL until the deal enters a status; then auto-filled from that status's
    -- default (§3.6) unless the caller states one.
    probability             SMALLINT,
    expected_close_date     DATE,
    -- Stamped when the deal enters a won/lost-type status.
    closed_at               TIMESTAMPTZ,
    lost_reason_id          UUID REFERENCES crm_lost_reasons (id) ON DELETE SET NULL,
    lost_note               TEXT,
    next_step               TEXT,
    -- Provenance: the lead this deal was converted from. Powers the timeline
    -- inheritance in §5.3 — a deal's history includes its lead's.
    lead_id                 UUID REFERENCES crm_leads (id) ON DELETE SET NULL,
    owner_email             TEXT,
    description             TEXT,
    source                  TEXT NOT NULL DEFAULT 'manual'
                                CHECK (source IN ('manual', 'import', 'email', 'agent')),
    zoho_id                 TEXT UNIQUE,
    last_activity_at        TIMESTAMPTZ,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_crm_deals_status_id
    ON crm_deals (status_id);
CREATE INDEX IF NOT EXISTS idx_crm_deals_organization_id
    ON crm_deals (organization_id);
CREATE INDEX IF NOT EXISTS idx_crm_deals_owner_email
    ON crm_deals (lower(owner_email));
CREATE INDEX IF NOT EXISTS idx_crm_deals_expected_close_date
    ON crm_deals (expected_close_date);
CREATE INDEX IF NOT EXISTS idx_crm_deals_last_activity_at
    ON crm_deals (last_activity_at);

-- The one cycle in this schema, closed here because it cannot be declared
-- inline in either direction. Guarded on pg_constraint so a redeploy is a
-- no-op — ALTER TABLE … ADD CONSTRAINT has no IF NOT EXISTS.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'crm_leads_converted_deal_id_fkey'
    ) THEN
        ALTER TABLE crm_leads
            ADD CONSTRAINT crm_leads_converted_deal_id_fkey
            FOREIGN KEY (converted_deal_id)
            REFERENCES crm_deals (id) ON DELETE SET NULL;
    END IF;
END $$;

-- ── Deal ⟷ contact, with a role ────────────────────────────────────────── §3.5
--
-- CASCADE on both sides: the link row is meaningless without either end. At
-- most one primary per deal is enforced in code, not by a partial unique index,
-- because the importer sets primaries in a pass that would fight one.

CREATE TABLE IF NOT EXISTS crm_deal_contacts (
    deal_id     UUID NOT NULL REFERENCES crm_deals (id) ON DELETE CASCADE,
    contact_id  UUID NOT NULL REFERENCES crm_contacts (id) ON DELETE CASCADE,
    role        TEXT,
    is_primary  BOOLEAN NOT NULL DEFAULT false,
    PRIMARY KEY (deal_id, contact_id)
);

-- ── Activities — the single timeline spine ─────────────────────────────── §3.8
--
-- One table for notes, calls, meetings, follow-up tasks, status changes and
-- system events, against any of the four entities. All four target columns are
-- nullable and a CHECK requires at least one: an activity attached to nothing
-- is invisible in every timeline and would accumulate silently.

CREATE TABLE IF NOT EXISTS crm_activities (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    type                TEXT NOT NULL
                            CHECK (type IN ('note', 'call', 'meeting', 'task',
                                            'status_change', 'system')),
    subject             TEXT,
    body                TEXT,
    occurred_at         TIMESTAMPTZ,
    due_at              TIMESTAMPTZ,
    completed_at        TIMESTAMPTZ,
    lead_id             UUID REFERENCES crm_leads (id) ON DELETE CASCADE,
    deal_id             UUID REFERENCES crm_deals (id) ON DELETE CASCADE,
    contact_id          UUID REFERENCES crm_contacts (id) ON DELETE CASCADE,
    organization_id     UUID REFERENCES crm_organizations (id) ON DELETE CASCADE,
    -- An email address, or `agent:<name>` when the platform wrote it.
    created_by          TEXT NOT NULL,
    meta                JSONB,
    zoho_id             TEXT UNIQUE,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT crm_activities_target_required CHECK (
        lead_id IS NOT NULL
        OR deal_id IS NOT NULL
        OR contact_id IS NOT NULL
        OR organization_id IS NOT NULL
    )
);

CREATE INDEX IF NOT EXISTS idx_crm_activities_deal_id_created_at
    ON crm_activities (deal_id, created_at);
CREATE INDEX IF NOT EXISTS idx_crm_activities_lead_id_created_at
    ON crm_activities (lead_id, created_at);
CREATE INDEX IF NOT EXISTS idx_crm_activities_contact_id_created_at
    ON crm_activities (contact_id, created_at);
CREATE INDEX IF NOT EXISTS idx_crm_activities_organization_id_created_at
    ON crm_activities (organization_id, created_at);
-- Partial: the only question asked of due_at is "what is still open", and the
-- completed tail is the majority of the table within a quarter.
CREATE INDEX IF NOT EXISTS idx_crm_activities_due_at
    ON crm_activities (due_at) WHERE completed_at IS NULL;

-- ── Status changes — funnel and dwell analytics for free ───────────────── §3.9
--
-- Statuses are rows and can be renamed or deleted, so this log stores the
-- status NAMES rather than FKs: an analytics row that stops meaning anything
-- when somebody tidies the pipeline is not a log.

CREATE TABLE IF NOT EXISTS crm_status_changes (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    entity_type     TEXT NOT NULL CHECK (entity_type IN ('lead', 'deal')),
    entity_id       UUID NOT NULL,
    from_status     TEXT,
    to_status       TEXT NOT NULL,
    changed_by      TEXT NOT NULL,
    changed_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- How long the entity sat in `from_status`. NULL for the first change.
    dwell_seconds   BIGINT
);

CREATE INDEX IF NOT EXISTS idx_crm_status_changes_entity
    ON crm_status_changes (entity_type, entity_id, changed_at);

-- ── Seeds ───────────────────────────────────────────────────────────────────
--
-- ON CONFLICT DO NOTHING throughout: these are starting points, not managed
-- rows. The owner reshapes the pipeline in the app and a redeploy must not
-- reinstate a stage they deleted or undo a rename.

INSERT INTO crm_lead_statuses (name, color, position, type, is_default) VALUES
    ('New',       'gray',  10, 'open',    true),
    ('Contacted', 'blue',  20, 'ongoing', false),
    ('Nurture',   'amber', 30, 'ongoing', false),
    -- `won` for a LEAD means qualified — it left the lead funnel upward. §3.7
    -- step 4 moves a converted lead to the first won-type status.
    ('Qualified', 'green', 40, 'won',     false),
    ('Lost',      'red',   50, 'lost',    false)
ON CONFLICT (name) DO NOTHING;

INSERT INTO crm_deal_statuses (name, color, position, type, is_default, probability) VALUES
    ('Qualification',  'gray',   10, 'open',    true,   10),
    ('Needs Analysis', 'blue',   20, 'ongoing', false,  25),
    ('Proposal',       'violet', 30, 'ongoing', false,  50),
    ('Negotiation',    'amber',  40, 'ongoing', false,  75),
    ('Closed Won',     'green',  50, 'won',     false, 100),
    ('Closed Lost',    'red',    60, 'lost',    false,   0)
ON CONFLICT (name) DO NOTHING;

INSERT INTO crm_lost_reasons (label, position) VALUES
    ('Price',               10),
    ('Competitor',          20),
    ('No budget',           30),
    ('No response',         40),
    ('Requirement dropped', 50),
    ('Other',               60)
ON CONFLICT (label) DO NOTHING;

-- ── Feature registration ────────────────────────────────────────────────────
--
-- sort_order 55 puts CRM beside Tasks (50) rather than defaulting to last.
-- is_default false is deliberate: `feature:crm` reaches `*`-holders (owner) and
-- `admin` (`feature:*`) until an admin grants it, because migration 130
-- enumerates the `manager`/`member` feature grants. That is D-CRM-3's posture
-- and is restated in WS-26c. The matching FEATURES entry lives in
-- packages/acb_auth/acb_auth/permissions.py — a catalog row with no FEATURES
-- entry is invisible to /auth/me even for an owner, which is why
-- tests/unit/test_org_access_control.py pins the two against each other.

INSERT INTO feature_catalog (slug, label, description, nav_href, category, sort_order, is_default) VALUES
    ('crm', 'CRM', 'Pipeline, leads and customers', '/crm', 'apps', 55, false)
ON CONFLICT (slug) DO UPDATE
    SET label       = EXCLUDED.label,
        description = EXCLUDED.description,
        nav_href    = EXCLUDED.nav_href,
        category    = EXCLUDED.category,
        sort_order  = EXCLUDED.sort_order;
        -- is_default is intentionally NOT overwritten (same rule as 130): an
        -- admin may have retuned the default set and a redeploy must not stomp it.
