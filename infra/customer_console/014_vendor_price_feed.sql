-- 014 — the vendor feed: what the world's models ARE, from upstream.
--
-- 🔴 **The problem this solves: every fact on /models was typed by hand.**
-- `model_profile` (012) deliberately seeds nothing, because a hardcoded table
-- of vendor prices "starts lying the first time a vendor ships a new model".
-- Correct — and the consequence was that the operator hand-copies windows and
-- prices out of eleven vendors' HTML pricing pages, which is the same mirror
-- with a slower clock. The owner's directive (2026-08-30): facts flow from
-- upstream, and the operator clicks instead of typing.
--
-- ⚠️ **The source is litellm's price map, and that choice is structural, not
-- convenient.** No vendor publishes a machine-readable price list — their
-- prices are HTML. litellm's `model_prices_and_context_window.json` is the
-- community-maintained aggregation of all of them, updated near-daily, and it
-- is keyed on the EXACT provider ids this system already routes on: the
-- Router resolves a vendor as `model.split("/", 1)[0]` and litellm is the one
-- gateway (CP-4). A feed keyed on any other vocabulary would need a mapping
-- table, and mapping tables drift.
--
-- ⚠️ **This table is a CACHE of upstream claims, never billing truth.**
-- Billing cost still reads `model_profile`, which only an explicit staff
-- write changes. The feed's job is to make that write a one-click copy
-- instead of a transcription. `usage_event.provider_cost_usd` snapshots at
-- call time as before. Nothing in the serving path reads this table.
--
-- ⚠️ **Rows are upserted, never deleted.** A model litellm drops (or renames)
-- keeps its last-known row with a stale `synced_at` — deleting it would strip
-- the prefill facts out from under a model an operator already declared.
-- `deprecated_on` carries the vendor's own retirement date when litellm
-- records one.
--
-- NUMERIC(14,6), wider than model_profile's (12,4): upstream per-token prices
-- like $0.0000000375 land at $0.0375/1M — four decimals holds that, but six
-- keeps a cheaper future model from rounding to zero, and a cache is the
-- wrong place to lose precision. The profile quantizes on copy.

CREATE TABLE IF NOT EXISTS vendor_price_feed (
    -- Vendor-qualified, always: `deepseek/deepseek-chat`, `openai/gpt-4o`.
    -- litellm's raw keys are inconsistent (some carry the prefix, some do
    -- not); the parser normalises so this column speaks the Router's grammar.
    model             TEXT PRIMARY KEY,
    provider          TEXT NOT NULL,

    -- litellm's word for what the model does, verbatim: 'chat', 'embedding',
    -- 'audio_transcription', … Kept raw so an unmapped mode still lands.
    mode              TEXT NOT NULL,

    -- Our words, mapped by `feed.py` at parse time. NULL means "litellm has a
    -- mode we do not serve yet" — the row still carries prices, but the
    -- console offers no one-click declare for it.
    task              TEXT,
    invocation        TEXT,

    -- NULL means litellm does not know, and it is never zero (012's rule).
    context_window    INTEGER,
    max_output        INTEGER,

    -- What the VENDOR charges, per million tokens, per litellm's record.
    -- Zero is legal HERE (a genuinely free model is a fact upstream claims);
    -- the operator sees "$0.00" on copy and decides.
    vendor_input_per_1m_usd        NUMERIC(14, 6),
    vendor_output_per_1m_usd       NUMERIC(14, 6),
    vendor_cached_input_per_1m_usd NUMERIC(14, 6),

    reads_images      BOOLEAN NOT NULL DEFAULT FALSE,
    thinks_first      BOOLEAN NOT NULL DEFAULT FALSE,

    -- The vendor's own retirement date, where litellm records one.
    deprecated_on     DATE,

    synced_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'vendor_price_feed_sane'
    ) THEN
        ALTER TABLE vendor_price_feed ADD CONSTRAINT vendor_price_feed_sane CHECK (
            (context_window IS NULL OR context_window > 0)
            AND (max_output IS NULL OR max_output > 0)
            AND (vendor_input_per_1m_usd IS NULL OR vendor_input_per_1m_usd >= 0)
            AND (vendor_output_per_1m_usd IS NULL OR vendor_output_per_1m_usd >= 0)
            AND (vendor_cached_input_per_1m_usd IS NULL
                 OR vendor_cached_input_per_1m_usd >= 0)
        );
    END IF;
END $$;

-- The console lists "available from your vendors" by provider; without this
-- the query walks every row of a ~3000-model table per page load.
CREATE INDEX IF NOT EXISTS vendor_price_feed_provider_idx
    ON vendor_price_feed (provider);

COMMENT ON TABLE vendor_price_feed IS
    'Upstream model facts (litellm price map): what each model IS and what '
    'its vendor charges. A CACHE of upstream claims for one-click declare '
    'and drift warnings - never read by billing, which snapshots '
    'model_profile at call time.';

COMMENT ON COLUMN vendor_price_feed.task IS
    'Our task slug, mapped from litellm mode at parse time. NULL = a mode '
    'we do not serve; the row still informs, it just cannot one-click '
    'declare.';


-- ── The sync ledger ─────────────────────────────────────────────────────────
--
-- ⚠️ Evidence, not decoration (non-negotiable 8: verify by evidence, never by
-- a green job). "The feed is current" must be provable from a row that says
-- WHEN, from WHERE, and HOW MANY — a console that merely believes it synced
-- is how four deploys once reported success while shipping nothing.

CREATE TABLE IF NOT EXISTS feed_sync_log (
    id            BIGSERIAL PRIMARY KEY,
    -- 'github' when the live feed answered; 'packaged:litellm' when the
    -- fallback (the JSON bundled inside the installed litellm) served. The
    -- fallback is real data with an older clock, and the console says which.
    source        TEXT NOT NULL,
    models_seen   INTEGER NOT NULL,
    rows_upserted INTEGER NOT NULL,
    started_at    TIMESTAMPTZ NOT NULL,
    finished_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE feed_sync_log IS
    'One row per feed sync: when, from which source, how many models. The '
    'evidence the console cites for "prices last fetched on <date>".';
