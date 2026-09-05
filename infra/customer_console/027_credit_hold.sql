-- 027 — reserve credits before a call, settle the real charge after it.
--
-- Spec: `project-docs/specs/credit_pricing.md` section 5 (slice 4).
--
-- ⚠️ **Do not write a bare percent sign in this file.** Migration 023 learned
-- that the expensive way.
--
-- 🔴 **Nothing reserves credits today.** The meter runs AFTER the provider
-- answers, so two calls arriving together each read the same balance, each
-- pass, and the organization goes negative by the size of the second one.
-- Under exactly the load where it matters, the gate does not hold.
--
-- 📌 **Three rows for one call, and the third is what makes a crash safe.**
--
--     hold     -1792   reserved before the call, at the WORST case
--     settle    -423   the real charge, written with the usage row
--     release  +1792   the reservation given back
--
-- Net: -423. A single mutable "reserved" column could not survive a process
-- that dies between the provider answering and the meter running — the credits
-- would stay reserved with nothing to reconcile them against. Three append-only
-- rows leave the ledger consistent at every point, and the sweeper can tell a
-- stranded hold from a settled one by looking for its partner.
--
-- ⚠️ **`hold_ref` points a settle and a release at the hold they close.**
-- Without it a sweeper cannot answer "is this hold still open" except by
-- matching on `ref`, which is a string convention rather than a constraint.
--
-- 🔴 **Idempotency needs NO new index.** `credit_ledger_reason_ref_unique`
-- already covers `(organization_id, reason, ref)`. The three writes use one
-- `ref` — the request id — and three different reasons, so a retried request
-- re-inserts nothing and re-charges nothing. That is the same property
-- `record_usage` already relies on, reused rather than rebuilt.
--
-- ⚠️ **`reason` still carries no CHECK, and that is deliberate.** `credits.py`
-- records why: `/credits/grant` accepts free-form reasons, so an expand-phase
-- migration must not reject rows the running code can still write. Narrowing
-- the request model comes first. This migration adds three words to the
-- vocabulary and constrains nothing.
--
-- R6: additive and nullable. Old code writes no `hold_ref` and stays legal.
--
-- Fences (R7): `tests/unit/test_customer_console_hold_settle.py`.

ALTER TABLE credit_ledger
    ADD COLUMN IF NOT EXISTS hold_ref BIGINT;

-- ⚠️ NOT a foreign key to `credit_ledger.id`, and the reason is R6. The column
-- is `uuid` on that table, so a self-referencing FK would need the type to
-- match, and it does not. The sweeper joins on `ref` and `reason`, which the
-- unique index above already makes exact for one organization.
--
-- The column stays for the AUDIT trail — a human reading a dispute wants to
-- see which hold a settle closed without reconstructing it from timestamps.

-- 🔴 The sweeper's index. PARTIAL, because an open hold is rare by
-- construction: every served call closes its own within milliseconds, and the
-- rows this index exists to find are the ones a crash left behind.
CREATE INDEX IF NOT EXISTS credit_ledger_open_hold_idx
    ON credit_ledger (organization_id, created_at)
    WHERE reason = 'hold';

COMMENT ON COLUMN credit_ledger.hold_ref IS
    'For a settle or a release, which hold it closes. NULL on every other '
    'row. Kept for the audit trail: a human reading a dispute should not have '
    'to reconstruct the pairing from timestamps. The sweeper matches on '
    '(organization_id, reason, ref), which the unique index makes exact.';
