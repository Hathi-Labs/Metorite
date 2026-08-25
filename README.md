# Metorite v2

> **Fracktal Works** — a headless, self‑mutating multi‑agent orchestration platform. Specialist agents live in their own GitHub repositories; skills are pip‑installable Python packages. The Core engine dynamically clones agent and skill repos at runtime, executes tasks on the **Microsoft Agent Framework (MAF)** runtime, and — when a repo is structurally broken — spawns an isolated GitHub Copilot SDK mutation container that patches the failing agent's own repo and stages the fix for human review.

---

## What is Metorite?

Metorite is the operating system for Fracktal Works. It coordinates a fleet of specialist AI agents (sales, triage, delivery, billing, reconciler, strategy, task‑manager, email‑assistant) over company data in its own project-management store, Zoho CRM, Odoo ERP, Gmail/IMAP, and meetings — with human‑in‑the‑loop approval for outward writes.

**Architecture in one sentence:** A FastAPI gateway receives chat / webhook / cron events, dynamically clones the target `agent-<name>` repository and its declared `skill-<name>` dependencies, imports the agent's `agents.py` at runtime via `importlib`, and runs it on the MAF runtime — either as a native MAF `ChatAgent` (routed through the gateway's own OpenAI‑compatible `/v1` endpoint via the LiteLLM SDK) or, for Copilot‑SDK‑backed agents, via `agent_framework_github_copilot`.

**Self‑mutation:** when an agent repo fails to load or run (structural incompatibility), the orchestrator spawns a Docker mutation sandbox that drives the Copilot SDK to read the error telemetry, propose a code fix, and produce a commit. The commit is staged to the Control Plane approval inbox — **a human approves before it is pushed.**

**No in‑app editor:** agents and skills are developed in VS Code (locally or via GitHub Codespaces), committed to their repos, and merged through the standard PR flow. The Control Plane is for chat, HITL approvals, and observability — not editing.

---

## Distributed repo layout

```
Hathi-Labs/Metorite          ← This repo: Core engine + infra
FracktalWorks/agent-task-manager     ← Agent: task management (GTD)
FracktalWorks/agent-sales            ← Agent: Zoho CRM sales workflows
FracktalWorks/agent-delivery         ← Agent: project delivery + push
FracktalWorks/agent-triage           ← Agent: email/WhatsApp/meeting triage
FracktalWorks/agent-reconciler       ← Agent: nightly source-of-truth diff
FracktalWorks/agent-strategy         ← Agent: weekly digest + planning
FracktalWorks/skill-*                ← Skills: pip-installable Python packages
```

Each **agent repo** contains: `config.json` (runtime, model tier, tool scope, required skills, triggers), `agents.py` (a `build_agents()` factory returning the MAF/Copilot agent list), `instructions.md` (persona), `tests/`, `evals/`.

Each **skill repo** is a Python package — pip‑installable, well‑typed entry functions, `tests/`, `evals/`.

---

## Architecture overview

```
[ Webhook / Cron / Chat event ]
              │
              ▼
┌─────────────────────────────────────────────────────────────┐
│ 1. Gateway: FastAPI (apps/gateway)                          │
│    • Receives events; /copilot/chat (AG-UI) + /v1 (OpenAI)  │
│    • Dynamically clones agent-<name> + skill repos          │
│    • Imports agents.py via importlib at runtime             │
└───────────────────────────┬─────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────┐
│ 2. Orchestrator: MAF runtime (apps/orchestrator)            │
│    • executor.run_agent_stream — 3-tier dispatch:           │
│        Tier 1   native MAF ChatAgent (→ gateway /v1)        │
│        Tier 1.5 GitHub Copilot SDK (interactive)            │
│        Tier 2   batch shim fallback                         │
│    • Streams AG-UI events; tees to Redis for reconnect      │
│    • On structural failure → mutation sandbox               │
└───────────────────────────┬─────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────┐
│ 3. Self-mutation: Copilot SDK container (docker run --rm)   │
│    • Patches the failing repo → commit                      │
│    • Staged to the approval inbox (human approves the push) │
└─────────────────────────────────────────────────────────────┘
```

