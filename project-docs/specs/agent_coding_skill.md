# Agent Coding Skill — durable scripts for MAF agents (`code_task` + `run_script`)

**Status:** 🟢 Phase 1 shipped (2026-07-23)
**Depends on:** `chat_agent_framework_review_2026-07.md` §2 (dual-runtime verdict: MAF is the framework, the Copilot SDK is the coding **engine**), blob-store workspace durability (migration 71 `agent_blob`, `acb_memory.put_file`), `permissions_sandbox_b6.md` (risk annotations, BO-7 container sandbox).

## 1. Why

Native MAF agents have no way to *write a program* when their built-in tools fall
short — previously the only coding path was a standalone GitHub Copilot SDK
agent, a separate runtime with separate context. The framework review concluded
the SDK should be a **capability, not a peer agent**. This skill makes every MAF
agent able to:

1. **author** scripts (a bounded Copilot session working in the agent's own
   workspace), and
2. **reuse** them forever (zero-LLM re-execution of a saved script),

with the scripts stored durably — across operations, restarts, and Metorite
updates.

## 2. The two tiers

| Tool | What it does | Cost |
|---|---|---|
| `code_task(task)` | One-shot Copilot SDK coding session (`orchestrator/code_session.py`) in the calling agent's workspace: writes, edits, runs, and debugs scripts under the script contract. 600 s wall-clock cap. | one LLM session |
| `run_script(path, args?)` | Executes an existing workspace `.py`/`.sh` script directly (`packages/acb_skills/acb_skills/code_tools.py`). No reasoning step. | zero LLM |

The intended loop: first need → `code_task` builds `agent-data/scripts/<x>.py`
and catalogs it; every later need → `run_script("agent-data/scripts/<x>.py")`;
changes → `code_task` naming the script (edited **in place**, not duplicated).

### The script contract (manifest-first)

Enforced by the coding session's harness prompt (`_HARNESS_INSTRUCTIONS`):

- `agent-data/SCRIPTS.md` is the **manifest** — read first, updated before the
  session ends (name, purpose, usage/args, last-changed per script).
- Reusable scripts live under `agent-data/scripts/`; scratch + generated data
  under `outputs/`.
- Sessions are deliberately **stateless** (no `service_session_id` resume):
  continuity lives in the workspace, not the conversation, so the contract works
  identically after a restart, a redeploy, or from a different chat session.
- Run what you write; no commit/push; no system packages (`uv pip install` into
  the venv only when genuinely needed).

## 3. Durability

Scripts must survive operations, restarts, and Metorite updates:

- The workspace lives **outside the app dir**, so deploy `git reset --hard`
  never touches it.
- `agent-data/`, `inputs/`, `outputs/` are blob-store backed (Postgres
  authoritative, disk is a rehydratable cache) — survives volume wipes and box
  moves.
- **The gap this skill closes:** the Copilot CLI writes files with its *native*
  tools, and scripts write their own outputs — both bypass the `write_artifact`
  write-through mirror. So:
  - `code_task` **always** finishes with a sweep (`_sweep_to_blob_store`) that
    mirrors every file changed during the session under `agent-data/` +
    `outputs/` into the store — *even when the session itself failed* (partial
    work is not lost).
  - `run_script` sweeps `outputs/` for files the script produced.
  - Sweep bounds: 2 MB/file, 200 files/run (the store is for scripts and
    reports, not gigabyte artifacts); failures skip the file, never the tool.

## 4. Execution hygiene (agent-use standards)

`run_script` (and, transitively, everything the coding session leaves behind)
runs under process-level hygiene:

- **Workspace jail** — the script path must resolve inside the workspace via
  `resolve_in_workspace` (same containment guard as `write_artifact`); traversal
  and absolute-path escapes fail closed.
- **Secret-scrubbed env** — the subprocess env is rebuilt from an allowlist
  (`PATH HOME LANG LC_ALL TZ TMPDIR PYTHONPATH`) with a deny-pattern
  (`TOKEN|SECRET|KEY|PASSWORD|CREDENTIAL`) on top; gateway tokens, provider
  keys, and DB URLs never reach arbitrary script code.
- **Timeouts** — `RUN_SCRIPT_TIMEOUT_SECONDS` (default 120 s) per script run;
  600 s per coding session (`asyncio.wait_for`).
- **Output cap** — combined stdout/stderr returned to the model is middle-
  truncated at 8 000 chars.
- **Risk annotations** — both tools are registered in `TOOL_ANNOTATIONS` as
  state-writing + **open-world** (a script may reach the network), so the
  permission policy and the addendum's risk block see them; the injected-tool
  permission gate (B6) wraps them like every other platform tool.
- The coding session itself routes **BYOK through the gateway `/v1`** (same
  provider block as Tier-1.5 agents) → platform model tiers, context-window
  guard, and cost observability apply; `_copilot_permission_handler` gates the
  CLI's own shell/file capabilities.

This is process-level hygiene, not a container sandbox — **BO-7** remains the
hardening path for genuinely untrusted code.

## 5. Wiring

- `orchestrator/_tool_injection.py` — both tools injected into every agent shape
  (MAF + Copilot), added to `_CORE_STANDARD_TOOL_NAMES` (the guaranteed floor:
  a `tool_scope` cannot strip them), documented in the full + compact
  system-prompt addendum ("### Coding skill (durable scripts)").
