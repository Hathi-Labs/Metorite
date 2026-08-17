# Metorite — Self-Anneal Agent

This is the Metorite orchestration platform repository.  It runs as a
headless, self-mutating multi-agent platform on MAF (Microsoft Agent Framework).

## Key facts

- **Python 3.11+** with `uv` package manager
- **FastAPI** for all HTTP/WS endpoints (gateway at `apps/gateway/`)
- **Postgres + pgvector** for entity graph, memory, audit, integrations
- **Redis Streams** for event bus and stream relay
- **Next.js** (App Router) for the Control Plane workbench (`workbench/control_plane/`)
- **Docker Compose** for local dev and single-VM production
- **DOX framework**: read the AGENTS.md chain before editing any file

## Entry points

- `agents.py` — MAF agent definition (build_agents entry point)
- `config.json` — Metorite agent contract
- `.github/prompts/system.md` — full system prompt
- `AGENTS.md` — root DOX document (global constraints, conventions, child index)

## Before editing

1. Read root `AGENTS.md`
2. Follow the DOX chain to the target file
3. Read the nearest `AGENTS.md` for local rules
4. Run `uv run python -m pytest tests/unit/ -x -q` after changes

## Control Plane UI (Next.js)

When editing any file under `workbench/control_plane/`:

- **Read** `workbench/control_plane/DESIGN_SYSTEM.md` first — it defines the
  unified UI/UX standards (colors, typography, spacing, component patterns).
- **Use shared components** from `src/components/`:
  - `Tabs` — for tab navigation (segmented or underline variants)
  - `FilterPills` — for filter/chip-style list filtering
  - Never inline ad-hoc tab bars, filter pills, or page headers.
- **Use semantic color tokens** (`bg-primary`, `text-foreground`, `border-border`,
  etc.) — never arbitrary hex values or `bg-[#1a1b1e]`.
- **Match the page layout pattern**: header → tabs/filters → content.
- Run `npx next build` from `workbench/control_plane/` to verify after changes.

## Terminal output — prefix noisy commands with `rtk`

`rtk` (Rust Token Killer) filters verbose command output before it reaches the
model, cutting ~60–90% of the tokens on common dev commands with no loss of the
signal (failures, diffs, status). In Claude Code a hook applies this
automatically; **in Copilot Chat you must write `rtk` yourself** because the
Chat host does not rewrite terminal commands.

When running any of these in the terminal, prefix the command with `rtk`:

- Tests: `rtk pytest ...`, `rtk jest ...`, `rtk vitest ...`, `rtk go test ...`
- Lint/type: `rtk ruff ...`, `rtk mypy ...`, `rtk tsc ...`, `rtk eslint ...`
- VCS: `rtk git status`, `rtk git diff`, `rtk git log`
- Infra: `rtk docker ...`, `rtk kubectl ...`, `rtk psql ...`

Do **not** use `rtk` for:

- `uv run ...` — `rtk` is a standalone binary, not a uv tool; run tests as
  bare `rtk pytest ...` (or `rtk pip ...`, which auto-detects uv), never
  `uv run rtk ...`.
- Reading files — prefer the codegraph MCP and the editor's file tools over
  `rtk read`; they give better, symbol-aware context.

If `rtk` is not installed (`rtk --version` fails), run commands normally;
install with `winget install rtk-ai.rtk` (or see https://www.rtk-ai.app).

## Code intelligence — use the codegraph MCP

A `codegraph` MCP server is configured in `.vscode/mcp.json` (same server Claude
Code uses). It is a pre-indexed knowledge graph of every symbol, edge, and file.
Prefer one `codegraph` query over a grep + read loop when locating code,
understanding call paths, or checking a change's blast radius — it returns the
verbatim source plus who calls it, in far fewer round-trips.

## Commit review cadence

After every batch of ~5 commits (or any major feature/fix wave) on `main`, run a
review pass before moving on:

1. `git log <last-review-tag>..HEAD --oneline` — list the unreviewd commits.
2. Check each changed file for: stale/misleading comments, broken cross-references
   in docs, logic correctness, and type-hint accuracy.
3. Run `uv run python -m pytest tests/unit/ -x -q` to confirm nothing regressed.
4. Mark the review done: `git tag review/YYYY-MM-DD && git push --tags`

**Checking if a review is due** (run this after pushing to main):
```bash
rtk git log $(git describe --tags --match "review/*" --abbrev=0 2>/dev/null)..HEAD --oneline
```
If 5 or more commits are listed, a review is due. A GitHub Actions workflow
(`.github/workflows/review-reminder.yml`) also auto-opens a reminder issue when
the threshold is crossed — close the issue once the review tag is pushed.