**Key principles:**
- **Source of truth = Zoho / Odoo** for CRM and ERP; the Core is a read‑mostly mirror of those and outward writes are approval‑gated. ⚠️ **Metorite itself is the source of truth for PROJECT MANAGEMENT** — ClickUp was retired outright on 2026-08-24 (D52); `pm_tasks`/`pm_task_personal` are the one task store and there is nothing upstream to mirror.
- **Decoupled repositories.** Each agent and skill is an independent GitHub repo, versioned and tested independently.
- **Dynamic loading.** Core adopts new agent logic on the next event — repos are cloned/pulled at event time (no redeploy).
- **Self‑mutation with a human gate.** A structurally broken repo gets an auto‑proposed fix; a human approves before it is pushed.
- **Git is the source of truth** for all agent‑editable artefacts (`agents.py`, `instructions.md`, skill packages, model‑routing config).

> **Runtime note:** MAF is the primary runtime. Copilot‑SDK‑backed agents (`agent_framework_github_copilot`) run interactive tasks as well as the mutation sandbox — see `AGENTS.md` for the current runtime policy.

---

## This repo layout (Metorite Core)

```
project-docs/        # Planning docs — start with AGENTS.md
  project_plan.md        # Requirements + roadmap + WBS
  system_architecture.md # C4 diagrams, data model, ADRs
  reference.md           # MAF / Copilot SDK / memory notes
  specs/                 # Per-feature specs

apps/                    # Deployable services / app modules
  gateway/               # FastAPI: events, /v1 LLM proxy, approvals, email + tasks routes
  orchestrator/          # MAF executor, dynamic agent loader, self-mutation
  ingestion/             # Zoho / Gmail webhook receivers + queue
  email_ingestion/       # Email sync workers (Gmail / Outlook / IMAP)
  reconciler/            # Nightly diff + escalation
  action_broker/         # Approval-gated source-of-truth write executor (see AGENTS.md)
  agent-*/               # First-party agents shipped in-repo

packages/                # Shared Python libs (uv workspace members)
  acb_common/            # Settings, structlog logging, Redis activity/cost feed
  acb_graph/             # Postgres + pgvector access layer
  acb_llm/               # LiteLLM SDK client + tiered routing + BYOK key store + guardrails
  acb_audit/             # Append-only audit log
  acb_skills/            # Skill/agent loader, tool injection, permission policy, integrations
  acb_memory/            # mem0 (episodic) + graphiti (bi-temporal KG) + session cache
  acb_auth/              # Auth helpers (header-trust SSO + internal token)
  # (acb_schemas was removed — 0 production importers; entity models live in acb_graph)

infra/                   # docker-compose, Postgres init/migrations, LiteLLM tier config
skills/                  # Anthropic SKILL.md registry
workbench/control_plane/ # Next.js Control Plane (chat, observability, HITL approvals)
evals/                   # Promptfoo + Inspect AI + golden trajectory evals
deploy/                  # Hostinger VPS + Caddy deploy scripts
scripts/                 # Ops / migration helpers
tests/                   # Cross-cutting unit + integration tests
```

---

## Running locally

### Prerequisites

