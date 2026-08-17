# Chat & Agent Framework Review — 2026-07

> ⚠️ **HISTORICAL RECORD (2026-08-10 consolidation, D26).** No work dispatches from
> this document. The active plan is `project-docs/work_plan.md` §2;
> the classification of record is `project-docs/INDEX.md`.


**Status:** review complete · **Date:** 2026-07-22 · **Requested by:** Vijay
**Scope:** multi-agent architecture, orchestration & handoff, context management, memory,
artifacts/files, HITL/AG-UI, document co-authoring — and the standing question of whether to
keep both the native-MAF and GitHub-Copilot-SDK runtimes.

**Method:** full code review of the executor/dispatch layer (`apps/services/orchestrator/`),
gateway + chat/HITL (`apps/services/gateway/`), memory (`packages/acb_memory`, `acb_llm`,
`acb_graph`), the four in-repo agents + registries, and the Control Plane
(`workbench/control_plane/`), cross-checked against the verified findings in
`multi_agent_orchestration.md`, `agent_file_and_memory_framework.md`, `llm_caching_memory.md`,
and `agents-workspaces-artifacts.md`.

---

## 1. Executive summary

1. **Keep both runtimes — but change what "both" means.** The real split is not
   "Microsoft agents vs Copilot agents." Both runtimes are MAF `BaseAgent`s
   (`SupportsAgentRun`) and run through the same executor, tool injection, memory, HITL, and
   streaming. The actual difference is the *engine*: a thin LLM loop over the gateway `/v1`
   (native MAF) vs a Copilot-CLI subprocess with a built-in coding harness (shell, file edit,
   MCP, todo, compaction). Treat MAF as **the framework** and the Copilot SDK as **one engine,
   reserved for coding-class agents** (self-mutation sandbox, `metorite`-dev coworker
   sessions). Specialist business agents should be native MAF.

2. **Two agents are on the wrong engine today.** `task-manager` (15 `gtd_*` API tools) and
   `apis-config` (web search + guidance) are pure tool-API agents that instantiate
   `GitHubCopilotAgent` — they pay the full Tier-1.5 complexity tax (session resume shims,
   window-guessing, addendum injection, JSON-RPC thread hops) for a coding harness they never
   use. Migrating these two to native MAF removes most of the dual-runtime surface from the
   interactive path. `email-assistant` and `orchestrator` prove the native path is mature.

3. **Do NOT try to replace the Copilot SDK for actual software development.** A native MAF
   agent has no coding harness — no shell tool, no file-editing loop, no repo-aware
   permissioning. Rebuilding that in MAF means rebuilding the Copilot CLI. For "basic
   software development, coding, and script generation" the Copilot engine is the right tool;
   the mistake is using it for agents that don't code.

4. **The Phase 0 delegation fix from `multi_agent_orchestration.md` (2026-07-17) has still
   not landed.** `call_agent` is absent from `_CORE_STANDARD_TOOL_NAMES`
   (`_tool_injection.py:41-48`) and `_build_injected_tools_addendum` (`:207`) remains
   scope-blind — scoped agents are still told about delegation tools they don't have. This is
   the single highest-leverage half-day fix in the platform.

5. **Memory architecture is right; memory operations are not.** The three-scope model
   (user-private / agent-shared / org-global) is exactly the correct answer to "whose memory
   applies when multiple people use one agent." But: Mem0 and Graphiti are **off by default**
   (`MEM0_ENABLED=false`, `GRAPHITI_ENABLED=false`), Graphiti search is not `group_id`-scoped
   (cross-user timeline leakage), the orchestrator chat path injects only user scope while the
   named-agent path injects all three (divergent behavior by entry point), and there is no
   decay/compaction — memories accumulate forever.

