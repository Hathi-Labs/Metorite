-- ============================================================================
-- MT-1b · phase 2/4 backfill — GENERATED, DO NOT EDIT BY HAND
-- ============================================================================
-- Regenerate with: uv run python scripts/gen_tenant_migration.py
-- Spec: project-docs/specs/saas_multitenancy.md §1.3 · MT-1b · WS-29 · D15
--
-- Batched UPDATE. Re-runnable and interruptible — each statement is idempotent, so a run that aborts can simply be run again. This is the slow phase; expect it to be the long pole on any table with real volume.
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


-- The operator's own organization owns every pre-existing row: this box
-- served exactly one tenant before MT-1b.

UPDATE access_request SET organization_id = (SELECT id FROM organization WHERE slug = 'default')
 WHERE organization_id IS NULL;

UPDATE action_item SET organization_id = (SELECT id FROM organization WHERE slug = 'default')
 WHERE organization_id IS NULL;

UPDATE agent_avatars SET organization_id = (SELECT id FROM organization WHERE slug = 'default')
 WHERE organization_id IS NULL;

UPDATE agent_blob SET organization_id = (SELECT id FROM organization WHERE slug = 'default')
 WHERE organization_id IS NULL;

UPDATE agent_file_history SET organization_id = (SELECT id FROM organization WHERE slug = 'default')
 WHERE organization_id IS NULL;

UPDATE agent_run SET organization_id = (SELECT id FROM organization WHERE slug = 'default')
 WHERE organization_id IS NULL;

UPDATE agent_skill_setting SET organization_id = (SELECT id FROM organization WHERE slug = 'default')
 WHERE organization_id IS NULL;

UPDATE app_audit SET organization_id = (SELECT id FROM organization WHERE slug = 'default')
 WHERE organization_id IS NULL;

UPDATE app_data SET organization_id = (SELECT id FROM organization WHERE slug = 'default')
 WHERE organization_id IS NULL;

UPDATE app_files SET organization_id = (SELECT id FROM organization WHERE slug = 'default')
 WHERE organization_id IS NULL;

UPDATE app_grants SET organization_id = (SELECT id FROM organization WHERE slug = 'default')
 WHERE organization_id IS NULL;

UPDATE app_pins SET organization_id = (SELECT id FROM organization WHERE slug = 'default')
 WHERE organization_id IS NULL;

UPDATE app_tool_grants SET organization_id = (SELECT id FROM organization WHERE slug = 'default')
 WHERE organization_id IS NULL;

UPDATE app_user SET organization_id = (SELECT id FROM organization WHERE slug = 'default')
 WHERE organization_id IS NULL;

UPDATE app_versions SET organization_id = (SELECT id FROM organization WHERE slug = 'default')
 WHERE organization_id IS NULL;

UPDATE apps SET organization_id = (SELECT id FROM organization WHERE slug = 'default')
 WHERE organization_id IS NULL;

UPDATE audit_event SET organization_id = (SELECT id FROM organization WHERE slug = 'default')
 WHERE organization_id IS NULL;

UPDATE chat_message SET organization_id = (SELECT id FROM organization WHERE slug = 'default')
 WHERE organization_id IS NULL;

UPDATE chat_session SET organization_id = (SELECT id FROM organization WHERE slug = 'default')
 WHERE organization_id IS NULL;

UPDATE chat_session_agent SET organization_id = (SELECT id FROM organization WHERE slug = 'default')
 WHERE organization_id IS NULL;

UPDATE chat_session_participant SET organization_id = (SELECT id FROM organization WHERE slug = 'default')
 WHERE organization_id IS NULL;

UPDATE copilot_config SET organization_id = (SELECT id FROM organization WHERE slug = 'default')
 WHERE organization_id IS NULL;

UPDATE copilot_event SET organization_id = (SELECT id FROM organization WHERE slug = 'default')
 WHERE organization_id IS NULL;

UPDATE crm_auto_lead_cursors SET organization_id = (SELECT id FROM organization WHERE slug = 'default')
 WHERE organization_id IS NULL;

UPDATE crm_deal_contacts SET organization_id = (SELECT id FROM organization WHERE slug = 'default')
 WHERE organization_id IS NULL;

