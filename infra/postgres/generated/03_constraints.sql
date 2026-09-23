-- ============================================================================
-- MT-1b · phase 3/4 constraints — GENERATED, DO NOT EDIT BY HAND
-- ============================================================================
-- Regenerate with: uv run python scripts/gen_tenant_migration.py
-- Spec: project-docs/specs/saas_multitenancy.md §1.3 · MT-1b · WS-29 · D15
--
-- SET NOT NULL + FK + index. ⚠️ THIS IS THE ACCESS EXCLUSIVE PHASE — it scans each table. Apply in a window, table by table if necessary, and never behind a long-running transaction (see the generator docstring: that is the exact shape of the 14h44m outage).
--
-- Tables in this phase: 143
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


-- access_request
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM access_request WHERE organization_id IS NULL) THEN
        RAISE EXCEPTION 'MT-1b: access_request still has unowned rows — run phase 2 (backfill) to completion first';
    END IF;
END $$;
ALTER TABLE access_request ALTER COLUMN organization_id SET NOT NULL;
ALTER TABLE access_request ADD CONSTRAINT access_request_org_fk
    FOREIGN KEY (organization_id) REFERENCES organization(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS access_request_org_idx ON access_request (organization_id);

-- action_item
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM action_item WHERE organization_id IS NULL) THEN
        RAISE EXCEPTION 'MT-1b: action_item still has unowned rows — run phase 2 (backfill) to completion first';
    END IF;
END $$;
ALTER TABLE action_item ALTER COLUMN organization_id SET NOT NULL;
ALTER TABLE action_item ADD CONSTRAINT action_item_org_fk
    FOREIGN KEY (organization_id) REFERENCES organization(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS action_item_org_idx ON action_item (organization_id);

-- agent_avatars
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM agent_avatars WHERE organization_id IS NULL) THEN
        RAISE EXCEPTION 'MT-1b: agent_avatars still has unowned rows — run phase 2 (backfill) to completion first';
    END IF;
END $$;
ALTER TABLE agent_avatars ALTER COLUMN organization_id SET NOT NULL;
ALTER TABLE agent_avatars ADD CONSTRAINT agent_avatars_org_fk
    FOREIGN KEY (organization_id) REFERENCES organization(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS agent_avatars_org_idx ON agent_avatars (organization_id);

-- agent_blob
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM agent_blob WHERE organization_id IS NULL) THEN
        RAISE EXCEPTION 'MT-1b: agent_blob still has unowned rows — run phase 2 (backfill) to completion first';
    END IF;
END $$;
ALTER TABLE agent_blob ALTER COLUMN organization_id SET NOT NULL;
ALTER TABLE agent_blob ADD CONSTRAINT agent_blob_org_fk
    FOREIGN KEY (organization_id) REFERENCES organization(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS agent_blob_org_idx ON agent_blob (organization_id);

-- agent_file_history
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM agent_file_history WHERE organization_id IS NULL) THEN
        RAISE EXCEPTION 'MT-1b: agent_file_history still has unowned rows — run phase 2 (backfill) to completion first';
    END IF;
END $$;
ALTER TABLE agent_file_history ALTER COLUMN organization_id SET NOT NULL;
ALTER TABLE agent_file_history ADD CONSTRAINT agent_file_history_org_fk
    FOREIGN KEY (organization_id) REFERENCES organization(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS agent_file_history_org_idx ON agent_file_history (organization_id);

-- agent_run
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM agent_run WHERE organization_id IS NULL) THEN
        RAISE EXCEPTION 'MT-1b: agent_run still has unowned rows — run phase 2 (backfill) to completion first';
    END IF;
END $$;
ALTER TABLE agent_run ALTER COLUMN organization_id SET NOT NULL;
ALTER TABLE agent_run ADD CONSTRAINT agent_run_org_fk
    FOREIGN KEY (organization_id) REFERENCES organization(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS agent_run_org_idx ON agent_run (organization_id);

-- agent_skill_setting
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM agent_skill_setting WHERE organization_id IS NULL) THEN
        RAISE EXCEPTION 'MT-1b: agent_skill_setting still has unowned rows — run phase 2 (backfill) to completion first';
    END IF;
END $$;
ALTER TABLE agent_skill_setting ALTER COLUMN organization_id SET NOT NULL;
ALTER TABLE agent_skill_setting ADD CONSTRAINT agent_skill_setting_org_fk
    FOREIGN KEY (organization_id) REFERENCES organization(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS agent_skill_setting_org_idx ON agent_skill_setting (organization_id);

-- app_audit
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM app_audit WHERE organization_id IS NULL) THEN
        RAISE EXCEPTION 'MT-1b: app_audit still has unowned rows — run phase 2 (backfill) to completion first';
    END IF;
END $$;
ALTER TABLE app_audit ALTER COLUMN organization_id SET NOT NULL;
ALTER TABLE app_audit ADD CONSTRAINT app_audit_org_fk
    FOREIGN KEY (organization_id) REFERENCES organization(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS app_audit_org_idx ON app_audit (organization_id);

-- app_data
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM app_data WHERE organization_id IS NULL) THEN
        RAISE EXCEPTION 'MT-1b: app_data still has unowned rows — run phase 2 (backfill) to completion first';
    END IF;
END $$;
ALTER TABLE app_data ALTER COLUMN organization_id SET NOT NULL;
ALTER TABLE app_data ADD CONSTRAINT app_data_org_fk
    FOREIGN KEY (organization_id) REFERENCES organization(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS app_data_org_idx ON app_data (organization_id);

-- app_files
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM app_files WHERE organization_id IS NULL) THEN
        RAISE EXCEPTION 'MT-1b: app_files still has unowned rows — run phase 2 (backfill) to completion first';
    END IF;
END $$;
ALTER TABLE app_files ALTER COLUMN organization_id SET NOT NULL;
ALTER TABLE app_files ADD CONSTRAINT app_files_org_fk
    FOREIGN KEY (organization_id) REFERENCES organization(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS app_files_org_idx ON app_files (organization_id);

-- app_grants
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM app_grants WHERE organization_id IS NULL) THEN
        RAISE EXCEPTION 'MT-1b: app_grants still has unowned rows — run phase 2 (backfill) to completion first';
    END IF;
END $$;
ALTER TABLE app_grants ALTER COLUMN organization_id SET NOT NULL;
ALTER TABLE app_grants ADD CONSTRAINT app_grants_org_fk
    FOREIGN KEY (organization_id) REFERENCES organization(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS app_grants_org_idx ON app_grants (organization_id);

-- app_pins
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM app_pins WHERE organization_id IS NULL) THEN
        RAISE EXCEPTION 'MT-1b: app_pins still has unowned rows — run phase 2 (backfill) to completion first';
    END IF;
END $$;
ALTER TABLE app_pins ALTER COLUMN organization_id SET NOT NULL;
ALTER TABLE app_pins ADD CONSTRAINT app_pins_org_fk
    FOREIGN KEY (organization_id) REFERENCES organization(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS app_pins_org_idx ON app_pins (organization_id);

-- app_tool_grants
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM app_tool_grants WHERE organization_id IS NULL) THEN
        RAISE EXCEPTION 'MT-1b: app_tool_grants still has unowned rows — run phase 2 (backfill) to completion first';
    END IF;
END $$;
ALTER TABLE app_tool_grants ALTER COLUMN organization_id SET NOT NULL;
ALTER TABLE app_tool_grants ADD CONSTRAINT app_tool_grants_org_fk
    FOREIGN KEY (organization_id) REFERENCES organization(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS app_tool_grants_org_idx ON app_tool_grants (organization_id);

-- app_user
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM app_user WHERE organization_id IS NULL) THEN
        RAISE EXCEPTION 'MT-1b: app_user still has unowned rows — run phase 2 (backfill) to completion first';
    END IF;
END $$;
ALTER TABLE app_user ALTER COLUMN organization_id SET NOT NULL;
ALTER TABLE app_user ADD CONSTRAINT app_user_org_fk
    FOREIGN KEY (organization_id) REFERENCES organization(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS app_user_org_idx ON app_user (organization_id);

-- app_versions
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM app_versions WHERE organization_id IS NULL) THEN
        RAISE EXCEPTION 'MT-1b: app_versions still has unowned rows — run phase 2 (backfill) to completion first';
    END IF;
END $$;
ALTER TABLE app_versions ALTER COLUMN organization_id SET NOT NULL;
ALTER TABLE app_versions ADD CONSTRAINT app_versions_org_fk
    FOREIGN KEY (organization_id) REFERENCES organization(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS app_versions_org_idx ON app_versions (organization_id);

-- apps
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM apps WHERE organization_id IS NULL) THEN
        RAISE EXCEPTION 'MT-1b: apps still has unowned rows — run phase 2 (backfill) to completion first';
    END IF;
END $$;
ALTER TABLE apps ALTER COLUMN organization_id SET NOT NULL;
ALTER TABLE apps ADD CONSTRAINT apps_org_fk
    FOREIGN KEY (organization_id) REFERENCES organization(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS apps_org_idx ON apps (organization_id);

-- audit_event
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM audit_event WHERE organization_id IS NULL) THEN
        RAISE EXCEPTION 'MT-1b: audit_event still has unowned rows — run phase 2 (backfill) to completion first';
    END IF;
END $$;
ALTER TABLE audit_event ALTER COLUMN organization_id SET NOT NULL;
ALTER TABLE audit_event ADD CONSTRAINT audit_event_org_fk
    FOREIGN KEY (organization_id) REFERENCES organization(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS audit_event_org_idx ON audit_event (organization_id);

-- chat_message
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM chat_message WHERE organization_id IS NULL) THEN
        RAISE EXCEPTION 'MT-1b: chat_message still has unowned rows — run phase 2 (backfill) to completion first';
    END IF;
