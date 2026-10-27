# Skills scope-out — general vs specialised families per agent (WS-23 S3)

**Status:** Proposal for owner review, built 2026-08-01 · **Owner:** vjvarada ·
verified against code 2026-08-01 (static analysis only — **no prod DB / usage
data consulted**, per the S3 dispatch constraints)
**Update 2026-08-01 — the core-floor diet is BUILT (§7).** Half A (skills as
an index, bodies on demand) ships **OFF** behind `SKILLS_INDEX_ONLY`
(OWNER-GATE); Half B (schema trim) is live and changes no call contract.
Measured: addendum 5,697 → 570 tokens, core-floor schemas 9,998 → 8,510.
**≤2k is still not reachable** — §7.4 shows why, §7.5 designs the mechanism
that would get there (progressive tool disclosure, deliberately not built).
**Owning workstream:** `work_plan.md` WS-23 · parent spec:
[`skills_registry.md`](skills_registry.md)

## 0. What this answers

The owner's directive: *"extract the common skills from the existing agents,
leaving specialised skills to have the agent scope only… which will be only
for that special agent, what will be a general skill injected into all
agents."* This doc is the evidence-based classification of the
platform-injected skill families into a **GENERAL default profile** (what
every agent gets) and **SPECIALISED families** (per-agent only), plus the
prepared one-switch mechanism. **The fail-open→fail-closed flip itself is
OWNER-GATED and ships OFF** (`work_plan.md` §6); nothing here changes any
agent's injected tool set until the owner flips it.

## 1. Method (three evidence sources per agent)

1. **Declared scope** — `config.json: tool_scope` as the loader reads it
   (`gateway/routes/integrations_skills.py::_declared_tool_scope` convention).
2. **Static references** — which injected platform tools the agent's own
   repo (instructions.md / agents.py / config) actually names.
