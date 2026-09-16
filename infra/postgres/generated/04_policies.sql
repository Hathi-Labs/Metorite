-- ============================================================================
-- MT-1b · phase 4/4 policies — GENERATED, DO NOT EDIT BY HAND
-- ============================================================================
-- Regenerate with: uv run python scripts/gen_tenant_migration.py
-- Spec: project-docs/specs/saas_multitenancy.md §1.3 · MT-1b · WS-29 · D15
--
-- ENABLE + FORCE ROW LEVEL SECURITY + the policy. Instant — no scan. ⚠️ AND IT IS A CLIFF: the moment this applies, any connection that has not bound app.tenant_id reads ZERO ROWS. That is the fail-closed property working (§0.1). MT-1c must be deployed AND VERIFIED first, or the product goes dark.
--
-- Tables in this phase: 142
--
-- ⚠️ NOT COVERED BY THIS FILE — `organization_id` already means something
-- else on these tables, so scoping them by that name would corrupt a
-- business column. They carry NO tenant isolation until the column is
-- renamed (owner call; see gen_tenant_migration.HOMONYM_BLOCKED):
--   crm_activities     organization_id = the customer company (144_crm.sql:289)
--   crm_contacts       organization_id = the customer company (144_crm.sql:74)
--   crm_deals          organization_id = the customer company (144_crm.sql:197)
--
-- ⚠️ NOT a numbered migration. `apply_migrations.sh` does not replay this
-- directory. Promoting it is a deliberate act taken against a database in a
-- maintenance window — see the module docstring of the generator for the
-- outage that makes that non-negotiable.
-- ============================================================================


-- Four clauses, each load-bearing (saas_multitenancy_implementation.md §1.1):
--   ENABLE       turns the policy on for ordinary roles
--   FORCE        applies it to the table OWNER too — without this the
--                owner silently reads every tenant
--   USING        filters what a query can SEE
--   WITH CHECK   constrains what it can WRITE. Without it a tenant can
--                INSERT a row stamped with another tenant's id.
--   , true       makes an unset GUC return NULL (-> no rows) instead of
--                RAISING, so an unconverted path fails closed and quiet
--                rather than 500-ing everywhere at once.

ALTER TABLE access_request ENABLE ROW LEVEL SECURITY;
ALTER TABLE access_request FORCE  ROW LEVEL SECURITY;
DROP POLICY IF EXISTS access_request_tenant_isolation ON access_request;
CREATE POLICY access_request_tenant_isolation ON access_request
    USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
    WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);

ALTER TABLE action_item ENABLE ROW LEVEL SECURITY;
ALTER TABLE action_item FORCE  ROW LEVEL SECURITY;
DROP POLICY IF EXISTS action_item_tenant_isolation ON action_item;
CREATE POLICY action_item_tenant_isolation ON action_item
    USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
    WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);

ALTER TABLE agent_avatars ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_avatars FORCE  ROW LEVEL SECURITY;
DROP POLICY IF EXISTS agent_avatars_tenant_isolation ON agent_avatars;
CREATE POLICY agent_avatars_tenant_isolation ON agent_avatars
    USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
    WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);

ALTER TABLE agent_blob ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_blob FORCE  ROW LEVEL SECURITY;
DROP POLICY IF EXISTS agent_blob_tenant_isolation ON agent_blob;
CREATE POLICY agent_blob_tenant_isolation ON agent_blob
    USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
    WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);

ALTER TABLE agent_file_history ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_file_history FORCE  ROW LEVEL SECURITY;
DROP POLICY IF EXISTS agent_file_history_tenant_isolation ON agent_file_history;
CREATE POLICY agent_file_history_tenant_isolation ON agent_file_history
    USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
    WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);

ALTER TABLE agent_run ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_run FORCE  ROW LEVEL SECURITY;
DROP POLICY IF EXISTS agent_run_tenant_isolation ON agent_run;
CREATE POLICY agent_run_tenant_isolation ON agent_run
    USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
    WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);

ALTER TABLE agent_skill_setting ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_skill_setting FORCE  ROW LEVEL SECURITY;
DROP POLICY IF EXISTS agent_skill_setting_tenant_isolation ON agent_skill_setting;
CREATE POLICY agent_skill_setting_tenant_isolation ON agent_skill_setting
    USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
    WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);

ALTER TABLE app_audit ENABLE ROW LEVEL SECURITY;
ALTER TABLE app_audit FORCE  ROW LEVEL SECURITY;
DROP POLICY IF EXISTS app_audit_tenant_isolation ON app_audit;
CREATE POLICY app_audit_tenant_isolation ON app_audit
    USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
    WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);

ALTER TABLE app_data ENABLE ROW LEVEL SECURITY;
ALTER TABLE app_data FORCE  ROW LEVEL SECURITY;
DROP POLICY IF EXISTS app_data_tenant_isolation ON app_data;
CREATE POLICY app_data_tenant_isolation ON app_data
    USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
    WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);

ALTER TABLE app_files ENABLE ROW LEVEL SECURITY;
ALTER TABLE app_files FORCE  ROW LEVEL SECURITY;
DROP POLICY IF EXISTS app_files_tenant_isolation ON app_files;
CREATE POLICY app_files_tenant_isolation ON app_files
    USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
    WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);

ALTER TABLE app_grants ENABLE ROW LEVEL SECURITY;
ALTER TABLE app_grants FORCE  ROW LEVEL SECURITY;
DROP POLICY IF EXISTS app_grants_tenant_isolation ON app_grants;
CREATE POLICY app_grants_tenant_isolation ON app_grants
    USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
    WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);

ALTER TABLE app_pins ENABLE ROW LEVEL SECURITY;
ALTER TABLE app_pins FORCE  ROW LEVEL SECURITY;
DROP POLICY IF EXISTS app_pins_tenant_isolation ON app_pins;
CREATE POLICY app_pins_tenant_isolation ON app_pins
    USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
    WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);

ALTER TABLE app_tool_grants ENABLE ROW LEVEL SECURITY;
ALTER TABLE app_tool_grants FORCE  ROW LEVEL SECURITY;
DROP POLICY IF EXISTS app_tool_grants_tenant_isolation ON app_tool_grants;
CREATE POLICY app_tool_grants_tenant_isolation ON app_tool_grants
    USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
    WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);

ALTER TABLE app_user ENABLE ROW LEVEL SECURITY;
ALTER TABLE app_user FORCE  ROW LEVEL SECURITY;
DROP POLICY IF EXISTS app_user_tenant_isolation ON app_user;
CREATE POLICY app_user_tenant_isolation ON app_user
    USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
    WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);

