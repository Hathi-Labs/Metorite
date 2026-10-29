# Orchestrator -- Agent Execution Engine

History of this file is in git (git log -p).

## Purpose

The orchestrator is the runtime engine for all agent execution in Metorite.
It loads agents from GitHub repos or local folders and runs them through MAF.
It handles cross-agent delegation, starts self-mutation on failure, and streams
chat responses as AG-UI events.

## Ownership

- Owner: Metorite Core team
- Path: apps/services/orchestrator/

## Local Contracts

A bare test file name is under `tests/unit/`, and a bare spec name is under
`project-docs/specs/`. Keep the item numbers, because specs cite them.

1. executor.py is the single entry point for agent execution (streaming and batch). It injects platform tools, MCP server config and integration credentials at runtime.
   - Credentials are filtered by the ACTING MEMBER first (`_integration_authorizer`), BEFORE the per-run env injection. A run with no member (cron, reconciler, webhooks) is unfiltered on purpose (spec `org_access_control.md` §8a).
   - ⚠️ Every `/v1` client here MUST read `settings.llm_api_key`, never `gateway_internal_token`. Fence: `test_service_identity_and_webhook_auth.py`.
2. copilot_agent.py provides MetoriteCopilotAgent, the MAF wrapper for Copilot SDK agents with BYOK and MCP server forwarding.
   - ⚠️ It is the ONE builder of a spawning Copilot client. `child_env_client` builds every agent client with `env=copilot_env()`, and `install_child_env_guard` (installed by `orchestrator/__init__.py`) covers every path.
   - Do not build a `CopilotClient` for an agent anywhere else. Fence: `test_copilot_child_env.py`.
3. agents.py exports build_orchestrator_agent(), the main orchestrator MAF Agent.
4. mutation.py handles Self_Mutation_Node. It starts a Docker sandbox on agent failure.
5. stream_relay.py buffers all SSE events to Redis Streams for fire-and-forget chat with live reconnection.
6. All agents must go through MAF. No raw Copilot SDK path for business execution.
7. mutation_runner.py runs inside the Docker sandbox and uses the Copilot SDK directly (by design).
8. workflow_tools.py gives published workflows to every agent as a trio: `list_workflows`, `run_workflow` and `get_workflow_run` (spec `workflows_app.md` F13). The gated `_tool_injection` pipeline injects it.
   - Calls go in-process to `gateway.routes.workflows.service`, so the approval gates bind an agent run too.
   - The trio binds the run's org from `executor._current_run_org()`. No tool takes an org argument. With no org, a tool returns `NO_ORG` and reads nothing.
   - `run_workflow` refuses a workflow with a wait that can pause. Fence: `test_workflows_tool_rls_r8.py`.
9. Run correlation binds in **two** places in run_agent_stream, on purpose (spec `observability_e2.md` §7).
   - `bind_run_context(...)` runs at the run boundary, BEFORE `load_agent`, so a load failure is still correlated. `_bind_run_instance(instance, run_id)` tops up `instance` after the load.
   - Do not "simplify" this into one bind. The single `clear_run_context()` in the `finally` unbinds every key.
   - `_run_sub_agent_streaming` does neither, so a delegated sub-run inherits the caller's `instance` (known asymmetry, WS-6a).
9a. **The run's artifact context is a ContextVar, never a module dict** (spec `projects_ai_chat.md` §21.16).
   - `acb_skills.write_artifact.artifact_context()` is the one reader. `_run_sub_agent_streaming` derives its OWN copy, so the parent's value never changes.
   - Every Copilot path calls `copilot_agent.carry_run_context(agent)` before the agent opens a session. With no context, every reader fails closed. Fence: `test_h201_run_context.py`.
10. `_tool_injection._collect_injectable_platform_tools()` holds the static tool collection. It is the read-only seam that the skills catalog builds on.
   - Register a newly injected tool in exactly one family, or `test_skills_registry.py` fails. A tool pack is never injected.
   - `_resolve_injected_scope(tool_scope, disabled_families=...)` intersects admin skill toggles with the declared scope. The core floor survives anything. Fence: `test_skill_toggle_enforcement.py`.
