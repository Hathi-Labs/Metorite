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
   contract. For example, new execution uses MAF, and secrets never live in
   agent or skill repos. UI work lives under `workbench/` and follows the
   design system.
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

Organisation: Hathi-Labs (Fracktal Works is customer zero, D36)
Project: Metorite -- headless, self-mutating agent orchestration platform
Runtime: MAF (Microsoft Agent Framework). No LangGraph. No deepagents. No n8n.

## Purpose

Metorite is a headless, self-mutating, multi-agent orchestration platform
for running a company. Events trigger specialist agents that load at run time
from GitHub repos or local folders. The agents run on MAF, and an isolated
mutation sandbox repairs an agent after a failure.

## Global Constraints (Non-Negotiable)

1. No in-app agent/skill *code* editing -- all code authoring is VS Code + Git. The Workflows app (`/workflows`) is the sanctioned exception-by-design: workflows are DB-persisted configuration orchestrating code-authored agents, compiled to MAF Workflows (ADR-028; spec project-docs/specs/workflows_app.md) -- not generated agent code, not a second runtime
2. No credentials in agent or skill repos -- Integration Registry holds all secrets
3. Self-mutation max_mutation_attempts = 1 per failure event
   - A native MAF agent lands an approved self-mutation as a PR against THIS
     monorepo. Only a first-party organization may do that. Third parties
     must never push to the shared monorepo.
   - The gate is MT-0b: `organization.first_party` defaults to FALSE
     (migration 157), and `SELF_MUTATION_DISABLED` turns the mechanism off for
     a whole deployment. Fence: `tests/unit/test_mt0b_self_mutation_containment.py`.
     Background: `docs/DESIGN_LIMITATION_native_maf_mutation.md`.
4. No autonomous writes to source systems until Action Broker is live
5. Git is the single source of truth for all agent artefacts
6. MAF is the ONLY agent runtime (D84, D92). Do not build a new Copilot SDK
   path. A new agent builds a MAF `Agent`. The WS-43 slices remove the Copilot
   uses that remain (`project-docs/specs/maf_coding_engine.md` §15).
   `tests/unit/test_no_copilot_sdk.py` refuses a new one.
7. No Theia / browser IDE
8. Source systems are authoritative, and Metorite is a read-mostly mirror of
   them. This binds Zoho, Gmail and every future connector.
   **Project management is the exception (D52.4).** Metorite is its system of
   record, and ClickUp is gone. `pm_tasks` and `pm_task_personal` are the only
   task store, and the Tasks app is a lens over them (D53). Do not build a
   mirror or a sync for anything under `pm_*`. A one-shot FILE importer may
   bring work in (D80, `project-docs/specs/project_import.md`). Owning
   section: `project-docs/specs/project_management_app.md` §12.
9. Every new execution feature uses MAF (D84). See constraint 6.
10. **All gateway endpoints require auth, by construction.**
    `require_authenticated` is attached app-wide at the `FastAPI(dependencies=[…])`
    level, so it covers a new route with no extra step. `PUBLIC_ROUTES` is the
    exemption list, and every entry authenticates itself another way.
    **Before you build or change ANY app, read
    `project-docs/specs/user_management_contract.md`.** It holds the binding rules
    for identity, membership and authorization. Never point the browser at the
    gateway, because it carries no credentials. Never add a route to
    `PUBLIC_ROUTES` to make it reachable. Never take the acting identity or the
    acting TENANT from a query parameter or a request body (contract R11). The
    tenant comes only from the authenticated session or a tenant-scoped API key.
11. **Multi-tenancy is `organization_id` + Postgres FORCE ROW LEVEL SECURITY,
    bound at the `get_db()` seam (D15).** A tenant is a **row**. A deployment is
    a *placement*, not a boundary. `project-docs/specs/saas_multitenancy.md` is
    the architecture of record, and `saas_multitenancy_implementation.md` holds
    the shapes. It supersedes `tenancy_and_visibility.md` §1 and §6. That
    document's §2–§5, the private → Center → org visibility ladder, still binds.
    - **Never introduce a second scoping doctrine.** Tenant isolation is
      `organization_id`. Visibility inside a tenant stays
      `email | group:<slug> | org`.
    - **Never give an agent a raw-SQL tool or a database connection**
      (`saas_multitenancy.md` §0.9.3).
    - **R5** (`project-docs/work_plan.md` §1) binds every PR tenant-ready by
      construction. A new persisted table passes the tenant-coverage gate
      (`tests/unit/test_tenant_coverage.py`) or carries an exemption with a
      reason. Add no database-connection or Redis site outside the seam or
      the wrapper.

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
- CI/CD on GitHub Actions. `pr-check.yml` lints and tests each PR.
  `deploy.yml` moves the `staging` ref on each merge, and ships production once
  a day or by a manual dispatch (root `CLAUDE.md` §4).
- Deploy target: Hostinger KVM 4 VPS (Ubuntu 24.04 + Docker)
- **The Control Plane UI has ONE look.** Read
  `workbench/control_plane/DESIGN_SYSTEM.md` and
  `workbench/control_plane/AGENTS.md` before you write any of it. They hold the
  rules and their fences.
- **Apps that run in the sandbox** (Custom Apps, generative UI, React artifacts)
  inherit nothing from the shell. They get the `--cc-*` contract instead
  (`src/lib/theme/app-tokens.ts`, documented in
  `apps/agents/agent-app-builder/instructions.md`). Style with those tokens. One
  hex value takes that part of the app out of the design system for good.
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
(core / github-copilot / ag-ui / openai / redis). `github-copilot-sdk` stays in
the lock until WS-43 removes its last use (D84).

## User Preferences

- MD-only by default -- never auto-build .docx unless user explicitly asks
- Test before claiming done -- run pytest after code changes
- Document after building -- update AGENTS.md files (DOX pass) after meaningful changes
- Branch, push and merge follow root `CLAUDE.md` §3 and §4. Never commit or push on `main`.
- Commit review cadence -- after every batch of ~5 commits (or any major feature/fix
  wave) on main, run a review pass over the changed files: check comments for accuracy
  and staleness, cross-references in docs, logic/type-hint correctness, and check
  that the deploy run's `Unit tests (a signal on a push, a gate on a release)` job is green. When a review is complete, mark it:
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
| Planning docs | project-docs/AGENTS.md | The plan and the product specs. `project-docs/INDEX.md` is the spec index |
| Marketing site | site/AGENTS.md | Static apex page (`metorite.com`); owner spec `project-docs/specs/marketing_site.md` (WS-33) |
| Workbench UI | workbench/AGENTS.md | Control Plane (Next.js) and local dev tools |
| Operator Console | workbench/operator_console/AGENTS.md | CP-8's SEPARATE staff-only cross-org customer console (D35); theming-exempt; ships dark |