UPDATE crm_deal_statuses SET organization_id = (SELECT id FROM organization WHERE slug = 'default')
 WHERE organization_id IS NULL;

UPDATE crm_lead_statuses SET organization_id = (SELECT id FROM organization WHERE slug = 'default')
 WHERE organization_id IS NULL;

UPDATE crm_leads SET organization_id = (SELECT id FROM organization WHERE slug = 'default')
 WHERE organization_id IS NULL;

UPDATE crm_lost_reasons SET organization_id = (SELECT id FROM organization WHERE slug = 'default')
 WHERE organization_id IS NULL;

UPDATE crm_organizations SET organization_id = (SELECT id FROM organization WHERE slug = 'default')
 WHERE organization_id IS NULL;

UPDATE crm_status_changes SET organization_id = (SELECT id FROM organization WHERE slug = 'default')
 WHERE organization_id IS NULL;

UPDATE crm_sync_cursors SET organization_id = (SELECT id FROM organization WHERE slug = 'default')
 WHERE organization_id IS NULL;

UPDATE crm_zoho_tombstones SET organization_id = (SELECT id FROM organization WHERE slug = 'default')
 WHERE organization_id IS NULL;

UPDATE custom_api_definitions SET organization_id = (SELECT id FROM organization WHERE slug = 'default')
 WHERE organization_id IS NULL;

UPDATE customer SET organization_id = (SELECT id FROM organization WHERE slug = 'default')
 WHERE organization_id IS NULL;

UPDATE deal SET organization_id = (SELECT id FROM organization WHERE slug = 'default')
 WHERE organization_id IS NULL;

UPDATE dynamic_agents SET organization_id = (SELECT id FROM organization WHERE slug = 'default')
 WHERE organization_id IS NULL;

UPDATE email_accounts SET organization_id = (SELECT id FROM organization WHERE slug = 'default')
 WHERE organization_id IS NULL;

UPDATE email_actions SET organization_id = (SELECT id FROM organization WHERE slug = 'default')
 WHERE organization_id IS NULL;

UPDATE email_ai_drafts SET organization_id = (SELECT id FROM organization WHERE slug = 'default')
 WHERE organization_id IS NULL;

UPDATE email_assistant_settings SET organization_id = (SELECT id FROM organization WHERE slug = 'default')
 WHERE organization_id IS NULL;

UPDATE email_attachments SET organization_id = (SELECT id FROM organization WHERE slug = 'default')
 WHERE organization_id IS NULL;

UPDATE email_cold_senders SET organization_id = (SELECT id FROM organization WHERE slug = 'default')
 WHERE organization_id IS NULL;

UPDATE email_contacts SET organization_id = (SELECT id FROM organization WHERE slug = 'default')
 WHERE organization_id IS NULL;

UPDATE email_embeddings SET organization_id = (SELECT id FROM organization WHERE slug = 'default')
 WHERE organization_id IS NULL;

UPDATE email_executed_rules SET organization_id = (SELECT id FROM organization WHERE slug = 'default')
 WHERE organization_id IS NULL;

UPDATE email_folders SET organization_id = (SELECT id FROM organization WHERE slug = 'default')
 WHERE organization_id IS NULL;

UPDATE email_knowledge SET organization_id = (SELECT id FROM organization WHERE slug = 'default')
 WHERE organization_id IS NULL;

UPDATE email_learned_patterns SET organization_id = (SELECT id FROM organization WHERE slug = 'default')
 WHERE organization_id IS NULL;

UPDATE email_messages SET organization_id = (SELECT id FROM organization WHERE slug = 'default')
 WHERE organization_id IS NULL;

UPDATE email_newsletters SET organization_id = (SELECT id FROM organization WHERE slug = 'default')
 WHERE organization_id IS NULL;

UPDATE email_rule_guidance SET organization_id = (SELECT id FROM organization WHERE slug = 'default')
 WHERE organization_id IS NULL;

UPDATE email_rule_patterns SET organization_id = (SELECT id FROM organization WHERE slug = 'default')
 WHERE organization_id IS NULL;

UPDATE email_rules SET organization_id = (SELECT id FROM organization WHERE slug = 'default')
 WHERE organization_id IS NULL;