11. **The tenant (`organization_id`) is threaded through the run boundary with the acting user.**
   - `run_agent_stream`, `run_agent` and `stream_relay.run_detached` take `organization_id`. `_RUN_ORG` carries it across the `run_in_executor` hop, where contextvars do NOT propagate. Its teardown is the guarded pop `_guarded_pop_run_org`.
   - **The gateway stamps the org SERVER-SIDE from `UserContext.organization_id`, NEVER from `event_payload`** (R11). A sub-run acts in its parent's tenant (`_resolve_sub_agent_org`).
   - An `acb_graph` read or write resolves its opener on the event-loop frame (`_graph_session_opener`, `_graph_session_opener_current`), before any `asyncio.to_thread` hop. With `ACB_GRAPH_TENANT_BIND` on and no tenant, it fails closed.
   - `_inject_mcp_servers` reads through `gateway.connections.mcp.servers_for_org`. Fences: `test_executor_org_threading.py`, `test_org_sources_run.py`, `test_acb_graph_chatsession_bind.py`, `test_acb_graph_agenttool_reads_bind.py`, `test_mcp_registry_r8.py`.
12. **`Dockerfile.coding-sandbox` and `sandbox/requirements.txt` make the coding sandbox image** (spec `maf_coding_engine.md` §7.2). It holds no secret, no SDK and no CLI, and it runs as any non-root uid.
   - Every pin stays. Fence: `test_coding_sandbox_image.py`. Building the image on the box is owner gate WS43-G2.
   - To add a Python package, open a reviewed PR that edits `sandbox/requirements.in`, the lock, `_SANDBOX_MODULES` and the table of §7.2. Fence: `test_coding_sandbox_packages.py`.
   - `sandbox/data_engine.py` is the only module that imports `duckdb`. Never set `allowed_directories` there. Fence: `test_data_engine.py`.
13. **A native run's context rides a per-run MAF context provider**, behind `MAF_NATIVE_SESSIONS` (default off, spec `maf_coding_engine.md` §15.9).
   - Call `run` on `_agent_for_run(agent, provider)`, never on the agent with the input alone. Never append a run's provider to the agent object itself. `agent_with_providers` is the one per-run copy.
   - `native_session_store.py` is the one module that reads or writes `maf_agent_session`, through `tenant_session(organization_id)`. A session loads and saves ONLY in the actor's own thread.
   - A failed load falls back to the text history and never fails the run. Fence: `test_native_session_persistence.py`.
14. **`sandbox_broker.py` is the one seam that runs `docker` for the coding sandbox** (D83, spec `maf_coding_engine.md` §7.1). A tool never calls `docker` itself.
   - It keys one container on the org, the agent and the thread, from the run binding, so `acquire()` takes no argument. It never falls back to the host.
   - `copilot_sandbox.py` and `mutation.py` still run `docker`, on a legacy list that only shrinks.
   - Fences: `test_sandbox_broker_seam.py`, `test_sandbox_broker_argv.py`, `test_sandbox_broker_tenant.py`, `test_sandbox_exec_hygiene.py`, `test_projects_sandbox_tools.py`, `test_run_data_hygiene.py`.
15. **A shared agent runs no shell on the host** (D85, spec `maf_coding_engine.md` §7.9).
   - `_withheld_shell_tools` withholds `code_task`, `run_script` and `install_dependency`. `_host_shell_refused` refuses the Copilot CLI's own shell, and a cover NEVER lifts it. `_d85_leaves_alone` decides the scope of both.
   - Pass `agent_config=loaded.config` at every call of `_inject_agent_tools` and `materialize_skill_bodies_for_agent`. A call with no config reads as shared.
   - Give a Copilot agent its handler through `_copilot_session._install_copilot_permission_handler` and nowhere else. Fence: `test_shared_agent_shell_tools.py`.
16. **Only an admin of the first-party organization may run the root `metorite` agent** (spec `maf_coding_engine.md` §15.4).
   - `_assert_may_run_agent(agent_name)` is the one gate. Each load site calls it once, before the load. Add no other load site without it.
   - A refusal raises `AgentNotFound`, and the batch path catches it BEFORE the self-mutation clause. Fence: `test_root_agent_first_party.py`.
17. **A covered run, and every run under it, sends nothing off the platform** (H-236, spec `maf_coding_engine.md` §16.3). It FAILS CLOSED.
   - `_tool_injection._run_no_egress(agent_name, parent_ctx)` is the one decision. It never reads a request field or a tool argument. Each run start computes it ONCE and passes it by name to `_inject_agent_tools(no_egress=)`.
   - A run that an `UNTRUSTED_INPUT_AGENTS` agent delegates to binds `no_egress`, and its answer marks the chat session.
   - The control trusts a platform tool by IDENTITY. Each site that builds one registers it with `acb_skills.egress._register_platform_callable`. An unreadable agent shape raises `NoEgressRefused`.
   - Fences: `test_delegation_no_egress.py`, `evals/email_injection/`.
