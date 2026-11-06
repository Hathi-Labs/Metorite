-- ============================================================================
-- 039_seat_tiers.sql — seat tiers as data, and one live tier per member
-- ============================================================================
-- Spec: seat_plans.md §3 and §11.1 (slice PLN-1). Metorite task 1280.
--
-- A tier is a plan_catalog row (kind 'core' for Standard, kind 'tier' for Pro
-- and Max). The numbers of a tier are ROWS with `effective_from`. A price or an
-- allowance change adds a row. A trigger refuses any UPDATE or DELETE of a
-- version row, so a live row is never edited.
--
-- What this file adds:
--
-- 1. The plan kind `tier`.
-- 2. `seat_tier`: which plan_catalog rows are seat tiers, and their rank.
--    `core` is rank 1, so every existing core seat reads as the lowest tier.
-- 3. `seat_tier_version`: allowance, features, AI tiers, limits and storage.
-- 4. `seat_tier_price`: a price per tier, currency and interval.
-- 5. `seat_assignment.is_seat_tier`, set by a trigger, and a partial unique
--    index on it. The database then refuses a second live tier seat for one
--    member in one org. The old index stays.
-- 6. Two views: the current version of a tier, and the tier of a member.
--
-- Pro and Max land INACTIVE. Nothing reads these tables yet, so no checkout,
-- seat grid or MRR changes. `plan_catalog.price_inr` stays the billing source
-- until PLN-12.
--
-- R6: expand only. New tables, one new column with a default, one CHECK made
-- wider. Nothing renamed. Nothing dropped but that CHECK, replaced in the same
-- block. Every statement replays safely.
-- R1: 038 is the highest number on disk at build time. Check again at merge.
--
-- This file contains no per-cent character, for the reason 003 gives.
--
-- Fence: tests/unit/test_customer_console_seat_tiers.py (R8).
-- ============================================================================


-- ── 1. The plan kind `tier` ─────────────────────────────────────────────────

DO $$
DECLARE
    old_name TEXT;
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'plan_catalog_kind_known'
          AND position('''tier''' in pg_get_constraintdef(oid)) > 0
    ) THEN
        FOR old_name IN
            SELECT c.conname
            FROM pg_constraint c
            JOIN pg_attribute a
              ON a.attrelid = c.conrelid AND a.attnum = ANY (c.conkey)
            WHERE c.conrelid = 'plan_catalog'::regclass
              AND c.contype = 'c'
              AND a.attname = 'kind'
        LOOP
            EXECUTE 'ALTER TABLE plan_catalog DROP CONSTRAINT '
                    || quote_ident(old_name);
        END LOOP;
        ALTER TABLE plan_catalog
            ADD CONSTRAINT plan_catalog_kind_known
            CHECK (kind IN ('core', 'center', 'addon', 'bundle', 'app', 'tier'));
    END IF;
END $$;

INSERT INTO plan_catalog (slug, name, kind, price_inr, active, sort_order) VALUES
    ('pro', 'Pro', 'tier', 0, FALSE, 11),
    ('max', 'Max', 'tier', 0, FALSE, 12)
ON CONFLICT (slug) DO NOTHING;


-- ── 2. Which plans are seat tiers ───────────────────────────────────────────

