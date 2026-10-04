# Orchestrator -- Agent Execution Engine

## Purpose

The orchestrator is the runtime engine for all agent execution in Metorite.
It dynamically loads agents from GitHub repos or local folders, executes them
via MAF, handles cross-agent delegation, triggers self-mutation on failure,
and streams chat responses as AG-UI events.

## Ownership

- Owner: Metorite Core team
- Path: apps/orchestrator/

## Local Contracts

1. executor.py is the single entry point for agent execution (streaming and batch). Injects platform tools, MCP server config from the registry, and integration credentials at runtime. Integration credentials are filtered by the ACTING MEMBER first (`_integration_authorizer` → `build_integrations(is_authorized=)`, org access control): an agent's config.json declares a want, not an entitlement, and the filter runs BEFORE the per-run env injection so an unauthorized credential never enters the run's environment. Runs with no attributable active member (cron, reconciler, webhooks) are deliberately unfiltered — see project-docs/specs/org_access_control.md §8a. ⚠️ Every `/v1` client here (agents.py, _model_resolution.py, code_session.py, mutation.py, the executor's BYOK provider config) MUST read `settings.llm_api_key` — never `gateway_internal_token`, which is the service identity and must not reach model-authored code (§8b). A test in tests/unit/test_service_identity_and_webhook_auth.py fails if one does.
2. copilot_agent.py provides MetoriteCopilotAgent -- the MAF wrapper for Copilot SDK agents with BYOK + MCP server forwarding
3. agents.py exports build_orchestrator_agent() -- the main orchestrator MAF Agent
4. mutation.py handles Self_Mutation_Node -- spawns Docker sandbox on agent failure
5. stream_relay.py buffers all SSE events to Redis Streams for fire-and-forget chat with live reconnection
6. All agents must go through MAF -- no raw Copilot SDK paths for business execution
7. mutation_runner.py runs inside the Docker sandbox -- uses Copilot SDK directly (by design)
8. workflow_tools.py exposes published Workflows-app workflows to every agent as a three-tool trio (`list_workflows` / `run_workflow` / `get_workflow_run`) — the sibling of app_tools.py, injected through the same `_tool_injection` gated pipeline. Calls go in-process to `gateway.routes.workflows.service` (the same entrypoints the Run button and API use), so concurrency caps, run history, and the approval gates inside a workflow bind agent-triggered runs identically; a run paused at a Human-approval node reports itself as waiting in the approvals inbox — an agent cannot bypass the gate. Spec: project-docs/specs/workflows_app.md F13
9. Run correlation is bound in **two** places in run_agent_stream, deliberately. `bind_run_context(run_id, thread_id, agent, user, source)` fires at the run boundary — BEFORE `load_agent`, so a failure during load is still correlated — and `_bind_run_instance(instance, run_id)` tops up `instance` (the tenant partition from `_resolve_agent_instance`) immediately after the load, because that value needs `loaded.config` and cannot exist earlier. H-201 part 4 adds a third, late top-up: once `_resolve_run_workspace` gives the store key, a shared run binds its `o:<org>` key, on the stream path (only when it differs from the manifest key) and on the batch path. Binds are additive. The single `clear_run_context()` in the `finally` unbinds every key. Do not "simplify" this into one bind: moving the first one late loses correlation on load failures, and dropping the second silently un-attributes every personal/team agent's spend. The same call patches the live presence key through `acb_common.refresh_run_presence` — the `phase="start"` event predates the load, so `/observability/active` and `/observability/roster` would otherwise never show a partition for any run. `_run_sub_agent_streaming` deliberately does **neither**: a delegated sub-run inherits the caller's `instance` (known asymmetry — spec §7 WS-6a) because `bind_context` cannot unbind and a correct fix needs save/restore around the sub-run. Spec: project-docs/specs/observability_e2.md §7 (WS-6a)
9a. **The run's artifact context is a ContextVar, never a module dict** (H-201 part 4, `project-docs/specs/projects_ai_chat.md` §21.16). `acb_skills.write_artifact.artifact_context()` is the one reader. It holds an immutable mapping of `session_id`, `agent_name`, `run_id`, `workspace_root`, `instance`, `member`, `integrations`, `integration_warnings`, the gateway, and `permission_check_root` for a sandboxed session. `run_agent_stream` and `run_agent` bind it inside a token scope, so it ends with the run. `_run_sub_agent_streaming` derives its OWN copy and resets its token, so the parent's value never changes. A Copilot SDK agent's tool and permission callbacks start on the SDK's reader thread with no context, so every path calls `copilot_agent.carry_run_context(agent)` before the agent opens a session. With no context, every reader fails closed: no write, no emit, and no "single live run" guess in `resolve_run_queue` or `resolve_relay_thread_id`. A run that starts inside another run's context is a delegation, and `_delegated_instance` keys it by the parent's `member`. Fence: `tests/unit/test_h201_run_context.py`.
10. _tool_injection.py's static tool collection lives in `_collect_injectable_platform_tools()` (WS-23 S1 — the exact import chain `_inject_agent_tools` always ran, extracted verbatim; injection behavior unchanged). It is the read-only introspection seam the skills catalog (`acb_skills.skill_families` + gateway `GET /integrations/skills`) builds on, and `tests/unit/test_skills_registry.py` drift-fails if an injected tool is missing from the family registry — register any newly injected tool there in exactly one family. WS-23 S2: `_resolve_injected_scope(tool_scope, disabled_families=…)` intersects admin skill toggles (`agent_skill_setting`, loaded once per run by `_load_disabled_skill_families` — same best-effort sync-DB mechanism as app grants/MCP) with the declared scope; the core floor survives anything, the `workflows` toggle is honored at the trio's append site, and NO rows means byte-identical pre-S2 behavior (`tests/unit/test_skill_toggle_enforcement.py` pins all three rules).
11. **Tenant (`organization_id`) is threaded through the run boundary alongside the acting user** (WS-29 MT-1d / H4, `saas_multitenancy_handover.md`). `run_agent_stream` / `run_agent` and `stream_relay.run_detached` take an `organization_id` parameter; `run_agent_stream` stores it in the module-level run-keyed dict `_RUN_ORG` (the tenant twin of `_RUN_QUEUES`, cleared in the same `finally`) — this is what the SYNC `acb_graph` worker-thread writes read across the `loop.run_in_executor` hop, where contextvars do NOT propagate. It ALSO `acb_common.db.bind_tenant(org)`s the run's own event loop (and `run_detached` binds the detached drain task, covering `on_complete`). **The org is stamped SERVER-SIDE by the gateway chat route from `UserContext.organization_id`, NEVER from `event_payload`** — the payload is agent/client-visible, so sourcing the tenant from it is R11's tenant-spoofing hole. As of **slice 6a** the batch `run_agent`/`_run_agent_inner` path HONOURS its org param and the other run-based org SOURCES are threaded (see the slice-6a bullet); the remaining non-chat sources (email-automation, webhooks, workflow/schedule) are `TODO(WS-29 slice 6b/6c)`. Fence: `tests/unit/test_executor_org_threading.py` (`executor-run-carries-org`).
   - **Slice 3 (dark, `ACB_GRAPH_TENANT_BIND`, default OFF = byte-identical):** the four `chat_session` touch points bind the tenant when the flag is ON — the two worker-thread writes `_store_session_id`/`_clear_stored_session_id` and the two reads `_get_stored_session_id`/`_session_workspace_override`. `_graph_session_opener(thread_id)` makes the choice **on the event-loop frame** (never in the worker thread, where `_RUN_ORG` and contextvars do not reach): flag OFF → the unbound `acb_graph.get_session`; flag ON + tenant → `acb_graph.tenant_session(org)`; flag ON + no tenant → `None`, and the caller **fails closed** (writes SKIP + log `executor.session_*_skipped_no_org`, never crash; reads fall back). The single flag reader is `acb_graph.tenant_bind_enabled()`. `_RUN_ORG`'s teardown is a **guarded pop** (`_guarded_pop_run_org`) so a superseded run's late `finally` cannot delete a newer same-thread run's org; and the missing-org warning is broadened to every source (`executor.run_missing_org`) plus `run_detached` (`stream_relay.detached_run_missing_org`), so the `/copilot/chat` and non-chat (email-automation) gaps are VISIBLE (org threading for those is slice 6). Fence: `tests/unit/test_acb_graph_chatsession_bind.py` (`chat-session-write-bound-under-rls`, `run-org-guarded-pop`, flag-OFF regression — R8 on the two-org phase-4 catalog).
   - **Slice 4 (dark, same flag):** the `pending_commit` write/read follow the SAME opener discipline. `_detect_agent_commits(thread_id=…)` resolves `_graph_session_opener(thread_id)` **once, on its event-loop frame** (it runs before `run_agent_stream`'s finally pops `_RUN_ORG`) and reuses that one opener for BOTH the dedup `SELECT commit_sha FROM pending_commit` read AND every write — the write is `mutation._register_pending_commit(..., opener=…)`, which opens the passed opener instead of a hard-coded `get_session`. flag ON + tenant → the INSERT's phase-1 DEFAULT stamps the bound org (the statement names no `organization_id`); flag ON + no tenant → opener `None` → **fail closed** (read → empty dedup set; write → SKIP + log `mutation.pending_commit_skipped_no_org`, never unbound, never raise); flag OFF → the unbound `get_session`, byte-identical. `_register_pending_commit`'s `opener` defaults to a private `_OPENER_UNSET` sentinel so the still-unconverted self-mutation-sandbox caller (`attempt_self_mutation`) stays byte-identical (org threading for that batch path is slice 6). Fence: `tests/unit/test_acb_graph_pending_commit_bind.py` (`pending-commit-write-bound-under-rls`, flag-OFF regression — R8 on the two-org phase-4 catalog).
   - **Slice 6a (dark — org SOURCES, not new writes):** threads the tenant for the run-based sources that resolve it from in-hand SERVER-SIDE identity, so the slice 3-5 writes get the right tenant for them too. (1) `/copilot/chat` (`gateway/main.py`) stamps `user.organization_id` and passes it to `run_detached` — that chat runs the MAF orchestrator DIRECTLY (not `run_agent_stream`), so `run_detached`'s tenant bind is what its async reads, `on_complete`, and delegated sub-agents resolve; NEVER from `input_data`/the message list (R11). (2) Sub-agent inheritance: `_run_sub_agent_streaming` passes `_resolve_sub_agent_org()` into `run_agent` — a sub-run acts in its PARENT's tenant, resolved on the caller's frame from `_RUN_ORG` keyed by the parent thread id (`_stream_relay_thread_id` / bound run context), falling back to the async bound tenant (`current_tenant`, for the `/copilot/chat` parent that binds but sets no `_RUN_ORG`); the resolver takes NO argument, so it cannot source the tenant from the delegated message. (3) The batch path `_run_agent_inner` now HONOURS `organization_id`: sets `_RUN_ORG[thread_id]` + `bind_tenant` (guarded pop + release in the `finally`, mirroring the stream path); `None` (workflow/schedule until 6c, self-mutation sandbox) stays byte-identical and logs `executor.run_missing_org`. After 6a the broadened missing-org warning no longer fires for `/copilot/chat`, sub-agent runs, or org-supplied batch runs. `TODO(WS-29 slice 6b)`: email-automation + inbound webhooks (RLS-scoped mailbox/account→org resolver); `TODO(WS-29 slice 6c)`: workflow cron scheduler / schedule sweep. Fence: `tests/unit/test_org_sources_run.py` (`copilot-run-carries-org`, `sub-agent-inherits-parent-org`, `batch-run-honours-org-param` — AST/behavioral, no DB; each RED-on-removal shown by mutation).
   - **Slice 7 (dark, same flag — the agent-tool READS):** binds the reads an agent run makes through the `acb_graph` sync engine so they RETURN ROWS under FORCE RLS instead of silently reading 0 (an unbound read of a FORCE-RLS'd tenant table returns nothing post-cutover — the agent would lose its entity context, granted apps, and skill toggles). New seam `_graph_session_opener_current()` → `_current_run_org()` is the ambient-frame twin of `_graph_session_opener` for a converted read with NO `thread_id` in hand: it resolves the run's tenant SERVER-SIDE on the event-loop frame (`_RUN_ORG` keyed by `_stream_relay_thread_id`, else the async `current_tenant()`; NEVER tool args / the payload, R11) and hands it to the shared `_opener_for_org` — the ONE flag/opener construction now used by both the thread-keyed and ambient openers. `_resolve_sub_agent_org` (slice 6a) delegates to `_current_run_org` (one resolver, not two). The read site MUST resolve the opener BEFORE any `asyncio.to_thread` hop and close it over the worker-thread body. Sites converted: `agents.py` `retrieve_entity_context`/`retrieve_sales_context` (entity/sales retrieval tools), `app_tools.py` `_granted_live_apps` (apps⋈app_grants⋈app_versions), `_tool_injection.py` `_load_disabled_skill_families` (`agent_skill_setting`), and `agents/pull_agent.py`/`agents/sales_pull_agent.py` `answer()` (no live caller today). Each fails closed to empty when the flag is ON and no org resolves; flag OFF → the unbound `get_session`, byte-identical. `sales_views.py` reads a caller-supplied session → bound transitively, no change. **NOT RLS-bound (RLS-EXEMPT by design), but org-scoped at the APP level:** `_inject_mcp_servers` reads `mcp_servers`, which is RLS-EXEMPT (`gen_tenant_migration.EXEMPT`, "keyed (organization_id, name) by MT-0d/158") — not FORCE-RLS'd, so an unbound read does not collapse to 0 and a `tenant_session` bind would be a no-op. Instead, behind the SAME flag, the query gains `AND organization_id = :org` so an agent is only injected its OWN org's MCP servers (mcp_servers stays exempt — no migration, no policy). The org is resolved via the SAME `_current_run_org()` (server-side, never tool args, R11); `organization_id` is NOT NULL (158) so there is no global-server case — strict `= :org`, and flag-ON-no-org fails closed to EMPTY. flag OFF → no filter, byte-identical. Fence: `tests/unit/test_mcp_servers_org_scope.py` (`mcp-servers-scoped-to-run-org` — R8 on the two-org catalog: flag ON + orgA bound injects orgA's server not orgB's, RED-on-removal by dropping the filter; flag-OFF regression; flag-ON-no-org empty). Fence for the RLS-bound reads: `tests/unit/test_acb_graph_agenttool_reads_bind.py` (`agent-retrieval-reads-bound-return-rows` — R8 on the two-org phase-4 catalog: flag ON + orgA bound, the REAL `retrieve_entity_context` + `_granted_live_apps` RETURN orgA's rows, orgB sees nothing, and the SAME unbound `get_session()` read returns 0 rows → RED-on-removal; plus the opener matrix + flag-OFF regression).
12. **`Dockerfile.coding-sandbox` and `sandbox/requirements.txt` make the coding sandbox image** (WS-43b, `project-docs/specs/maf_coding_engine.md` §7.2). It holds no secret, no SDK and no CLI, and it runs as any non-root uid. The base carries a digest, Node.js carries one exact 22.x version and its SHA-256, and pip installs with `--require-hashes`. `tests/unit/test_coding_sandbox_image.py` (WS43-F10) fails when one of those pins goes. Its `sandbox_docker` half runs only in `.github/workflows/sandbox-docker.yml`, and that workflow fails on any skip. To build the image on the box is owner gate WS43-G2.

13. **A native run's context rides a per-run MAF context provider** (WS-43t1, `project-docs/specs/maf_coding_engine.md` §15.9). It sits behind `MAF_NATIVE_SESSIONS`, which is off by default.
   - `_compose_maf_run` returns the run input and a provider. Call `run` on `_agent_for_run(agent, provider)`, never on the agent with the input alone, or the run loses the member's memory.
   - With the flag on and with history, the input is a list of `Message(role=..., contents=[...])`. `RunContextProvider` (`_native_run_context.py`) adds the integrations, `memory_context` and `system_context` to the instructions, never to the messages. So a stored session never holds them.
   - `agent_for_run` returns a shallow copy of the agent with its own provider list. Never append a run's provider to the agent object itself, because one agent can serve two runs at once.
   - With the flag off, the input is the string of `_compose_maf_run_input`, byte for byte. Fence: `tests/unit/test_native_session_persistence.py` (WS43-F20).
   - `agent_with_providers(agent, providers)` is the one per-run copy. `agent_for_run` and the sandbox tools of projects-assistant (WS-43d) both use it.

14. **`sandbox_broker.py` is the one seam that runs `docker` for the coding sandbox** (WS-43c, `project-docs/specs/maf_coding_engine.md` §7.1, D83). A tool asks it for a container and an exec, and never calls `docker` itself. It keys one container on the organization, the agent and the thread. It reads all three from the run binding. So `acquire()` takes no argument. It mounts one read-write dir, the run's own state dir, and an empty read-only cover on a root `.git`. One function, `mount_list()`, builds the mounts for every start and every restart. It refuses a source that nests with another sandbox dir. The exec lock belongs to the mount source, so two containers on one dir share it. It removes a container by its id or its `metorite.start` label, and never by its name. A cancel at any step of `acquire()` frees the slot. A cancelled exec still runs the kill sweep before the dir lock goes free. It ships dark: `MAF_CODING_SCOPE` and `sandbox_image` are empty, so it starts no container. The startup sweep in the gateway lifespan always runs. It never falls back to the host. `copilot_sandbox.py` and `mutation.py` still run `docker`, on a legacy list that only shrinks. Fences: `tests/unit/test_sandbox_broker_seam.py` (WS43-F1), `test_sandbox_broker_argv.py` (WS43-F2), `test_sandbox_broker_tenant.py` (WS43-F3) and `test_sandbox_exec_hygiene.py` (WS43-F4). The Docker half of WS43-F4 carries the `sandbox_docker` marker, and `.github/workflows/sandbox-docker.yml` runs it on the coding image.
   - **The `projects` target (WS-43d, D86, spec §16.3).** `target_for_agent` maps projects-assistant to `projects`, and `MAF_CODING_SCOPE` takes `projects:<org>` or `projects:*`. `covers()` is true for it only with the scope, a healthy broker (Docker answered in the last 60 s, the free-space floor, a pinned image) and the D85 seam present. `lifts_shell_block()` is false for this target, so a true `covers()` never gives back `code_task`, `run_script` or `install_dependency`. For this target, `/workspace` is READ-ONLY and the env holds `PYTHONSAFEPATH=1` and `PYTHONNOUSERSITE=1`, so no thread runs code that another member's thread left. `mount_list()` adds three nested dir mounts for this target only. The thread's `outputs/<thread slug>/` goes at `/workspace/outputs`, read-write. The thread's `inputs/<thread slug>/` goes at `/workspace/inputs`, READ-ONLY (H-227), so a container sees only its own thread's uploads and no loose file. The run-data dir `state/.run-data/<org>/<thread>/` goes at `/workspace/.run`, read-write. `prepare_projects_dirs()` makes them with the safe opener before each start, the `.run` mountpoint too. `host_dir()` holds the dir lock with no start, for the file tools. `end_run()` deletes the run data, and the executor awaits `_end_sandbox_run()` in the `finally` of every run. The thread's container is then stale, and the next run starts a fresh one. The startup sweep deletes every run-data dir. Fences: `tests/unit/test_projects_sandbox_tools.py` (WS43-F21), `tests/unit/test_run_data_hygiene.py` (WS43-F22) and `tests/unit/test_h227_thread_scope.py` (H-227).

15. **A shared agent runs no shell on the host** (D85, `project-docs/specs/maf_coding_engine.md` §7.9). Two halves, two functions, two flags.
   - **The tool half.** `_tool_injection._withheld_shell_tools(agent_name, agent_config)` withholds `code_task`, `run_script` and `install_dependency`. The run binds `shell_tools_withheld`. `_sandbox_covers(agent, org)` may lift it. It asks `sandbox_broker.lifts_shell_block` (WS-43d), never `covers()` alone. That is `False` for the `projects` target, and `covers()` for every other target, so it is `False` for every agent until WS-43f.
   - **The host half.** `_tool_injection._host_shell_refused(agent_name, agent_config)` refuses the Copilot CLI's own shell. The run binds `host_shell_refused`. A cover NEVER lifts it, because the CLI runs on the host. Only `_copilot_cli_in_broker_sandbox` could, and it is `False` for every agent.
   - `_d85_leaves_alone` decides scope for both halves: a `personal` agent, and the root `metorite` agent (`_D85_OWNER_PENDING`), which only a first-party admin may run (contract 16). The org comes from `_current_run_org()`, never from input.
   - The withheld tool names leave the scope through `_resolve_injected_scope(withheld=)`. So the injected list, the addendum and the skill bodies agree.
   - Pass `agent_config=loaded.config` at every call of `_inject_agent_tools` and `materialize_skill_bodies_for_agent`. A call with no config reads as shared.
   - `read_attachment` (H-229) is NOT a `SHELL_TOOLS` member, so neither half touches it. It parses a chat attachment in-process and starts no process. It gives back the attached-document flow that D85 stopped. Fence: `tests/unit/test_read_attachment.py::test_d85_does_not_withhold_it`.
   - Each artifact context (the run, the batch run, each sub-agent) binds both flags. Give a Copilot agent its handler through `_copilot_session._install_copilot_permission_handler` and nowhere else. It ALWAYS wraps the slot in `permission_policy.guard_shared_agent_shell`, also a handler the agent's factory set, in every `AGENT_PERMISSION_MODE`. Production runs `enforce` (read on 2026-10-03). Tier 2 always passes `--deny-tool shell`, and every `MetoriteCopilotAgent` session sets `enable_file_hooks=False`. Fence: `tests/unit/test_shared_agent_shell_tools.py` (WS43-F23).

16. **Only an admin of the first-party organization may run the root `metorite` agent** (owner, 2026-10-03, `project-docs/specs/maf_coding_engine.md` §15.4).
   - `_assert_may_run_agent(agent_name)` is the one gate, and `_FIRST_PARTY_ADMIN_ONLY_AGENTS` names the agents. `run_agent_stream`, `_run_agent_inner` and `_run_sub_agent_streaming` each call it once, before they load the agent. Add no other load site without it.
   - The member is the verified `user` of the run binding, the parent's for a delegated run. The org is `_current_run_org()`. `mutation._read_first_party` is the one first-party read, and "admin" is `admin:members:manage`.
   - A refusal raises `AgentNotFound` (an `AgentLoadError`). The batch path catches it BEFORE the `AgentLoadError` clause, which starts a self-mutation. The sync run API maps it to 404. Fence: `tests/unit/test_root_agent_first_party.py`.

17. **A covered run, and every run under it, sends nothing off the platform** (H-236, `project-docs/specs/maf_coding_engine.md` §16.3). The owner kept delegation in a covered Projects run on 2026-10-03, and a called agent runs on the host. The control FAILS CLOSED.
   - `_tool_injection._run_no_egress(agent_name, parent_ctx)` is the one decision. It is set when the parent was `no_egress`, when the parent's agent is covered, or when this run's own agent is covered (`_run_covered`: `covers()`, or the scope alone, so a health probe cannot clear it). So a run of a covered agent is a covered run, whatever its parent. It never reads a request field or a tool argument.
   - `run_agent_stream`, `_run_agent_inner` and `_run_sub_agent_streaming` each compute it ONCE, before anything can fail, and derive it into the artifact context at once. A batch run passes the `organization_id` it was handed, because it decides the flag before it binds its tenant. `run_agent_stream` records a covered run by run id (`run_was_no_egress`, bounded, never removed on read), so the gateway's memory extraction (`routes/agent.py::_extract_run_memory`) skips it. Then they pass it to `_inject_agent_tools(no_egress=)` and bind it again. `_self_anneal` takes it as a required argument and passes it to both retries. `_inject_agent_tools` defaults to `False`, so every executor call passes it by name, and the WS43-F24 AST fence says so.
   - With `no_egress`, `_inject_agent_tools` drops every egress tool from the platform tools, the scope and the agent's own tools. The test is `acb_skills.egress.is_egress_tool`, plus the floor `sandbox_tools.HOST_NETWORK_TOOLS`. It pops `mcp_servers`. It replaces each native MAF agent in `agents` with a per-run view that carries `EgressGuardProvider` and no `mcp_tools`, so read `agents` again after injection. `run_agent_stream` injects no MCP server into such a run.
   - An agent shape the control cannot read (a MAF agent that is not an `Agent` and not Copilot-shaped) raises `NoEgressRefused`. `_run_agent_inner` answers it before the self-anneal and the self-mutation, so nothing retries a refused run.
   - Fence: `tests/unit/test_delegation_no_egress.py` (WS43-F24).

## Work Guidance

### Adding a new agent runtime feature
1. Feature goes in executor.py (streaming: run_agent_stream, batch: run_agent)
2. If it touches Copilot SDK agents, modify MetoriteCopilotAgent in copilot_agent.py
3. Ensure all Copilot SDK event types are translated to AG-UI SSE events
4. Test with both github-copilot and maf agent types
5. Run pytest tests/ before committing

### Modifying the mutation layer
1. mutation.py contains attempt_self_mutation() and prompt builders
2. Agent purpose context (instructions.md, skills, trigger) is assembled in _build_telemetry()
3. _stash_pull_before_mutation() syncs the clone (stash → fetch → rebase → pop stash)
   before the Docker sandbox runs, preventing stale-code fixes and merge conflicts
4. The Docker sandbox runs mutation_runner.py with MUTATION_PROMPT env var
5. Commits are registered as pending_commit rows for inbox approval
6. Local-only repos skip push on approval; use git reset HEAD~1 for rejection
7. _pull_latest() in acb_skills.loader preserves local-only commits (pending approval)
   via rebase instead of destructive reset --hard

### Session continuity and stale-session recovery
- Copilot SDK session IDs (service_session_id) are stored in-memory
  (_copilot_session_store) AND in Postgres (chat_session.service_session_id).
- On each run_agent_stream() call, _get_stored_session_id() looks up the ID;
  if found, _resume_session() is used so the SDK preserves full history.
- **Stale session after gateway restart**: When the Copilot CLI process dies,
  resume_session() raises "Failed to create GitHub Copilot session". The
  executor catches this via the _run_copilot_attempt() retry loop:
  1. Detects "session"+"error" in the exception message and a stored session ID.
  2. Calls _clear_stored_session_id() to NULL the Postgres record.
  3. Injects prior conversation (messages[], last 20, 300 chars each) as text prefix.
  4. Retries with session=None, creating a fresh Copilot SDK session.
  Max 1 retry (_session_retry_attempted flag); second failure surfaces as RUN_ERROR.
- _clear_stored_session_id() NULLs the Postgres row async via run_in_executor.
- Model switch mid-thread: detected via _copilot_model_store; forces new session.

### Streaming event flow
1. run_agent_stream() creates MetoriteCopilotAgent patches on loaded agent
2. agent.run(stream=True) returns AgentResponseUpdate objects via _run_copilot_attempt()
3. Each update is translated to AG-UI SSE events (TEXT_MESSAGE_CONTENT, TOOL_CALL_*, etc.)

### Prompt-cache sentinel convention (specs/llm_caching_memory.md)
When the executor appends memory context to an agent's instructions /
`system_message`, it inserts a `<!-- CACHE BREAK -->` sentinel
(`acb_llm.prompt_cache.CACHE_BREAK`) at the stable/dynamic boundary — stable
prefix (instructions + tool addendum) BEFORE the sentinel, dynamic memory
AFTER. The single `apply_prompt_caching` transform (called at both completion
choke points — `acb_llm.complete*` and gateway `/v1`) consumes it: Anthropic
tiers get an explicit `cache_control` breakpoint at the seam + the tool array
cached; every other provider has the sentinel stripped. **Keep the stable
prefix first and never put per-request/per-turn content before the sentinel** —
anything before it is treated as cacheable and byte-stability is required for a
cache hit.

### Injected tools (auto-available to all agents)
The executor's _inject_agent_tools() patches every loaded agent with cross-cutting
tools so no agent repo needs to declare them:
- call_agent / call_agents_parallel / call_agent_background  (agent_tools.py)
- web_search / fetch_page                                  (web_tools.py)
- write_artifact                                           (write_artifact.py)
- remember / recall_timeline / save_memory / save_episode   (memory_tools.py)
- manage_todo_list                                         (todo_tools.py)
- ask_questions                                            (ask_tools.py)
- get_errors                                               (error_tools.py)
- save_note / recall_notes                                 (note_tools.py)
- query_history                                            (history_tools.py)
- github_search / github_repo_search                       (github_tools.py)
Injection targets: _tools (GitHubCopilotAgent), tools (MAF Agent), _default_options.tools (legacy).
Tool guidance is appended to _default_options.system_message via _build_injected_tools_addendum().
User context (_set_memory_user_id) is set by gateway route agent.py before each run.
4. DETACHED EXECUTION: the gateway wraps the generator in stream_relay.run_detached(),
   which drains it in a background asyncio task pushing all events to Redis
   (cc:stream:{thread_id}). The HTTP response is just a Redis subscriber --
   client disconnects never kill the agent run.
5. Every _sse() frame is teed to Redis via per-thread ORDERED push chains
   (_tee_sse_line) so events land in exact emission order. Tier-1 MAF AG-UI
   frames (which bypass _sse) are teed explicitly in the Tier-1 loop.
6. RUN_FINISHED is emitted INSIDE the try block (before the finally tears down
   the relay) and the finally awaits pending pushes before mark_inactive --
   reconnecting clients always see the run end.
7. mark_active(reset=True) clears the previous run's stream so replay-from-0
   covers exactly the current run (prior turns live in Postgres).
8. Reconnect endpoint (GET /agent/run/{thread_id}/reconnect) replays from the
   cursor then subscribes live FROM THE REPLAY TAIL (no event gap).

### Reasoning / thinking stream (github-copilot runtime)
- Copilot SDK sessions emit ASSISTANT_REASONING_DELTA token-by-token when
  SessionConfig has streaming=True; copilot_agent.py translates them via
  Content.from_text_reasoning(text=...) -- the kwarg is REQUIRED (keyword-only
  API; positional calls raise TypeError silently swallowed by _on_event).
- The final ASSISTANT_REASONING full-block event is SKIPPED when its
  reasoning_id already streamed as deltas (prevents duplicated thinking text).
- think_mode "thinking"/"max" sets default_options["reasoning_effort"]
  (medium/high); _create_session forwards it to SessionConfig and retries
  without it if the model rejects it.
- Executor translates text_reasoning contents to THINKING_TEXT_MESSAGE_CONTENT
  SSE frames; tool-role text frames (progress, partial output) become
  TOOL_CALL_PARTIAL {toolCallId, delta} when the raw Copilot event
  (TOOL_EXECUTION_PARTIAL_RESULT / TOOL_EXECUTION_PROGRESS) carries a
  tool_call_id -- live terminal output streams into that tool's row in the
  UI -- otherwise PROGRESS_UPDATE. Never TEXT_MESSAGE_CONTENT (would pollute
  the visible answer). ASSISTANT_INTENT carries no text content; the raw
  INTENT handler renders it as a timeline entry.

### Todo-list tracking (VS Code Todos panel parity)
- The Copilot CLI tracks the agent's plan with its built-in `sql` tool
  against a `todos` table (INSERT INTO todos / UPDATE todos SET status).
- executor._TodoTracker parses those queries from TOOL_CALL args and emits
  TODO_LIST SSE frames ({todos: [{id,title,status}]}) on every change.
- Frontend: route.ts maps TODO_LIST -> {type:"todos"}; useAgentChat stores
  todos[] on the assistant ChatMessage; TodoPanel.tsx renders the
  collapsible "Todos (n/m)" panel pinned above the chat input.

## Verification

- pytest tests/ -- all 154 tests must pass
- Gateway must start: uv run uvicorn gateway.main:app
- Chat endpoint must stream: POST /agent/run/stream with model
- Tool calling must work: web_search, call_agent visible in stream

## Child DOX Index

None -- leaf directory. All orchestrator code is co-located.