18. **A covered agent's run takes its tier from the policy** (D90, spec `ai_tier_routing.md` §4.5). The policy lives in `acb_skills.tier_policy`, and the executor holds only the glue.
   - `_tier_policy_for_run` is the one entry. Fence: `test_tier_policy.py`.
   - The parallel start (2026-10-30, §4.3) is dark behind `AI_TURN_KIND_PARALLEL`. Only the native Tier 1 stream of `run_agent_stream` uses it. The batch path, a sub-agent, a delegated run and a Copilot SDK agent still await the answer.
   - The stream loop takes the answer only while a pull is in flight, so a cancel closes the request inside its stream. Each output of the loop calls `seal()` first. `_apply_parallel_tier` mirrors the tier site, so change both together. Fence: `test_turn_kind_parallel.py`.
19. **A Mirror run gets no CRM record write** (spec `crm_platform.md` §20.10 item 4).
   - `_drop_mirror_record_writes` runs right after `_apply_own_tool_scope`, at all three sites. In Mirror mode it drops `MIRROR_RECORD_WRITES`, and `log_activity` stays. Fence: `test_crm_mirror_run_tools.py`.
20. **A self-anneal retry holds no tool that the first try did not hold.** Access is an intersection, never a union.
   - `_assemble_batch_agents` is the one batch assembly. `_narrow_to_held` removes each tool outside the held set of `_BatchRunInputs`.
   - Do not build or inject agents for a retry anywhere else. Fence: `test_anneal_retry_scope.py`.
20b. **Each turn carries today's date.** `_context_preamble_parts` adds `acb_common.today.today_line()` LAST. Fence: `test_crm_answer_consistency.py`.
21. **The orchestrator's per-turn context carries the Knowledge index block** (spec `knowledge_base.md` §6.5).
   - Only the agent `orchestrator` gets it, behind `COMPANY_SEARCH_ENABLED`, `KNOWLEDGE_ENABLED` and `KNOWLEDGE_ORGS`. The org comes from `_current_run_org()` and the member from `_verified_run_member()`, never from the payload.
   - Fences: `test_knowledge_index_block.py`, `test_company_search_recency.py`.
22. **A run that holds a CRM tool gets the number check** (spec `crm_platform.md` §18.11.2).
   - `_tool_injection._apply_answer_check` runs at the end of `_inject_agent_tools`. Read `agents` again after injection. Do not attach the provider anywhere else.
   - Each new relay of a run queue must drop the hold beat (`_relayable`). Fences: `test_crm_answer_check.py`, `test_crm_answer_check_executor.py`.
23. **A streamed run opens ONE holder of the CRM questions that it asks.** `run_agent_stream` calls `open_asks_for_run()` and `release_asks` in the same `finally`. Never give the ContextVar a mutable default. Fences: `test_crm_questions_chat.py`, `test_crm_agent.py`.
24. **The orchestrator's per-turn context carries the member's own profile block** (spec `personalization.md` §5.1).
   - It needs an org, `assistant_profile.enabled_for(org)`, a verified member and `_RUN_SOLO_ROOM` True. `enabled_for(org)` is the one reader.
   - A delegated run cannot widen `_RUN_SOLO_ROOM`. Never read it from the payload. Fences: `test_executor_profile_block.py`, `test_executor_profile_block_r8.py` (R8), `test_assistant_profile_org_rollout.py`.
25. **The orchestrator's per-turn context carries the member's corrections and limits of the chat** (spec `one_assistant_context.md` §4).
   - `_prime_chat_constraints` runs only for the agent `orchestrator`, with `CHAT_CONSTRAINTS` on, a solo room and a verified member. A delegated run inherits the list and never opens the tools.
   - Fences: `test_chat_constraints.py`, `evals/trajectories/test_chat_constraints_trajectory.py`.
26. **A pack tool acts as its owner acts in a sub-run of this chat** (spec `one_assistant_context.md` §4.2.3). `_tool_injection._pack_tools(slug, run_ctx)` is the one builder. Since OA-7c its live caller is `_apply_tool_packs` (contract 27).
   - STRICT within a turn (the owner confirmed it on 2026-10-27): the first email call marks the chat. Each later call of the turn runs held. An egress tool then does not run, and it answers `egress.EGRESS_HELD_PACK_ANSWER`.
   - The run check of each call keys on `tool_packs.PACK_RUN_KEY`, which the server mints for each run. It never keys on `run_id`, which a client can set.
   - `_pack_held` fails closed. ⚠️ A held pack drops each tool that `_is_egress` names, and that drop is the only held-set control of a pack run.
   - Do not copy the bodies of `_is_egress`, `_run_no_egress` or `_gate_injected_tool` into it. `_pack_owner_module` is the one loader of a pack owner. Fence: `test_pack_guards.py`.
