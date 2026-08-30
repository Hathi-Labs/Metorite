-- 021 — one column decides which tiers a customer may see. D-AI-3.
--
-- Spec: `ai_metering_and_analytics.md` §3.1, §3.3 and §8.4 clause 1.
--
-- 🔴 **The problem this closes.** `tier_catalog` (015) holds the whole slate,
-- and the slate is not the picker. Eleven tiers ship. A customer picks a chat
-- band, a code band or an image band. The Router resolves the other six, and
-- an app names the task — `tier-stt` is speech to text, and nobody selects a
-- search index. A picker entry for one of those offers a choice no person can
-- act on.
--
-- **The test that separates the two values.** TRUE means a person chooses this
-- tier on purpose. FALSE means the Router or the app chooses it.
--
-- ⚠️ **The default is TRUE, and the six rows below turn FALSE.** A tier added
-- later shows up until an operator hides it. `tier-video` and `tier-music` are
-- FALSE because nothing binds them yet, and not because they are internal.
--
-- ⚠️ **The seed runs ONCE, and the guard is the point.** A bare `UPDATE` after
-- an `ADD COLUMN IF NOT EXISTS` would put the six rows back to FALSE on every
-- replay, so an operator who later shows `tier-video` would find the ladder
-- hiding it again at the next deploy. 016 solved the same problem with an
-- `IS NULL` predicate. A NOT NULL boolean holds no such sentinel, so the guard
-- is the column itself: the seed runs in the same block that creates it.
--
-- ⚠️ **The guard reads ONE schema, and `current_schema()` is which.** Every
-- other statement in this file resolves through the search path. A guard that
-- reads `information_schema.columns` across all schemas answers "the column
-- exists" for a `tier_catalog` in a schema the ALTER never touches. The block
-- then skips, and the unqualified `COMMENT ON COLUMN` below fails the deploy
-- under `ON_ERROR_STOP`. A diff review reproduced that with a probe schema.
--
-- R6: expand only. One column, with a default. Rename nothing. Drop nothing.
-- R1: 018 is the highest number on disk. 019 and 020 are claimed by sibling
-- branches (§3.1), so the merge re-checks this number.
--
-- Fence: tests/unit/test_customer_console_tier_pricing.py — the six seeds read
-- FALSE, and `GET /my/tiers` serves no hidden row.

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = current_schema()
          AND table_name = 'tier_catalog'
          AND column_name = 'customer_visible'
    ) THEN
        ALTER TABLE tier_catalog
            ADD COLUMN customer_visible BOOLEAN NOT NULL DEFAULT TRUE;

        -- The six §3.3 names. Every other slate row keeps the TRUE default.
        UPDATE tier_catalog SET customer_visible = FALSE
        WHERE slug IN (
            'tier-vision',  -- §3.2 step 3 resolves it. No caller names it
            'tier-stt',     -- §1.2. The app names `transcribe`
            'tier-tts',     -- §1.2. The app names `speak`
            'tier-embed',   -- §1.2, and D19.2 absorbs the price
            'tier-video',   -- Nothing binds it, and no Router verb serves it
            'tier-music'    -- Nothing binds it, and litellm has no music mode
        );
    END IF;
END $$;

COMMENT ON COLUMN tier_catalog.customer_visible IS
    'TRUE lets a customer picker show this tier (D-AI-3). FALSE hides it, '
    'because the Router or the app selects it and no person does. The '
    'default is TRUE, so a tier added later shows up until an operator '
    'hides it. Usage still records a hidden tier, so an administrator sees '
    'what it cost.';
