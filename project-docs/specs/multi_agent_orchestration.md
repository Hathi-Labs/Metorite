# Multi-Agent Orchestration — Architecture & Work Plan

**Status:** Phase 4 only — Phases 0/1 shipped, 2/3/5 superseded · verified against code on 2026-08-03 · Owner: WS-12
**Scope:** how MAF agents and GitHub Copilot SDK agents delegate to each other today, and the
backbone for the future visual workflow editor.

Every claim marked ✅ below was **verified by execution** against the live VPS
(`agent-framework-core==1.8.1`) or an isolated throwaway venv (`core==1.11.0` +
`orchestrations==1.0.0`) on 2026-07-17/18. Those reproduction commands are in
[§8](#8-appendix--reproduction) and are **not agent-runnable** (they require prod SSH);
§8.1 carries the local, agent-runnable equivalents added in the 2026-08-03 pass.

> **Update 2026-08-03 (truth pass) — THIS DOCUMENT IS NOW PHASE 4 ONLY.**
> The row was audited against code on 2026-08-03 and ~90% of what its title claims is
> already delivered elsewhere. What is left here:
>
> | Phase | State | Where it went |
> |---|---|---|
> | **0** — hand-off fix | ✅ **shipped 2026-07-22** | delegation family is in the floor (`_tool_injection.py:41-65`); addendum is scope-aware |
> | **1** — context discipline | ✅ **shipped / moot / reassigned** | 1.1 → `93b93a08` (#191); 1.2 moot; 1.3 → **WS-23** ([`skills_registry.md`](skills_registry.md) · [`skills_scope_out.md`](skills_scope_out.md)) |
> | **2–3** — workflow runtime + editor | 🚫 **superseded** | the shipped Workflows app ([`workflows_app.md`](workflows_app.md), ADR-028, D6) |
> | **4** — framework uplift | 🔲 **the only live work in this document** | all four §5.5 shims verified still in the tree on 2026-08-03 |
> | **5** — orchestrations / collab chat | 🚫 **shipped elsewhere / reassigned** | 5.2 = multiplayer rooms (WS-10); 5.1 → **WS-11** / [`workflows_app.md`](workflows_app.md) §8; 5.3 unchanged (skip) |
>
> **Scope of record.** A three-way disagreement existed until 2026-08-03 — the board cell said
> "Phases 1, 4", `work_plan.md` §3 D6 said "Phases 1/4/5", this banner said "Phases 1, 4 and 5".
> **Resolved: Phase 4 only.** The board and D6 are to be swept to match; where they still disagree,
> this header wins for *what remains in this document* and `work_plan.md` wins for ordering.
>
> **Non-goals** (explicitly out of scope for this spec, do not build them from here):
> - a second workflow engine, graph spec, compiler, runner or editor — D6, `workflows_app.md`
> - injected-tool-surface / addendum token reduction — **WS-23**, `skills_registry.md`
> - a collaborative multi-agent chat surface — **shipped** as multiplayer rooms; residue is WS-10's
>   owner-gated floor-control re-decision
> - choosing the uplift target (minimal vs full bump) — **OWNER-GATE**, see §6 Phase 4.0

> **Update 2026-08-01 (doc-truth pass) — Phases 2–3 SUPERSEDED. Do not build a second workflow
> engine from this document.** Phases 2 and 3 below (workflow graph spec, compiler, runner, editor
> UI) and §5.3's "the workflow editor *is* the orchestrator" are superseded by the **shipped
> Workflows app** — see [`workflows_app.md`](workflows_app.md), ADR-028, migration
> `infra/postgres/132_workflows.sql`, gateway `apps/services/gateway/gateway/routes/workflows/`,
> and the `/workflows` editor in the control plane — per `work_plan.md` decision **D6**.
> §7 open question 1 (agent-invocable workflows) was answered by **F13 workflows-as-tools**
> (shipped: `list_workflows` / `run_workflow` / `get_workflow_run`, see `workflows_app.md` F13);
> open question 2 (Postgres vs repo files) was answered: **Postgres** (`132_workflows.sql`).

---

## 1. Executive summary

1. **The email hand-off failure was not an orchestration gap.** `call_agent` exists and is
   injected everywhere. `technical-project-planner`'s `config.json: tool_scope` omitted it, and
   `_CORE_STANDARD_TOOL_NAMES` doesn't rescue it — so it was silently stripped. Meanwhile the
   system-prompt addendum **described `call_agent` anyway** and listed `email-assistant` by name.
   The agent was misinformed, not confused. **Fix: ~2 lines.**

2. **We already have the workflow backbone.** `WorkflowBuilder` + `AgentExecutor` ship in
   `agent-framework-core`, which we have. A graph mixing a native MAF agent and a Copilot SDK
   agent **builds and runs today** ✅ — no adapter, no new dependency.

3. **`BaseAgent` / `SupportsAgentRun` is the unification layer.** Both runtimes conform. This is
   the single most important architectural fact in this document; everything else follows.

4. **4 of MAF's 5 pre-built orchestrations accept mixed runtimes. `HandoffBuilder` does not** ✅ —
   it hard-rejects `GitHubCopilotAgent`. Ironic, given "handoff" is what we set out to build; but
   handoff is also **the wrong pattern** for our case (see [§5.1](#51-handoff-is-the-wrong-pattern-for-our-case)).

5. **Phases 0–3 need no dependency change at all** ✅ — ship them on core 1.8.1 first. The framework
   uplift to current (core 1.11 + satellites) is a **separate, worthwhile cycle** — not just the price
   of orchestrations, but a **maintenance dividend** that retires ≥4 live workarounds (§5.5), **all
   four of which were re-verified present in the tree on 2026-08-03**. Its cost is **one** forced
   vendor-SDK major + one breaking AG-UI change (§4.0/§7) — *corrected 2026-08-03: the openai
   1.99 → 2.x major landed independently and is already in `uv.lock` at `openai 2.38.0`.*
   Phases 0–1 have since shipped and Phase 5 has been reassigned, so this is now the whole document.

---

## 2. What actually broke

### 2.1 The chain

```
planner needs to email a file
  → reaches for call_agent            → NOT in its tool schema  (scope stripped it)
  → falls back to Copilot SDK's built-in Task/"general-purpose agent"
                                      → GitHub OAuth error (we run BYOK)  ← the decoy
  → falls back to shell mail clients  → none installed
  → gives up, tells the user to do it manually
```

Only the first link is real. Everything after is fallback flailing that generated unrelated errors.

### 2.2 Root cause A — `call_agent` is not in the guaranteed floor *(historical — fixed by Phase 0.1)*

> **Anchor corrected 2026-08-03.** `_CORE_STANDARD_TOOL_NAMES` now spans
> [`_tool_injection.py:41-65`](../../apps/services/orchestrator/orchestrator/_tool_injection.py#L41-L65)
> and **does** contain `call_agent` / `call_agents_parallel` / `call_agent_background` (Phase 0.1,
> landed 2026-07-22, with the rationale below quoted in its own comment). The list quoted next is the
> floor **as it was when the bug happened** — read §2 as the incident record, not current state.

At the time of the incident, `_CORE_STANDARD_TOOL_NAMES`
guarantees every tool an agent needs to **work alone** — and not one tool it needs to **hand off**:

```
web_search, fetch_page, write_artifact, share_artifact, manage_todo_list,
ask_questions, run_diagnostics, get_errors, save_note, recall_notes
```

The planner's declared scope:

```json
"tool_scope": ["web_search","fetch_page","write_artifact",
               "manage_todo_list","ask_user","save_note","recall_notes"]
```

Verified effective toolset on prod ✅:

```
ask_questions, ask_user, fetch_page, get_errors, manage_todo_list,
recall_notes, run_diagnostics, save_note, share_artifact, web_search, write_artifact

call_agent injected? False
```

This is a **live latent bug in `apis-config` too** — its scope also omits `call_agent`, so it
cannot delegate to anything. Three of four in-repo agents remembered to list it. The convention is
held together by remembering to type one line.

> Side finding: `ask_user` in that scope matches **no injected tool** (the real name is
> `ask_questions`). It silently no-ops. Nothing warns.

### 2.3 Root cause B — the addendum lies (the one that actually misled the model)

```python
def _build_injected_tools_addendum(*, is_sub_agent: bool = False) -> str:
```

No `tool_scope` parameter. It is **scope-blind by construction**. Verified on prod ✅:

```
addendum MENTIONS call_agent?   True
addendum lists email-assistant? True
```

So the planner's prompt described a tool it did not have, and named the exact agent to send it to.
That is why the transcript reads *"the call_agent function isn't wired here"* and *"let me try the
email-assistant agent directly"* — it knew the name **only** because the prompt supplied it.

### 2.4 Measured cost of the scope-blind addendum ✅

| Block | ~tokens | note |
|---|---:|---|
| **Full addendum** (per Copilot agent, per turn) | **7,827** | describes ~30 tools |
| ├─ design.md | 4,078 | injected unconditionally |
| └─ registry (all agents know all agents) | 416 | **5% of the total** |
| Sub-agent compact addendum | 1,227 | for comparison |

The planner receives **11 tools and a 7,827-token prompt describing ~30 of them.**

**Note for the "reduce clutter via hub-and-spoke" proposal:** the inter-agent awareness this would
remove is **416 tokens — 5%**. It would leave ~95% of the measured cost untouched while adding a
routing hop. The clutter is the scope-blind addendum and unconditional design.md, not the topology.

---

## 3. Verified capability matrix

### 3.1 The substrate — both runtimes conform ✅

```
native MAF Agent : Agent → AgentMiddlewareLayer → AgentTelemetryLayer → RawAgent → BaseAgent
Copilot SDK      : GitHubCopilotAgent → BaseAgent          ← NOT an `Agent` subclass

isinstance(maf_agent,     SupportsAgentRun) → True
isinstance(copilot_agent, SupportsAgentRun) → True
```

`AgentExecutor(agent: SupportsAgentRun, *, session, id, context_mode, context_filter)` accepts a
**runtime-checkable structural protocol**, not a class. Required members:
`name, description, id, create_session, get_session, run`. `create_session`/`get_session` come from
the shared `BaseAgent`; `run` from each runtime; `name`/`description`/`id` are set in `__init__`.

> ⚠️ **Gotcha that will bite anyone re-checking this:** a *class-level* `hasattr` reports **both**
> runtimes as non-conforming, because three required members are instance attributes. Test
> instances, never classes, or you will reach the opposite conclusion.

### 3.2 Mixed-runtime graph — builds today on core 1.8.1 ✅

```python
maf = Agent(client, "…", name="maf-node")
cop = GitHubCopilotAgent(name="copilot-node", instructions="…")
wf  = (WorkflowBuilder(start_executor=AgentExecutor(maf, id="a"))
       .add_edge(AgentExecutor(maf, id="a"), AgentExecutor(cop, id="b"))
       .build())
# -> MIXED-RUNTIME WORKFLOW BUILT OK -> Workflow
```

`as_tool()` also works on both → `FunctionTool` / `FunctionTool`.

### 3.3 Pre-built orchestrations — mixed-runtime support ✅

Tested in an isolated venv (`core==1.11.0`, `orchestrations==1.0.0`):

| Builder | Package | Mixed MAF + Copilot | Constraint found by execution |
|---|---|:---:|---|
| `WorkflowBuilder` + `AgentExecutor` | **core (have it)** | ✅ | none |
| `SequentialBuilder` | orchestrations | ✅ | — |
| `ConcurrentBuilder` | orchestrations | ✅ | needs ≥2 targets |
| `GroupChatBuilder` | orchestrations | ✅ | participants are `SupportsAgentRun`; **`orchestrator_agent` must be a native MAF `Agent`** |
| `MagenticBuilder` | orchestrations | ✅ | manager is `StandardMagenticManager(agent: SupportsAgentRun)` — **a Copilot agent can be the manager** |
| **`HandoffBuilder`** | orchestrations | ❌ | **hard TypeError** (below) |

```
Handoff Copilot-only -> TypeError: Participants must be Agent instances. Got GitHubCopilotAgent.
                        Handoff workflows require Agent because they rely on cloning, tool
                        injection, and middleware…
Handoff MAF-only     -> ValueError: Handoff workflows require all participant agents to have
                        'require_per_service_call_history_persistence=True'.
```

Confirmed by the docs: *"Handoff orchestration only supports `Agent` and the agents must support
local tools execution."*

### 3.4 Dependency reality (PyPI snapshot 2026-07-18 — **stale, must be re-resolved**)

> ⚠️ **This table is a 2026-07-18 PyPI snapshot, not current state.** Version numbers on PyPI move;
> nobody may bump against these numbers. **Phase 4.1 must re-resolve the whole set from PyPI at
> build time** and record what it actually got — the table below is background only.

```
agent-framework-orchestrations  latest 1.0.0   requires core <2,>=1.9.0
prod today: agent-framework-core 1.8.1                        ← below that floor
            (re-verified 2026-08-03: uv.lock and the repo .venv both carry core 1.8.1)

Latest satellites all pin core >=1.11.0 → coupled, move together:
  agent-framework-openai         1.10.1  requires core>=1.11.0 + openai>=2.25
  agent-framework-github-copilot 1.0.0rc3 requires core>=1.11.0 + github-copilot-sdk==1.0.2 (was 0.1.32 → MAJOR)
  agent-framework-ag-ui          1.0.0rc8 requires core>=1.11.0
  agent-framework-redis          1.0.0b260521  already current, needs only core>=1.6.0
```

> **Correction 2026-08-03 — the openai major already landed; Phase 4 drags ONE SDK major, not two.**
> This section, §4.0 and §7 all billed Phase 4 for `openai 1.99 → 2.x`. That is no longer true:
> `uv.lock` and the repo `.venv` both carry **`openai 2.38.0`** under an unchanged
> `agent-framework-openai 1.7.0` (whose only openai constraint is unpinned `{ name = "openai" }`),
> so the openai 2.x major came in independently of the framework bump. The **only** remaining
> forced vendor-SDK major is `github-copilot-sdk 0.1.32 → 1.0.2`, pinned exactly by
> `agent-framework-github-copilot`. §7's "two forced SDK majors" risk row is retired accordingly.

**Two shapes of upgrade.** (a) *Minimal:* core→1.9/1.10 + orchestrations, satellites untouched (they
only need `core <2`) — dodges the copilot-SDK major, and unlocks the orchestrations package that
**only WS-11 now consumes** (5.1, reassigned). (b) *Full:* core→1.11 + all satellites latest —
required to reach the copilot-side fixes in §5.5, but drags the **one remaining vendor-SDK major**
(github-copilot-sdk 0.1.32→1.0.2) and one breaking AG-UI change. The full-set resolved to
`core 1.11.0` in an isolated venv on 2026-07-18 ✅ — re-prove it (Phase 4.1) before acting on it.
(Note: `OpenAIChatCompletionClient` already takes `model=` on 1.8.1 — no `model_id` churn; the real
churn is the underlying copilot-SDK major, not the framework client surface.)

### 3.5 Capacity ✅ (my earlier memory concern was overblown)

```
RAM   : 3915 MB total · 2805 MB available · gateway RSS ≈ 418 MB · swap 4 GB (137 MB used)
Disk  : 48 G total · 23 G available · agent clones 3.5 G
        (metorite-dev 2.1 G, agent-sales-assistant 1.4 G — everything else < 10 MB)
```

Agents run **in-process on a shared venv**, so `sys.modules` is shared and the marginal cost of an
extra node is the agent object + its tools, not a new interpreter. Multi-agent workflows are very
likely fine. **Still measure in Phase 2** — but this is not the blocker I previously implied.

---

## 4. Target architecture — four layers

```
┌── L3  Pre-built orchestrations (Phase 5 — needs the Phase-4 uplift, core ≥1.9)
│       Sequential · Concurrent · GroupChat · Magentic     [mixed-runtime OK]
│       Handoff                                             [MAF-only — excluded]
├── L2  Designed workflows  ← the workflow-editor backbone   [core 1.8.1, have it]
│       WorkflowBuilder + AgentExecutor · graph authored in the editor
│       deterministic · traceable · checkpointable · per-node context control
├── L1  Conversational delegation  ← fixes the email bug     [core 1.8.1, have it]
│       call_agent / call_agents_parallel / call_agent_background
│       model-decided · ad-hoc · lazy-loaded · agent-as-tools semantics
└── L0  Substrate: BaseAgent / SupportsAgentRun              [nothing to do]
        both runtimes already conform
```

**L1 and L2 are complementary, not competing** — same substrate, different decision-maker:

| | L1 `call_agent` | L2 `Workflow` |
|---|---|---|
| Who decides the route | the **model**, mid-conversation | the **graph**, authored up front |
| Shape | ad-hoc, bounded subtask | designed pipeline |
| Loading | **lazy** — one repo at call time | eager — all nodes live |
| Determinism | none | full, traceable |
| Answers | "planner needs an email sent" | "run the weekly report pipeline" |

---

## 5. Decisions & rationale

### 5.1 Handoff is the wrong pattern for our case

Microsoft's own distinction:

> **Handoff** — control is explicitly passed; the receiving agent takes **full ownership** of the
> task and the conversation. **Agent-as-tools** — a primary agent delegates a subtask; once done,
> **control returns to the primary agent**, which retains overall responsibility and manages context.

The planner needed an email sent. The user is talking to the **planner** about a project plan — they
should not be dumped into the email agent. Control must return. That is **agent-as-tools**, which is
exactly what `call_agent` implements.

So `HandoffBuilder`'s Copilot restriction **does not block us** — we were never going to use it for
this. (Note also: MAF's handoff is internally a **mesh** — *"agents are connected directly without an
orchestrator"* — so even Microsoft's handoff is not hub-and-spoke.)

### 5.2 Keep `call_agent`; do not replace it with `as_tool()`

`call_agent` is a **lazy `as_tool()` over a dynamic registry**, plus guards MAF doesn't ship:

| | MAF `as_tool()` | our `call_agent` |
|---|:---:|:---:|
| HITL gate | `approval_mode` | `request_confirmation` (fails closed) |
| Sub-agent streaming | `stream_callback` | `_run_sub_agent_streaming` |
| **Lazy repo load** | ✗ needs a live instance | ✅ loads at call time |
| Cycle detection | ✗ | ✅ `_delegation_refusal` |
| Depth cap | ✗ | ✅ `_MAX_DELEGATION_DEPTH=2` |
| Timeout / tier inheritance | ✗ | ✅ |

`as_tool()` requires an **instantiated** agent. Using it for delegation would mean cloning and
building every potential delegate at load time — 8 repos, 3.5 GB, to answer one question. Our
registry is dynamic and lazily cloned; `call_agent` is the right abstraction **for L1**. We are not
behind MAF here.

### 5.3 The workflow editor *is* the orchestrator — a designed one

> **Update 2026-08-01 (doc-truth pass):** superseded — the shipped Workflows app is that editor.
> See the banner at the top of this document (decision D6).

The hub-and-spoke instinct is right that coordination should be centralised. It lands at **L2**, not
L1: the graph is the central authority, authored by a human, deterministic and traceable. This is
strictly better than Magentic's LLM-in-the-routing-loop for our case, and it avoids the
[information-bottleneck failure mode](https://claude.com/blog/multi-agent-coordination-patterns)
Anthropic documents for orchestrator patterns. Users chat directly with specialists, so no hub is
in the loop for L1 anyway.

### 5.4 Context discipline belongs at the node, not the topology

`AgentExecutor(context_mode='full' | 'last_agent' | 'custom', context_filter=…)` gives **per-node**
control over what each agent sees. That is a first-class knob that directly serves the
"avoid clutter" goal — and it needs no topology change.

### 5.5 The framework uplift is a maintenance dividend, not just orchestrations

**Phases 0–3 need zero dependency changes** — keep them on core 1.8.1 and ship them first. But the
uplift to current (core 1.11 + satellites) is **not** merely the price of the orchestrations package,
as an earlier draft framed it. Reading the actual changelogs (core 1.9→1.11, copilot-sdk 0.1.32→1.0.x),
the release wave fixes a cluster of bugs we currently **hand-work-around** — so the uplift lets us
*delete* shim code, not just add features. **All four shims re-verified present 2026-08-03** (exact
anchors below) — this is the reason WS-12 stays open at all:

| Upstream fix | Version | Shim it retires (verified present 2026-08-03) |
|---|---|---|
| Copilot SDK exposes `tokenPrices` + **context-window limits** on public types | copilot-sdk 1.0.2 | `COPILOT_INFINITE_SESSIONS` window-guessing — `_copilot_session.py:75` (also read at `:122`, `:158`) |
| "Disable harness compaction when max tokens not provided" (#6410) | core 1.9.0 | same false "context length exceeded", framework side |
| github-copilot function approval via `on_pre_tool_use` hook (#6750) + tool-approval middleware (#6414/#6522) | core 1.10/1.9 | `_gate_injected_tool` — **defined** at `_tool_injection.py:280`, re-exported through `executor.py:85`; exists only because `on_permission_request` skips injected tools |
| Telemetry-context fixes: background ctx error (#6764), OTel parent ctx for deferred streams (#6709), span nesting (#6552) | core 1.10.0 | the **telemetry killswitch** — `executor.py:113-140` (`ENABLE_INSTRUMENTATION` read at `:138`) |
| Message-injection middleware — enqueue into an active run (#6998) | core 1.11.0 | native-MAF `_nq` steering queue — `executor.py:2680` — + write_artifact steering |
| Structured-response parse fix — avoids spurious `ValidationError`/`JSONDecodeError` (#6383) | core 1.11.0 | JSON-mode fragility ([[llm-json-mode-required]]) |
| `defer` / `toolSearch` native lazy tool loading + progressive MCP disclosure (#6850) | copilot-sdk 1.0.2/1.0.7, core 1.11 | hand tool-count management in `_CORE_STANDARD_TOOL_NAMES` |

Each row is a place we could remove code. That is a maintenance dividend, not a feature wishlist — and
it is why the uplift is worth scheduling **sooner than "only when we need Magentic/GroupChat."** Every
release we skip, we keep maintaining shims for bugs already fixed upstream; the debt compounds.

**The catch is real too:** the good fixes concentrate in core 1.11 + copilot-sdk 1.0.2 — i.e. the full
coordinated bump (§3.4), which drags **one vendor SDK major** (github-copilot-sdk 0.1.32→1.0.2 —
*corrected 2026-08-03: the openai 1.99→2.x major already landed independently*) and **one breaking
AG-UI change** (interrupt/resume canonicalization, #6925) against our most-customized subsystem. So it
is a genuine investment with a genuine payoff — scoped as its own cycle (Phase 4), not folded into the
Phase 0 bug-fix. When adopting orchestrations, still expose Magentic/GroupChat as **node types inside a
graph**, not a parallel top-level architecture — **that item is now WS-11's** (see Phase 5.1).

### 5.6 Collaborative multi-agent chat — the three shapes of "collaboration" *(design note — Shape C has SHIPPED)*

> **Update 2026-08-03:** the analysis below stands and was **vindicated by what shipped**. Shape C
> exists today as **multiplayer rooms**, built the cheapest-first way this section recommends — a
> rule-based selector, not an LLM coordinator: `RoomAgent.role: "primary" | "mentioned"` in
> [`workbench/control_plane/src/lib/rooms.ts:31-37`](../../workbench/control_plane/src/lib/rooms.ts#L31-L37)
> ("`primary` answers an unaddressed turn; `mentioned` answers when @named") is exactly option 1
> below, `floorMode: "open" | "driver"` (`rooms.ts:85`) is the turn discipline, and
> `apps/services/orchestrator/orchestrator/steer.py::route_turn` (`:123`) is the routing decision.
> It was built **without** `agent-framework-orchestrations` — that package is absent from `uv.lock`
> (0 occurrences, verified 2026-08-03) and never became a dependency. Keep this section as the
> reasoning record; **do not build Shape C from it.** Owner of the residue: **WS-10**.

"Multiple agents collaborating" is not one thing. It is three, and only the third needs a runtime
coordinator ("orchestrator"). Getting this distinction wrong leads to building L3 machinery for
problems L1/L2 already solve.

| Shape | What it is | Coordinator | Layer |
|---|---|---|---|
| **A. One owner pulls in helpers** | The conversation-owning agent delegates bounded subtasks; control returns to it | none — the owning agent **is** the coordinator | **L1** `call_agent` (agent-as-tools) |
| **B. Designed pipeline** | Fixed flow (planner → researcher → reviewer) authored up front | none — the **graph** coordinates, deterministically | **L2** `WorkflowBuilder` |
| **C. Free-form room** | Agents share one conversation, see each other's messages, dynamically build on them / take turns | **required** — something must pick who speaks next | **L3** `GroupChatBuilder` / `MagenticBuilder` |

**The user's "do I need an orchestrator for multi-agent chat?" resolves to: only for Shape C.**
Shapes A and B collaborate with no orchestrator.

**Shape C's coordinator does NOT have to be an LLM agent.** `GroupChatBuilder` rejects an unconfigured
call with: *"No orchestrator has been configured. Pass `orchestrator_agent`, `orchestrator`, or
`selection_func`."* (evidence: the constructor's own error message, not a positive build test). So the
"who speaks next" decision has three implementations, cheapest first:

1. **`selection_func`** — a plain Python function (round-robin, rule-based). No LLM, no per-turn cost,
   deterministic. Most "collaboration" is really just turn-taking and lands here.
2. **`orchestrator_agent`** — an LLM agent that reads the conversation and chooses. Flexible; adds a
   model call per turn. **Typed as a native MAF `Agent`** — so keep the selector MAF-side.
3. **Magentic manager** — `StandardMagenticManager(agent: SupportsAgentRun)`; also **plans** and tracks
   progress for open-ended tasks. Verified ✅: the manager may be a **Copilot** agent.

**Mixed-runtime support for Shape C** (verified ✅): the *participants* (collaborating agents) can be
mixed MAF + Copilot in both `GroupChat` and `Magentic`. The only runtime constraint is on the
*coordinator role* — GroupChat's `orchestrator_agent` is MAF-typed (sidestepped entirely by using a
`selection_func`); Magentic's manager accepts either runtime.

**Caveat before building Shape C:** Anthropic's
[coordination-patterns writeup](https://claude.com/blog/multi-agent-coordination-patterns) flags
free-form multi-agent chat as the *least predictable* pattern — agents duplicate work or talk past
each other without firm turn-taking + termination rules. It is the most impressive demo and the least
reliable in production. Before reaching for Shape C, ask whether a **Shape B designed workflow**
produces the same outcome with full traceability. Prefer B unless the collaboration genuinely must be
dynamic and open-ended.

---

## 6. Work plan

### Phase 0 — Fix the hand-off *(≈half a day · no deps · unblocks email today)*

> **✅ LANDED 2026-07-22** — 0.1 delegation family in `_CORE_STANDARD_TOOL_NAMES`;
> 0.2 `_build_injected_tools_addendum(effective_scope=…)` emits only sections for
> tools actually injected (per-variant lru_cache keeps each agent's prefix
> byte-stable); 0.3 `executor.tool_scope_unknown_entry` warning (catches
> `ask_user`); 0.4 tests in `tests/unit/test_tool_scope_addendum.py` +
> `test_core_tool_floor.py`. **Re-verified 2026-08-03** — the floor at
> `_tool_injection.py:41-65` contains all three delegation tools.
> *(The trailing "Phase 1 … remains open" sentence was true on 2026-07-22 and false
> by 2026-08-03; see Phase 1's own note below.)*

| # | Change | File |
|---|---|---|
| 0.1 | Add `call_agent`, `call_agents_parallel`, `call_agent_background` to `_CORE_STANDARD_TOOL_NAMES` | `_tool_injection.py` |
| 0.2 | Thread `tool_scope` into `_build_injected_tools_addendum(*, is_sub_agent, tool_scope)`; emit only sections for tools actually injected | `_tool_injection.py` |
| 0.3 | Warn when a `tool_scope` entry matches no known tool (catches `ask_user`) | `_tool_injection.py` |
| 0.4 | Tests: floor includes delegation; addendum omits un-injected tools; scope-typo warns | `tests/unit/test_core_tool_floor.py` |

**Safety:** adding `call_agent` to the floor means every agent can reach every agent. Blast radius
stays bounded — each target's own `request_confirmation` gate still requires a human, and
`_delegation_refusal` + depth cap already guard recursion.
**Done when:** the planner can email a file via `call_agent("email-assistant", …)`, attaching
`technical-project-planner:outputs/…` (that cross-workspace syntax **already works**).

### ~~Phase 1 — Context discipline~~ — ✅ STRUCK 2026-08-03 (shipped / moot / reassigned)

> **Nothing in Phase 1 is work. Do not dispatch it.** Each item below is struck with its evidence.
> **Context discipline is owned by WS-23** — [`skills_registry.md`](skills_registry.md) +
> [`skills_scope_out.md`](skills_scope_out.md). Take any further prompt-budget work there, not here.

- ~~**1.1** Gate `design.md` (4,078 tok) on need — skip for agents that never render documents/UI.~~
  **✅ SHIPPED ~6 weeks ago** as `93b93a08` *"feat(agents): design.md on demand via
  `load_design_system()` — off every prompt (#191)"* — `packages/acb_skills/acb_skills/design_tools.py`
  + `tests/unit/test_design_tools.py`, and `_tool_injection.py:595-597` now reads *"the full ~16KB
  design.md is no longer injected into every prompt."*
  ⚠️ **It shipped as a different mechanism than proposed here.** The proposal was *per-agent gating*
  ("skip for agents that never render UI"); what shipped is **progressive disclosure** — `design.md`
  is off *every* prompt for *every* agent and is pulled on demand via the `load_design_system` tool,
  which now sits in the core floor (`_tool_injection.py:46`). Do not re-open this as "gating was
  never built": the goal was met by a better mechanism.
- ~~**1.2** Trim registry descriptions to one line.~~ **MOOT — the subject does not exist.**
  `technical-project-planner` is in neither `_AGENT_REGISTRY` nor `apps/agents/` (the six live agents
  are `apis-config`, `app-builder`, `email-assistant`, `orchestrator`, `task-manager`,
  `whatsapp-assistant`); its only trace is a stale diagnostic comment at `_copilot_session.py:59`.
  All six live `config.json` descriptions are already single-line — measured 2026-08-03 at
  130–222 chars (≈32–55 tokens), zero newlines. Nothing to trim.
- ~~**1.3** Re-measure. Target: **7,827 → under 2,000** for a scoped agent.~~
  **DELIVERED BY WS-23 (S1 baseline, S4 diet).** Measured: full injected surface **19,259 → 12,644**
  tokens; addendum **5,697 → 570** behind `SKILLS_INDEX_ONLY` (shipped **OFF**, OWNER-GATE).
  See `skills_registry.md` §S1/§S4 and `skills_scope_out.md` §7.
  🚫 **The bare "under 2,000" target is withdrawn — it was ambiguous and kept being misread.**
  Restated precisely: the target was the **addendum**, and it is **met at 570 tokens behind
  `SKILLS_INDEX_ONLY`**. It was **never** a target for the whole injected-tool surface, and must not
  be quoted as one: `skills_scope_out.md` §7.4 proves that reading unreachable — the 22 core-floor
  schemas cost **1,252 tokens with every description deleted**, so ≤2k on the full surface is
  arithmetically out of reach without progressive tool disclosure (designed and costed in
  `skills_scope_out.md` §7.5, deliberately not built).

### ~~Phase 2 — Workflow runtime~~ — 🚫 SUPERSEDED (D6)

> **Update 2026-08-01 (doc-truth pass):** SUPERSEDED by the shipped Workflows app — see the banner
> at the top of this document (decision D6). Kept for the record; do not implement.

- **2.1 Multi-loader.** `ExitStack` over N `load_agent()` contexts. Today `load_agent` is a
  context manager yielding **one** agent per run; a graph needs N live at once. **This is the real
  work.**
- **2.2 Per-node tool injection.** Apply each node's `tool_scope` independently (depends on Phase 0.2).
- **2.3 Graph spec.** Versioned JSON: nodes (`agent_name`, `tool_scope?`, `context_mode`,
  `context_filter?`), edges (incl. `switch_case` / `fan_out` / `fan_in`), `start`, `output_from`.
  **This is the editor's save format — design it before the UI.**
- **2.4 Compiler.** spec → `AgentExecutor` per node → `WorkflowBuilder` → `.build()`.
  Pass `output_from=` explicitly (omitting it is **deprecated** and will break).
- **2.5 Runner + streaming.** Bridge workflow events to the existing SSE relay; reuse the
  `SUB_AGENT_*` event shape.
- **2.6 Measure memory** with all 8 agents live; confirm §3.5.

**Editor vocabulary is already covered by core:** `add_chain`, `add_edge`, `add_fan_out_edges`,
`add_fan_in_edges`, `add_switch_case_edge_group`, `add_multi_selection_edge_group`.

### ~~Phase 3 — Workflow editor UI~~ — 🚫 SUPERSEDED (D6)

> **Update 2026-08-01 (doc-truth pass):** SUPERSEDED by the shipped Workflows app's `/workflows`
> editor — see the banner at the top of this document (decision D6). Do not implement.

Node palette from the live registry · canvas → graph spec · save/load · run + live trace.

### Phase 4 — Framework uplift & migration to latest *(≈1–2 weeks · its own hardening cycle)* — **THE ONLY LIVE PHASE**

Migrate the whole `agent-framework` stack to current. Justified by the **workaround dividend** (§5.5) —
all four shims re-verified in the tree 2026-08-03. Scope it as a standalone cycle.

> ### ⚠️ Hazard — 4.1 must not mutate any venv it did not create
>
> The original instruction was **"never touch `/opt/acb/app/.venv`"** (the prod venv). That is still
> binding, and **on a dev box it is not enough**: 4.1 must also **never mutate the repo's own
> `.venv`**. `uv pip install`, `uv sync`, `uv lock` and `uv add` run against the project venv by
> default and would silently upgrade the tree an agent is testing against, invalidating every
> measurement in this document and breaking the local test suite. 4.1 is **evidence-only** — it
> creates a throwaway venv in a scratch directory, installs into it **by explicit
> `--python <throwaway>/bin/python`**, records the result, and deletes it. If a command in 4.1 would
> write to `<repo>/.venv` or `<repo>/uv.lock`, that command belongs in **4.2**, not 4.1.

**4.0 — Version target.** *(Table below is the 2026-07-18 PyPI snapshot; §3.4's warning applies —
re-resolve before acting.)* The satellites are coupled: openai/copilot/ag-ui *latest* all pin
`core >=1.11.0`, so they move together. It is one coordinated bump, not piecemeal.

**Choosing between the two shapes is an 🔒 OWNER-GATE.** *(Registered as an owner call, not a ticket.)*
It is a cost/risk trade the owner makes, not a fact an agent can derive. An agent may **produce the
evidence** (4.1) and must then **stop and report**. It may not run 4.2 until the owner picks a shape.
Rationale: minimal-bump leaves the copilot-SDK major and the four §5.5 shims in place; full-bump takes
the vendor major and the breaking AG-UI change against our most-customized subsystem. Neither is
"correct" — it is a schedule decision.

| Package | Installed (verified 2026-08-03) | Snapshot target | Bump drags in |
|---|---|---|---|
| agent-framework-core | 1.8.1 | **1.11.0** | — |
| agent-framework-openai | 1.7.0 | **1.10.1** | — *(openai 2.x **already installed**: `uv.lock` = `openai 2.38.0`)* |
| agent-framework-github-copilot | 1.0.0b260402 | **1.0.0rc3** | **github-copilot-sdk 0.1.32 → 1.0.2** (the one remaining SDK major) |
| agent-framework-ag-ui | 1.0.0rc3 | **1.0.0rc8** | breaking interrupt/resume (#6925) |
| agent-framework-redis | 1.0.0b260521 | 1.0.0b260521 | **already current — no change** |
| agent-framework-orchestrations | *(absent from `uv.lock`)* | **1.0.0** | needs core ≥1.9 (**not** satisfied at 1.8.1) |
| github-copilot-sdk | 0.1.32 | 1.0.2 | **pinned exactly** by copilot rc3 (not 1.0.7) |

> **Minimal-bump fallback:** orchestrations needs only **core ≥1.9**, and our *currently-installed*
> satellites only require `core <2` — so core→1.9/1.10 + orchestrations, leaving the vendor SDK
> untouched, is a lighter path that dodges the copilot-SDK major. Note its payoff shrank on
> 2026-08-03: the orchestrations package it unlocks now has **exactly one consumer left** — WS-11's
> 5.1 — since Phase 5.2 shipped without it. Produce the evidence in 4.1; the owner picks.

---

**4.1 — Resolution proof (isolated throwaway venv). 🟢 AGENT-SAFE — evidence only; produce it, do not choose.**

**Done when:** a committed `docs/framework-uplift/framework-uplift-resolution.md` exists containing
**both** fully-resolved lock sets, each produced in its own throwaway venv:

| Set | Contents |
|---|---|
| **minimal** | `agent-framework-core` (1.9/1.10 line) + `agent-framework-orchestrations`, satellites left at their currently-installed versions |
| **full** | `agent-framework-core` 1.11 line + `agent-framework-openai` + `agent-framework-github-copilot` + `agent-framework-ag-ui` + `agent-framework-redis` + `agent-framework-orchestrations` |

and for **each** set the document records, verbatim:
1. the exact `uv pip install` command that produced it (including the explicit
   `--python <throwaway>/…` that kept it off the repo venv);
2. the full `uv pip list` output of the resolved venv (this **is** the lock set — no hand-typed
   version tables, and no version number quoted from `§3.4`'s stale snapshot);
3. an **import smoke** whose output is pasted in, run inside that venv, covering all three surfaces —
   `import agent_framework`, `import agent_framework.openai`, `import agent_framework_github_copilot`
   — printing each module's `__version__` (or `importlib.metadata.version`) and exiting non-zero on
   any ImportError;
4. any resolution **conflict or backtrack** uv reported, quoted, or the explicit line
   "no conflicts reported";
5. the throwaway venv's path and proof it was deleted.

**And the document must NOT contain a recommendation, a preference, or a chosen shape.** Its last
section is titled *"Evidence for the owner's 4.0 decision"* and states the trade-off neutrally.
An agent that finishes 4.1 **stops and reports** (🔒 the 4.0 choice).

**Not done if:** `<repo>/.venv` or `<repo>/uv.lock` changed. Verify with `git status --short uv.lock`
(must be empty) and by confirming `uv pip list --python .venv/…` still shows `agent-framework-core
1.8.1` / `openai 2.38.0` after 4.1 completes.

---

**4.2 — Land the coordinated bump. 🟢 AGENT-SAFE** *(but blocked until the owner resolves 4.0)*

Update the `pyproject.toml` pins that carry `agent-framework-*` and `uv sync`; redis unchanged.

**Done when:** (a) `uv.lock` is committed and `git status --short uv.lock` is empty after a fresh
`uv sync`; (b) every `agent-framework-*` version in `uv.lock` matches the owner-chosen lock set from
4.1 **exactly**, with no package resolved outside it; (c) the same three-import smoke from 4.1 passes
against the repo venv; (d) the Phase-4 verification block below is green (see §6.4v).
**Anti-drift:** the four `pyproject.toml` files were named as *orchestrator, gateway,
agent-email-assistant, agent-task-manager* on 2026-07-17 — **re-derive the list at build time**
(`grep -rln "agent-framework" --include=pyproject.toml .`) rather than trusting that list; the
`apps/agents/` tree has changed since.

---

**4.3 — Absorb the forced SDK major. 🟢 AGENT-SAFE**

`github-copilot-sdk 0.1.32 → 1.0.2` is the real risk — **6 of 6** in-repo agents ride the Copilot
path. *(The "openai 1.99→2.x" half of this item is struck: already at 2.38.0.)* Re-verify against
[[maf-agent-openai-client-choice]] and [[copilot-sdk-context-window-unknown]].

**Done when:** (a) a written diff-review of the copilot-SDK **session and tool-call API shape**
(`create_session` / session options / tool-registration / permission-hook signatures) between 0.1.32
and 1.0.2 is recorded in the same `docs/framework-uplift/` file, naming every call site in
`_copilot_session.py` and `copilot_agent.py` that the change touches, or stating "no signature
change" per surface; (b) `uv run python -m pytest tests/unit/test_hitl_both_runtimes.py
tests/unit/test_ask_user_hitl.py tests/unit/test_permission_policy.py -q` is green; (c) the model-tier
resolution path still resolves — `uv run python -m pytest tests/unit/test_model_resolution.py -q`
(substitute the real filename if it has moved; derive it, do not trust this line).

---

**4.4 — Migrate the one breaking AG-UI change (#6925). 🟢 AGENT-SAFE**

Interrupt/resume is canonicalized around `RUN_FINISHED.outcome.interrupts` + `ResumeEntry`. This hits
our most-customized code — the HITL resume path.

> **Anchors corrected 2026-08-03.** The previous text placed `resolve_relay_thread_id` and
> `_pending_user_input` in **`ask_tools.py`** — **that file does not exist anywhere in the repo.**
> Both live in `apps/services/orchestrator/orchestrator/executor.py`:
> `resolve_relay_thread_id` at **`:220`** and `_pending_user_input` at **`:339`**
> (*not* `:257` — line 257 is a comment inside the neighbouring `_active_elicitation_request_id`
> ContextVar that merely mentions the name). Re-derive both with
> `grep -n "def resolve_relay_thread_id\|^_pending_user_input" apps/services/orchestrator/orchestrator/executor.py`
> before editing; this file changes often.

**Done when:** (a) the HITL resume path builds and parks/resumes on the new
`RUN_FINISHED.outcome.interrupts` + `ResumeEntry` shape with no compatibility shim left behind;
(b) `uv run python -m pytest tests/unit/test_hitl_both_runtimes.py tests/unit/test_ask_user_hitl.py
tests/unit/test_hitl_heartbeat.py tests/unit/test_hitl_stall_suppression.py
tests/unit/test_genui_hitl.py -q` is green **without** any test being skipped or xfailed as part of
the migration (a skipped HITL test is a failed 4.4); (c) `uv run python -m pytest
evals/trajectories/test_hitl_trajectory.py evals/trajectories/test_stream_replay_trajectory.py -q`
is green.
**Gains that ride along:** SSE keepalive for silent streams (#6980 — targets our idle-watchdog/HITL
stalls), AG-UI thread snapshot persistence (#6471), clear-queued-approvals-on-cancel (#6947),
preserve streamed text message id in mixed snapshots (#6269).

---

**4.5 — Retire the shims, one at a time, each behind its own verification.**

Work the §5.5 table. For each row, confirm the upstream fix actually covers *our* case before
deleting the workaround — these are strong candidates, **not guarantees**, which is why each row
below carries its own done-when rather than one gate for the set.

**The shape of every done-when here is the same:** the named test is green **today with the shim in
place**; it must be green **after the shim is deleted**, with the assertions that pin the shim's
existence replaced by assertions that pin the upstream behaviour — not deleted, not skipped.

- **4.5.1 — Telemetry killswitch** (`executor.py:113-140`). 🔒 **OWNER-GATE — do not flip.**
  Already registered in `work_plan.md` §6 under **WS-6 observability activation**
  (*"re-enabling the MAF telemetry kill switch … it hides a known ContextVar-reset bug"*; §6 cites
  `executor.py:114`, the comment banner — the env read is at `:138`).
  **An agent may:** delete the shim's *code* behind an unchanged default and demonstrate the
  ContextVar-reset bug is gone upstream. **An agent may not:** make instrumentation on-by-default,
  or set `ENABLE_INSTRUMENTATION=1` in any committed env/deploy file.
  **Done when:** `tests/unit/test_executor_telemetry_killswitch.py` goes from asserting *"we disable
  agent_framework instrumentation unless opted in"* to asserting the post-uplift contract, and is
  green with `_disable_agent_telemetry_once` removed; plus a recorded streamed-run trace showing no
  `"Token was created in a different Context"` at end-of-run ([[chat-maf-telemetry-contextvar-bug]]).
  Command: `uv run python -m pytest tests/unit/test_executor_telemetry_killswitch.py -q`
- **4.5.2 — `COPILOT_INFINITE_SESSIONS`** (`_copilot_session.py:75`). 🟢 AGENT-SAFE.
  **Done when:** the real context window is read off the SDK's public model type (`ModelBilling` /
  `tokenPrices`) instead of guessed, all three env overrides (`COPILOT_INFINITE_SESSIONS`,
  `COPILOT_COMPACTION_THRESHOLD`, `COPILOT_BUFFER_THRESHOLD`) are gone from
  `_copilot_session.py`, and a test asserts the window comes from the SDK for a gateway-routed BYOK
  model. Command: `uv run python -m pytest tests/unit/test_copilot_session.py -q`
  *(derive the real filename; if no such test exists, adding it is part of 4.5.2.)*
- **4.5.3 — `_gate_injected_tool`** (`_tool_injection.py:280`, re-exported at `executor.py:85`).
  🟢 AGENT-SAFE. **Done when:** injected tools are gated by the native `on_pre_tool_use` hook and
  `_gate_injected_tool` is deleted, with **`tests/unit/test_permission_policy.py` green** — that
  file is the shim's pin (it is the only test referencing `_gate_injected_tool` by name) and its
  `test_inject_rewraps_repo_baked_tools`-style assertions must be **rewritten to assert the hook
  fires for injected tools**, not removed. Fail-closed behaviour must not regress (root AGENTS.md
  harness rule 2). Commands: `uv run python -m pytest tests/unit/test_permission_policy.py -q` and
  `uv run python -m pytest evals/trajectories/test_permission_trajectory.py -q`
- **4.5.4 — Native-MAF `_nq` steering queue** (`executor.py:2680`). 🟢 AGENT-SAFE.
  **Done when:** message-injection middleware (#6998) replaces the hand-rolled queue, `_nq` and
  `_active_run_queue` are gone from `executor.py`, and the steer contract is unchanged —
  `tests/unit/test_steer_routing.py` (DROP/ENGAGE/ABORT/STEER, `202 {"steered": true}`,
  `409 steer_outside_run_floor`) and `tests/unit/test_supersede_guard.py` green with **no assertion
  weakened**. This one is coupled to WS-10: steer shipped on `_nq` (`15c8933f`), so 4.5.4 is a
  behaviour-preserving swap under a shipped feature — treat any assertion change as a red flag.
  Command: `uv run python -m pytest tests/unit/test_steer_routing.py tests/unit/test_supersede_guard.py -q`

---

**4.6 — Gate.**

> **Corrected 2026-08-03: there is no "21/21 eval suite".** The offline trajectory suite collects
> **135 tests** (`uv run python -m pytest evals/trajectories/ -q --collect-only`, measured
> 2026-08-03). The "21/21" figure was stale and must not be used as a pass criterion.

**Done when:**
1. `uv run python -m pytest evals/trajectories/ -q` shows **no new failures relative to the
   pre-uplift baseline recorded in `docs/framework-uplift/`** — record the baseline *before* 4.2.
   ⚠️ *Baseline is not zero on Windows:* measured 2026-08-03 on this box, **6 failed / 129 passed**,
   5 of them `ValueError: preexec_fn is not supported on Windows platforms` (the Module Studio
   subprocess runner) plus `test_chat_fold_trajectory.py::test_cancelled_run_still_persists_partial_turn`.
   Take the baseline on the **same** machine you will re-measure on.
2. the Phase-4 unit block below (§6.4v) is green;
3. the control-plane build is clean (`npm run build` in `workbench/control_plane`);
4. `uv.lock` is committed and clean — deploy `git reset --hard`s and `uv sync`s, so a dirty lock
   breaks prod;
5. 🔒 **OWNER-GATE — the manual soak.** A human drives the Copilot streaming path (a streamed run,
   an `ask_questions` park-and-resume, a steer mid-run) and signs off. **An agent cannot perform,
   simulate or self-certify this**, and must not mark 4.6 done without a recorded human sign-off.
   An agent that reaches this point stops and reports.

#### §6.4v — Phase-4 verification block (run these; never `tests/unit/` as a directory)

> ⚠️ **Never run `tests/unit/` as a directory on a dev box.** It hangs — this box's `.env` has Mem0
> enabled and `test_memory_integration.py` blocks on the live DB. Name files. Also never run
> `test_owner_bootstrap.py`, `test_memory_integration.py`, `test_memory_e2e.py`,
> `test_run_agent_stream_e2e.py`, `test_debug_routes.py` locally. CI runs the directory; you do not.

```bash
# Shim pins — must be green BEFORE the uplift (baseline) and AFTER each 4.5 deletion.
uv run python -m pytest \
  tests/unit/test_executor_telemetry_killswitch.py \
  tests/unit/test_core_tool_floor.py \
  tests/unit/test_tool_scope_addendum.py \
  tests/unit/test_own_tool_scope.py -q
# measured 2026-08-03, pre-uplift baseline: 21 passed in 74.40s

# Permission gate + HITL/steer contracts (4.3 / 4.4 / 4.5.1 / 4.5.3 / 4.5.4).
uv run python -m pytest \
  tests/unit/test_permission_policy.py \
  tests/unit/test_hitl_both_runtimes.py \
  tests/unit/test_ask_user_hitl.py \
  tests/unit/test_steer_routing.py -q
# measured 2026-08-03, pre-uplift baseline: 81 passed in 74.92s

# Offline trajectory evals (4.6 gate 1) — compare to the recorded baseline, not to zero.
uv run python -m pytest evals/trajectories/ -q
# measured 2026-08-03 on Windows: 6 failed, 129 passed (see 4.6 note 1)

# Lock hygiene (4.1 must leave this empty; 4.2 must leave it empty after committing).
git status --short uv.lock

uv run ruff check .
```

### ~~Phase 5 — Pre-built orchestrations + collaborative chat~~ — ✅ STRUCK 2026-08-03 (shipped / reassigned)

> **No item in Phase 5 is WS-12 work.** 5.1 moved to WS-11, 5.2 shipped as multiplayer rooms,
> 5.3 was always "skip". Nothing here is dispatchable from this document.

- **5.1 — ➡️ REASSIGNED to WS-11 / [`workflows_app.md`](workflows_app.md) §8.** *(Expose
  Magentic/GroupChat as **node types inside a graph**, not a parallel top-level architecture.)*
  It belongs there because the graph, its node catalog and its compiler are all owned by the
  Workflows app under D6 — adding a node type to someone else's engine from this document is exactly
  the parallel seam D6 exists to prevent. **It is the only remaining consumer of
  `agent-framework-orchestrations`, and therefore sequences AFTER Phase 4** (the package needs
  core ≥1.9; we are on 1.8.1 and the package is absent from `uv.lock`). No acceptance is written for
  it here — WS-11 writes it, against its own node-catalog contract.
- ~~**5.2 Collaborative chat surface (Shape C).**~~ **✅ SHIPPED as multiplayer rooms — and shipped
  the way this spec recommended.** The §5.6 "cheapest-first" guidance was to start with a rule-based
  `selection_func` rather than an LLM coordinator; what shipped is exactly that, expressed natively:
  `RoomAgent.role: "primary" | "mentioned"` (`workbench/control_plane/src/lib/rooms.ts:31-37` —
  *"`primary` answers an unaddressed turn; `mentioned` answers when @named"*), the turn discipline as
  `floorMode: "open" | "driver"` (`rooms.ts:85`), and the routing decision as
  `orchestrator/steer.py::route_turn` (`:123`, DROP/ENGAGE/ABORT/STEER).
  **It was built without the orchestrations package** — absent from `uv.lock`, verified 2026-08-03 —
  so Phase 5.2 never depended on Phase 4 after all. Residue (the floor-control re-decision) is
  **WS-10's and 🔒 OWNER-GATE**, registered in `work_plan.md` §6 by name. Do not re-open it here.
- ~~**5.3** `HandoffBuilder`: MAF-only.~~ **Unchanged and still "skip"** — not work, a standing
  decision (§5.1: handoff is the wrong pattern for our case). **Do not** rewrite Copilot agents for it.

---

## 7. Risks & open questions

| Risk | Severity | Mitigation |
|---|---|---|
| ~~Two forced SDK majors (openai 1.99→2.x, copilot-sdk 0.1.32→1.0.2)~~ | ~~**high**~~ **RETIRED 2026-08-03** | **Half of this risk expired on its own.** `uv.lock` + the repo `.venv` both carry `openai 2.38.0` under an unchanged `agent-framework-openai 1.7.0` — the openai major landed independently of Phase 4. Successor row below. |
| **One** forced SDK major (github-copilot-sdk 0.1.32→1.0.2) | **high** | 6/6 in-repo agents ride the Copilot path. Phase 4.1 isolated-venv proof + 4.3's API-shape diff; the minimal-bump fallback dodges it entirely (🔒 owner picks, 4.0) |
| Breaking AG-UI interrupt/resume (#6925) vs our custom HITL resume | **high** | Phase 4.4 deliberate migration; the schedule risk lives here. Its anchors were wrong until 2026-08-03 (`ask_tools.py` does not exist) — re-derive before editing |
| Shim removal deletes a workaround the fix doesn't fully cover | med | 4.5 verifies each fix against our case *before* deleting; one at a time, each with its own named test |
| 4.5.4 regresses shipped steer (WS-10) | med | `_nq` is load-bearing for a **shipped** feature (`15c8933f`); 4.5.4 is a behaviour-preserving swap — any weakened assertion in `test_steer_routing.py` is a red flag |
| 4.1 mutates the repo `.venv` / `uv.lock` while "just proving resolution" | med | the hazard note at the head of Phase 4; `git status --short uv.lock` must be empty when 4.1 ends |
| Bumping against §3.4's stale PyPI snapshot | med | §3.4 and §4.0 are marked 2026-07-18 snapshots; 4.1 must re-resolve from PyPI and record what it got |
| ~~Floor-wide `call_agent` widens reach~~ | low | **Shipped 2026-07-22 (Phase 0.1)** and the mitigation held: per-target confirm gate + depth/cycle guards |
| ~~N live agents exhaust 4 GB~~ · ~~Graph spec churn after editor ships~~ | — | **Moot** — both belonged to Phases 2–3, superseded by the Workflows app (D6) |
| `HandoffBuilder` never supports Copilot | low | we don't need handoff semantics (§5.1) |

**Open questions**
1. Should L2 workflows be **user-authored only**, or may an agent invoke a saved workflow via a tool?
   *(Answered — see the banner at the top: F13 workflows-as-tools shipped.)*
2. Where do workflow definitions live — Postgres (survives `git reset --hard`) or repo files?
   Precedent says **Postgres**. *(Answered — see the banner at the top: Postgres, migration 132.)*
3. ~~Does a node need HITL? `AgentExecutor` supports `request_info`; confirm it survives our SSE relay.~~
   *(Moot here — Phases 2–3 superseded. Workflow-node HITL shipped as the Action Broker inbox
   pause/resume in `workflows_app.md`. The HITL-vs-SSE-relay question that **is** still live is
   Phase 4.4's, and it is scoped there.)*
4. ~~Do we cap live nodes per workflow (`metorite-dev` alone is a 2.1 GB clone)?~~
   *(Moot here — belongs to `workflows_app.md` under D6.)*

**No open questions remain in this document.** Every decision Phase 4 needs is either recorded above
or explicitly marked 🔒 OWNER-GATE (the 4.0 target choice, 4.5.1's killswitch flip, 4.6's manual soak).
If an agent finds itself needing a decision that is not one of those three, the spec has drifted —
stop and report rather than choosing.

---

## 8. Appendix — reproduction

> ⚠️ **§8 is NOT agent-runnable and is NOT the verification block.** Every command below requires
> prod SSH (`ssh acb@…`) — an agent has no such reach and must not attempt it, and probes A/B were
> run against a **2026-07-17 prod state that has since changed** (Phase 0 landed; the floor now
> contains `call_agent`, so probe A would now print `True`). Kept as the incident record.
> **The commands you actually run are §6.4v** (Phase-4 verification block) and §8.1 below.

### 8.1 Local, agent-runnable equivalents

```bash
# Shim + version facts this document depends on — all offline, all local.
grep -n "^_CORE_STANDARD_TOOL_NAMES" -A 25 apps/services/orchestrator/orchestrator/_tool_injection.py
grep -n "def resolve_relay_thread_id\|^_pending_user_input" apps/services/orchestrator/orchestrator/executor.py
grep -n "ENABLE_INSTRUMENTATION" apps/services/orchestrator/orchestrator/executor.py
grep -n "COPILOT_INFINITE_SESSIONS" apps/services/orchestrator/orchestrator/_copilot_session.py
grep -n "def _gate_injected_tool" apps/services/orchestrator/orchestrator/_tool_injection.py
grep -n "_nq: asyncio.Queue" apps/services/orchestrator/orchestrator/executor.py

# Installed versions — the §3.4 / §4.0 "Installed" column. Read-only.
uv pip list | grep -E "agent-framework|^openai |github-copilot-sdk"
grep -c "agent-framework-orchestrations" uv.lock     # 0 today — the package is absent

# Shape C's shipped selector (Phase 5.2).
sed -n '31,37p;85p' workbench/control_plane/src/lib/rooms.ts
grep -n "def route_turn" apps/services/orchestrator/orchestrator/steer.py
```

### 8.2 Original prod probes (2026-07-17 · prod SSH · **not agent-runnable**)

```bash
ssh acb@187.127.179.143

# A. planner's effective toolset — proves call_agent is stripped
cd /opt/acb/app && .venv/bin/python - <<'PY'
from orchestrator._tool_injection import _resolve_injected_scope, _build_injected_tools_addendum
scope = ["web_search","fetch_page","write_artifact","manage_todo_list",
         "ask_user","save_note","recall_notes"]     # technical-project-planner's real scope
eff = _resolve_injected_scope(scope)
print("call_agent injected?", "call_agent" in eff)                       # False
ad = _build_injected_tools_addendum()
print("addendum mentions call_agent?", "call_agent(" in ad)              # True  ← the lie
print("addendum tokens ~", len(ad)//4)                                   # ~7827
PY

# B. mixed-runtime workflow on core 1.8.1 — no new deps
cd /opt/acb/app && .venv/bin/python - <<'PY'
from agent_framework import Agent, AgentExecutor, WorkflowBuilder
from agent_framework.openai import OpenAIChatCompletionClient
import agent_framework_github_copilot as gc
c   = OpenAIChatCompletionClient(model="gpt-4o-mini", api_key="sk-probe")
a   = AgentExecutor(Agent(c, "p", name="maf"), id="maf-node")
b   = AgentExecutor(gc.GitHubCopilotAgent(name="cop", instructions="p"), id="copilot-node")
wf  = WorkflowBuilder(start_executor=a).add_edge(a, b).build()
print("MIXED-RUNTIME WORKFLOW OK ->", type(wf).__name__)
PY

# C. orchestrations matrix (ISOLATED venv — never touch /opt/acb/app/.venv)
rm -rf /tmp/orchprobe && mkdir -p /tmp/orchprobe && cd /tmp/orchprobe
uv venv -q .venv && uv pip install -q --python .venv/bin/python \
    agent-framework-orchestrations agent-framework-github-copilot agent-framework-openai
# → pulls core 1.11.0; HandoffBuilder rejects GitHubCopilotAgent,
#   Sequential/Concurrent/GroupChat/Magentic accept mixed runtimes
rm -rf /tmp/orchprobe        # ALWAYS clean up
```

---

## 9. References

- [MAF — Workflow orchestrations](https://learn.microsoft.com/en-us/agent-framework/workflows/orchestrations/)
- [MAF — Handoff](https://learn.microsoft.com/en-us/agent-framework/workflows/orchestrations/handoff) *(the `Agent`-only restriction)*
- [MAF — Magentic](https://learn.microsoft.com/en-us/agent-framework/workflows/orchestrations/magentic)
- [MAF — Orchestration patterns reach 1.0](https://devblogs.microsoft.com/agent-framework/agent-frameworks-orchestration-patterns-reach-1-0/)
- [OpenAI — Agent orchestration: handoffs vs agents-as-tools](https://openai.github.io/openai-agents-python/multi_agent/)
- [Anthropic — How we built our multi-agent research system](https://www.anthropic.com/engineering/multi-agent-research-system)
- [Anthropic — Multi-agent coordination patterns](https://claude.com/blog/multi-agent-coordination-patterns)

Related specs: [`agent_file_and_memory_framework.md`](agent_file_and_memory_framework.md) ·
[`core_module_map.md`](core_module_map.md) · [`harness_hardening_2026-07.md`](harness_hardening_2026-07.md)

**Where this document's struck phases went** (added 2026-08-03 — follow these, not the struck text):

| Struck here | Now owned by |
|---|---|
| Phase 1 (context discipline / prompt budget) | **WS-23** — [`skills_registry.md`](skills_registry.md) · [`skills_scope_out.md`](skills_scope_out.md) |
| Phases 2–3 + §5.3 (graph, compiler, runner, editor) | **`workflows_app.md`** per D6 |
| Phase 5.1 (Magentic/GroupChat as graph node types) | **WS-11** — [`workflows_app.md`](workflows_app.md) §8; sequences after Phase 4 |
| Phase 5.2 (Shape C collaborative chat) | **WS-10** — shipped as multiplayer rooms (`docs/multiplayer/README.md`); floor-control residue is 🔒 OWNER-GATE |
| **Phase 4 (framework uplift)** | **stays here — WS-12** |

## Board record (2026-08-09) — moved from work_plan.md §2

> Moved here in the 2026-08-09 consolidation (work_plan.md D18): board rows now
> carry state + gates only. The narrative below is preserved verbatim from the
> final long-form row; the dated corrections after it win where they conflict.

### WS-12 — **Framework uplift**
**State cell (as of the move):** 🟡 Ph4
**Narrative (verbatim):** **Audited NO-GO on all seven contract points; shrunk to Phase 4 only on 2026-08-03, not closed.** Ph0 shipped. **Ph1 struck** — 1.1 shipped as *progressive disclosure* (`93b93a08`, #191); 1.2 moot (`technical-project-planner` exists in neither `_AGENT_REGISTRY` nor `apps/agents/`); 1.3 delivered by **WS-23**. Ph2–3 superseded by the shipped Workflows app (D6). **Ph5 struck** — 5.2 shipped as multiplayer rooms *without* the orchestrations package, so it never depended on Phase 4; **5.1 is reassigned to WS-11**. **Ph4 is the genuinely undone part** — all four §5.5 shims re-verified in-tree 2026-08-03. **Drift correction: Phase 4 drags ONE SDK major, not two** — `uv.lock` and the repo `.venv` both carry `openai 2.38.0`, so the billed `openai 1.99 → 2.x` major already landed independently; only `github-copilot-sdk 0.1.32 → 1.0.2` remains. **0 PRs dispatchable today:** 4.0's target choice (minimal- vs full-bump) is **OWNER-GATE**; 4.1 (resolution proof in an isolated throwaway venv, evidence-only, AGENT-SAFE — it must never mutate `<repo>/.venv` or `uv.lock`) is what unblocks it.

**Corrections applied 2026-08-09:** current as moved.