END $$;
ALTER TABLE chat_message ALTER COLUMN organization_id SET NOT NULL;
ALTER TABLE chat_message ADD CONSTRAINT chat_message_org_fk
    FOREIGN KEY (organization_id) REFERENCES organization(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS chat_message_org_idx ON chat_message (organization_id);

-- chat_session
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM chat_session WHERE organization_id IS NULL) THEN
        RAISE EXCEPTION 'MT-1b: chat_session still has unowned rows — run phase 2 (backfill) to completion first';
    END IF;
END $$;
ALTER TABLE chat_session ALTER COLUMN organization_id SET NOT NULL;
ALTER TABLE chat_session ADD CONSTRAINT chat_session_org_fk
    FOREIGN KEY (organization_id) REFERENCES organization(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS chat_session_org_idx ON chat_session (organization_id);

-- chat_session_agent
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM chat_session_agent WHERE organization_id IS NULL) THEN
        RAISE EXCEPTION 'MT-1b: chat_session_agent still has unowned rows — run phase 2 (backfill) to completion first';
    END IF;
END $$;
ALTER TABLE chat_session_agent ALTER COLUMN organization_id SET NOT NULL;
ALTER TABLE chat_session_agent ADD CONSTRAINT chat_session_agent_org_fk
    FOREIGN KEY (organization_id) REFERENCES organization(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS chat_session_agent_org_idx ON chat_session_agent (organization_id);

-- chat_session_participant
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM chat_session_participant WHERE organization_id IS NULL) THEN
        RAISE EXCEPTION 'MT-1b: chat_session_participant still has unowned rows — run phase 2 (backfill) to completion first';
    END IF;
END $$;
ALTER TABLE chat_session_participant ALTER COLUMN organization_id SET NOT NULL;
ALTER TABLE chat_session_participant ADD CONSTRAINT chat_session_participant_org_fk
    FOREIGN KEY (organization_id) REFERENCES organization(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS chat_session_participant_org_idx ON chat_session_participant (organization_id);

-- copilot_config
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM copilot_config WHERE organization_id IS NULL) THEN
        RAISE EXCEPTION 'MT-1b: copilot_config still has unowned rows — run phase 2 (backfill) to completion first';
    END IF;
END $$;
ALTER TABLE copilot_config ALTER COLUMN organization_id SET NOT NULL;
ALTER TABLE copilot_config ADD CONSTRAINT copilot_config_org_fk
    FOREIGN KEY (organization_id) REFERENCES organization(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS copilot_config_org_idx ON copilot_config (organization_id);

-- copilot_event
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM copilot_event WHERE organization_id IS NULL) THEN
        RAISE EXCEPTION 'MT-1b: copilot_event still has unowned rows — run phase 2 (backfill) to completion first';
    END IF;
END $$;
ALTER TABLE copilot_event ALTER COLUMN organization_id SET NOT NULL;
ALTER TABLE copilot_event ADD CONSTRAINT copilot_event_org_fk
    FOREIGN KEY (organization_id) REFERENCES organization(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS copilot_event_org_idx ON copilot_event (organization_id);

-- crm_auto_lead_cursors
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM crm_auto_lead_cursors WHERE organization_id IS NULL) THEN
        RAISE EXCEPTION 'MT-1b: crm_auto_lead_cursors still has unowned rows — run phase 2 (backfill) to completion first';
    END IF;
END $$;
ALTER TABLE crm_auto_lead_cursors ALTER COLUMN organization_id SET NOT NULL;
ALTER TABLE crm_auto_lead_cursors ADD CONSTRAINT crm_auto_lead_cursors_org_fk
    FOREIGN KEY (organization_id) REFERENCES organization(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS crm_auto_lead_cursors_org_idx ON crm_auto_lead_cursors (organization_id);

-- crm_deal_contacts
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM crm_deal_contacts WHERE organization_id IS NULL) THEN
        RAISE EXCEPTION 'MT-1b: crm_deal_contacts still has unowned rows — run phase 2 (backfill) to completion first';
    END IF;
END $$;
ALTER TABLE crm_deal_contacts ALTER COLUMN organization_id SET NOT NULL;
ALTER TABLE crm_deal_contacts ADD CONSTRAINT crm_deal_contacts_org_fk
    FOREIGN KEY (organization_id) REFERENCES organization(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS crm_deal_contacts_org_idx ON crm_deal_contacts (organization_id);

-- crm_deal_statuses
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM crm_deal_statuses WHERE organization_id IS NULL) THEN
        RAISE EXCEPTION 'MT-1b: crm_deal_statuses still has unowned rows — run phase 2 (backfill) to completion first';
    END IF;
END $$;
ALTER TABLE crm_deal_statuses ALTER COLUMN organization_id SET NOT NULL;
ALTER TABLE crm_deal_statuses ADD CONSTRAINT crm_deal_statuses_org_fk
    FOREIGN KEY (organization_id) REFERENCES organization(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS crm_deal_statuses_org_idx ON crm_deal_statuses (organization_id);

-- crm_lead_statuses
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM crm_lead_statuses WHERE organization_id IS NULL) THEN
        RAISE EXCEPTION 'MT-1b: crm_lead_statuses still has unowned rows — run phase 2 (backfill) to completion first';
    END IF;
END $$;
ALTER TABLE crm_lead_statuses ALTER COLUMN organization_id SET NOT NULL;
ALTER TABLE crm_lead_statuses ADD CONSTRAINT crm_lead_statuses_org_fk
    FOREIGN KEY (organization_id) REFERENCES organization(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS crm_lead_statuses_org_idx ON crm_lead_statuses (organization_id);

-- crm_leads
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM crm_leads WHERE organization_id IS NULL) THEN
        RAISE EXCEPTION 'MT-1b: crm_leads still has unowned rows — run phase 2 (backfill) to completion first';
    END IF;
END $$;
ALTER TABLE crm_leads ALTER COLUMN organization_id SET NOT NULL;
ALTER TABLE crm_leads ADD CONSTRAINT crm_leads_org_fk
    FOREIGN KEY (organization_id) REFERENCES organization(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS crm_leads_org_idx ON crm_leads (organization_id);

-- crm_lost_reasons
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM crm_lost_reasons WHERE organization_id IS NULL) THEN
        RAISE EXCEPTION 'MT-1b: crm_lost_reasons still has unowned rows — run phase 2 (backfill) to completion first';
    END IF;
END $$;
ALTER TABLE crm_lost_reasons ALTER COLUMN organization_id SET NOT NULL;
ALTER TABLE crm_lost_reasons ADD CONSTRAINT crm_lost_reasons_org_fk
    FOREIGN KEY (organization_id) REFERENCES organization(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS crm_lost_reasons_org_idx ON crm_lost_reasons (organization_id);

-- crm_organizations
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM crm_organizations WHERE organization_id IS NULL) THEN
        RAISE EXCEPTION 'MT-1b: crm_organizations still has unowned rows — run phase 2 (backfill) to completion first';
    END IF;
END $$;
ALTER TABLE crm_organizations ALTER COLUMN organization_id SET NOT NULL;
ALTER TABLE crm_organizations ADD CONSTRAINT crm_organizations_org_fk
    FOREIGN KEY (organization_id) REFERENCES organization(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS crm_organizations_org_idx ON crm_organizations (organization_id);

-- crm_status_changes
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM crm_status_changes WHERE organization_id IS NULL) THEN
        RAISE EXCEPTION 'MT-1b: crm_status_changes still has unowned rows — run phase 2 (backfill) to completion first';
    END IF;
END $$;
ALTER TABLE crm_status_changes ALTER COLUMN organization_id SET NOT NULL;
ALTER TABLE crm_status_changes ADD CONSTRAINT crm_status_changes_org_fk
    FOREIGN KEY (organization_id) REFERENCES organization(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS crm_status_changes_org_idx ON crm_status_changes (organization_id);

-- crm_sync_cursors
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM crm_sync_cursors WHERE organization_id IS NULL) THEN
        RAISE EXCEPTION 'MT-1b: crm_sync_cursors still has unowned rows — run phase 2 (backfill) to completion first';
    END IF;
END $$;
ALTER TABLE crm_sync_cursors ALTER COLUMN organization_id SET NOT NULL;
ALTER TABLE crm_sync_cursors ADD CONSTRAINT crm_sync_cursors_org_fk
    FOREIGN KEY (organization_id) REFERENCES organization(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS crm_sync_cursors_org_idx ON crm_sync_cursors (organization_id);

-- crm_zoho_tombstones
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM crm_zoho_tombstones WHERE organization_id IS NULL) THEN
        RAISE EXCEPTION 'MT-1b: crm_zoho_tombstones still has unowned rows — run phase 2 (backfill) to completion first';
    END IF;
END $$;
ALTER TABLE crm_zoho_tombstones ALTER COLUMN organization_id SET NOT NULL;
ALTER TABLE crm_zoho_tombstones ADD CONSTRAINT crm_zoho_tombstones_org_fk
    FOREIGN KEY (organization_id) REFERENCES organization(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS crm_zoho_tombstones_org_idx ON crm_zoho_tombstones (organization_id);

-- custom_api_definitions
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM custom_api_definitions WHERE organization_id IS NULL) THEN
        RAISE EXCEPTION 'MT-1b: custom_api_definitions still has unowned rows — run phase 2 (backfill) to completion first';
    END IF;
END $$;
ALTER TABLE custom_api_definitions ALTER COLUMN organization_id SET NOT NULL;
ALTER TABLE custom_api_definitions ADD CONSTRAINT custom_api_definitions_org_fk
    FOREIGN KEY (organization_id) REFERENCES organization(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS custom_api_definitions_org_idx ON custom_api_definitions (organization_id);

-- customer
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM customer WHERE organization_id IS NULL) THEN
        RAISE EXCEPTION 'MT-1b: customer still has unowned rows — run phase 2 (backfill) to completion first';
    END IF;