ALTER TABLE app_versions ENABLE ROW LEVEL SECURITY;
ALTER TABLE app_versions FORCE  ROW LEVEL SECURITY;
DROP POLICY IF EXISTS app_versions_tenant_isolation ON app_versions;
CREATE POLICY app_versions_tenant_isolation ON app_versions
    USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
    WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);

ALTER TABLE apps ENABLE ROW LEVEL SECURITY;
ALTER TABLE apps FORCE  ROW LEVEL SECURITY;
DROP POLICY IF EXISTS apps_tenant_isolation ON apps;
CREATE POLICY apps_tenant_isolation ON apps
    USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
    WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);

ALTER TABLE audit_event ENABLE ROW LEVEL SECURITY;
ALTER TABLE audit_event FORCE  ROW LEVEL SECURITY;
DROP POLICY IF EXISTS audit_event_tenant_isolation ON audit_event;
CREATE POLICY audit_event_tenant_isolation ON audit_event
    USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
    WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);

ALTER TABLE chat_message ENABLE ROW LEVEL SECURITY;
ALTER TABLE chat_message FORCE  ROW LEVEL SECURITY;
DROP POLICY IF EXISTS chat_message_tenant_isolation ON chat_message;
CREATE POLICY chat_message_tenant_isolation ON chat_message
    USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
    WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);

ALTER TABLE chat_session ENABLE ROW LEVEL SECURITY;
ALTER TABLE chat_session FORCE  ROW LEVEL SECURITY;
DROP POLICY IF EXISTS chat_session_tenant_isolation ON chat_session;
CREATE POLICY chat_session_tenant_isolation ON chat_session
    USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
    WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);

ALTER TABLE chat_session_agent ENABLE ROW LEVEL SECURITY;
ALTER TABLE chat_session_agent FORCE  ROW LEVEL SECURITY;
DROP POLICY IF EXISTS chat_session_agent_tenant_isolation ON chat_session_agent;
CREATE POLICY chat_session_agent_tenant_isolation ON chat_session_agent
    USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
    WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);

ALTER TABLE chat_session_participant ENABLE ROW LEVEL SECURITY;
ALTER TABLE chat_session_participant FORCE  ROW LEVEL SECURITY;
DROP POLICY IF EXISTS chat_session_participant_tenant_isolation ON chat_session_participant;
CREATE POLICY chat_session_participant_tenant_isolation ON chat_session_participant
    USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
    WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);

ALTER TABLE copilot_config ENABLE ROW LEVEL SECURITY;
ALTER TABLE copilot_config FORCE  ROW LEVEL SECURITY;
DROP POLICY IF EXISTS copilot_config_tenant_isolation ON copilot_config;
CREATE POLICY copilot_config_tenant_isolation ON copilot_config
    USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
    WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);

ALTER TABLE copilot_event ENABLE ROW LEVEL SECURITY;
ALTER TABLE copilot_event FORCE  ROW LEVEL SECURITY;
DROP POLICY IF EXISTS copilot_event_tenant_isolation ON copilot_event;
CREATE POLICY copilot_event_tenant_isolation ON copilot_event
    USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
    WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);

ALTER TABLE crm_auto_lead_cursors ENABLE ROW LEVEL SECURITY;
ALTER TABLE crm_auto_lead_cursors FORCE  ROW LEVEL SECURITY;
DROP POLICY IF EXISTS crm_auto_lead_cursors_tenant_isolation ON crm_auto_lead_cursors;
CREATE POLICY crm_auto_lead_cursors_tenant_isolation ON crm_auto_lead_cursors
    USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
    WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);

ALTER TABLE crm_deal_contacts ENABLE ROW LEVEL SECURITY;
ALTER TABLE crm_deal_contacts FORCE  ROW LEVEL SECURITY;
DROP POLICY IF EXISTS crm_deal_contacts_tenant_isolation ON crm_deal_contacts;
CREATE POLICY crm_deal_contacts_tenant_isolation ON crm_deal_contacts
    USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
    WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);

ALTER TABLE crm_deal_statuses ENABLE ROW LEVEL SECURITY;
ALTER TABLE crm_deal_statuses FORCE  ROW LEVEL SECURITY;
DROP POLICY IF EXISTS crm_deal_statuses_tenant_isolation ON crm_deal_statuses;
CREATE POLICY crm_deal_statuses_tenant_isolation ON crm_deal_statuses
    USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
    WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);

ALTER TABLE crm_lead_statuses ENABLE ROW LEVEL SECURITY;
ALTER TABLE crm_lead_statuses FORCE  ROW LEVEL SECURITY;
DROP POLICY IF EXISTS crm_lead_statuses_tenant_isolation ON crm_lead_statuses;
CREATE POLICY crm_lead_statuses_tenant_isolation ON crm_lead_statuses
    USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
    WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);

ALTER TABLE crm_leads ENABLE ROW LEVEL SECURITY;
ALTER TABLE crm_leads FORCE  ROW LEVEL SECURITY;
DROP POLICY IF EXISTS crm_leads_tenant_isolation ON crm_leads;
CREATE POLICY crm_leads_tenant_isolation ON crm_leads
    USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
    WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);

ALTER TABLE crm_lost_reasons ENABLE ROW LEVEL SECURITY;
ALTER TABLE crm_lost_reasons FORCE  ROW LEVEL SECURITY;
DROP POLICY IF EXISTS crm_lost_reasons_tenant_isolation ON crm_lost_reasons;
CREATE POLICY crm_lost_reasons_tenant_isolation ON crm_lost_reasons
    USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
    WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);

ALTER TABLE crm_organizations ENABLE ROW LEVEL SECURITY;
ALTER TABLE crm_organizations FORCE  ROW LEVEL SECURITY;
DROP POLICY IF EXISTS crm_organizations_tenant_isolation ON crm_organizations;
CREATE POLICY crm_organizations_tenant_isolation ON crm_organizations
    USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
    WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);

ALTER TABLE crm_status_changes ENABLE ROW LEVEL SECURITY;
ALTER TABLE crm_status_changes FORCE  ROW LEVEL SECURITY;
DROP POLICY IF EXISTS crm_status_changes_tenant_isolation ON crm_status_changes;
CREATE POLICY crm_status_changes_tenant_isolation ON crm_status_changes
    USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
    WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);

ALTER TABLE crm_sync_cursors ENABLE ROW LEVEL SECURITY;
ALTER TABLE crm_sync_cursors FORCE  ROW LEVEL SECURITY;
DROP POLICY IF EXISTS crm_sync_cursors_tenant_isolation ON crm_sync_cursors;
CREATE POLICY crm_sync_cursors_tenant_isolation ON crm_sync_cursors
    USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
    WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);

