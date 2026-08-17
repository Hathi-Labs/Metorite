# Runtime-Agent Effectiveness — Applying the Dev-Kit Lessons Inward

> **Status:** Proposed · **Created:** 2026-07-04
> Motivation: building the portable `coding-agent-setup` kit (CodeGraph, RTK,
> review/verify, project-instructions) forced us to articulate what makes a
> codebase *agent-developable*. That articulation exposed an asymmetry: those
> four capabilities are given to EXTERNAL agents building Metorite, but
> Metorite's OWN runtime agents (chat, email, orchestrator, and the
> self-mutation loop) mostly lack them. This spec applies the kit's lessons
> inward.
> Source practices: [awesome-harness-engineering](https://github.com/ai-boost/awesome-harness-engineering)
> (§ Tool Design, § Context Delivery & Compaction, § Skills & MCP) plus the
> kit's own `PLAYBOOK.md` (Principles 1-2) and `HARNESS-PRACTICES.md` (§4-§7).
> Companions: [`dev_velocity_tooling_2026-07.md`](dev_velocity_tooling_2026-07.md)
> (build-time CodeGraph, L2), [`context_assembly_c2.md`](context_assembly_c2.md)
> (the assembler this spec extends), [`llm_caching_memory.md`](llm_caching_memory.md)
> (C4 caching, referenced by Item ⑤), [`harness_hardening_2026-07.md`](harness_hardening_2026-07.md)
> (the trajectory-eval discipline every item here must satisfy).

## Verdict

Metorite gives external agents a symbol graph, output compression, and
review discipline — then runs its own agents with grep/read and full-size tool
output. Closing that asymmetry is mostly *adoption of mechanisms that already
exist*, not new infrastructure. Ranked by ROI:

| Item | What | Current state | Effort |
|---|---|---|---|
| **①** | Tool-scope the 3 unscoped agents (`tool_scope`/`own_tool_scope`) | Mechanism shipped, adopted by 1 of 4 agents | **XS** (config + 1 eval) |
| **②** | RTK-style compression of runtime shell/tool output before it re-enters context | Partial (sub-agent + old-history only; live shell output uncompressed) | **S** (one translator seam) |
| **③** | CodeGraph (symbol nav) for the self-mutation sandbox | Missing (grep/read only; no MCP in container) | **M** (Docker + index + MCP wire) |
| **④** | Structure-aware compression for one oversized turn in `assemble_run_context` | Missing (blind head+tail char-trim) | **S-M** (one seam, fallback preserved) |
| **⑤** | Realize dormant prompt caching by defaulting to a caching-capable tier | Infra shipped, no-op on DeepSeek default | **Decision, not code** |

Binding rule (root `AGENTS.md` § Harness Engineering Practices, rule 3): every
item that changes harness behavior ships with a golden trajectory eval in
`evals/trajectories/`. Eval plans are inline per item below.

---

## Item ① — Tool-scope the three unscoped agents  *(XS, do first)*

**Problem.** `tool_scope` (filters the ~24 injected platform tools) and
`own_tool_scope` (filters an agent's repo-baked tools) both exist and are tested,
but only `agent-task-manager` declares `tool_scope`, and NO agent declares
`own_tool_scope`. Consequently:
- `agent-email-assistant` receives the full 24-tool platform catalog **on top of
  its ~60 baked tools** (~84 tools) — the exact over-injection the Berkeley
  Function-Calling Leaderboard degradation warns against (cited in-code at
  `executor.py:1029-1031`).
- `agent-apis-config` and `agent-orchestrator` likewise get all 24 platform tools.

**Mechanism (already built, do not re-implement):**
- `_inject_agent_tools()` platform-tool filter — `executor.py:1028-1044` (match by
  `fn.__name__`; unmatched ⇒ fail-open to all + warn).
- `_apply_own_tool_scope()` baked-tool filter — `executor.py:869-899` (runs
  BEFORE injection; filters `agent.tools`/`_tools` in place; fail-open + warn).
- The 24 injectable platform tool names (valid `tool_scope` entries):
  `call_agent, call_agents_parallel, call_agent_background, web_search,
  fetch_page, write_artifact, share_artifact, emit_generative_ui, remember,
  recall_timeline, save_memory, save_episode, manage_todo_list, ask_questions,
  get_errors, run_diagnostics, install_dependency, save_note, recall_notes,
  query_history, github_search, github_repo_search`.

**Change.** Add a `tool_scope` array to each of the three configs, choosing the
subset each agent actually needs (mirroring `agent-task-manager/config.json`).
For `agent-email-assistant`, also add `own_tool_scope` naming the email tools it
truly uses, to trim the ~60-tool baked surface.

Proposed starting scopes (refine against each agent's real usage before merge):
- **orchestrator** — routing + memory + delegation: `call_agent,
  call_agents_parallel, call_agent_background, ask_questions, query_history,
  recall_timeline, remember, emit_generative_ui`.
- **apis-config** — setup/admin + web lookup: `ask_questions, web_search,
  fetch_page, install_dependency, save_note, recall_notes, emit_generative_ui`.
- **email-assistant** — `tool_scope`: `ask_questions, remember, recall_timeline,
  save_memory, emit_generative_ui, call_agent`; plus an `own_tool_scope` naming
  its core email actions (draft/send/label/search) — enumerate from the agent's
  baked tool list.

**Eval.** Extend the existing static check
(`test_gtd_quality_trajectory.py::test_tool_scope_is_declared_and_lean`) into a
behavioral trajectory: assert that an agent with a `tool_scope` of N names is
injected exactly those N platform tools (and that `own_tool_scope` filters the
baked list), plus the fail-open-on-no-match path. This closes the item-⑥ gap
that tool filtering has no behavioral eval.

**Risk.** Low/reversible. Fail-open means a wrong name degrades to "all tools"
(today's behavior), never to "no tools." Verify each agent still completes its
core flow after scoping (per `PLAYBOOK.md` Principle 4 — drive the real path).

---

## Item ② — Compress runtime shell/tool output before it re-enters context  *(S)*

**Problem.** This is `PLAYBOOK.md` Principle 2 (compress tool *output*) applied to
CC's own agents. Today:
- ✅ Sub-agent delegation results are head-trimmed to `_MAX_SUB_RESULT_CHARS`
  (default 8000) — `executor.py:1515-1517, 1620-1636`.
- ✅ Old-history tool-result JSON is stripped from non-recent turns —
  `_TOOL_RESULT_RE`, `executor.py:321, 4872`.
- ❌ The **live built-in shell tool output** of a Copilot-SDK runtime agent
  (a `pytest`/`git log`/`build` dump) re-enters context **at full size**. The
  `result_str[:2000]` slices at `executor.py:3955, 3997` truncate only the UI
  SSE frame; `return result` hands the full object back to the model. The
  built-in shell tool doesn't even flow through that shim.

**The seam (Metorite-owned, no SDK fork needed).** The SDK's built-in shell
emits `TOOL_EXECUTION_COMPLETE`, translated to a MAF `AgentResponseUpdate` in
`MetoriteCopilotAgent._on_event` at `copilot_agent.py:419-441`. Line 436
reads the full stdout as `result=result_text or ""`. That argument is the exact
insertion point. The parallel custom-tool path is `EXTERNAL_TOOL_COMPLETED` at
`copilot_agent.py:465-490` (line 485). There is no PostToolUse hook — the SDK's
only hook is the pre-execution permission gate — so this translator seam is the
correct and only clean CC-side interception.

**Change.** Add `_compress_tool_output(tool_name: str, text: str) -> str` (new
helper, likely in `acb_llm` alongside the context fitter so it's reusable) and
call it at `copilot_agent.py:436` and `:485`:
```python
result=_compress_tool_output(getattr(d, "tool_name", ""), result_text or ""),
```
Compression strategy (deterministic, RTK-style — NOT an LLM call in the hot path):
- Only compress when `len(text)` exceeds a threshold (env
  `RUNTIME_TOOL_OUTPUT_MAX_CHARS`, default ~4000) — small outputs pass untouched.
- Head+tail keep with a marker (reuse the sub-agent trimmer's newline-boundary
  logic), PLUS collapse runs of identical lines to `[… ×N …]` and, for
  recognized test output, prefer failure lines. Keep it a pure function.
- Tee-nothing needed here (unlike RTK's disk tee) because the full output already
  exists in the run trace / logs; note in the marker that full output is in the
  run log.

**Eval.** New `test_runtime_output_compression_trajectory.py`: feed a synthetic
`TOOL_EXECUTION_COMPLETE` with a 50k-char shell dump through the translator,
assert the re-entering `AgentResponseUpdate` content is bounded and preserves the
failure signal (e.g. an injected `FAILED test_x` line survives), and that a small
output is passed through byte-identical.

**Risk.** Medium — this changes what the model sees. Mitigations: threshold-gated
(small outputs untouched), failure-line-preserving, env-tunable, and the full
output remains in the run trace. Guard against compressing structured tool
results that the agent parses programmatically (gate on `tool_name` — compress
shell/test/build output, not JSON-returning custom tools).

---

## Item ③ — CodeGraph for the self-mutation sandbox  *(M)*

**Problem.** `PLAYBOOK.md` Principle 1 (symbol graph over grep) applied to CC's
highest-stakes runtime behavior — the self-mutation loop. The sandbox agent
navigates the mounted repo (`/workspace/repo`) with the SDK's built-in
file/grep/shell tools only; the runtime-fix prompt literally says "Use grep/view
tools" (`mutation.py:458`). `SessionConfig` in `mutation_runner.py:53-82` wires
NO `mcp_servers` and no tools. `Dockerfile.mutation` installs only
`git + ca-certificates + github-copilot-sdk` — no Node, no `codegraph`, no index.

**Change (three parts):**
1. **Image** — `apps/orchestrator/Dockerfile.mutation`: add Node.js and
   `npm i -g @colbymchenry/codegraph` (mirror `coding-agent-setup/install.sh`).
2. **Index** — build at container start: run `codegraph init /workspace/repo`
   before the agent session (fast for a single agent repo), OR mount a prebuilt
   `.codegraph` from the host clone. Prefer build-at-start for freshness; the
   repo is small per-agent.
3. **Wire the MCP server** — add to `session_config_kwargs` in
   `mutation_runner.py:54` before `SessionConfig(**session_config_kwargs)` at :81:
   ```python
   session_config_kwargs["mcp_servers"] = {
       "codegraph": {"type": "stdio", "command": "codegraph",
                     "args": ["serve", "--mcp"]}
   }
   ```
   (Shape mirrors `.mcp.json` / `copilot_agent.py:127-129`.)
4. **Prompt** — update `MUTATION_PROMPT` / the runtime-fix prompt (`mutation.py`)
   to say "prefer the codegraph tools for navigation over grep" (Principle 1's
   discipline: install AND instruct-to-use, or it's ignored).

**Eval.** `test_mutation_selfanneal_trajectory.py` — currently the self-anneal
loop has NO trajectory eval at all (item ⑥'s biggest gap). Add one that exercises
`attempt_self_mutation`/`_run_mutation_sandbox` end-to-end against a fixture repo
with a known bug, asserting the loop converges. Wire the codegraph availability
assertion into it. This is worth doing for the loop's own sake regardless of ③.

**Risk.** Medium — container build weight (Node adds ~150MB), index build time at
start. Mitigate: only add codegraph if the repo is above a size threshold;
fall back cleanly to grep/read if `codegraph init` fails (the agent already knows
how). Nothing here changes correctness of the mutation itself, only navigation
efficiency.

---

## Item ④ — Structure-aware compression for one oversized turn  *(S-M)*

**Problem.** The one case earlier analysis explicitly reserved for semantic
compression. `assemble_run_context()` first drops whole oldest turns
(`context.py:348-364`), then hands the residue to `fit_messages_to_context()`
(`context.py:366`), which handles a single genuinely-huge message
(`context.py:126-180`) by **blind head+tail character slicing** at
`context.py:168-170`:
```python
out[idx]["content"] = content[:head] + _TRUNCATION_MARKER + (content[-tail:] if tail else "")
```
This cuts through the middle of an email thread, JSON, or a big tool result at an
arbitrary byte offset — mangling structure exactly where structure matters.

**The seam.** `context.py:168-170` — the head/tail slice. Everything the
compressor needs (`content`, target `keep`, `head`, `tail`, `_TRUNCATION_MARKER`)
is already in scope. Replace with:
```python
out[idx]["content"] = compress_message_content(content, target_chars=keep)
```
where `compress_message_content(content, target_chars)`:
- Detects structure (email thread → keep newest message + sender/subject of
  older; JSON → keep shape, elide long values; diff/code → keep hunk headers +
  changed lines; else → today's head+tail slice as the guaranteed fallback).
- Is a pure, deterministic function (NO LLM call in the assembly hot path —
  keeps it cache-friendly and fast; `PLAYBOOK.md` Principle 2 caveat on not
  putting a compressor model in the instruction/context path).
- Falls back to the exact current char-slice on any parse failure, so behavior
  never regresses below today.

Placing it at the per-message seam covers BOTH callers automatically
(`assemble_run_context` and `acompletion_with_fallback`), since both route
through `fit_messages_to_context`.

**Eval.** Extend `test_context_assembly_trajectory.py`: feed an oversized
structured message (a long multi-message email thread), assert the compressed
result stays under budget AND preserves the newest message intact + older-message
metadata (not an arbitrary byte cut). Assert the fallback path triggers on
unparseable content.

**Risk.** Low-medium — deterministic, fallback-preserving, off the hot path for
normal-size turns (only fires when a single message overflows after whole-turn
dropping). Scope creep risk: keep the structure detectors few and well-tested;
don't build a general summarizer.

---

## Item ⑤ — Realize the dormant prompt cache  *(decision, not code)*

**Not an engineering task — a tier decision with cost implications.** C4 caching
(`prompt_cache.py:150`, wired at `client.py:530,600` and `v1_compat.py:189`) is
fully shipped but a **no-op on the default `tier-balanced` → DeepSeek** (it only
emits `cache_control` for Anthropic and `prompt_cache_key` for OpenAI;
`client.py:36-48`, default `copilot_chat_model="tier-balanced"` at
`settings.py:139`). The ~90% stable-prefix win is purely latent.

Realizing it means defaulting to a caching-capable tier (Anthropic/OpenAI) — a
tradeoff between per-token cost (DeepSeek is cheaper per token) and the
cache-discounted cost of a caching provider on CC's very stable system prefix +
tool array. This should be **measured, not assumed**: estimate cache-hit rate on
real traffic (the C4 telemetry from `harness_hardening` HH-3 records
`cache_read_input_tokens`) and compare effective cost per run. Surfaced here for
completeness; recommend a separate cost analysis before any default flip.

---

## Sequencing

1. **① first** — XS, reversible, immediately improves tool selection on every
   agent. Ship with its behavioral eval (also closes an item-⑥ eval gap).
2. **④** — self-contained, one seam, fallback-preserving; improves context
   fidelity for the email-heavy workloads.
3. **②** — one translator seam; meaningful token savings on the mutation/coding
   agents' test/build loops.
4. **③** — biggest lift (Docker + index + MCP); do alongside adding the
   self-anneal trajectory eval, which is overdue regardless.
5. **⑤** — a costing decision, not code; take to a separate analysis.

Every code item lands with its trajectory eval in the same PR (root `AGENTS.md`
rule 3). None of items ①-④ requires new infrastructure — they are adoption and
extension of mechanisms that already exist.
