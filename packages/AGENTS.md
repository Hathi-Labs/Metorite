# Shared Packages

History of this file is in git (git log -p).

## Purpose
Reusable Python packages shared across all Metorite services.

## Packages

### acb_skills/
Agent loading, skill management and tool injection. The child doc is
`packages/acb_skills/AGENTS.md`.
- `integrations.build_integrations(..., is_authorized=)` filters the services that the ACTING MEMBER may not use. An agent declares a want, not an entitlement.
- `memory_tools` gates org-scoped memory writes on `memory:write_org` through a run-scoped predicate ContextVar.
- Both stay unfiltered when no member is attached, so background runs do not change.

### acb_llm/
LiteLLM integration, the unified credential store (LLM and integration keys) and model routing.
- `key_store._resolve_org(None)` and `model_config._resolve_org(cur, None)` answer the sole organization only while one organization exists. At org #2 an untenanted read returns nothing and an untenanted write raises.
- That fail-closed contract is deliberate (`work_plan.md` §3, D33 finding 3). It **must NOT be "fixed"** with a default-org fallback, which would serve the operator's keys to a customer.
- Pass `organization_id` at the CALL SITE, from the bound tenant (`acb_common.db.current_tenant`), never from request input. Fence: `tests/unit/test_credential_tenant_threading.py`.
- Only the tenant-scoped methods resolve an org (`get`, `put`, `delete`, `get_all`, `get_by_type`, `get_by_service`). `encrypt` and `decrypt` are plain Fernet over the single `ACB_MASTER_KEY`.
- `client.py` is `H4` and stays untenanted. Per-tenant live credentials need a tenant-scoped API key, which is an owner gate, not a refactor.
- `decide.py` is the ONE tenant facade for typed decisions. It calls `acb_auth.console_resolve.decide_on_console` and ships dark behind `DECIDE_ENABLED`, an OWNER-ONLY flag. It has no local litellm path. Fences: `tests/unit/test_acb_llm_decide.py`, `tests/unit/test_email_decide_shadow.py`.
- `jobs.py` is the ONE policy of an AI call's job (task 1128): `structured`, `writing` or `judgement`. The three seams read `job=`. Thinking reaches only a model that takes it: `params_for_model` decides it off the Router, and `customer_console/reasoning.py::reasoning_params_for` decides it for each chain step. Each seam adds the word "json" to JSON mode and logs `acb_llm.empty_answer`. A second thinking literal must import `THINKING_OFF`. Fence: `tests/unit/test_llm_jobs.py`.
- `context.acompletion_stream_text` ends a stream at `STREAM_FIRST_CHUNK_S` (45 s), `STREAM_IDLE_S` (30 s) and `STREAM_TOTAL_S` (150 s). Past a bound it raises a `StreamTimeout` subclass with a `reason` code. The caller decides what to do. Do not catch it into a draft. Fence: `tests/unit/test_llm_stream_bounds.py`.

### acb_memory/
Mem0 and Graphiti memory providers.
- `compartments.py` owns the scope vocabulary (`scope_key`, `scope_kind`) and `resolve_clearance`. It decides which compartments a run may read and the ONE it may write. Spec: `docs/multiplayer/memory-clearance.md` §3.
- An excluded compartment's scope key is never passed to `search()`. That is a boundary, not a request in a system prompt.
- It has no dependencies on purpose. The CALLER decides `shared`, so acb_memory stays below acb_auth and the gateway in the import graph.
- `session_cache` keys on the clearance fingerprint and on the thread.
- `blob_store.py`: `purge_files` and `session_paths` delete the files and the history of a deleted chat. The gateway's `workspace.purge_thread_files` is their one caller. The purge never deletes a `_RUN_ROW` row. Fence: `tests/unit/test_h227_thread_scope.py` (R8).

