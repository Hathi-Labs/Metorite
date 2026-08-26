# DOX framework

- DOX is highly performant AGENTS.md hierarchy installed here
- Agent must follow DOX instructions across any edits

## Core Contract

- AGENTS.md files are binding work contracts for their subtrees
- Work products, source materials, instructions, records, assets, and durable docs must stay
  understandable from the nearest applicable AGENTS.md plus every parent AGENTS.md above it

## Writing Standard

Every document in this repo, and every message an agent sends to a person, is
written in Simplified Technical English (ASD-STE100). Owner directive,
2026-08-26. The contract is **`docs/style_ste.md`**, and it is the only place
the rules live. The fence is `.claude/hooks/ste-lint.mjs`, which runs on every
markdown write and on every commit. Docs that predate the rule are grandfathered.
Text you add is not.

## Read Before Editing

1. Read this root AGENTS.md
2. Identify every file or folder you expect to touch
3. Walk from the repository root to each target path
4. Read every AGENTS.md found along each route
5. If a parent AGENTS.md lists a child whose scope contains the path, read that child and continue
6. Use the nearest AGENTS.md as the local contract and parent docs for repo-wide rules
7. If docs conflict, the closer doc controls local work details, but no child doc may weaken DOX

Do not rely on memory. Re-read the applicable DOX chain in the current session before editing.

## Place Before Building

Every new feature, module, tool, endpoint, or asset must land in its
architecturally-correct home **before** any code is written. Convenience of the
file you happen to have open is never a reason to place work elsewhere. Before
building:

1. Name the feature's kind — deployed service, dynamically-loaded agent,
   importable skill, shared package, infra, deploy, planning doc, or UI. The
   `apps/` split (services vs agents vs skills) and the packages list are
   load-bearing, not cosmetic — respect them.
2. Walk the Child DOX Index (below) and the nested indexes to find the scope
   that owns that kind. The nearest owning AGENTS.md is the target directory.
3. Verify the placement against Global Constraints and the local AGENTS.md
   contract (e.g. new event-driven execution defaults to MAF paths, not the
   Copilot-SDK runtime; secrets never live in agent/skill repos; UI work lives
   under `workbench/` and follows the design system).
4. Reuse the existing seam before adding a new one — extend the owning package,
   service, or skill rather than duplicating its capability in a closer folder.
   If no scope fits, the right move is a new scope with its own AGENTS.md, not an
   orphan file wedged into an unrelated tree.
5. If placement is genuinely ambiguous, state the candidate homes and the
   trade-off before writing code — do not default to the current directory.

A feature in the wrong layer is a defect even if it works. Correct the placement
first, then build.

## Update After Editing

Every meaningful change requires a DOX pass before the task is done.
Update the closest owning AGENTS.md when a change affects:
- purpose, scope, ownership, or responsibilities
- durable structure, contracts, workflows, or operating rules
- required inputs, outputs, permissions, constraints, side effects, or artifacts
- user preferences about behavior, communication, process, organization, or quality
- AGENTS.md creation, deletion, move, rename, or index contents

Update parent docs when parent-level structure, ownership, workflow, or child index changes.
Update child docs when parent changes alter local rules.
Remove stale or contradictory text immediately.

## Style

- Keep docs concise, current, and operational
- Document stable contracts, not diary entries
- Put broad rules in parent docs and concrete details in child docs
- Prefer direct bullets with explicit names
- Do not duplicate rules across many files unless each scope needs a local version
- Delete stale notes instead of explaining history

---

# Metorite -- Project Root

Organisation: Fracktal Works
Project: Metorite v2 -- Headless, self-mutating agent orchestration platform
Runtime: MAF (Microsoft Agent Framework) native, plus the GitHub Copilot SDK as a second runtime for interactive coworker chat + the self-mutation sandbox. No LangGraph. No deepagents. No n8n.
Last updated: 2026-08-09

## Purpose

Metorite is a headless, self-mutating, multi-agent orchestration platform
for running a company. Events trigger specialist agents dynamically loaded
from GitHub repos or local folders, executed via MAF (native) or the Copilot
SDK (interactive coworker sessions), and self-healing on failure via isolated
Copilot SDK sandboxes.

