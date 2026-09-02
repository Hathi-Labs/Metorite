# Agents, Workspaces, Files & Artifacts — How It All Connects

> **Status:** living reference (workspace/artifact model) · no owning board row · **Last reviewed:** 2026-08-09 (D15 phrasing sweep only — content not re-verified against code)

Definitive reference for how agents are registered, loaded, where their files
live on disk, and how the chat Files panel and the Artifacts viewer surface
them. Written after a full end-to-end review (backend + frontend + live VPS).

> **See also — the durable-state contract & its implementation:**
> [`specs/agent_file_and_memory_framework.md`](specs/agent_file_and_memory_framework.md)
> is the canonical framework every MAF agent MUST follow for persisting code
> (git, reviewed PR) vs. state (Postgres blob store, authoritative), and
> [`specs/agent_persistence_implementation.md`](specs/agent_persistence_implementation.md)
> is the engineering reference (every function, table, and seam — read before
> changing how persistence works). Required reading before building the
> in-platform agent workbench or any new MAF agent (this deployment or a future second tenant deployment *(2026-08-09, under D15: another organization — rows + RLS, not a deployment)*). This doc explains the *layout*; the framework explains the *contract*;
> the implementation reference explains *how it's built*.
>
> **The disk workspace described below is now a rehydratable cache** — the three
> folders (`agent-data/`, `inputs/`, `outputs/`) are backed by the Postgres blob
> store (write-through on write, fault-in on read miss, rehydrate on load). The
> layout and browser behaviour here are unchanged; the store sits *behind* these
> read paths.

> TL;DR of the two things that confused us repeatedly:
> 1. **An agent's on-disk workspace is ALWAYS `{agents_clone_dir}/repos/{agent_name}`** — keyed by the *logical agent name*, never the GitHub repo name and never the registry `local_path`. The clone only exists **after the agent has run at least once** (lazy clone). Registered ≠ cloned.
> 2. **Copilot SDK agents do not write to `outputs/`** — they create/edit files in the working-directory **root** via native tools and never call `write_artifact`. So the file browsers must walk the **whole working tree**, not just `inputs/outputs/agent-data`.

---

## 1. The three registration sources

An agent can come from any of three places; the gateway merges them at read time
(`apps/gateway/gateway/routes/agent.py`):

| Source | Where | Examples | Notes |
|---|---|---|---|
| **Static `_AGENT_REGISTRY`** | hard-coded list in `agent.py` (~line 98) | task-manager, sales, delivery, triage, reconciler, billing, strategy, apis-config, email-assistant | All `agent_runtime: github-copilot`. A few set a **relative** `local_path` (`apps/agent-task-manager`), most have none. |
| **Dynamic `dynamic_agents`** | Postgres table (`infra/postgres/15_dynamic_agents.sql`) | agent-sales-assistant, agent-project-manager, agent-startup-guru, metorite-dev | User-registered via the UI. `repo_name` stored as `org/repo` slug. Survives deploy/reboot. |
| **`agents.json`** | `apps/gateway/agents.json` | (legacy, currently 1 entry) | Read-only fallback; synced into Postgres on load. Only written when Postgres is down. |

- **List endpoint:** `GET /agent` (gateway) → proxied by Next.js `GET /api/agent/list`. Returns static (minus dynamic-overridden) + dynamic. `agent_runtime = "github-copilot" if (repo_name and not local_path) else "maf"`.
- **Register endpoint:** `POST /agent` (`register_agent`). UI sends `{name, description, tags, integrations, repo_url | local_path}` (NOT `agent_runtime` — the backend classifies). GitHub-repo agents get `github-copilot`; local-path agents get `maf`.
- **Registration triggers an eager background clone** (`_eager_clone`) **only for GitHub-repo dynamic agents**. If that clone fails (private repo, auth, bad URL) the agent is still registered/live but has **no workspace on disk**.

## 2. Loading & the on-disk workspace path (the part we kept getting wrong)

`packages/acb_skills/acb_skills/loader.py :: load_agent(agent_name, repo_name, local_path)`:

- **Clone root:** `agents_clone_dir` setting, default **`~/.acb/agents`** (NOT `/tmp` — that was the old default; `/tmp` clones get wiped on reboot). Override: `AGENTS_CLONE_DIR`. → `cache_root = {agents_clone_dir}/repos`.
- **Local-path branch:** copies `local_path` → `cache_root/{agent_name}` and runs there. `local_path` is a *source pointer only*; the agent NEVER runs from it.
- **GitHub branch:** `_ensure_repo(repo, clone_as=agent_name)` → clones into `cache_root/{agent_name}`.

**Result — for every case the workspace is `{agents_clone_dir}/repos/{agent_name}`** (X = the logical agent name):

| Case | Inputs | Workspace dir |
|---|---|---|
| static, no local_path | name=`sales` | `…/repos/sales` (cloned from `FracktalWorks/agent-sales`) |
| static, with local_path | name=`task-manager`, local_path=`apps/agent-task-manager` | `…/repos/task-manager` (copied from monorepo source) |
| dynamic, github | name=`agent-sales-assistant`, repo=`FracktalWorks/agent-sales-assistant` | `…/repos/agent-sales-assistant` |
| dynamic, local_path | name=`x`, local_path=`/abs/dir` | `…/repos/x` |

