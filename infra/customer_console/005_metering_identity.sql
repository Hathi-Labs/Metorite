-- ============================================================================
-- 005_metering_identity.sql — the meter's identity is OURS, not the caller's
-- ============================================================================
-- Spec: customer_console.md §6 CP-4 · found by independent verification
-- of CP-4 (finding F2), measured on a live database.
--
-- CP-4 keyed usage_event on a CALLER-SUPPLIED request_id, and 003 made that
-- (organization_id, request_id) unique. Combined, the customer decides whether
-- they are metered: five distinct completions sent with one reused id produced
-- ONE usage row while the provider was called five times.
--
--     5 completions, one reused request_id -> usage_event rows = 1
--
-- Unpriced today, so it costs nothing — but CP-6 bolts the rate card and the
-- balance gate onto this row. The row IS the invoice, and the party being
-- invoiced must not control whether it exists. This is the same class of defect
-- CP-3 verification found one layer down (003's F3).
--
-- So: request_id becomes SERVER-generated, one per provider call, always. The
-- caller's own correlation id is kept as client_ref — useful for support and
-- for tracing, trusted for nothing.
--
-- Idempotency of OUR retries stays possible because we generate the id: a
-- retry we initiate reuses the id we already minted. A retry the CUSTOMER
-- initiates is a second provider call and is metered as one, which is correct
-- — we paid for it twice.
-- ============================================================================

ALTER TABLE usage_event ADD COLUMN IF NOT EXISTS client_ref TEXT;

CREATE INDEX IF NOT EXISTS usage_event_client_ref_idx
    ON usage_event (organization_id, client_ref) WHERE client_ref IS NOT NULL;

COMMENT ON COLUMN usage_event.client_ref IS
    'The caller''s own correlation id. NOT the idempotency key and NOT unique — '
    'a caller that controls the meter''s identity controls whether it is billed '
    '(CP-4 verification F2). request_id is server-generated.';