## Global Constraints (Non-Negotiable)

1. No in-app agent/skill *code* editing -- all code authoring is VS Code + Git. The Workflows app (`/workflows`) is the sanctioned exception-by-design: workflows are DB-persisted configuration orchestrating code-authored agents, compiled to MAF Workflows (ADR-028; spec project-docs/specs/workflows_app.md) -- not generated agent code, not a second runtime
2. No credentials in agent or skill repos -- Integration Registry holds all secrets
3. Self-mutation max_mutation_attempts = 1 per failure event
   - ⚠️ **DEV-ONLY / must be replaced before production:** native MAF agents (local_path, no own remote) currently land approved self-mutations by opening a PR against THIS Metorite monorepo. This is fine only while all agents are first-party and Metorite is WIP. It MUST be swapped for a tenant-isolated mechanism before any multi-tenant/customer deployment — third parties must never push to the shared monorepo. See `docs/DESIGN_LIMITATION_native_maf_mutation.md`. **This is now ticketed as `saas_multitenancy.md` MT-0b (WS-29) and is a HARD BLOCKER before customer #2** — the cheapest sufficient fix is a config gate defaulting to disabled, not a redesign.
4. No autonomous writes to source systems until Action Broker is live
5. Git is the single source of truth for all agent artefacts
6. MAF is the PRIMARY native agent runtime. The Copilot SDK is the supported second runtime for interactive coworker chat (Tier 1.5, /copilot/chat, BYOK-routed through the gateway) and the self-mutation sandbox -- not a general execution path for event-driven specialist agents
7. No Theia / browser IDE
8. Source systems are authoritative -- Metorite is a read-mostly mirror. **⚠️ AMENDED 2026-08-24 (D52.4): this is no longer true of PROJECT MANAGEMENT.** It still binds Zoho, Gmail and every future connector. It does **not** bind Projects: ClickUp is retired outright (no connector, no importer, no sync), and **Metorite is the project-management system of record** -- `pm_tasks` / `pm_task_personal` are the only task store in the product, and the Tasks app is a personal *lens* over them rather than a second store (D53). Do not build a "mirror" or a "sync" for anything under `pm_*`; there is nothing upstream to mirror. Board `WS-39`; owning section `project-docs/specs/project_management_app.md` §12.
9. New event-driven / specialist-agent execution features default to MAF paths; the Copilot-SDK runtime is reserved for interactive chat + mutation (both gateway-routed), not new autonomous execution entrypoints
10. **All gateway endpoints require auth, by construction rather than by opting in.** `require_authenticated` is attached app-wide at the `FastAPI(dependencies=[…])` level, so a route added tomorrow is covered without anyone remembering; `PUBLIC_ROUTES` is the exemption list and every entry authenticates itself another way. **Before building or modifying ANY app, read `project-docs/specs/user_management_contract.md`** — the ten binding rules for identity, membership and authorization, each one learned by breaking it. In particular: never navigate the browser directly at the gateway (it carries no credentials), never add a route to `PUBLIC_ROUTES` to make it reachable, and never take the acting identity from a query parameter or request body — **nor the acting TENANT, which is R11, added 2026-08-08 with D15; the tenant comes from the authenticated session or a tenant-scoped API key and from nowhere else.** The contract carries **eleven** rules, not ten.
11. **Multi-tenancy is `organization_id` + Postgres RLS, and it is NOT built yet.**
    The tenant boundary was re-taken on 2026-08-08 (**D15**, board **WS-29**, spec
    `project-docs/specs/saas_multitenancy.md`): a tenant is a **row** isolated by
    `FORCE ROW LEVEL SECURITY` bound at the `get_db()` seam; a deployment is a
    *placement*, not a boundary. This **supersedes `tenancy_and_visibility.md` §1 and §6**
    (one-deployment-per-tenant) — **§2–§5 of that document, the private → Center → org
    visibility ladder, are unchanged and still binding.** Before building anything that
    persists tenant data: read `saas_multitenancy.md` §1 and §11, and
    `saas_multitenancy_implementation.md` for the shapes. Two rules bind today, ahead of
    the build: **never introduce a second scoping doctrine** (tenant isolation is
    `organization_id`; visibility inside a tenant stays `email | group:<slug> | org`), and
    **never give an agent a raw-SQL tool or a database connection** — §0.9.3 makes that a
    condition on the whole tenancy decision, not a nicety. Board rule **R5**
    (`project-docs/work_plan.md` §1, owner-directed 2026-08-09) binds every PR
    tenant-ready by construction while WS-29 is in flight: new persisted tables satisfy
    the tenant-coverage gate (or are exempted with a reason), no new database-connection
    or Redis sites outside the seam/wrapper, and session acquisition stays on the seam
    idiom so the H2 conversion remains mechanical.