UPDATE email_senders SET organization_id = (SELECT id FROM organization WHERE slug = 'default')
 WHERE organization_id IS NULL;

UPDATE email_sync_log SET organization_id = (SELECT id FROM organization WHERE slug = 'default')
 WHERE organization_id IS NULL;

UPDATE email_thread_status SET organization_id = (SELECT id FROM organization WHERE slug = 'default')
 WHERE organization_id IS NULL;

UPDATE email_voice_profiles SET organization_id = (SELECT id FROM organization WHERE slug = 'default')
 WHERE organization_id IS NULL;

UPDATE attachments SET organization_id = (SELECT id FROM organization WHERE slug = 'default')
 WHERE organization_id IS NULL;

UPDATE gtd_contexts SET organization_id = (SELECT id FROM organization WHERE slug = 'default')
 WHERE organization_id IS NULL;

UPDATE calendar_day_state SET organization_id = (SELECT id FROM organization WHERE slug = 'default')
 WHERE organization_id IS NULL;

UPDATE gtd_folders SET organization_id = (SELECT id FROM organization WHERE slug = 'default')
 WHERE organization_id IS NULL;

UPDATE my_tasks_horizons SET organization_id = (SELECT id FROM organization WHERE slug = 'default')
 WHERE organization_id IS NULL;

UPDATE gtd_items SET organization_id = (SELECT id FROM organization WHERE slug = 'default')
 WHERE organization_id IS NULL;

UPDATE people SET organization_id = (SELECT id FROM organization WHERE slug = 'default')
 WHERE organization_id IS NULL;

UPDATE people_absences SET organization_id = (SELECT id FROM organization WHERE slug = 'default')
 WHERE organization_id IS NULL;

UPDATE people_credentials SET organization_id = (SELECT id FROM organization WHERE slug = 'default')
 WHERE organization_id IS NULL;

UPDATE people_resumes SET organization_id = (SELECT id FROM organization WHERE slug = 'default')
 WHERE organization_id IS NULL;

UPDATE people_skills SET organization_id = (SELECT id FROM organization WHERE slug = 'default')
 WHERE organization_id IS NULL;

UPDATE gtd_projects SET organization_id = (SELECT id FROM organization WHERE slug = 'default')
 WHERE organization_id IS NULL;

UPDATE my_tasks_reviews SET organization_id = (SELECT id FROM organization WHERE slug = 'default')
 WHERE organization_id IS NULL;

UPDATE calendar_rollover_log SET organization_id = (SELECT id FROM organization WHERE slug = 'default')
 WHERE organization_id IS NULL;

UPDATE user_settings SET organization_id = (SELECT id FROM organization WHERE slug = 'default')
 WHERE organization_id IS NULL;

UPDATE gtd_spaces SET organization_id = (SELECT id FROM organization WHERE slug = 'default')
 WHERE organization_id IS NULL;

UPDATE gtd_waiting SET organization_id = (SELECT id FROM organization WHERE slug = 'default')
 WHERE organization_id IS NULL;

UPDATE live_session SET organization_id = (SELECT id FROM organization WHERE slug = 'default')
 WHERE organization_id IS NULL;

UPDATE meeting SET organization_id = (SELECT id FROM organization WHERE slug = 'default')
 WHERE organization_id IS NULL;

UPDATE meeting_bot SET organization_id = (SELECT id FROM organization WHERE slug = 'default')
 WHERE organization_id IS NULL;

UPDATE meeting_note SET organization_id = (SELECT id FROM organization WHERE slug = 'default')
 WHERE organization_id IS NULL;

UPDATE meeting_recording SET organization_id = (SELECT id FROM organization WHERE slug = 'default')
 WHERE organization_id IS NULL;

UPDATE message SET organization_id = (SELECT id FROM organization WHERE slug = 'default')
 WHERE organization_id IS NULL;

UPDATE notes_glossary SET organization_id = (SELECT id FROM organization WHERE slug = 'default')
 WHERE organization_id IS NULL;

UPDATE org_group_member SET organization_id = (SELECT id FROM organization WHERE slug = 'default')
 WHERE organization_id IS NULL;

UPDATE org_role_permission SET organization_id = (SELECT id FROM organization WHERE slug = 'default')
 WHERE organization_id IS NULL;

