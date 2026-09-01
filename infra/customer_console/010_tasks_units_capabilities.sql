-- 010 — tasks, units and capabilities (WS-31 CP-10 slice 2)
--
-- Spec: `project-docs/specs/customer_console.md` §6A.9 order steps 1 and 2 ·
-- D60 · D61 (G-3, G-4, G-5) · D19.2.
--
-- 🔴 THE HOLE THIS CLOSES IS LIVE TODAY. `credits.rate_call` is tokens-only:
-- it divides three token counters by 1000 against per-1k rates and knows no
-- other unit. So `tier-stt` — which ships in the production seed — CANNOT be
-- priced, and neither can `speak` or `image`. Three of six tasks.
--
-- R6. Every column added here is either NULLABLE or carries a DEFAULT that
-- reproduces today's behaviour, so old code meets this schema unharmed:
--   * `task` defaults to 'chat', which is what every existing row IS.
--   * `unit` defaults to 'tokens', which is what every existing price IS.
--   * `usage_event`'s three new columns are NULLABLE, per §6A.9 in terms.
--
-- The ladder replays. Every statement is guarded, because `pr-check.yml`
-- applies this three times in a row and asserts the second and third are
-- no-ops (H-25).


-- ── The task allowlist (G-5) ────────────────────────────────────────────────
--
-- **Tasks are an allowlist. Tiers stay free text.** The asymmetry is
-- deliberate (D61, G-5): a tier is a NAME we sell and may grow at will, while
-- a task decides which litellm verb runs. An unknown task is a bug, and a
-- typo that silently created one would send an image request to a chat model.

CREATE TABLE IF NOT EXISTS task_catalog (
    slug         TEXT PRIMARY KEY,
    label        TEXT NOT NULL,
    -- The unit this task is priced in, by nature rather than by choice.
    -- Recorded here so an operator cannot price `transcribe` per 1k tokens.
    natural_unit TEXT NOT NULL,
    sort_order   INTEGER NOT NULL DEFAULT 100
);

COMMENT ON TABLE task_catalog IS
    'The six things a model can be asked to do. An allowlist, not free text '
    '(D61 G-5): a task decides which provider verb runs, so a typo would '
    'route an image request to a chat model.';

INSERT INTO task_catalog (slug, label, natural_unit, sort_order) VALUES
    ('chat',       'Chat completion',   'tokens',     10),
    ('embed',      'Embeddings',        'tokens',     20),
    ('vision',     'Image understanding', 'tokens',   30),
    ('transcribe', 'Speech to text',    'minutes',    40),
    ('speak',      'Text to speech',    'characters', 50),
    ('image',      'Image generation',  'images',     60)
ON CONFLICT (slug) DO NOTHING;


-- ── What each model CAN do, and which verb does it ──────────────────────────
--
-- ⚠️ Capability is not availability (§6A.9 rule 3). This table says what a
-- model can do. `tier_binding` says what we USE it for. The operator's most
-- valuable view is the GAP between them.
--
-- Today this knowledge is `_STT_TIER_IDS: frozenset({"stt"})` in
-- `acb_llm/client.py`, so `tier-image` would be handed to `acompletion` and
-- rejected by the provider (D60.2).

CREATE TABLE IF NOT EXISTS model_capability (
    model      TEXT NOT NULL,
    task       TEXT NOT NULL REFERENCES task_catalog(slug),
    -- The litellm verb: 'acompletion', 'atranscription', 'aspeech',
    -- 'aembedding', 'aimage_generation'. Data, not a frozenset.
    invocation TEXT NOT NULL,
    -- Per (model, task), not per model (§6A.9 rule 4). Only chat and speak
    -- stream, and only on models that support it.
    streams    BOOLEAN NOT NULL DEFAULT FALSE,
    PRIMARY KEY (model, task)
);

COMMENT ON TABLE model_capability IS
    'What each model can do, and the provider verb for each task. Replaces '
    'the _STT_TIER_IDS frozenset (D60.2). Capability is not availability: '
    'tier_binding decides what we actually use.';


-- ── tier_binding gains a task ───────────────────────────────────────────────
--
-- Resolution becomes two steps (D60): (task, tier) -> model, then
-- (model, task) -> invocation. A multimodal model then needs no special case.

ALTER TABLE tier_binding
    ADD COLUMN IF NOT EXISTS task TEXT NOT NULL DEFAULT 'chat';

DO $$
BEGIN
    -- The FK is added separately so the DEFAULT above lands first and every
    -- existing row is already valid.
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'tier_binding_task_fk'
    ) THEN
        ALTER TABLE tier_binding
            ADD CONSTRAINT tier_binding_task_fk
            FOREIGN KEY (task) REFERENCES task_catalog(slug);
    END IF;

    -- Widen the primary key to (task, tier, effective_from). Without this a
    -- second row for the same tier under a different task collides, so the
    -- table could hold `chat` bindings only.
    IF EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'tier_binding_pkey'
          AND (SELECT count(*) FROM unnest(conkey)) = 2
    ) THEN
        ALTER TABLE tier_binding DROP CONSTRAINT tier_binding_pkey;
        ALTER TABLE tier_binding
            ADD CONSTRAINT tier_binding_pkey
            PRIMARY KEY (task, tier, effective_from);
    END IF;
END $$;