6. **Artifact durability has a runtime-shaped hole.** The blob store only backs
   `agent-data/ inputs/ outputs/` (`STORE_FOLDERS`, `blob_store.py:39`), but Copilot-SDK
   agents write to the workspace **root** via native tools (documented in
   `agents-workspaces-artifacts.md` TL;DR #2). Those files exist only on disk — a volume wipe
   or box migration silently loses exactly the files the coding-class agents produce.

7. **Chat-level HITL is genuinely strong** (parked futures, cross-worker Redis control bus,
   reconnect replay, both-runtime parity tests). **Write-path HITL is not real yet**: the
   ~~Action Broker ships with zero handlers and real ClickUp/email writes bypass it (BO-1)~~
   *(corrected 2026-08-09: false since 2026-07-13 — the broker is live and wired at six
   registration sites; `ACTION_BROKER_ENFORCE` ships OFF and email handlers remain BO-1c;
   see `work_plan.md` WS-1)*, and
   `require_internal_auth` fails open with no token configured (BO-2). These remain the
   correct P0s.

8. **Document co-authoring is a good 80% solution** — the shared-file canvas (agent writes,
   `DocumentPane` polls at 1.2 s, user PUTs edits back to the same workspace file, open docs
   folded into agent context) matches VS Code mental models. The remaining 20% is
   concurrency-control and awareness, not CRDT.

---

## 2. The dual-runtime question (the core decision)

### 2.1 What the split actually is

| | Native MAF (`Agent`) | Copilot-SDK (`GitHubCopilotAgent` / `MetoriteCopilotAgent`) |
|---|---|---|
| LLM loop | in-process, gateway `/v1` (LiteLLM SDK) | Copilot CLI subprocess over JSON-RPC, BYOK provider → gateway `/v1` |
| Built-in tools | none — everything injected by `_tool_injection.py` | shell, file read/write/edit, MCP, todo, `ask_user`, session compaction |
| Session state | thread history via `assemble_run_context` / Redis | server-side `service_session_id`, persisted in `chat_session` (`executor.py:4314-4422`) |
| Stream path | Tier 1 (`executor.py:2138-2390`) | Tier 1.5 (`executor.py:2392-2993`) |
| Exemplars | `orchestrator`, `email-assistant` | `metorite` (self-anneal), mutation sandbox, `task-manager`*, `apis-config`* |

\* mislabeled/misplaced — see 2.3.

Both conform to `SupportsAgentRun`; mixed-runtime `WorkflowBuilder` graphs build on the
installed core 1.8.1 (verified in `multi_agent_orchestration.md` §3). So there is **no
architectural fork to heal** — the unification layer already exists. The question is purely
*which engine each agent should use*, and the answer is by workload:

- **Coding-class work** (edit repos, run tests, shell, multi-file changes): Copilot engine.
  This is the VS-Code-Orchestrator heritage and it earns its keep here.
- **Tool-API work** (call gateway/skill APIs, retrieve, draft, route): native MAF. Lower
  latency, no subprocess, no session-resume machinery, full prompt-cache control, cleaner
  context assembly.

### 2.2 The measured cost of Tier 1.5

The Copilot path is the platform's most-customized subsystem. Live shims (each verified in
code): monkey-patched `start/_create_session/_resume_session/_stream_updates`
(`executor.py:2417-2428`), stale-session retry with history re-injection (`:2821-2918`),
`COPILOT_INFINITE_SESSIONS` window-guess neutralization (`_copilot_session.py:101-203`),
`_copilot_no_text_end` (`:4227`), the 7.8k-token scope-blind addendum, thread-hop
ContextVar fallbacks (`_RUN_QUEUES`, `resolve_relay_thread_id`), and the telemetry
killswitch. The Phase-4 framework uplift retires several of these (spec §5.5), but every
agent moved off this path stops paying all of them at once.

### 2.3 Concrete moves

1. **Migrate `task-manager` and `apis-config` to native MAF `Agent`.** Their tools are
   plain Python callables already (skill-task-gtd functions; `web_search`). The
   email-assistant `build_agents()` (`agent-email-assistant/agents.py:1909-1951`) is the
   template. After this, Tier 1.5 serves only genuinely coding-class agents.
2. **Keep the Copilot engine for**: the mutation sandbox (`mutation_runner.py` — the one
   sanctioned raw-SDK path), the `metorite` self-anneal/dev agent, and any future
   "dev coworker" agents users chat with about code.
3. **Fix the runtime label.** `"runtime": "maf"` currently means two different things
   (executor path vs agent class) — `task-manager/config.json` says `maf` while
   `gateway/routes/agent.py:107` says `github-copilot` and `_declared_runtime` (`:175`)
   overrides. Rename the concept: every agent runs "on MAF"; add an explicit
   `engine: native | copilot-cli` field, and derive the tier from the built agent class
   (which `_is_copilot_sdk`, `executor.py:1995-1999`, already does by capability).
4. **Collapse to one registry.** Root `agent_registry.json` (7 aspirational MAF agents, 6
   with no source) is stale and unused by the gateway; the truth is
   `_AGENT_REGISTRY` + Postgres `dynamic_agents` + per-agent `config.json`. Delete or
   regenerate the root file from the live registry; make `config.json` the single
   declaration of runtime/engine, tools, and triggers.
