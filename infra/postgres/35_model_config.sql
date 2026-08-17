-- Metorite — Runtime-mutable model configuration.
-- Replaces git-tracked files that `git reset --hard origin/main` wiped on every
-- deploy (symptom: hidden models reappeared, tier assignments reverted):
--   - infra/enabled_models.json        → key 'enabled_models'
--   - infra/litellm/tier_overrides.yaml → key 'tier_overrides'
-- Config now persists in Postgres so it survives deploys, restarts, and reboots
-- (same rationale as 15_dynamic_agents.sql).
--
-- The legacy files are kept for a one-time seed: on first read after this
-- migration, the gateway imports their contents into this table, then the DB
-- becomes the sole source of truth. Writes go exclusively here.
--
-- Stored as JSON blobs keyed by config key:
--   'enabled_models' → {"enabled": [...], "hidden": [...]}
--   'tier_overrides' → {"model_list": [...]}

CREATE TABLE IF NOT EXISTS model_config (
    key        TEXT PRIMARY KEY,
    value      JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
