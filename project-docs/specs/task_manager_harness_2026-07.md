# Task Manager × Harness Engineering (2026-07-03)

> ⚠️ **HISTORICAL RECORD (2026-08-10 consolidation, D26).** No work dispatches from
> this document. The active plan is `project-docs/work_plan.md` §2;
> the classification of record is `project-docs/INDEX.md`.


> **Status:** Tier 1 shipped 2026-07-03 · Tier 2 planned, no board row (WS-18 owns task-manager work) · not verified against code since 2026-07-03. *(Header added 2026-08-09.)*

> **What this is.** The task-manager app reviewed against the practice areas in
> [awesome-harness-engineering](https://github.com/ai-boost/awesome-harness-engineering),
> as a companion to the platform-level [`core_module_map.md`](core_module_map.md)
> (which scoped apps OUT). The platform core (streaming, HITL, loop/stall
> detection, caching, memory, cross-worker control) is at/above the list's
> documented best practice and the tasks rail **inherits all of it** by being a
> thin wrapper over the shared AgentChat + MAF executor — so this review is
> about the APP layer only: the GTD tools, the clarify seam, sync, and the
> agent's authorization/eval posture.
> Siblings: [`task_manager_app.md`](task_manager_app.md) (product spec + runbook) ·
> [`harness_hardening_2026-07.md`](harness_hardening_2026-07.md) (platform HH queue).

## Tier 1 — ✅ shipped 2026-07-03 (branch `claude/task-manager-project-plan-ds2n5j`)

| # | List practice | What shipped |
|---|---|---|
| T1-1 | **Tool annotation hints** (`readOnlyHint`/`destructiveHint`/…) | All 13 task-manager tools registered in `acb_skills.tool_annotations` via the guarded `@_annotate_risk` decorator (email-assistant recipe): `gtd_list/list_projects/accounts/people/inbox_insights/clarify` read-only · `gtd_capture(_many)/organize/update` reversible writes · `gtd_sync` write+open-world (the only network-egress tool) · `get_task_status`/`list_project_tasks` read-only+open-world. **Invariant: NO destructive tool in the GTD set** — C-04 (push is a human UI action) is now machine-checkable and eval-locked. |
| T1-2 | **Lethal-trifecta detection** (private data + untrusted content + external reach) | The trifecta is real here: `people` serves HR data; SYNCED task text is authored by *other people* in the PM tool; `call_agent` reaches email. Mitigations: `_fmt_item` marks every row `LOCAL`/`SYNCED` and quotes titles; `gtd_list` prepends a treat-as-data notice whenever SYNCED rows are present; the rail persona quotes the open item's title explicitly as DATA; and the tool scope (T1-4) removes background/parallel delegation fan-out. |
| T1-3 | **Domain eval harness** ("skill bundles with built-in evals"; agent-quality evals = the E1 gap) | `evals/trajectories/test_gtd_quality_trajectory.py` — golden clarify set (one case per GTD decision branch + delegation naming + capability-match-is-a-suggestion + project auto-match + P7 stage mapping), sync GTD-lens golden set, annotation-completeness + no-destructive invariant, trifecta delimiting, tool-scope leanness. Runs in the same CI-blocking trajectory job as the platform evals (HH-1). |
| T1-4 | **Semantic tool exposure / smaller tool sets** (HH-5) | `agent-task-manager/config.json` now declares `tool_scope` (first agent to adopt the HH-5 mechanism): keeps `call_agent` (single delegation channel for F6 follow-ups), `ask_questions` (HITL), memory quartet, todos, notes, `emit_generative_ui`; drops web_search/fetch_page, install_dependency, get_errors, artifacts, and parallel/background delegation. |

**Bugs the new evals caught immediately** (both fixed, both locked):
- `_has()` hint matching was bare substring — “profile the stepper driver”
  contains “file” → misfiled as REFERENCE. Now word-boundary matching in
  `ai.py` AND the frontend mirror `clarify.ts`.
- A PROJECT-hinted capture never auto-filed under an *existing* matching
  project — it always proposed a duplicate new project. Now: strong match to
  an ACTIVE project ⇒ NEXT action filed under it.
  *(Server-side only; the frontend fallback keeps the old shape and the server
  proposal upgrade corrects it in place — intentional, evals lock the server.)*

## Tier 2 — planned (structural)

- **State-machine guardrails for processing** (list: "state machine
  guardrails", "governance without prompt-level trust"): enforce FIFO /
  one-at-a-time in `gtd_organize` itself (structured refusal naming the
  correct head item) instead of relying on instructions.md prose.
- **Weekly review as a persistent planning artifact** (F5): the review writes
  a consolidated cross-session document the agent reads next session —
  hibernate-and-wake applied to GTD itself.
- **Structured error surfaces** audit: every gtd_* failure returns the valid
  options inline (unknown project → project list) for one-turn recovery.

## Explicitly not adopted

AST/symbol indexing, virtual filesystems, meta-harness generators (wrong shape
for a CRUD-plus-judgment app); any app-side rebuild of streaming/HITL/memory/
caching (already inherited from the platform core — rebuilding would regress).

## Status log

- 2026-07-03 — Review done; Tier 1 shipped (annotations, trifecta delimiting,
  GTD golden evals + 2 heuristic bugs found-and-fixed, tool_scope, this doc).
- 2026-07-03 — **Atomizer + capture dedup** shipped with the harness posture
  baked in: LLM (tier1, temperature 0, strict-JSON contract) proposes the
  split + same/maybe/different judgment; the deterministic splitter +
  token-similarity path is both the fallback AND the guardrail (an LLM
  "duplicate" verdict without lexical support degrades to "similar" — the
  human decides; a poisoned existing title can't silently discard a capture).
  Golden-locked in the GTD eval suite; `exclude_ids` prevents the
  capture-then-check self-match.
