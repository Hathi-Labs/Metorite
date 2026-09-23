-- ============================================================================
-- MT-1b · phase 1/4 add_columns — GENERATED, DO NOT EDIT BY HAND
-- ============================================================================
-- Regenerate with: uv run python scripts/gen_tenant_migration.py
-- Spec: project-docs/specs/saas_multitenancy.md §1.3 · MT-1b · WS-29 · D15
--
-- Nullable ADD COLUMN. No table scan, no lock of consequence. Safe to apply on a live system.
--
-- Tables in this phase: 137
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


ALTER TABLE access_request
    ADD COLUMN IF NOT EXISTS organization_id UUID
    DEFAULT current_setting('app.tenant_id', true)::uuid;

ALTER TABLE action_item
    ADD COLUMN IF NOT EXISTS organization_id UUID
    DEFAULT current_setting('app.tenant_id', true)::uuid;

ALTER TABLE agent_avatars
    ADD COLUMN IF NOT EXISTS organization_id UUID
    DEFAULT current_setting('app.tenant_id', true)::uuid;

ALTER TABLE agent_blob
    ADD COLUMN IF NOT EXISTS organization_id UUID
    DEFAULT current_setting('app.tenant_id', true)::uuid;

ALTER TABLE agent_file_history
    ADD COLUMN IF NOT EXISTS organization_id UUID
    DEFAULT current_setting('app.tenant_id', true)::uuid;

ALTER TABLE agent_run
    ADD COLUMN IF NOT EXISTS organization_id UUID
    DEFAULT current_setting('app.tenant_id', true)::uuid;

ALTER TABLE agent_skill_setting
    ADD COLUMN IF NOT EXISTS organization_id UUID
    DEFAULT current_setting('app.tenant_id', true)::uuid;

ALTER TABLE app_audit
    ADD COLUMN IF NOT EXISTS organization_id UUID
    DEFAULT current_setting('app.tenant_id', true)::uuid;

ALTER TABLE app_data
    ADD COLUMN IF NOT EXISTS organization_id UUID
    DEFAULT current_setting('app.tenant_id', true)::uuid;

ALTER TABLE app_files
    ADD COLUMN IF NOT EXISTS organization_id UUID
    DEFAULT current_setting('app.tenant_id', true)::uuid;

ALTER TABLE app_grants
    ADD COLUMN IF NOT EXISTS organization_id UUID
    DEFAULT current_setting('app.tenant_id', true)::uuid;

ALTER TABLE app_pins
    ADD COLUMN IF NOT EXISTS organization_id UUID
    DEFAULT current_setting('app.tenant_id', true)::uuid;

ALTER TABLE app_tool_grants
    ADD COLUMN IF NOT EXISTS organization_id UUID
    DEFAULT current_setting('app.tenant_id', true)::uuid;

ALTER TABLE app_user
    ADD COLUMN IF NOT EXISTS organization_id UUID
    DEFAULT current_setting('app.tenant_id', true)::uuid;

ALTER TABLE app_versions
    ADD COLUMN IF NOT EXISTS organization_id UUID
    DEFAULT current_setting('app.tenant_id', true)::uuid;

ALTER TABLE apps
    ADD COLUMN IF NOT EXISTS organization_id UUID
    DEFAULT current_setting('app.tenant_id', true)::uuid;

ALTER TABLE audit_event
    ADD COLUMN IF NOT EXISTS organization_id UUID
    DEFAULT current_setting('app.tenant_id', true)::uuid;

ALTER TABLE chat_message
    ADD COLUMN IF NOT EXISTS organization_id UUID
    DEFAULT current_setting('app.tenant_id', true)::uuid;

ALTER TABLE chat_session
    ADD COLUMN IF NOT EXISTS organization_id UUID
    DEFAULT current_setting('app.tenant_id', true)::uuid;

ALTER TABLE chat_session_agent
    ADD COLUMN IF NOT EXISTS organization_id UUID
    DEFAULT current_setting('app.tenant_id', true)::uuid;

ALTER TABLE chat_session_participant
    ADD COLUMN IF NOT EXISTS organization_id UUID
    DEFAULT current_setting('app.tenant_id', true)::uuid;