UPDATE org_settings SET organization_id = (SELECT id FROM organization WHERE slug = 'default')
 WHERE organization_id IS NULL;

UPDATE pending_actions SET organization_id = (SELECT id FROM organization WHERE slug = 'default')
 WHERE organization_id IS NULL;

UPDATE pending_commit SET organization_id = (SELECT id FROM organization WHERE slug = 'default')
 WHERE organization_id IS NULL;

UPDATE person SET organization_id = (SELECT id FROM organization WHERE slug = 'default')
 WHERE organization_id IS NULL;

UPDATE plugins SET organization_id = (SELECT id FROM organization WHERE slug = 'default')
 WHERE organization_id IS NULL;

UPDATE pm_activities SET organization_id = (SELECT id FROM organization WHERE slug = 'default')
 WHERE organization_id IS NULL;

UPDATE pm_custom_fields SET organization_id = (SELECT id FROM organization WHERE slug = 'default')
 WHERE organization_id IS NULL;

UPDATE pm_intake SET organization_id = (SELECT id FROM organization WHERE slug = 'default')
 WHERE organization_id IS NULL;

UPDATE pm_notifications SET organization_id = (SELECT id FROM organization WHERE slug = 'default')
 WHERE organization_id IS NULL;

UPDATE pm_project_grants SET organization_id = (SELECT id FROM organization WHERE slug = 'default')
 WHERE organization_id IS NULL;

UPDATE pm_project_watchers SET organization_id = (SELECT id FROM organization WHERE slug = 'default')
 WHERE organization_id IS NULL;

UPDATE pm_projects SET organization_id = (SELECT id FROM organization WHERE slug = 'default')
 WHERE organization_id IS NULL;

UPDATE pm_recurrences SET organization_id = (SELECT id FROM organization WHERE slug = 'default')
 WHERE organization_id IS NULL;

UPDATE pm_report_recipients SET organization_id = (SELECT id FROM organization WHERE slug = 'default')
 WHERE organization_id IS NULL;

UPDATE pm_reports SET organization_id = (SELECT id FROM organization WHERE slug = 'default')
 WHERE organization_id IS NULL;

UPDATE pm_tags SET organization_id = (SELECT id FROM organization WHERE slug = 'default')
 WHERE organization_id IS NULL;

UPDATE pm_task_assignees SET organization_id = (SELECT id FROM organization WHERE slug = 'default')
 WHERE organization_id IS NULL;

UPDATE pm_task_attachments SET organization_id = (SELECT id FROM organization WHERE slug = 'default')
 WHERE organization_id IS NULL;

UPDATE pm_task_counters SET organization_id = (SELECT id FROM organization WHERE slug = 'default')
 WHERE organization_id IS NULL;

UPDATE pm_task_links SET organization_id = (SELECT id FROM organization WHERE slug = 'default')
 WHERE organization_id IS NULL;

UPDATE pm_task_personal SET organization_id = (SELECT id FROM organization WHERE slug = 'default')
 WHERE organization_id IS NULL;

UPDATE pm_task_statuses SET organization_id = (SELECT id FROM organization WHERE slug = 'default')
 WHERE organization_id IS NULL;

UPDATE pm_task_tombstones SET organization_id = (SELECT id FROM organization WHERE slug = 'default')
 WHERE organization_id IS NULL;

UPDATE pm_task_types SET organization_id = (SELECT id FROM organization WHERE slug = 'default')
 WHERE organization_id IS NULL;

UPDATE pm_task_watchers SET organization_id = (SELECT id FROM organization WHERE slug = 'default')
 WHERE organization_id IS NULL;

UPDATE pm_tasks SET organization_id = (SELECT id FROM organization WHERE slug = 'default')
 WHERE organization_id IS NULL;

UPDATE pm_view_task_positions SET organization_id = (SELECT id FROM organization WHERE slug = 'default')
 WHERE organization_id IS NULL;

UPDATE pm_view_user_state SET organization_id = (SELECT id FROM organization WHERE slug = 'default')
 WHERE organization_id IS NULL;

UPDATE pm_views SET organization_id = (SELECT id FROM organization WHERE slug = 'default')
 WHERE organization_id IS NULL;

UPDATE project SET organization_id = (SELECT id FROM organization WHERE slug = 'default')
 WHERE organization_id IS NULL;