**Clones are lazy** — created on the **first `load_agent` call** (i.e. when the agent runs), except the eager warm-clone at registration for GitHub dynamic agents. `load_agent` is only called from the orchestrator executor (`apps/orchestrator/orchestrator/executor.py`: `run_agent`, `run_agent_stream`, sub-agent helpers) and `_eager_clone`.

**What runs an agent (→ clones it):** chat (`POST /agent/run/stream`), `POST /agent/run[/async]`, webhooks (`POST /agent/webhook/{source}`), the email assistant, and `call_agent` sub-agent delegation. There is **no in-app cron**. An agent that is only ever invoked as a **sub-agent specialist** (e.g. via the orchestrator's `call_agent`) under a *different* run still clones under its own name — but if it's only *registered as a specialist tool* and never actually invoked, it never clones.

## 3. Why a registered agent can have NO files / not appear

This is the exact situation with `agent-sales-assistant`, `agent-startup-guru`,
`agent-project-manager` (confirmed on the VPS — only `email-assistant` and
`metorite-dev` had clones):

1. **Never run as a primary agent** → never cloned → no workspace dir. They show in the agents page (registry) and as orchestrator specialist tools, but have no disk presence.
2. **Eager clone failed or went to old `/tmp`** and was wiped on reboot.
3. **Agent writes files but not to `outputs/`** → before the whole-tree fix, the browsers only walked the 3 special dirs, hiding everything.

## 4. Chat Files panel vs Artifacts viewer

| | Chat Files panel (`ArtifactSidebar`) | Artifacts viewer (`/artifacts`) |
|---|---|---|
| Frontend | `src/components/ArtifactSidebar.tsx` | `src/app/artifacts/page.tsx` |
| Proxy → gateway | `GET /api/agent/workspace/{sessionId}` → `GET /agent/workspace/{id}` | `GET /api/agent/artifacts` → `GET /agent/artifacts` |
| Backend resolves via | `_get_workspace_path(session_id)`: `chat_session.workspace_path` (set by `write_artifact` PATCH — usually NULL) → else `_resolve_agent_workspace(agent_name)` | `_discover_agent_workspaces()`: all live agent names → workspace dir |
| Scope | one session's agent | all live agents |

- `chat_session.agent_name` is written **once**, when the session is created: frontend `createSession` → `POST /api/chat/sessions` → `POST /chat/sessions`. No frontend ever PATCHes `workspace_path`.
- **Artifacts page hide points** (`page.tsx`): an agent shows only if its `agent_name` from `/agent/artifacts` is ALSO in `/api/agent/list` with `status` live/empty (exact name match). The user must **click an agent card to expand** it before files render. `is_dir` entries are never counted as "files" but render as folders.

## 5. The workspace browser walks the WHOLE tree (with guards)

`apps/gateway/gateway/routes/workspace.py` (`_walk_tree`, `_walk_agent_artifacts`):

- Walks the entire clone working tree, **not** just `inputs/outputs/agent-data` — so Copilot agents' root-level files (reports, scripts, code) are visible.
- **Pruned:** `_EXCLUDED_DIRS` (`.git`, `node_modules`, `.next`, `__pycache__`, `dist`, `build`, caches, …) and any dotdir.
- **Hidden files:** `_is_hidden_or_secret_file` — dotfiles (`.env`, `.zoho_token_cache.json`), `*.pid/*.pem/*.key/*.pyc`, and names containing `token_cache`/`credential`/`secret`/`id_rsa`. The raw-file endpoints enforce the same via `_is_blocked_path` (so `?path=.env` 404s).
- **Cap:** `_MAX_TREE_FILES` (4000) bounds whole-monorepo clones (e.g. `metorite-dev`).
- `_agent_workspace_dir` checks BOTH `{agents_clone_dir}/repos/` and the legacy `/tmp/acb_agents/repos/` (and `agent-` prefix variants), so clones stranded in `/tmp` are still found.
- `_discover_agent_workspaces` falls back to `_canonical_workspace_dir` so **every live registered agent appears** in the viewer (with 3 empty folders) even before it has ever run/cloned. `_walk_agent_artifacts` always emits the 3 special folders first.

## 6. Practical consequences / gotchas

- To get an agent's files to appear with real content, **run the agent once** (clones it) and have it actually write files. Email-style agents (draft via Gmail API) legitimately produce no files.
- Static github-clone agents (sales, delivery, …) only have a workspace once run.
- `email-assistant` drafts via API → its `outputs/` is genuinely empty; its source files show because we walk the whole tree.
- Deploy = push to `main` → GH Actions `git reset --hard` + `systemctl restart acb-gateway` + rebuild workbench. Backend changes need the gateway restart to take effect.

## 7. Live VPS quick-checks (read-only)

```bash
ssh acb@187.127.172.200
ls ~/.acb/agents/repos/            # which agents actually have a clone
ls /tmp/acb_agents/repos/          # legacy stranded clones
docker exec acb-postgres sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT name,status,repo_name,local_path FROM dynamic_agents"'
TOK=$(grep -E "^(GATEWAY_INTERNAL_TOKEN|LITELLM_MASTER_KEY)=" /opt/acb/app/.env | head -1 | cut -d= -f2-)
curl -s -H "Authorization: Bearer $TOK" "http://127.0.0.1:8080/agent/artifacts?agent=<name>"   # what the viewer gets
```