ALTER TABLE crm_zoho_tombstones ENABLE ROW LEVEL SECURITY;
ALTER TABLE crm_zoho_tombstones FORCE  ROW LEVEL SECURITY;
DROP POLICY IF EXISTS crm_zoho_tombstones_tenant_isolation ON crm_zoho_tombstones;
CREATE POLICY crm_zoho_tombstones_tenant_isolation ON crm_zoho_tombstones
    USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
    WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);

ALTER TABLE custom_api_definitions ENABLE ROW LEVEL SECURITY;
ALTER TABLE custom_api_definitions FORCE  ROW LEVEL SECURITY;
DROP POLICY IF EXISTS custom_api_definitions_tenant_isolation ON custom_api_definitions;
CREATE POLICY custom_api_definitions_tenant_isolation ON custom_api_definitions
    USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
    WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);

ALTER TABLE customer ENABLE ROW LEVEL SECURITY;
ALTER TABLE customer FORCE  ROW LEVEL SECURITY;
DROP POLICY IF EXISTS customer_tenant_isolation ON customer;
CREATE POLICY customer_tenant_isolation ON customer
    USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
    WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);

ALTER TABLE deal ENABLE ROW LEVEL SECURITY;
ALTER TABLE deal FORCE  ROW LEVEL SECURITY;
DROP POLICY IF EXISTS deal_tenant_isolation ON deal;
CREATE POLICY deal_tenant_isolation ON deal
    USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
    WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);

ALTER TABLE dynamic_agents ENABLE ROW LEVEL SECURITY;
ALTER TABLE dynamic_agents FORCE  ROW LEVEL SECURITY;
DROP POLICY IF EXISTS dynamic_agents_tenant_isolation ON dynamic_agents;
CREATE POLICY dynamic_agents_tenant_isolation ON dynamic_agents
    USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
    WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);

ALTER TABLE email_accounts ENABLE ROW LEVEL SECURITY;
ALTER TABLE email_accounts FORCE  ROW LEVEL SECURITY;
DROP POLICY IF EXISTS email_accounts_tenant_isolation ON email_accounts;
CREATE POLICY email_accounts_tenant_isolation ON email_accounts
    USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
    WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);

ALTER TABLE email_actions ENABLE ROW LEVEL SECURITY;
ALTER TABLE email_actions FORCE  ROW LEVEL SECURITY;
DROP POLICY IF EXISTS email_actions_tenant_isolation ON email_actions;
CREATE POLICY email_actions_tenant_isolation ON email_actions
    USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
    WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);

ALTER TABLE email_ai_drafts ENABLE ROW LEVEL SECURITY;
ALTER TABLE email_ai_drafts FORCE  ROW LEVEL SECURITY;
DROP POLICY IF EXISTS email_ai_drafts_tenant_isolation ON email_ai_drafts;
CREATE POLICY email_ai_drafts_tenant_isolation ON email_ai_drafts
    USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
    WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);

ALTER TABLE email_assistant_settings ENABLE ROW LEVEL SECURITY;
ALTER TABLE email_assistant_settings FORCE  ROW LEVEL SECURITY;
DROP POLICY IF EXISTS email_assistant_settings_tenant_isolation ON email_assistant_settings;
CREATE POLICY email_assistant_settings_tenant_isolation ON email_assistant_settings
    USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
    WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);

ALTER TABLE email_attachments ENABLE ROW LEVEL SECURITY;
ALTER TABLE email_attachments FORCE  ROW LEVEL SECURITY;
DROP POLICY IF EXISTS email_attachments_tenant_isolation ON email_attachments;
CREATE POLICY email_attachments_tenant_isolation ON email_attachments
    USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
    WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);

ALTER TABLE email_cold_senders ENABLE ROW LEVEL SECURITY;
ALTER TABLE email_cold_senders FORCE  ROW LEVEL SECURITY;
DROP POLICY IF EXISTS email_cold_senders_tenant_isolation ON email_cold_senders;
CREATE POLICY email_cold_senders_tenant_isolation ON email_cold_senders
    USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
    WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);

ALTER TABLE email_contacts ENABLE ROW LEVEL SECURITY;
ALTER TABLE email_contacts FORCE  ROW LEVEL SECURITY;
DROP POLICY IF EXISTS email_contacts_tenant_isolation ON email_contacts;
CREATE POLICY email_contacts_tenant_isolation ON email_contacts
    USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
    WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);

ALTER TABLE email_embeddings ENABLE ROW LEVEL SECURITY;
ALTER TABLE email_embeddings FORCE  ROW LEVEL SECURITY;
DROP POLICY IF EXISTS email_embeddings_tenant_isolation ON email_embeddings;
CREATE POLICY email_embeddings_tenant_isolation ON email_embeddings
    USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
    WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);

ALTER TABLE email_executed_rules ENABLE ROW LEVEL SECURITY;
ALTER TABLE email_executed_rules FORCE  ROW LEVEL SECURITY;
DROP POLICY IF EXISTS email_executed_rules_tenant_isolation ON email_executed_rules;
CREATE POLICY email_executed_rules_tenant_isolation ON email_executed_rules
    USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
    WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);

ALTER TABLE email_folders ENABLE ROW LEVEL SECURITY;
ALTER TABLE email_folders FORCE  ROW LEVEL SECURITY;
DROP POLICY IF EXISTS email_folders_tenant_isolation ON email_folders;
CREATE POLICY email_folders_tenant_isolation ON email_folders
    USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
    WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);

ALTER TABLE email_knowledge ENABLE ROW LEVEL SECURITY;
ALTER TABLE email_knowledge FORCE  ROW LEVEL SECURITY;
DROP POLICY IF EXISTS email_knowledge_tenant_isolation ON email_knowledge;
CREATE POLICY email_knowledge_tenant_isolation ON email_knowledge
    USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
    WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);

ALTER TABLE email_learned_patterns ENABLE ROW LEVEL SECURITY;
ALTER TABLE email_learned_patterns FORCE  ROW LEVEL SECURITY;
DROP POLICY IF EXISTS email_learned_patterns_tenant_isolation ON email_learned_patterns;
CREATE POLICY email_learned_patterns_tenant_isolation ON email_learned_patterns
    USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
    WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);

ALTER TABLE email_messages ENABLE ROW LEVEL SECURITY;
ALTER TABLE email_messages FORCE  ROW LEVEL SECURITY;
DROP POLICY IF EXISTS email_messages_tenant_isolation ON email_messages;
CREATE POLICY email_messages_tenant_isolation ON email_messages
    USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
    WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);

ALTER TABLE email_newsletters ENABLE ROW LEVEL SECURITY;
ALTER TABLE email_newsletters FORCE  ROW LEVEL SECURITY;
DROP POLICY IF EXISTS email_newsletters_tenant_isolation ON email_newsletters;
CREATE POLICY email_newsletters_tenant_isolation ON email_newsletters
    USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
    WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);

