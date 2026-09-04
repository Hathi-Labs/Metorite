-- 022 — the meter admits when it could not meter.
--
-- Spec: `project-docs/specs/credit_pricing.md` §3 (slice 1).
--
-- 🔴 **Two vendor conventions report a cached token count, and the billing
-- code only understands one.** OpenAI-compatible providers report the cached
-- count INSIDE `prompt_tokens`. Anthropic-style providers report it BESIDE
-- them. `credits.py` subtracts, which is right for the first and wrong for the
-- second — and it used to clamp the result at zero, so the wrong answer was
-- silent.
--
-- Measured 2026-09-04 against `tier-balanced` rates: one real call billed
-- 251.60 credits read as a subset and 183.60 credits read as a sibling. That
-- is 27 percent less, with no error and no log line. `prompt=100
-- cached=99999` was accepted and billed 340 credits.
--
-- ⚠️ **Do not write a bare percent sign anywhere in this file.** The test
-- ladder applies migrations through psycopg, which reads one as a placeholder
-- and fails the whole file. The first draft of this comment did exactly that,
-- and R8 — a real database rather than a fake — is the only reason anybody
-- found out.
--
-- ⚠️ **`metering_fault` is NOT `refusal_reason`, and reusing that column would
-- have been a defect.** Migration 020 gives a slug to a CUSTOMER WALL, where
-- the call did not serve, and five reads in `store.py` then exclude such a row
-- from every call count. A partition failure is the opposite shape: the
-- customer received their completion and only our meter failed. Writing it to
-- `refusal_reason` would hide a served call from five counts and report a wall
-- that never happened.
--
-- ⚠️ **`cache_convention` records rather than repairs.** We refuse the
-- impossible case instead of re-normalising it, because a guess bills the
-- customer wrong in the other direction. This column is how the fleet gets
-- MEASURED before anybody changes the arithmetic.
--
-- R6: additive, nullable, no rename and no rewrite. Old code meets this schema
-- mid-deploy and keeps inserting. Every existing row reads NULL for both,
-- which is true of them — they all metered.
--
-- Fences (R7): `tests/unit/test_customer_console_credits.py` (the raise, the
-- served completion, the fault row) and `tests/unit/test_customer_console_sql.py`
-- (the CHECK, and that counting reads still count a faulted row).

ALTER TABLE usage_event
    ADD COLUMN IF NOT EXISTS metering_fault TEXT;

ALTER TABLE usage_event
    ADD COLUMN IF NOT EXISTS cache_convention TEXT;

-- ⚠️ Guarded `DO $$`, because `ALTER TABLE … ADD CONSTRAINT` has no
-- `IF NOT EXISTS` and this ladder replays on every start (013 and 020 both
-- make the same move).
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'usage_event_metering_fault_known'
    ) THEN
        ALTER TABLE usage_event ADD CONSTRAINT usage_event_metering_fault_known
            CHECK (
                metering_fault IS NULL
                OR metering_fault IN ('usage_partition')
            );
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'usage_event_cache_convention_known'
    ) THEN
        ALTER TABLE usage_event ADD CONSTRAINT usage_event_cache_convention_known
            CHECK (
                cache_convention IS NULL
                OR cache_convention IN ('subset', 'sibling')
            );
    END IF;
END $$;

COMMENT ON COLUMN usage_event.metering_fault IS
    'Why this call could not be metered, from a closed vocabulary. NULL means '
    'the meter worked. ⚠️ This is NOT refusal_reason: the call SERVED and the '
    'customer got their completion, so every read that COUNTS calls must keep '
    'counting this row. Only its billed_credits are zero.';

COMMENT ON COLUMN usage_event.cache_convention IS
    'Which vendor convention reported the cached token count. "subset" means '
    'it sat inside prompt_tokens (OpenAI-compatible). "sibling" means it sat '
    'beside them (Anthropic-style). NULL means no cached count arrived. '
    'Recorded so the fleet can be measured before anybody re-normalises the '
    'arithmetic on a guess.';