| Tool | Version | Purpose |
|---|---|---|
| Python | 3.12+ | Backend services |
| [uv](https://docs.astral.sh/uv/) | ≥ 0.4 | Python workspace manager |
| Docker + Docker Compose | latest | Infra stack |
| Node.js | 20+ | Control plane (`workbench/`) |

### 1. Install the Python workspace

```bash
uv sync
```

### 2. Configure environment

```bash
cp .env.example .env
# Edit .env — set LITELLM_MASTER_KEY, ACB_MASTER_KEY, provider secrets, etc.
```

### 3. Start the infra stack

```bash
docker compose -f infra/docker-compose.yml up -d
# then apply the numbered SQL migrations (compose only auto-loads 00/01):
bash scripts/apply_migrations.sh
```

Services once running:

| Service | URL |
|---|---|
| Postgres (pgvector) | `localhost:5432` |
| Redis | `localhost:6379` |
| Neo4j (optional; graphiti) | `localhost:7687` |
| LLM routing | in‑process **LiteLLM SDK** via the gateway `/v1` endpoint (no separate proxy) |

### 4. Start the gateway

```bash
uv run uvicorn gateway.main:app --reload --host 0.0.0.0 --port 8000 --app-dir apps/gateway
```

`GET http://localhost:8000/health` → `{"status":"ok","env":"dev"}`

### 5. Start the Control Plane

```bash
cd workbench/control_plane
npm run dev          # http://localhost:3001
```

| Pane | Path | Description |
|---|---|---|
| Chat / Agent Inbox | `/` | AG‑UI + SSE (MAF orchestrator); HITL queue |
| Observability | `/observability` | Activity feed, spend, mutation approval history |

---

## Common commands

| Task | Command |
|---|---|
| Lint | `uv run ruff check .` |
| Format | `uv run ruff format .` |
| Type‑check | `uv run mypy apps packages` |
| Tests (unit) | `uv run pytest tests/unit/` |
| Tests (all, needs docker stack) | `uv run pytest -m integration` |
| Coverage | `uv run pytest --cov=apps --cov=packages` |
| Add dep to a package | `uv add --package <name> <dep>` |
| Upgrade lockfile | `uv lock --upgrade` |
| Infra logs | `docker compose -f infra/docker-compose.yml logs -f --tail=100` |

See [`Makefile`](Makefile) for convenience targets.

---

## Tech stack

| Layer | Technology |
|---|---|
| Control plane | Next.js 16, React 19, Tailwind v4, AG‑UI + SSE |
| Agent runtime | Microsoft Agent Framework (MAF) + GitHub Copilot SDK |
| Dynamic loading | Python `importlib` + git clone/pull (per‑event agent clone) |
| Self‑mutation | GitHub Copilot SDK (isolated Docker mutation container) |
| LLM routing | **LiteLLM SDK in‑process** (gateway `/v1`), BYOK via encrypted Postgres key store — Anthropic / OpenAI / DeepSeek / Groq / OpenRouter / … |
| Database | Postgres 16 + pgvector |
| Cache / event streams | Redis 7 |
| Memory | mem0 (episodic) + graphiti + Neo4j (bi‑temporal KG, optional) |
| Evals | Promptfoo + Inspect AI + golden trajectory tests |
| Deploy | Docker Compose (data plane) + host systemd units behind Caddy (Hostinger VPS) |

---

## Agent + Skill development

**Authoring environment:** VS Code (locally or GitHub Codespaces). No in‑app editor.

1. Create a repo from the agent or skill template.
2. Edit `agents.py` / `instructions.md` / skill `impl.py` in VS Code.
3. Add evals in `evals/` (Promptfoo golden cases + Inspect AI scenarios).
4. Open a PR — CI runs `pytest` (+ path‑gated evals); merge when green.
5. Core picks up the new version on the next event (no redeploy).

**Self‑mutation:** if the live system hits a structural failure loading/running an agent, the mutation sandbox proposes a fix on the failing agent's own repo and stages the commit. You review and approve; Core picks it up on the next run.

---

## Planning docs

All planning artefacts live in [`project-docs/`](project-docs/). Start with `AGENTS.md`.

| Doc | Purpose |
|---|---|
| [`AGENTS.md`](AGENTS.md) | Root project contract + constraints + conventions (read first) |
| [`project-docs/project_plan.md`](project-docs/project_plan.md) | Requirements + milestones + WBS + risks |
| [`project-docs/system_architecture.md`](project-docs/system_architecture.md) | C4 diagrams, data model, ADRs |
| [`project-docs/reference.md`](project-docs/reference.md) | MAF / Copilot SDK / memory library notes |
| [`FOUNDATION_AUDIT_REPORT.md`](FOUNDATION_AUDIT_REPORT.md) | Foundational architecture audit (findings by severity) |
| [`FOUNDATION_BUILDOUT_CHECKLIST.md`](FOUNDATION_BUILDOUT_CHECKLIST.md) | Missing/partial foundational capabilities + priorities (incl. `CH-*` competitive refs, BO‑20/BO‑21) |
| [`COMPETITIVE_COMPARISON.md`](COMPETITIVE_COMPARISON.md) | Three‑way comparison vs Hermes Agent & OpenClaw — where we stand + what to learn (annealed into `project-docs/specs/competitive_hardening_2026-07.md`) |
