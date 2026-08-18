-- ============================================================================
-- 002_seed_catalog.sql — the sellable things, as DATA
-- ============================================================================
-- Source of record: work_plan.md D23 (Center packages are the sales object) and
-- D24 (the customer-framing round that set the final numbers), restated in
-- saas_multitenancy.md §2.4b. **Do not re-derive prices from anywhere else and
-- do not re-litigate them here** — if a number below disagrees with D23/D24,
-- D23/D24 win and this file is the bug.
--
-- Ladder of record: 600 · 1,200 · 1,800 · 2,400 · 3,000 (INR per user per month).
--
-- Seeded with ON CONFLICT DO NOTHING so replaying the ladder never overwrites a
-- price an operator has since changed in the database. A price is DATA: it
-- moves by an operator write or a later migration, never by an edit to this
-- file being re-run.
--
-- ⚠️ Two packages below name Centers that Metorite does not yet register
-- (`rnd`, `support`). That is deliberate and visible: D22 put both on the Center
-- roster of record, while `lib/centers.ts` + `EXPECTED_CENTER_SLUGS` still list
-- six. The catalog is allowed to be ahead of the product — but they are seeded
-- INACTIVE so nothing can sell a package whose Center does not exist. Flip
-- `active` in the same change that registers the Center
-- (department_centers.md §2's registration checklist).
-- ============================================================================

INSERT INTO plan_catalog (slug, name, kind, price_inr, active, sort_order) VALUES
    -- The mandatory base. Membership IS the Core seat (D19.3) — joining an
    -- organization consumes one, which is why it is a plan row and not a
    -- special case in code.
    ('core',        'Core',                  'core',   600.00, TRUE,  10),

    -- App-bearing Center packages, ₹600/user/month. Each bundles the Center's
    -- modules PLUS its slice of Projects, Knowledge Base and Dashboards.
    ('personal',    'Personal Center',       'center', 600.00, TRUE,  20),
    ('sales',       'Sales Center',          'center', 600.00, TRUE,  21),
    ('marketing',   'Marketing Center',      'center', 600.00, TRUE,  22),
    ('finance',     'Finance Center',        'center', 600.00, TRUE,  23),
    ('support',     'Support Center',        'center', 600.00, FALSE, 24),

    -- Slices-only Centers, ₹300/user/month — pitched as "team workspace"
    -- (D24), i.e. the cross-cutting apps scoped to that team with no unique
    -- apps of their own yet.
    ('operations',  'Operations Center',     'center', 300.00, TRUE,  30),
    ('people',      'People Center',         'center', 300.00, TRUE,  31),
    ('rnd',         'R&D Center',            'center', 300.00, FALSE, 32),

    -- The Company Center is the leadership rollup and is NOT sold — it is free
    -- for leadership (D23). Present in the catalog at zero so the seat grid can
    -- render it uniformly instead of special-casing its absence.
    ('company',     'Company Center',        'center',   0.00, TRUE,  33),

    -- Org-wide add-ons.
    ('builder',     'App & Agent Builder',   'addon',  500.00, TRUE,  40),
    ('workflows',   'Workflows',             'addon',  300.00, TRUE,  41),

    -- Bundles. The all-Centers seat is the multi-hat relief: every Center
    -- package, no add-ons (D24.3, priced down from ₹3,600 because the
    -- ₹1,800 seat made it dominated).
    ('all_centers', 'All Centers',           'bundle', 1800.00, TRUE, 50),
    -- Complete keeps D20.5's promise: all-GA, price-protected, wildcard.
    ('complete',    'Complete',              'bundle', 3000.00, TRUE, 51)
ON CONFLICT (slug) DO NOTHING;


-- ── Tier bindings: the model access rework, as data (D32.7) ─────────────────
--
-- Mirrors today's infra/litellm/config.yaml so the Router starts life
-- behaviourally identical to the gateway it takes over from — CP-4 ships as a
-- pass-through precisely so this can be proven rather than assumed. After
-- that, a provider swap is an INSERT here with a later effective_from, and no
-- customer deployment changes at all.
INSERT INTO tier_binding (tier, model, effective_from) VALUES
    ('tier-fast',     'deepseek/deepseek-chat',      '2026-01-01T00:00:00Z'),
    ('tier-balanced', 'deepseek/deepseek-v4-pro',    '2026-01-01T00:00:00Z'),
    ('tier-powerful', 'deepseek/deepseek-v4-pro',    '2026-01-01T00:00:00Z'),
    ('tier-stt',      'groq/whisper-large-v3-turbo', '2026-01-01T00:00:00Z')
ON CONFLICT (tier, effective_from) DO NOTHING;


-- ── Rate card ───────────────────────────────────────────────────────────────
--
-- ⚠️ **THESE NUMBERS ARE PLACEHOLDERS AND MUST NOT BE BILLED AGAINST.**
--
-- D19.2 fixes the RULE — a credit is the ₹10 unit, each call draws fractionally
-- at provider cost × 2, the card is denominated natively in INR with no runtime
-- FX — but the actual per-model input is provider pricing, which changes under
-- us and which nobody should transcribe from memory into a billing table.
--
-- The sequencing exists for this reason: CP-4 ships the Router as an UNPRICED
-- pass-through so a month of real per-org burn lands in usage_event first, and
-- CP-6 sets this card against measurement. A rate card set on estimates is a
-- rate card you change on customers who have already seen it.
--
-- Seeded at zero rather than at a guess: a zero here bills nothing and is
-- obviously wrong, where a plausible-looking guess bills confidently and is
-- quietly wrong. `test_customer_console_credits.py` pins that a zero card is
-- treated as UNPRICED (raises) rather than as free, and
-- `test_customer_console_sql.py` pins that THESE seeded rows are still zero —
-- scoped to this effective_from, since CP-6's suites insert fixture-priced
-- cards at a later date to exercise rating without touching the seed.
INSERT INTO model_rate_card
    (model, input_credits_per_1k, output_credits_per_1k,
     cached_input_credits_per_1k, effective_from) VALUES
    ('deepseek/deepseek-chat',      0, 0, 0, '2026-01-01T00:00:00Z'),
    ('deepseek/deepseek-v4-pro',    0, 0, 0, '2026-01-01T00:00:00Z'),
    ('groq/whisper-large-v3-turbo', 0, 0, 0, '2026-01-01T00:00:00Z')
ON CONFLICT (model, effective_from) DO NOTHING;
