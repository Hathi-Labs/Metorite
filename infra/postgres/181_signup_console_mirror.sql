-- ============================================================================
-- 181_signup_console_mirror.sql — WS-31 CP-2e: the signup Console-mirror marker
-- ============================================================================
-- Spec: project-docs/specs/customer_console.md §6 CP-2e.
--
-- CP-2c ships a real dark window: a TRANSIENT Console failure during signup
-- leaves an org live on the TENANT plane but never mirrored to the Customer
-- Console, and step 0a's `access.membership_of` → `AlreadyMember` short-circuits
-- any literal route resubmit, so nothing in slice 2 re-drives step 2. The org is
-- un-metered / absent from the billing registry until reconciled. CP-2e is the
-- out-of-band SWEEP that closes it, and this migration is the state it reads and
-- writes:
--
--   * `console_mirrored_at` — the sweep's WHOLE selection predicate. NULL means
--     "this org has never been mirrored to the Console"; the signup route and the
--     reconciler both set it to now() on a Console-mirror SUCCESS. The sweep
--     selects on it being NULL (AND first_party = false).
--   * `gstin` / `billing_state` — the two values signup threads to the Console in
--     step 2 and NOWHERE else on the tenant plane today (179's INSERT writes only
--     slug/display_name/domain). They are LOST the instant step 2 fails — the
--     exact rows the sweep needs — so signup now persists them here, immediately
--     after step 1 and before step 2, and the sweep re-drives faithfully on them.
--
-- ⚠️ R6 EXPAND, forward-only, no backfill, NO DEFAULT — 177's precedent, argued
-- not copied. All three are nullable and default-free. On `console_mirrored_at`
-- NULL is LOAD-BEARING: a `DEFAULT now()` would stamp every pre-existing row
-- "already mirrored" and hide the very gap this ticket closes (177 makes the
-- identical argument for `registry_status`). On `gstin`/`billing_state` NULL
-- means "not supplied / pre-dates this column", the honest state for the seeded
-- `default` org and any pre-CP-2e row. Old code meets new schema safely (R6's
-- purpose): nothing running before this migration reads any of the three.
--
-- ⚠️ R5 source gate — satisfied by inheritance, no new EXEMPT row, no new table.
-- This adds COLUMNS to `organization`, already in `gen_tenant_migration.EXEMPT`
-- ("the tenant list itself" — a policy that hid it from the connection resolving
-- WHICH tenant this is would break the box's first question). A column on an
-- exempt table inherits the table's reason and adds no exemption entry (177's
-- ruling); `tests/unit/test_tenant_coverage.py`'s map is left untouched. Zero new
-- tables, zero new DB-connection sites (R5(b)).
--
-- Idempotent (ADD COLUMN IF NOT EXISTS — which, unlike ADD CONSTRAINT, Postgres
-- supports directly). Depends on: 130_org_access_control.sql (organization),
-- 157_org_first_party.sql (the `first_party` the sweep predicate also reads).
-- Fence: tests/unit/test_signup_reconciler.py (R8, tenant + Console ladders).
-- ============================================================================

ALTER TABLE organization
    ADD COLUMN IF NOT EXISTS console_mirrored_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS gstin               TEXT,
    ADD COLUMN IF NOT EXISTS billing_state       TEXT;

COMMENT ON COLUMN organization.console_mirrored_at IS
    'CP-2e: when this org was last mirrored onto the Customer Console. NULL = '
    'never mirrored, which is the reconciler sweep''s selection predicate '
    '(WHERE console_mirrored_at IS NULL AND first_party = false). NO DEFAULT on '
    'purpose — a default would stamp existing rows "mirrored" and hide the gap.';

COMMENT ON COLUMN organization.gstin IS
    'CP-2e: the customer''s GST identification number, persisted at signup so a '
    'Console-mirror retry (the reconciler) can re-drive faithfully. Optional; '
    'NULL means not supplied / pre-dates this column.';

COMMENT ON COLUMN organization.billing_state IS
    'CP-2e: the customer''s registered billing state (place of supply), persisted '
    'at signup so the reconciler can re-drive the Console mirror faithfully.';
