# Dev-Velocity Tooling — Keeping a Large Codebase Agent-Developable

> **Status:** Phases 1-3 shipped · **Created:** 2026-07-04
> Motivation: the codebase is large enough (766 tracked files; a 5,019-line
> `executor.py`; 33 files >800 LOC) that unbounded growth in file size and
> function complexity is becoming *agent drag* — the dominant cost when a
> coding agent edits a monster file is the context it must load and the
> blast radius of any change.
> Source practices: [awesome-harness-engineering](https://github.com/ai-boost/awesome-harness-engineering)
> (§ Refactoring & Complexity Management, § Code Health & Verification) plus
> current (2026) tooling for large-codebase agentic development (radon/xenon
> complexity gates, code-graph MCP navigation, `code_health_review` loops).
> Companion: [`harness_hardening_2026-07.md`](harness_hardening_2026-07.md)
> (the HH queue — harness *behaviour*; this spec is about dev *velocity*).

## Verdict

Metorite's structural hygiene is already strong (DOX AGENTS.md hierarchy,
uv workspace, per-package pyproject, tool_scope, eval trajectories). The gap is
**enforcement pressure against complexity growth** and **structural code
intelligence for agents**. Three layers, ranked by ROI:

| Layer | What | State |
|---|---|---|
| **L1** | Enforcement gates (complexity ceiling, correctness lint, mypy, pre-commit) | **Phase 1 — shipped 2026-07-04** |
| **L2** | Structural code intelligence (code-graph / symbol-nav MCP for agents) | **Phase 2 — shipped 2026-07-04** (config; one-time install per dev) |
| **L3** | Scheduled code-health review (measure → flag → plan → refactor → re-measure), flag-only | **Phase 3 — shipped 2026-07-04** |

## Baseline measured 2026-07-04

- Repo-wide **average cyclomatic complexity ≈ 6.90** (healthy, grade B). The
  problem is concentrated in the tail, not systemic.
- **Worst function: `executor.run_agent_stream` at cc = 223.** (For reference,
  >10 is a refactor candidate.) This one function is the single biggest
  agent-drag liability in the repo; its decomposition is already the subject of
  the `core_loop_unification` workstream.
- Blocks over cc: **>30 → 21 · >20 → 66 · >15 → 108 · >12 → 164.**
- Full ruff check (never enforced — CI ran `continue-on-error`): **~1.3k
  findings**, of which the correctness class (F821 undefined-name, F601
  repeated-key) hid **4 real latent bugs** (see Phase 1).
- `ruff format --check`: 246/264 files not formatter-clean.
- Several files carry a **UTF-8 BOM** (U+FEFF) that breaks radon/xenon parsing.

## Phase 1 — Enforcement gates (shipped 2026-07-04)

Grandfather-and-ratchet throughout: thresholds set so current code passes, with
pressure only on *new* debt. Nothing big-bang; the legacy tail is paid down
deliberately by hand (NOT by a blanket auto-fixer — see the Auto-fix hazard).

**Shipped:**
1. **Complexity ceiling (blocking, `xenon`)** — `xenon --max-absolute F
   --max-modules F --max-average B apps packages` in `pr-check.yml`. `abs=F`
   grandfathers the cc=223 monster; `avg=B` (repo is 6.90) is the **ratchet** —
   any PR that pushes the repo-wide average worse, or adds a function worse than
   today's worst, fails.
2. **Complexity report (`ruff C901`, `max-complexity=15`)** — flags **37**
   genuine hotspots as a non-blocking dashboard. This is the refactor backlog.
3. **Correctness lint (blocking, `ruff`)** — `--select F821,F601,F602,F502,F7,B006`.
   Catches undefined names, repeated dict keys, format errors, mutable defaults.
   Passes today (pre-existing violations fixed); blocks any NEW real bug.
4. **Full ruff report (non-blocking)** — the ~1.3k-finding style backlog +
   complexity, as the ratchet dashboard. Trends to zero over time.
5. **mypy strict (report-only)** — `strict=true` was configured but never
   invoked in CI. Now runs as a report; flip to blocking per the ratchet below.
6. **`.pre-commit-config.yaml`** — `pre-commit` was a dep with no config. Now
   wires ruff (lint+format) + mypy + BOM-strip, **diff-scoped** (staged files
   only — legacy code is never force-touched). Install: `uv run pre-commit install`.
7. **radon/xenon** added to dev deps.

**4 real bugs the correctness gate surfaced (fixed in the `fix:` commit):**
- `imap.py` `get_message` called `_parse_imap_full_message` (nonexistent) →
  now uses `_parse_imap_fetch_full` (the intended helper — its docstring even
  names `get_message` as a caller). The get_message path was never wired.
- `runner.py` AI-draft body-hydration referenced `account_user` (undefined in
  scope; param is `user_email`) → guaranteed NameError on that branch.
- `acb_llm/client.py` `_env_to_provider` had a duplicated `OPENROUTER_API_KEY`
  key → deduped.
- `test_memory_integration.py` redundant local `patch` re-import → removed.

### Auto-fix hazard (learned the hard way, 2026-07-04)

