-- ============================================================================
-- 001_control_plane.sql — WS-31 CP-1: the platform Control Plane schema
-- ============================================================================
-- Spec: project-docs/specs/customer_console.md §3 · doctrine
-- project-docs/specs/saas_operations_doctrine.md · decisions D32, D33.
--
-- ⚠️ THIS IS A DIFFERENT DATABASE FROM THE TENANT ONE, AND THAT IS THE POINT.
--
-- `saas_multitenancy.md` §0.9.2 defines three planes. This is the **Control**
-- plane: organizations, identities, placement, entitlements, subscriptions,
-- usage and the credit ledger — "shared, cross-tenant by design. No RLS — it
-- must read across tenants. It is the operator's view."
--
-- Consequences that decide where this file lives and how it is written:
--
--   * NOT in `infra/postgres/`. That directory is the TENANT ladder:
--     `scripts/apply_migrations.sh` replays all of it into the tenant database,
--     and `scripts/gen_tenant_migration.py` discovers tables from the same glob
--     in order to demand `organization_id` + FORCE RLS on each. Both would be
--     wrong here — these tables are deliberately cross-tenant, and putting them
--     on that ladder would either ship them into customer databases or force a
--     pile of RLS exemptions that mean nothing. Separate plane, separate ladder.
--
--   * NO row-level security, anywhere in this file. The operator console has to
--     answer "how many customers do we have and what do they owe us", which is
--     a question RLS exists to make unanswerable. Isolation for this plane is a
--     network and credential boundary, not a row predicate.
--
--   * It holds NO tenant business data. No mail, no CRM rows, no documents. A
--     compromise here exposes contracts and usage counts — bad, survivable —
--     rather than customers' actual content. Keep it that way: if a column
--     here would hold something a customer typed, it belongs in the tenant DB.
--
-- Idempotent (CREATE ... IF NOT EXISTS) per repo convention, so the ladder can
-- be replayed. R6's expand/contract rules bind from migration 002 onward; this
-- one creates a fresh database and has no running code to stay compatible with.
-- ============================================================================

-- Needed for gen_random_uuid(). pgcrypto rather than uuid-ossp: it is in
-- contrib on every supported Postgres and we already depend on it elsewhere.
CREATE EXTENSION IF NOT EXISTS pgcrypto;
-- CITEXT so an email is one identity regardless of how it was typed. Learned
-- in the tenant plane the expensive way: migration 162 had to retrofit
-- `lower(email)` uniqueness onto app_user after duplicates already existed.
CREATE EXTENSION IF NOT EXISTS citext;


