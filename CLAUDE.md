# Metorite — read this before your first tool call

You are working on **Metorite**: an AI-driven company operating system,
now also sold as multi-tenant SaaS. This file is loaded into every session,
including cloud and headless ones. It is a **router plus the few facts you must
know before reading anything else** — it deliberately duplicates nothing.

---

## 1. Where truth lives (read in this order)

| # | File | What it owns |
|---|---|---|
| 1 | **`project-docs/INDEX.md`** | Which specs are **ACTIVE** (you may build from these), which are deferred/historical (you may not). A spec missing from INDEX is a defect — say so. |
| 2 | **`project-docs/work_plan.md` §1** | The agent-ready spec contract + standing rules **R1–R8**. Binding on every PR. |
| 3 | **`project-docs/work_plan.md` §2** | The dispatch board: every workstream, its state, its gates. **This is the only current-state authority.** |
| 4 | **`project-docs/work_plan.md` §6** | The owner-gate registry. Actions you must **refuse by name**. |
| 5 | **`project-docs/work_plan.md` §3** | Decisions **D1–D39**. Recorded once, never re-litigated. Cite them; do not reopen them. |
| 5a | **`project-docs/HANDOFF.md`** | The cross-session **queue of actions** (D39) — injected at session start, so pending work is carried by the repo rather than by anyone's memory. ⚠️ **Actions, never state** — row 3 stays the only current-state authority. Run each entry's **Check** first and **delete** the ones that pass; `/handoff` is the workflow. |
| 6 | **`project-docs/specs/engineering_practice.md`** | *How* we build: environments, release rings, migrations, testing, agent work-partitioning, security, definition of done. |
| 7 | The **owning spec** named by your board row | Scope and acceptance for the thing you are building. |

For ordering and ownership, `work_plan.md` wins over every spec.
`project-docs/` = the plan and the product specs. `docs/` = engineering
reference tied to code. Do not put product specs in `docs/`.

## 2. The architecture, in one screen

- **⚠️ Centers are WITHDRAWN FROM THE SURFACE (D49, 2026-08-24).** The nav
  section is gone and nothing navigates to a Center. **The code is not** —
  `lib/centers.ts`, `/centers/<slug>`, the `center.*` features and the
  `group:<slug>` slice grants (D12) all stay, because the Center stopped being a
  *destination* and is still the *scoping primitive* the live Projects grant
  model rests on. Do not delete them; do not link to them.
  `department_centers.md` is now a design record (WS-13/14/15/16 parked).
- **Apps** are the surfaces, in four sections: **Personal Center** (the
  per-user category, kept by name), **Apps**, **AI Studio**, **Admin**.
  **Exactly eight panes are live**; every other pane is `preview` — routes,
  API and tests intact, nav entry absent. The allowlist of record is
  `specs/launch_surface.md` §2, mirrored in `src/lib/nav.ts`, and `nav.test.ts`
  fails if the two disagree. `preview` is **not** a permission: never revoke a
  feature to hide an app.
- **Pricing is FLAT: ₹500/user/month + AI credits**, one sellable seat
  (`core`), everything live included. Center packages, add-ons and Complete are
  retired. `specs/launch_surface.md` §4 is the shape of record;
  `saas_multitenancy.md` §2.4b is the superseded D23/D24 record. Still binding:
  D19.3's hard cap, D32.5's three counts, the entitlement seam.
- **Tenancy is a ROW, not a deployment.** `organization_id` + Postgres FORCE ROW
  LEVEL SECURITY bound at the `get_db()` seam. We are **multi-tenant from
  customer #1**; "silo" describes *placement* only. There is no phase in which
  single-tenant code is acceptable. (D15)
- **Visibility inside a tenant** is private → Center → org, plus `group:<slug>`
  grants. Tenancy is *which company*; visibility is *who inside it*. Two
  mechanisms, two axes, no third. (D12)

## 3. Non-negotiables — these bind before you touch anything

1. **Never commit or push on `main`.** Cut a branch first. (`plan-guard.mjs`
   enforces this and will block you.)
2. **Refuse owner-gated work by name** (§6): live credentials, VPS/deploy
   reach, force-push, member/role writes, enforcement flips, cutovers,
   production one-offs. Build the thing, write it up, stop, hand it over.
3. **R5 — tenant-ready by construction.** New tables are tenant-scoped; no new
   DB connection sites outside the seam; Redis keys go through the prefix
   wrapper; use the existing session idiom; never trust a tenant or identity
   from request input.
4. **R6 — expand/contract migrations.** The deploy applies migrations *before*
   restarting services, so old code always meets new schema. Nullable with
   defaults; never rename in place; tighten in a later release. **We cannot roll
   back** — only roll forward or restore.
