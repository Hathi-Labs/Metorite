-- 015 — the customer's price is keyed on the TIER, and the tier slate is
-- the whole product surface. Owner decision D67, 2026-08-30.
--
-- 🔴 **Why the key moved.** `model_rate_card` priced (model, task), so a
-- customer's price was whatever the SERVING model cost: a failover changed
-- what they paid mid-day, and two tiers sharing one model could not differ
-- in price — no premium tier without a premium model. But Metorite sells
-- TIERS: the tier is the product, the model is supply. One price per
-- (tier, task) is stable across failovers (a fallback moves OUR cost, never
-- their price) and margin reads per product, not per engine.
--
-- ⚠️ **Nothing has ever been billed** — every model card ships `unpriced`
-- and the spend gate is off — so the key can move without a single invoice
-- to reconcile. That is why this lands NOW rather than after go-live.
--
-- ⚠️ **`model_rate_card` STAYS ON DISK** (R6 expand/contract: never drop in
-- the release that stops writing). The metering path stops reading it, the
-- write endpoint answers 410 and points here, and its rows remain readable
-- history.

-- ── The tier registry ───────────────────────────────────────────────────────
--
-- Tiers used to exist only as strings on `tier_binding` rows, so an EMPTY
-- tier could not exist at all — and the owner's directive (2026-08-30) is
-- the opposite: every capability we intend to sell shows on the board now,
-- empty, and fills up over time. A registry row is what lets "tier-video"
-- exist before any video model can be called.

CREATE TABLE IF NOT EXISTS tier_catalog (
    slug        TEXT PRIMARY KEY,
    label       TEXT NOT NULL,
    -- Customer words: what picking this tier means. Shown in their app.
    blurb       TEXT NOT NULL DEFAULT '',
    sort_order  INTEGER NOT NULL DEFAULT 100
);

COMMENT ON TABLE tier_catalog IS
    'The tiers we sell - the product surface. A row here is what lets a '
    'tier exist EMPTY (bound to nothing, priced later). tier_binding stays '
    'free of an FK on purpose: a binding for an unregistered tier still '
    'serves, and the console shows it as a ghost rather than hiding it.';

-- The slate. Three chat bands, one band per capability, and the two
-- capabilities nothing can serve yet (video, music) — the board shows the
-- whole intended product, and empty slots are honest about being empty.
INSERT INTO tier_catalog (slug, label, blurb, sort_order) VALUES
    ('tier-fast',     'Fast',           'Quick answers at the lowest price.',                 10),
    ('tier-balanced', 'Balanced',       'The everyday setting - good answers, fair price.',   20),
    ('tier-powerful', 'Powerful',       'The strongest models, for hard problems.',           30),
    ('tier-code',     'Code',           'Tuned for writing and fixing software.',             40),
    ('tier-vision',   'Vision',         'Reads and understands images.',                      50),
    ('tier-image',    'Image',          'Makes images from a description.',                   60),
    ('tier-stt',      'Speech to text', 'Turns audio into text.',                             70),
    ('tier-tts',      'Text to speech', 'Reads text aloud.',                                  80),
    ('tier-embed',    'Search index',   'Builds the vectors behind search.',                  90),
    ('tier-video',    'Video',          'Makes video from a description.',                   100),
    ('tier-music',    'Music',          'Makes music and sound.',                            110)
ON CONFLICT (slug) DO NOTHING;

-- ── The two tasks the slate needs and the catalog lacks ─────────────────────
--
-- `video` and `music` are sold per second of output — the industry's own
-- unit. ⚠️ No `KNOWN_INVOCATIONS` verb exists for either yet: a capability
-- for a video model is REFUSED until the Router grows the call, and that is
-- deliberate — the tier, the task and the price can all exist first, and
-- the day a verb lands nothing else has to change.

INSERT INTO task_catalog (slug, label, natural_unit, sort_order) VALUES
    ('video', 'Video generation', 'seconds', 70),
    ('music', 'Music generation', 'seconds', 80)
ON CONFLICT (slug) DO NOTHING;

-- ── The tier rate card ──────────────────────────────────────────────────────
--
-- Same shape, columns and rules as `model_rate_card` (010), re-keyed.
-- INSERT-only with `effective_from`, never UPDATE: a re-price is a new row
-- with a later date, so a past invoice stays readable against the card that
-- produced it.

CREATE TABLE IF NOT EXISTS tier_rate_card (
    tier             TEXT NOT NULL REFERENCES tier_catalog(slug),
    task             TEXT NOT NULL REFERENCES task_catalog(slug),
    unit             TEXT NOT NULL DEFAULT 'tokens',
    input_credits_per_1k        NUMERIC(14, 6) NOT NULL DEFAULT 0,
    output_credits_per_1k       NUMERIC(14, 6) NOT NULL DEFAULT 0,
    cached_input_credits_per_1k NUMERIC(14, 6) NOT NULL DEFAULT 0,
    -- For every unit that is not 'tokens': credits per one `unit`.
    credits_per_unit NUMERIC(14, 6) NOT NULL DEFAULT 0,
    -- G-4: a zero cannot carry three meanings.
    pricing_mode     TEXT NOT NULL DEFAULT 'unpriced'
        CHECK (pricing_mode IN ('unpriced', 'absorbed', 'priced')),
    effective_from   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tier, task, effective_from)
);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'tier_rate_card_nonneg'
    ) THEN
        ALTER TABLE tier_rate_card ADD CONSTRAINT tier_rate_card_nonneg CHECK (
            input_credits_per_1k >= 0
            AND output_credits_per_1k >= 0
            AND cached_input_credits_per_1k >= 0
            AND credits_per_unit >= 0
        );
    END IF;
END $$;

COMMENT ON TABLE tier_rate_card IS
    'What a CUSTOMER pays, per (tier, task) - D67. The tier is the product: '
    'a failover changes our cost, never their price. INSERT-only, versioned '
    'by effective_from. Nothing is seeded: every pair ships unpriced, and '
    'pricing one is the owner''s commercial act (H-42).';

COMMENT ON COLUMN tier_rate_card.pricing_mode IS
    'unpriced = nobody decided (bills zero, loudly). absorbed = deliberately '
    'covered by the seat price (D19.2). priced = the rates bind.';