27. **The packs of a turn load only in the member's own web chat with the orchestrator** (WS-67 OA-7c, `one_assistant_context.md` §4.2.2). It ships dark behind `TOOL_PACKS`.
   - `_start_pack_turn` is the one caller of `tool_packs.packs_on`. It starts the read of the sticky packs as the first statement of the main try. The `finally` of that try cancels a read that the run did not await.
   - No read starts for a shared room, a batch run (WhatsApp, D115) or a delegated run. No read starts with no card channel or no verified member. With the flag off, no pack state is read or written.
   - `_apply_tool_packs` runs after `_apply_agent_md_overrides` and BEFORE `_inject_agent_tools`. It drops the specialist tool of each owner that a pack replaces. It swaps the owner's persona text for the rules, and adds the pack tools after the core tools. Each run builds its packs again. A run with no bound tenant loads no pack.
   - The apply runs before `_apply_own_tool_scope`, so an `own_tool_scope` on the orchestrator would strip the pack tools. `test_the_orchestrator_has_no_own_tool_scope` fails when a change adds one.
   - `PACK_RUN_KEY` and `PACKS_KEY` go into the run binding after `bind_artifact_context`. `_run_sub_agent_streaming` drops both from a child's context. A spawn child (the worker) gets the key of its own pack tools instead (OA-9c, `_job_pack_keys`), and the batch run binds it again. Fence: `test_spawn_subagents.py`.
   - `tool_packs.delegation_refusal` is the ONE refusal rule. `_run_sub_agent_streaming`, `agent_tools.call_agent` and `call_agents_parallel` ask it. Do not add a refusal with a rule of its own. The "Delegatable agents" block leaves out an agent only while the run refuses the hand-off to it (`PACK_REFUSED_ATTR`).
   - `_pack_base_count` reads the same scope and withheld names as `_inject_agent_tools`. Change both together. Pack names stay out of `run_tools`.
   - `_record_pack_call` holds a lock of the chat around the email working set. Its event goes through `_emit_to_run_or_relay`.
   - Fences: `test_tool_packs_choice.py`, `test_tool_packs_off.py`.

27. **The packs of a turn load only in the member's own web chat with the orchestrator** (WS-67 OA-7c, `project-docs/specs/one_assistant_context.md` §4.2.2). It ships dark behind `TOOL_PACKS`.
   - `_start_pack_turn` is the one caller of `tool_packs.packs_on`. It starts the read of the sticky packs as the first statement of the main try, beside the load. So the finally of that try cancels a read that the run did not await.
   - These runs start no read: a shared room, a batch run (the WhatsApp path, D115) and a delegated run. A run with no card channel or no verified member starts none too. With the flag off it reads and writes no pack state.
   - `_tool_injection._apply_tool_packs` applies the choice after `_apply_agent_md_overrides` and BEFORE `_inject_agent_tools`. It drops the specialist tool of each owner that a pack replaces. It swaps the owner's persona text for the rules, and it adds the pack tools after the core tools. Each run builds its packs again. A run with no bound tenant loads no pack.
   - The apply runs before `_apply_own_tool_scope`, so an `own_tool_scope` on the orchestrator would strip the pack tools. The pin `test_the_orchestrator_has_no_own_tool_scope` fails when a change adds one.
   - The run key (`tool_packs.PACK_RUN_KEY`) and the record (`tool_packs.PACKS_KEY`) go into the run binding after `bind_artifact_context`, which replaces the context. `_run_sub_agent_streaming` drops both from the context of a child. OA-9c: a spawn child gets the key that the spawn minted for its pack tools (`_job_pack_keys`), never the parent's key or the record.
   - `_run_sub_agent_streaming` refuses a hand-off to an owner when the calling run holds every pack of that owner (`tool_packs.delegation_refusal`). With some packs only, the hand-off stays open for the rest. `agent_tools.call_agent` and `call_agents_parallel` ask the same rule first. Do not add a refusal with a rule of its own.
   - The "Delegatable agents" block leaves out an agent only while the run refuses the hand-off to it (`PACK_REFUSED_ATTR`).
   - `_pack_base_count` reads the same scope and withheld names as `_inject_agent_tools`. With no app grant the two counts agree, and a fence says so. Change both together.
   - Pack names stay out of `run_tools`. OA-9c reads the record of the packs.
   - `_record_pack_call` holds a lock of the chat around the email working set. Its event goes to the relay of a run with no queue (`_emit_to_run_or_relay`).
   - Fences: `tests/unit/test_tool_packs_choice.py` and `tests/unit/test_tool_packs_off.py`.

