-- ============================================================================
-- 198_signup_core_seats.sql — the team size a signup asked for, kept
-- ============================================================================
-- Spec: project-docs/specs/customer_console.md §6 CP-2c item 4 · CP-2e.
--
-- ⚠️ **This closes a hole the seat repair opened, and 181 is the precedent it
-- is argued from rather than copied.**
--
-- The signup form now asks how many people will use Metorite, and that count
-- becomes `core_seats` on the Customer Console in step 2 of the two-plane
-- provision. It is threaded through the call and persisted NOWHERE — exactly
-- the shape 181 fixed for `gstin`/`billing_state`, and it fails the same way:
--
--   * step 1 commits the tenant organization;
--   * step 2 fails transiently (a Console restart, a 5xx, the network);
--   * the founder resubmits, and step 0a's `membership_of` now finds their
--     tenant membership and answers `AlreadyMember`, so no resubmit can ever
--     re-drive step 2;
--   * the ONLY repair left is the CP-2e reconciler — and it reconstructs the
--     Console call from tenant-plane columns alone.
--
-- Without this column the reconciler cannot know the team size, so it re-drives
-- with no `core_seats`, the Console applies its default of ONE, and
-- `grant_seats` runs once only (`if not grants`). The organization is then
-- permanently stuck at one seat: the founder holds it, and the first colleague
-- they invite is refused at the cap with *"ask your admin for an invite"* — the
-- exact dead end the seat repair exists to close, reachable through its own
-- recovery path.
--
-- ⚠️ R6 EXPAND, forward-only, no backfill, NO DEFAULT — 181's ruling, and for
-- its reason. NULL is LOAD-BEARING and means *"this organization pre-dates the
-- question"*, which is honest for every row written before today and for the
-- seeded `default` org. A `DEFAULT 1` would be worse than nothing: it would
-- assert that every pre-existing signup asked for one seat, which is precisely
-- the wrong claim, and the reconciler would then re-drive that assertion onto
-- the Console as if the founder had made it. The reconciler sends the column
-- only when it is NOT NULL, so a NULL row keeps today's behaviour exactly.
--
-- ⚠️ It is a RECORD OF WHAT WAS ASKED, never the authority on what is held.
-- The Customer Console owns seats — `seat_grant` is the table, `seat_counts`
-- the reader, D19.3's hard cap the rule. This column is one number captured at
-- one moment so a retry can repeat it faithfully. Nothing may read it to decide
-- how many seats an organization HAS, and a later seat purchase does not update
-- it.
--
-- ⚠️ R5 source gate — satisfied by inheritance, no new EXEMPT row, no new
-- table. This adds a COLUMN to `organization`, already in
-- `gen_tenant_migration.EXEMPT` for the reason 177 and 181 both give. Zero new
-- tables, zero new DB-connection sites (R5(b)).
--
-- Idempotent (ADD COLUMN IF NOT EXISTS). Depends on:
-- 130_org_access_control.sql (organization), 181_signup_console_mirror.sql
-- (the sibling columns the same writer persists in the same call).
-- Fence: tests/unit/test_signup_reconciler.py and
-- tests/unit/test_signup_provision_route.py (R8, tenant + Console ladders).
-- ============================================================================

ALTER TABLE organization
    ADD COLUMN IF NOT EXISTS signup_core_seats INTEGER;

COMMENT ON COLUMN organization.signup_core_seats IS
    'The team size this organization asked for at signup, persisted so a '
    'Console-mirror retry (the CP-2e reconciler) can re-drive `core_seats` '
    'faithfully instead of falling back to the Console default of 1. NO '
    'DEFAULT on purpose — NULL means "pre-dates the question", and the '
    'reconciler omits the field for a NULL row. It is a RECORD OF WHAT WAS '
    'ASKED, never the authority on seats held: the Customer Console owns that '
    '(`seat_grant`), and a later purchase does not update this column.';
