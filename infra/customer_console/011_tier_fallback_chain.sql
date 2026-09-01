-- 011 — a tier points at an ORDERED CHAIN of models, not one model.
--
-- 🔴 **The failure this exists to end.** Every tier holds exactly one model
-- today. When that model's provider is overloaded, rate limited or simply
-- down, every customer on that tier gets an error — and we own a live key for
-- three other vendors that could have answered. The Router has nowhere to go
-- because the table has nowhere to put a second choice.
--
-- ⚠️ **A CHAIN IS VERSIONED WHOLE, and that is the design decision.** The
-- alternative — one `effective_from` per step — was rejected because it makes
-- REMOVAL impossible under the insert-only rule (§6A.5): you cannot delete a
-- step, so dropping one would need a tombstone row with a NULL model, and then
-- every reader has to know about tombstones. Versioning the whole chain at one
-- timestamp makes removal "insert the chain you want", history stays exactly
-- reconstructable, and no reader learns a new concept.
--
-- Resolution therefore becomes: find the newest `effective_from` that has
-- passed for this (task, tier), then take EVERY row at that timestamp, ordered
-- by rank. `router.resolve_chain` is that query and `router.resolve_tier` is
-- its first element.
--
-- ⚠️ **R6, and the expand window is genuinely safe here.** The column lands
-- with `DEFAULT 1`, so every existing row becomes a one-step chain and old
-- code reading it is still correct. The one hazard is old code meeting a
-- MULTI-step chain: `ORDER BY effective_from DESC LIMIT 1` would pick an
-- arbitrary step. It cannot happen — a multi-step chain can only be written
-- through the API that ships in this same release, so no such row exists while
-- old code is running. `resolve_tier` gains `rank ASC` in this release anyway,
-- which makes the pick deterministic even if that reasoning is ever wrong.

ALTER TABLE tier_binding
    ADD COLUMN IF NOT EXISTS rank INTEGER NOT NULL DEFAULT 1;

COMMENT ON COLUMN tier_binding.rank IS
    'Try order within one chain. 1 is the primary. The Router walks up from '
    '1 when a step fails, so a gap in the numbering costs nothing and a '
    'duplicate is refused by the primary key.';

-- ⚠️ Rank 0 and negative ranks are refused. Nothing reads a rank as an index,
-- but "the first one" and "rank 1" must mean the same thing to a person
-- reading a row, and an off-by-one here is invisible until an outage.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'tier_binding_rank_positive'
    ) THEN
        ALTER TABLE tier_binding
            ADD CONSTRAINT tier_binding_rank_positive CHECK (rank >= 1);
    END IF;
END $$;

-- The primary key WIDENS to admit a chain.
--
-- ⚠️ Widening a key never rejects an existing row — every row carries rank 1
-- today, so uniqueness on (task, tier, effective_from) still holds under the
-- wider key. This is a loosening, not the tightening R6 defers to a later
-- release.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'tier_binding_pkey'
          AND conrelid = 'tier_binding'::regclass
          AND array_length(conkey, 1) = 3
    ) THEN
        ALTER TABLE tier_binding DROP CONSTRAINT tier_binding_pkey;
        ALTER TABLE tier_binding
            ADD CONSTRAINT tier_binding_pkey
            PRIMARY KEY (task, tier, effective_from, rank);
    END IF;
END $$;

-- The resolution index. Resolution asks for one (task, tier) at a time and
-- wants the newest date first, so the index carries the sort rather than
-- making the planner do it on every AI request.
CREATE INDEX IF NOT EXISTS tier_binding_resolve_idx
    ON tier_binding (task, tier, effective_from DESC, rank);

COMMENT ON TABLE tier_binding IS
    'The tier -> model map, centrally. Customers never see a model (D32.7); '
    'agents name a tier and this table decides what actually runs, so swapping '
    'a provider is one row here rather than a deploy to every customer box. '
    'A tier holds an ORDERED CHAIN as of 011: the whole chain shares one '
    'effective_from and the Router tries rank 1, then 2, then 3.';