END $$;
ALTER TABLE customer ALTER COLUMN organization_id SET NOT NULL;
ALTER TABLE customer ADD CONSTRAINT customer_org_fk
    FOREIGN KEY (organization_id) REFERENCES organization(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS customer_org_idx ON customer (organization_id);

-- deal
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM deal WHERE organization_id IS NULL) THEN
        RAISE EXCEPTION 'MT-1b: deal still has unowned rows — run phase 2 (backfill) to completion first';
    END IF;
END $$;
ALTER TABLE deal ALTER COLUMN organization_id SET NOT NULL;
ALTER TABLE deal ADD CONSTRAINT deal_org_fk
    FOREIGN KEY (organization_id) REFERENCES organization(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS deal_org_idx ON deal (organization_id);

-- dynamic_agents
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM dynamic_agents WHERE organization_id IS NULL) THEN
        RAISE EXCEPTION 'MT-1b: dynamic_agents still has unowned rows — run phase 2 (backfill) to completion first';
    END IF;
END $$;
ALTER TABLE dynamic_agents ALTER COLUMN organization_id SET NOT NULL;
ALTER TABLE dynamic_agents ADD CONSTRAINT dynamic_agents_org_fk
    FOREIGN KEY (organization_id) REFERENCES organization(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS dynamic_agents_org_idx ON dynamic_agents (organization_id);

-- email_accounts
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM email_accounts WHERE organization_id IS NULL) THEN
        RAISE EXCEPTION 'MT-1b: email_accounts still has unowned rows — run phase 2 (backfill) to completion first';
    END IF;
END $$;
ALTER TABLE email_accounts ALTER COLUMN organization_id SET NOT NULL;
ALTER TABLE email_accounts ADD CONSTRAINT email_accounts_org_fk
    FOREIGN KEY (organization_id) REFERENCES organization(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS email_accounts_org_idx ON email_accounts (organization_id);

-- email_actions
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM email_actions WHERE organization_id IS NULL) THEN
        RAISE EXCEPTION 'MT-1b: email_actions still has unowned rows — run phase 2 (backfill) to completion first';
    END IF;
END $$;
ALTER TABLE email_actions ALTER COLUMN organization_id SET NOT NULL;
ALTER TABLE email_actions ADD CONSTRAINT email_actions_org_fk
    FOREIGN KEY (organization_id) REFERENCES organization(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS email_actions_org_idx ON email_actions (organization_id);

-- email_ai_drafts
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM email_ai_drafts WHERE organization_id IS NULL) THEN
        RAISE EXCEPTION 'MT-1b: email_ai_drafts still has unowned rows — run phase 2 (backfill) to completion first';
    END IF;
END $$;
ALTER TABLE email_ai_drafts ALTER COLUMN organization_id SET NOT NULL;
ALTER TABLE email_ai_drafts ADD CONSTRAINT email_ai_drafts_org_fk
    FOREIGN KEY (organization_id) REFERENCES organization(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS email_ai_drafts_org_idx ON email_ai_drafts (organization_id);

-- email_assistant_settings
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM email_assistant_settings WHERE organization_id IS NULL) THEN
        RAISE EXCEPTION 'MT-1b: email_assistant_settings still has unowned rows — run phase 2 (backfill) to completion first';
    END IF;
END $$;
ALTER TABLE email_assistant_settings ALTER COLUMN organization_id SET NOT NULL;
ALTER TABLE email_assistant_settings ADD CONSTRAINT email_assistant_settings_org_fk
    FOREIGN KEY (organization_id) REFERENCES organization(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS email_assistant_settings_org_idx ON email_assistant_settings (organization_id);

-- email_attachments
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM email_attachments WHERE organization_id IS NULL) THEN
        RAISE EXCEPTION 'MT-1b: email_attachments still has unowned rows — run phase 2 (backfill) to completion first';
    END IF;
END $$;
ALTER TABLE email_attachments ALTER COLUMN organization_id SET NOT NULL;
ALTER TABLE email_attachments ADD CONSTRAINT email_attachments_org_fk
    FOREIGN KEY (organization_id) REFERENCES organization(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS email_attachments_org_idx ON email_attachments (organization_id);

-- email_cold_senders
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM email_cold_senders WHERE organization_id IS NULL) THEN
        RAISE EXCEPTION 'MT-1b: email_cold_senders still has unowned rows — run phase 2 (backfill) to completion first';
    END IF;
END $$;
ALTER TABLE email_cold_senders ALTER COLUMN organization_id SET NOT NULL;
ALTER TABLE email_cold_senders ADD CONSTRAINT email_cold_senders_org_fk
    FOREIGN KEY (organization_id) REFERENCES organization(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS email_cold_senders_org_idx ON email_cold_senders (organization_id);

-- email_contacts
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM email_contacts WHERE organization_id IS NULL) THEN
        RAISE EXCEPTION 'MT-1b: email_contacts still has unowned rows — run phase 2 (backfill) to completion first';
    END IF;
END $$;
ALTER TABLE email_contacts ALTER COLUMN organization_id SET NOT NULL;
ALTER TABLE email_contacts ADD CONSTRAINT email_contacts_org_fk
    FOREIGN KEY (organization_id) REFERENCES organization(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS email_contacts_org_idx ON email_contacts (organization_id);

-- email_embeddings
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM email_embeddings WHERE organization_id IS NULL) THEN
        RAISE EXCEPTION 'MT-1b: email_embeddings still has unowned rows — run phase 2 (backfill) to completion first';
    END IF;
END $$;
ALTER TABLE email_embeddings ALTER COLUMN organization_id SET NOT NULL;
ALTER TABLE email_embeddings ADD CONSTRAINT email_embeddings_org_fk
    FOREIGN KEY (organization_id) REFERENCES organization(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS email_embeddings_org_idx ON email_embeddings (organization_id);

-- email_executed_rules
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM email_executed_rules WHERE organization_id IS NULL) THEN
        RAISE EXCEPTION 'MT-1b: email_executed_rules still has unowned rows — run phase 2 (backfill) to completion first';
    END IF;
END $$;
ALTER TABLE email_executed_rules ALTER COLUMN organization_id SET NOT NULL;
ALTER TABLE email_executed_rules ADD CONSTRAINT email_executed_rules_org_fk
    FOREIGN KEY (organization_id) REFERENCES organization(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS email_executed_rules_org_idx ON email_executed_rules (organization_id);

-- email_folders
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM email_folders WHERE organization_id IS NULL) THEN
        RAISE EXCEPTION 'MT-1b: email_folders still has unowned rows — run phase 2 (backfill) to completion first';
    END IF;
END $$;
ALTER TABLE email_folders ALTER COLUMN organization_id SET NOT NULL;
ALTER TABLE email_folders ADD CONSTRAINT email_folders_org_fk
    FOREIGN KEY (organization_id) REFERENCES organization(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS email_folders_org_idx ON email_folders (organization_id);

-- email_knowledge
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM email_knowledge WHERE organization_id IS NULL) THEN
        RAISE EXCEPTION 'MT-1b: email_knowledge still has unowned rows — run phase 2 (backfill) to completion first';
    END IF;
END $$;
ALTER TABLE email_knowledge ALTER COLUMN organization_id SET NOT NULL;
ALTER TABLE email_knowledge ADD CONSTRAINT email_knowledge_org_fk
    FOREIGN KEY (organization_id) REFERENCES organization(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS email_knowledge_org_idx ON email_knowledge (organization_id);

-- email_learned_patterns
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM email_learned_patterns WHERE organization_id IS NULL) THEN
        RAISE EXCEPTION 'MT-1b: email_learned_patterns still has unowned rows — run phase 2 (backfill) to completion first';
    END IF;
END $$;
ALTER TABLE email_learned_patterns ALTER COLUMN organization_id SET NOT NULL;
ALTER TABLE email_learned_patterns ADD CONSTRAINT email_learned_patterns_org_fk
    FOREIGN KEY (organization_id) REFERENCES organization(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS email_learned_patterns_org_idx ON email_learned_patterns (organization_id);

-- email_messages
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM email_messages WHERE organization_id IS NULL) THEN
        RAISE EXCEPTION 'MT-1b: email_messages still has unowned rows — run phase 2 (backfill) to completion first';
    END IF;
END $$;
ALTER TABLE email_messages ALTER COLUMN organization_id SET NOT NULL;
ALTER TABLE email_messages ADD CONSTRAINT email_messages_org_fk
    FOREIGN KEY (organization_id) REFERENCES organization(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS email_messages_org_idx ON email_messages (organization_id);

-- email_newsletters
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM email_newsletters WHERE organization_id IS NULL) THEN
        RAISE EXCEPTION 'MT-1b: email_newsletters still has unowned rows — run phase 2 (backfill) to completion first';
    END IF;
END $$;
ALTER TABLE email_newsletters ALTER COLUMN organization_id SET NOT NULL;
ALTER TABLE email_newsletters ADD CONSTRAINT email_newsletters_org_fk
    FOREIGN KEY (organization_id) REFERENCES organization(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS email_newsletters_org_idx ON email_newsletters (organization_id);

-- email_rule_guidance
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM email_rule_guidance WHERE organization_id IS NULL) THEN
        RAISE EXCEPTION 'MT-1b: email_rule_guidance still has unowned rows — run phase 2 (backfill) to completion first';
    END IF;
END $$;
ALTER TABLE email_rule_guidance ALTER COLUMN organization_id SET NOT NULL;
ALTER TABLE email_rule_guidance ADD CONSTRAINT email_rule_guidance_org_fk
    FOREIGN KEY (organization_id) REFERENCES organization(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS email_rule_guidance_org_idx ON email_rule_guidance (organization_id);

-- email_rule_patterns
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM email_rule_patterns WHERE organization_id IS NULL) THEN
        RAISE EXCEPTION 'MT-1b: email_rule_patterns still has unowned rows — run phase 2 (backfill) to completion first';
    END IF;
