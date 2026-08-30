-- 017 — the credit itself gets a price the owner can set. H-42's mechanism.
--
-- 🔴 **What this is.** Customers BUY credits (in rupees) and calls BURN
-- credits (per the tier card, D67). Until now the rupee side lived nowhere:
-- launch_surface.md §4 sells "Rs 500/user/month + AI credits" and never says
-- what a credit costs, so the console's margins ran on numbers each operator
-- typed by hand — two operators, two different margins on the same screen.
-- This table is the single saved answer: what one credit SELLS for, plus the
-- INR-per-USD planning rate the console converts vendor bills with.
--
-- 🔴 **Billing NEVER reads this table.** A call bills credits; the tier card
-- owns how many. The fence is test_customer_console_credit_price.py::
-- test_billing_never_reads_the_credit_price — the same call bills the same
-- credits with this table empty or full (R7).
--
-- ⚠️ **INSERT, never UPDATE** — the tier_rate_card discipline: a past sale
-- stays readable against the price that sold it. The newest row whose date
-- has passed rules. Future-dating a row is "new price from the 1st".
--
-- ⚠️ **Seeds NOTHING.** The number is the owner's commercial act (H-42).
-- Building the mechanism prices nothing.

CREATE TABLE IF NOT EXISTS credit_price (
    effective_from  TIMESTAMPTZ NOT NULL PRIMARY KEY DEFAULT now(),
    inr_per_credit  NUMERIC(14,6) NOT NULL,
    -- A PLANNING rate for margin arithmetic, not a live FX feed. Saved so
    -- every operator reads the SAME margins; refresh it when it matters.
    usd_to_inr      NUMERIC(14,6) NOT NULL,
    CONSTRAINT credit_price_sane CHECK (
        inr_per_credit > 0 AND inr_per_credit <= 100000
        AND usd_to_inr > 0 AND usd_to_inr <= 100000
    )
);
