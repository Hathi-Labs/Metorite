# Gateway -- FastAPI Entry Point

History of this file is in git. Run git log -p on it to read past contracts.

## Purpose

The gateway is the HTTP/WS entry point for all external interaction with
Metorite. It hosts the MAF AG-UI chat endpoint, agent run/stream endpoints,
webhook receivers, OAuth callbacks, and the Control Plane API.

## Ownership

- Owner: Metorite Core team
- Path: apps/services/gateway/

## Local Contracts

Each item names a module, the rules that must stay true, and the tests that
fence them. The owning spec holds the detail, and the code is the fact.
A fence name with no folder is a file in `tests/unit/`. A name that ends in
`_r8.py` runs against a real database (R8).

0. db.py -- **The shared async engine seam (BO-10).** It re-exports `acb_common.db` (`get_engine()`, `get_session_factory()`, `get_db()`). **The gateway makes ZERO `create_async_engine` calls of its own.** A new package uses this seam and adds no engine. Fence: `test_db_engine_seam.py`.
0a. csv_export.py -- **The ONE CSV writer.** Its formula guard puts an apostrophe before a cell that starts with `=`, `+`, `-`, `@`, TAB or CR. Do not add a second CSV writer. Fences: `test_projects_export.py`, `test_crm_export.py`.
0b. capacity.py -- **One person's hours against their open work.** It is a leaf module, because `routes/people` imports `routes/projects`. The callers compute no hours of their own, and each caller decides what its audience may read. The capacity route drops each `HR_KEYS` key for a caller without `admin:members:read`. The readers never open a session (R5). Fences: `test_projects_analytics_capacity.py`, `test_people_dashboard.py`, `test_projects_analytics_rebalance.py`.
0c. conflicts.py -- **The pure rules of `GET /projects/analytics/conflicts`.** It opens no session and reads no clock (R5). `timeline.ts` keeps a copy of `dependency_conflict`, and `tests/fixtures/projects_dependency_conflicts.json` runs against both copies. Fences: `test_projects_analytics_conflicts.py`, `test_projects_plan_preview.py`.
0d. pdf_render.py + routes/documents.py -- **The ONE document-to-PDF seam.** ⚠️ **The sanitizer is the control.** `sanitize_html` keeps an allowlist of text tags, so a render cannot fetch a resource (SSRF). ⚠️ **MuPDF never runs in the gateway process.** `render_pdf` runs a child process with a timeout and the `acb_common.child_env` allowlist. A crash is 422 and a timeout is 503, never 500. ⚠️ **It formats nothing.** A report PDF is the HTML that `reportEmail.ts` formats. Fences: `test_pdf_render.py`, `test_documents_pdf_route.py`.
0e. decide_features.py -- **The ONE `decide` mode registry, and the ONE shadow helper.** Do not add a second of either.
   - Each feature is `off`, `shadow` or `on`, and every default is `off`. `mode_for` takes the org from `current_tenant()`, never from mail or a request. Only a name in `ON_FEATURES` accepts `on`.
   - 🔴 **`shadow()` ALWAYS returns the old answer**, and it re-raises the old exception. Its log line holds no subject, body, sender or rule name.
   - ⚠️ A mode change needs a restart. 🔴 **Any value other than empty on a box is OWNER-GATE (H-166)**, and so is `DECIDE_ENABLED`.
   - `_ask_all` holds the one email cap permit. Never call `ask` or `shadow` inside `llm_slot`.
   - Fences: `test_email_decide_shadow.py`, `test_email_decide_questions.py`, `test_email_decide_on.py`, `test_email_llm_cap.py`, `test_email_insights_screen.py`.
0f. routes/workspace.py -- **The session file routes** (`maf_coding_engine.md` §7.5, §16.3).
   - **Every route that reads or writes a file by path opens it with `acb_skills.safe_open`.**
   - A session of a shared agent sees only its own `outputs/<thread slug>/` and `inputs/<thread slug>/`. `agent_paths.is_other_thread_rel` is the one rule. A loose file opens only for the session that began it.
   - `_tenant_history` takes the store key from the path and the caller's tenant, never from the `.cc-instance` marker.
   - A chat delete purges the chat's files BEFORE it deletes the row. A failed purge answers 503 and keeps the chat. Each door that makes a chat row calls `chat.prepare_new_session` first.
   - ⚠️ `_app_workspace_for` binds a session only to the folder of an App Workshop app. Its query skips a Studio package (`format IS DISTINCT FROM 'manifest'`). It takes each folder through `routes/apps/_common.workspace_dir`, which answers None for an empty or blank path. `Path('')` is the working directory of the process, so an empty path must never bind. Fences: `test_studio_packages_r8.py`, `test_studio_packages_store.py`.
   - Fences: `test_run_data_hygiene.py`, `test_h227_thread_scope.py`, `test_sandbox_safe_open.py`.
0g. outbound_guard.py -- **The ONE guard for a request to a URL that a member supplied.** `check_url` resolves the host once and refuses an address that is not public. `send` pins that address, follows no redirect, and sets `trust_env=False`. `OutboundRefused` never names the path or the query. ⚠️ Do not add a second check of an outbound URL. The old copies in `email/transport/attachments.py` and `workflows/tools.py` are a board finding. Fence: `test_email_webhook_guard.py`.
0h. crm_sources/ -- **The connector seam for an external CRM** (`crm_platform.md` §5.1). The `zoho/` adapter reads only. The credential comes in through the constructor. No file here reads the settings, the environment or the disk. `zoho/auth.py::ZOHO_DATA_CENTRES` is the one host allowlist. `ZohoSource._get` is the one door of an API call, under a credit budget. `zoho/client.py` copies the ingestion client: copy it, do not import it. Fences: `test_crm_sources_no_global_creds.py`, `test_crm_sources_zoho.py`.
0i. connections/ -- **The registry of external connections.** `store.py` is the ONLY reader and writer of the encrypted blob in `integration_connections`. It uses the `acb_llm.key_store` key and makes none of its own. `shared_token.py` refreshes a token under a row lock. A refused grant sets `needs_reconnect` and is never retried. Fences: `test_crm_connection.py`, `test_crm_connection_r8.py`, `test_kc_sources_store_lift.py`.
0j. crm_sync/ -- **The CRM mirror sync** (`crm_platform.md` §5.4 and §18). It reads the provider and never writes to it.
   - **The fenced doors.** Each mirror write goes through a `core.*_from_source` function, and only `crm_sync/` may name one. It never calls `insert_row` or `update_row`. Fences: `test_crm_upsert_from_source_fence.py`, `test_crm_mirror_readonly.py`.
   - **The run.** The lock is a row, one `running` row for each org. Each step and each page opens its own `tenant_session(org)`. No session stays open across a provider call. An empty listing never deletes rows. A log line never holds record content or Zoho text. `scheduler._organization_ids` is the one unbound read.
   - ⚠️ A read by ids costs credits, so no failure may repeat it on every run. Fences: `test_crm_sync_engine.py`, `test_crm_activity_recover.py`.
   - **The one-of rules.** `sync.short_reads` is the one reader of `short_by` for a tool. `mapping.rule_problem` is the one rule of a legal field rule. `fill_rank.empty_columns` is the one empty-column rule. `mapping_plans.commit` is the one approve. `crm_sync/executor.py` is the one path of a rule value. `catalogue.entry_of` is the one reader of an entry, and `catalogue.write_answer` is the one writer of an answer. `routes/crm/rules.py` is the one writer of `crm_business_rules`.
   - ⚠️ **One lock order.** Lock the rule rows through `core.FIELD_RULE_LOCK_SQL` before any other rule-row lock. Fence: `test_crm_map_plan_r8.py`.
   - **The model steps** are the mapping pipeline, the note labels, the note vectors and the digest. Each runs only for an org on its list, outside the run lock. Each counts a daily cap in Redis before each request. Each list ships empty, and a name in it is OWNER-GATE.
   - The pipeline writes a draft, never a live rule. It never replaces a part with `by: admin`. ⚠️ The digest stores a text only when `acb_common.number_check` passes it. Fences: `test_crm_map_pipeline.py`, `test_crm_note_labels.py`, `test_crm_note_vectors.py`, `test_crm_digest.py`.
   - `catalogue.py`, `questions.py` and `terms.py` are pure, with no word of one org. A member who is not an admin records a proposal only. Fences: `test_crm_catalogue.py`, `test_crm_questions.py`, `test_crm_answer_consistency.py`, `test_crm_answer_first.py`.
0k. routes/agent_access.py -- **The authenticated half of the outside-agent authorization server** (`external_agent_access.md` §15). The public half is `apps/services/agent_edge`. Both halves take their rules from `acb_auth.agent_oauth`.
   - ⚠️ The member is `user.email` and the org is the bound tenant (R11). The router is NOT in `PUBLIC_ROUTES`. The org allowlist (`agent_oauth.org_allowed`) runs before the client fetch and the ticket.
   - ⚠️ Redis keeps only the SHA-256 of the consent ticket. `approve` spends it with one `GETDEL` before any other check.
   - A list or a revoke of connections acts for the member only, and no answer holds a token. Fences: `test_agent_oauth_server.py`, `test_agent_connections_r8.py`.
   - ⚠️ First-party clients (D-EA-11, §10.7). For a client on `agent_first_party_client_ids` (default empty), `check` answers `first_party` with a code and no ticket, only on a live connection whose scopes cover the request, with `state`, to an `https` redirect at the client's own origin. Every other check runs first. `approve` then replaces the member's older connection of that client. A client off the list keeps the screen. Fence: `test_agent_first_party.py`.
