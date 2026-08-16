-- Metorite — Add display_name alias for dynamic agents.
-- Allows users to set a human-readable name (e.g. "Sales Assistant")
-- while keeping the internal name as the repo slug (e.g. "agent-sales-assistant").
-- If display_name is NULL or empty, the UI falls back to showing `name`.

ALTER TABLE dynamic_agents
    ADD COLUMN IF NOT EXISTS display_name TEXT;