ALTER TABLE copilot_config
    ADD COLUMN IF NOT EXISTS organization_id UUID
    DEFAULT current_setting('app.tenant_id', true)::uuid;

ALTER TABLE copilot_event
    ADD COLUMN IF NOT EXISTS organization_id UUID
    DEFAULT current_setting('app.tenant_id', true)::uuid;

ALTER TABLE crm_auto_lead_cursors
    ADD COLUMN IF NOT EXISTS organization_id UUID
    DEFAULT current_setting('app.tenant_id', true)::uuid;

ALTER TABLE crm_deal_contacts
    ADD COLUMN IF NOT EXISTS organization_id UUID
    DEFAULT current_setting('app.tenant_id', true)::uuid;

ALTER TABLE crm_deal_statuses
    ADD COLUMN IF NOT EXISTS organization_id UUID
    DEFAULT current_setting('app.tenant_id', true)::uuid;

ALTER TABLE crm_lead_statuses
    ADD COLUMN IF NOT EXISTS organization_id UUID
    DEFAULT current_setting('app.tenant_id', true)::uuid;

ALTER TABLE crm_leads
    ADD COLUMN IF NOT EXISTS organization_id UUID
    DEFAULT current_setting('app.tenant_id', true)::uuid;

ALTER TABLE crm_lost_reasons
    ADD COLUMN IF NOT EXISTS organization_id UUID
    DEFAULT current_setting('app.tenant_id', true)::uuid;

ALTER TABLE crm_organizations
    ADD COLUMN IF NOT EXISTS organization_id UUID
    DEFAULT current_setting('app.tenant_id', true)::uuid;

ALTER TABLE crm_status_changes
    ADD COLUMN IF NOT EXISTS organization_id UUID
    DEFAULT current_setting('app.tenant_id', true)::uuid;

ALTER TABLE crm_sync_cursors
    ADD COLUMN IF NOT EXISTS organization_id UUID
    DEFAULT current_setting('app.tenant_id', true)::uuid;

ALTER TABLE crm_zoho_tombstones
    ADD COLUMN IF NOT EXISTS organization_id UUID
    DEFAULT current_setting('app.tenant_id', true)::uuid;

ALTER TABLE custom_api_definitions
    ADD COLUMN IF NOT EXISTS organization_id UUID
    DEFAULT current_setting('app.tenant_id', true)::uuid;

ALTER TABLE customer
    ADD COLUMN IF NOT EXISTS organization_id UUID
    DEFAULT current_setting('app.tenant_id', true)::uuid;

ALTER TABLE deal
    ADD COLUMN IF NOT EXISTS organization_id UUID
    DEFAULT current_setting('app.tenant_id', true)::uuid;

ALTER TABLE dynamic_agents
    ADD COLUMN IF NOT EXISTS organization_id UUID
    DEFAULT current_setting('app.tenant_id', true)::uuid;

ALTER TABLE email_accounts
    ADD COLUMN IF NOT EXISTS organization_id UUID
    DEFAULT current_setting('app.tenant_id', true)::uuid;

ALTER TABLE email_actions
    ADD COLUMN IF NOT EXISTS organization_id UUID
    DEFAULT current_setting('app.tenant_id', true)::uuid;

ALTER TABLE email_ai_drafts
    ADD COLUMN IF NOT EXISTS organization_id UUID
    DEFAULT current_setting('app.tenant_id', true)::uuid;

ALTER TABLE email_assistant_settings
    ADD COLUMN IF NOT EXISTS organization_id UUID
    DEFAULT current_setting('app.tenant_id', true)::uuid;

ALTER TABLE email_attachments
    ADD COLUMN IF NOT EXISTS organization_id UUID
    DEFAULT current_setting('app.tenant_id', true)::uuid;

ALTER TABLE email_cold_senders
    ADD COLUMN IF NOT EXISTS organization_id UUID
    DEFAULT current_setting('app.tenant_id', true)::uuid;

ALTER TABLE email_contacts
    ADD COLUMN IF NOT EXISTS organization_id UUID
    DEFAULT current_setting('app.tenant_id', true)::uuid;