0l. my_day_loop.py -- **The `my_day` loop** (`my_day_feed.md` §8.3, §10.9). `note_active` is the one writer of the active set. ⚠️ The set holds the session's EXACT address, never a lower-case copy. `MY_DAY_LOOP_ENABLED` (default OFF) has one reader. `_organization_ids` is the one unbound read. Fences: `test_my_day_loop.py`, `test_my_day_loop_r8.py`.
0m. org_facts.py + routes/org_facts.py -- **The ONE read seam of org facts.** Each app registers one provider. `set_by` is a role, never an email. Do not add a second store, and do not use Mem0 `org:global` for facts. Fences: `test_org_facts.py`, `test_org_facts_r8.py`.
0n. agent_run_sweep.py -- **The sweep of stranded agent runs.** It is the only writer of `pm_agent_run_strands`. The run writes stay in `routes/projects/agent_runs.py`. It never reassigns or moves a task. `AGENT_RUN_SWEEP_ENABLED` (default OFF) has one reader. Fence: `test_agent_run_sweep_r8.py`.
0o. routes/agent_apps.py -- **The gateway half of the app door** (MCP-P1a, `external_agent_access.md` §19.4, D136). `GET /agent-access/apps` and `GET /agent-access/apps/{app}` serve `list_apps` and `describe_app` of the edge.
   - Only a signed agent of branch 0 reads them. Any other caller gets 404, and so does each call while `agent_door_apps` names no app.
   - The answer holds the manifest and the state of the organization, never data of an app. The module opens no session.
   - Each args schema comes from `route.dependant` of the live route (AD-9), never from a hand list. `agent_oauth.door_available` decides each op.
   - Fences: `test_mcp_edge_app_door.py`, `test_mcp_edge_app_door_r8.py`, `test_app_door_coverage.py`.
0p. assistant_summaries.py -- **The ONE reader and writer of `chat_session.member_summary`** (WS-63 PZ-33a, `personalization.md` §15.3). No other module names the column.
   - Each function takes a session that the caller opened with `tenant_session(org)`, or the sync `acb_graph` session for `clear_on_share`. No function takes an org. Only `clear_for_member` takes a member, from the session or the member record. ⚠️ Each caller checks the member: a statement finds the chat by its id, and RLS binds only the tenant.
   - ⚠️ A write sets that column only. It never changes `updated_at`, `last_preview` or `message_count`.
   - ⚠️ `{"v": 1, "off": true}` is a Delete of the member. No mark and no write changes it. The marked write holds the mark in its WHERE, so a Delete during the model call wins.
   - The answer checks are pure. They call `typed_text`, `is_sensitive`, `needs_confirm`, `grounded`, `sensitive_terms` and `confirm_form_without_token` of `acb_common.assistant_profile`, and copy none of them. The stored text as one string must pass them too. An answer that does not parse writes nothing. An `Answer` comes only from `check_answer`.
   - The module imports no litellm and no streamed completion. An allow list holds its importers: `routes/rooms.py` (`clear_on_share`) and `routes/admin/members.py` (`clear_for_member`). A new importer fails `test_chat_summary_private.py` until its slice adds it.
   - Fences: `test_chat_summary.py`, `test_chat_summary_private.py`, `test_chat_summary_r8.py`.
1. main.py -- **The app factory and the lifespan.** It also serves `/copilot/chat`, the AG-UI endpoint. ⚠️ **A loop's flag gate lives INSIDE its start function, never as an `if` in main.py.** So each flag has one reader. main.py starts and stops each loop UNCONDITIONALLY. Fences: `test_launch_defang_kill_switches.py`, `test_crm_zoho_sync.py`, `test_ingestion_consumer.py::test_gateway_lifespan_starts_the_consumer_and_stops_it_after_yield`.
2. routes/agent.py -- **The agent run doors, the registry and the mutation inbox.**
   - **The endpoint check is the boundary of record.** The three run doors call `assert_can_run_agent`. The filtered `GET /agent` is UX only. Registry writes need `agents:manage`.
   - ⚠️ `/agent/webhook/{source}` starts a run from the internet. It checks HMAC-SHA256 over the raw body, and it FAILS CLOSED with 503 when no secret is set.
   - The run doors check the room and call `chat.prepare_new_session`. Fences: `test_h227_thread_scope.py`, `test_agent_list_tier_routed.py`.
   - 🔴 **The session names the run's member, never the body** (H-337). `_drop_identity_claims` removes `user_email` and `user_id` before the executor reads them. The stream door removes each claim. `/agent/run` and `/agent/run/async` keep a claim of the session's own member, and write the session's address in its place. A service caller keeps its claim. Fence: `test_chat_payload_identity_claims.py`.
3. routes/chat.py -- **Chat history and `GET /chat/active-sessions`.**
   - **Authorization is MEMBERSHIP, not ownership.** Each predicate goes through `gateway/rooms.py`. **Every helper opens `acb_graph.tenant_session(organization_id)`, never `get_session()`.** A session of another tenant gets no access and no write. Fences: `test_chat_write_under_rls.py`, `test_rooms.py`.
   - 🔴 **A human turn changes only when its own author writes it.** `_attribute` takes the email from the caller, never from the body.
   - 🔴 **Only the run's fold changes an agent reply, and the fold seals it.** No write updates a system row. ⚠️ The fold id comes from `routes/agent.py::fold_message_id`. Do not go back to `assistant-{thread}-{run_id}`.
   - 🔴 **Only the server creates an agent row or a system row.** Only `routes/agent.py` passes `mint=True`. ⚠️ `_ensure_session` runs at every run start, so it must never raise a role.
   - 🔴 **Each text and JSON value passes `acb_common.pg_text.storable` before a write.** Fence: `test_chat_nul_persist.py`.
   - A custom event of `RUN_MEMBER_ONLY_EVENTS` reaches only the member that the row's `run_member_email` names (`_events_for_reader`, PZ-20b). A chat that becomes shared then shows another reader no mark. Fence: `test_profile_used_event.py`.
3a. routes/rooms.py + rooms.py + room_stream.py -- **The room layer** (`groups_sessions_authority.md`). `resolve_room_access(session_id, email)` is the single source of room authorization. ⚠️ A **missing session row** is permissive, but a **failed lookup** gives NO access. Agents live in `chat_session_agent`, not in `chat_session_participant`, because an agent has no access of its own. `SOLO_ROOM_SQL` is the rule of `room_is_solo` as SQL, for a chat and its own member (PZ-33a). A change to one form changes the other. Fence: `test_chat_summary_r8.py`.
4. routes/oauth.py -- **OAuth for Zoho.** `GMAIL_SCOPES` in `email_ingestion/providers/gmail.py` is the one Gmail scope set. No other file may name a Google scope. Fence: `test_gmail_one_scope_set.py`.
5. routes/integrations.py -- **The Integration Registry, the MCP servers and the plugins.**
   - **Every `provider_keys` call passes `organization_id=current_tenant()`.** Never take the org from `req.service`, a header or the body (R5, R11). Fence: `test_credential_tenant_threading.py`.
   - 🔒 **No route here or in `routes/oauth.py` writes `os.environ` or the env file.** One env serves every tenant. Fence: `test_integrations_in0.py`.
   - 🔒 Each writer needs `integrations:manage`. 🔒 `mcp_servers` is RLS-exempt, so its list and delete filter on `current_tenant()`. 🔒 `/mcp/test` and plugin install fetch only through `outbound_guard.py`. Fence: `test_integrations_mcp_tenant_r8.py`.
   - 🔒 **`acb_common/env_guard.py` holds the key and value checks.** Only a key in `BUILTIN_ENV_KEYS` is stored as an `integration` row. A mail app key is a platform name. Fences: `test_integrations_env_hardening.py`, `test_integrations_mail_app_keys.py`.
   - ⚠️ `acb_llm/key_store.py` makes three untenanted calls that the walker cannot see. They are owner-gated (`work_plan.md` §6 gate (f)).
5a. routes/integrations_skills.py -- **The read-only skills catalog** (`skills_registry.md`). A tool pack loads only through `orchestrator._tool_injection._pack_owner_module`, and it runs no tool. Fence: `test_tool_packs_registry.py`.
5b. routes/agent_skills.py -- **The skill toggles of each agent.** The PUT needs `admin:access:manage`. `core` and `apps` cannot toggle (422). `orchestrator/_tool_injection.py::_resolve_injected_scope` applies the rows as an intersection only.
5c. routes/github/ + connections/github_app.py -- **The Metorite GitHub App** (`github_integration.md`), dark behind `GITHUB_APP_ENABLED`.
   - ⚠️ The installation token lives in PROCESS MEMORY only. It never goes to Redis, the database, a log or an answer.
   - `connections/github.py` is the ONE reader of a connection. `PUT /integrations/github/token` is the ONE writer of a pasted token.
   - `POST /webhooks/github` has a fixed order of checks: the size cap, the HMAC over the raw bytes, the headers, the org, then the delivery key.
   - Merge-to-Done moves a task only when an agent worked it. `agent_worked.AGENT_WORKED_SQL` is the one copy of that rule. A checks read holds no session across a GitHub call.
   - Fences: `test_github_app_auth.py`, `test_github_connect_r8.py`, `test_github_per_org_r8.py`, `test_github_webhook_r8.py`, `test_github_merge_r8.py`, `test_github_reconcile_r8.py`.