28. **`load_skill_tools` loads a pack in the middle of a turn** (WS-67 OA-7e, layer (b), `project-docs/specs/one_assistant_context.md` §4.2.2). It ships dark behind `TOOL_PACKS`.
   - OA-7i: `_plan_pack_loader` asks `tool_packs.plan_loader` once for each run. `_offer_pack_loader` and the removal of a specialist read that one plan. `_mcp_may_attach` looks ahead at the registry and fails closed. Do not compute reachability anywhere else.
   - `_offer_pack_loader` puts `tool_packs.LoadSkillToolsProvider` on a view of the run (`agent_with_providers`). Only `_apply_tool_packs` with `offer_loader` calls it, and only `executor._apply_pack_turn` passes that. Do not offer the loader on another path.
   - The load builds through `_pack_tools` (`_build_pack_for_load`), with the run context of the call and the names of the live tool list. Do not build a pack a second way.
   - A turn that loads no pack still returns a `PackApply` with no build. The executor then binds the run key and the record that a load needs.
   - The load tool rides in each request of the run, so the cap of layer (a) counts it (`base += 1`).
   - A load never takes a tool or a persona text away. `PackRun.replaced` and the "Delegatable agents" block keep the state of the run start.
   - MCP rule: a load of a pack of the email assistant refuses when the live list holds an MCP tool. The catalog line says so. Precedence differs by layer. In layer (a) the email pack wins and `_inject_mcp_servers` attaches none. In layer (b) the MCP tool wins and the pack stays out.
   - A load makes no pack sticky. A pack becomes sticky when one of its tools runs (`_pack_call`). The catalog offers only a pack that fits the cap.
   - Fence: `tests/unit/test_load_skill_tools.py`.

## Work Guidance

### Adding a new agent runtime feature
1. Feature goes in executor.py (streaming: run_agent_stream, batch: run_agent)
2. If it touches Copilot SDK agents, modify MetoriteCopilotAgent in copilot_agent.py
3. Translate all Copilot SDK event types to AG-UI SSE events

### Modifying the mutation layer
1. mutation.py contains attempt_self_mutation() and prompt builders
2. _build_telemetry() assembles the agent purpose context (instructions.md, skills, trigger)
3. _stash_pull_before_mutation() syncs the clone before the Docker sandbox runs
4. The Docker sandbox runs mutation_runner.py with the MUTATION_PROMPT env var
5. Commits become pending_commit rows for inbox approval
6. _pull_latest() in acb_skills.loader keeps local-only commits (pending approval) through a rebase, never reset --hard

### Session continuity
- Copilot SDK session IDs live in memory (_copilot_session_store) AND in Postgres (chat_session.service_session_id).
- After a gateway restart, _run_copilot_attempt() clears a stale stored ID. Then it tries once more with a fresh session and the prior messages as a text prefix.

### Prompt-cache sentinel convention (specs/llm_caching_memory.md)
The executor puts `acb_llm.prompt_cache.CACHE_BREAK` between the stable prefix and
the dynamic memory. **Never put per-request or per-turn content before the
sentinel.** A cache hit needs byte-stability before it.

### Injected tools
- `_inject_agent_tools()` gives every loaded agent the cross-cutting tools of `acb_skills`, so no agent repo declares them.
- `_apply_voice()` puts `acb_llm.voice.voice_prompt(AGENT)` right after the agent's own instructions, on every runtime shape. floor_opt_out cannot remove it. Fence: `test_agent_voice.py`.
- DETACHED EXECUTION: the gateway wraps the generator in `stream_relay.run_detached()`, so a client disconnect never kills the run.
- Every `_sse()` frame goes to Redis through ordered per-thread push chains (`_tee_sse_line`). RUN_FINISHED is sent INSIDE the try block, before the relay teardown.

### Reasoning stream (github-copilot runtime)
- `Content.from_text_reasoning(text=...)` needs the keyword. A positional call raises TypeError, which `_on_event` swallows.
- Send reasoning and tool text on their own frames, never as TEXT_MESSAGE_CONTENT.

## Verification

- `uv run pytest` on the fences of the items you touched
- Gateway must start: uv run uvicorn gateway.main:app
- Chat endpoint must stream: POST /agent/run/stream with model

## Child DOX Index

None -- leaf directory. All orchestrator code is co-located.
