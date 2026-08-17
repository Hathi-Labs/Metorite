---
name: codebase_health_audit
domain: maintenance
description: Review a flagged file for maintainability, produce a decomposition plan, and (only on explicit approval) refactor it — measuring cyclomatic complexity before and after. Flag-and-propose; never auto-refactor.
when_to_use: "Invoked on a file the weekly codebase-health issue flagged (or any file a developer names) that has high cyclomatic complexity, large LOC, or a low maintainability index. Also when reviewing whether a change keeps the codebase agent-developable."
inputs:
  target_path: "repo-relative path of the file (or a specific function) to audit"
  reason: "why it was flagged — e.g. 'cc 223 in run_agent_stream' or 'file > 800 LOC'"
outputs:
  health_before: "measured cc/LOC/MI for the target (from scripts/codebase_health.py)"
  plan: "a concrete decomposition plan — named extractions, seams, ordering, risk"
  cite: "[file:<path>] and [func:<name>@<line>] references required"
allowed_tools:
  - read_only
authority: suggest
cost_tier: 2
version: 0.1.0
provenance: "hand-authored, 2026-07-04, dev-velocity tooling Phase 3"
rollout_stage: shadow
success_rate_30d: null
cases_seen_30d: 0
---

# Codebase Health Audit

## Goal
Keep Metorite developable by coding agents as it grows. The measurable
target is **agent drag** — the context an agent must load and the blast radius
of an edit. Proxy metrics: per-function cyclomatic complexity, file LOC, and the
maintainability index. The loop is **review -> plan -> refactor -> re-measure**,
and it is **flag-and-propose**: you never refactor a file unsupervised. A
5,000-line file is exactly where an unreviewed automated edit does the most
damage (see the auto-fix hazard in the spec).

## Steps

### 1. Measure (always)
Run the shared measurement engine on the target, before touching anything:
```
uv run python scripts/codebase_health.py --json --paths <dir-of-target>
```
Record `health_before`: the target's worst-function cc, its LOC, its MI, and the
named functions over the warn/fail thresholds. Cite `[file:<path>]`.

### 2. Review
Read the flagged functions. For each high-cc function, name *why* it is complex:
- Branching on many cases (dispatch that wants a table / strategy map)?
- Deeply nested conditionals (guard-clause / early-return candidate)?
- One function doing multiple responsibilities (extract-method candidate)?
- Repeated blocks (helper-extraction candidate)?
Note existing seams — helpers already imported, re-export boundaries (do NOT
break them; see Constraints), and test coverage on the target.

### 3. Plan (the deliverable — stop here by default)
Produce a concrete decomposition plan, not a vibe:
- The specific extractions: new function/class names, what moves into each,
  their signatures.
- The ordering (smallest, lowest-risk seam first) so each step is independently
  reviewable and testable.
- The risk per step and how it's verified (which existing test covers it; if
  none, the test to add first).
- Which workstream it belongs to if one exists (e.g. `executor.run_agent_stream`
  -> `core_loop_unification`).
Cite `[func:<name>@<line>]` for each target. **Present the plan and stop** unless
the user explicitly approves refactoring.

### 4. Refactor (only on explicit approval)
- One extraction at a time, each a separate reviewable commit.
- Run the relevant tests after **each** step (`uv run python -m pytest ...`),
  not just at the end.
- Preserve behavior exactly — this is refactoring, not redesign. No new
  features, no changed signatures on public entry points without calling it out.

### 5. Re-measure
Re-run `scripts/codebase_health.py` on the target and report `health_after`
next to `health_before`. The worst-function cc and file LOC must have *dropped*;
if they didn't, the refactor didn't achieve its goal — say so plainly.

## Constraints
- **Flag-and-propose is the default.** Never refactor without explicit approval.
  The plan (step 3) is the normal end state.
- **Never run a blanket `ruff --fix` / auto-formatter across the file or repo.**
  This repo uses re-export shims and import-for-side-effect (FastAPI routers,
  plugin registries) that F401 auto-fix silently deletes — a real regression
  happened this way (spec: "Auto-fix hazard"). Fix by hand, diff-review every
  deleted import.
- **Verify against clean HEAD.** A finding computed against a dirty/already-edited
  tree can be a phantom. Before claiming a bug or a complexity number, confirm it
  against `git show HEAD:<path>` (spec: false-positive lesson).
- **Respect DOX.** Read the AGENTS.md chain for the target's subtree before
  editing; update the owning AGENTS.md if the refactor changes durable structure.
- Behavior-preserving only; no scope creep into feature changes.

## Tests
See `evals/cases.yaml`.
