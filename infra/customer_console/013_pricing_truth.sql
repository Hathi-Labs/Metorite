-- 013 — what the call COST us, and which step of the chain served it.
--
-- 🔴 **`provider_cost_usd` existed for 12 migrations and nothing ever wrote
-- it.** 001 added the column, the margin queries COALESCE it, 012's header
-- cites it as the billing input — and `_record_completion` never passed it, so
-- every row holds NULL and every margin reads "not measured", forever. A1
-- (`ai_metering_and_analytics.md` §6) calls margin the most important number
-- on the operator side. This migration adds the columns the WRITE needs;
-- the write itself lands in the same slice.
--
-- ⚠️ **All three additions are nullable or defaulted (R6).** Old code meets
-- this schema mid-deploy and keeps inserting; old rows stay honest: NULL
-- `served_rank` means "written before the chain recorded evidence", and that
-- is true of them.

-- ── usage_event: the two facts about HOW the call was served ────────────────
--
-- ⚠️ `model` already records the step that ANSWERED (the route reassigns
-- `resolved` after the walk). What is missing is its POSITION: rank 1 is the
-- primary, anything above 1 is a failover that earned its keep. Deriving the
-- position later by joining `tier_binding` history is possible and fragile —
-- the join breaks the day a chain is re-bound, which is exactly when somebody
-- is reading the history.

ALTER TABLE usage_event
    ADD COLUMN IF NOT EXISTS served_rank INTEGER;

-- 🔴 §3.4: a BYOK organization is metered but NOT charged for tokens. The
-- rater cannot honour that without knowing which credential served, and this
-- column is the auditable record that it did. `_rate_completion`'s own
-- docstring named this the gap to close BEFORE any real price is set.
ALTER TABLE usage_event
    ADD COLUMN IF NOT EXISTS byok_served BOOLEAN NOT NULL DEFAULT FALSE;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'usage_event_served_rank_positive'
    ) THEN
        ALTER TABLE usage_event ADD CONSTRAINT usage_event_served_rank_positive
            CHECK (served_rank IS NULL OR served_rank >= 1);
    END IF;
END $$;

COMMENT ON COLUMN usage_event.served_rank IS
    'Position in the fallback chain of the step that answered. 1 is the '
    'primary; above 1 is a failover that earned its keep. NULL on rows '
    'written before this column existed.';

COMMENT ON COLUMN usage_event.byok_served IS
    'TRUE when the call ran on the ORGANIZATION''S own vendor credential '
    '(BYOK, spec 3.4). Such a call is metered and billed zero, and our '
    'provider_cost_usd for it is zero because we paid the vendor nothing.';

-- ── model_profile: the vendor's cached-read price ───────────────────────────
--
-- ⚠️ Vendors charge a FRACTION of the input price for a cached read, and 012
-- gave the profile only input and output. Costing a cache-hitting call at the
-- full input price would OVERSTATE our cost and understate the margin — a
-- wrong number in the safe direction is still a wrong number. The cost
-- computation refuses instead: cached tokens with no cached price make the
-- whole cost NULL (unknown), never an estimate.

ALTER TABLE model_profile
    ADD COLUMN IF NOT EXISTS vendor_cached_input_per_1m_usd NUMERIC(12, 4);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'model_profile_cached_positive'
    ) THEN
        ALTER TABLE model_profile ADD CONSTRAINT model_profile_cached_positive
            CHECK (
                vendor_cached_input_per_1m_usd IS NULL
                OR vendor_cached_input_per_1m_usd >= 0
            );
    END IF;
END $$;

COMMENT ON COLUMN model_profile.vendor_cached_input_per_1m_usd IS
    'USD per million CACHED input tokens the vendor charges us - the '
    'discounted cache-read rate, not the write surcharge. NULL means nobody '
    'has told us, and the cost computation then refuses to cost a '
    'cache-hitting call rather than estimate.';
