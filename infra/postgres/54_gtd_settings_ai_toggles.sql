-- 54_gtd_settings_ai_toggles.sql — user toggles for the ClickUp-intelligence
-- features (task_manager_app.md §2.2/§9.3).
--
-- What: two per-user booleans on user_settings —
--       clarify_use_llm  — use the LLM clarify cognition pass (Phase 3). When
--                          off, Clarify uses only the instant deterministic
--                          heuristic (no LLM call on the interactive path).
--       background_sync   — keep connected PM workspaces synced in the
--                          background on a schedule (Phase 1). When off, the
--                          per-account background loops don't run for this user
--                          (manual + auto-sync-on-open still work).
-- Why:  both features shipped always-on; these give the user cost/latency
--       control (the LLM clarify adds a round-trip to every clarify; the
--       background scheduler makes periodic outward ClickUp calls). Default
--       TRUE so the migration preserves current behaviour.
-- Depends on: 51_gtd_settings.sql. Idempotent.

ALTER TABLE user_settings
    ADD COLUMN IF NOT EXISTS clarify_use_llm BOOLEAN NOT NULL DEFAULT true;

ALTER TABLE user_settings
    ADD COLUMN IF NOT EXISTS background_sync BOOLEAN NOT NULL DEFAULT true;