ALTER TABLE email_rule_guidance ENABLE ROW LEVEL SECURITY;
ALTER TABLE email_rule_guidance FORCE  ROW LEVEL SECURITY;
DROP POLICY IF EXISTS email_rule_guidance_tenant_isolation ON email_rule_guidance;
CREATE POLICY email_rule_guidance_tenant_isolation ON email_rule_guidance
    USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
    WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);

ALTER TABLE email_rule_patterns ENABLE ROW LEVEL SECURITY;
ALTER TABLE email_rule_patterns FORCE  ROW LEVEL SECURITY;
DROP POLICY IF EXISTS email_rule_patterns_tenant_isolation ON email_rule_patterns;
CREATE POLICY email_rule_patterns_tenant_isolation ON email_rule_patterns
    USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
    WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);

ALTER TABLE email_rules ENABLE ROW LEVEL SECURITY;
ALTER TABLE email_rules FORCE  ROW LEVEL SECURITY;
DROP POLICY IF EXISTS email_rules_tenant_isolation ON email_rules;
CREATE POLICY email_rules_tenant_isolation ON email_rules
    USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
    WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);

ALTER TABLE email_senders ENABLE ROW LEVEL SECURITY;
ALTER TABLE email_senders FORCE  ROW LEVEL SECURITY;
DROP POLICY IF EXISTS email_senders_tenant_isolation ON email_senders;
CREATE POLICY email_senders_tenant_isolation ON email_senders
    USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
    WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);

ALTER TABLE email_sync_log ENABLE ROW LEVEL SECURITY;
ALTER TABLE email_sync_log FORCE  ROW LEVEL SECURITY;
DROP POLICY IF EXISTS email_sync_log_tenant_isolation ON email_sync_log;
CREATE POLICY email_sync_log_tenant_isolation ON email_sync_log
    USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
    WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);

ALTER TABLE email_thread_status ENABLE ROW LEVEL SECURITY;
ALTER TABLE email_thread_status FORCE  ROW LEVEL SECURITY;
DROP POLICY IF EXISTS email_thread_status_tenant_isolation ON email_thread_status;
CREATE POLICY email_thread_status_tenant_isolation ON email_thread_status
    USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
    WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);

ALTER TABLE email_voice_profiles ENABLE ROW LEVEL SECURITY;
ALTER TABLE email_voice_profiles FORCE  ROW LEVEL SECURITY;
DROP POLICY IF EXISTS email_voice_profiles_tenant_isolation ON email_voice_profiles;
CREATE POLICY email_voice_profiles_tenant_isolation ON email_voice_profiles
    USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
    WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);

ALTER TABLE gtd_attachments ENABLE ROW LEVEL SECURITY;
ALTER TABLE gtd_attachments FORCE  ROW LEVEL SECURITY;
DROP POLICY IF EXISTS gtd_attachments_tenant_isolation ON gtd_attachments;
CREATE POLICY gtd_attachments_tenant_isolation ON gtd_attachments
    USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
    WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);

ALTER TABLE gtd_contexts ENABLE ROW LEVEL SECURITY;
ALTER TABLE gtd_contexts FORCE  ROW LEVEL SECURITY;
DROP POLICY IF EXISTS gtd_contexts_tenant_isolation ON gtd_contexts;
CREATE POLICY gtd_contexts_tenant_isolation ON gtd_contexts
    USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
    WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);

ALTER TABLE gtd_day_state ENABLE ROW LEVEL SECURITY;
ALTER TABLE gtd_day_state FORCE  ROW LEVEL SECURITY;
DROP POLICY IF EXISTS gtd_day_state_tenant_isolation ON gtd_day_state;
CREATE POLICY gtd_day_state_tenant_isolation ON gtd_day_state
    USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
    WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);

ALTER TABLE gtd_folders ENABLE ROW LEVEL SECURITY;
ALTER TABLE gtd_folders FORCE  ROW LEVEL SECURITY;
DROP POLICY IF EXISTS gtd_folders_tenant_isolation ON gtd_folders;
CREATE POLICY gtd_folders_tenant_isolation ON gtd_folders
    USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
    WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);

ALTER TABLE gtd_horizons ENABLE ROW LEVEL SECURITY;
ALTER TABLE gtd_horizons FORCE  ROW LEVEL SECURITY;
DROP POLICY IF EXISTS gtd_horizons_tenant_isolation ON gtd_horizons;
CREATE POLICY gtd_horizons_tenant_isolation ON gtd_horizons
    USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
    WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);

ALTER TABLE gtd_items ENABLE ROW LEVEL SECURITY;
ALTER TABLE gtd_items FORCE  ROW LEVEL SECURITY;
DROP POLICY IF EXISTS gtd_items_tenant_isolation ON gtd_items;
CREATE POLICY gtd_items_tenant_isolation ON gtd_items
    USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
    WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);

ALTER TABLE gtd_people ENABLE ROW LEVEL SECURITY;
ALTER TABLE gtd_people FORCE  ROW LEVEL SECURITY;
DROP POLICY IF EXISTS gtd_people_tenant_isolation ON gtd_people;
CREATE POLICY gtd_people_tenant_isolation ON gtd_people
    USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
    WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);

ALTER TABLE gtd_person_absences ENABLE ROW LEVEL SECURITY;
ALTER TABLE gtd_person_absences FORCE  ROW LEVEL SECURITY;
DROP POLICY IF EXISTS gtd_person_absences_tenant_isolation ON gtd_person_absences;
CREATE POLICY gtd_person_absences_tenant_isolation ON gtd_person_absences
    USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
    WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);

ALTER TABLE gtd_person_credentials ENABLE ROW LEVEL SECURITY;
ALTER TABLE gtd_person_credentials FORCE  ROW LEVEL SECURITY;
DROP POLICY IF EXISTS gtd_person_credentials_tenant_isolation ON gtd_person_credentials;
CREATE POLICY gtd_person_credentials_tenant_isolation ON gtd_person_credentials
    USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
    WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);

ALTER TABLE gtd_person_resumes ENABLE ROW LEVEL SECURITY;
ALTER TABLE gtd_person_resumes FORCE  ROW LEVEL SECURITY;
DROP POLICY IF EXISTS gtd_person_resumes_tenant_isolation ON gtd_person_resumes;
CREATE POLICY gtd_person_resumes_tenant_isolation ON gtd_person_resumes
    USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
    WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);