-- ── §3.2 Registry and placement ─────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS organization (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    slug            TEXT NOT NULL UNIQUE,
    name            TEXT NOT NULL,
    -- The §4.1d lifecycle. `suspended` deliberately keeps login working while
    -- features lock: a suspended customer who cannot log in cannot pay you.
    status          TEXT NOT NULL DEFAULT 'trial'
                    CHECK (status IN ('trial', 'active', 'past_due',
                                      'suspended', 'cancelled', 'deleted')),
    -- GST fields, captured at SIGNUP rather than at first invoice
    -- (saas_operations_doctrine.md §3.1). SaaS is an 18 percent service supply
    -- and a B2B invoice needs the GSTIN and place of supply to split
    -- ("18 percent" spelled out deliberately: a literal per-cent sign in this
    -- file is read as a placeholder by psycopg and by anything else using
    -- client-side parameter binding, so it breaks every runner but psql.)
    -- CGST/SGST from IGST. Chasing a GSTIN after invoices have gone out is a
    -- customer conversation, not a migration — so the columns exist from day
    -- one even though they are nullable (a customer may be unregistered).
    gstin           TEXT,
    billing_state   TEXT,            -- place of supply; ISO 3166-2:IN subdivision
    -- Retention: set when the org enters `cancelled`, read by the export
    -- window. Never delete customer data on non-payment without one.
    export_until    TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE organization IS
    'The customer company. One row per company that bought Metorite; the '
    'authority for "which companies exist" that no single tenant deployment can '
    'answer (customer_console.md §3.1).';

CREATE TABLE IF NOT EXISTS deployment (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    label               TEXT NOT NULL UNIQUE,
    base_url            TEXT NOT NULL,
    -- Exists because §5.1 condition 3 warns that per-box drift is how version
    -- skew arrives. One place that knows what every box runs is the cheapest
    -- possible defence, and it costs one column.
    version_sha         TEXT,
    health_checked_at   TIMESTAMPTZ,
    status              TEXT NOT NULL DEFAULT 'active'
                        CHECK (status IN ('active', 'draining', 'retired')),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS org_placement (
    organization_id UUID PRIMARY KEY REFERENCES organization(id) ON DELETE CASCADE,
    deployment_id   UUID NOT NULL REFERENCES deployment(id),
    -- Which database within that deployment. Day one every row resolves to the
    -- same target; the INDIRECTION is the point, not the values — it is what
    -- turns "move this customer to their own database" into a data move rather
    -- than an architecture change (D15, and migration 159's tenant_placement
    -- makes the same argument one plane down).
    database_target TEXT,
    moved_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS user_identity (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email        CITEXT NOT NULL UNIQUE,
    display_name TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE user_identity IS
    'One row per HUMAN, globally — NOT per membership. The split exists because '
    'our own support staff need membership in more than one tenant on day two, '
    'and partners on day thirty. Mirrors migration 159 in the tenant plane, '
    'which becomes the local projection of this table (D32.4).';

CREATE TABLE IF NOT EXISTS org_membership (
    organization_id  UUID NOT NULL REFERENCES organization(id) ON DELETE CASCADE,
    user_identity_id UUID NOT NULL REFERENCES user_identity(id) ON DELETE CASCADE,
    role             TEXT NOT NULL DEFAULT 'member'
                     CHECK (role IN ('owner', 'admin', 'member')),
    status           TEXT NOT NULL DEFAULT 'active'
                     CHECK (status IN ('invited', 'active', 'suspended', 'removed')),
    joined_at        TIMESTAMPTZ,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (organization_id, user_identity_id)
);

CREATE INDEX IF NOT EXISTS org_membership_identity_idx
    ON org_membership (user_identity_id);


-- ── §3.3 Subscriptions and seats ────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS plan_catalog (
    slug        TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    -- core: the mandatory per-member base · center: a Center package ·
    -- addon: org-wide · bundle: all-Centers / Complete (D23, D24).
    kind        TEXT NOT NULL CHECK (kind IN ('core', 'center', 'addon', 'bundle')),
    -- PRICES ARE DATA. A price change must never be a code change — that is the
    -- same argument the rate card makes for AI (§3.4) and it applies here too.
    price_inr   NUMERIC(12, 2) NOT NULL,
    active      BOOLEAN NOT NULL DEFAULT TRUE,
    sort_order  INT NOT NULL DEFAULT 100
);

CREATE TABLE IF NOT EXISTS org_subscription (
    organization_id         UUID PRIMARY KEY REFERENCES organization(id) ON DELETE CASCADE,
    status                  TEXT NOT NULL DEFAULT 'trial'
                            CHECK (status IN ('trial', 'active', 'past_due',
                                              'suspended', 'cancelled')),
    trial_ends_at           TIMESTAMPTZ,
    current_period_start    DATE,
    current_period_end      DATE,
    -- The processor is the source of truth for MONEY; this database is the
    -- source of truth for ENTITLEMENTS and USAGE. These columns are the join
    -- between the two, never a place to recompute what the processor issued.
    provider                TEXT CHECK (provider IN ('razorpay', 'manual')),
    provider_customer_id    TEXT,
    provider_subscription_id TEXT,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS seat_grant (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id     UUID NOT NULL REFERENCES organization(id) ON DELETE CASCADE,
    plan_slug           TEXT NOT NULL REFERENCES plan_catalog(slug),
    -- Signed: a reduction is a NEGATIVE grant, never an UPDATE. Same reasoning
    -- as credit_ledger — the history of what a customer bought is exactly what
    -- you need on the day they dispute an invoice, and an UPDATE destroys it.
    quantity_purchased  INT NOT NULL,
    reason              TEXT,
    effective_from      TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS seat_grant_org_plan_idx
    ON seat_grant (organization_id, plan_slug);

CREATE TABLE IF NOT EXISTS seat_assignment (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id  UUID NOT NULL REFERENCES organization(id) ON DELETE CASCADE,
    plan_slug        TEXT NOT NULL REFERENCES plan_catalog(slug),
    user_identity_id UUID NOT NULL REFERENCES user_identity(id) ON DELETE CASCADE,
    -- Which purchase shape put this seat here (D23): a Center package, a
    -- bundle expansion, or an a-la-carte add-on.
    source           TEXT NOT NULL DEFAULT 'alacarte'
                     CHECK (source IN ('core', 'center', 'plan', 'alacarte')),
    assigned_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    released_at      TIMESTAMPTZ
);

-- THE constraint that makes double-assignment impossible rather than merely
-- unlikely. Partial, so a released seat can be re-assigned later: without the
-- WHERE clause a member who left and rejoined could never get their seat back.
CREATE UNIQUE INDEX IF NOT EXISTS seat_assignment_live_uniq
    ON seat_assignment (organization_id, plan_slug, user_identity_id)
    WHERE released_at IS NULL;

CREATE INDEX IF NOT EXISTS seat_assignment_org_idx
    ON seat_assignment (organization_id) WHERE released_at IS NULL;


-- ── §3.4 AI: keys, rating, usage, credits ───────────────────────────────────

CREATE TABLE IF NOT EXISTS llm_api_key (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id UUID NOT NULL REFERENCES organization(id) ON DELETE CASCADE,
    -- Looked up by prefix, verified by hash. The prefix is safe to log and
    -- safe to show in a UI ("cc_live_a8f3…"); the secret is never stored.
    prefix          TEXT NOT NULL UNIQUE,
    key_hash        TEXT NOT NULL,
    label           TEXT,
    scopes          TEXT[] NOT NULL DEFAULT '{}',
    created_by      TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    revoked_at      TIMESTAMPTZ
);

COMMENT ON TABLE llm_api_key IS
    'THE KEY RESOLVES THE TENANT. Everything downstream — budget gate, metering, '
    'model policy, rate limits — hangs off that one resolution, and the org is '
    'never taken from request input (user_management_contract.md R11).';

CREATE TABLE IF NOT EXISTS tier_binding (
    tier            TEXT NOT NULL,
    model           TEXT NOT NULL,
    effective_from  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tier, effective_from)
);

COMMENT ON TABLE tier_binding IS
    'The tier -> model map, centrally. Customers never see a model (D32.7); '
    'agents name a tier and this table decides what actually runs, so swapping '
    'a provider is one row here rather than a deploy to every customer box.';

CREATE TABLE IF NOT EXISTS model_rate_card (
    model                       TEXT NOT NULL,
    input_credits_per_1k        NUMERIC(14, 6) NOT NULL,
    output_credits_per_1k       NUMERIC(14, 6) NOT NULL,
    -- Cache reads are priced lower because they COST less. "Cached context is
    -- billed at a fraction" is a real differentiator that costs almost nothing.
    cached_input_credits_per_1k NUMERIC(14, 6) NOT NULL DEFAULT 0,
    effective_from              TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (model, effective_from)
);

CREATE TABLE IF NOT EXISTS usage_event (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id   UUID NOT NULL REFERENCES organization(id) ON DELETE CASCADE,
    user_email        CITEXT,
    agent             TEXT,
    module_slug       TEXT,
    model             TEXT,
    tier              TEXT,
    prompt_tokens     INT NOT NULL DEFAULT 0,
    completion_tokens INT NOT NULL DEFAULT 0,
    cached_tokens     INT NOT NULL DEFAULT 0,
    provider_cost_usd NUMERIC(14, 8),      -- bookkeeping only; no runtime FX
    billed_credits    NUMERIC(14, 4) NOT NULL DEFAULT 0,
    -- NOT DECORATION. Retries, stream reconnects and the streaming usage-rebuild
    -- path all create double-write opportunities. A customer billed twice for
    -- one call is a credibility event; this constraint is what makes it
    -- impossible rather than unlikely.
    request_id        TEXT NOT NULL UNIQUE,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS usage_event_org_created_idx
    ON usage_event (organization_id, created_at DESC);

CREATE TABLE IF NOT EXISTS credit_ledger (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id UUID NOT NULL REFERENCES organization(id) ON DELETE CASCADE,
    -- Positive = purchase/grant, negative = consumption.
    delta           NUMERIC(14, 4) NOT NULL,
    reason          TEXT NOT NULL,
    ref             TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE credit_ledger IS
    'APPEND-ONLY. Balance is SUM(delta) — there is deliberately no balance '
    'column to UPDATE, because a mutable balance destroys the audit trail at '
    'exactly the moment a customer disputes a charge. Cache the sum in Redis '
    'for the hot path; never write it back here as truth.';

CREATE INDEX IF NOT EXISTS credit_ledger_org_idx
    ON credit_ledger (organization_id, created_at DESC);

CREATE TABLE IF NOT EXISTS usage_rollup (
    organization_id UUID NOT NULL REFERENCES organization(id) ON DELETE CASCADE,
    period          DATE NOT NULL,
    dimension       TEXT NOT NULL,       -- 'module:crm', 'tier:balanced', 'member:…'
    quantity        NUMERIC(18, 4) NOT NULL,
    PRIMARY KEY (organization_id, period, dimension)
);

CREATE TABLE IF NOT EXISTS member_ai_cap (
    organization_id  UUID NOT NULL REFERENCES organization(id) ON DELETE CASCADE,
    user_identity_id UUID NOT NULL REFERENCES user_identity(id) ON DELETE CASCADE,
    monthly_credits  NUMERIC(14, 4) NOT NULL,
    -- 'degrade' drops the member to tier-fast; 'block' returns 402. Degrade is
    -- the default because a cap that stops work mid-sentence generates a
    -- support ticket, and the tier vocabulary is what makes degrading
    -- expressible at all (D32.8).
    on_exhaustion    TEXT NOT NULL DEFAULT 'degrade'
                     CHECK (on_exhaustion IN ('degrade', 'block')),
    PRIMARY KEY (organization_id, user_identity_id)
);


-- ── Provisioning idempotency (§2.1: "idempotent and resumable") ─────────────

CREATE TABLE IF NOT EXISTS provisioning_run (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    -- Caller-supplied. Two signup submissions with the same key produce ONE
    -- organization: provisioning is a multi-step distributed action that WILL
    -- fail halfway, and a half-provisioned tenant that cannot be re-run safely
    -- is a manual database repair on your first busy day.
    idempotency_key TEXT NOT NULL UNIQUE,
    organization_id UUID REFERENCES organization(id) ON DELETE SET NULL,
    -- Which steps have completed, so a re-run resumes rather than restarts.
    steps_done      TEXT[] NOT NULL DEFAULT '{}',
    status          TEXT NOT NULL DEFAULT 'running'
                    CHECK (status IN ('running', 'complete', 'failed')),
    last_error      TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);


-- ── Audit (every write that moves money or access lands one) ────────────────

CREATE TABLE IF NOT EXISTS control_audit (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id UUID REFERENCES organization(id) ON DELETE SET NULL,
    actor           TEXT NOT NULL,
    action          TEXT NOT NULL,
    detail          JSONB NOT NULL DEFAULT '{}',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS control_audit_org_idx
    ON control_audit (organization_id, created_at DESC);