ALTER TABLE email_embeddings
    ADD COLUMN IF NOT EXISTS organization_id UUID
    DEFAULT current_setting('app.tenant_id', true)::uuid;

ALTER TABLE email_executed_rules
    ADD COLUMN IF NOT EXISTS organization_id UUID
    DEFAULT current_setting('app.tenant_id', true)::uuid;

ALTER TABLE email_folders
    ADD COLUMN IF NOT EXISTS organization_id UUID
    DEFAULT current_setting('app.tenant_id', true)::uuid;

ALTER TABLE email_knowledge
    ADD COLUMN IF NOT EXISTS organization_id UUID
    DEFAULT current_setting('app.tenant_id', true)::uuid;

ALTER TABLE email_learned_patterns
    ADD COLUMN IF NOT EXISTS organization_id UUID
    DEFAULT current_setting('app.tenant_id', true)::uuid;

ALTER TABLE email_messages
    ADD COLUMN IF NOT EXISTS organization_id UUID
    DEFAULT current_setting('app.tenant_id', true)::uuid;

ALTER TABLE email_newsletters
    ADD COLUMN IF NOT EXISTS organization_id UUID
    DEFAULT current_setting('app.tenant_id', true)::uuid;

ALTER TABLE email_rule_guidance
    ADD COLUMN IF NOT EXISTS organization_id UUID
    DEFAULT current_setting('app.tenant_id', true)::uuid;

ALTER TABLE email_rule_patterns
    ADD COLUMN IF NOT EXISTS organization_id UUID
    DEFAULT current_setting('app.tenant_id', true)::uuid;

ALTER TABLE email_rules
    ADD COLUMN IF NOT EXISTS organization_id UUID
    DEFAULT current_setting('app.tenant_id', true)::uuid;

ALTER TABLE email_senders
    ADD COLUMN IF NOT EXISTS organization_id UUID
    DEFAULT current_setting('app.tenant_id', true)::uuid;

ALTER TABLE email_sync_log
    ADD COLUMN IF NOT EXISTS organization_id UUID
    DEFAULT current_setting('app.tenant_id', true)::uuid;

ALTER TABLE email_thread_status
    ADD COLUMN IF NOT EXISTS organization_id UUID
    DEFAULT current_setting('app.tenant_id', true)::uuid;

ALTER TABLE email_voice_profiles
    ADD COLUMN IF NOT EXISTS organization_id UUID
    DEFAULT current_setting('app.tenant_id', true)::uuid;

ALTER TABLE attachments
    ADD COLUMN IF NOT EXISTS organization_id UUID
    DEFAULT current_setting('app.tenant_id', true)::uuid;

ALTER TABLE calendar_day_state
    ADD COLUMN IF NOT EXISTS organization_id UUID
    DEFAULT current_setting('app.tenant_id', true)::uuid;

ALTER TABLE my_tasks_horizons
    ADD COLUMN IF NOT EXISTS organization_id UUID
    DEFAULT current_setting('app.tenant_id', true)::uuid;

ALTER TABLE people
    ADD COLUMN IF NOT EXISTS organization_id UUID
    DEFAULT current_setting('app.tenant_id', true)::uuid;

ALTER TABLE people_absences
    ADD COLUMN IF NOT EXISTS organization_id UUID
    DEFAULT current_setting('app.tenant_id', true)::uuid;

ALTER TABLE people_credentials
    ADD COLUMN IF NOT EXISTS organization_id UUID
    DEFAULT current_setting('app.tenant_id', true)::uuid;

ALTER TABLE people_resumes
    ADD COLUMN IF NOT EXISTS organization_id UUID
    DEFAULT current_setting('app.tenant_id', true)::uuid;

ALTER TABLE people_skills
    ADD COLUMN IF NOT EXISTS organization_id UUID
    DEFAULT current_setting('app.tenant_id', true)::uuid;

ALTER TABLE my_tasks_reviews
    ADD COLUMN IF NOT EXISTS organization_id UUID
    DEFAULT current_setting('app.tenant_id', true)::uuid;