ALTER TABLE gtd_person_skills ENABLE ROW LEVEL SECURITY;
ALTER TABLE gtd_person_skills FORCE  ROW LEVEL SECURITY;
DROP POLICY IF EXISTS gtd_person_skills_tenant_isolation ON gtd_person_skills;
CREATE POLICY gtd_person_skills_tenant_isolation ON gtd_person_skills
    USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
    WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);

ALTER TABLE gtd_projects ENABLE ROW LEVEL SECURITY;
ALTER TABLE gtd_projects FORCE  ROW LEVEL SECURITY;
DROP POLICY IF EXISTS gtd_projects_tenant_isolation ON gtd_projects;
CREATE POLICY gtd_projects_tenant_isolation ON gtd_projects
    USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
    WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);

ALTER TABLE gtd_reviews ENABLE ROW LEVEL SECURITY;
ALTER TABLE gtd_reviews FORCE  ROW LEVEL SECURITY;
DROP POLICY IF EXISTS gtd_reviews_tenant_isolation ON gtd_reviews;
CREATE POLICY gtd_reviews_tenant_isolation ON gtd_reviews
    USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
    WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);

ALTER TABLE gtd_rollover_log ENABLE ROW LEVEL SECURITY;
ALTER TABLE gtd_rollover_log FORCE  ROW LEVEL SECURITY;
DROP POLICY IF EXISTS gtd_rollover_log_tenant_isolation ON gtd_rollover_log;
CREATE POLICY gtd_rollover_log_tenant_isolation ON gtd_rollover_log
    USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
    WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);

ALTER TABLE gtd_settings ENABLE ROW LEVEL SECURITY;
ALTER TABLE gtd_settings FORCE  ROW LEVEL SECURITY;
DROP POLICY IF EXISTS gtd_settings_tenant_isolation ON gtd_settings;
CREATE POLICY gtd_settings_tenant_isolation ON gtd_settings
    USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
    WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);

ALTER TABLE gtd_spaces ENABLE ROW LEVEL SECURITY;
ALTER TABLE gtd_spaces FORCE  ROW LEVEL SECURITY;
DROP POLICY IF EXISTS gtd_spaces_tenant_isolation ON gtd_spaces;
CREATE POLICY gtd_spaces_tenant_isolation ON gtd_spaces
    USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
    WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);

ALTER TABLE gtd_waiting ENABLE ROW LEVEL SECURITY;
ALTER TABLE gtd_waiting FORCE  ROW LEVEL SECURITY;
DROP POLICY IF EXISTS gtd_waiting_tenant_isolation ON gtd_waiting;
CREATE POLICY gtd_waiting_tenant_isolation ON gtd_waiting
    USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
    WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);

ALTER TABLE live_session ENABLE ROW LEVEL SECURITY;
ALTER TABLE live_session FORCE  ROW LEVEL SECURITY;
DROP POLICY IF EXISTS live_session_tenant_isolation ON live_session;
CREATE POLICY live_session_tenant_isolation ON live_session
    USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
    WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);

ALTER TABLE meeting ENABLE ROW LEVEL SECURITY;
ALTER TABLE meeting FORCE  ROW LEVEL SECURITY;
DROP POLICY IF EXISTS meeting_tenant_isolation ON meeting;
CREATE POLICY meeting_tenant_isolation ON meeting
    USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
    WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);

ALTER TABLE meeting_bot ENABLE ROW LEVEL SECURITY;
ALTER TABLE meeting_bot FORCE  ROW LEVEL SECURITY;
DROP POLICY IF EXISTS meeting_bot_tenant_isolation ON meeting_bot;
CREATE POLICY meeting_bot_tenant_isolation ON meeting_bot
    USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
    WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);

ALTER TABLE meeting_note ENABLE ROW LEVEL SECURITY;
ALTER TABLE meeting_note FORCE  ROW LEVEL SECURITY;
DROP POLICY IF EXISTS meeting_note_tenant_isolation ON meeting_note;
CREATE POLICY meeting_note_tenant_isolation ON meeting_note
    USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
    WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);

ALTER TABLE meeting_recording ENABLE ROW LEVEL SECURITY;
ALTER TABLE meeting_recording FORCE  ROW LEVEL SECURITY;
DROP POLICY IF EXISTS meeting_recording_tenant_isolation ON meeting_recording;
CREATE POLICY meeting_recording_tenant_isolation ON meeting_recording
    USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
    WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);

ALTER TABLE message ENABLE ROW LEVEL SECURITY;
ALTER TABLE message FORCE  ROW LEVEL SECURITY;
DROP POLICY IF EXISTS message_tenant_isolation ON message;
CREATE POLICY message_tenant_isolation ON message
    USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
    WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);

ALTER TABLE notes_glossary ENABLE ROW LEVEL SECURITY;
ALTER TABLE notes_glossary FORCE  ROW LEVEL SECURITY;
DROP POLICY IF EXISTS notes_glossary_tenant_isolation ON notes_glossary;
CREATE POLICY notes_glossary_tenant_isolation ON notes_glossary
    USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
    WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);

ALTER TABLE org_group_member ENABLE ROW LEVEL SECURITY;
ALTER TABLE org_group_member FORCE  ROW LEVEL SECURITY;
DROP POLICY IF EXISTS org_group_member_tenant_isolation ON org_group_member;
CREATE POLICY org_group_member_tenant_isolation ON org_group_member
    USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
    WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);

ALTER TABLE org_role_permission ENABLE ROW LEVEL SECURITY;
ALTER TABLE org_role_permission FORCE  ROW LEVEL SECURITY;
DROP POLICY IF EXISTS org_role_permission_tenant_isolation ON org_role_permission;
CREATE POLICY org_role_permission_tenant_isolation ON org_role_permission
    USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
    WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);

ALTER TABLE org_settings ENABLE ROW LEVEL SECURITY;
ALTER TABLE org_settings FORCE  ROW LEVEL SECURITY;
DROP POLICY IF EXISTS org_settings_tenant_isolation ON org_settings;
CREATE POLICY org_settings_tenant_isolation ON org_settings
    USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
    WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);

ALTER TABLE pending_actions ENABLE ROW LEVEL SECURITY;
ALTER TABLE pending_actions FORCE  ROW LEVEL SECURITY;
DROP POLICY IF EXISTS pending_actions_tenant_isolation ON pending_actions;
CREATE POLICY pending_actions_tenant_isolation ON pending_actions
    USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
    WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);

ALTER TABLE pending_commit ENABLE ROW LEVEL SECURITY;
ALTER TABLE pending_commit FORCE  ROW LEVEL SECURITY;
DROP POLICY IF EXISTS pending_commit_tenant_isolation ON pending_commit;
CREATE POLICY pending_commit_tenant_isolation ON pending_commit
    USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
    WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);

