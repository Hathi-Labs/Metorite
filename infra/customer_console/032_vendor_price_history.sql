-- 032 — what the vendor charged us, and WHEN it changed.
--
-- Spec: `project-docs/specs/credit_pricing.md` section 4.1.
--
-- ⚠️ **Do not write a bare percent sign in this file.** Migration 023 learned
-- that the expensive way: the test ladder applies migrations through psycopg,
-- which reads one as a placeholder and fails the whole file.
--
-- 🔴 **We kept history for the half we control and dropped it for the half we
-- do not.** `tier_margin` is keyed `(tier, effective_from)` and `tier_rate_card`
-- versions the same way, so every decision WE make is dated and recoverable.
-- `vendor_price_feed` is keyed on `model` alone with a single `synced_at`, so
-- every sync overwrites the last one. `feed_sync_log` records counts and not
-- prices. The result: nobody can answer "what did this model cost us in
-- August", which is the first question any margin argument asks.
--
-- 📌 **A CHANGE LOG, not a snapshot log.** A full copy of the feed on every
-- sync would be 2700 rows a day to record a table that mostly does not move.
-- This writes a row only when a price is actually DIFFERENT, so the table's
-- size tracks how often vendors reprice rather than how often we sync.
--
-- 🔴 **A TRIGGER, and that is the point.** The feed upserts in one bulk
-- statement, and any history written from Python would have to be remembered
-- by every future writer of that table. The database cannot forget. This is
-- the R7 fence for this rule, and `test_vendor_price_history.py` is its test.
--
-- ⚠️ **Additive only, per R6.** A new table and a trigger on an existing one.
-- No column changes, no backfill. History starts the day this ships, because
-- inventing rows for prices nobody observed would be worse than having none.

CREATE TABLE IF NOT EXISTS vendor_price_history (
    id                             bigserial PRIMARY KEY,
    model                          text NOT NULL,
    provider                       text NOT NULL,
    observed_at                    timestamptz NOT NULL DEFAULT now(),

    -- The same six prices `vendor_price_feed` carries, at the same scales.
    -- Deliberately a COPY and not a foreign key: this row must still read
    -- correctly after the feed forgets the model entirely.
    vendor_input_per_1m_usd        numeric(14,6),
    vendor_output_per_1m_usd       numeric(14,6),
    vendor_cached_input_per_1m_usd numeric(14,6),
    vendor_per_second_usd          numeric(18,10),
    vendor_per_character_usd       numeric(18,10),
    vendor_per_image_usd           numeric(18,10),

    -- 'first'  — the first time we ever saw a price for this model.
    -- 'change' — a price moved from what we last recorded.
    --
    -- Two words and no third, for the reason migration 031 gives: a vocabulary
    -- that grows by typo splits one bucket into two silently.
    reason                         text NOT NULL,
    CONSTRAINT vendor_price_history_reason_known
        CHECK (reason = ANY (ARRAY['first'::text, 'change'::text]))
);

-- "Show me this model's price over time" is the only read this table has.
CREATE INDEX IF NOT EXISTS vendor_price_history_model_idx
    ON vendor_price_history (model, observed_at DESC);

-- "What repriced this week, across every vendor" — the drift digest.
CREATE INDEX IF NOT EXISTS vendor_price_history_observed_idx
    ON vendor_price_history (observed_at DESC);

CREATE OR REPLACE FUNCTION vendor_price_history_record() RETURNS trigger AS $fn$
BEGIN
    -- ⚠️ `IS DISTINCT FROM`, never `<>`. Half these columns are NULL for any
    -- given model, and `NULL <> NULL` is NULL, which is not true — so a plain
    -- comparison would record nothing when a price appeared out of NULL, and
    -- that is exactly the first move worth recording.
    IF TG_OP = 'INSERT' THEN
        INSERT INTO vendor_price_history (
            model, provider,
            vendor_input_per_1m_usd, vendor_output_per_1m_usd,
            vendor_cached_input_per_1m_usd,
            vendor_per_second_usd, vendor_per_character_usd,
            vendor_per_image_usd, reason
        ) VALUES (
            NEW.model, NEW.provider,
            NEW.vendor_input_per_1m_usd, NEW.vendor_output_per_1m_usd,
            NEW.vendor_cached_input_per_1m_usd,
            NEW.vendor_per_second_usd, NEW.vendor_per_character_usd,
            NEW.vendor_per_image_usd, 'first'
        );
        RETURN NEW;
    END IF;

    IF NEW.vendor_input_per_1m_usd IS DISTINCT FROM OLD.vendor_input_per_1m_usd
        OR NEW.vendor_output_per_1m_usd IS DISTINCT FROM OLD.vendor_output_per_1m_usd
        OR NEW.vendor_cached_input_per_1m_usd
            IS DISTINCT FROM OLD.vendor_cached_input_per_1m_usd
        OR NEW.vendor_per_second_usd IS DISTINCT FROM OLD.vendor_per_second_usd
        OR NEW.vendor_per_character_usd IS DISTINCT FROM OLD.vendor_per_character_usd
        OR NEW.vendor_per_image_usd IS DISTINCT FROM OLD.vendor_per_image_usd
    THEN
        INSERT INTO vendor_price_history (
            model, provider,
            vendor_input_per_1m_usd, vendor_output_per_1m_usd,
            vendor_cached_input_per_1m_usd,
            vendor_per_second_usd, vendor_per_character_usd,
            vendor_per_image_usd, reason
        ) VALUES (
            NEW.model, NEW.provider,
            NEW.vendor_input_per_1m_usd, NEW.vendor_output_per_1m_usd,
            NEW.vendor_cached_input_per_1m_usd,
            NEW.vendor_per_second_usd, NEW.vendor_per_character_usd,
            NEW.vendor_per_image_usd, 'change'
        );
    END IF;

    RETURN NEW;
END;
$fn$ LANGUAGE plpgsql;

-- ⚠️ DROP then CREATE, because `CREATE TRIGGER IF NOT EXISTS` does not exist
-- in Postgres 16. The pair is idempotent, which is what H-25's three-times
-- ladder replay requires.
DROP TRIGGER IF EXISTS vendor_price_feed_history ON vendor_price_feed;
CREATE TRIGGER vendor_price_feed_history
    AFTER INSERT OR UPDATE ON vendor_price_feed
    FOR EACH ROW EXECUTE FUNCTION vendor_price_history_record();

COMMENT ON TABLE vendor_price_history IS
    'Append-only log of vendor price changes. One row the first time a model '
    'is seen, and one more each time a price actually moves. Written by a '
    'trigger on vendor_price_feed, never by application code.';