END $$;
ALTER TABLE email_rule_patterns ALTER COLUMN organization_id SET NOT NULL;
ALTER TABLE email_rule_patterns ADD CONSTRAINT email_rule_patterns_org_fk
    FOREIGN KEY (organization_id) REFERENCES organization(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS email_rule_patterns_org_idx ON email_rule_patterns (organization_id);

-- email_rules
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM email_rules WHERE organization_id IS NULL) THEN
        RAISE EXCEPTION 'MT-1b: email_rules still has unowned rows — run phase 2 (backfill) to completion first';
    END IF;
END $$;
ALTER TABLE email_rules ALTER COLUMN organization_id SET NOT NULL;
ALTER TABLE email_rules ADD CONSTRAINT email_rules_org_fk
    FOREIGN KEY (organization_id) REFERENCES organization(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS email_rules_org_idx ON email_rules (organization_id);

-- email_senders
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM email_senders WHERE organization_id IS NULL) THEN
        RAISE EXCEPTION 'MT-1b: email_senders still has unowned rows — run phase 2 (backfill) to completion first';
    END IF;
END $$;
ALTER TABLE email_senders ALTER COLUMN organization_id SET NOT NULL;
ALTER TABLE email_senders ADD CONSTRAINT email_senders_org_fk
    FOREIGN KEY (organization_id) REFERENCES organization(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS email_senders_org_idx ON email_senders (organization_id);

-- email_sync_log
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM email_sync_log WHERE organization_id IS NULL) THEN
        RAISE EXCEPTION 'MT-1b: email_sync_log still has unowned rows — run phase 2 (backfill) to completion first';
    END IF;
END $$;
ALTER TABLE email_sync_log ALTER COLUMN organization_id SET NOT NULL;
ALTER TABLE email_sync_log ADD CONSTRAINT email_sync_log_org_fk
    FOREIGN KEY (organization_id) REFERENCES organization(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS email_sync_log_org_idx ON email_sync_log (organization_id);

-- email_thread_status
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM email_thread_status WHERE organization_id IS NULL) THEN
        RAISE EXCEPTION 'MT-1b: email_thread_status still has unowned rows — run phase 2 (backfill) to completion first';
    END IF;
END $$;
ALTER TABLE email_thread_status ALTER COLUMN organization_id SET NOT NULL;
ALTER TABLE email_thread_status ADD CONSTRAINT email_thread_status_org_fk
    FOREIGN KEY (organization_id) REFERENCES organization(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS email_thread_status_org_idx ON email_thread_status (organization_id);

-- email_voice_profiles
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM email_voice_profiles WHERE organization_id IS NULL) THEN
        RAISE EXCEPTION 'MT-1b: email_voice_profiles still has unowned rows — run phase 2 (backfill) to completion first';
    END IF;
END $$;
ALTER TABLE email_voice_profiles ALTER COLUMN organization_id SET NOT NULL;
ALTER TABLE email_voice_profiles ADD CONSTRAINT email_voice_profiles_org_fk
    FOREIGN KEY (organization_id) REFERENCES organization(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS email_voice_profiles_org_idx ON email_voice_profiles (organization_id);

-- attachments
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM attachments WHERE organization_id IS NULL) THEN
        RAISE EXCEPTION 'MT-1b: attachments still has unowned rows — run phase 2 (backfill) to completion first';
    END IF;
END $$;
ALTER TABLE attachments ALTER COLUMN organization_id SET NOT NULL;
ALTER TABLE attachments ADD CONSTRAINT gtd_attachments_org_fk
    FOREIGN KEY (organization_id) REFERENCES organization(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS gtd_attachments_org_idx ON attachments (organization_id);

-- gtd_contexts
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM gtd_contexts WHERE organization_id IS NULL) THEN
        RAISE EXCEPTION 'MT-1b: gtd_contexts still has unowned rows — run phase 2 (backfill) to completion first';
    END IF;
END $$;
ALTER TABLE gtd_contexts ALTER COLUMN organization_id SET NOT NULL;
ALTER TABLE gtd_contexts ADD CONSTRAINT gtd_contexts_org_fk
    FOREIGN KEY (organization_id) REFERENCES organization(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS gtd_contexts_org_idx ON gtd_contexts (organization_id);

-- calendar_day_state
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM calendar_day_state WHERE organization_id IS NULL) THEN
        RAISE EXCEPTION 'MT-1b: calendar_day_state still has unowned rows — run phase 2 (backfill) to completion first';
    END IF;
END $$;
ALTER TABLE calendar_day_state ALTER COLUMN organization_id SET NOT NULL;
ALTER TABLE calendar_day_state ADD CONSTRAINT gtd_day_state_org_fk
    FOREIGN KEY (organization_id) REFERENCES organization(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS gtd_day_state_org_idx ON calendar_day_state (organization_id);

-- gtd_folders
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM gtd_folders WHERE organization_id IS NULL) THEN
        RAISE EXCEPTION 'MT-1b: gtd_folders still has unowned rows — run phase 2 (backfill) to completion first';
    END IF;
END $$;
ALTER TABLE gtd_folders ALTER COLUMN organization_id SET NOT NULL;
ALTER TABLE gtd_folders ADD CONSTRAINT gtd_folders_org_fk
    FOREIGN KEY (organization_id) REFERENCES organization(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS gtd_folders_org_idx ON gtd_folders (organization_id);

-- my_tasks_horizons
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM my_tasks_horizons WHERE organization_id IS NULL) THEN
        RAISE EXCEPTION 'MT-1b: my_tasks_horizons still has unowned rows — run phase 2 (backfill) to completion first';
    END IF;
END $$;
ALTER TABLE my_tasks_horizons ALTER COLUMN organization_id SET NOT NULL;
ALTER TABLE my_tasks_horizons ADD CONSTRAINT gtd_horizons_org_fk
    FOREIGN KEY (organization_id) REFERENCES organization(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS gtd_horizons_org_idx ON my_tasks_horizons (organization_id);

-- gtd_items
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM gtd_items WHERE organization_id IS NULL) THEN
        RAISE EXCEPTION 'MT-1b: gtd_items still has unowned rows — run phase 2 (backfill) to completion first';
    END IF;
END $$;
ALTER TABLE gtd_items ALTER COLUMN organization_id SET NOT NULL;
ALTER TABLE gtd_items ADD CONSTRAINT gtd_items_org_fk
    FOREIGN KEY (organization_id) REFERENCES organization(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS gtd_items_org_idx ON gtd_items (organization_id);

-- people
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM people WHERE organization_id IS NULL) THEN
        RAISE EXCEPTION 'MT-1b: people still has unowned rows — run phase 2 (backfill) to completion first';
    END IF;
END $$;
ALTER TABLE people ALTER COLUMN organization_id SET NOT NULL;
ALTER TABLE people ADD CONSTRAINT gtd_people_org_fk
    FOREIGN KEY (organization_id) REFERENCES organization(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS gtd_people_org_idx ON people (organization_id);

-- people_absences
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM people_absences WHERE organization_id IS NULL) THEN
        RAISE EXCEPTION 'MT-1b: people_absences still has unowned rows — run phase 2 (backfill) to completion first';
    END IF;
END $$;
ALTER TABLE people_absences ALTER COLUMN organization_id SET NOT NULL;
ALTER TABLE people_absences ADD CONSTRAINT gtd_person_absences_org_fk
    FOREIGN KEY (organization_id) REFERENCES organization(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS gtd_person_absences_org_idx ON people_absences (organization_id);

-- people_credentials
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM people_credentials WHERE organization_id IS NULL) THEN
        RAISE EXCEPTION 'MT-1b: people_credentials still has unowned rows — run phase 2 (backfill) to completion first';
    END IF;
END $$;
ALTER TABLE people_credentials ALTER COLUMN organization_id SET NOT NULL;
ALTER TABLE people_credentials ADD CONSTRAINT gtd_person_credentials_org_fk
    FOREIGN KEY (organization_id) REFERENCES organization(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS gtd_person_credentials_org_idx ON people_credentials (organization_id);

-- people_resumes
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM people_resumes WHERE organization_id IS NULL) THEN
        RAISE EXCEPTION 'MT-1b: people_resumes still has unowned rows — run phase 2 (backfill) to completion first';
    END IF;
END $$;
ALTER TABLE people_resumes ALTER COLUMN organization_id SET NOT NULL;
ALTER TABLE people_resumes ADD CONSTRAINT gtd_person_resumes_org_fk
    FOREIGN KEY (organization_id) REFERENCES organization(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS gtd_person_resumes_org_idx ON people_resumes (organization_id);

-- people_skills
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM people_skills WHERE organization_id IS NULL) THEN
        RAISE EXCEPTION 'MT-1b: people_skills still has unowned rows — run phase 2 (backfill) to completion first';
    END IF;
END $$;
ALTER TABLE people_skills ALTER COLUMN organization_id SET NOT NULL;
ALTER TABLE people_skills ADD CONSTRAINT gtd_person_skills_org_fk
    FOREIGN KEY (organization_id) REFERENCES organization(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS gtd_person_skills_org_idx ON people_skills (organization_id);

-- gtd_projects
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM gtd_projects WHERE organization_id IS NULL) THEN
        RAISE EXCEPTION 'MT-1b: gtd_projects still has unowned rows — run phase 2 (backfill) to completion first';
    END IF;
END $$;
ALTER TABLE gtd_projects ALTER COLUMN organization_id SET NOT NULL;
ALTER TABLE gtd_projects ADD CONSTRAINT gtd_projects_org_fk
    FOREIGN KEY (organization_id) REFERENCES organization(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS gtd_projects_org_idx ON gtd_projects (organization_id);

-- my_tasks_reviews
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM my_tasks_reviews WHERE organization_id IS NULL) THEN
        RAISE EXCEPTION 'MT-1b: my_tasks_reviews still has unowned rows — run phase 2 (backfill) to completion first';
    END IF;
