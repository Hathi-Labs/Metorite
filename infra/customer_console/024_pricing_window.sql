-- 024 — the vendor rate depends on WHEN a call ran and HOW BIG it was.
--
-- Spec: `project-docs/specs/credit_pricing.md` section 4.1 (slice 2).
--
-- ⚠️ **Do not write a bare percent sign in this file.** The test ladder applies
-- migrations through psycopg, which reads one as a placeholder and fails the
-- whole file. Migration 023 learned that the expensive way.
--
-- 🔴 **One model can carry FOUR different prices for the same token.** DeepSeek
-- charges an off-peak rate for eight hours a day and a peak rate otherwise.
-- OpenAI charges roughly double for input past a context threshold. Today
-- `model_profile` holds one number per token kind, so a call that ran cheap and
-- a call that ran dear are recorded as costing the same.
--
-- ⚠️ **The existing columns keep their names and hold the PEAK rate.** They
-- already do, by accident: `vendor_price_feed` carries DeepSeek's peak numbers
-- (0.44 and 1.32, measured 2026-09-04) because the feed has no window
-- dimension. Renaming them to say `peak` would be a rename in place, and R6
-- forbids that on a ladder that cannot roll back.
--
-- 📌 **Two different questions, and this migration keeps them apart.**
--
--   * *What did this call COST us?* The answer is the rate for the window it
--     actually ran in. Recording peak for a call we paid off-peak overstates
--     cost and understates margin, which defeats the margin monitor that
--     section 4.3 exists to build.
--   * *What should we CHARGE for this tier?* The answer derives from the PEAK
--     rate always (owner directive, 2026-09-04), because a price that assumes
--     the cheap window breaks the moment traffic moves to the dear one. The
--     specification measured that: Fast at 30 credits earns 66.3 percent at
--     off-peak cost and 32.7 percent at peak, against its own 45 percent floor.
--
-- ⚠️ **The customer's charge is untouched by all of this.** D67 keys the charge
-- on the TIER, so a window change moves our cost and never their price. That is
-- the same property a failover has, and it is the point of D67.
--
-- R6: every column is additive and nullable. Old code meets this schema
-- mid-deploy and keeps inserting. NULL in an off-peak column means the model
-- has one rate all day, which is true of every model but DeepSeek's two.

-- ── What we pay, off-peak ──────────────────────────────────────────────────
ALTER TABLE model_profile
    ADD COLUMN IF NOT EXISTS vendor_input_offpeak_per_1m_usd NUMERIC(12, 4),
    ADD COLUMN IF NOT EXISTS vendor_output_offpeak_per_1m_usd NUMERIC(12, 4),
    ADD COLUMN IF NOT EXISTS vendor_cached_input_offpeak_per_1m_usd NUMERIC(12, 4);

-- ── WHEN off-peak runs, in UTC ─────────────────────────────────────────────
--
-- ⚠️ A TIME range rather than a boolean, because the question a metering call
-- asks is "which window was 14:07 UTC in", and a boolean cannot answer it.
-- Both NULL means this model has no off-peak window at all.
--
-- ⚠️ The range MAY wrap midnight, and DeepSeek's does (16:30 to 00:30 UTC).
-- The reader must handle start > end. `pricing_window.py` does, and its test
-- names the wrap case.
ALTER TABLE model_profile
    ADD COLUMN IF NOT EXISTS offpeak_start_utc TIME,
    ADD COLUMN IF NOT EXISTS offpeak_end_utc TIME;

-- ── What we pay above the long-context threshold ───────────────────────────
--
-- 🔴 **Without this a large document under-bills by half.** OpenAI charges
-- roughly double for input past about 272000 tokens. The threshold is per
-- model, because vendors do not agree on it.
ALTER TABLE model_profile
    ADD COLUMN IF NOT EXISTS context_tier_threshold INTEGER,
    ADD COLUMN IF NOT EXISTS vendor_input_long_per_1m_usd NUMERIC(12, 4),
    ADD COLUMN IF NOT EXISTS vendor_output_long_per_1m_usd NUMERIC(12, 4),
    ADD COLUMN IF NOT EXISTS vendor_cached_input_long_per_1m_usd NUMERIC(12, 4);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'model_profile_window_positive'
    ) THEN
        ALTER TABLE model_profile ADD CONSTRAINT model_profile_window_positive
            CHECK (
                (vendor_input_offpeak_per_1m_usd IS NULL
                 OR vendor_input_offpeak_per_1m_usd >= 0)
                AND (vendor_output_offpeak_per_1m_usd IS NULL
                     OR vendor_output_offpeak_per_1m_usd >= 0)
                AND (vendor_cached_input_offpeak_per_1m_usd IS NULL
                     OR vendor_cached_input_offpeak_per_1m_usd >= 0)
                AND (vendor_input_long_per_1m_usd IS NULL
                     OR vendor_input_long_per_1m_usd >= 0)
                AND (vendor_output_long_per_1m_usd IS NULL
                     OR vendor_output_long_per_1m_usd >= 0)
                AND (vendor_cached_input_long_per_1m_usd IS NULL
                     OR vendor_cached_input_long_per_1m_usd >= 0)
                AND (context_tier_threshold IS NULL
                     OR context_tier_threshold > 0)
            );
    END IF;

    -- ⚠️ A half-configured window is worse than none: one bound set means the
    -- reader cannot tell whether the operator meant all day or nothing.
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'model_profile_offpeak_range_complete'
    ) THEN
        ALTER TABLE model_profile
            ADD CONSTRAINT model_profile_offpeak_range_complete
            CHECK (
                (offpeak_start_utc IS NULL AND offpeak_end_utc IS NULL)
                OR (offpeak_start_utc IS NOT NULL AND offpeak_end_utc IS NOT NULL)
            );
    END IF;
END $$;

-- ── What the meter recorded about this one call ────────────────────────────
ALTER TABLE usage_event
    ADD COLUMN IF NOT EXISTS window_at_call TEXT,
    ADD COLUMN IF NOT EXISTS context_tier TEXT;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'usage_event_window_known'
    ) THEN
        ALTER TABLE usage_event ADD CONSTRAINT usage_event_window_known
            CHECK (
                window_at_call IS NULL
                OR window_at_call IN ('peak', 'offpeak')
            );
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'usage_event_context_tier_known'
    ) THEN
        ALTER TABLE usage_event ADD CONSTRAINT usage_event_context_tier_known
            CHECK (
                context_tier IS NULL
                OR context_tier IN ('short', 'long')
            );
    END IF;
END $$;

COMMENT ON COLUMN model_profile.offpeak_start_utc IS
    'When this vendor''s off-peak window opens, in UTC. NULL in both bounds '
    'means one rate all day. The range may wrap midnight, and DeepSeek''s '
    'does, so a reader must handle start greater than end.';

COMMENT ON COLUMN model_profile.context_tier_threshold IS
    'Prompt tokens above which the long-context rates apply. Per model, '
    'because vendors do not agree on the number. NULL means one rate at every '
    'size.';

COMMENT ON COLUMN usage_event.window_at_call IS
    'Which vendor window this call ran in, resolved from its START time in '
    'UTC. A call that crosses the boundary bills at the window it started in. '
    'Recorded so provider_cost_usd can be explained a year later.';

COMMENT ON COLUMN usage_event.context_tier IS
    'Whether this call was priced at the short or the long context rate, '
    'resolved from the tokens the provider actually reported and never from a '
    'pre-flight estimate.';