## Global Conventions

- Python 3.12+ with uv package manager (pyproject: requires-python >=3.12,<3.14; CI + prod run 3.12)
- FastAPI for all HTTP/WS endpoints
- Postgres + pgvector for entity graph, memory, audit, integrations
- Redis Streams for event bus
- Gateway /v1/chat/completions for LLM routing (keys from encrypted Postgres; no separate proxy)
- MAF native OTel for observability (OTLP-ready)
- Docker Compose for local dev and single-VM production
- Type hints required on all public functions
- async/await throughout -- no sync blocking in request paths
- Tests in tests/unit/ and tests/integration/ -- pytest with asyncio
- CI/CD via GitHub Actions: deploy.yml (push-to-deploy on main), pr-check.yml (lint+test on PRs)
- Deploy target: Hostinger KVM 4 VPS (Ubuntu 24.04 + Docker)
- **Control Plane UI is THEMED. Read `workbench/control_plane/DESIGN_SYSTEM.md`
  before writing any of it.** Settings → Appearance switches the whole org between
  RapidTool, Fluent, Material and Graphite, which disagree about palette, corner
  radius, icon pack, glass/glow and control behaviour (Material buttons are pills,
  Graphite's labels are uppercase). Three rules, all machine-checked by
  `src/lib/theme/conformance.test.ts`:
  1. **Never write a colour.** Use `bg-primary`, `text-foreground`,
     `border-border`, `var(--success)` — not `#0ea5e9`, `hsl(…)` or `bg-[#1a1b1e]`.
     Text on a coloured fill takes the `-foreground` partner, never `text-white`.
  2. **Never import `lucide-react`.** Use `<Icon name="Plus" />`; Lucide names are
     the vocabulary, the theme picks the pack.
  3. **Never hand-roll a control.** Use `Button`/`Input`/`Badge` from
     `src/components/ui/` — a theme's state layer, focus ring and label transform
     are not expressible in a class string, which is why the primitives exist.
  Also use the shared `Tabs`, `FilterPills` and page-header patterns from
  `src/components/` rather than inlining ad-hoc versions.
- **Apps that run in the sandbox** (Custom Apps, generative UI, React artifacts)
  inherit nothing from the shell — they get the `--cc-*` contract instead
  (`src/lib/theme/app-tokens.ts`, documented in
  `apps/agents/agent-app-builder/instructions.md`). Style with those tokens and
  the app follows the org's theme for life; write one hex value and that part of
  it leaves the design system permanently.
- Agent-generated artefacts (images, reports, PDFs) MUST be written to
  `inputs/`, `outputs/`, or `agent-data/` within the agent workspace so the
  Control Plane file browser and inline chat cards can discover them.  These
  three directories are the only ones visible in the Files Viewer sidebar;
  all other workspace files are hidden from the frontend user.  The workspace
  API exposes these directories but hides other paths.

## Harness Engineering Practices (binding for harness-touching work)

Metorite's orchestrator IS an agent harness. Any change touching the
agent loop, tool surface, context assembly, memory, HITL, streaming,
sub-agents, permissions, or evals must be informed by current harness
best practice:

- Reference index: github.com/ai-boost/awesome-harness-engineering
  (curated practices: context compaction, tool design, risk annotations,
  eval harnesses, observability, sandboxing). Consult it when designing or
  reviewing harness features; cite the practice you're applying or
  deliberately rejecting in the spec/PR.
- Our gap analysis + work queue: project-docs/specs/harness_hardening_2026-07.md
  (HH-1..8). Check it before starting harness work — the gap may already be
  queued, in-progress, or explicitly deferred with rationale.
- Competitor reference implementations: project-docs/specs/competitive_hardening_2026-07.md
  (CH-1..9, sourced from COMPETITIVE_COMPARISON.md) — proven patterns from Hermes
  Agent (fail-closed approval, container sandbox flags, self-improving Curator,
  typed sub-agent messaging) and OpenClaw (durable job queue, hub-and-spoke
  channels; and its CVEs as the cautionary case). Consult it for "what good looks
  like" when hardening security, plumbing, or multi-agent coordination.
- Multiplayer prior art: project-docs/specs/multiplayer_prior_art_qm_2026-08.md
  (QM-0..7, sourced from github.com/yc-software/qm) — a shipped multiplayer agent
  harness that independently reproduced our least-cleared-viewer rule. Consult it
  before building room concurrency (it folds a second turn into the live run as a
  steer rather than rejecting it), shared-room credentials (nothing ambient; every
  credential needs a grant to that room), or skill/prompt-budget work (index in the
  prompt, bodies read on demand). Reference-only: it owns no work item.
- Standing rules derived from it:
  1. New platform/agent tools declare risk annotations
     (acb_skills.tool_annotations: read_only/destructive/idempotent/open_world).
  2. Destructive or outward-facing actions FAIL CLOSED without a human
     (request_confirmation default; never auto-approve non-interactively).
  3. Harness behaviour changes ship with a golden trajectory eval in
     evals/trajectories/ (offline, CI-blocking) — not just unit tests.
  4. Keep per-agent tool surfaces small: use tool_scope (platform tools)
     and own_tool_scope (repo-baked tools) in config.json.
  5. Keep the system-prompt prefix byte-stable (cache-friendly); don't
     inject volatile content before the stable blocks.

## Package Versions

`uv.lock` is the single source of truth for pinned versions — do not maintain a
hand-copied table here (it drifts: the previous snapshot was stale on 3 of 6 pins).
Check with `uv tree` / `uv pip list`. Key runtime pins: `agent-framework-*`
(core / github-copilot / ag-ui / openai / redis) and `github-copilot-sdk`.

## User Preferences

- MD-only by default -- never auto-build .docx unless user explicitly asks
- Test before claiming done -- run pytest after code changes
- Document after building -- update AGENTS.md files (DOX pass) after meaningful changes
- No git push without explicit user request -- commit locally, mention what was committed
- Commit review cadence -- after every batch of ~5 commits (or any major feature/fix
  wave) on main, run a review pass over the changed files: check comments for accuracy
  and staleness, cross-references in docs, logic/type-hint correctness, and run
  `uv run python -m pytest tests/unit/ -x -q`. When a review is complete, mark it:
  `git tag review/YYYY-MM-DD && git push --tags`
  Check whether a review is currently due:
  `git log $(git describe --tags --match "review/*" --abbrev=0 2>/dev/null || echo "")..HEAD --oneline`
  A GitHub Actions workflow (`.github/workflows/review-reminder.yml`) auto-opens a
  reminder issue after every 5th unreviewd commit; close the issue after the review tag
  is pushed.

## Child DOX Index

| Scope | Path | Covers |
|---|---|---|
| Application services | apps/AGENTS.md | Gateway, orchestrator, ingestion, email_ingestion, reconciler, action_broker |
| Shared packages | packages/AGENTS.md | acb_skills, acb_llm, acb_memory, acb_graph, acb_common, acb_audit, acb_auth |
| Skills | skills/AGENTS.md | Skill definitions and SKILL.md patterns |
| Infrastructure | infra/AGENTS.md | Docker Compose, Postgres, LLM tier config, Redis |
| Deployment | deploy/AGENTS.md | Hostinger VPS deployment |
| Planning docs | project-docs/AGENTS.md | Product requirements, project plan, architecture |
| Marketing site | site/AGENTS.md | Static apex page (`metorite.com`); owner spec `project-docs/specs/marketing_site.md` (WS-33) |
| Workbench UI | workbench/AGENTS.md | Control Plane (Next.js) and local dev tools |
| Operator Console | workbench/operator_console/AGENTS.md | CP-8's SEPARATE staff-only cross-org customer console (D35); theming-exempt; ships dark |