END $$;
ALTER TABLE my_tasks_reviews ALTER COLUMN organization_id SET NOT NULL;
ALTER TABLE my_tasks_reviews ADD CONSTRAINT gtd_reviews_org_fk
    FOREIGN KEY (organization_id) REFERENCES organization(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS gtd_reviews_org_idx ON my_tasks_reviews (organization_id);

-- calendar_rollover_log
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM calendar_rollover_log WHERE organization_id IS NULL) THEN
        RAISE EXCEPTION 'MT-1b: calendar_rollover_log still has unowned rows — run phase 2 (backfill) to completion first';
    END IF;
END $$;
ALTER TABLE calendar_rollover_log ALTER COLUMN organization_id SET NOT NULL;
ALTER TABLE calendar_rollover_log ADD CONSTRAINT gtd_rollover_log_org_fk
    FOREIGN KEY (organization_id) REFERENCES organization(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS gtd_rollover_log_org_idx ON calendar_rollover_log (organization_id);

-- user_settings
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM user_settings WHERE organization_id IS NULL) THEN
        RAISE EXCEPTION 'MT-1b: user_settings still has unowned rows — run phase 2 (backfill) to completion first';
    END IF;
END $$;
ALTER TABLE user_settings ALTER COLUMN organization_id SET NOT NULL;
ALTER TABLE user_settings ADD CONSTRAINT gtd_settings_org_fk
    FOREIGN KEY (organization_id) REFERENCES organization(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS gtd_settings_org_idx ON user_settings (organization_id);

-- gtd_spaces
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM gtd_spaces WHERE organization_id IS NULL) THEN
        RAISE EXCEPTION 'MT-1b: gtd_spaces still has unowned rows — run phase 2 (backfill) to completion first';
    END IF;
END $$;
ALTER TABLE gtd_spaces ALTER COLUMN organization_id SET NOT NULL;
ALTER TABLE gtd_spaces ADD CONSTRAINT gtd_spaces_org_fk
    FOREIGN KEY (organization_id) REFERENCES organization(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS gtd_spaces_org_idx ON gtd_spaces (organization_id);

-- gtd_waiting
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM gtd_waiting WHERE organization_id IS NULL) THEN
        RAISE EXCEPTION 'MT-1b: gtd_waiting still has unowned rows — run phase 2 (backfill) to completion first';
    END IF;
END $$;
ALTER TABLE gtd_waiting ALTER COLUMN organization_id SET NOT NULL;
ALTER TABLE gtd_waiting ADD CONSTRAINT gtd_waiting_org_fk
    FOREIGN KEY (organization_id) REFERENCES organization(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS gtd_waiting_org_idx ON gtd_waiting (organization_id);

-- live_session
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM live_session WHERE organization_id IS NULL) THEN
        RAISE EXCEPTION 'MT-1b: live_session still has unowned rows — run phase 2 (backfill) to completion first';
    END IF;
END $$;
ALTER TABLE live_session ALTER COLUMN organization_id SET NOT NULL;
ALTER TABLE live_session ADD CONSTRAINT live_session_org_fk
    FOREIGN KEY (organization_id) REFERENCES organization(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS live_session_org_idx ON live_session (organization_id);

-- meeting
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM meeting WHERE organization_id IS NULL) THEN
        RAISE EXCEPTION 'MT-1b: meeting still has unowned rows — run phase 2 (backfill) to completion first';
    END IF;
END $$;
ALTER TABLE meeting ALTER COLUMN organization_id SET NOT NULL;
ALTER TABLE meeting ADD CONSTRAINT meeting_org_fk
    FOREIGN KEY (organization_id) REFERENCES organization(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS meeting_org_idx ON meeting (organization_id);

-- meeting_bot
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM meeting_bot WHERE organization_id IS NULL) THEN
        RAISE EXCEPTION 'MT-1b: meeting_bot still has unowned rows — run phase 2 (backfill) to completion first';
    END IF;
END $$;
ALTER TABLE meeting_bot ALTER COLUMN organization_id SET NOT NULL;
ALTER TABLE meeting_bot ADD CONSTRAINT meeting_bot_org_fk
    FOREIGN KEY (organization_id) REFERENCES organization(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS meeting_bot_org_idx ON meeting_bot (organization_id);

-- meeting_note
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM meeting_note WHERE organization_id IS NULL) THEN
        RAISE EXCEPTION 'MT-1b: meeting_note still has unowned rows — run phase 2 (backfill) to completion first';
    END IF;
END $$;
ALTER TABLE meeting_note ALTER COLUMN organization_id SET NOT NULL;
ALTER TABLE meeting_note ADD CONSTRAINT meeting_note_org_fk
    FOREIGN KEY (organization_id) REFERENCES organization(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS meeting_note_org_idx ON meeting_note (organization_id);

-- meeting_recording
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM meeting_recording WHERE organization_id IS NULL) THEN
        RAISE EXCEPTION 'MT-1b: meeting_recording still has unowned rows — run phase 2 (backfill) to completion first';
    END IF;
END $$;
ALTER TABLE meeting_recording ALTER COLUMN organization_id SET NOT NULL;
ALTER TABLE meeting_recording ADD CONSTRAINT meeting_recording_org_fk
    FOREIGN KEY (organization_id) REFERENCES organization(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS meeting_recording_org_idx ON meeting_recording (organization_id);

-- message
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM message WHERE organization_id IS NULL) THEN
        RAISE EXCEPTION 'MT-1b: message still has unowned rows — run phase 2 (backfill) to completion first';
    END IF;
END $$;
ALTER TABLE message ALTER COLUMN organization_id SET NOT NULL;
ALTER TABLE message ADD CONSTRAINT message_org_fk
    FOREIGN KEY (organization_id) REFERENCES organization(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS message_org_idx ON message (organization_id);

-- notes_glossary
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM notes_glossary WHERE organization_id IS NULL) THEN
        RAISE EXCEPTION 'MT-1b: notes_glossary still has unowned rows — run phase 2 (backfill) to completion first';
    END IF;
END $$;
ALTER TABLE notes_glossary ALTER COLUMN organization_id SET NOT NULL;
ALTER TABLE notes_glossary ADD CONSTRAINT notes_glossary_org_fk
    FOREIGN KEY (organization_id) REFERENCES organization(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS notes_glossary_org_idx ON notes_glossary (organization_id);

-- org_group_member
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM org_group_member WHERE organization_id IS NULL) THEN
        RAISE EXCEPTION 'MT-1b: org_group_member still has unowned rows — run phase 2 (backfill) to completion first';
    END IF;
END $$;
ALTER TABLE org_group_member ALTER COLUMN organization_id SET NOT NULL;
ALTER TABLE org_group_member ADD CONSTRAINT org_group_member_org_fk
    FOREIGN KEY (organization_id) REFERENCES organization(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS org_group_member_org_idx ON org_group_member (organization_id);

-- org_role_permission
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM org_role_permission WHERE organization_id IS NULL) THEN
        RAISE EXCEPTION 'MT-1b: org_role_permission still has unowned rows — run phase 2 (backfill) to completion first';
    END IF;
END $$;
ALTER TABLE org_role_permission ALTER COLUMN organization_id SET NOT NULL;
ALTER TABLE org_role_permission ADD CONSTRAINT org_role_permission_org_fk
    FOREIGN KEY (organization_id) REFERENCES organization(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS org_role_permission_org_idx ON org_role_permission (organization_id);

-- org_settings
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM org_settings WHERE organization_id IS NULL) THEN
        RAISE EXCEPTION 'MT-1b: org_settings still has unowned rows — run phase 2 (backfill) to completion first';
    END IF;
END $$;
ALTER TABLE org_settings ALTER COLUMN organization_id SET NOT NULL;
ALTER TABLE org_settings ADD CONSTRAINT org_settings_org_fk
    FOREIGN KEY (organization_id) REFERENCES organization(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS org_settings_org_idx ON org_settings (organization_id);

-- pending_actions
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pending_actions WHERE organization_id IS NULL) THEN
        RAISE EXCEPTION 'MT-1b: pending_actions still has unowned rows — run phase 2 (backfill) to completion first';
    END IF;
END $$;
ALTER TABLE pending_actions ALTER COLUMN organization_id SET NOT NULL;
ALTER TABLE pending_actions ADD CONSTRAINT pending_actions_org_fk
    FOREIGN KEY (organization_id) REFERENCES organization(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS pending_actions_org_idx ON pending_actions (organization_id);

-- pending_commit
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pending_commit WHERE organization_id IS NULL) THEN
        RAISE EXCEPTION 'MT-1b: pending_commit still has unowned rows — run phase 2 (backfill) to completion first';
    END IF;
END $$;
ALTER TABLE pending_commit ALTER COLUMN organization_id SET NOT NULL;
ALTER TABLE pending_commit ADD CONSTRAINT pending_commit_org_fk
    FOREIGN KEY (organization_id) REFERENCES organization(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS pending_commit_org_idx ON pending_commit (organization_id);

-- person
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM person WHERE organization_id IS NULL) THEN
        RAISE EXCEPTION 'MT-1b: person still has unowned rows — run phase 2 (backfill) to completion first';
    END IF;
END $$;
ALTER TABLE person ALTER COLUMN organization_id SET NOT NULL;
ALTER TABLE person ADD CONSTRAINT person_org_fk
    FOREIGN KEY (organization_id) REFERENCES organization(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS person_org_idx ON person (organization_id);

-- plugins
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM plugins WHERE organization_id IS NULL) THEN
        RAISE EXCEPTION 'MT-1b: plugins still has unowned rows — run phase 2 (backfill) to completion first';
    END IF;
END $$;
ALTER TABLE plugins ALTER COLUMN organization_id SET NOT NULL;
ALTER TABLE plugins ADD CONSTRAINT plugins_org_fk
    FOREIGN KEY (organization_id) REFERENCES organization(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS plugins_org_idx ON plugins (organization_id);

-- pm_activities
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pm_activities WHERE organization_id IS NULL) THEN
        RAISE EXCEPTION 'MT-1b: pm_activities still has unowned rows — run phase 2 (backfill) to completion first';
    END IF;