ALTER TABLE person ENABLE ROW LEVEL SECURITY;
ALTER TABLE person FORCE  ROW LEVEL SECURITY;
DROP POLICY IF EXISTS person_tenant_isolation ON person;
CREATE POLICY person_tenant_isolation ON person
    USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
    WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);

ALTER TABLE plugins ENABLE ROW LEVEL SECURITY;
ALTER TABLE plugins FORCE  ROW LEVEL SECURITY;
DROP POLICY IF EXISTS plugins_tenant_isolation ON plugins;
CREATE POLICY plugins_tenant_isolation ON plugins
    USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
    WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);

ALTER TABLE pm_activities ENABLE ROW LEVEL SECURITY;
ALTER TABLE pm_activities FORCE  ROW LEVEL SECURITY;
DROP POLICY IF EXISTS pm_activities_tenant_isolation ON pm_activities;
CREATE POLICY pm_activities_tenant_isolation ON pm_activities
    USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
    WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);

ALTER TABLE pm_custom_fields ENABLE ROW LEVEL SECURITY;
ALTER TABLE pm_custom_fields FORCE  ROW LEVEL SECURITY;
DROP POLICY IF EXISTS pm_custom_fields_tenant_isolation ON pm_custom_fields;
CREATE POLICY pm_custom_fields_tenant_isolation ON pm_custom_fields
    USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
    WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);

ALTER TABLE pm_intake ENABLE ROW LEVEL SECURITY;
ALTER TABLE pm_intake FORCE  ROW LEVEL SECURITY;
DROP POLICY IF EXISTS pm_intake_tenant_isolation ON pm_intake;
CREATE POLICY pm_intake_tenant_isolation ON pm_intake
    USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
    WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);

ALTER TABLE pm_notifications ENABLE ROW LEVEL SECURITY;
ALTER TABLE pm_notifications FORCE  ROW LEVEL SECURITY;
DROP POLICY IF EXISTS pm_notifications_tenant_isolation ON pm_notifications;
CREATE POLICY pm_notifications_tenant_isolation ON pm_notifications
    USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
    WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);

ALTER TABLE pm_project_grants ENABLE ROW LEVEL SECURITY;
ALTER TABLE pm_project_grants FORCE  ROW LEVEL SECURITY;
DROP POLICY IF EXISTS pm_project_grants_tenant_isolation ON pm_project_grants;
CREATE POLICY pm_project_grants_tenant_isolation ON pm_project_grants
    USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
    WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);

ALTER TABLE pm_project_watchers ENABLE ROW LEVEL SECURITY;
ALTER TABLE pm_project_watchers FORCE  ROW LEVEL SECURITY;
DROP POLICY IF EXISTS pm_project_watchers_tenant_isolation ON pm_project_watchers;
CREATE POLICY pm_project_watchers_tenant_isolation ON pm_project_watchers
    USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
    WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);

ALTER TABLE pm_projects ENABLE ROW LEVEL SECURITY;
ALTER TABLE pm_projects FORCE  ROW LEVEL SECURITY;
DROP POLICY IF EXISTS pm_projects_tenant_isolation ON pm_projects;
CREATE POLICY pm_projects_tenant_isolation ON pm_projects
    USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
    WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);

ALTER TABLE pm_recurrences ENABLE ROW LEVEL SECURITY;
ALTER TABLE pm_recurrences FORCE  ROW LEVEL SECURITY;
DROP POLICY IF EXISTS pm_recurrences_tenant_isolation ON pm_recurrences;
CREATE POLICY pm_recurrences_tenant_isolation ON pm_recurrences
    USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
    WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);

ALTER TABLE pm_reports ENABLE ROW LEVEL SECURITY;
ALTER TABLE pm_reports FORCE  ROW LEVEL SECURITY;
DROP POLICY IF EXISTS pm_reports_tenant_isolation ON pm_reports;
CREATE POLICY pm_reports_tenant_isolation ON pm_reports
    USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
    WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);

ALTER TABLE pm_tags ENABLE ROW LEVEL SECURITY;
ALTER TABLE pm_tags FORCE  ROW LEVEL SECURITY;
DROP POLICY IF EXISTS pm_tags_tenant_isolation ON pm_tags;
CREATE POLICY pm_tags_tenant_isolation ON pm_tags
    USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
    WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);

ALTER TABLE pm_task_assignees ENABLE ROW LEVEL SECURITY;
ALTER TABLE pm_task_assignees FORCE  ROW LEVEL SECURITY;
DROP POLICY IF EXISTS pm_task_assignees_tenant_isolation ON pm_task_assignees;
CREATE POLICY pm_task_assignees_tenant_isolation ON pm_task_assignees
    USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
    WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);

ALTER TABLE pm_task_attachments ENABLE ROW LEVEL SECURITY;
ALTER TABLE pm_task_attachments FORCE  ROW LEVEL SECURITY;
DROP POLICY IF EXISTS pm_task_attachments_tenant_isolation ON pm_task_attachments;
CREATE POLICY pm_task_attachments_tenant_isolation ON pm_task_attachments
    USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
    WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);

ALTER TABLE pm_task_counters ENABLE ROW LEVEL SECURITY;
ALTER TABLE pm_task_counters FORCE  ROW LEVEL SECURITY;
DROP POLICY IF EXISTS pm_task_counters_tenant_isolation ON pm_task_counters;
CREATE POLICY pm_task_counters_tenant_isolation ON pm_task_counters
    USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
    WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);

ALTER TABLE pm_task_links ENABLE ROW LEVEL SECURITY;
ALTER TABLE pm_task_links FORCE  ROW LEVEL SECURITY;
DROP POLICY IF EXISTS pm_task_links_tenant_isolation ON pm_task_links;
CREATE POLICY pm_task_links_tenant_isolation ON pm_task_links
    USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
    WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);

ALTER TABLE pm_task_personal ENABLE ROW LEVEL SECURITY;
ALTER TABLE pm_task_personal FORCE  ROW LEVEL SECURITY;
DROP POLICY IF EXISTS pm_task_personal_tenant_isolation ON pm_task_personal;
CREATE POLICY pm_task_personal_tenant_isolation ON pm_task_personal
    USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
    WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);

ALTER TABLE pm_task_statuses ENABLE ROW LEVEL SECURITY;
ALTER TABLE pm_task_statuses FORCE  ROW LEVEL SECURITY;
DROP POLICY IF EXISTS pm_task_statuses_tenant_isolation ON pm_task_statuses;
CREATE POLICY pm_task_statuses_tenant_isolation ON pm_task_statuses
    USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
    WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);