A blanket `ruff check --fix` across the repo **deleted 52 aliased imports,
including intentional re-export shims** carrying explicit `# noqa: F401`
(e.g. `ToolCallStreamState as _FcStreamState` re-exported for tests;
`from gateway.routes.tasks import ai as _ai` — side-effecting router
registration). `py_compile` still passed; the breakage only surfaced as a test
import failure. **Rule: never bulk-`--fix` this repo.** It uses re-exports and
import-for-side-effect (FastAPI routers, plugin registries) that F401 auto-fix
treats as dead. The style backlog is ratcheted down **by rule, by directory,
diff-reviewed** — never repo-wide-auto.

### Ratchet plan (how the gates tighten over time)

1. Pay down the 37 C901 hotspots by hand (start with `executor.run_agent_stream`
   via `core_loop_unification`). As the max drops, tighten `xenon --max-absolute`
   F→E→D and `ruff max-complexity` 15→12→10.
2. Clean style classes one at a time (per-rule, per-directory), then migrate each
   cleaned rule from the report-only gate into the blocking `--select` list.
3. Once mypy report is clean per-package, flip `mypy` to blocking per-package.
4. Add F811 to the blocking correctness gate after the pre-existing nested-scope
   `import inspect` redefinition in `executor.py` is manually resolved.

## Phase 2 — Structural code intelligence (shipped 2026-07-04)

Give coding agents symbol/call-graph navigation instead of grep +
read-whole-file, so a 766-file repo doesn't cost a full-file read per lookup.

**Chosen: CodeGraph** (`@colbymchenry/codegraph`, MIT). 100% local (SQLite +
tree-sitter), indexes Python *and* TypeScript, auto-syncs on file change,
respects `.gitignore`. It serves the layer that helps agents **develop** the
codebase (Claude Code / Cursor working in-repo) — it is NOT wired into the
runtime `mcp_servers` table, because CodeGraph needs a per-checkout local index
and doesn't fit a shared-VPS runtime agent.

**Shipped (config only — the tool install is one-time per developer):**
- `.mcp.json` at repo root — Claude Code's project-scoped MCP config, so any
  coding agent in this repo gets the `codegraph_explore` tool automatically.
- `codegraph.json` — Metorite-specific index excludes (generated SQL,
  migrations, `.next/`, learning-resources, `project-docs/`) on top of
  CodeGraph's defaults.
- `.gitignore` — `.codegraph/` + db files (the local index is per-checkout,
  never committed).

**One-time developer setup** (not run by CI; a global binary, review before
installing): `npm i -g @colbymchenry/codegraph && codegraph init`. Then Claude
Code picks up `.mcp.json` automatically.

Alternatives considered: **CodeGuardian MCP** (also computes cc/MI/tech-debt —
but we already have that via `scripts/codebase_health.py`, so the pure-nav
CodeGraph is the cleaner split); **Repomix** (zero-infra repo-map fallback if a
dev can't install CodeGraph).

## Phase 3 — Scheduled code-health review (shipped 2026-07-04)

The "periodic review for maintainability" philosophy, made durable, as a
**measure → flag → plan → refactor → re-measure** loop with a *measurable*
target (complexity/LOC/MI), not a vibe. Runs as **GitHub Actions**, not an
in-app agent — the scout confirmed Metorite deliberately has "no in-app
cron for agents," and CI needs zero new runtime, secrets, or VPS process.

**Shipped:**
- `scripts/codebase_health.py` — the shared, dependency-light (radon-only),
  read-only measurement engine. Reports per-function cc, file LOC, and the
  maintainability index; `--json` (machine-readable), `--diff` (changed files
  vs merge-base), env-tunable thresholds. Used by BOTH consumers below.
- `.github/workflows/codebase-health.yml` — weekly (Mon 05:00 UTC) whole-repo
  run that opens/updates ONE rolling tracking **issue** (label `codebase-health`)
  with the report. Flags and proposes; never edits code.
- `pr-check.yml` — per-PR `--diff` health report (non-blocking) so a PR adding a
  big/complex function is visible in review before merge.
- **`skills/maintenance/codebase_health_audit/`** — the skill a human or coding
  agent invokes to ACT on a flagged file: `review → plan → refactor →
  re-measure`, `authority: suggest`, **flag-and-propose (never auto-refactor)**.
  It bakes in this workstream's hard-won rules: no blanket `ruff --fix` (the
  auto-fix hazard), verify against clean HEAD (the false-positive lesson),
  behavior-preserving only, one extraction per commit with tests between.

**Why flag-only, not auto-refactor:** unsupervised refactor of a 5k-line file is
exactly the high-blast-radius change the AGENTS.md fail-closed rule exists to
prevent — and the Auto-fix hazard above is direct proof that bulk automation is
dangerous in this repo.

### Refactor queue (the standing backlog — mirrors the HH queue)

Seeded from the first health run. Pay down top-down; re-measure after each.

| Target | Signal | Owning workstream |
|---|---|---|
| `executor.run_agent_stream` | cc 223 · file 5,020 LOC · MI 0 | `core_loop_unification` |
| `chat_fold.fold_run_events` | cc 82 | `core_loop_unification` (translator) |
| `email/automation/runner._apply_rule_actions` | cc 71 · file 1,352 LOC | email automation |
| `executor._inject_agent_tools` | cc 69 | (tool injection) |
| `executor._run_sub_agent_streaming` | cc 57 | `core_loop_unification` |
| `gateway/routes/agent.py` | 2,599 LOC · max cc 43 | gateway routes |
| `gateway/routes/integrations.py` | 1,988 LOC · max cc 35 | gateway routes |

Full list: `uv run python scripts/codebase_health.py --top 30`.
