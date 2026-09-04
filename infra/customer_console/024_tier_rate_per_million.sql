-- 024 — the customer's card moves to credits per MILLION tokens. Expand half.
--
-- Spec: `project-docs/specs/credit_pricing.md` section 4.2 (slice 3).
-- Owner directive, 2026-09-04: keep it per million, because that is what every
-- vendor quotes now.
--
-- ⚠️ **Do not write a bare percent sign in this file.** Migration 022 learned
-- that the expensive way: the test ladder applies migrations through psycopg,
-- which reads one as a placeholder and fails the whole file.
--
-- 🔴 **This is RELEASE ONE of two, and running only this one is correct.**
-- R6 forbids a rename in place on a ladder that cannot roll back. So:
--
--   * this release ADDS the per-million columns and fills them,
--   * the code writes BOTH sets and reads the per-million set,
--   * a LATER release drops the per-thousand columns.
--
-- Old code that meets this schema mid-deploy still reads
-- `input_credits_per_1k` and still finds its number there. New code that meets
-- the OLD schema — the other order, which happens when a rollout is uneven —
-- finds the per-million column absent and falls back to the per-thousand one
-- times 1000. Both directions work, which is the whole point of an expand.
--
-- ⚠️ **The backfill multiplies by exactly 1000 and nothing else.** These are
-- the SAME price written at a different scale, not a repricing. A customer's
-- bill must not move by a rupee because we changed a unit, and
-- `test_customer_console_tier_pricing.py` asserts the equality directly.
--
-- 📌 **`tier_rate_card` is INSERT-only, and this UPDATE does not break that.**
-- The rule exists so a past invoice stays readable against the row that
-- produced it. Writing the same price into a second column changes no price
-- and rewrites no history — it states the existing number in a second unit.
-- A row's `effective_from` is untouched, so what was in force when, still is.
--
-- R6: additive and nullable. Fences (R7):
-- `tests/unit/test_customer_console_tier_pricing.py` (the equality, the
-- fallback and the dual write) and `tests/unit/test_customer_console_sql.py`.

ALTER TABLE tier_rate_card
    ADD COLUMN IF NOT EXISTS input_credits_per_1m NUMERIC(18, 6),
    ADD COLUMN IF NOT EXISTS output_credits_per_1m NUMERIC(18, 6),
    ADD COLUMN IF NOT EXISTS cached_input_credits_per_1m NUMERIC(18, 6);

-- ⚠️ NUMERIC(18, 6) and not (14, 6). A per-thousand rate of 34 becomes 34000
-- per million, and three more digits of headroom is the difference between a
-- price that stores and an overflow nobody predicted. The per-unit columns
-- keep their own scale, because an image is still priced per image.

-- The backfill. Idempotent by the NULL guard, so the ladder replays clean.
UPDATE tier_rate_card
   SET input_credits_per_1m = input_credits_per_1k * 1000
 WHERE input_credits_per_1m IS NULL;

UPDATE tier_rate_card
   SET output_credits_per_1m = output_credits_per_1k * 1000
 WHERE output_credits_per_1m IS NULL;

UPDATE tier_rate_card
   SET cached_input_credits_per_1m = cached_input_credits_per_1k * 1000
 WHERE cached_input_credits_per_1m IS NULL;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'tier_rate_card_per_1m_nonneg'
    ) THEN
        ALTER TABLE tier_rate_card ADD CONSTRAINT tier_rate_card_per_1m_nonneg
            CHECK (
                (input_credits_per_1m IS NULL OR input_credits_per_1m >= 0)
                AND (output_credits_per_1m IS NULL OR output_credits_per_1m >= 0)
                AND (cached_input_credits_per_1m IS NULL
                     OR cached_input_credits_per_1m >= 0)
            );
    END IF;
END $$;

COMMENT ON COLUMN tier_rate_card.input_credits_per_1m IS
    'What a customer pays per MILLION prompt tokens, in credits. The scale of '
    'record from release one of migration 024. The per-thousand column beside '
    'it holds the same price at the old scale and a later release drops it. '
    'Every vendor quotes per million, so the card now speaks the same unit as '
    'the cost it is derived from.';
