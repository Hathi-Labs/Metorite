-- 030 — the per-thousand columns go. Contract half.
--
-- Spec: `project-docs/specs/credit_pricing.md` section 4.2 (slice 10).
--
-- ⚠️ **Do not write a bare percent sign in this file.** Migration 022 learned
-- that the expensive way.
--
-- 🔴 **This is RELEASE TWO, and it is the only DESTRUCTIVE migration in the
-- credit-pricing stack.** Migration 025 added the per-million columns and
-- backfilled them. The code has written both and read per-million since. This
-- drops what nothing reads any more.
--
-- 📌 **We cannot roll back** (CLAUDE.md §3.4). Restoring these columns means
-- restoring the database, so the check that matters is whether anything still
-- SELECTs them. Measured 2026-09-05, and the answer is no:
--
--     resolve_tier_rate          reads per-million only
--     the /catalog/models wire   sends per-million only
--     POST /catalog/tier-rates   writes per-million only
--
-- ⚠️ **`model_rate_card` KEEPS its own per-thousand columns and this migration
-- must not touch them.** D67.2 retired that table as a billing input and kept
-- it so a past invoice still reads back. `router.py`'s `resolve_rate_card`
-- still selects them, one function above the tier read that no longer does.
-- The two are a single screen apart, which is exactly how a careless contract
-- would take the wrong one.
--
-- 📌 **The ordinary R6 hazard does not apply here, and the reason is a fact
-- about this deployment rather than a rule.** The deploy applies migrations
-- BEFORE it restarts services, so a DROP normally leaves the old process
-- meeting a schema without its column until the restart lands. That window
-- would matter if anything were billing. Nothing is: the rate card ships
-- unpriced, `CUSTOMER_CONSOLE_SPEND_GATE` ships off, and the owner confirmed
-- on 2026-09-05 that no customer is on the product yet. Recorded so the
-- exemption reads as a decision somebody took, and not as a rule nobody knew.
--
-- Fences (R7): `tests/unit/test_customer_console_tier_pricing.py` asserts the
-- columns are gone and that a price still round-trips on either request scale.

ALTER TABLE tier_rate_card
    DROP COLUMN IF EXISTS input_credits_per_1k,
    DROP COLUMN IF EXISTS output_credits_per_1k,
    DROP COLUMN IF EXISTS cached_input_credits_per_1k;

-- 🔴 **THE NAME IS LOAD-BEARING, and getting it wrong breaks every future
-- deploy.** Postgres drops a CHECK along with any column it names, so the
-- three DROPs above also removed migration 015's `tier_rate_card_nonneg`.
--
-- 015 re-adds that constraint under an `IF NOT EXISTS ... conname =
-- 'tier_rate_card_nonneg'` guard, and its predicate names the per-thousand
-- columns. **The deploy replays the WHOLE ladder on every run** — the log
-- says "Customer Console ladder applied (30 files)". So on the next deploy
-- 015 would find its constraint absent, try to re-create it over columns that
-- no longer exist, and fail the migration step for everybody.
--
-- Measured 2026-09-05: the suite went from 155 passing to **876 errors** on
-- exactly this, because every fixture replays the ladder.
--
-- ⚠️ So this re-creates the constraint under **015's own name**, over the
-- columns that survive. 015's guard then finds it and does nothing, which is
-- what a replayed migration is supposed to do.
--
-- ⚠️ The per-million rates are already guarded by `tier_rate_card_per_1m_nonneg`
-- (025). This predicate deliberately covers only `credits_per_unit`, the one
-- clause 015 held that nothing else does — two constraints over one fact is
-- how they drift apart.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'tier_rate_card_nonneg'
    ) THEN
        ALTER TABLE tier_rate_card ADD CONSTRAINT tier_rate_card_nonneg
            CHECK (credits_per_unit >= 0);
    END IF;
END $$;

COMMENT ON TABLE tier_rate_card IS
    'What a CUSTOMER pays per (tier, task), in credits per MILLION tokens or '
    'per natural unit. D67 keys the price on the tier, so a failover moves our '
    'cost and never their bill. INSERT-only: a re-price is a new row at a new '
    'effective_from, never an edit, so a past invoice stays readable against '
    'the card that produced it.';