### acb_graph/
Postgres entity graph (SQLAlchemy sessions).
- ⚠️ `db.py` is a SEPARATE, SYNC engine. It is the single `_ALLOWED_SYNC` entry of `tests/unit/test_db_engine_seam.py`.
- `get_session()` binds NO `app.tenant_id`. It serves RLS-exempt reads and the best-effort `acb_audit` write.
- `tenant_session(organization_id)` is the sync twin of `acb_common.db.tenant_session`. It uses the same GUC, the same bound `set_config` and the shared `TenantUnbound`. It takes an explicit tenant only, with NO ambient ContextVar fallback (the H4 rule).
- `tenant_bind_enabled()` is the ONE reader of `ACB_GRAPH_TENANT_BIND` (default OFF). Do not add a second reader. Flipping it on a live box is OWNER-GATE.
- Fences: `tests/unit/test_acb_graph_tenant_seam.py` (R8), `tests/unit/test_acb_graph_chatsession_bind.py` (R8) and `tests/unit/test_db_engine_seam.py`.

### acb_common/
Shared settings, logging, the activity and cost feed, and utilities.
- **`db.py` is the ONE async engine and pool of a process** (`get_db()`, `get_session_factory()`, `get_engine()`). It imports SQLAlchemy inside the functions, and `acb_common/__init__.py` does not import it.
- Every gateway route package and `gateway/db.py` re-export it. The engine is never disposed. A second `create_async_engine` under `apps/` or `packages/` fails `tests/unit/test_db_engine_seam.py`.
- `_log.py::_RUN_CONTEXT_KEYS` and `activity.py::_INHERIT` hold the same keys: `(run_id, thread_id, agent, user, source, instance)`. Extend those two tuples together or attribution silently half-lands.
- That pair is drift-checked by `tests/unit/test_observability.py::test_inherit_and_run_context_keys_match`. Binds are additive and non-empty-only. Spec: `project-docs/specs/observability_e2.md` §7.
- `provisioning.py` is the ONE caller-side wrapper over migration 179's `provision_organization` (`provision_local_organization`). It acquires through `get_session_factory()`, NOT `tenant_session()` and NOT `get_db()`, because it creates the tenant.
- After its commit it calls `acb_auth.access.mirror_identity_membership` for the owner. The import stays inside the function. `persist_org_billing_profile` and `mark_console_mirrored` are best-effort and never raise.
- Provisioning fences: `tests/unit/test_org_provisioning.py`, `tests/unit/test_h6_rbac_rekey.py` and `tests/unit/test_signup_reconciler.py`.

- **`app_manifests/` holds the ONE route manifest of each app** (MCP-P1a, D136, `external_agent_access.md` §19.3). Mira's chat client and the MCP door both read it. It is pure data and imports the standard library only, so `acb_auth`, the edge and the gateway can import it.
- `base.py` holds the types and the readers. `build_call` is the one builder of a door call. `knowledge.py` holds the Knowledge rows, and `acb_skills/knowledge_manifest.py` re-exports them. Each A, B or C row names a door `op` or an `off_door` reason (AD-2), and an `App` with neither fails at import.
- Fences: `tests/unit/test_app_door_coverage.py`, `tests/unit/test_knowledge_chat_coverage.py`.

Each module below holds ONE rule. Do not copy its rule into a caller.
- `today.py`: today's date of a chat run. Fence: `tests/unit/test_crm_answer_consistency.py`.
- `priority.py`: the server-side priority matrix (D78). The gateway and the chat skills import it. Fences: `tests/unit/test_chat_priority_levels.py`, `tests/unit/test_priority_shared.py`.
- `_log.py` (`attributed_agent`, `automation_agent_scope`, `chat_agent_label`, `attributed_openai`): the agent name of a model call. Do not read `ctx["agent"]` for a report in a third place. Fence: `tests/unit/test_usage_attribution.py`.
- `pg_text.storable(value)`: it changes each NUL and each lone surrogate to U+FFFD, at any depth. A writer of model, tool or client text calls it before `json.dumps` and before the bind. Fence: `tests/unit/test_chat_nul_persist.py`.
- `number_check.py`: the ONE number check of a text that a model wrote (`find_figures`, `Sources`, `check`, `format_figure`). `settings.crm_answer_check_mode` is the one reader of `CRM_ANSWER_CHECK`. Fences: `tests/unit/test_number_check.py`, `tests/unit/test_number_check_invented.py`.
- `assistant_profile.py`: the ONE parser of `user_settings.assistant_profile`. It opens no connection. Fences: `tests/unit/test_assistant_profile.py`, `tests/unit/test_executor_profile_block.py`.
  - `card_writes_for(org)` is the one reader of `ASSISTANT_PROFILE_CARD_WRITES_ORGS` (PZ-32). `interview_stopped` is the one rule of a stop, and it reads `stopped_at` only. Migration 317 backfilled a stop from before PZ-32. Do not infer a stop from the areas again. Fences: `tests/unit/test_profile_pz32.py`, `tests/unit/test_interview_stop_backfill_r8.py` (R8).
  - `chat_summary_for(org)` is the one reader of `ASSISTANT_CHAT_SUMMARY_ORGS` (PZ-33). It needs `enabled_for(org)` too, and it reads the settings at each call. Fence: `tests/unit/test_chat_summary.py`.