5. **Reconcile the constraint docs.** `project-docs/AGENTS.md` (2026-07-07) still says
   "MAF is the sole runtime; Copilot SDK mutation-only"; root `AGENTS.md` (2026-07-13)
   sanctions Tier 1.5 interactive chat; `apps/services/orchestrator/AGENTS.md:22` and the
   webhook comment (`agent.py:2745`) still deny it. One statement, one place.

### 2.4 Answer to "should we retain both?"

**Yes — as one framework with two engines, not as two agent frameworks.** The failure mode
to avoid is not "having two runtimes" (the substrate handles that); it is *defaulting new
agents to the Copilot engine because the old VS Code agents used it*. Constraint #9 (new
event-driven work defaults to MAF paths) is already right; enforce it by migrating the two
stragglers and making the engine an explicit, reviewed choice in `config.json`.

---

## 3. Orchestration, delegation & handoff

The L0–L3 layering in `multi_agent_orchestration.md` is the right architecture and this
review endorses its conclusions (agent-as-tools over handoff; `call_agent` over eager
`as_tool()`; graphs as the workflow-editor backbone; GroupChat/Magentic only for genuinely
free-form collaboration, with a `selection_func` before an LLM coordinator). Findings on top:

1. **Land Phase 0 now.** The email-handoff root cause is still live:
   `_CORE_STANDARD_TOOL_NAMES` omits the `call_agent` family, and the addendum is
   scope-blind by construction (`_build_injected_tools_addendum(*, is_sub_agent)` — no
   `tool_scope` parameter). Any scoped agent (`apis-config` today) still cannot delegate
   while its prompt claims it can. Phase 1 (gate `design.md`, trim registry entries,
   re-measure 7,827 → <2,000 tokens) is the next-highest-leverage day.
2. **Deduplicate the delegation stack.** Delegation exists twice: the orchestrator's
   `delegate_to_agent` + dynamic specialist `FunctionTool`s (`orchestrator/agents.py:222,
   290-380`) and the universally-injected `call_agent` family (`acb_skills/agent_tools.py`).
   Both funnel into `_run_sub_agent_streaming`, but the orchestrator pair lacks the
   cycle/depth guards. Make the orchestrator's specialist tools thin wrappers over
   `call_agent` so guards, streaming, and confirm gates exist exactly once.
3. **MAF workflow constructs are unused today — that's fine, but build L2 before Shape C.**
   All current orchestration is LLM-tool-driven (L1). The first structured need (weekly
   report pipeline, reconcile-then-escalate) should land as `WorkflowBuilder` graphs
   (Phase 2 of the spec: multi-loader, versioned graph spec, compiler) rather than more
   prompt-encoded sequencing.
4. **Handoff semantics: correctly rejected.** Users talk to a specialist; control must
   return. `HandoffBuilder`'s MAF-only restriction is irrelevant to this design — do not
   rewrite agents to satisfy it.

---

## 4. Memory & multi-user context

### 4.1 The model is right

`scope_key()` (`mem0_client.py:50-62`) gives exactly the three scopes the multi-user
question needs: `user:<email>` (private), `agent:<name>` (shared across users of that
agent), `org:global`. Tools exist for all three (`remember/save_memory`,
`recall_agent/save_agent_memory`, `recall_org/save_org_memory`), plus Graphiti timelines and
the entity graph for company facts. `chat_session`/`chat_message` are user-scoped with
ownership checks on reconnect/cancel. This is a better substrate than most commercial agent
platforms have.

### 4.2 On cross-pollination (the opinion asked for)

Cross-user learning through the **agent scope is a feature, not a bug — if writes are
disciplined.** Recommended policy:

- **User scope**: preferences, personal context, private facts. Never auto-promoted.
- **Agent scope**: durable *procedural* knowledge — procedures, domain facts, committed
  decisions. The write-hygiene rule from `agent_file_and_memory_framework.md` §8 ("save the
  committed outcome, never the proposal") should be stated in every agent's instructions,
  plus one more rule: **no personal identifiers in agent scope** — if a fact is about a
  person, it belongs in user scope or the entity graph.
- **Promotion, not osmosis**: rather than agents freely writing agent-scope memory
  mid-conversation, prefer a periodic distillation pass (or an explicit
  `save_agent_memory` with the above rules) that reviews user-scoped learnings and promotes
  the generalizable ones. At current team size, prompt-level rules + occasional review of
  the Memory Manager UI is sufficient; an approval gate on `save_org_memory` is worth adding
  since its "confirm with the user" is currently prompt-only.

### 4.3 Operational gaps to close

| # | Gap | Where | Fix |
|---|---|---|---|
| M1 | Mem0 + Graphiti **disabled by default** — fresh deploys run memoryless | `settings.py:252,256` | enable in prod env; assert at startup when chat is served without memory |
| M2 | Graphiti `search()` ignores `group_id` → cross-user timeline leakage | `graphiti_client.py:159-163` | pass `group_ids=[scope]`; default timeline recall to the acting user + org groups |
| M3 | Divergent injection: `/copilot/chat` path injects user scope only; `/agent/run/stream` injects user+agent+org | `orchestrator/agents.py:499-567` vs `routes/agent.py:1254-1280` | extract one `build_memory_block(user, agent)` used by both |
| M4 | No decay/TTL/compaction — memories never age out | `acb_memory` (absent) | periodic consolidation job: dedupe, decay stale user-scope facts, cap per-scope counts |
| M5 | `/memory/*` path-`user_id` IDOR (noted in-code) | `gateway/routes/memory.py:22-25` | derive user from `UserContext`, not the path |
| M6 | Embeddings silently degrade to zero-vectors without `OPENAI_API_KEY` | `main.py:895-905` | fail loudly / health-check red; zero-vector search *looks* alive |

### 4.4 Multi-user file isolation

Workspaces are keyed by `agent_name` only — every user of an agent shares
`inputs/ outputs/ agent-data/` on disk and in the blob store. For a small trusted team this
is acceptable *and useful* (shared deliverables), but `inputs/` uploads deserve a decision:
either declare the workspace team-shared (document it in the UI) or add per-user
`inputs/<user>/` partitions before inviting wider use. Session threads are already
per-user; the exposure is only the shared workspace + agent/org memory scopes.

---

## 5. Files & artifacts

What works: the three-folder contract, `write_artifact` path containment + auto-versioning,
write-through blob mirroring with append-only history, rehydrate-on-load, fault-in reads,
whole-tree workspace browser with secret filtering, and artifact events rendering live
cards in chat. Two gaps:

1. **Copilot-engine agents' files are not durable.** They write to the workspace root via
   native tools; only the three folders are blob-backed. Options (either is fine):
   (a) steer harder — inject a "write deliverables under `outputs/`" rule into the Copilot
   addendum *and* a post-run sweep that mirrors new/changed non-source files into the blob
   store as `outputs/`; or (b) extend `STORE_FOLDERS` backing to the whole tree minus
   `_EXCLUDED_DIRS` + gitignored source. A run that produced files a wipe can lose should be
   treated as a bug.
2. **Artifacts vs git is correctly split** (state → blob store, code → reviewed PR;
   workspace `.gitignore` protects deliverables from `git reset --hard`). Keep it; the only
   pending item is the known DEV-ONLY mutation-remote limitation
   (`docs/DESIGN_LIMITATION_native_maf_mutation.md`) before any multi-tenant use.
   *(ticketed as MT-0b — built 2026-08-08 pending review)*

---

## 6. Context management

Strong: single server-side assembler (`assemble_run_context`, `acb_llm/context.py:316-411`)
with real token budgets, whole-turn eviction before per-message truncation,
structure-aware compression (email/JSON), tier-alias window resolution, prompt-cache
breakpoints at the stable/dynamic seam, and the session-memory Redis cache keeping the
prefix byte-stable. Copilot-side compaction is deliberately neutralized for BYOK (correct,
given the backend guesses the window).

Improvements, in order:
1. **Summarize-then-evict.** Long threads currently *lose* oldest turns silently. Add a
   cheap-tier rolling summary of evicted turns injected as one system block (keeps the
   stable prefix; only the summary block churns).
2. **Real tokenizer on the string-prompt path** — the 4-chars/token heuristic
   (`executor.py:4067`) under/over-shoots on code-heavy content; `acb_llm` already has
   `count_message_tokens`.
3. **The Phase-4 framework uplift** (core 1.11 + satellites) retires the compaction
   window-guessing, the telemetry killswitch, and `_gate_injected_tool` — schedule it as
   its own cycle per the orchestration spec; the AG-UI interrupt/resume break (#6925) lands
   in our most-customized code, so it carries the schedule risk.

---

## 7. HITL & AG-UI

Working well: three HITL surfaces (confirmation / elicitation / native `ask_user`) with
blocking resume on a parked future, cross-worker delivery over the Redis control bus,
reconnect replay from cursors, Stop cascading to background children, both-runtime parity
tests, and fail-closed `request_confirmation` on destructive tools.

Gaps (all known, re-prioritized here):
1. **BO-1 — Action Broker is inert.** Zero registered handlers at ship; ClickUp/email
   writes bypass `broker.execute()`. Until wired, non-negotiable #4 is false. This is the
   platform's biggest promise/reality gap.
2. **BO-2 — auth fails open** without `GATEWAY_INTERNAL_TOKEN` (`deps.py:172-181`), and
   `ACB_TRUST_UNSIGNED_EMAIL` is an impersonation escape hatch. Require the token in prod.
3. **AG-UI state channel unused.** Backend never emits `STATE_SNAPSHOT`/`STATE_DELTA`;
   state is reconstructed by folding the event log (which works, and `chat_fold.py` is a
   solid server-side port). Don't adopt STATE_* piecemeal — do it as part of Phase 4's AG-UI
   uplift if at all; the fold approach is fine for current needs.
4. **Approvals UI is dormant by default** (empty unless `ACTION_BROKER_ENFORCE`); once BO-1
   lands, the inbox becomes real without UI work.

---

## 8. Document co-authoring

Current state: agent writes a doc → auto-opens live in `SidePanelEditor`/`DocumentPane`
(1.2 s polling) → user edits PUT back to the same workspace file → open docs are folded
into the agent's system context. This is genuine shared-file co-authoring with the right
mental model (one file, both parties, VS Code-style canvas). Not present: concurrency
control (last-write-wins), presence, mid-run awareness of user edits, a real editor
(`@monaco-editor/react` is an unused dependency; the pane is a `<textarea>`).

Recommended path — in order, stopping when it's good enough:
1. **Optimistic locking**: version/etag on workspace PUT; on conflict, show a diff-merge
   prompt instead of clobbering. Cheap, removes the only data-loss path.
2. **Edit-awareness for the agent**: when the user saves while a run is active, enqueue a
   system notice ("user edited outputs/plan.md — re-read before further edits") via the
   existing `_nq` steering queue (or message-injection middleware post-uplift, #6998).
3. **Adopt the dead Monaco dependency or drop it** — CodeMirror 6 is lighter if bundle size
   matters; either beats a textarea for markdown/code.
4. **CRDT/multi-cursor only if simultaneous human+human editing becomes a real workflow.**
   For human+agent turn-taking, 1–3 deliver the experience at a fraction of the complexity.

---

## 9. Prioritized action plan

| P | Item | Effort | Ref |
|---|---|---|---|
| **P0** | Land orchestration Phase 0: `call_agent` family into the tool floor; scope-aware addendum; scope-typo warning | ~½ day | §3.1 |
| **P0** | Enable Mem0 (+ Graphiti if Neo4j is up) in prod; startup assert | hours | §4.3 M1 |
| **P0** | BO-1 Action Broker enforcement + BO-2 auth fail-closed (existing checklist) | days | §7 |
| **P1** | Graphiti `group_id` scoping; unify the two memory-injection paths; `/memory` IDOR | ~1 day | §4.3 M2/M3/M5 |
| **P1** | Migrate `task-manager` + `apis-config` to native MAF; engine field in `config.json`; delete/regenerate stale root `agent_registry.json`; reconcile the three contradictory runtime-policy docs | ~2–3 days | §2.3 |
| **P1** | Orchestration Phase 1 (context discipline: gate design.md, trim registry, re-measure) | ~1 day | §3.1 |
| **P1** | Copilot root-write durability (steer to `outputs/` + post-run blob sweep) | ~1 day | §5.1 |
| **P2** | Workspace PUT optimistic locking + agent edit-awareness (co-authoring 1–2) | ~2 days | §8 |
| **P2** | Deduplicate delegation stack (orchestrator specialists → `call_agent`) | ~1 day | §3.2 |
| **P2** | Summarize-then-evict context; real tokenizer on string path | ~2 days | §6 |
| **P2** | Memory consolidation/decay job; `save_org_memory` approval gate | ~2 days | §4.3 M4 |
| **P3** | Phase 4 framework uplift (own cycle, per spec §5.5/§7) → then Phase 5 orchestrations / Shape C, workflow editor (L2) | 1–2 wks | §3.3, §6.3 |

---

## 10. Related documents

`specs/multi_agent_orchestration.md` (L0–L3, verified capability matrix) ·
`specs/agent_file_and_memory_framework.md` (durable-state contract) ·
`specs/llm_caching_memory.md` (scopes + caching) ·
`agents-workspaces-artifacts.md` (workspace layout) ·
`specs/multi_user_organization_research.md` (identity/tenancy research) ·
`FOUNDATION_BUILDOUT_CHECKLIST.md` (BO-*) · `specs/harness_hardening_2026-07.md` (HH-*).