ALTER TABLE calendar_rollover_log
    ADD COLUMN IF NOT EXISTS organization_id UUID
    DEFAULT current_setting('app.tenant_id', true)::uuid;

ALTER TABLE user_settings
    ADD COLUMN IF NOT EXISTS organization_id UUID
    DEFAULT current_setting('app.tenant_id', true)::uuid;

ALTER TABLE live_session
    ADD COLUMN IF NOT EXISTS organization_id UUID
    DEFAULT current_setting('app.tenant_id', true)::uuid;

ALTER TABLE meeting
    ADD COLUMN IF NOT EXISTS organization_id UUID
    DEFAULT current_setting('app.tenant_id', true)::uuid;

ALTER TABLE meeting_bot
    ADD COLUMN IF NOT EXISTS organization_id UUID
    DEFAULT current_setting('app.tenant_id', true)::uuid;

ALTER TABLE meeting_note
    ADD COLUMN IF NOT EXISTS organization_id UUID
    DEFAULT current_setting('app.tenant_id', true)::uuid;

ALTER TABLE meeting_recording
    ADD COLUMN IF NOT EXISTS organization_id UUID
    DEFAULT current_setting('app.tenant_id', true)::uuid;

ALTER TABLE message
    ADD COLUMN IF NOT EXISTS organization_id UUID
    DEFAULT current_setting('app.tenant_id', true)::uuid;

ALTER TABLE notes_glossary
    ADD COLUMN IF NOT EXISTS organization_id UUID
    DEFAULT current_setting('app.tenant_id', true)::uuid;

ALTER TABLE org_group_member
    ADD COLUMN IF NOT EXISTS organization_id UUID
    DEFAULT current_setting('app.tenant_id', true)::uuid;

ALTER TABLE org_role_permission
    ADD COLUMN IF NOT EXISTS organization_id UUID
    DEFAULT current_setting('app.tenant_id', true)::uuid;

ALTER TABLE org_settings
    ADD COLUMN IF NOT EXISTS organization_id UUID
    DEFAULT current_setting('app.tenant_id', true)::uuid;

ALTER TABLE pending_actions
    ADD COLUMN IF NOT EXISTS organization_id UUID
    DEFAULT current_setting('app.tenant_id', true)::uuid;

ALTER TABLE pending_commit
    ADD COLUMN IF NOT EXISTS organization_id UUID
    DEFAULT current_setting('app.tenant_id', true)::uuid;

ALTER TABLE person
    ADD COLUMN IF NOT EXISTS organization_id UUID
    DEFAULT current_setting('app.tenant_id', true)::uuid;

ALTER TABLE plugins
    ADD COLUMN IF NOT EXISTS organization_id UUID
    DEFAULT current_setting('app.tenant_id', true)::uuid;

ALTER TABLE pm_activities
    ADD COLUMN IF NOT EXISTS organization_id UUID
    DEFAULT current_setting('app.tenant_id', true)::uuid;

ALTER TABLE pm_custom_fields
    ADD COLUMN IF NOT EXISTS organization_id UUID
    DEFAULT current_setting('app.tenant_id', true)::uuid;

ALTER TABLE pm_intake
    ADD COLUMN IF NOT EXISTS organization_id UUID
    DEFAULT current_setting('app.tenant_id', true)::uuid;

ALTER TABLE pm_notifications
    ADD COLUMN IF NOT EXISTS organization_id UUID
    DEFAULT current_setting('app.tenant_id', true)::uuid;

ALTER TABLE pm_project_grants
    ADD COLUMN IF NOT EXISTS organization_id UUID
    DEFAULT current_setting('app.tenant_id', true)::uuid;

ALTER TABLE pm_project_watchers
    ADD COLUMN IF NOT EXISTS organization_id UUID
    DEFAULT current_setting('app.tenant_id', true)::uuid;

ALTER TABLE pm_projects
    ADD COLUMN IF NOT EXISTS organization_id UUID
    DEFAULT current_setting('app.tenant_id', true)::uuid;

ALTER TABLE pm_recurrences
    ADD COLUMN IF NOT EXISTS organization_id UUID
    DEFAULT current_setting('app.tenant_id', true)::uuid;