END $$;
ALTER TABLE pm_activities ALTER COLUMN organization_id SET NOT NULL;
ALTER TABLE pm_activities ADD CONSTRAINT pm_activities_org_fk
    FOREIGN KEY (organization_id) REFERENCES organization(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS pm_activities_org_idx ON pm_activities (organization_id);

-- pm_custom_fields
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pm_custom_fields WHERE organization_id IS NULL) THEN
        RAISE EXCEPTION 'MT-1b: pm_custom_fields still has unowned rows — run phase 2 (backfill) to completion first';
    END IF;
END $$;
ALTER TABLE pm_custom_fields ALTER COLUMN organization_id SET NOT NULL;
ALTER TABLE pm_custom_fields ADD CONSTRAINT pm_custom_fields_org_fk
    FOREIGN KEY (organization_id) REFERENCES organization(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS pm_custom_fields_org_idx ON pm_custom_fields (organization_id);

-- pm_intake
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pm_intake WHERE organization_id IS NULL) THEN
        RAISE EXCEPTION 'MT-1b: pm_intake still has unowned rows — run phase 2 (backfill) to completion first';
    END IF;
END $$;
ALTER TABLE pm_intake ALTER COLUMN organization_id SET NOT NULL;
ALTER TABLE pm_intake ADD CONSTRAINT pm_intake_org_fk
    FOREIGN KEY (organization_id) REFERENCES organization(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS pm_intake_org_idx ON pm_intake (organization_id);

-- pm_notifications
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pm_notifications WHERE organization_id IS NULL) THEN
        RAISE EXCEPTION 'MT-1b: pm_notifications still has unowned rows — run phase 2 (backfill) to completion first';
    END IF;
END $$;
ALTER TABLE pm_notifications ALTER COLUMN organization_id SET NOT NULL;
ALTER TABLE pm_notifications ADD CONSTRAINT pm_notifications_org_fk
    FOREIGN KEY (organization_id) REFERENCES organization(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS pm_notifications_org_idx ON pm_notifications (organization_id);

-- pm_project_grants
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pm_project_grants WHERE organization_id IS NULL) THEN
        RAISE EXCEPTION 'MT-1b: pm_project_grants still has unowned rows — run phase 2 (backfill) to completion first';
    END IF;
END $$;
ALTER TABLE pm_project_grants ALTER COLUMN organization_id SET NOT NULL;
ALTER TABLE pm_project_grants ADD CONSTRAINT pm_project_grants_org_fk
    FOREIGN KEY (organization_id) REFERENCES organization(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS pm_project_grants_org_idx ON pm_project_grants (organization_id);

-- pm_project_watchers
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pm_project_watchers WHERE organization_id IS NULL) THEN
        RAISE EXCEPTION 'MT-1b: pm_project_watchers still has unowned rows — run phase 2 (backfill) to completion first';
    END IF;
END $$;
ALTER TABLE pm_project_watchers ALTER COLUMN organization_id SET NOT NULL;
ALTER TABLE pm_project_watchers ADD CONSTRAINT pm_project_watchers_org_fk
    FOREIGN KEY (organization_id) REFERENCES organization(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS pm_project_watchers_org_idx ON pm_project_watchers (organization_id);

-- pm_projects
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pm_projects WHERE organization_id IS NULL) THEN
        RAISE EXCEPTION 'MT-1b: pm_projects still has unowned rows — run phase 2 (backfill) to completion first';
    END IF;
END $$;
ALTER TABLE pm_projects ALTER COLUMN organization_id SET NOT NULL;
ALTER TABLE pm_projects ADD CONSTRAINT pm_projects_org_fk
    FOREIGN KEY (organization_id) REFERENCES organization(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS pm_projects_org_idx ON pm_projects (organization_id);

-- pm_recurrences
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pm_recurrences WHERE organization_id IS NULL) THEN
        RAISE EXCEPTION 'MT-1b: pm_recurrences still has unowned rows — run phase 2 (backfill) to completion first';
    END IF;
END $$;
ALTER TABLE pm_recurrences ALTER COLUMN organization_id SET NOT NULL;
ALTER TABLE pm_recurrences ADD CONSTRAINT pm_recurrences_org_fk
    FOREIGN KEY (organization_id) REFERENCES organization(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS pm_recurrences_org_idx ON pm_recurrences (organization_id);

-- pm_report_recipients
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pm_report_recipients WHERE organization_id IS NULL) THEN
        RAISE EXCEPTION 'MT-1b: pm_report_recipients still has unowned rows — run phase 2 (backfill) to completion first';
    END IF;
END $$;
ALTER TABLE pm_report_recipients ALTER COLUMN organization_id SET NOT NULL;
ALTER TABLE pm_report_recipients ADD CONSTRAINT pm_report_recipients_org_fk
    FOREIGN KEY (organization_id) REFERENCES organization(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS pm_report_recipients_org_idx ON pm_report_recipients (organization_id);

-- pm_reports
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pm_reports WHERE organization_id IS NULL) THEN
        RAISE EXCEPTION 'MT-1b: pm_reports still has unowned rows — run phase 2 (backfill) to completion first';
    END IF;
END $$;
ALTER TABLE pm_reports ALTER COLUMN organization_id SET NOT NULL;
ALTER TABLE pm_reports ADD CONSTRAINT pm_reports_org_fk
    FOREIGN KEY (organization_id) REFERENCES organization(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS pm_reports_org_idx ON pm_reports (organization_id);

-- pm_tags
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pm_tags WHERE organization_id IS NULL) THEN
        RAISE EXCEPTION 'MT-1b: pm_tags still has unowned rows — run phase 2 (backfill) to completion first';
    END IF;
END $$;
ALTER TABLE pm_tags ALTER COLUMN organization_id SET NOT NULL;
ALTER TABLE pm_tags ADD CONSTRAINT pm_tags_org_fk
    FOREIGN KEY (organization_id) REFERENCES organization(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS pm_tags_org_idx ON pm_tags (organization_id);

-- pm_task_assignees
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pm_task_assignees WHERE organization_id IS NULL) THEN
        RAISE EXCEPTION 'MT-1b: pm_task_assignees still has unowned rows — run phase 2 (backfill) to completion first';
    END IF;
END $$;
ALTER TABLE pm_task_assignees ALTER COLUMN organization_id SET NOT NULL;
ALTER TABLE pm_task_assignees ADD CONSTRAINT pm_task_assignees_org_fk
    FOREIGN KEY (organization_id) REFERENCES organization(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS pm_task_assignees_org_idx ON pm_task_assignees (organization_id);

-- pm_task_attachments
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pm_task_attachments WHERE organization_id IS NULL) THEN
        RAISE EXCEPTION 'MT-1b: pm_task_attachments still has unowned rows — run phase 2 (backfill) to completion first';
    END IF;
END $$;
ALTER TABLE pm_task_attachments ALTER COLUMN organization_id SET NOT NULL;
ALTER TABLE pm_task_attachments ADD CONSTRAINT pm_task_attachments_org_fk
    FOREIGN KEY (organization_id) REFERENCES organization(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS pm_task_attachments_org_idx ON pm_task_attachments (organization_id);

-- pm_task_counters
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pm_task_counters WHERE organization_id IS NULL) THEN
        RAISE EXCEPTION 'MT-1b: pm_task_counters still has unowned rows — run phase 2 (backfill) to completion first';
    END IF;
END $$;
ALTER TABLE pm_task_counters ALTER COLUMN organization_id SET NOT NULL;
ALTER TABLE pm_task_counters ADD CONSTRAINT pm_task_counters_org_fk
    FOREIGN KEY (organization_id) REFERENCES organization(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS pm_task_counters_org_idx ON pm_task_counters (organization_id);

-- pm_task_links
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pm_task_links WHERE organization_id IS NULL) THEN
        RAISE EXCEPTION 'MT-1b: pm_task_links still has unowned rows — run phase 2 (backfill) to completion first';
    END IF;
END $$;
ALTER TABLE pm_task_links ALTER COLUMN organization_id SET NOT NULL;
ALTER TABLE pm_task_links ADD CONSTRAINT pm_task_links_org_fk
    FOREIGN KEY (organization_id) REFERENCES organization(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS pm_task_links_org_idx ON pm_task_links (organization_id);

-- pm_task_personal
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pm_task_personal WHERE organization_id IS NULL) THEN
        RAISE EXCEPTION 'MT-1b: pm_task_personal still has unowned rows — run phase 2 (backfill) to completion first';
    END IF;
END $$;
ALTER TABLE pm_task_personal ALTER COLUMN organization_id SET NOT NULL;
ALTER TABLE pm_task_personal ADD CONSTRAINT pm_task_personal_org_fk
    FOREIGN KEY (organization_id) REFERENCES organization(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS pm_task_personal_org_idx ON pm_task_personal (organization_id);

-- pm_task_statuses
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pm_task_statuses WHERE organization_id IS NULL) THEN
        RAISE EXCEPTION 'MT-1b: pm_task_statuses still has unowned rows — run phase 2 (backfill) to completion first';
    END IF;
END $$;
ALTER TABLE pm_task_statuses ALTER COLUMN organization_id SET NOT NULL;
ALTER TABLE pm_task_statuses ADD CONSTRAINT pm_task_statuses_org_fk
    FOREIGN KEY (organization_id) REFERENCES organization(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS pm_task_statuses_org_idx ON pm_task_statuses (organization_id);

-- pm_task_tombstones
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pm_task_tombstones WHERE organization_id IS NULL) THEN
        RAISE EXCEPTION 'MT-1b: pm_task_tombstones still has unowned rows — run phase 2 (backfill) to completion first';
    END IF;
END $$;
ALTER TABLE pm_task_tombstones ALTER COLUMN organization_id SET NOT NULL;
ALTER TABLE pm_task_tombstones ADD CONSTRAINT pm_task_tombstones_org_fk
    FOREIGN KEY (organization_id) REFERENCES organization(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS pm_task_tombstones_org_idx ON pm_task_tombstones (organization_id);

-- pm_task_types
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pm_task_types WHERE organization_id IS NULL) THEN
        RAISE EXCEPTION 'MT-1b: pm_task_types still has unowned rows — run phase 2 (backfill) to completion first';
    END IF;