- `env_guard.py`: the ONE rule for a write to `os.environ` and to the env file of the box (`check_env_write`, `EnvWriteRefused`). The deny list is defence in depth. The gate is the allowlist `BUILTIN_ENV_KEYS` in `gateway/routes/integrations.py`.
- A new `Settings` field or literal env read goes onto the deny list, or onto the fence's list of integration fields. Fence: `tests/unit/test_integrations_env_hardening.py`.
- `child_env.py`: the ONE env of every child process that the gateway starts, as an allowlist of literal names. Pass `env=` at every spawn and at every `CopilotClient(`. Never put `os.environ` in `extra=`.
- Do not hand a spawn function to other code as a value. The one exception is `orchestrator/mutation_runner.py`, which keeps its own literal allowlist. Spec: `project-docs/specs/box_hardening.md` §5 BH-1.
- Child env fences: `tests/unit/test_child_env_seam.py`, `tests/unit/test_copilot_child_env.py` and `tests/unit/test_child_env_call_sites.py`.

### acb_audit/
Audit event recording.
- `record()` logs always and writes the DB best-effort. On an event loop it hands the sync write to `asyncio.to_thread` and returns at once. Call `await drain()` when you need the row now. Fence: `tests/unit/test_audit_non_blocking.py`.
- `audit_event` is tenant-scoped behind `ACB_GRAPH_TENANT_BIND`. The org rides ON the `AuditEvent` object, and the caller stamps it on its own frame. Never read it from a ContextVar inside `_persist`.
- A tenant-less event falls back to the operator org (`slug = 'default'` or `OPERATOR_ORG_ID`), so the row is kept. That fallback is correct for audit and wrong for keys.
- Do not add `organization_id` to the ORM `AuditEvent` model, or SQLAlchemy sends a NULL that defeats the DEFAULT. Fence: `tests/unit/test_acb_graph_audit_bind.py` (R8).