ALTER TABLE pm_task_tombstones ENABLE ROW LEVEL SECURITY;
ALTER TABLE pm_task_tombstones FORCE  ROW LEVEL SECURITY;
DROP POLICY IF EXISTS pm_task_tombstones_tenant_isolation ON pm_task_tombstones;
CREATE POLICY pm_task_tombstones_tenant_isolation ON pm_task_tombstones
    USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
    WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);

ALTER TABLE pm_task_types ENABLE ROW LEVEL SECURITY;
ALTER TABLE pm_task_types FORCE  ROW LEVEL SECURITY;
DROP POLICY IF EXISTS pm_task_types_tenant_isolation ON pm_task_types;
CREATE POLICY pm_task_types_tenant_isolation ON pm_task_types
    USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
    WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);

ALTER TABLE pm_task_watchers ENABLE ROW LEVEL SECURITY;
ALTER TABLE pm_task_watchers FORCE  ROW LEVEL SECURITY;
DROP POLICY IF EXISTS pm_task_watchers_tenant_isolation ON pm_task_watchers;
CREATE POLICY pm_task_watchers_tenant_isolation ON pm_task_watchers
    USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
    WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);

ALTER TABLE pm_tasks ENABLE ROW LEVEL SECURITY;
ALTER TABLE pm_tasks FORCE  ROW LEVEL SECURITY;
DROP POLICY IF EXISTS pm_tasks_tenant_isolation ON pm_tasks;
CREATE POLICY pm_tasks_tenant_isolation ON pm_tasks
    USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
    WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);

ALTER TABLE pm_view_task_positions ENABLE ROW LEVEL SECURITY;
ALTER TABLE pm_view_task_positions FORCE  ROW LEVEL SECURITY;
DROP POLICY IF EXISTS pm_view_task_positions_tenant_isolation ON pm_view_task_positions;
CREATE POLICY pm_view_task_positions_tenant_isolation ON pm_view_task_positions
    USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
    WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);

ALTER TABLE pm_view_user_state ENABLE ROW LEVEL SECURITY;
ALTER TABLE pm_view_user_state FORCE  ROW LEVEL SECURITY;
DROP POLICY IF EXISTS pm_view_user_state_tenant_isolation ON pm_view_user_state;
CREATE POLICY pm_view_user_state_tenant_isolation ON pm_view_user_state
    USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
    WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);

ALTER TABLE pm_views ENABLE ROW LEVEL SECURITY;
ALTER TABLE pm_views FORCE  ROW LEVEL SECURITY;
DROP POLICY IF EXISTS pm_views_tenant_isolation ON pm_views;
CREATE POLICY pm_views_tenant_isolation ON pm_views
    USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
    WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);

ALTER TABLE project ENABLE ROW LEVEL SECURITY;
ALTER TABLE project FORCE  ROW LEVEL SECURITY;
DROP POLICY IF EXISTS project_tenant_isolation ON project;
CREATE POLICY project_tenant_isolation ON project
    USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
    WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);

ALTER TABLE summary_run ENABLE ROW LEVEL SECURITY;
ALTER TABLE summary_run FORCE  ROW LEVEL SECURITY;
DROP POLICY IF EXISTS summary_run_tenant_isolation ON summary_run;
CREATE POLICY summary_run_tenant_isolation ON summary_run
    USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
    WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);

ALTER TABLE task ENABLE ROW LEVEL SECURITY;
ALTER TABLE task FORCE  ROW LEVEL SECURITY;
DROP POLICY IF EXISTS task_tenant_isolation ON task;
CREATE POLICY task_tenant_isolation ON task
    USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
    WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);

ALTER TABLE task_accounts ENABLE ROW LEVEL SECURITY;
ALTER TABLE task_accounts FORCE  ROW LEVEL SECURITY;
DROP POLICY IF EXISTS task_accounts_tenant_isolation ON task_accounts;
CREATE POLICY task_accounts_tenant_isolation ON task_accounts
    USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
    WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);

ALTER TABLE transcript_segment ENABLE ROW LEVEL SECURITY;
ALTER TABLE transcript_segment FORCE  ROW LEVEL SECURITY;
DROP POLICY IF EXISTS transcript_segment_tenant_isolation ON transcript_segment;
CREATE POLICY transcript_segment_tenant_isolation ON transcript_segment
    USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
    WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);

ALTER TABLE user_permission_override ENABLE ROW LEVEL SECURITY;
ALTER TABLE user_permission_override FORCE  ROW LEVEL SECURITY;
DROP POLICY IF EXISTS user_permission_override_tenant_isolation ON user_permission_override;
CREATE POLICY user_permission_override_tenant_isolation ON user_permission_override
    USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
    WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);

ALTER TABLE user_role ENABLE ROW LEVEL SECURITY;
ALTER TABLE user_role FORCE  ROW LEVEL SECURITY;
DROP POLICY IF EXISTS user_role_tenant_isolation ON user_role;
CREATE POLICY user_role_tenant_isolation ON user_role
    USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
    WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);

ALTER TABLE wa_accounts ENABLE ROW LEVEL SECURITY;
ALTER TABLE wa_accounts FORCE  ROW LEVEL SECURITY;
DROP POLICY IF EXISTS wa_accounts_tenant_isolation ON wa_accounts;
CREATE POLICY wa_accounts_tenant_isolation ON wa_accounts
    USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
    WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);

ALTER TABLE wa_ai_drafts ENABLE ROW LEVEL SECURITY;
ALTER TABLE wa_ai_drafts FORCE  ROW LEVEL SECURITY;
DROP POLICY IF EXISTS wa_ai_drafts_tenant_isolation ON wa_ai_drafts;
CREATE POLICY wa_ai_drafts_tenant_isolation ON wa_ai_drafts
    USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
    WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);

ALTER TABLE wa_categories ENABLE ROW LEVEL SECURITY;
ALTER TABLE wa_categories FORCE  ROW LEVEL SECURITY;
DROP POLICY IF EXISTS wa_categories_tenant_isolation ON wa_categories;
CREATE POLICY wa_categories_tenant_isolation ON wa_categories
    USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
    WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);

ALTER TABLE wa_chat_avatars ENABLE ROW LEVEL SECURITY;
ALTER TABLE wa_chat_avatars FORCE  ROW LEVEL SECURITY;
DROP POLICY IF EXISTS wa_chat_avatars_tenant_isolation ON wa_chat_avatars;
CREATE POLICY wa_chat_avatars_tenant_isolation ON wa_chat_avatars
    USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
    WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);

ALTER TABLE wa_chat_labels ENABLE ROW LEVEL SECURITY;
ALTER TABLE wa_chat_labels FORCE  ROW LEVEL SECURITY;
DROP POLICY IF EXISTS wa_chat_labels_tenant_isolation ON wa_chat_labels;
CREATE POLICY wa_chat_labels_tenant_isolation ON wa_chat_labels
    USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
    WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);