6. routes/memory.py -- **Memory search and management.** The path parameter is a SCOPE KEY (`acb_memory.scope_key`), not a user id. `_authorize_scope` has one rule for each shape, and refuses an unknown shape.
   - Your own email only. An admin does not read a member's private memory.
   - `agent:<name>` needs `agents:run:<name>`. `org:global` needs `memory:read_org` or `memory:write_org`. `room:<thread_id>` needs room membership.
   - A service principal must assert `X-User-Email` to touch a person's scope. An omitted identity fails closed. `delete` checks that the memory is IN the scope.
7. routes/settings.py -- **LLM settings and model config.** Every `model_config` and `provider_keys` call passes `organization_id=current_tenant()`. Fence: `test_credential_tenant_threading.py`. ⚠️ The `os.environ` half of `_inject_env_into_litellm` is a known cross-tenant write. 🔒 `_write_env_key` takes the env-write guard of item 5. Fence: `test_integrations_env_hardening.py`.
8. routes/email/ -- **Email accounts, messages, send, sync, AI chat and the connect flow** (`email_app_master_plan.md`).
   - ⚠️ **`scheduler_hooks.py::process_new_mail` is the one new-mail pipeline.** Each step runs in its own `try`, so no step failure breaks mail sync. The CRM step runs only inside `if auto_lead_enabled():`. ⚠️ Deep resyncs and first syncs reach it with up to 6 months of mail. A new step that acts on each message needs its own history check.
   - **The connect flow.** Both legs are GATED, and the browser reaches each leg through a Next BFF route. ⚠️ **The callback is not public, and it must not become public (risk R-4).** Do not add `response_mode=form_post`. Fences: `test_email_oauth_state.py`, `test_email_oauth_authorize_wiring.py`, `test_email_tenant_bind_rls.py`.
   - **The jobs bind a tenant.** Each job opens `_tenant_session()` with the ambient tenant. ⚠️ No job may call `commit()`, because a commit ends `SET LOCAL`. ⚠️ `routes/email` keeps ONE `_get_db()` site. Fences: `test_email_automation_tenancy.py`, `test_email_request_jobs_tenancy.py`.
   - ⚠️ **D-EM-4: a mailbox is private to the member who connects it.** Each handler shows an owner proof in its own body, or has an `OWNER_SCOPE_EXEMPT` entry. Fence: `test_email_owner_scope_fence.py`.
   - 🔴 **`mailbox_access.py` is the ONE mailbox access seam.** Never write `user_id = :uid` by hand. Another inline site needs a `# private-only: <reason>` comment. Fences: `test_email_owner_predicate_seam.py`, `test_email_shared_mailbox_r8.py`.
   - ⚠️ Do not write a conflict target on `email_accounts`. A new child of `email_accounts` must be `ON DELETE CASCADE`. Fence: `test_email_account_unique_per_tenant.py`.
   - **Hold no session across a provider call or a model call.** ⚠️ Never call `_sync_account`, `_run_manual_sync` or `hold_mailbox` inside a `_tenant_session` block. Do not call a composed form such as `classify_matches` in a job body. Fences: `test_email_sync_one_at_a_time.py`, `test_email_draft_no_stall.py`.
   - **A disconnect stops the sync first**, with no block open. ⚠️ Never call a teardown leg from outside `teardown.py`. Never call `finish_teardowns` inside an open transaction. Fences: `test_email_disconnect_order.py`, `test_email_mailbox_teardown.py`.
   - ⚠️ `memory_purge.py` refuses a key with no account id, and `MemoryClient.delete_scope` refuses `"*"`. Keep the `email_mailbox_purges` write inside the `DELETE` block. Fences: `test_email_disconnect_memory_purge.py`, `test_email_mailbox_purge_durable.py`.
   - **The model cap.** Each model await sits inside `llm_slot`, or goes through `core._llm_json`. ⚠️ Wrap the leaf await only. Each call names its feature from `core.EMAIL_AI_FEATURES`. 🔴 `EMAIL_LLM_BUDGET_MODE=enforce` on a box is OWNER-GATE. Fences: `test_email_llm_cap.py`, `test_usage_attribution.py`.
   - 🔴 Do not write `body_html` in a new SQL text. `email_ingestion.html_tier` is the one reader of `EMAIL_HTML_FROM_PROVIDER`. Fences: `test_email_html_tier.py`, `test_email_html_hot_only.py`.
   - **The one-of rules.** `transport/attachments.py::_fetch_owned_attachment` is the one owned fetch of a file. `mailbox_identity.py` decides the label and the colour of a mailbox. `automation/identity.py` holds the "self" set, and a From header is not proof. `automation/rules.py::auto_run_on` is the one reader of `auto_run`. `senders._stored_unsubscribe_link` is the one read of a stored link. `insights_store.write_facts` is the one writer of `email_insights`. `classify_addresses` is the one classifier of the held card.
   - Automatic reply drafting is OFF for a new mailbox. Do not put `DRAFT_EMAIL` back in `_PRESET_RULES`. An agent must not run "Remove older mail" on a production mailbox. Fences: `test_email_auto_draft_defaults.py`, `test_email_storage_limit.py`, `test_email_background_writes.py`.
9. routes/v1_compat.py -- **The OpenAI-compatible `/v1/chat/completions` endpoint.** ⚠️ **This choke point is `H4` for tenancy and cannot be threaded here.** Its Bearer is the deployment-wide `LITELLM_MASTER_KEY`, so `current_tenant()` is `None`. Do not derive an org from `x-cc-agent` or the body (R11). A fix needs a tenant-scoped API key (`work_plan.md` §6 gate (f)). Fence: the H4 count in `test_credential_tenant_threading.py`.
10. routes/debug.py -- **Diagnostics over the agent_run trace store.** EXECUTIVE/AGENT-gated.
11. routes/observability.py -- **Live observability over the activity bus** (`cc:activity`). EXECUTIVE/AGENT-gated. `acb_llm.context._infer_app_source()` reads the caller's `gateway.routes.<app>` module, so a new app needs no wiring.
12. routes/notes/ -- **The AI Note Taker `/notes` API** (`note_taker_app.md`).
   - **A meeting is private to its `owner_email`.** The owner predicate is written ONCE, in `core.OWNED_MEETING_PREDICATE` and `core.load_owned_meeting`. It is case-insensitive. A miss is 404, never 403.
   - **Dispatch acts AS the owner**, so `dispatch.cross_owner_refusal` gates `_dispatch` and `_dispatch_email`. Count the request paths that reach them, not the call sites.
   - **There is no sentinel actor.** `auto_dispatch(meeting_id, triggered_by)` takes the identity of the member who started the job, as a REQUIRED parameter.
   - `/live/wanted` and `/stt/bot-live-token` stay machine-authed with `MEETING_BOT_TOKEN`, on purpose. ⚠️ `/notes/meetings/{meeting_id}/live/wanted` is in neither `main.PUBLIC_ROUTES` nor the router's `exempt` list. So the gate refuses the worker with 401. This is filed, not fixed, because the repair opens a route.
   - ⚠️ **Still NOT owner-scoped (spec §4, N5):** the note and action reads in `summaries.py`, `copilot_context.py`, `copilot_agenda.py`, the `/bot*` routes, `live_transcript.py`, `live_session.py`, `speaker_id.py`, `agenda_progress.py` and `events.py`.
12a. routes/tasks/ -- **The task-manager `/tasks` API** (`task_manager_app.md`). Task reads and writes go through `item_source()` and `agent_source()`, over the one store (D53).
   - **The people directory is NOT owner-scoped, and it must never be.** The rule is "directory open, HR fields restricted". `core.py` defines `can_read_hr_fields` and `require_people_write()` over existing capabilities. Never add a new slug.
   - ⚠️ `_row_to_person(row, *, include_hr)` has **no default**, on purpose. The projection is at the serialization layer, never in the SQL. Fences: `test_tasks_people_scoping.py`, `test_no_task_provider_connectors.py`.
