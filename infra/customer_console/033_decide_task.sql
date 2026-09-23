-- 033 — the `decide` task, and the one hidden tier that serves it. D75.
--
-- Spec: `customer_console.md` §6A.14, CP-13a artefacts 1 and 2.
--
-- 🔴 **`decide` is a TASK and not a chat tier** (clause 1, D60.1). The
-- answer is a typed decision with a calibrated probability, and never free
-- text. So it is a different kind of job, and D60.6 keeps any degradation
-- inside the task. A `decide` call never falls back to a chat model.
--
-- **The unit is `tokens`** (clause 8). The vendor sells input tokens, and
-- output tokens are free. The Router writes the vendor's input count into
-- `usage_event.prompt_tokens`, so `_record_completion` reads a real prompt.
--
-- **`tier-decide` is hidden** (clause 2). The app picks the task, and a
-- member never picks a decision model. It joins the six hidden tiers that
-- 021 turned FALSE. The INSERT names the column, so the value lands on the
-- first write and needs no guarded UPDATE.
--
-- ⚠️ **This migration seeds NO `model_capability`, `model_profile`,
-- `tier_binding` or rate row.** Those are operator writes (CP-13b). A seeded
-- binding would serve the day the key goes in, and the operator must make
-- that choice. The `(tier-decide, decide)` rate stays unpriced until the
-- owner prices it (H-42). `test_the_rate_card_ships_unpriced` fails on any
-- seeded price.
--
-- R6: expand only. Two INSERT rows. Rename nothing. Drop nothing.
-- Idempotent: `ON CONFLICT (slug) DO NOTHING` on both rows, so a replay
-- changes nothing. An operator who later shows the tier keeps that choice.
-- R1: 032 is the highest number on disk at build time. Check again at merge.
--
-- Fence: tests/unit/test_customer_console_decide.py — the task is seeded in
-- tokens, and `tier-decide` reads `customer_visible` FALSE.

INSERT INTO task_catalog (slug, label, natural_unit, sort_order) VALUES
    ('decide', 'Decide', 'tokens', 90)
ON CONFLICT (slug) DO NOTHING;

INSERT INTO tier_catalog (slug, label, blurb, sort_order, task, customer_visible)
VALUES (
    'tier-decide',
    'Decide',
    'Fast typed decisions with a calibrated probability.',
    120,
    'decide',
    FALSE
)
ON CONFLICT (slug) DO NOTHING;
