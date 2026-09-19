-- 031 — WHERE the cost number came from. Measured, or derived by us.
--
-- Spec: `project-docs/specs/credit_pricing.md` section 4.1 · closes half of H-85.
--
-- ⚠️ **Do not write a bare percent sign in this file.** Migration 023 learned
-- that the expensive way: the test ladder applies migrations through psycopg,
-- which reads one as a placeholder and fails the whole file.
--
-- 🔴 **`provider_cost_usd` has never said how it was arrived at.** Every row
-- carries a number and no provenance. Two very different facts share that
-- column today:
--
--   * the vendor CHARGED us this, and told us so in the response, or
--   * we MULTIPLIED our own recorded price by the tokens we counted.
--
-- The first is a measurement. The second is an estimate, and it is only as
-- good as the last time somebody updated `model_profile`. A margin report that
-- mixes them reads as one kind of fact, and it is two.
--
-- 📌 **Why this matters more now than it did.** Until this release every row
-- was the second kind, so the column meant one thing consistently. OpenRouter
-- reports `usage.cost` — what it actually billed us — so rows of the first
-- kind start arriving. Without this column nobody downstream could tell a
-- measured margin from a guessed one, and the guessed one is the one that
-- silently rots.
--
-- ⚠️ **NULL is the honest answer and stays legal.** A call we could not cost
-- at all (no recorded price, unreadable usage) writes NULL in
-- `provider_cost_usd` and NULL here. D-AI-7 rule 3: unknown is never zero, and
-- it is never a guess wearing a label either.
--
-- ⚠️ **Additive only, per R6.** The column is nullable with no default and no
-- backfill. Old code that does not know the column keeps writing rows, and
-- those rows read NULL — which is correct, because old code cannot tell us
-- which kind its number was. A backfill to 'computed' would be a claim about
-- history nobody measured. We are not making it.

ALTER TABLE usage_event
    ADD COLUMN IF NOT EXISTS cost_source text;

-- The two words, and no third. A vocabulary that grows by typo is how a
-- report starts under-counting: 'vendor' and 'Vendor' would split one bucket
-- into two and nothing would say so.
--
-- 'vendor'   — the provider reported this cost for this call. Authoritative.
-- 'computed' — we derived it from `model_profile` and the tokens we counted.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'usage_event_cost_source_known'
    ) THEN
        ALTER TABLE usage_event
            ADD CONSTRAINT usage_event_cost_source_known
            CHECK (cost_source IS NULL OR cost_source = ANY (ARRAY['vendor'::text, 'computed'::text]));
    END IF;
END
$$;

-- Reading "show me every measured call for this org, newest first" is the
-- margin monitor's whole query. Partial, because the rows that carry a source
-- are the only ones it ever wants and a full index would pay for the NULLs.
CREATE INDEX IF NOT EXISTS usage_event_cost_source_idx
    ON usage_event (organization_id, created_at DESC)
    WHERE cost_source IS NOT NULL;

COMMENT ON COLUMN usage_event.cost_source IS
    'How provider_cost_usd was arrived at: vendor (the provider reported it) '
    'or computed (we multiplied model_profile by counted tokens). NULL means '
    'the call was not costed at all.';
