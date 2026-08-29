-- 012 — what a model IS, as opposed to what it is used for.
--
-- 🔴 **The operator console shows a dash for every fact worth choosing on.**
-- `/models` lists what we can call and whether it is priced. It cannot say how
-- big the window is, whether the model reads an image, or what the vendor
-- charges us — because no column holds any of it. So the page that exists to
-- help somebody pick a model tells them almost nothing about the models.
--
-- ⚠️ **Keyed on MODEL, not on (model, task).** `model_capability` is keyed on
-- the pair, and a context window is a property of the model alone. Putting it
-- there would give a model declared for both `chat` and `image` two copies of
-- its window, free to disagree — and then the page has to pick one.
--
-- 🔴 **This table is UPDATED in place, and that is a deliberate break from
-- `tier_binding` and `model_rate_card`.** Those are insert-only because a past
-- invoice must stay readable against the commercial decision that produced it.
-- A context window is not a decision, it is a fact about the world, and the
-- audit trail owes nobody a history of what Anthropic's documentation said last
-- month.
--
-- ⚠️ **`vendor_*_per_1m_usd` is what the VENDOR charges US.** It is NOT
-- `model_rate_card`, which is what we charge a customer. These two numbers are
-- the most confusable pair in the system after `provider_credential` and
-- `llm_api_key`, and reading one as the other inverts a margin — so the column
-- names carry `vendor` and the unit, and the console labels the field "we pay".
--
-- ⚠️ **This is REFERENCE data, not a billing input.** Margin is computed from
-- `usage_event.provider_cost_usd`, which is recorded per call at the time of
-- the call. Nothing here is read to price anything, which is the other reason
-- it does not need effective dating.
--
-- ⚠️ **Nothing is seeded.** A table of hardcoded context windows and prices is
-- a mirror of eleven vendors' documentation, and it starts lying the first time
-- one of them ships a new model. An empty row renders as "—", which is true.

CREATE TABLE IF NOT EXISTS model_profile (
    model             TEXT PRIMARY KEY,

    -- A human name. NULL means "use the id", which is what the console does.
    label             TEXT,

    -- ⚠️ NULL means NOBODY HAS TOLD US, and it must never be read as zero.
    -- "0 tokens" describes a broken model; a missing window describes a
    -- missing row. The console renders NULL as an em dash for this reason.
    context_window    INTEGER,
    max_output        INTEGER,

    vendor_input_per_1m_usd  NUMERIC(12, 4),
    vendor_output_per_1m_usd NUMERIC(12, 4),

    -- One line: what this model is FOR, in an operator's words. Empty rather
    -- than NULL, because every caller would coalesce it anyway.
    description       TEXT NOT NULL DEFAULT '',

    -- ⚠️ The two capabilities that are NOT tasks. `chat`, `transcribe` and the
    -- rest live in `model_capability` because a tier binds them. Reading an
    -- image and thinking first are properties OF a chat model, and D-AI-2 turns
    -- on exactly the first one: the image tier follows the chat model when that
    -- model can read an image.
    reads_images      BOOLEAN NOT NULL DEFAULT FALSE,
    thinks_first      BOOLEAN NOT NULL DEFAULT FALSE,

    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE model_profile IS
    'What a model IS - window, output cap, what the VENDOR charges us, and '
    'whether it reads images. Keyed on model alone, because these are '
    'properties of the model and not of a (model, task) pair. UPDATED in '
    'place: a context window is a fact about the world, not a commercial '
    'decision that owes an audit trail.';

COMMENT ON COLUMN model_profile.vendor_input_per_1m_usd IS
    'USD per million input tokens that the VENDOR charges US. NOT '
    'model_rate_card, which is what we charge a customer. Reading one as the '
    'other inverts a margin.';

COMMENT ON COLUMN model_profile.context_window IS
    'Tokens. NULL means nobody has told us, and it is never zero.';

-- ⚠️ A window or a price of zero is a data-entry slip, not a real model. Zero
-- reads as "free" on the console and as "broken" in a comparison, and both are
-- wrong in a way nobody would question. NULL is how you say "unknown".
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'model_profile_positive'
    ) THEN
        ALTER TABLE model_profile ADD CONSTRAINT model_profile_positive CHECK (
            (context_window IS NULL OR context_window > 0)
            AND (max_output IS NULL OR max_output > 0)
            AND (vendor_input_per_1m_usd IS NULL OR vendor_input_per_1m_usd >= 0)
            AND (vendor_output_per_1m_usd IS NULL OR vendor_output_per_1m_usd >= 0)
        );
    END IF;
END $$;
