-- 019 — the per-unit vendor costs (H-78, customer_console.md §6A.11a).
--
-- 🔴 **The gap this closes: an image, transcribe or speak job has no cost
-- source.** `014` stores per-MILLION-TOKEN prices and nothing else, so the
-- "Price from cost" panel reads a dash for every non-chat job and the
-- operator types the vendor's dollar price by hand. Hand-typed vendor prices
-- go stale silently, which is the defect `014` itself exists to remove.
--
-- ⚠️ **The two tables carry the same price in DIFFERENT units, on purpose.**
-- `vendor_price_feed` is a cache of upstream claims, so it stores the
-- vendor's own number in the vendor's own unit, unconverted. litellm prices
-- transcription per SECOND. `task_catalog` (010) prices `transcribe` per
-- MINUTE, so `model_profile` stores per minute. The x60 conversion happens
-- ONCE, server-side, at the declare-and-prefill seam. Nothing else
-- multiplies by 60.
--
-- ⚠️ **NUMERIC(18, 10), wider than either table's token columns.** A
-- per-million-token price is a dollar-scale number. A raw per-unit price is
-- much smaller: `input_cost_per_character` measures 0.000015 on OpenAI
-- text-to-speech today. Ten decimal places keep a cheaper future model from
-- rounding to zero.
--
-- Three rules bind every column below. NULL means litellm does not know, and
-- NULL never means zero. Zero is legal, because a free model is a real
-- thing. A negative is refused by the CHECK.
--
-- R6: every column is nullable and additive. Nothing is renamed and nothing
-- is dropped. The two CHECK constraints are re-created rather than edited in
-- place, because Postgres has no `ALTER CONSTRAINT` for a CHECK body and
-- `012`/`014` are shipped ladder files that stay byte-untouched.
--
-- Fences (R7): tests/unit/test_customer_console_vendor_feed.py and
-- tests/unit/test_customer_console_model_profile.py, both R8.


-- ── The feed: the vendor's own unit ─────────────────────────────────────────

ALTER TABLE vendor_price_feed
    ADD COLUMN IF NOT EXISTS vendor_per_second_usd    NUMERIC(18, 10),
    ADD COLUMN IF NOT EXISTS vendor_per_character_usd NUMERIC(18, 10),
    ADD COLUMN IF NOT EXISTS vendor_per_image_usd     NUMERIC(18, 10);

COMMENT ON COLUMN vendor_price_feed.vendor_per_second_usd IS
    'USD per second of audio, verbatim from litellm input_cost_per_second. '
    'The OUTPUT per-second field is deliberately ignored: whisper-1 sets both '
    'to 0.0001 and a sum charges twice.';

COMMENT ON COLUMN vendor_price_feed.vendor_per_character_usd IS
    'USD per character of input text, verbatim from litellm '
    'input_cost_per_character.';

COMMENT ON COLUMN vendor_price_feed.vendor_per_image_usd IS
    'USD per generated image, from litellm output_cost_per_image and then '
    'input_cost_per_image. A pixel-priced or token-priced entry stays NULL: '
    'a pixel price needs an image size, and the feed holds no size.';

-- Re-created with one `>= 0` clause per new column. `014` adds this
-- constraint only when it is absent, so replaying the ladder over this file
-- leaves the wider body in place.
ALTER TABLE vendor_price_feed DROP CONSTRAINT IF EXISTS vendor_price_feed_sane;
ALTER TABLE vendor_price_feed ADD CONSTRAINT vendor_price_feed_sane CHECK (
    (context_window IS NULL OR context_window > 0)
    AND (max_output IS NULL OR max_output > 0)
    AND (vendor_input_per_1m_usd IS NULL OR vendor_input_per_1m_usd >= 0)
    AND (vendor_output_per_1m_usd IS NULL OR vendor_output_per_1m_usd >= 0)
    AND (vendor_cached_input_per_1m_usd IS NULL
         OR vendor_cached_input_per_1m_usd >= 0)
    AND (vendor_per_second_usd IS NULL OR vendor_per_second_usd >= 0)
    AND (vendor_per_character_usd IS NULL OR vendor_per_character_usd >= 0)
    AND (vendor_per_image_usd IS NULL OR vendor_per_image_usd >= 0)
);


-- ── The profile: the task's natural unit ────────────────────────────────────

ALTER TABLE model_profile
    ADD COLUMN IF NOT EXISTS vendor_per_minute_usd    NUMERIC(18, 10),
    ADD COLUMN IF NOT EXISTS vendor_per_character_usd NUMERIC(18, 10),
    ADD COLUMN IF NOT EXISTS vendor_per_image_usd     NUMERIC(18, 10);

COMMENT ON COLUMN model_profile.vendor_per_minute_usd IS
    'USD per MINUTE of audio that the vendor charges us, for a transcribe '
    'model. task_catalog prices transcribe in minutes and litellm prices it '
    'in seconds, so the copy from vendor_price_feed multiplies by 60 once.';

COMMENT ON COLUMN model_profile.vendor_per_character_usd IS
    'USD per character of input text that the vendor charges us, for a speak '
    'model. Same unit as the feed column, so the copy converts nothing.';

COMMENT ON COLUMN model_profile.vendor_per_image_usd IS
    'USD per generated image that the vendor charges us. Same unit as the '
    'feed column, so the copy converts nothing.';

-- NULL keeps the meaning `012` gave it: nobody has told us.
ALTER TABLE model_profile DROP CONSTRAINT IF EXISTS model_profile_positive;
ALTER TABLE model_profile ADD CONSTRAINT model_profile_positive CHECK (
    (context_window IS NULL OR context_window > 0)
    AND (max_output IS NULL OR max_output > 0)
    AND (vendor_input_per_1m_usd IS NULL OR vendor_input_per_1m_usd >= 0)
    AND (vendor_output_per_1m_usd IS NULL OR vendor_output_per_1m_usd >= 0)
    AND (vendor_per_minute_usd IS NULL OR vendor_per_minute_usd >= 0)
    AND (vendor_per_character_usd IS NULL OR vendor_per_character_usd >= 0)
    AND (vendor_per_image_usd IS NULL OR vendor_per_image_usd >= 0)
);
