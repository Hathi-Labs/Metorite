-- 026 — the second way a served call goes unbilled, and the louder one.
--
-- Spec: `project-docs/specs/credit_pricing.md` section 3 (slice 1, completed).
--
-- ⚠️ **Do not write a bare percent sign in this file.** Migration 023 learned
-- that the expensive way.
--
-- 🔴 **A provider whose usage shape we do not recognise bills ZERO and flags
-- NOTHING.** Migration 023 gave the partition failure a name. Measured
-- 2026-09-05, this one had none: `usage_from_response` never raises, so an
-- unreadable body returns three zeros, the partition assert passes (0 is not
-- greater than 0), `rate_call` multiplies zeros and answers zero credits, and
-- the row lands looking exactly like a served call that happened to be free.
--
-- That is worse than the partition case it sits beside. A partition failure at
-- least writes a log line. This one writes nothing at all, so a new provider
-- with an unexpected body would serve free traffic for as long as nobody
-- happened to read a spend chart and wonder.
--
-- 📌 **Both faults answer the owner's question the same way: we do not price
-- it, we absorb it.** Guessing is what migration 023 exists to stop, and an
-- estimate here would be the same defect pointed the other way. So the rule is
-- bill nothing, record that we billed nothing, and make the total visible on
-- the operator's own page — because "we eat it" is only acceptable while it is
-- rare, and nothing measured whether it was.
--
-- ⚠️ **A per-unit job is NOT unreadable when it reports no tokens.** An image,
-- a minute of audio and a character of speech are measured by `quantity` and
-- have no token counts at all. Only a TOKEN-priced task that served with a
-- zero prompt earns this slug, and `_record_completion` makes that
-- distinction rather than this migration.
--
-- R6: the vocabulary grows, and growing a CHECK's allowed set never rejects a
-- row the running code can already write. Old code writes NULL and stays
-- legal.
--
-- Fences (R7): `tests/unit/test_customer_console_credits.py` and
-- `tests/unit/test_customer_console_sql.py`.

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'usage_event_metering_fault_known'
    ) THEN
        ALTER TABLE usage_event DROP CONSTRAINT usage_event_metering_fault_known;
    END IF;

    ALTER TABLE usage_event ADD CONSTRAINT usage_event_metering_fault_known
        CHECK (
            metering_fault IS NULL
            OR metering_fault IN ('usage_partition', 'usage_unreadable')
        );
END $$;

-- ⚠️ The index is PARTIAL, and that is the point. A fault is rare by design,
-- so an index over every row would be mostly dead weight — and the operator
-- page asks exactly one question of this column: "which rows have one".
CREATE INDEX IF NOT EXISTS usage_event_metering_fault_idx
    ON usage_event (organization_id, created_at DESC)
    WHERE metering_fault IS NOT NULL;

COMMENT ON COLUMN usage_event.metering_fault IS
    'Why this call could not be metered, from a closed vocabulary. NULL means '
    'the meter worked. "usage_partition" means the counts contradicted each '
    'other. "usage_unreadable" means the provider reported nothing we '
    'recognise. ⚠️ This is NOT refusal_reason: the call SERVED and the '
    'customer holds their completion, so every read that COUNTS calls must '
    'keep counting this row. Only its billed_credits are zero, and that zero '
    'is money we chose to absorb rather than money the customer owed.';