3. **Repo-baked tools** — `own_tool_scope` surfaces (email-assistant ~60
   email tools, whatsapp-assistant's WhatsApp tools). Already specialised by
   construction and OUTSIDE the injected pool; noted, not classified.

Not consulted: run-time usage counts (prod DB off-limits for this pass) —
rows relying on declared scope alone are marked accordingly.

## 2. The proposed GENERAL default profile

`acb_skills.skill_families.DEFAULT_PROFILE = ("core", "memory", "workflows",
"apps")` — shipped as data, consumed only when `SKILLS_FAIL_CLOSED` is ON.

| Family | Why it is general |
|---|---|
| `core` | The guaranteed floor (rule 2) — automatic, listed for completeness. 19 tools ≈15.4k tokens (addendum + schemas); most of the cost story. |
| `memory` | The only toggleable family with near-universal evidence: **5 of 6** registered agents declare memory tools in `tool_scope`; the orchestrator's and email-assistant's prompts *instruct* calling them. Cross-session continuity is platform behaviour, not a specialty. |
| `workflows` | Scope-independent by the S2 decision note (`skills_registry.md` §2): governed org automations offered to every agent; the trio is appended after scope filtering. *Caveat for owner:* no agent repo references the trio — its generality is a platform decision, not agent evidence. Keeping it in the profile preserves today's decided default. |
| `apps` | Dynamic, governed by `app_grants` (its own admin surface) — profiles never grant or remove app tools; listed so "what does a default agent get" reads complete. |

**Deliberately excluded (SPECIALISED):**

| Family | Who gets it | Why specialised |
|---|---|---|
| `history` (`query_history` — SELECT over chat history) | **orchestrator** only | Only the orchestrator declares it; it is the cross-session "what did we discuss" agent. Raw SQL over every user's chat history is also the most privacy-sensitive injected tool — narrow by default. |
| `coding` extras (`install_dependency`, `github_search`, `github_repo_search`) | **apis-config** only | Only apis-config declares `install_dependency` (it installs client libs while configuring APIs). **Nobody** declares or references the GitHub search pair. Note the family grain: enabling `coding` for apis-config also *permits* the search pair, but its `tool_scope` still narrows to `install_dependency` — tool_scope stays the finer grain inside a family. |

## 3. Per-agent recommendation table

"Declared" = families its `tool_scope` intersects today. Recommended enabled
families exclude the automatic `core`/`apps` pass-throughs for brevity —
every agent keeps those regardless.

| Agent (runtime) | Declared scope → families | Recommended enabled families | Evidence | Confidence |
|---|---|---|---|---|
| **orchestrator** (MAF) | memory (remember, recall_timeline) + history (query_history) | **memory, history, workflows** | instructions.md §"MEMORY TOOLS" teaches remember/recall_timeline/save_memory/save_episode; config declares query_history. ⚠ Drift: prompt teaches `save_memory`/`save_episode` but `tool_scope` omits them — the agent is told to call tools it does not receive (fix the scope or the prompt). | High |
| **email-assistant** (MAF) | memory (remember, recall_timeline, save_memory) | **memory, workflows** | agents.py prompt references remember/save_memory; instructions.md step 5 says "`save_episode` a one-line note" — ⚠ same drift: `save_episode` not in scope. ~60 repo-baked email tools via `own_tool_scope` (outside the pool). | High |
| **whatsapp-assistant** (MAF) | memory (remember, recall_timeline, save_memory) | **memory, workflows** | Declared scope only — repo code/prompts do not reference the memory tools by name. Repo-baked WhatsApp tools via `own_tool_scope`. | Medium (declared, untaught) |
| **task-manager** (Copilot) | memory (remember, recall_timeline, save_memory, save_episode) | **memory, workflows** | Declared scope only; instructions.md's "remember" is prose ("capture/add/note/remember a task"), not the tool. | Medium (declared, untaught) |
| **apis-config** (MAF) | coding (install_dependency) | **coding, workflows** | instructions.md is built around web_search (core); `install_dependency` declared for installing client packages during setup. No memory tools declared or referenced → memory NOT recommended. | Medium (install_dependency declared; GitHub pair unused) |
| **app-builder** (Copilot) | — (core-only scope) | **workflows** (i.e. nothing beyond the profile floor minus memory) | Scope = ask_questions + load_design_system, both core. No non-core references anywhere in the repo. | High |
| **Dynamic (DB-registered) agents** | unknown locally | **keep fail-open until owner reviews each** | The `agent_registry` table was not read (no prod DB contact). These are exactly the agents most likely to lack a `tool_scope`, i.e. the ones the flip changes. | **Low — do not flip until reviewed** |

Two cross-cutting observations for the owner:

- **The flip's blast radius is the dynamic agents.** All six statically
  registered agents declare a `tool_scope`, so `SKILLS_FAIL_CLOSED=1` changes
  **none of them**. It only changes DB-registered agents without a scope —
  which is also why the flip must wait for a roster review against the live
  table (owner action; a read of `agent_registry` + each clone's config).
- **memory-family recommendation vs. today's finer grain.** Recommending the
  `memory` family for an agent does not widen it: `tool_scope` still narrows
  within the family (email-assistant keeps 3 of 8 memory tools). Family
  toggles answer "may this agent use memory at all"; tool_scope answers
  "which memory verbs".

## 4. The prepared mechanism (shipped OFF)

- `DEFAULT_PROFILE` + `default_profile_tools()` in
  `packages/acb_skills/acb_skills/skill_families.py` — data, not behaviour.
- `SKILLS_FAIL_CLOSED` (env, read in
  `orchestrator/_tool_injection.py::_skills_fail_closed`) — **default OFF**.
  When ON, `_resolve_injected_scope(None)` returns
  `core ∪ default_profile_tools()` instead of the fail-open `None` sentinel;
  scoped agents and admin toggles (S2 intersection) are untouched either way.
  Covered both positions in `tests/unit/test_generated_addendum.py`; the S2
  byte-identical no-rows regression continues to pass with the switch absent.
- **Flip checklist (owner):** review §3's dynamic-agent rows against the live
  registry → set `SKILLS_FAIL_CLOSED=1` in the gateway/orchestrator env →
  re-check the Skills catalog matrix (`GET /integrations/skills`: the
  `all_families` flag currently means "no scope AND no disables" and should
  be revisited to reflect the profile when flipped) → watch
  `executor.tool_scope_no_match` / agent behaviour for a supervised window.

## 5. Measured cost (chars/4 run-context tokenizer, 2026-08-01)

Addendum + injected-tool JSON schemas (incl. the workflow trio):

| Profile | Addendum | Schemas | Total | Tools |
|---|---|---|---|---|
| All families (unscoped today) | 5,697 | 13,562 | **19,259** | 34 |
| DEFAULT_PROFILE (core+memory+workflows) | 5,386 | 12,371 | **17,757** | 30 |
| email-assistant, declared scope resolved (today) | 5,386 | 11,134 | **16,520** | 25 |
| email-assistant, recommended (core + full memory family) | 5,386 | 12,371 | **17,757** | 30 |
| Core floor alone | 5,124 | 10,322 | **15,446** | 22 |

**The ≤2k email-assistant target (`skills_registry.md` §4 S3 /
`llm_caching_memory.md` Phase 7) is not reachable by family toggles.** The
instrument now exists and says the floor itself costs ≈15.4k tokens — ≈10.3k
of it the 19 core tools' schemas. Getting under 2k is a *core-floor diet*
(fewer/leaner schemas, progressive disclosure), a different workstream than
toggling optional families, which at best saves ≈3.8k (19.3k → 15.4k).

## 6. Prior art for the core-floor diet — `qm`'s progressive disclosure

§5's closing phrase — *"progressive disclosure"* — names a mechanism that
[`yc-software/qm`](https://github.com/yc-software/qm) has already implemented, read
2026-08-01. Recorded here so whoever picks up the diet knows what "good" looks
like; full write-up in
`multiplayer_prior_art_qm_2026-08.md` §QM-2.

Four parts, each independently adoptable:

1. **Index, not body.** The prompt carries one line per skill — name,
   one-line description, and the path to read for the rest
   (`- **name** — description → read skills/<name>/SKILL.md`). Bodies never
   enter the prompt at all.
2. **Bodies live where the agent can read them.** `SKILL.md` is materialized
   into the scope's sandbox once; full trees (assets, bundles) are laid down
   **lazily**, triggered when a tool call actually touches the skill's
   directory. Both layers are content-hash idempotent with marker files, so
   re-materialization is free.
3. **Connector gating.** A skill for a provider the org has not configured is
   filtered out of the index entirely rather than listed and then failing —
   the same instinct as our S2 intersection, applied to relevance instead of
   permission.
4. **Cache placement.** The index is appended *before* the prompt-cache
   boundary so it stays in the stable prefix — matching our
   [`llm_caching_memory.md`](llm_caching_memory.md) choke-point discipline.

**What it does and does not buy us.** This attacks the **addendum** half of
the floor (≈5.4k of 15.4k) and proves the pattern end to end. It does *not*
touch the **schema** half (≈10.3k across 19 core tools), which is the larger
number and needs its own answer — fewer tools, leaner schemas, or
tool-search-style deferred loading. It is also not a config flip for us: it
assumes the agent can read a body from somewhere, which here means the
`agent-data/` blob store or the workspace path rather than a sandbox
filesystem. Sequence it as the successor to S3, not as part of it.

## 7. The core-floor diet — BUILT 2026-08-01 (index ships OFF; trim is live)

**Status:** both halves built and measured 2026-08-01 · index mode
**SHIPPED OFF** behind `SKILLS_INDEX_ONLY` (**OWNER-GATE**, `work_plan.md`
§6) · the schema trim is **live and unconditional** (it changes no call
contract) · verified with `tests/unit/test_skill_index.py`,
`tests/unit/test_tool_schema_diet.py` and the two trajectory evals.

### 7.1 Half A — index in the prompt, bodies on demand

`packages/acb_skills/acb_skills/skill_index.py`. Our own build of §6's four
parts, on our own primitives:

- **One line per family**, from the registry: label, a new one-sentence
  `summary` field in `SKILL_FAMILIES`, and the literal call that returns the
  rest — `recall_notes("skills/<family>.md")`. A *call*, not a bare path.
- **Bodies live in `agent-data/skills/<family>.md`** — the blob-store-backed
  visible folder `save_note`/`recall_notes` already own, and `recall_notes`
  is in the non-toggleable core floor, so the read path needs **no new tool
  and no new schema**. Writes are content-hash idempotent (an unchanged body
  is not rewritten); bodies are *not* mirrored to the blob store on purpose —
  they are derived data, regenerated every run, and a stale rehydrate must never
  overwrite a fresh body. Materialization happens once per run in the
  executor, **after** the blob-store rehydrate.
- **Byte preservation is structural, not clerical.** The addendum and the
  bodies both come from one new function,
  `acb_skills.addendum.rendered_parts()`, which returns each rendered piece
  paired with its owning family: the addendum is those pieces joined, a body
  is the same pieces filtered to one family. Guidance content is unchanged;
  only its delivery is deferred. Pinned by
  `test_bodies_partition_the_full_addendum_losslessly`.
- **Relevance gating** — a scope-governed family whose tools the agent did
  not receive is absent from the index entirely (our equivalent of qm's
  connector gating; the same instinct as the S2 intersection).
- **Cache placement** — the switch is read inside
  `render_injected_tools_addendum`, so the index lands exactly where the
  addendum did: appended to the system message by `_inject_agent_tools`,
  which runs *before* the executor inserts `acb_llm.prompt_cache.CACHE_BREAK`
  at the memory boundary. The index is therefore inside the **cache-stable
  prefix**, and it is derived only from static registries plus the
  process-cached agent-registry block, so it is byte-stable across turns.
- **One deliberate exception:** the MANDATORY block stays inline. It is the
  one part of the addendum that must be obeyed without being asked for, so
  deferring it behind a read would trade ~90 tokens for a behaviour
  regression.

The drift guarantee moved with the prose rather than weakening: the
trajectory eval now also asserts that with the switch ON, every injected tool
is still documented in *index + bodies*, and that the index itself stays
under 4k characters.

### 7.2 Half B — the schema diet (live)

Trimmed only what a diet may touch: worked examples, prose restating the JSON
shape printed immediately above it, one duplicated alias description, a
stale hard-coded agent list the live registry block already supplies, and a
design-system block whose own text points at `load_design_system("blocks")`.
No parameter was removed, renamed, retyped, or made (non-)required —
`tests/unit/test_tool_schema_diet.py` pins the full call contract of all 34
injectable tools and ratchets each core tool's schema cost.

**Top offenders, before → after (chars/4 tokenizer):**

| Tool | Before | After | What went |
|---|---|---|---|
| `emit_generative_ui` | 3,638 | 2,921 | 4 worked examples → 1; the report-block kit (duplicates `load_design_system("blocks")`); the React sample collapsed to one line |
| `manage_todo_list` | 619 | 552 | field-by-field prose restating the JSON above it |
| `write_artifact` | 587 | 540 | the download-link example |
| `ask_questions` | 586 | 485 | field-by-field prose restating the JSON above it |
| `call_agent` | 457 | 324 | stale hard-coded agent names + 2 examples |
| `call_agents_parallel` | 390 | 280 | example + long "for example" list |
| `get_errors` | 240 | 151 | duplicated the `run_diagnostics` body verbatim |
| `web_search` / `fetch_page` / `save_note` / `call_agent_background` / `run_diagnostics` | 295/285/304/267/270 | 251/245/260/224/217 | worked examples, developer-facing alias commentary |

Core-floor schemas: **9,998 → 8,510 tokens (−1,488, −14.9%)**.
`emit_generative_ui` alone is still 34% of the floor.

### 7.3 Measured, all three cases

| Case | Before (2026-08-01 S3) | Trim only | Trim + index ON |
|---|---|---|---|
| **Full unscoped surface** (34 tools) | 5,697 + 13,562 = **19,259** | 5,697 + 12,074 = **17,771** | 570 + 12,074 = **12,644** |
| **DEFAULT_PROFILE** (30 tools) | 5,386 + 12,371 = **17,757** | 5,386 + 10,883 = **16,269** | 454 + 10,883 = **11,337** |
| **email-assistant, recommended profile** (30 tools) | 5,386 + 12,371 = **17,757** | 5,386 + 10,883 = **16,269** | 454 + 10,883 = **11,337** |
| email-assistant, declared scope today (25) | **16,520** | **15,032** | — |
| Core floor alone (22 incl. workflow trio) | 5,124 + 10,322 = **15,446** | 5,124 + 8,834 = **13,958** | 400 + 8,834 = **9,234** |
| Sub-agent (compact) addendum | 1,614 | 1,614 | **478** |

The **addendum half is solved**: 5,697 → 570 tokens (−90%); the core floor's
addendum 5,124 → 400. The **schema half is not**: it now dominates at 96% of
the remaining floor.

### 7.4 Is ≤2k reachable without progressive tool disclosure? No.

Measured structural floor: the 22 core-floor schemas with **every description
deleted** still cost **1,252 tokens** (name + parameter names/types alone).
Add the index (400) and you are at 1,652 *with no tool descriptions at all* —
which would gut selection accuracy. Give each tool a single honest one-line
description (~25 tokens × 22 = 550) and the same surface lands at ≈2,200.

So ≤2k is arithmetically out of reach while 22 tool schemas are in the array:
**the only remaining lever is putting fewer tools in the array.** Nothing
short of progressive tool disclosure gets there.

### 7.5 Designed, NOT built — progressive tool disclosure (recommendation)

Removing tools from the schema list changes what the model can call directly,
so this is written up rather than shipped.

**Shape.** Keep an *always-on* set of ~6 tools and expose the rest through a
meta-tool, exactly as `load_design_system` / `load_artifact_kit` already do
for documents:

- Always-on (the tools an agent needs to *answer* and to *ask*):
  `emit_generative_ui`, `write_artifact`, `ask_questions`,
  `manage_todo_list`, `recall_notes`, `call_agent`.
- `load_skill_tools(family)` — returns the family's tool schemas and, on the
  runtimes that support it, *appends them to the live session's tool array*
  for the rest of the run. Its own description is the index line we already
  render, so the model knows what it can ask for.
- The index (Half A) is the natural catalog: it already names each family,
  what it is for, and how to reach its guidance.

**Projected cost** at trimmed schema sizes: 400 (index) + `emit_generative_ui`
2,921 + write_artifact 540 + ask_questions 485 + manage_todo_list 552 +
recall_notes 193 + call_agent 324 + the meta-tool ≈150 = **≈5,565** — still
over 2k, because `emit_generative_ui` alone is 2,921. **≤2k therefore needs
BOTH** progressive tool disclosure **and** an `emit_generative_ui` schema that
defers its four modes' shapes to `load_design_system`/`load_artifact_kit`
(a ~250-token schema instead of 2,921). With both: ≈400 + 250 + 540 + 485 +
552 + 193 + 324 + 150 ≈ **2,894**; trimming the always-on set to four tools
lands **≈1,900**.

**Recommendation.** Sequence it as: (1) owner reviews and flips
`SKILLS_INDEX_ONLY` under supervision — it is the change with the best
ratio and it is already reversible; (2) then, if ≤2k is still wanted, take
`emit_generative_ui`'s schema to a pointer, because it is 34% of the floor
and the cheapest single win left; (3) only then build
`load_skill_tools`, which is the one change that alters what the model can
call and therefore needs its own eval pass, not just a flag.

**Risks to carry into that decision.** Both runtimes build the tool array at
session creation (`_prepare_tools` on the Copilot path,
`default_options["tools"]` on native MAF) — mutating it mid-run is
runtime-specific work, not a shared seam, and a per-turn-varying tool array
**busts the Anthropic tools-prefix cache** (`llm_caching_memory.md` §3.3
marks the last tool for caching and requires "same context → same ordered
subset"). Deferred loading must therefore be *monotonic within a run* and
keyed so a given agent+task resolves the same set.
