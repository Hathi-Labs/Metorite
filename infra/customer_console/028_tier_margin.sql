-- 028 — the margin we intend per tier, and the floor that alarms.
--
-- Spec: `project-docs/specs/credit_pricing.md` section 4.3 (slice 7).
--
-- ⚠️ **Do not write a bare percent sign in this file.** Migration 022 learned
-- that the expensive way.
--
-- 🔴 **One margin knob cannot price eleven tiers.** The console defaults every
-- suggestion to 70 percent today, and that number is wrong at both ends of the
-- slate. A cheap tier absorbs a fat multiplier invisibly — nobody perceives 17
-- credits against 30. An expensive tier cannot, because the absolute number is
-- what sells it, and the design document's own table asks for 2.5 on Fast and
-- 1.4 on Powerful.
--
-- 🔴 **THIS TABLE SHIPS EMPTY, and that is the whole design.** Every number in
-- it is a commercial decision and H-42 owns all of them. An absent row means
-- the tier has no suggestion, exactly as `tier_rate_card` ships unpriced and
-- `test_the_rate_card_ships_unpriced` refuses a priced ladder. An agent builds
-- the mechanism. The owner sets the figures.
--
-- ⚠️ **Two different numbers, and confusing them inverts an alarm.**
--
--   `margin_multiplier`  what we MULTIPLY cost by to suggest a price. M in the
--                        design document. 2.5 means charge 2.5 times cost.
--   `margin_floor`       the realised margin BELOW WHICH somebody should look.
--                        A fraction, so 0.45 is 45 percent.
--
-- The first is an intention and the second is an alarm threshold. A tier can
-- sit above its floor while its multiplier is wrong, and below its floor while
-- its multiplier is right — the floor is measured against what actually
-- happened, and traffic moves.
--
-- ⚠️ **INSERT-only, keyed on `effective_from`**, the same shape as every other
-- rate table here. A margin change is a commercial decision, and a past
-- suggestion must stay readable against the numbers that produced it.
--
-- R6: a new table. Nothing reads it until the code that does ships, and an
-- empty table answers "no suggestion" rather than an error.
--
-- Fences (R7): `tests/unit/test_customer_console_tier_margin.py`.

CREATE TABLE IF NOT EXISTS tier_margin (
    tier              TEXT NOT NULL REFERENCES tier_catalog(slug),
    -- What we multiply raw cost by. NULL means "no suggestion for this tier",
    -- which is different from 1.0 meaning "sell at cost".
    margin_multiplier NUMERIC(6, 3),
    -- The realised margin below which the monitor alarms, as a fraction.
    margin_floor      NUMERIC(4, 3),
    effective_from    TIMESTAMPTZ NOT NULL DEFAULT now(),

    PRIMARY KEY (tier, effective_from),

    CONSTRAINT tier_margin_sane CHECK (
        -- ⚠️ A multiplier below 1 sells below cost. Refused here rather than
        -- left to a reviewer: the console would render it as a suggestion and
        -- an operator would have no reason to doubt it.
        (margin_multiplier IS NULL OR margin_multiplier >= 1)
        -- A floor is a fraction. 45 would read as 4500 percent and never alarm.
        AND (margin_floor IS NULL OR (margin_floor >= 0 AND margin_floor < 1))
    )
);

COMMENT ON TABLE tier_margin IS
    'What margin we intend per tier, and the floor that alarms. SHIPS EMPTY: '
    'every number here is a commercial decision and H-42 owns all of them. An '
    'absent row means the tier has no suggestion, exactly as tier_rate_card '
    'ships unpriced.';

COMMENT ON COLUMN tier_margin.margin_multiplier IS
    'What we multiply raw cost by to SUGGEST a price. An intention. NULL means '
    'no suggestion, which is different from 1.0 meaning sell at cost.';

COMMENT ON COLUMN tier_margin.margin_floor IS
    'The realised margin below which somebody should look, as a FRACTION — '
    '0.45 is 45 percent. An alarm threshold measured against what actually '
    'happened, not an intention. A tier can sit above its floor with a wrong '
    'multiplier, and below it with a right one, because traffic moves.';