END $$;
ALTER TABLE pm_task_types ALTER COLUMN organization_id SET NOT NULL;
ALTER TABLE pm_task_types ADD CONSTRAINT pm_task_types_org_fk
    FOREIGN KEY (organization_id) REFERENCES organization(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS pm_task_types_org_idx ON pm_task_types (organization_id);

-- pm_task_watchers
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pm_task_watchers WHERE organization_id IS NULL) THEN
        RAISE EXCEPTION 'MT-1b: pm_task_watchers still has unowned rows — run phase 2 (backfill) to completion first';
    END IF;
END $$;
ALTER TABLE pm_task_watchers ALTER COLUMN organization_id SET NOT NULL;
ALTER TABLE pm_task_watchers ADD CONSTRAINT pm_task_watchers_org_fk
    FOREIGN KEY (organization_id) REFERENCES organization(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS pm_task_watchers_org_idx ON pm_task_watchers (organization_id);

-- pm_tasks
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pm_tasks WHERE organization_id IS NULL) THEN
        RAISE EXCEPTION 'MT-1b: pm_tasks still has unowned rows — run phase 2 (backfill) to completion first';
    END IF;
END $$;
ALTER TABLE pm_tasks ALTER COLUMN organization_id SET NOT NULL;
ALTER TABLE pm_tasks ADD CONSTRAINT pm_tasks_org_fk
    FOREIGN KEY (organization_id) REFERENCES organization(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS pm_tasks_org_idx ON pm_tasks (organization_id);

-- pm_view_task_positions
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pm_view_task_positions WHERE organization_id IS NULL) THEN
        RAISE EXCEPTION 'MT-1b: pm_view_task_positions still has unowned rows — run phase 2 (backfill) to completion first';
    END IF;
END $$;
ALTER TABLE pm_view_task_positions ALTER COLUMN organization_id SET NOT NULL;
ALTER TABLE pm_view_task_positions ADD CONSTRAINT pm_view_task_positions_org_fk
    FOREIGN KEY (organization_id) REFERENCES organization(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS pm_view_task_positions_org_idx ON pm_view_task_positions (organization_id);

-- pm_view_user_state
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pm_view_user_state WHERE organization_id IS NULL) THEN
        RAISE EXCEPTION 'MT-1b: pm_view_user_state still has unowned rows — run phase 2 (backfill) to completion first';
    END IF;
END $$;
ALTER TABLE pm_view_user_state ALTER COLUMN organization_id SET NOT NULL;
ALTER TABLE pm_view_user_state ADD CONSTRAINT pm_view_user_state_org_fk
    FOREIGN KEY (organization_id) REFERENCES organization(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS pm_view_user_state_org_idx ON pm_view_user_state (organization_id);

-- pm_views
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pm_views WHERE organization_id IS NULL) THEN
        RAISE EXCEPTION 'MT-1b: pm_views still has unowned rows — run phase 2 (backfill) to completion first';
    END IF;
END $$;
ALTER TABLE pm_views ALTER COLUMN organization_id SET NOT NULL;
ALTER TABLE pm_views ADD CONSTRAINT pm_views_org_fk
    FOREIGN KEY (organization_id) REFERENCES organization(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS pm_views_org_idx ON pm_views (organization_id);

-- project
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM project WHERE organization_id IS NULL) THEN
        RAISE EXCEPTION 'MT-1b: project still has unowned rows — run phase 2 (backfill) to completion first';
    END IF;
END $$;
ALTER TABLE project ALTER COLUMN organization_id SET NOT NULL;
ALTER TABLE project ADD CONSTRAINT project_org_fk
    FOREIGN KEY (organization_id) REFERENCES organization(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS project_org_idx ON project (organization_id);

-- summary_run
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM summary_run WHERE organization_id IS NULL) THEN
        RAISE EXCEPTION 'MT-1b: summary_run still has unowned rows — run phase 2 (backfill) to completion first';
    END IF;
END $$;
ALTER TABLE summary_run ALTER COLUMN organization_id SET NOT NULL;
ALTER TABLE summary_run ADD CONSTRAINT summary_run_org_fk
    FOREIGN KEY (organization_id) REFERENCES organization(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS summary_run_org_idx ON summary_run (organization_id);

-- task
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM task WHERE organization_id IS NULL) THEN
        RAISE EXCEPTION 'MT-1b: task still has unowned rows — run phase 2 (backfill) to completion first';
    END IF;
END $$;
ALTER TABLE task ALTER COLUMN organization_id SET NOT NULL;
ALTER TABLE task ADD CONSTRAINT task_org_fk
    FOREIGN KEY (organization_id) REFERENCES organization(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS task_org_idx ON task (organization_id);

-- task_accounts
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM task_accounts WHERE organization_id IS NULL) THEN
        RAISE EXCEPTION 'MT-1b: task_accounts still has unowned rows — run phase 2 (backfill) to completion first';
    END IF;
END $$;
ALTER TABLE task_accounts ALTER COLUMN organization_id SET NOT NULL;
ALTER TABLE task_accounts ADD CONSTRAINT task_accounts_org_fk
    FOREIGN KEY (organization_id) REFERENCES organization(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS task_accounts_org_idx ON task_accounts (organization_id);

-- transcript_segment
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM transcript_segment WHERE organization_id IS NULL) THEN
        RAISE EXCEPTION 'MT-1b: transcript_segment still has unowned rows — run phase 2 (backfill) to completion first';
    END IF;
END $$;
ALTER TABLE transcript_segment ALTER COLUMN organization_id SET NOT NULL;
ALTER TABLE transcript_segment ADD CONSTRAINT transcript_segment_org_fk
    FOREIGN KEY (organization_id) REFERENCES organization(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS transcript_segment_org_idx ON transcript_segment (organization_id);

-- user_permission_override
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM user_permission_override WHERE organization_id IS NULL) THEN
        RAISE EXCEPTION 'MT-1b: user_permission_override still has unowned rows — run phase 2 (backfill) to completion first';
    END IF;
END $$;
ALTER TABLE user_permission_override ALTER COLUMN organization_id SET NOT NULL;
ALTER TABLE user_permission_override ADD CONSTRAINT user_permission_override_org_fk
    FOREIGN KEY (organization_id) REFERENCES organization(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS user_permission_override_org_idx ON user_permission_override (organization_id);

-- user_role
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM user_role WHERE organization_id IS NULL) THEN
        RAISE EXCEPTION 'MT-1b: user_role still has unowned rows — run phase 2 (backfill) to completion first';
    END IF;
END $$;
ALTER TABLE user_role ALTER COLUMN organization_id SET NOT NULL;
ALTER TABLE user_role ADD CONSTRAINT user_role_org_fk
    FOREIGN KEY (organization_id) REFERENCES organization(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS user_role_org_idx ON user_role (organization_id);

-- wa_accounts
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM wa_accounts WHERE organization_id IS NULL) THEN
        RAISE EXCEPTION 'MT-1b: wa_accounts still has unowned rows — run phase 2 (backfill) to completion first';
    END IF;
END $$;
ALTER TABLE wa_accounts ALTER COLUMN organization_id SET NOT NULL;
ALTER TABLE wa_accounts ADD CONSTRAINT wa_accounts_org_fk
    FOREIGN KEY (organization_id) REFERENCES organization(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS wa_accounts_org_idx ON wa_accounts (organization_id);

-- wa_ai_drafts
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM wa_ai_drafts WHERE organization_id IS NULL) THEN
        RAISE EXCEPTION 'MT-1b: wa_ai_drafts still has unowned rows — run phase 2 (backfill) to completion first';
    END IF;
END $$;
ALTER TABLE wa_ai_drafts ALTER COLUMN organization_id SET NOT NULL;
ALTER TABLE wa_ai_drafts ADD CONSTRAINT wa_ai_drafts_org_fk
    FOREIGN KEY (organization_id) REFERENCES organization(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS wa_ai_drafts_org_idx ON wa_ai_drafts (organization_id);

-- wa_categories
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM wa_categories WHERE organization_id IS NULL) THEN
        RAISE EXCEPTION 'MT-1b: wa_categories still has unowned rows — run phase 2 (backfill) to completion first';
    END IF;
END $$;
ALTER TABLE wa_categories ALTER COLUMN organization_id SET NOT NULL;
ALTER TABLE wa_categories ADD CONSTRAINT wa_categories_org_fk
    FOREIGN KEY (organization_id) REFERENCES organization(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS wa_categories_org_idx ON wa_categories (organization_id);

-- wa_chat_avatars
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM wa_chat_avatars WHERE organization_id IS NULL) THEN
        RAISE EXCEPTION 'MT-1b: wa_chat_avatars still has unowned rows — run phase 2 (backfill) to completion first';
    END IF;
END $$;
ALTER TABLE wa_chat_avatars ALTER COLUMN organization_id SET NOT NULL;
ALTER TABLE wa_chat_avatars ADD CONSTRAINT wa_chat_avatars_org_fk
    FOREIGN KEY (organization_id) REFERENCES organization(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS wa_chat_avatars_org_idx ON wa_chat_avatars (organization_id);

-- wa_chat_labels
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM wa_chat_labels WHERE organization_id IS NULL) THEN
        RAISE EXCEPTION 'MT-1b: wa_chat_labels still has unowned rows — run phase 2 (backfill) to completion first';
    END IF;
END $$;
ALTER TABLE wa_chat_labels ALTER COLUMN organization_id SET NOT NULL;
ALTER TABLE wa_chat_labels ADD CONSTRAINT wa_chat_labels_org_fk
    FOREIGN KEY (organization_id) REFERENCES organization(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS wa_chat_labels_org_idx ON wa_chat_labels (organization_id);

-- wa_chat_status
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM wa_chat_status WHERE organization_id IS NULL) THEN
        RAISE EXCEPTION 'MT-1b: wa_chat_status still has unowned rows — run phase 2 (backfill) to completion first';
    END IF;
