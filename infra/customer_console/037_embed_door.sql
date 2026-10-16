-- 037 — the embedding door's three rows. CP-EMB, `customer_console.md` §6A.16.
--
-- 🔴 **The owner's call, 2026-10-16.** Production had no working vector:
-- the gateway's own `/v1/embeddings` needs an OpenAI key the box does not
-- hold. So `tier-embed` serves `text-embedding-3-small` on the AI/ML API
-- account that Jev already uses (CP-13h, credential provider `aimlapi`).
-- §6A.10c clause 4 keeps the choice of a resold model for the owner, and the
-- owner made it. That is why this file seeds a BINDING, where 033 did not.
--
-- Three rows, each guarded:
--
-- 1. `tier_binding` (`tier-embed`, `embed`) -> `aimlapi/text-embedding-3-small`.
--    Written only while NO row exists for the pair. A later re-bind or a
--    tombstone (H-178) is a row, so a replay never undoes an operator act.
-- 2. `model_capability` for that model and `embed`, invocation `aembedding`.
--    Written only while the seeded binding is IN FORCE, the rule 010 uses
--    for `tier-stt`. A removal is refused while a binding is in force, so a
--    replay can never revive a model the operator removed.
-- 3. `tier_rate_card` (`tier-embed`, `embed`), `absorbed`, every rate zero.
--    D19.2 absorbs embeddings into the seat price, and G-4 (D61) says a zero
--    must say WHY. `absorbed` is not a price: `rate_call` answers zero for it
--    and draws no credit. Written only while NO card exists for the pair, so
--    a later owner price is never overwritten.
--
-- ⚠️ No `provider_credential` row. The `aimlapi` key is already installed on
-- the Console for Jev (§6.0 B1). A secret never lives in a migration.
--
-- R6: expand only. Three INSERTs. Rename nothing. Drop nothing.
-- R1: 036 is the highest number on disk at build time. Check again at merge.
--
-- Fence: tests/unit/test_customer_console_embed.py::TestTheSeed.

INSERT INTO tier_binding (tier, model, task, effective_from)
SELECT 'tier-embed', 'aimlapi/text-embedding-3-small', 'embed', now()
WHERE NOT EXISTS (
    SELECT 1 FROM tier_binding WHERE tier = 'tier-embed' AND task = 'embed'
);

INSERT INTO model_capability (model, task, invocation, streams)
SELECT tb.model, 'embed', 'aembedding', FALSE
FROM tier_binding tb
WHERE tb.tier = 'tier-embed'
  AND tb.task = 'embed'
  AND tb.model = 'aimlapi/text-embedding-3-small'
  AND tb.effective_from = (
      SELECT max(x.effective_from) FROM tier_binding x
      WHERE x.tier = tb.tier AND x.task = tb.task AND x.effective_from <= now())
ON CONFLICT (model, task) DO NOTHING;

INSERT INTO tier_rate_card (
    tier, task, unit, credits_per_unit,
    input_credits_per_1m, output_credits_per_1m, cached_input_credits_per_1m,
    pricing_mode, effective_from)
SELECT 'tier-embed', 'embed', 'tokens', 0, 0, 0, 0, 'absorbed', now()
WHERE NOT EXISTS (
    SELECT 1 FROM tier_rate_card WHERE tier = 'tier-embed' AND task = 'embed'
);
