-- ============================================================================
-- 199_registry_trial_ends_at.sql — the trial deadline, cached on the tenant
-- ============================================================================
-- Spec: project-docs/specs/customer_console.md §6 CP-2j.
--
-- The sibling of 177's `registry_status`, and it exists for the same reason.
-- 177 cached the registry's LAST-SEEN lifecycle word so the box could act on it
-- without a Console round trip. This caches the registry's last-seen trial
-- DEADLINE, so the customer's own app can say *"Trial — 12 days left"*.
--
-- ⚠️ **Why the tenant plane and not a billing call.** The Customer Console owns
-- the subscription, and `GET /me/billing` is the customer's door to it. That
-- door is gated on `can_pay` and reached with the organization key, so it
-- answers the BILLING page. The banner is not a billing page — every admin sees
-- it on every screen — and making the shell wait on a cross-plane call to render
-- a sentence is the coupling `console_resolve`'s projection exists to avoid.
-- Sign-in already carries the answer, so this is where it lands.
--
-- ⚠️ R6 EXPAND, forward-only, no backfill, NO DEFAULT — 177's and 181's ruling.
-- NULL is LOAD-BEARING and means *"the registry has not told us"*, which is the
-- honest state for the seeded `default` org, for any row written before this
-- column, and for a box whose resolve flag has never been on. The surface
-- renders NOTHING on NULL rather than inventing a deadline, so a missing value
-- degrades to silence and never to a countdown nobody agreed to.
--
-- ⚠️ **It is a CACHE, never the authority.** The Console owns `org_subscription.
-- trial_ends_at`. This column is what the last successful resolve saw, refreshed
-- on every resolve that takes the write path, and it is read for DISPLAY only.
-- Nothing may gate access on it — the lifecycle `status` (177) is the gate, and
-- a second gate keyed on a cached date is how a clock skew locks a customer out.
--
-- ⚠️ R5 source gate — satisfied by inheritance, no new EXEMPT row, no new table.
-- A COLUMN on `organization`, already in `gen_tenant_migration.EXEMPT` for the
-- reason 177, 181 and 198 all give. Zero new tables, zero new connection sites.
--
-- Idempotent (ADD COLUMN IF NOT EXISTS). Depends on:
-- 177_console_resolve_projection.sql (the projection this joins).
-- Fence: tests/unit/test_deployment_resolve_cache.py (R8, tenant ladder).
-- ============================================================================

ALTER TABLE organization
    ADD COLUMN IF NOT EXISTS registry_trial_ends_at TIMESTAMPTZ;

COMMENT ON COLUMN organization.registry_trial_ends_at IS
    'CP-2j: the registry''s LAST-SEEN trial deadline for this organization, '
    'cached from the sign-in resolve answer so the customer''s own app can '
    'render "Trial — N days left" without a cross-plane call. NO DEFAULT on '
    'purpose — NULL means "the registry has not told us", and the surface '
    'renders nothing rather than inventing a deadline. DISPLAY ONLY: the '
    'Console owns org_subscription.trial_ends_at, and access is gated on '
    'registry_status (177), never on this date.';