ALTER TABLE wa_chat_status ENABLE ROW LEVEL SECURITY;
ALTER TABLE wa_chat_status FORCE  ROW LEVEL SECURITY;
DROP POLICY IF EXISTS wa_chat_status_tenant_isolation ON wa_chat_status;
CREATE POLICY wa_chat_status_tenant_isolation ON wa_chat_status
    USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
    WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);

ALTER TABLE wa_chats ENABLE ROW LEVEL SECURITY;
ALTER TABLE wa_chats FORCE  ROW LEVEL SECURITY;
DROP POLICY IF EXISTS wa_chats_tenant_isolation ON wa_chats;
CREATE POLICY wa_chats_tenant_isolation ON wa_chats
    USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
    WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);

ALTER TABLE wa_commitments ENABLE ROW LEVEL SECURITY;
ALTER TABLE wa_commitments FORCE  ROW LEVEL SECURITY;
DROP POLICY IF EXISTS wa_commitments_tenant_isolation ON wa_commitments;
CREATE POLICY wa_commitments_tenant_isolation ON wa_commitments
    USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
    WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);

ALTER TABLE wa_contacts ENABLE ROW LEVEL SECURITY;
ALTER TABLE wa_contacts FORCE  ROW LEVEL SECURITY;
DROP POLICY IF EXISTS wa_contacts_tenant_isolation ON wa_contacts;
CREATE POLICY wa_contacts_tenant_isolation ON wa_contacts
    USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
    WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);

ALTER TABLE wa_group_summaries ENABLE ROW LEVEL SECURITY;
ALTER TABLE wa_group_summaries FORCE  ROW LEVEL SECURITY;
DROP POLICY IF EXISTS wa_group_summaries_tenant_isolation ON wa_group_summaries;
CREATE POLICY wa_group_summaries_tenant_isolation ON wa_group_summaries
    USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
    WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);

ALTER TABLE wa_labels ENABLE ROW LEVEL SECURITY;
ALTER TABLE wa_labels FORCE  ROW LEVEL SECURITY;
DROP POLICY IF EXISTS wa_labels_tenant_isolation ON wa_labels;
CREATE POLICY wa_labels_tenant_isolation ON wa_labels
    USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
    WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);

ALTER TABLE wa_media ENABLE ROW LEVEL SECURITY;
ALTER TABLE wa_media FORCE  ROW LEVEL SECURITY;
DROP POLICY IF EXISTS wa_media_tenant_isolation ON wa_media;
CREATE POLICY wa_media_tenant_isolation ON wa_media
    USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
    WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);

ALTER TABLE wa_message_embeddings ENABLE ROW LEVEL SECURITY;
ALTER TABLE wa_message_embeddings FORCE  ROW LEVEL SECURITY;
DROP POLICY IF EXISTS wa_message_embeddings_tenant_isolation ON wa_message_embeddings;
CREATE POLICY wa_message_embeddings_tenant_isolation ON wa_message_embeddings
    USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
    WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);

ALTER TABLE wa_messages ENABLE ROW LEVEL SECURITY;
ALTER TABLE wa_messages FORCE  ROW LEVEL SECURITY;
DROP POLICY IF EXISTS wa_messages_tenant_isolation ON wa_messages;
CREATE POLICY wa_messages_tenant_isolation ON wa_messages
    USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
    WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);

ALTER TABLE wa_saved_replies ENABLE ROW LEVEL SECURITY;
ALTER TABLE wa_saved_replies FORCE  ROW LEVEL SECURITY;
DROP POLICY IF EXISTS wa_saved_replies_tenant_isolation ON wa_saved_replies;
CREATE POLICY wa_saved_replies_tenant_isolation ON wa_saved_replies
    USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
    WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);

ALTER TABLE wa_sync_log ENABLE ROW LEVEL SECURITY;
ALTER TABLE wa_sync_log FORCE  ROW LEVEL SECURITY;
DROP POLICY IF EXISTS wa_sync_log_tenant_isolation ON wa_sync_log;
CREATE POLICY wa_sync_log_tenant_isolation ON wa_sync_log
    USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
    WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);

ALTER TABLE wa_templates ENABLE ROW LEVEL SECURITY;
ALTER TABLE wa_templates FORCE  ROW LEVEL SECURITY;
DROP POLICY IF EXISTS wa_templates_tenant_isolation ON wa_templates;
CREATE POLICY wa_templates_tenant_isolation ON wa_templates
    USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
    WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);

ALTER TABLE workflow_modules ENABLE ROW LEVEL SECURITY;
ALTER TABLE workflow_modules FORCE  ROW LEVEL SECURITY;
DROP POLICY IF EXISTS workflow_modules_tenant_isolation ON workflow_modules;
CREATE POLICY workflow_modules_tenant_isolation ON workflow_modules
    USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
    WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);

ALTER TABLE workflow_run_pauses ENABLE ROW LEVEL SECURITY;
ALTER TABLE workflow_run_pauses FORCE  ROW LEVEL SECURITY;
DROP POLICY IF EXISTS workflow_run_pauses_tenant_isolation ON workflow_run_pauses;
CREATE POLICY workflow_run_pauses_tenant_isolation ON workflow_run_pauses
    USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
    WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);

ALTER TABLE workflow_runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE workflow_runs FORCE  ROW LEVEL SECURITY;
DROP POLICY IF EXISTS workflow_runs_tenant_isolation ON workflow_runs;
CREATE POLICY workflow_runs_tenant_isolation ON workflow_runs
    USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
    WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);

ALTER TABLE workflow_triggers ENABLE ROW LEVEL SECURITY;
ALTER TABLE workflow_triggers FORCE  ROW LEVEL SECURITY;
DROP POLICY IF EXISTS workflow_triggers_tenant_isolation ON workflow_triggers;
CREATE POLICY workflow_triggers_tenant_isolation ON workflow_triggers
    USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
    WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);

ALTER TABLE workflow_versions ENABLE ROW LEVEL SECURITY;
ALTER TABLE workflow_versions FORCE  ROW LEVEL SECURITY;
DROP POLICY IF EXISTS workflow_versions_tenant_isolation ON workflow_versions;
CREATE POLICY workflow_versions_tenant_isolation ON workflow_versions
    USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
    WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);

ALTER TABLE workflows ENABLE ROW LEVEL SECURITY;
ALTER TABLE workflows FORCE  ROW LEVEL SECURITY;
DROP POLICY IF EXISTS workflows_tenant_isolation ON workflows;
CREATE POLICY workflows_tenant_isolation ON workflows
    USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
    WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);