UPDATE summary_run SET organization_id = (SELECT id FROM organization WHERE slug = 'default')
 WHERE organization_id IS NULL;

UPDATE task SET organization_id = (SELECT id FROM organization WHERE slug = 'default')
 WHERE organization_id IS NULL;

UPDATE task_accounts SET organization_id = (SELECT id FROM organization WHERE slug = 'default')
 WHERE organization_id IS NULL;

UPDATE transcript_segment SET organization_id = (SELECT id FROM organization WHERE slug = 'default')
 WHERE organization_id IS NULL;

UPDATE user_permission_override SET organization_id = (SELECT id FROM organization WHERE slug = 'default')
 WHERE organization_id IS NULL;

UPDATE user_role SET organization_id = (SELECT id FROM organization WHERE slug = 'default')
 WHERE organization_id IS NULL;

UPDATE wa_accounts SET organization_id = (SELECT id FROM organization WHERE slug = 'default')
 WHERE organization_id IS NULL;

UPDATE wa_ai_drafts SET organization_id = (SELECT id FROM organization WHERE slug = 'default')
 WHERE organization_id IS NULL;

UPDATE wa_categories SET organization_id = (SELECT id FROM organization WHERE slug = 'default')
 WHERE organization_id IS NULL;

UPDATE wa_chat_avatars SET organization_id = (SELECT id FROM organization WHERE slug = 'default')
 WHERE organization_id IS NULL;

UPDATE wa_chat_labels SET organization_id = (SELECT id FROM organization WHERE slug = 'default')
 WHERE organization_id IS NULL;

UPDATE wa_chat_status SET organization_id = (SELECT id FROM organization WHERE slug = 'default')
 WHERE organization_id IS NULL;

UPDATE wa_chats SET organization_id = (SELECT id FROM organization WHERE slug = 'default')
 WHERE organization_id IS NULL;

UPDATE wa_commitments SET organization_id = (SELECT id FROM organization WHERE slug = 'default')
 WHERE organization_id IS NULL;

UPDATE wa_contacts SET organization_id = (SELECT id FROM organization WHERE slug = 'default')
 WHERE organization_id IS NULL;

UPDATE wa_group_summaries SET organization_id = (SELECT id FROM organization WHERE slug = 'default')
 WHERE organization_id IS NULL;

UPDATE wa_labels SET organization_id = (SELECT id FROM organization WHERE slug = 'default')
 WHERE organization_id IS NULL;

UPDATE wa_media SET organization_id = (SELECT id FROM organization WHERE slug = 'default')
 WHERE organization_id IS NULL;

UPDATE wa_message_embeddings SET organization_id = (SELECT id FROM organization WHERE slug = 'default')
 WHERE organization_id IS NULL;

UPDATE wa_messages SET organization_id = (SELECT id FROM organization WHERE slug = 'default')
 WHERE organization_id IS NULL;

UPDATE wa_saved_replies SET organization_id = (SELECT id FROM organization WHERE slug = 'default')
 WHERE organization_id IS NULL;

UPDATE wa_sync_log SET organization_id = (SELECT id FROM organization WHERE slug = 'default')
 WHERE organization_id IS NULL;

UPDATE wa_templates SET organization_id = (SELECT id FROM organization WHERE slug = 'default')
 WHERE organization_id IS NULL;

UPDATE workflow_modules SET organization_id = (SELECT id FROM organization WHERE slug = 'default')
 WHERE organization_id IS NULL;

UPDATE workflow_run_pauses SET organization_id = (SELECT id FROM organization WHERE slug = 'default')
 WHERE organization_id IS NULL;

UPDATE workflow_runs SET organization_id = (SELECT id FROM organization WHERE slug = 'default')
 WHERE organization_id IS NULL;

UPDATE workflow_triggers SET organization_id = (SELECT id FROM organization WHERE slug = 'default')
 WHERE organization_id IS NULL;

UPDATE workflow_versions SET organization_id = (SELECT id FROM organization WHERE slug = 'default')
 WHERE organization_id IS NULL;

UPDATE workflows SET organization_id = (SELECT id FROM organization WHERE slug = 'default')
 WHERE organization_id IS NULL;
