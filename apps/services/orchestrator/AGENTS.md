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
9. Run correlation is bound in **two** places in run_agent_stream, deliberately. `bind_run_context(run_id, thread_id, agent, user, source)` fires at the run boundary — BEFORE `load_agent`, so a failure during load is still correlated — and `_bind_run_instance(instance, run_id)` tops up `instance` (the tenant partition from `_resolve_agent_instance`) immediately after the load, because that value needs `loaded.config` and cannot exist earlier. Binds are additive; the single `clear_run_context()` in the `finally` unbinds every key. Do not "simplify" this into one bind: moving the first one late loses correlation on load failures, and dropping the second silently un-attributes every personal/team agent's spend. The same call patches the live presence key via `acb_common.refresh_run_presence` — the `phase="start"` event predates the load, so `/observability/active` and `/observability/roster` would otherwise never show a partition for any run. `_run_sub_agent_streaming` deliberately does **neither**: a delegated sub-run inherits the caller's `instance` (known asymmetry — spec §7 WS-6a) because `bind_context` cannot unbind and a correct fix needs save/restore around the sub-run. Spec: project-docs/specs/observability_e2.md §7 (WS-6a)
10. _tool_injection.py's static tool collection lives in `_collect_injectable_platform_tools()` (WS-23 S1 — the exact import chain `_inject_agent_tools` always ran, extracted verbatim; injection behavior unchanged). It is the read-only introspection seam the skills catalog (`acb_skills.skill_families` + gateway `GET /integrations/skills`) builds on, and `tests/unit/test_skills_registry.py` drift-fails if an injected tool is missing from the family registry — register any newly injected tool there in exactly one family. WS-23 S2: `_resolve_injected_scope(tool_scope, disabled_families=…)` intersects admin skill toggles (`agent_skill_setting`, loaded once per run by `_load_disabled_skill_families` — same best-effort sync-DB mechanism as app grants/MCP) with the declared scope; the core floor survives anything, the `workflows` toggle is honored at the trio's append site, and NO rows means byte-identical pre-S2 behavior (`tests/unit/test_skill_toggle_enforcement.py` pins all three rules).
11. **Tenant (`organization_id`) is threaded through the run boundary alongside the acting user** (WS-29 MT-1d / H4, `saas_multitenancy_handover.md`). `run_agent_stream` / `run_agent` and `stream_relay.run_detached` take an `organization_id` parameter; `run_agent_stream` stores it in the module-level run-keyed dict `_RUN_ORG` (the tenant twin of `_RUN_QUEUES`, cleared in the same `finally`) — this is what the SYNC `acb_graph` worker-thread writes read across the `loop.run_in_executor` hop, where contextvars do NOT propagate. It ALSO `acb_common.db.bind_tenant(org)`s the run's own event loop (and `run_detached` binds the detached drain task, covering `on_complete`). **The org is stamped SERVER-SIDE by the gateway chat route from `UserContext.organization_id`, NEVER from `event_payload`** — the payload is agent/client-visible, so sourcing the tenant from it is R11's tenant-spoofing hole. The batch `run_agent`/`_run_agent_inner` path and workflow/schedule/sub-agent org resolution are a later slice — see the `TODO(WS-29 slice 6)`. Fence: `tests/unit/test_executor_org_threading.py` (`executor-run-carries-org`).
   - **Slice 3 (dark, `ACB_GRAPH_TENANT_BIND`, default OFF = byte-identical):** the four `chat_session` touch points bind the tenant when the flag is ON — the two worker-thread writes `_store_session_id`/`_clear_stored_session_id` and the two reads `_get_stored_session_id`/`_session_workspace_override`. `_graph_session_opener(thread_id)` makes the choice **on the event-loop frame** (never in the worker thread, where `_RUN_ORG` and contextvars do not reach): flag OFF → the unbound `acb_graph.get_session`; flag ON + tenant → `acb_graph.tenant_session(org)`; flag ON + no tenant → `None`, and the caller **fails closed** (writes SKIP + log `executor.session_*_skipped_no_org`, never crash; reads fall back). The single flag reader is `acb_graph.tenant_bind_enabled()`. `_RUN_ORG`'s teardown is a **guarded pop** (`_guarded_pop_run_org`) so a superseded run's late `finally` cannot delete a newer same-thread run's org; and the missing-org warning is broadened to every source (`executor.run_missing_org`) plus `run_detached` (`stream_relay.detached_run_missing_org`), so the `/copilot/chat` and non-chat (email-automation) gaps are VISIBLE (org threading for those is slice 6). Fence: `tests/unit/test_acb_graph_chatsession_bind.py` (`chat-session-write-bound-under-rls`, `run-org-guarded-pop`, flag-OFF regression — R8 on the two-org phase-4 catalog).

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