12c. routes/crm/ -- **The native CRM `/crm` API** (`crm_app.md`, `crm_platform.md`). `core.py` is the leaf. `broker_handlers.py` and `auto_lead.py` register no routes and are not imported from `__init__.py`.
   - ⚠️ **Every `crm_*` table carries the TENANT `organization_id`, under FORCE RLS.** No CRM model has an `organization_id` field. Fence: `test_crm_rename_upgrade.py`.
   - ⚠️ **`flags.py` is the CRM kill switch.** `require_crm_enabled` runs FIRST, so an org outside `CRM_ORGS` gets 404, never 403. ⚠️ `access.py` is the third router dependency. Its groups come from `routes/projects/core._MY_GROUPS_SQL`, imported and never copied. Fences: `test_crm_kill_switch.py`, `test_crm_access.py`.
   - **Visibility is org-wide to `feature:crm` holders** (D-CRM-3). `owner_email` is assignment, not an ACL. Do not align this with `routes/notes` without a new decision.
   - ⚠️ **Writes go through `core.insert_row`, `update_row` and `upsert_by_zoho_id` only.** Sort keys are an allowlist, and each caller value is a bound parameter. ⚠️ Write each path out for each entity, never `/crm/{entity}/{id}`. Fences: `test_crm_routes.py`, `test_crm_migration.py`.
   - **A status transition has THREE effects, always**, through `pipeline.apply_status_transition`. ⚠️ **Stage entry rules are checked in exactly TWO places.** A third is forbidden (D-CRM-13). The import and sync create path must stay ungated. Fence: `test_crm_pipeline.py`.
   - **The Zoho sync.** `ingestion/sources/zoho/writer.py` is the ONLY Zoho write surface, and `sync_zoho.execute_push` is its only caller. A queued push must NOT clear `zoho_dirty`. Enabling `CRM_ZOHO_SYNC` and a hand-run cycle against prod are OWNER-GATE. Fences: `test_crm_zoho_sync.py`, `test_crm_sync_tenancy.py`.
   - ⚠️ **The email timeline is the ONE place scoped to the CALLER.** A third copy of the account scope means a shared module. Fence: `test_crm_email_timeline.py`.
   - ⚠️ **`auto_lead.py` is an UNATTENDED writer.** The flag is read at the call site. A failure never moves the cursor past lost work. Dedup is a SELECT guard, never `ON CONFLICT`. The step never reads a mail body. Fence: `test_crm_auto_lead.py`.
   - **The read routes.** `records.list_query` is the one list builder of `aggregate.py` and `export.py`. ⚠️ The field SQL lives in `field_sql.py` only. `records.refuse_nul` is the one NUL guard. An export past `MAX_EXPORT_ROWS` is a 422, never a partial file. Fences: `test_crm_aggregate.py`, `test_crm_nul_guard_r8.py`, `test_crm_export.py`.
   - ⚠️ **`stage_metadata.py` is dry-run by default**, and `?apply=true` is the owner gate. ⚠️ `POST /crm/reset` is the one route that removes CRM records. Fences: `test_crm_stage_metadata.py`, `test_crm_reset_r8.py`.
   - **The names of the core records** (CRM-Z10g, D133.1). Only the rename, two clears and the switch write `crm_settings.labels`. Do not add another writer. `PUT /crm/settings/labels` in `settings.py` is the one admin rename. It is admin only and Native only, and Mirror mode gets 409. The `mode = 'native'` guard sits in the WHERE of the UPDATE. Two clears set the names and the cutover stamps to NULL: the start over (`_CLEAR_SETUP_SQL`) and "Change CRM setup" (`_reset_setup`). The CRM-Z10b switch copies the source names in at the cutover. `GET /crm/mode` serves the stored names in Native mode with no fill-rank flag, in the shape of the Mirror labels. Fence: `test_crm_labels_r8.py`.
   - ⚠️ **The switch from Mirror to Native lives in `cutover.py` only** (CRM-Z10b, `crm_platform.md` §3.4). `fold_cutover_overlay` and `own_cutover_fields` are its two raw writes to guarded tables, and no other file names them. `module_state` is the one rule of a module that is not complete. ⚠️ Phase 1 always starts its own run, and phase 2 switches only when the newest run IS that run (`run_id`). A fresh run is no shortcut, because a one-module run looks the same. Phase 2 also needs that run to have read each imported key (reason `not_read`). ⚠️ The proof of a read is its END in the run (`checked_at` in the cursor, after `planned_ms` of the run), never a key in `counts`. Phase 2 also waits for the deals in the roles queue (`deal_contacts`, reason `roles_waiting`). A one-module run names its keys in `counts["run_only"]` (`engine.RUN_ONLY_KEY`). ⚠️ Phase 1 starts the final sync with `engine.acquire(final=True)`, which reads every generic module now, with no wait of `MODULE_READ_EVERY`. The final sync has its own budget (`engine.final_budget`, one day's share once in 24 hours), and it treats the Leave out keys as held. Phase 2 takes only a run with `counts["run_final"]` (`engine.RUN_FINAL_KEY`). Phase 2 takes the connect lock first, then `crm_settings` `FOR UPDATE`. `disconnect_zoho_reporting` holds the connect lock from its status read to its UPDATE. The write guard and the auto-lead step read the mode `FOR SHARE`. `CRM_CUTOVER_ORGS` ships empty. Fences: `test_crm_cutover_switch_r8.py`, `test_crm_upsert_from_source_fence.py`.
   - **The agent consumer.** `apps/agents/agent-crm` calls these routes over HTTP as the caller. A shape change here breaks an agent tool. Its allowlist is `{GET, POST, PATCH}`, so no agent path deletes a record.
12d. routes/projects/ -- **The Projects API over the one task store.** The package is H2-converted, so it refuses a `get_db()`.
   - **The chat is a second consumer.** `skill_projects/manifest.py` maps EVERY `/projects` route to a tool, or excludes it with a reason. A new route fails CI until it has an entry. Fence: `test_projects_chat_coverage.py`.
   - ⚠️ **`authority.py` is the one rule of who may delete and who may write a grant.** Do not write a second copy. Fence: `test_projects_write_authority.py`.
   - ⚠️ **`agent_runs.py` is the only writer of `pm_agent_runs` and `pm_agent_run_activities`.** `transition()` is the status machine. The long poll sleeps with no session open. Fences: `test_agent_runs_r8.py`, `test_agent_run_exclusive.py`.
   - ⚠️ **`tasks.py` narrows a signed agent write** to `AGENT_CREATE_FIELDS` and `AGENT_PATCH_FIELDS`. An agent never widens who sees a task (fences: `test_mcp_edge_assignees.py` r13, `test_mcp_edge_projects.py` r11 to r13). Only `claim_task` calls `core.lock_task`. Fences: `test_mcp_edge_task_writes.py`, `test_mcp_edge_claim.py`.
   - ⚠️ **`tree.py` narrows a signed node write** to `AGENT_NODE_CREATE_FIELDS` and `AGENT_NODE_PATCH_FIELDS`, so an agent names no lead (a grant write). A connection with pins makes no space (403). `PATCH /nodes/{id}` takes `If-Match`, and an absent header passes. `move.py` refuses a signed move with no `accepted_drops`. An agent never widens who sees a task. `move._move_audience` compares the members of the source and of the destination through `notifications.project_grant_holders`. A wider move gets 403 `audience_widens`. Fences: `test_mcp_edge_projects.py` (r11 to r13) and `test_mcp_edge_assignees.py` (WS-55 MCP-G1 and MCP-G2).
   - ⚠️ **`core.precondition_holds` is the version rule of a task write.** Do not add a third strict check on `updated_at`. Fence: `test_task_precondition_fields_at.py`.
   - Every route that returns a task row calls `filters.attach_agent_state`, one query for each field. `analytics.py` holds the ONE rule of who did the work. The report sections join rules that exist and copy none. Fences: `test_projects_pr_chip_r8.py`, `test_projects_analytics_actor.py`.
   - ⚠️ **Two modules write `reminders`**: `routes/projects/reminders.py` and `gateway/reminders/loop.py`, both through `reminders._ledger`. Each statement filters on `member_email` from the session. Fence: `test_reminders_r8.py`.
   - ⚠️ **`refs.py` is the ONE network call here.** Its host is fixed, and it holds no session during the read. Fences: `test_task_refs_route.py`, `test_task_refs_text_links.py`.
13. routes/apps/ -- **Custom Apps, the App Workshop `/apps` API** (`docs/app-workshop/README.md`). `_common.py` is the leaf. `ensure_workspace` is the read-path choke point. `_TOOL_REGISTRY` is empty (D52), but the bridge stays. Request args can never override a frozen constraint. A destructive tool goes through the Action Broker. An action's `readonly` is derived, never trusted from the manifest. ⚠️ `record_app_audit` stays on the unbound seam until H4, and `app_audit` has FORCE RLS. A caller in a bound request writes its audit row with `record_app_audit_bound`. Both run the one statement `_APP_AUDIT_SQL`. ⚠️ A Studio package (`format = 'manifest'`, WS-STU A3) is not an app here. `load_app`, `list_apps` and `list_pinned_apps` skip it with `format IS DISTINCT FROM 'manifest'`, so each slug route answers 404 for it. A new reader of `apps` here needs the same guard. ⚠️ An empty or blank `workspace_path` never means the working directory (`workspace_dir`). A write path (publish, files, sync, delete, rehydrate) calls `app_workspace`, which answers 409 for such a row. The writer calls it BEFORE its statement commits, so the 409 writes nothing. A read path (`lifecycle._to_detail`, `runtime._effective_manifest`) calls `app_workspace_or_none` and falls back to the stored manifest. Fences: `test_studio_packages_r8.py`, `test_studio_packages_store.py`.
14. routes/admin/ -- **Org access control: `/admin` and `/auth/me`** (`org_access_control.md`).
   - `GET /auth/me` is NOT admin-gated. It returns resolved OUTCOMES, so the matching rule has one implementation.
   - ⚠️ **The `/admin` auth floor is PER-ROUTE.** Each route declares `Depends(require_admin_user)` in its own signature.
   - `_common.py` holds the invariants. The org keeps an owner. Nobody assigns a role above their own rank. System roles are immutable. Nobody locks themselves out. ⚠️ Two guards answer 409, so a test must check the detail text. Fence: `test_admin_member_offboarding.py`.
   - Feature modules import from `_common`, never from each other.
   - **`purge_member` is the hard delete**, a separate route, never a flag on Remove. It keeps the person's work and the audit trail. ⚠️ A KEEP clause must exclude all that the delete side CASCADES away. Only two tables are on both sides: `chat_session` splits on `visibility`, and `package_install` (WS-PKG PKG-4) splits on `scope_kind` and on `_OTHERS_SEE_THE_APP`. That term finds a grant on the app to a person other than the member. Fences: `test_admin_member_purge.py`, `tests/unit/_schema_cascade.py`.
   - ⚠️ **The provisioning `ON CONFLICT` arms name the statuses they rewrite, and never negate.** The target is `ON CONFLICT (lower(email))`. Approve never rewrites the roles of an existing member that is not `invited`. Fences: `test_signin_requests.py`, `test_app_user_upserts.py`.
   - The `/auth/me/*` routes take the member and the tenant from the session. 🔴 No staff route reads an assistant profile. Fence: `test_assistant_profile_private.py`.
   - `routes/operator.py` needs the internal bearer AND `X-Operator-Token`. Fence: `test_operator_door.py`.
   - `PATCH /internal/operator/organizations/{slug}` renames one org (CP-8). The org comes from the path slug, and the body allows only `name`.
   - **A rename has ONE body.** `routes/admin/organization.py::rename_org_row` holds the UPDATE of `organization.display_name`. Both rename doors call it after `clean_display_name`. The slug never changes. Fences: `test_operator_door.py`, `test_admin_organization_rename.py`.
   - After the commit, the admin rename mirrors the name to the Console, best-effort. It logs `org_rename_mirror_failed` with the slug and the name when the call fails. It logs `org_rename_mirror_wrong_org` when the Console answers for another org.
15. routes/workflows/ -- **The Workflows app `/workflows` API** (`workflows_app.md`, `docs/workflow-editor/README.md`). Publish compiles the edit-model to an immutable run-model, and a run pins its version.
   - **The engine (`engine/`) is transport-free**, with no FastAPI or DB import. Agent nodes call `orchestrator.executor.run_agent`. Write-class tool nodes go through `action_broker.propose/submit` and fail closed.
   - ⚠️ **`__init__.py` import order is load-bearing.** Static paths come before `/{workflow_id}`.
   - The palette is never hard-coded. The copilot never writes the workflow row.
   - **Publish, rollback, disable and enable need `workflows:publish`**, because they ARM triggers.
   - The webhook trigger is public by token. `core.hook_url()` builds the hook URL. The browser must NEVER build one from `window.location`. A duplicate always gets a new hook token.
   - **Schedules are DB rows.** The claim is a CAS that commits BEFORE `start_run`, so each exit path calls `service.record_skipped_run()`. ⚠️ `_claim_baseline` arms a new schedule, because `compute_due_fire` only looks forward.
   - A resume replays with stored outputs, so no side effect repeats. At startup `reconcile_orphaned_runs()` runs BEFORE the scheduler. Auto-disable counts only `UNATTENDED_TRIGGERS`.
   - Fences: `test_workflows_tool_contract.py`, `evals/trajectories/test_workflow_engine_trajectory.py`, `test_workflow_fixtures.py`.
16. agents.json -- **The dynamic agent registry**, beside `pyproject.toml`.
17. routes/signin.py + routes/signup.py + routes/seats.py + routes/admin/members.py -- **The Customer Console seam.** `acb_auth.console_resolve` is the ONE Console client, and the only reader of `CUSTOMER_CONSOLE_URL` and `CUSTOMER_CONSOLE_DEPLOYMENT_KEY`. The browser never holds the key.
   - The actor is the session email, never the body (R11). The routes are not in `PUBLIC_ROUTES`, and they ship dark. Unwired, they refuse and never succeed silently. The invite mirror runs after the commit, best effort.
   - ⚠️ **Exactly FOUR importers of `console_resolve` are allowed.** Fence: `test_console_dependency_boundary.py`.
   - ⚠️ `signup.py` refuses a reserved label. `subdomain.ts` owns the set. Fence: `test_subdomain_host_vocabulary.py`.
   - Fences: `test_signin_resolve_route.py`, `test_signup_provision_route.py`, `test_seat_admin_proxy_route.py`, `test_invite_console_mirror.py`.
18. routes/whatsapp_channel/ -- **Chat on WhatsApp, the assistant channel** (`whatsapp_assistant_channel.md`). `routes/whatsapp/` keeps the inbox app.
   - `flags.py` is the ONE reader of the `WHATSAPP_ASSISTANT_*` settings. A dark channel answers 404 and opens no session.
   - `inbound.py` does nothing without `WHATSAPP_APP_SECRET`. Its two unbound reads go through SECURITY DEFINER functions. ⚠️ The failed-code limit lives in the process.
   - Only the insert that wrote the `whatsapp_bot_messages` row starts a run. That table holds no message text. A message gets at most one reply. The bot never touches a shared room.
   - `acts.py` needs `WHATSAPP_ASSISTANT_WRITES` (default OFF). A Confirm runs the stored call once, with no model.
   - Fences: `test_wac_link_code.py`, `test_wac_link_table.py`, `test_wac_bot_run_r8.py`, `test_wac_writes_r8.py`.
19. routes/shell/ -- **The shell's own routes** (`navigation_shell.md`, `my_day_feed.md`).
   - `state.py` is the only writer of `my_day_card_state`. Its POST takes the member from the session. Fence: `test_my_day_state_r8.py`.
   - `cache.enabled` is the one reader of `MY_DAY_CACHE`. A request with `user.agent` set reads and stores nothing. A write that changes the feed bumps a version after its commit. Fence: `test_my_day_cache_r8.py`.
   - A gated source runs only when `MY_DAY_PROVIDERS` lists it. A failed read is `failed`, never an empty `ok`. Each source read names the tenant and the member beside RLS. Fences: `test_shell_feed.py`, `test_feed_fold_fingerprint.py`.
   - `feed_decide.py` runs in shadow. It is the one writer of `my_day_judgments`, and the feed never reads an answer. Fence: `test_feed_decide_r8.py`.
20. routes/knowledge/ -- **The Knowledge app `/knowledge` API** (`knowledge_base.md`). `core.py` is the leaf. `flags.py` is the one reader of `KNOWLEDGE_ENABLED` and `KNOWLEDGE_ORGS`.
   - ⚠️ **`core.visible_pages_clause(user)` is the ONE predicate of who sees a page.** Do not call `resolve_visibility`, and do not copy `_VISIBLE_PROJECTS_SQL`. Fence: `test_kb_visibility_one_seam.py`.
   - A hidden page answers 404, never 403. ⚠️ Take the tree locks BEFORE the page row lock. Fence: `test_kb_pages_r8.py`.
   - ⚠️ Accept and reject of a proposal refuse `chat`, `mcp` and every agent, so the agent never publishes. Fence: `test_kb_proposals_r8.py`.
   - ⚠️ A read of `kb_page_versions` calls `core.load_page` first. Fence: `test_kb_history_r8.py`.
   - ⚠️ A source disconnect deletes the blob and nodes first, then revokes only an unshared grant. Fence: `test_kc_sources_r8.py`.
19a. retrieval/ -- **Company retrieval, the one seam** (D105). Each source checks `feature:<app>`, then calls its app's own search. So it writes no SQL and no visibility rule. The command bar's `PROVIDERS` is a projection of the same registry. The email source passes `with_total=False`, so it skips the COUNT that the Email app shows (KG-3a). Fences: `test_company_search_sources.py`, `test_company_search_r8.py`, `test_email_fts_column_r8.py`.
19b. kb_index/ -- **The Knowledge index.** `embed.embed` is the ONE door that makes a vector. ⚠️ Each read of `kb_chunks` joins a published `kb_pages` row under `visible_pages_clause`. ⚠️ Never store a zero vector. Do not import `litellm` or `openai` here. Fences: `test_kb_one_index.py`, `test_kb_chunk.py`.
19c. kb_entities/ -- **The entity registry.** ⚠️ `ensure_entity` is the only `INSERT INTO kb_entities`. ⚠️ Each read takes its FROM and `where` from `owner_rules`. Fence: `test_kc_entities_r8.py`.
19d. kb_sources/ and kb_sync/ -- **Company file sources.** The credentials come in from the caller. The Drive scope is `drive.readonly` only. `kb_sources/http.py::GuardedHttp` is the one network door. No session is open during a Google call. `kb_sync/engine.py` holds the one lock order. ⚠️ `visible_documents_clause` is the ONE document predicate. Fences: `test_kb_sources_no_global_creds.py`, `test_kc_sources_read_only_scopes.py`, `test_kb_sync_tenancy.py`, `test_kc_document_visibility.py`.
21. push/ -- **Web push** (`calendar_events_reminders.md` §5.9). `store.py` is the one writer of `push_subscriptions`. ⚠️ The flag is a router dependency. ⚠️ A member supplies the endpoint, so `endpoints.py` and `outbound_guard.request_https` check it first. Each statement filters on `member_email`. Fences: `test_push_platform.py`, `test_push_subscriptions.py`.
22. reminders/ -- **The reminder loop** (D114). ⚠️ One row, one transaction, with `FOR UPDATE SKIP LOCKED`. ⚠️ No WhatsApp message names a reminder (D114.7). Fences: `test_reminder_loop.py`, `test_reach_loop.py`.
   - **The web push** (RE-9b, `calendar_events_reminders.md` §5.6). `_fire`, `_check_back` and `_give_up` plan one push each through `reach/push_channel.plan_reminder_push`, on the session of the step. A step more than the TTL of its kind after its due time plans no push (ruling j). `run_tick` takes the steps of EVERY org first, then one send phase calls `push_channel.send_planned` for each org under one deadline (`SEND_PHASE_S`, ruling k). Each tick starts at the next org, and each org has a fair share of the time, so no org starves. ⚠️ Do not move a send back into `run_org`: a stalled push service would delay the fires of each later org. Only `push.sender.send_to_member` reaches the network. ⚠️ Add no session site in `reach/push_channel.py`. The send step opens its sessions in `reach/outbox.py` only. ⚠️ A send step claims a row (`reason = 'sending'`) and holds no session during the send. Fences: `test_reach_push.py` (R8), `test_reach_plan.py`.
   - `context.py` is pure. It holds the words of a tap that opens the chat (RM-4). `routes/chat_reminder.py` calls `routes/projects/reminders.open_reminder`, the ONE open, then posts through `chat_brief._post_row`. No model runs. ⚠️ Each word comes from the member's own row, never from the request body. Fences: `test_reminder_context.py`, `test_reminders_r8.py`.
22a. jobs/ -- **The shared durable job queue** (WS-BRIEF BR-1a, `docs/job_queue.md`). `queue.py` is the one writer of `jobs`. ⚠️ No function takes an org: `enqueue` takes it from the bound tenant by the column DEFAULT. ⚠️ A claim is `FOR UPDATE SKIP LOCKED` with a fence token, and a settle matches the token. The handler runs in a SAVEPOINT of the settle transaction. It must not call `commit()` or open a second session. ⚠️ A handler runs for at most `worker.HANDLER_TIMEOUT`, which stays below `queue.LEASE` and the 600 s idle timeout. A call to a provider or a model waits for the detached mode of BR-1b (`docs/job_queue.md` §5a). `JOB_QUEUE` (default OFF) has one reader, `worker.worker_enabled`. ⚠️ `worker.seed_hooks` is the ONE recurring seam: a hook runs once for each org and 15-minute bucket (`docs/job_queue.md` §5c). Add no second loop. Fence: `test_job_queue.py`.
22b. brief/ -- **The core brief** (WS-BRIEF, `core_brief.md`). `store.py` is the one writer of `brief_runs` and `brief_claims`. ⚠️ Each function takes a session that the caller opened with `tenant_session(org)`, and no function takes an org. ⚠️ The member is a required argument, and each statement filters on it. The store does not authenticate. The caller resolves the member from a trusted source, never from request input. ⚠️ `href` is a string that code built, never a model value. Add no second `<app>:<ref>` grammar: the SQL CHECK is the fence. `prune_runs` is the one retention delete, and the prune hook of `run.py` calls it with a 90 day cut-off. The member purge (`routes/admin/members.py`) names `brief_runs`, and the claims go by cascade. ⚠️ `set_state` with `from_state` moves a row only along `store.TRANSITIONS`. Fence: `test_brief_store_r8.py`.
    - BR-4b: `brief/fence.py` is a pure check of the phrase answer. It uses `numbers_in` of `acb_common.assistant_profile` and has no number pattern of its own. BR-2a-2 calls it. Fence: `test_brief_claim_fence.py`.
   - **BR-2a-1, the planner and the gather** (`flags.py`, `planner.py`, `gather.py`, `run.py`). `flags` is the one reader of `BRIEF_ENABLED` and `BRIEF_ORGS`. `brief.plan` and `brief.run` are DETACHED and idempotent kinds, because the feed read opens the apps' own sessions. ⚠️ The org comes from the worker. The members and their three preferences come from ONE reader, `acb_common.assistant_profile.members_app_prefs`. It gives one entry for each member in any case, and `apps.brief` only. The run reads them again through `member_app_prefs`. Every active member gets the brief, minus the opt-outs (§9 Q1). The member writes them through `PUT /auth/me/assistant-profile` with the key `apps`. ⚠️ The PUT, the GET and both readers take ONE row rule, `APP_ROW_SQL_TEMPLATE`: the row that holds `apps.<app>` wins, then the lower-case row. No second store, and no SQL of the profile column here. ⚠️ The planner window is 2 hours back (`LOOKBACK`) and 30 minutes ahead. An older slot with no row gets a `slot_missed` row. ⚠️ A claim is built by code: `source_app` is the prefix of `signal_id`, never `card["app"]`, and `href` passes `orchestrator.in_app_path.is_in_app_path`, the one in-app check. A card with an unknown prefix or no href gets no claim (`brief.claim_skipped`). ⚠️ `unattended.begin` runs before `create_run`, and the member resolves before the row. A run that gathers nothing gives the slot back under `asyncio.shield`, a cancel of `HANDLER_TIMEOUT` included, so catch `BaseException` there. The slice calls no model and sends nothing. Fences: `test_brief_run.py`, `test_brief_run_r8.py`.
   - **BR-2c-1, the in-app delivery** (`routes/brief_today.py`, `routes/chat_brief.py`). `flags.in_app(org)` is the one reader of `BRIEF_IN_APP`, and it needs `org_allowed` too. The router of `GET /brief/today` takes the gate `feature:chat`. With the switch off, the route answers 404. ⚠️ The member is `user.email` and the tenant is the bound session. The request names no member, no org and no date. ⚠️ The one read is `brief_today.read_today`: the newest run (`store.read_latest_run`) whose slot has passed, dated today or the day before by the calendar date in `planner.member_zone`. Never key it on `Day.feed_day`. ⚠️ The GET is a pure read and writes nothing. `delivered` means "on an in-app surface", and `run.py` sets it after `ready` when `flags.in_app` is on. A future "seen" signal is a separate `seen_at` that an explicit POST writes, never a GET. The route enqueues nothing. With the switch on, `post_brief` shows a `ready` or `delivered` run through `read_today`, and reads no feed. Its stamp and its row id are the run's `local_date`. A failed stored read logs `chat_brief.stored_failed` and composes. Off, it reads no store. Fences: `test_brief_today.py`, `test_brief_today_r8.py`, `test_chat_brief_turn_r8.py`.
22c. jobs/unattended.py -- **The ledger of unattended runs** (WS-BRIEF AUTO-2, `docs/job_queue.md` §9). It is the one writer of `unattended_runs` and `unattended_incidents`. ⚠️ `begin` commits the row BEFORE the work. A taken slot answers `fresh=False`, so a restart never runs the slot again. ⚠️ An orphan, or a send that possibly ran, turns `unknown`, and nothing retries it. ⚠️ Call `recover` before any other check that can skip the slot. The first failure of a job and error class logs `unattended.incident_opened` at error, once. The Reach brief send and the member pass use it. `UNATTENDED_LEDGER` (default OFF) has one reader, `unattended.ledger_enabled`. Fence: `test_unattended_ledger.py`.
22d. studio/ -- **The skill store of AI Studio** (WS-STU F1, `ai_studio.md` §3.5). `skills_store.py` is the one writer of `studio_skills` and `studio_skill_versions`. This package holds no route: the routes are `routes/studio/skills.py` (F2a, below), and J1 adds the feature gate. ⚠️ Each function takes a session that the caller opened with `tenant_session(org)`, and no function takes an org. ⚠️ The member is a required argument, and each statement filters on it. In F1 a member reads only the skills that the member owns. The caller resolves the member from the session, never from request input. A new skill is `private`, and no function changes the visibility. ⚠️ A version is immutable. The store holds no UPDATE of a version, and the trigger `studio_skill_versions_immutable` refuses each UPDATE. `add_version` raises `current_version` and writes the version in one statement, so two writers get two numbers. A slug is unique for each owner, so `SkillSlugTaken` names no skill of a colleague. A tool pack id (F3) uses the skill `id`, never the slug. The body check is light, and the store does not import the A2 manifest check. ⚠️ It refuses a NUL, a lone surrogate and a float that Postgres writes longer, so its byte count is the count of the CHECK. ⚠️ The name and the description refuse a NUL and a lone surrogate too, and the slug check is `fullmatch`. So no text reaches a statement that Postgres refuses, and a refusal never echoes the value. The member purge (`routes/admin/members.py`) names `studio_skills`, and the versions go by cascade. Fence: `test_studio_skills_r8.py`.
    - **F2a, the skill routes** (`routes/studio/skills.py`, `ai_studio.md` §7). They register on the router of `routes/studio/actions.py`, so `main.py` mounts no second router. ⚠️ The first line of each route reads `flags.studio_allowed`. The org is the bound session and the member is `user.email`, never request input. A skill of another member or org is 404 `Not found`. ⚠️ Each body write runs the depth cap, then `check_body`, then the catalog check, BEFORE the store session opens. `actions.catalog_rows` is the one source of the catalog, and it opens its own session. ⚠️ `skills.TRANSITIONS` is the one state table. ⚠️ A move reads the skill with `store.get_skill(..., for_update=True)` before it applies the table, so two moves at once give one 200 and one 409. Do not take the lock with `set_state` of the old state, because that UPDATE writes the old state over a committed move. A new version of a published skill returns it to `draft` in the same transaction. No route changes the visibility. Fences: `test_studio_skills_routes.py`, `test_studio_skills_routes_r8.py`.
    - **A3, the package store** (`studio/packages_store.py`, `ai_studio.md` §3.6). It is the one writer of a Studio package: a row of `apps` with `format = 'manifest'`. ⚠️ Each function takes a session that the caller opened with `tenant_session(org)`, and the member is a required argument. Each statement filters on the bound org, the format and `owner_email`. ⚠️ It runs A2's `validate_manifest` before each write. Then it checks the JSON text itself, as `skills_store` does, so no manifest reaches a statement that the CHECK `apps_manifest_package` refuses. One `PackageStoreError` carries every path. ⚠️ `apps.slug` is unique for all orgs, so the slug is `<manifest id>-<8 hex digits of the new id>`. A taken slug writes nothing (`ON CONFLICT (slug) DO NOTHING`), and the store takes a new id. No refusal names a slug. A new package is private, a draft, and has `workspace_path = ''`. No function changes the format, and the trigger `apps_format_fixed` refuses it. ⚠️ `update_manifest` refuses a new manifest id, because the slug holds it. It reads the row `FOR UPDATE` first. ⚠️ No other module writes a package. `test_only_the_package_store_writes_a_package` scans `apps/` and `packages/` for an INSERT or UPDATE of `apps` that names `format` or `'manifest'`. The member purge deletes a package with the member's other `apps` rows. Fences: `test_studio_packages_store.py`, `test_studio_packages_r8.py`.
    - **A4, publish and rollback** (same module, over `app_versions`). `publish_version`, `rollback`, `list_versions` and `get_version` take the same session and member. ⚠️ Each locks the package row `FOR UPDATE` first, so two writers get N and N+1. Publish re-reads the stored draft, runs `check_manifest`, then `validate_manifest` WITH a `catalog` that the caller passes as `{id: class}` from `routes/studio/actions.catalog_rows` (a store must not import a route). It writes the version (`bundle_html = ''`, `bundle_sha256` of the manifest text, `review_status = 'auto'`), then `apps.status = 'live'` and `apps.live_version`, and leaves `apps.manifest`. ⚠️ `rollback` takes the same required `catalog` and checks the copy before any write. The store refuses a `catalog` of None, a lone surrogate or a secret in the notes, and a version outside 1 to 2^31-1. ⚠️ A rollback makes a NEW version that copies the older manifest, and it OVERWRITES THE DRAFT. The audit row is an `INSERT INTO app_audit` in the same transaction. ⚠️ Never call `record_app_audit` (its own session), never propose `app.publish_review`, and never update or delete an `app_versions` row. Fences: `test_studio_packages_store.py` (group h), `test_studio_packages_versions_r8.py`.
    - **C1, the typed object store** (`objects_store.py`, `ai_studio.md` §3.1). It is the one writer of `studio_objects`, `studio_object_fields`, `studio_records` and `studio_record_hot`. It holds no route. The door of B2a is its first caller. It reads a record by its id, and a page of records with a filter and a sort (C2a, below).
        - ⚠️ Only a Studio package owns an object. `_PACKAGE_SQL` needs `format = 'manifest'`, so `define_object` answers None for an App Workshop app. Fence: `test_r8_a_code_app_owns_no_object`.
        - `get_object_by_name` is the one read of an object by its name, and it reads one package only. The door uses it, because a manifest names an object by its name.
        - ⚠️ Each function takes a session that the caller opened with `tenant_session(org)`, and no function takes an org. No function commits or opens a transaction, so C1b can put several calls in one. The one exception is the SAVEPOINT of the query (C2a), and it ends nothing of the caller's transaction. Fence: `test_the_store_holds_no_route_and_no_transaction`.
        - ⚠️ The one check of a value is `acb_common.studio_fields`. The store keeps no rule of a field type, and A2 calls the same module. A bad value raises `FieldError` with the field key, and the session stays usable.
        - ⚠️ A record write fills its hot rows in the statement that writes the record. A field that turns hot gets a backfill in the caller's transaction, on an object of at most 50,000 records.
        - ⚠️ One lock order. Every write takes the advisory lock of the org first, then the row lock of the object. A registry change takes `FOR UPDATE` on the object row. A record write takes `FOR KEY SHARE` on it, then reads the fields. A read takes no lock. The trigger `studio_object_fields_hot_budget` takes the same advisory lock, and it refuses a 9th hot field of an object and a 25th of an org.
        - ⚠️ The org lock is exclusive, so the writes of one org run one at a time. Do not make it shared for record writes. Two joined transactions would each hold it, and each would then wait for the other at a registry change. Fence: `test_r8_joined_calls_in_two_sessions_never_deadlock`.
        - Each record function takes a required `package_id`. A statement reaches the record only when the object row of the bound org has that package. So a door of one package cannot reach a record of another package, also with a true object id. The actor is an argument from the session, never from request input.
        - A stored field reads with no authoring check (`studio_fields.stored_field`). A stricter rule binds a new spec only, so an older object still reads and writes.
        - The member purge names no object table. A record is company data, as a `pm_tasks` row is (D63).
        - Fences: `test_studio_fields.py`, `test_studio_objects_r8.py`, `test_admin_member_purge.py`.
    - **C2a, the object query** (`objects_store.query_records`, `ai_studio.md` §14 C2a). It reads one page of the records of one object, with a filter and a sort. No route calls it yet. B2b's `object:<name>.list` is its first caller.
        - The grammar is `acb_common/studio_query.py`: the shape, the op table of each type and the caps. It holds no SQL. B2b and the chat tool pass their input through it, and keep no second grammar.
        - ⚠️ The SQL stays in `objects_store.py`, so the fence of the one writer still names one module. A field key and a value are always binds. An f-string of `_q_*` and `build_*` takes only an index, a module constant or SQL built there.
        - ⚠️ Each statement filters on the bound org, the object and `_IN_PACKAGE`. A hot filter or sort reads `studio_record_hot`. A filter on a field that is not hot reads `data`, and it runs only on at most 5,000 candidate records (`hot_or_small`).
        - ⚠️ The statements run in `begin_nested()`, each under a LOCAL `statement_timeout` of 5 seconds. The store reads the old value first and sets it back in `finally`. A timeout raises `QueryError(query_timeout)`, and the caller's transaction stays usable.
        - ⚠️ No `numeric` comparison is LEAKPROOF, so a number filter reads the hot rows of its field as the app role. A sort on a number reads the index in order.
        - Fences: `test_studio_query.py`, `test_studio_query_r8.py`.
    - **B2a, the action door** (`routes/studio/invoke.py`, `ai_studio.md` §14 B2a). It registers on the router of `routes/studio/actions.py`. The org is the bound session and the member is `user.email`, never request input.
        - ⚠️ The order of the checks binds: the switch, then the shape of the action, then `packages_store.get_package` for the caller, then the declared `actions[].id`, then the class, then the executor. A package of another member or org, an App Workshop app and an id that names nothing get 404 `Not found`. A 404 before the package names nothing and writes no audit row.
        - ⚠️ The route takes the raw `Request`, so FastAPI parses no body before the switch. An org outside `STUDIO_ORGS` gets 404 for each body. Only the executor parses the body. Do not add a `Body` parameter.
        - ⚠️ An `action` that is not an A2 action id (`OP_PATTERN`, at most 48 characters) gets 404 before any read. Each text of an audit row has at most `MAX_AUDIT_TEXT` characters.
        - ⚠️ The class of an object action comes only from `studio_manifest.OBJECT_OP_CLASSES`. The class of each other source comes only from `actions.catalog_rows`. Class B and C get 403 `approval_required`, and nothing runs until B2c. B2c reuses the approval path of MCP-P1b.
        - The one executor is `object:<name>.get`. It reads the object by its name in the package, then the record, each with the package id of the path, never of the body. An object that the package lacks is 404 `object_missing`, and a record id that names nothing is 404 `Not found`. A catalog that fails is 503 `catalog_unavailable`, with an audit row. B2b records the executor of a door op in the spec before it builds one.
        - ⚠️ The catalog read and the audit each open their own session, after the door session closes. The audit goes through `routes/apps/_common.record_app_audit_bound`, never `record_app_audit`. `app_audit` has FORCE RLS, and the unbound session of `record_app_audit` writes no row as a role without BYPASSRLS. The row holds no record data.
        - ⚠️ The audit is best-effort on purpose, for a class A read only. A failed audit logs and never fails the read, because a read changes nothing. B2c must not copy this. B2c writes its audit row in the same transaction as the write, and a failed audit fails the write.
        - Fences: `test_studio_action_door.py`, `test_studio_action_door_r8.py`.

## Work Guidance

### Authentication posture (BO-2)

Authentication is **default-deny, applied app-wide**: `main.py` attaches
`require_authenticated(public=PUBLIC_ROUTES)` at `FastAPI(dependencies=[...])`,
so every route — including one added tomorrow — requires a valid internal
bearer token or a domain-verified `X-User-Email`. You do NOT add an auth
dependency for that; you add an *authorization* one.

A genuinely public endpoint (provider webhook, OAuth callback, liveness probe)
goes in `main.PUBLIC_ROUTES` **by route template**, and must authenticate
itself some other way. `tests/unit/test_default_deny_auth.py` fails if any
route escapes the guard, or if a PUBLIC_ROUTES entry matches no live route.

⚠️ **A gated route the BROWSER must navigate to gets a Next BFF route, never a
PUBLIC_ROUTES entry.** A top-level navigation carries no Bearer and no
`X-User-Email`. The session cookies are on the workbench origin, not `api.*`.
A navigation cannot add headers. So each such route answers 401 with
`{"detail":"Authentication required"}`.

The repair is a BFF route that has the session. It calls the gated route with
`gatewayHeaders()` and `redirect: "manual"`. Then it sends the upstream
`Location` as its own redirect
(`workbench/control_plane/src/app/api/email/oauth/[provider]/authorize/route.ts`).
Do not open the route instead. An authorize leg binds the resource to its
caller, so an identity from a query parameter could attach a mailbox to
another account. `tests/unit/test_email_oauth_authorize_wiring.py` checks that
the template is still absent from `PUBLIC_ROUTES`.

Swagger/ReDoc/openapi.json are dev-only (`docs_enabled`): FastAPI mounts them
without a dependency chain, so they cannot be guarded, and they publish the
whole API surface.

**Identity headers are internal-only, and the boundary is the reverse proxy.**
The gateway trusts `X-User-Email` and `X-User-Role` only with a matching
internal Bearer (`acb_auth/deps.py` branch 1a). Nothing in the gateway can tell
a forwarded header from a forged one, so the Bearer is the whole proof. The
Bearer falls back to the widely held `LITELLM_MASTER_KEY` when
`GATEWAY_INTERNAL_TOKEN` is unset. The public Caddy vhost deletes both headers
from each inbound request (`deploy/hostinger/caddy/Caddyfile`). A change to
those lines is an owner action, and `tests/unit/test_caddy_auth_gate.py` fences it.

Every on-box caller reaches the gateway over loopback (`GATEWAY_BASE_URL`,
`GATEWAY_URL`, `COPILOT_LLM_BASE_URL`, `WHATSAPP_BRIDGE_GATEWAY_URL`). That is
why the strip is safe. Do not point one of them at the public hostname.

### Adding a new endpoint
1. Create or extend a route file in routes/
2. Register the router in main.py
3. Use acb_auth.get_current_user for authentication; gate access with
   `require_permission("feature:<slug>")` (preferred for new routes) or the
   legacy `require_role(...)`. `get_current_user` already resolves the caller's
   org roles + overrides onto `UserContext.access`, so no second lookup is needed.
   A whole feature surface is gated at the router with
   `require_feature_router("<slug>", exempt=[...])`, so a new endpoint under
   that prefix is covered by default.
   ⚠️ **Adding a machine entrypoint** (provider webhook, OAuth callback, worker
   callback) under a gated prefix: add its route TEMPLATE to that router's
   `exempt` list, or the feature gate will 401 it and silently stop ingestion.
   `tests/unit/test_org_access_enforcement.py` fails on any route that takes no
   `UserContext` and is not exempt — do not silence it without checking which
   kind of route you added
4. Follow FastAPI patterns: Pydantic models, dependency injection
5. Audit all write operations via acb_audit.record()
6. A new `/projects` route needs an entry in `skill_projects/manifest.py` (item 12d).

### Mutation inbox flow
1. Pending commits listed via GET /agent/mutations/pending
2. Approve: POST /agent/mutations/pending/{id}/approve -> git push (GitHub) or keep (local)
3. Reject: POST /agent/mutations/pending/{id}/reject -> git reset HEAD~1
4. Local-only repos detected via git remote get-url origin check

### Agent registration
1. POST /agent with repo_url or local_path
2. Auto-fetches config.json to populate metadata
3. Persisted to agents.json at project root
4. agent_runtime auto-detected: repo_url -> github-copilot, local_path -> maf

## Verification

- Gateway health: GET /health returns {status: ok}
- Chat endpoint: POST /copilot/chat streams AG-UI events
- Agent stream: POST /agent/run/stream returns SSE stream with model
- Detached runs: disconnect mid-stream, cc:active:{tid} stays "1" and
  cc:stream:{tid} keeps growing; reconnect endpoint replays to RUN_FINISHED
  (E2E: uv run python scripts/_test_reconnect_e2e.py <agent> "<prompt>")
- Active sessions: GET /chat/active-sessions lists the running thread IDs
  of the caller's org that the caller can see. A run with no session row
  yet is listed only for the member who started it
  (tests/unit/test_active_sessions_tenant.py)
- Live activity: GET /observability/active lists agent runs in flight; GET
  /observability/activity/stream is an SSE feed of every agent/model activation
  (start a chat or trigger an app → events appear); backfill via
  /observability/activity/recent. GET /observability/roster = all agents +
  status; GET /observability/cost?days=N = daily $ rollup. EXECUTIVE/AGENT-gated.
  Cross-app check: trigger an email/tasks LLM call and confirm it appears with
  the right `source` and a non-null `cost_usd`.
- Workspace files: GET /agent/workspace/{id} lists files; only inputs/, outputs/, and agent-data/ are visible to the frontend user (agent source code is hidden)
- File download: GET /agent/workspace/{id}/file?path= serves raw bytes (50 MB cap). `&format=pdf` converts a Markdown or HTML file to a PDF attachment (item 0d)
- Upload: POST /agent/workspace/{id}/upload writes to `acb_skills.agent_paths.upload_dir_rel`. In a shared agent's tenant dir that is `inputs/<thread slug>/`, the session's own folder, and `read_attachment` reads only there. The route tells a tenant dir from its path and the caller's tenant (`_own_thread_slug`), never from the `.cc-instance` marker. It writes through `_safe_upload`. Fences: `tests/unit/test_read_attachment.py`, `tests/unit/test_h201_tenant_workdirs.py`
- Artifacts: GET /agent/artifacts?agent=&category= lists the files of the caller's OWN workspaces. The file and upload routes use the same root, the caller's state dir of a `personal` agent (`workspace._member_agent_workspace`). A shared or team agent answers 404. A session workspace under a `repos/` root is read-only: PUT, DELETE, upload and promote answer 403 (`_refuse_shared_clone_write`). Fences: `tests/unit/test_h201_artifact_routes.py`, `tests/unit/test_h201_email_artifact_sources.py`
- All endpoints require auth (Bearer token + optional X-User-Email/X-User-Role)
- Identity chain: Next.js → Bearer + user headers → deps.py resolves real UserContext
- Chat sessions scoped by user.email; fallback to "default" for anonymous/internal calls

## The user-management contract — binding on every route you add here

Full contract: `project-docs/specs/user_management_contract.md` (**eleven**
rules). The six that bite in *this* directory, each learned by
breaking it — **plus R11, which bites here hardest:**

> **R11 — never take the acting TENANT from input.** Not an `X-Organization-Id`
> header, not a query parameter, not a body field. It comes from the
> authenticated session or a tenant-scoped API key. Under **D15**
> (`project-docs/specs/saas_multitenancy.md` §1) every route in this
> directory runs against a tenant-bound session, and the binding happens **once**
> in `acb_common.db` — so a route that reaches data any other way is the bug.
> Build shapes: `saas_multitenancy_implementation.md` §2.
>
> ⚠️ **A background unit in this directory is NOT a route and must not behave
> like one.** It has no request to inherit from, so `tenant_session()` with no
> argument is the bug there — it would act for whoever happened to be in
> context. The rule (H4) is **resolve on an unbound session, then bind
> explicitly, and refuse if the resolution finds nothing**: two sessions in that
> order, the first deciding the tenant and writing nothing. Three built
> examples, three stored facts: `routes/crm/auto_lead` (the mailbox owner's
> org), `routes/workflows/service._pm_lifecycle_sweeper` (the workflow owner's
> org — `workflows` has no `organization_id` column until H3 phase 1) and
> `routes/projects/agent_dispatch` (the task's own org, carried **on the event
> payload**, because an event consumer has nowhere legitimate to look one up —
> the emitter stamps it inside its bound session). A consumer that refuses
> cannot record its refusal in tenant data either: it logs and returns.
> `tests/unit/test_db_engine_seam.py` is the fence — `H2_EXEMPT_FILES`,
> `H2_BASELINE_ELSEWHERE` and the exempt→converted call-edge table.

⚠️ **A loop that discovers orgs has ONE unbound read**, its `_organization_ids`.
Register it as a tenant-discovery site in `test_db_engine_seam.py`. Every other
statement runs in `tenant_session(org)`.

1. **The app is default-deny; do not opt out to make something reachable.**
   `require_authenticated` is attached at the app level in `main.py`, so a new
   route is gated without you doing anything. If it 401s, the answer is almost
   never `PUBLIC_ROUTES` — that hands the surface to the internet. It is a
   caller that cannot carry credentials, and the fix belongs in the BFF.
   Adding a browser-navigated authorize leg to `PUBLIC_ROUTES` would make the
   query parameter it reads an account-takeover primitive.
2. **The `/admin` auth floor is per-route, not a package property.**
   `routes/admin/_common.py` builds the router with **no** `dependencies=`;
   every route declares `Depends(require_admin_user)` in its own signature. A
   route that omits it inherits *nothing*.
3. **Owner-scoped reads: shared predicate, 404 never 403.**
   `routes/notes/core.py` — `OWNED_MEETING_PREDICATE` / `load_owned_meeting`.
   403 confirms a row exists; 404 does not. Bind the predicate *into* the
   mutating statement rather than loading first, so there is no TOCTOU window.
   Check ownership **before** the expensive read, or the error code becomes the
   oracle.
4. **Never take the acting identity from a query parameter or body.** The
   authenticated `user.email` outranks anything the caller supplies.
5. **Destructive routes owe four things.** They use the shared self-lockout
   guard (`_common.assert_not_self_lockout`, `assert_not_self_demotion`). They
   run in one transaction. The response names what was destroyed in each
   table. An audit row outlives the deletion. Watch `ON DELETE CASCADE`: a
   delete on one table silently empties tables on your "kept" list. A count
   that reports destroyed rows as kept is worse than no count.
6. **Verify, do not predict.** Read the row back before declaring success. A
   route that decides what *would* happen, writes, and never checks is a 200
   for somebody it never let in.

**Tests:** anything decided in SQL needs a *structural* assertion against the
statement string, because the shared fake (`tests/unit/_admin_fakes.py`)
re-implements clauses in Python and a mirror only ever agrees with itself.
Where two guards return the same status code, discriminate on the detail text
and on what was written — never on the bare code.

## Child DOX Index

None -- leaf directory.