### acb_auth/
Authentication, roles and org access control.
- Guards: `require_role(UserRole.EXECUTIVE)`, `require_permission("feature:whatsapp")` and the app-wide default-deny `require_authenticated(public=...)`.
- **`permissions.FEATURES` is the feature vocabulary, not `feature_catalog`.** A new `feature_catalog` row always needs a `FEATURES` edit too. Fence: `tests/unit/test_org_access_control.py`. Checklist: `project-docs/specs/department_centers.md` §2.
- Two DISTINCT secrets: `GATEWAY_INTERNAL_TOKEN` is service identity and `LITELLM_MASTER_KEY` is the `/v1` key that agents hold. With the internal token unset, `_resolve_internal_token()` falls back to the LLM key, and that ladder lives in one place.
- `GATEWAY_REFUSE_LLM_KEY_IDENTITY` defaults OFF, and flipping it is OWNER-GATE.
- `X-User-Email` and `X-User-Role` are internal-only headers. The reverse proxy strips them at the public vhost.
- `resolve_access(..., record_request=False)`: the default must stay False, and `deps._with_resolved_access` stays its only caller that passes True. Fence: `tests/unit/test_signin_requests.py`.
- `allowed_email_domain()` and `is_company_email()` give a label, not a boundary.
- `console_resolve.py` is a SECOND cache on a different axis (which tenant), and it must stay one. It has exactly two importers, `gateway/routes/signin.py` and `gateway/routes/signup.py`. Nothing under `packages/` or the gateway may import `customer_console`.
- It never compares against a lifecycle string, and it ships dark. Its `reconcile` sweep lives in the same module. Fences: `tests/unit/test_console_dependency_boundary.py`, `tests/unit/test_signup_reconciler.py`.
- Every `app_user` upsert targets `ON CONFLICT (lower(email))`, never `(email)`. The bare column raises 42P10 at plan time. Fence: `tests/unit/test_app_user_upserts.py` (R8).
- `email_otp.py` is the ONE email-OTP token store and the ONE place its rate limits are decided. It acquires through `get_session_factory()` unbound, because `auth_email_otp_token` is RLS-exempt.
- Both OTP limits are transactional, on the token row, under `pg_advisory_xact_lock`, never Redis: 5 verify tries per 10 minutes and 3 sends per hour.
- `reserve_send` claims the slot and writes the winner's hash in one statement. `record_token` must not claim, and it is first-writer-wins. The consume takes exactly ONE row. Log lines carry the domain only.
- OTP fences: `tests/unit/test_email_otp_token.py` (R8) and `tests/unit/test_h3_rls_promotion_rehearsal.py`.
- `access.mirror_identity_membership` is the ONE identity-shadow writer. Do not add a second `user_identity` writer. Fences: `tests/unit/test_h6_rbac_rekey.py`, `tests/unit/test_h6_identity_shadow.py`.
- `permissions.normalize_grant_subject` is the ONE grant subject grammar of D12. Fence: `tests/unit/test_grant_subject_grammar.py`.
- `agent_tokens.py` is the ONE store of the tokens of an outside agent, and it keeps only the SHA-256 of each. `agent_token` and `agent_client` are RLS-EXEMPT. A token act opens an unbound session, reads the row, and binds the tenant from that row.
- ⚠️ Lock order is always the connection row (`FOR NO KEY UPDATE`), then the tokens. A spend is one `UPDATE ... WHERE used_at IS NULL`. A reused refresh token revokes the connection. Fence: `tests/unit/test_agent_connection_tokens.py` (R8).
- D-EA-11: `create_connection(..., replace_same_client=True)` revokes the member's other connections of the client, and their tokens, in the transaction of the insert. Only the approve of a first-party client passes `True`. Fence: `tests/unit/test_agent_first_party.py` (R8).
- `agent_oauth.py` holds the OAuth rules that the edge and the gateway share. It does no I/O, and `enabled()`, `org_allowed()`, `writes_enabled` and `is_first_party()` are the one readers of their settings. Fences: `tests/unit/test_agent_oauth_server.py`, `tests/unit/test_mcp_edge_coverage.py`, `tests/unit/test_agent_client_stamp.py`.
- MCP-P1a: `agent_oauth.door_apps()` is the one reader of `agent_door_apps`. `door_route` gives the door pairs from `app_manifests`, never from a hand list (AD-3). `covered_by` is the one cover rule of a scope, and `door_available` is the one availability rule of a door op. The metadata documents name the scope of an app only while the door flag names the app.
- Branch 0 admits a door pair, and `deps._door_gate` checks the door flag, AD-18, the app scope and the caps. A door write of class B or C that is not a proposal gets 403 `approval_required` until MCP-P1b. Each door call writes one audit row (`_audit_door`). Fence: `tests/unit/test_mcp_edge_app_door.py` (`test_a_write_with_no_approval_fails_closed`).
- `agent_signing.py` is the ONE canonical string and the ONE HMAC of the signed request from the agent edge to the gateway. Branch 0 of `deps.get_current_user` never falls through to the bearer branches, and it reads no host input.
- 🔴 Before a change to branch 0, `agent_signing.py`, the `Visibility` narrowing in `core.py` or their fences merges, it needs an adversarial review AND an independent verification. `test_branch_zero_digest_is_pinned` fails until the reviewed commit updates the digest.
- Branch 0 fences: `tests/unit/test_mcp_edge_signing.py`, `tests/unit/test_mcp_edge_authority.py` (R8), `tests/unit/test_tenant_request_binding.py`, `tests/unit/_branch_zero_tripwire.py`, `tests/unit/test_mcp_edge_writes.py` (R8).

## Conventions
- Each package has its own pyproject.toml
- Public API exported through __init__.py
- Settings through acb_common.get_settings()
- Logging through acb_common.get_logger()

## Child DOX Index
- packages/acb_skills/AGENTS.md