END $$;
ALTER TABLE wa_chat_status ALTER COLUMN organization_id SET NOT NULL;
ALTER TABLE wa_chat_status ADD CONSTRAINT wa_chat_status_org_fk
    FOREIGN KEY (organization_id) REFERENCES organization(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS wa_chat_status_org_idx ON wa_chat_status (organization_id);

-- wa_chats
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM wa_chats WHERE organization_id IS NULL) THEN
        RAISE EXCEPTION 'MT-1b: wa_chats still has unowned rows — run phase 2 (backfill) to completion first';
    END IF;
END $$;
ALTER TABLE wa_chats ALTER COLUMN organization_id SET NOT NULL;
ALTER TABLE wa_chats ADD CONSTRAINT wa_chats_org_fk
    FOREIGN KEY (organization_id) REFERENCES organization(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS wa_chats_org_idx ON wa_chats (organization_id);

-- wa_commitments
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM wa_commitments WHERE organization_id IS NULL) THEN
        RAISE EXCEPTION 'MT-1b: wa_commitments still has unowned rows — run phase 2 (backfill) to completion first';
    END IF;
END $$;
ALTER TABLE wa_commitments ALTER COLUMN organization_id SET NOT NULL;
ALTER TABLE wa_commitments ADD CONSTRAINT wa_commitments_org_fk
    FOREIGN KEY (organization_id) REFERENCES organization(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS wa_commitments_org_idx ON wa_commitments (organization_id);

-- wa_contacts
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM wa_contacts WHERE organization_id IS NULL) THEN
        RAISE EXCEPTION 'MT-1b: wa_contacts still has unowned rows — run phase 2 (backfill) to completion first';
    END IF;
END $$;
ALTER TABLE wa_contacts ALTER COLUMN organization_id SET NOT NULL;
ALTER TABLE wa_contacts ADD CONSTRAINT wa_contacts_org_fk
    FOREIGN KEY (organization_id) REFERENCES organization(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS wa_contacts_org_idx ON wa_contacts (organization_id);

-- wa_group_summaries
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM wa_group_summaries WHERE organization_id IS NULL) THEN
        RAISE EXCEPTION 'MT-1b: wa_group_summaries still has unowned rows — run phase 2 (backfill) to completion first';
    END IF;
END $$;
ALTER TABLE wa_group_summaries ALTER COLUMN organization_id SET NOT NULL;
ALTER TABLE wa_group_summaries ADD CONSTRAINT wa_group_summaries_org_fk
    FOREIGN KEY (organization_id) REFERENCES organization(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS wa_group_summaries_org_idx ON wa_group_summaries (organization_id);

-- wa_labels
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM wa_labels WHERE organization_id IS NULL) THEN
        RAISE EXCEPTION 'MT-1b: wa_labels still has unowned rows — run phase 2 (backfill) to completion first';
    END IF;
END $$;
ALTER TABLE wa_labels ALTER COLUMN organization_id SET NOT NULL;
ALTER TABLE wa_labels ADD CONSTRAINT wa_labels_org_fk
    FOREIGN KEY (organization_id) REFERENCES organization(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS wa_labels_org_idx ON wa_labels (organization_id);

-- wa_media
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM wa_media WHERE organization_id IS NULL) THEN
        RAISE EXCEPTION 'MT-1b: wa_media still has unowned rows — run phase 2 (backfill) to completion first';
    END IF;
END $$;
ALTER TABLE wa_media ALTER COLUMN organization_id SET NOT NULL;
ALTER TABLE wa_media ADD CONSTRAINT wa_media_org_fk
    FOREIGN KEY (organization_id) REFERENCES organization(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS wa_media_org_idx ON wa_media (organization_id);

-- wa_message_embeddings
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM wa_message_embeddings WHERE organization_id IS NULL) THEN
        RAISE EXCEPTION 'MT-1b: wa_message_embeddings still has unowned rows — run phase 2 (backfill) to completion first';
    END IF;
END $$;
ALTER TABLE wa_message_embeddings ALTER COLUMN organization_id SET NOT NULL;
ALTER TABLE wa_message_embeddings ADD CONSTRAINT wa_message_embeddings_org_fk
    FOREIGN KEY (organization_id) REFERENCES organization(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS wa_message_embeddings_org_idx ON wa_message_embeddings (organization_id);

-- wa_messages
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM wa_messages WHERE organization_id IS NULL) THEN
        RAISE EXCEPTION 'MT-1b: wa_messages still has unowned rows — run phase 2 (backfill) to completion first';
    END IF;
END $$;
ALTER TABLE wa_messages ALTER COLUMN organization_id SET NOT NULL;
ALTER TABLE wa_messages ADD CONSTRAINT wa_messages_org_fk
    FOREIGN KEY (organization_id) REFERENCES organization(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS wa_messages_org_idx ON wa_messages (organization_id);

-- wa_saved_replies
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM wa_saved_replies WHERE organization_id IS NULL) THEN
        RAISE EXCEPTION 'MT-1b: wa_saved_replies still has unowned rows — run phase 2 (backfill) to completion first';
    END IF;
END $$;
ALTER TABLE wa_saved_replies ALTER COLUMN organization_id SET NOT NULL;
ALTER TABLE wa_saved_replies ADD CONSTRAINT wa_saved_replies_org_fk
    FOREIGN KEY (organization_id) REFERENCES organization(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS wa_saved_replies_org_idx ON wa_saved_replies (organization_id);

-- wa_sync_log
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM wa_sync_log WHERE organization_id IS NULL) THEN
        RAISE EXCEPTION 'MT-1b: wa_sync_log still has unowned rows — run phase 2 (backfill) to completion first';
    END IF;
END $$;
ALTER TABLE wa_sync_log ALTER COLUMN organization_id SET NOT NULL;
ALTER TABLE wa_sync_log ADD CONSTRAINT wa_sync_log_org_fk
    FOREIGN KEY (organization_id) REFERENCES organization(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS wa_sync_log_org_idx ON wa_sync_log (organization_id);

-- wa_templates
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM wa_templates WHERE organization_id IS NULL) THEN
        RAISE EXCEPTION 'MT-1b: wa_templates still has unowned rows — run phase 2 (backfill) to completion first';
    END IF;
END $$;
ALTER TABLE wa_templates ALTER COLUMN organization_id SET NOT NULL;
ALTER TABLE wa_templates ADD CONSTRAINT wa_templates_org_fk
    FOREIGN KEY (organization_id) REFERENCES organization(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS wa_templates_org_idx ON wa_templates (organization_id);

-- workflow_modules
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM workflow_modules WHERE organization_id IS NULL) THEN
        RAISE EXCEPTION 'MT-1b: workflow_modules still has unowned rows — run phase 2 (backfill) to completion first';
    END IF;
END $$;
ALTER TABLE workflow_modules ALTER COLUMN organization_id SET NOT NULL;
ALTER TABLE workflow_modules ADD CONSTRAINT workflow_modules_org_fk
    FOREIGN KEY (organization_id) REFERENCES organization(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS workflow_modules_org_idx ON workflow_modules (organization_id);

-- workflow_run_pauses
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM workflow_run_pauses WHERE organization_id IS NULL) THEN
        RAISE EXCEPTION 'MT-1b: workflow_run_pauses still has unowned rows — run phase 2 (backfill) to completion first';
    END IF;
END $$;
ALTER TABLE workflow_run_pauses ALTER COLUMN organization_id SET NOT NULL;
ALTER TABLE workflow_run_pauses ADD CONSTRAINT workflow_run_pauses_org_fk
    FOREIGN KEY (organization_id) REFERENCES organization(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS workflow_run_pauses_org_idx ON workflow_run_pauses (organization_id);

-- workflow_runs
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM workflow_runs WHERE organization_id IS NULL) THEN
        RAISE EXCEPTION 'MT-1b: workflow_runs still has unowned rows — run phase 2 (backfill) to completion first';
    END IF;
END $$;
ALTER TABLE workflow_runs ALTER COLUMN organization_id SET NOT NULL;
ALTER TABLE workflow_runs ADD CONSTRAINT workflow_runs_org_fk
    FOREIGN KEY (organization_id) REFERENCES organization(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS workflow_runs_org_idx ON workflow_runs (organization_id);

-- workflow_triggers
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM workflow_triggers WHERE organization_id IS NULL) THEN
        RAISE EXCEPTION 'MT-1b: workflow_triggers still has unowned rows — run phase 2 (backfill) to completion first';
    END IF;
END $$;
ALTER TABLE workflow_triggers ALTER COLUMN organization_id SET NOT NULL;
ALTER TABLE workflow_triggers ADD CONSTRAINT workflow_triggers_org_fk
    FOREIGN KEY (organization_id) REFERENCES organization(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS workflow_triggers_org_idx ON workflow_triggers (organization_id);

-- workflow_versions
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM workflow_versions WHERE organization_id IS NULL) THEN
        RAISE EXCEPTION 'MT-1b: workflow_versions still has unowned rows — run phase 2 (backfill) to completion first';
    END IF;
END $$;
ALTER TABLE workflow_versions ALTER COLUMN organization_id SET NOT NULL;
ALTER TABLE workflow_versions ADD CONSTRAINT workflow_versions_org_fk
    FOREIGN KEY (organization_id) REFERENCES organization(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS workflow_versions_org_idx ON workflow_versions (organization_id);

-- workflows
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM workflows WHERE organization_id IS NULL) THEN
        RAISE EXCEPTION 'MT-1b: workflows still has unowned rows — run phase 2 (backfill) to completion first';
    END IF;
END $$;
ALTER TABLE workflows ALTER COLUMN organization_id SET NOT NULL;
ALTER TABLE workflows ADD CONSTRAINT workflows_org_fk
    FOREIGN KEY (organization_id) REFERENCES organization(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS workflows_org_idx ON workflows (organization_id);