CREATE TABLE IF NOT EXISTS seat_tier (
    plan_slug     TEXT PRIMARY KEY REFERENCES plan_catalog(slug),
    -- 1 is the lowest tier. Rank is unique, so the order is never a tie.
    rank          INT NOT NULL UNIQUE CHECK (rank >= 1),
    display_name  TEXT NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO seat_tier (plan_slug, rank, display_name) VALUES
    ('core', 1, 'Standard'),
    ('pro',  2, 'Pro'),
    ('max',  3, 'Max')
ON CONFLICT (plan_slug) DO NOTHING;


-- ── 3. The numbers of a tier, versioned ─────────────────────────────────────

CREATE TABLE IF NOT EXISTS seat_tier_version (
    id                       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    plan_slug                TEXT NOT NULL REFERENCES seat_tier(plan_slug),
    effective_from           TIMESTAMPTZ NOT NULL,
    -- The allowance as a multiple of the Standard allowance (1, 5, 10). The
    -- credit amount of 1x comes from the priced rate card (task 524).
    allowance_multiple       NUMERIC(8, 2) NOT NULL CHECK (allowance_multiple >= 0),
    -- Feature keys the seat unlocks, and the AI tiers the seat may call.
    features                 JSONB NOT NULL DEFAULT '[]'::jsonb,
    ai_tiers                 JSONB NOT NULL DEFAULT '[]'::jsonb,
    -- An optional ceiling per AI tier: {"<ai_tier>": <credits>}.
    ai_tier_ceilings         JSONB NOT NULL DEFAULT '{}'::jsonb,
    storage_gb_per_seat      INT NOT NULL CHECK (storage_gb_per_seat >= 0),
    max_background_agents    INT NOT NULL CHECK (max_background_agents >= 0),
    max_automations          INT NOT NULL CHECK (max_automations >= 0),
    max_file_mb              INT NOT NULL CHECK (max_file_mb >= 0),
    created_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT seat_tier_version_uniq UNIQUE (plan_slug, effective_from),
    CONSTRAINT seat_tier_version_features_array
        CHECK (jsonb_typeof(features) = 'array'),
    CONSTRAINT seat_tier_version_ai_tiers_array
        CHECK (jsonb_typeof(ai_tiers) = 'array'),
    CONSTRAINT seat_tier_version_ceilings_object
        CHECK (jsonb_typeof(ai_tier_ceilings) = 'object')
);

-- ── 4. The price of a tier, per currency and interval ───────────────────────

CREATE TABLE IF NOT EXISTS seat_tier_price (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    plan_slug        TEXT NOT NULL REFERENCES seat_tier(plan_slug),
    currency         TEXT NOT NULL CHECK (currency IN ('USD', 'INR')),
    billing_interval TEXT NOT NULL CHECK (billing_interval IN ('month', 'year')),
    -- Major units, as a decimal. Prices exclude tax.
    amount           NUMERIC(12, 2) NOT NULL CHECK (amount >= 0),
    effective_from   TIMESTAMPTZ NOT NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT seat_tier_price_uniq
        UNIQUE (plan_slug, currency, billing_interval, effective_from)
);

-- A version or price row is never edited and never deleted. A change is a new
-- row with a later `effective_from`.
CREATE OR REPLACE FUNCTION seat_tier_rows_immutable() RETURNS trigger AS $fn$
BEGIN
    RAISE EXCEPTION 'seat tier rows are never changed: add a row with a later effective_from'
        USING ERRCODE = 'restrict_violation';
END;
$fn$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS seat_tier_version_immutable ON seat_tier_version;
CREATE TRIGGER seat_tier_version_immutable
    BEFORE UPDATE OR DELETE ON seat_tier_version
    FOR EACH ROW EXECUTE FUNCTION seat_tier_rows_immutable();

DROP TRIGGER IF EXISTS seat_tier_price_immutable ON seat_tier_price;
CREATE TRIGGER seat_tier_price_immutable
    BEFORE UPDATE OR DELETE ON seat_tier_price
    FOR EACH ROW EXECUTE FUNCTION seat_tier_rows_immutable();

-- The first version of each tier, from seat_plans.md §3.1. The fixed
-- effective_from makes a replay a no-op.
INSERT INTO seat_tier_version
    (plan_slug, effective_from, allowance_multiple, features, ai_tiers,
     storage_gb_per_seat, max_background_agents, max_automations, max_file_mb)
VALUES
    ('core', '2026-11-06T00:00:00Z', 1,
     '["chat","apps","mcp","dictation","meeting_notes","extra_usage"]'::jsonb,
     '["text"]'::jsonb, 30, 1, 10, 100),
    ('pro', '2026-11-06T00:00:00Z', 5,
     '["chat","apps","mcp","dictation","meeting_notes","extra_usage",
       "deep_research","image_generation","voice_conversation"]'::jsonb,
     '["text","image"]'::jsonb, 250, 3, 100, 1000),
    ('max', '2026-11-06T00:00:00Z', 10,
     '["chat","apps","mcp","dictation","meeting_notes","extra_usage",
       "deep_research","image_generation","voice_conversation",
       "video_music_sound","early_access"]'::jsonb,
     '["text","image","video"]'::jsonb, 1024, 10, 500, 5000)
