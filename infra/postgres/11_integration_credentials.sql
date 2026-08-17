-- Metorite — Unified credential store migration.
-- Extends the existing provider_keys table (ADR-008, 08_provider_keys.sql)
-- to store ALL API credentials — both LLM provider keys AND business
-- integration credentials (Zoho, ClickUp, Apollo, Gmail, etc.).
--
-- credential_type discriminates:
--   'llm'          — LLM provider keys (OpenAI, Anthropic, Gemini, etc.)
--   'integration'  — Business integration credentials (Zoho, ClickUp, etc.)
--
-- Each integration service may have MULTIPLE keys (e.g. Zoho needs
-- client_id + client_secret + refresh_token).  These are stored as
-- separate rows keyed by provider='{service}:{key_name}'.
--
-- Backward compatible: existing rows without credential_type default to 'llm'.

ALTER TABLE provider_keys
ADD COLUMN IF NOT EXISTS credential_type TEXT NOT NULL DEFAULT 'llm';

CREATE INDEX IF NOT EXISTS idx_provider_keys_credential_type
ON provider_keys (credential_type);

-- Track which service each credential belongs to (denormalized for fast lookup).
-- For LLM keys this is the same as provider (e.g. 'openai').
-- For integration keys this is the service name (e.g. 'zoho-crm', 'clickup').
ALTER TABLE provider_keys
ADD COLUMN IF NOT EXISTS service TEXT;

CREATE INDEX IF NOT EXISTS idx_provider_keys_service
ON provider_keys (service);