- `packages/acb_skills/acb_skills/code_tools.py` — the skill layer.
- `apps/services/orchestrator/orchestrator/code_session.py` — the engine runner.

## 6. Tests

`tests/unit/test_code_tools.py` (24 cases): containment escapes, env scrub
(live subprocess proves no `GATEWAY_INTERNAL_TOKEN`/`OPENAI_API_KEY`/
`DATABASE_URL` leak), timeout, output cap, sweep selectivity (mtime cutoff +
size bound), `code_task` always-sweep on failure, floor/addendum/annotation
wiring.

## 7. Harmonisation with agent mutability

The agent's chat workspace **is** its persistent git clone
(`_resolve_effective_agent_dir` defaults to `LoadedAgent.agent_dir`), which is
what makes the coding skill and the existing self-mutation machinery one
system rather than two. There are two kinds of scripts, with different
governance, and three mutation paths that all converge on the same
human-approval pipeline:

### Script taxonomy

| | Workspace scripts | Repo-baked skills |
|---|---|---|
| Home | `agent-data/scripts/` | `skills/*/scripts/` + `agents.py` |
| Tracked by | blob store (Postgres) | git (agent repo) |
| Catalog | `agent-data/SCRIPTS.md` | each skill's `SKILL.md` |
| Change gate | none (agent's own working memory) | **pending_commit inbox approval** |
| Loaded as | `run_script` target | MAF tool via `build_agents()` |

### Mutation paths (all → `pending_commit` → human approve → push)

1. **Failure-driven** (`orchestrator/mutation.py`): a run crashes → Docker
   mutation sandbox fixes the repo, commits locally, row registered. Pre-dates
   this skill; unchanged.
2. **Chat-driven** (`code_task`, this spec): the agent notices a built-in
   skill misbehaving (or the user asks) → the coding session edits
   `skills/*/scripts/*.py` **in place** and commits locally (contract step 6).
   The executor's layered post-run commit scan (`_detect_agent_commits`:
   post-commit-hook queue file → since-SHA scan → 50-commit catch-up) registers
   every local commit for inbox approval; the loader's pre-push hook blocks any
   direct push. **Fail-safe:** if the session edits tracked source and forgets
   to commit, `code_task` itself commits the residue (`_commit_repo_changes`) —
   necessary because the loader's next `_pull_latest` stash-drops/hard-resets
   uncommitted tracked changes (they would be silently destroyed, the fix lost).
   `git add -A` respects the loader-managed `.gitignore`, so `agent-data/`,
   `inputs/`, `outputs/` never leak into a commit.
3. **Promotion** (`loader._sync_new_skills`): a `code_task` session (or a
   human) drops a new script into `skills/<skill>/scripts/` → on the next
   load the loader auto-wraps it as an async tool in `agents.py`, commits, and
   registers that commit for approval. This is the graduation path from
   "durable personal script" to "first-class agent tool": prove it under
   `agent-data/scripts/` via `run_script`, then ask `code_task` to move it into
   a skill folder.

So the full loop the user asked for works end-to-end: broken built-in skill →
`code_task` edits the actual source → local commit → inbox approval → push →
next `load_agent` pulls it → the fixed tool is live — while unapproved edits
can never reach `main` and can never be silently lost.

## 8. Integrations: declared-only credential pass-through

Scripts often exist precisely to call an external service the platform already
holds credentials for (ClickUp, Zoho CRM, Gmail, SerpAPI, …). Blanket
scrubbing would make the skill useless for that; blanket pass-through would
leak the platform's whole secret surface into arbitrary code. The rule is the
same one the rest of the platform already enforces: **an agent's scripts get
exactly the integrations that agent declared — nothing else.**

- `config.json: integrations` / `optional_integrations` → resolved by the
  Integration Registry per run; the executor injects the canonical env vars
  run-scoped (`_inject_integrations_to_env`, B6 Tier-0 restore token) and now
  also publishes the resolved service names into the tool context.
- `code_tools._script_env` grants the subprocess the base allowlist **plus
  exactly those services' env vars** (via the shared
  `acb_skills.integrations.FIELD_TO_ENV` mapping — single source of truth with
  the executor's injection, keeping "what the run has" ≡ "what a script may
  see"). Undeclared integrations, the gateway master key, and the DB URL stay
  scrubbed. Sub-agents get *their own* declared set, not the parent's.
- **Discoverability:** the injected `list_integrations()` tool (core floor,
  read-only) reports resolved services + the env-var *names* scripts can read,
  and declared-but-unavailable services with the reason — never values. The
  `code_task` session prompt names the same vars, and the harness forbids
  hard-coding, printing, or persisting credential values.
- On-the-fly skills against an integration therefore need no key handling at
  all: the agent checks `list_integrations`, `code_task` writes a script that
  `os.getenv`s the canonical vars, and `run_script` reuses it forever. A new
  service still onboards through the registry (resolver + `FIELD_TO_ENV` entry
  + declaration in the agent's config) — never by pasting keys into scripts.

## 9. Open follow-ups

- **BO-7**: move `run_script` execution into the container sandbox when it
  lands; the skill API is already shaped for it (path + args in, capped output
  out).
- Optional `list_scripts` convenience tool (today `recall_notes("SCRIPTS.md")`
  covers it).
- DOE-v2 promotion: graduating a proven `agent-data/scripts/*` script into a
  repo-baked agent tool via the existing pending-commit approval path.