5. **R7 — name the fence.** A rule you introduce must name the test that makes
   breaking it fail, or be labelled advisory.
6. **R8 — verify SQL against a real database.** Hermetic fakes agree with
   whatever SQL they are handed; five live bugs shipped green that way.
7. **R1 — migration numbers are taken at build time and re-checked at merge.**
   Three collisions in two weeks.
8. **Verify delivery by evidence, never by a green job.** Migration ledger
   lines, the deployed SHA, the log line. Four deploys once reported success
   while shipping nothing.

## 4. How development proceeds

**Feature by feature, app by app — one narrowed slice at a time.** The loop
(`/next-ticket`, defined in `.claude/commands/next-ticket.md`):

> **audit** the spec is dispatchable → **implement** one narrowed slice →
> **verify** independently against the acceptance criteria → **review** the diff
> adversarially → **open a PR** → **stop before merge**.

Rules that make it work:

- **The verifier is never the implementer**, and re-derives facts from the code
  rather than trusting the write-up.
- **The reviewer hunts for the case where this is wrong.** An agent asked "is
  this correct?" says yes.
- **Re-verify every anchor at dispatch** (file paths, line numbers, migration
  numbers). Specs go stale; the code is the fact.
- **Respect the seams.** Extend the shared seam, never add a parallel one: one
  DB engine (`acb_common.db`), one entitlement intersect, one subject-grammar
  validator, one task store, one Center registry (`lib/centers.ts`), one status
  colour vocabulary (`src/lib/statusAccent.ts`). A second implementation of an
  existing seam is a defect, not a feature.
- **The UI is one product, themed centrally.** Every app is a projection, never
  a surface with its own look: no app-local palette, no second colour
  vocabulary, no hand-rolled control. `workbench/control_plane/DESIGN_SYSTEM.md`
  is the contract and `AGENTS.md` beside it carries the eight rules and their
  fences — both are auto-loaded when you touch UI code. Owner directive
  2026-08-10. Categorical hues (contexts, tags, labels) go through the
  `--cat-1…8` ramp via `src/lib/categorical.ts`, never a raw Tailwind palette
  class. Headless primitives come from `src/components/ui/` — `@base-ui/react`
  is the one substrate (D-PM-15) and `Modal.tsx` is the only file allowed to
  import it. The conformance suite checks eight regexes and **nothing tests layout
  or cross-app continuity**, so the theme-switch check (Fluent → Material →
  Graphite, on your surface *and* its neighbour) is the real gate.
- **Keep branches short and integrate often.** Long branches are the root cause
  behind the migration-renumber collisions, the green-alone/red-together PRs and
  a duplicated tenancy design. Three or four in flight is the ceiling.
- **Ship dark.** New behaviour lands behind a flag, default OFF; flipping it is
  usually the owner's act.

## 5. What not to do

- **Do not re-litigate decisions.** D1–D31 are taken. If one looks wrong, say so
  and stop — do not build against your own alternative.
- **Do not refactor the tree to conform** to R6/R7/R8. Those bind *new and
  changed* work. Existing violations are findings for the board.
- **Do not invent a second way to do an existing thing** — a second session
  idiom, a second grant vocabulary, a second status doc. Mirrors go stale and
  then lie; that is why one owner is named per topic (`work_plan.md` §4).
- **Do not mark work done from the write-up.** Run the owning spec's own
  verification commands.
- **Do not add product specs to `docs/`** or leave a new spec out of
  `INDEX.md` — a spec enters the index in the PR that creates it.

## 6. Environment notes that will bite you

- Windows is the primary dev box: pass `encoding="utf-8"` explicitly when
  reading files in tests and scripts; cp1252 is the default and it crashes.
- **Two recorded pytest hazards, both narrower than they sound:** the board
  warns "never run `tests/unit/` as a directory — `test_memory_integration.py`
  hangs" (WS-9) and "never `pytest tests/unit -k calendar`" (WS-21). Measured
  2026-08-10: a directory run *with* a `-k` filter that deselects those suites
  completes fine, so what hangs is running **those** tests, not collection
  itself. Prefer naming the files you mean; if you run the directory, keep the
  memory and calendar suites deselected.
- `uv run pytest …` is the runner. Frontend: `npx tsc --noEmit && npx vitest run`
  in `workbench/control_plane`.
- `.claude/` (agents, commands, hooks, settings) is **tracked** — every checkout
  inherits the same review loop and the same refusals (D29). Local-only and
  derived paths stay ignored; the CodeGraph index is rebuilt, never committed.