-- ── model_rate_card gains a task, a unit and a pricing mode ─────────────────
--
-- G-4 (D61): `pricing_mode` ∈ {unpriced, absorbed, priced}. **A zero cannot
-- carry three meanings.** Today a zero means "nobody has priced this yet",
-- and D19.2 also wants embeddings ABSORBED into the seat price — which is
-- also zero, and must not read as an operational mistake. The third is a
-- deliberate price of zero. Three states, three names.

ALTER TABLE model_rate_card
    ADD COLUMN IF NOT EXISTS task TEXT NOT NULL DEFAULT 'chat';
ALTER TABLE model_rate_card
    ADD COLUMN IF NOT EXISTS unit TEXT NOT NULL DEFAULT 'tokens';
-- Used when `unit` is not 'tokens'. The three per-1k columns stay, because
-- chat is the common case and they are load-bearing (§6A.9 in terms).
ALTER TABLE model_rate_card
    ADD COLUMN IF NOT EXISTS credits_per_unit NUMERIC(14, 6) NOT NULL DEFAULT 0;
ALTER TABLE model_rate_card
    ADD COLUMN IF NOT EXISTS pricing_mode TEXT NOT NULL DEFAULT 'unpriced';

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'model_rate_card_task_fk'
    ) THEN
        ALTER TABLE model_rate_card
            ADD CONSTRAINT model_rate_card_task_fk
            FOREIGN KEY (task) REFERENCES task_catalog(slug);
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'model_rate_card_pricing_mode_known'
    ) THEN
        ALTER TABLE model_rate_card
            ADD CONSTRAINT model_rate_card_pricing_mode_known
            CHECK (pricing_mode IN ('unpriced', 'absorbed', 'priced'))
            NOT VALID;
        ALTER TABLE model_rate_card
            VALIDATE CONSTRAINT model_rate_card_pricing_mode_known;
    END IF;

    IF EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'model_rate_card_pkey'
          AND (SELECT count(*) FROM unnest(conkey)) = 2
    ) THEN
        ALTER TABLE model_rate_card DROP CONSTRAINT model_rate_card_pkey;
        ALTER TABLE model_rate_card
            ADD CONSTRAINT model_rate_card_pkey
            PRIMARY KEY (model, task, effective_from);
    END IF;
END $$;


-- ── usage_event records what was actually consumed ──────────────────────────
--
-- NULLABLE, deliberately and per §6A.9. Every row written before this
-- migration was a chat completion measured in tokens, and back-filling a
-- guess would make a made-up number indistinguishable from a measured one.
-- NULL reads as "this row predates units", which is true.

ALTER TABLE usage_event ADD COLUMN IF NOT EXISTS task TEXT;
ALTER TABLE usage_event ADD COLUMN IF NOT EXISTS quantity NUMERIC(14, 4);
ALTER TABLE usage_event ADD COLUMN IF NOT EXISTS unit TEXT;

COMMENT ON COLUMN usage_event.quantity IS
    'What was consumed, in `unit`. NULL on rows written before CP-10 slice 2, '
    'which were all token-measured chat completions. Not back-filled: a '
    'guessed number must not look like a measured one.';


-- ── The seed keeps its meaning ──────────────────────────────────────────────
--
-- ⚠️ `test_the_rate_card_ships_unpriced` still binds. This migration builds
-- the mechanism to price correctly and prices NOTHING. Every seeded row is
-- 'unpriced' by the DEFAULT above, which is exactly what it was.
--
-- The one capability row that reflects what already ships: the STT tier is
-- live in the production seed and its verb is not `acompletion`. Recording it
-- is not a new decision — it is writing down `_STT_TIER_IDS`.

-- ⚠️ `tier-stt` is seeded HERE, not in 002, and the reason is measured rather
-- than reasoned about. 002 runs before this file creates the `task` column, so
-- any row it writes is tagged `chat` — and the ladder replays every deploy, so
-- 002 would re-create that wrong row after this file corrected it, once per
-- deploy, for ever. The first replay test caught exactly that.
--
-- The DELETE removes the historical row 002 wrote before this slice existed.
-- ⚠️ It is not the UPDATE path §6A.5 forbids. That rule bans a RUNTIME
-- re-point or re-price, because a mutable rate card destroys the audit trail
-- when a customer disputes a charge. This drops a binding nothing ever billed
-- against: `provider_credential` is empty, so no call has ever succeeded.

DELETE FROM tier_binding WHERE tier = 'tier-stt' AND task = 'chat';

INSERT INTO tier_binding (tier, model, task, effective_from)
SELECT 'tier-stt', 'groq/whisper-large-v3-turbo', 'transcribe',
       '2026-01-01T00:00:00Z'
WHERE NOT EXISTS (
    SELECT 1 FROM tier_binding WHERE tier = 'tier-stt' AND task = 'transcribe'
);

INSERT INTO model_capability (model, task, invocation, streams)
SELECT tb.model, 'transcribe', 'atranscription', FALSE
FROM tier_binding tb
WHERE tb.task = 'transcribe'
ON CONFLICT (model, task) DO NOTHING;

-- Every other seeded binding is a chat model, and chat streams.
INSERT INTO model_capability (model, task, invocation, streams)
SELECT DISTINCT tb.model, 'chat', 'acompletion', TRUE
FROM tier_binding tb
WHERE tb.task = 'chat'
ON CONFLICT (model, task) DO NOTHING;