ALTER TABLE pm_report_recipients
    ADD COLUMN IF NOT EXISTS organization_id UUID
    DEFAULT current_setting('app.tenant_id', true)::uuid;

ALTER TABLE pm_reports
    ADD COLUMN IF NOT EXISTS organization_id UUID
    DEFAULT current_setting('app.tenant_id', true)::uuid;

ALTER TABLE pm_tags
    ADD COLUMN IF NOT EXISTS organization_id UUID
    DEFAULT current_setting('app.tenant_id', true)::uuid;

ALTER TABLE pm_task_assignees
    ADD COLUMN IF NOT EXISTS organization_id UUID
    DEFAULT current_setting('app.tenant_id', true)::uuid;

ALTER TABLE pm_task_attachments
    ADD COLUMN IF NOT EXISTS organization_id UUID
    DEFAULT current_setting('app.tenant_id', true)::uuid;

ALTER TABLE pm_task_counters
    ADD COLUMN IF NOT EXISTS organization_id UUID
    DEFAULT current_setting('app.tenant_id', true)::uuid;

ALTER TABLE pm_task_links
    ADD COLUMN IF NOT EXISTS organization_id UUID
    DEFAULT current_setting('app.tenant_id', true)::uuid;

ALTER TABLE pm_task_personal
    ADD COLUMN IF NOT EXISTS organization_id UUID
    DEFAULT current_setting('app.tenant_id', true)::uuid;

ALTER TABLE pm_task_statuses
    ADD COLUMN IF NOT EXISTS organization_id UUID
    DEFAULT current_setting('app.tenant_id', true)::uuid;

ALTER TABLE pm_task_tombstones
    ADD COLUMN IF NOT EXISTS organization_id UUID
    DEFAULT current_setting('app.tenant_id', true)::uuid;

ALTER TABLE pm_task_types
    ADD COLUMN IF NOT EXISTS organization_id UUID
    DEFAULT current_setting('app.tenant_id', true)::uuid;

ALTER TABLE pm_task_watchers
    ADD COLUMN IF NOT EXISTS organization_id UUID
    DEFAULT current_setting('app.tenant_id', true)::uuid;

ALTER TABLE pm_tasks
    ADD COLUMN IF NOT EXISTS organization_id UUID
    DEFAULT current_setting('app.tenant_id', true)::uuid;

ALTER TABLE pm_view_task_positions
    ADD COLUMN IF NOT EXISTS organization_id UUID
    DEFAULT current_setting('app.tenant_id', true)::uuid;

ALTER TABLE pm_view_user_state
    ADD COLUMN IF NOT EXISTS organization_id UUID
    DEFAULT current_setting('app.tenant_id', true)::uuid;

ALTER TABLE pm_views
    ADD COLUMN IF NOT EXISTS organization_id UUID
    DEFAULT current_setting('app.tenant_id', true)::uuid;

ALTER TABLE project
    ADD COLUMN IF NOT EXISTS organization_id UUID
    DEFAULT current_setting('app.tenant_id', true)::uuid;

ALTER TABLE summary_run
    ADD COLUMN IF NOT EXISTS organization_id UUID
    DEFAULT current_setting('app.tenant_id', true)::uuid;

ALTER TABLE task
    ADD COLUMN IF NOT EXISTS organization_id UUID
    DEFAULT current_setting('app.tenant_id', true)::uuid;

ALTER TABLE task_accounts
    ADD COLUMN IF NOT EXISTS organization_id UUID
    DEFAULT current_setting('app.tenant_id', true)::uuid;

ALTER TABLE transcript_segment
    ADD COLUMN IF NOT EXISTS organization_id UUID
    DEFAULT current_setting('app.tenant_id', true)::uuid;

ALTER TABLE user_permission_override
    ADD COLUMN IF NOT EXISTS organization_id UUID
    DEFAULT current_setting('app.tenant_id', true)::uuid;

ALTER TABLE user_role
    ADD COLUMN IF NOT EXISTS organization_id UUID
    DEFAULT current_setting('app.tenant_id', true)::uuid;

