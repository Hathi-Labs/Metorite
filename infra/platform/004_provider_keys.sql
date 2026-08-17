-- ============================================================================
-- 004_provider_keys.sql — WS-31 CP-4: the Router's own provider credentials
-- ============================================================================
-- Spec: platform_control_plane.md §4 (the Router) · §3.4.
--
-- ⚠️ **Why this is not `acb_llm.key_store`.** That store exists and works, and
-- reusing it was the obvious move — but it reads the **tenant** database
-- (`acb_llm/key_store.py` imports `acb_common`, and its rows are keyed
-- `(organization_id, provider)` inside each Metorite deployment). The
-- Router is on the other side of the boundary: it holds OUR provider accounts,
-- centrally, and calls providers on behalf of every customer. Pointing it at a
-- tenant database would put our Anthropic/DeepSeek credentials on a customer's
-- box, which is the precise thing D32.1 moved metering here to avoid.
--
-- So this is a second store by necessity, not by drift. The seam it must not
-- duplicate is the *tenant* one; it is not on that seam at all.
--
-- BYOK (§3.4 — "BYOK is a tier, not an exception") is the one case where a row
-- here belongs to a customer: `organization_id` non-NULL means "this
-- organization insists on its own provider key", and such an org is metered but
-- not charged for tokens. NULL means the platform's own account, used for
-- everyone else.
--
-- Secrets are encrypted at rest with a key derived from
-- CONTROL_PLANE_ENCRYPTION_KEY, which never appears in this database.
-- ============================================================================

CREATE TABLE IF NOT EXISTS provider_credential (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    provider        TEXT NOT NULL,          -- 'deepseek', 'anthropic', 'groq', …
    -- NULL = the platform's own account. Non-NULL = a BYOK organization.
    organization_id UUID REFERENCES organization(id) ON DELETE CASCADE,
    -- Fernet ciphertext. Never the plaintext, and never logged.
    secret_enc      TEXT NOT NULL,
    api_base        TEXT,                   -- self-hosted / proxy endpoints
    label           TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    revoked_at      TIMESTAMPTZ
);

-- One live credential per (provider, org). Partial so a revoked one can be
-- replaced rather than blocking the row forever — the same shape, and the same
-- reasoning, as seat_assignment's live-only uniqueness.
CREATE UNIQUE INDEX IF NOT EXISTS provider_credential_live_uniq
    ON provider_credential (provider, COALESCE(organization_id,
                                               '00000000-0000-0000-0000-000000000000'::uuid))
    WHERE revoked_at IS NULL;

COMMENT ON TABLE provider_credential IS
    'The Router''s provider accounts. Distinct from acb_llm.key_store, which is '
    'the TENANT-side store inside each Metorite deployment — these '
    'credentials are ours and must never sit on a customer box.';