ON CONFLICT (plan_slug, effective_from) DO NOTHING;

INSERT INTO seat_tier_price
    (plan_slug, currency, billing_interval, amount, effective_from)
VALUES
    ('core', 'USD', 'month',   20.00, '2026-11-06T00:00:00Z'),
    ('core', 'USD', 'year',   200.00, '2026-11-06T00:00:00Z'),
    ('core', 'INR', 'month', 1699.00, '2026-11-06T00:00:00Z'),
    ('core', 'INR', 'year', 16990.00, '2026-11-06T00:00:00Z'),
    ('pro',  'USD', 'month',  100.00, '2026-11-06T00:00:00Z'),
    ('pro',  'USD', 'year',  1000.00, '2026-11-06T00:00:00Z'),
    ('pro',  'INR', 'month', 8499.00, '2026-11-06T00:00:00Z'),
    ('pro',  'INR', 'year', 84990.00, '2026-11-06T00:00:00Z'),
    ('max',  'USD', 'month',  200.00, '2026-11-06T00:00:00Z'),
    ('max',  'USD', 'year',  2000.00, '2026-11-06T00:00:00Z'),
    ('max',  'INR', 'month', 16999.00, '2026-11-06T00:00:00Z'),
    ('max',  'INR', 'year', 169990.00, '2026-11-06T00:00:00Z')
ON CONFLICT (plan_slug, currency, billing_interval, effective_from) DO NOTHING;


-- ── 5. One live tier seat per member per org ────────────────────────────────
--
-- A partial index cannot look into another table, so a trigger copies the fact
-- "this plan is a seat tier" onto the row. The index then holds in the
-- database, and two concurrent inserts cannot both pass.

ALTER TABLE seat_assignment
    ADD COLUMN IF NOT EXISTS is_seat_tier BOOLEAN NOT NULL DEFAULT FALSE;

CREATE OR REPLACE FUNCTION seat_assignment_set_tier_flag() RETURNS trigger AS $fn$
BEGIN
    NEW.is_seat_tier := EXISTS (
        SELECT 1 FROM seat_tier WHERE plan_slug = NEW.plan_slug
    );
    RETURN NEW;
END;
$fn$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS seat_assignment_tier_flag ON seat_assignment;
CREATE TRIGGER seat_assignment_tier_flag
    BEFORE INSERT OR UPDATE OF plan_slug ON seat_assignment
    FOR EACH ROW EXECUTE FUNCTION seat_assignment_set_tier_flag();

-- Existing seats: core is a tier, so every live core seat is flagged. The old
-- unique index already allows one live core seat per member, so the new index
-- builds without a conflict.
UPDATE seat_assignment SET is_seat_tier = TRUE
 WHERE is_seat_tier = FALSE
   AND plan_slug IN (SELECT plan_slug FROM seat_tier);

CREATE UNIQUE INDEX IF NOT EXISTS seat_assignment_one_live_tier
    ON seat_assignment (organization_id, user_identity_id)
    WHERE released_at IS NULL AND is_seat_tier;


-- ── 6. Views ────────────────────────────────────────────────────────────────

-- The version of each tier that applies now.
CREATE OR REPLACE VIEW seat_tier_current AS
SELECT DISTINCT ON (v.plan_slug)
       v.plan_slug, t.rank, t.display_name, v.effective_from,
       v.allowance_multiple, v.features, v.ai_tiers, v.ai_tier_ceilings,
       v.storage_gb_per_seat, v.max_background_agents, v.max_automations,
       v.max_file_mb
  FROM seat_tier_version v
  JOIN seat_tier t ON t.plan_slug = v.plan_slug
 WHERE v.effective_from <= now()
 ORDER BY v.plan_slug, v.effective_from DESC;

-- The live tier of each member. A core seat is rank 1, Standard.
CREATE OR REPLACE VIEW member_seat_tier AS
SELECT a.organization_id, a.user_identity_id, a.plan_slug,
       t.rank AS tier_rank, t.display_name AS tier_name
  FROM seat_assignment a
  JOIN seat_tier t ON t.plan_slug = a.plan_slug
 WHERE a.released_at IS NULL;