ALTER TABLE wa_accounts
    ADD COLUMN IF NOT EXISTS organization_id UUID
    DEFAULT current_setting('app.tenant_id', true)::uuid;

ALTER TABLE wa_ai_drafts
    ADD COLUMN IF NOT EXISTS organization_id UUID
    DEFAULT current_setting('app.tenant_id', true)::uuid;

ALTER TABLE wa_categories
    ADD COLUMN IF NOT EXISTS organization_id UUID
    DEFAULT current_setting('app.tenant_id', true)::uuid;

ALTER TABLE wa_chat_avatars
    ADD COLUMN IF NOT EXISTS organization_id UUID
    DEFAULT current_setting('app.tenant_id', true)::uuid;

ALTER TABLE wa_chat_labels
    ADD COLUMN IF NOT EXISTS organization_id UUID
    DEFAULT current_setting('app.tenant_id', true)::uuid;

ALTER TABLE wa_chat_status
    ADD COLUMN IF NOT EXISTS organization_id UUID
    DEFAULT current_setting('app.tenant_id', true)::uuid;

ALTER TABLE wa_chats
    ADD COLUMN IF NOT EXISTS organization_id UUID
    DEFAULT current_setting('app.tenant_id', true)::uuid;

ALTER TABLE wa_commitments
    ADD COLUMN IF NOT EXISTS organization_id UUID
    DEFAULT current_setting('app.tenant_id', true)::uuid;

ALTER TABLE wa_contacts
    ADD COLUMN IF NOT EXISTS organization_id UUID
    DEFAULT current_setting('app.tenant_id', true)::uuid;

ALTER TABLE wa_group_summaries
    ADD COLUMN IF NOT EXISTS organization_id UUID
    DEFAULT current_setting('app.tenant_id', true)::uuid;

ALTER TABLE wa_labels
    ADD COLUMN IF NOT EXISTS organization_id UUID
    DEFAULT current_setting('app.tenant_id', true)::uuid;

ALTER TABLE wa_media
    ADD COLUMN IF NOT EXISTS organization_id UUID
    DEFAULT current_setting('app.tenant_id', true)::uuid;

ALTER TABLE wa_message_embeddings
    ADD COLUMN IF NOT EXISTS organization_id UUID
    DEFAULT current_setting('app.tenant_id', true)::uuid;

ALTER TABLE wa_messages
    ADD COLUMN IF NOT EXISTS organization_id UUID
    DEFAULT current_setting('app.tenant_id', true)::uuid;

ALTER TABLE wa_saved_replies
    ADD COLUMN IF NOT EXISTS organization_id UUID
    DEFAULT current_setting('app.tenant_id', true)::uuid;

ALTER TABLE wa_sync_log
    ADD COLUMN IF NOT EXISTS organization_id UUID
    DEFAULT current_setting('app.tenant_id', true)::uuid;

ALTER TABLE wa_templates
    ADD COLUMN IF NOT EXISTS organization_id UUID
    DEFAULT current_setting('app.tenant_id', true)::uuid;

ALTER TABLE workflow_modules
    ADD COLUMN IF NOT EXISTS organization_id UUID
    DEFAULT current_setting('app.tenant_id', true)::uuid;

ALTER TABLE workflow_run_pauses
    ADD COLUMN IF NOT EXISTS organization_id UUID
    DEFAULT current_setting('app.tenant_id', true)::uuid;

ALTER TABLE workflow_runs
    ADD COLUMN IF NOT EXISTS organization_id UUID
    DEFAULT current_setting('app.tenant_id', true)::uuid;

ALTER TABLE workflow_triggers
    ADD COLUMN IF NOT EXISTS organization_id UUID
    DEFAULT current_setting('app.tenant_id', true)::uuid;

ALTER TABLE workflow_versions
    ADD COLUMN IF NOT EXISTS organization_id UUID
    DEFAULT current_setting('app.tenant_id', true)::uuid;

ALTER TABLE workflows
    ADD COLUMN IF NOT EXISTS organization_id UUID
    DEFAULT current_setting('app.tenant_id', true)::uuid;
