# `project-docs/` — the plan and the product specs

**Scope:** the planning documentation of Metorite. This folder holds the work
plan, the specification of each app, and the architecture records. It holds no
code. The parent contract is the root `AGENTS.md` (DOX).

## Read this first

1. **`INDEX.md`** classifies every spec. Build only from a spec that it lists
   as **ACTIVE**. A DEFERRED or HISTORICAL spec dispatches nothing.
2. **`work_plan.md`** owns order and state. §1 holds the spec contract and the
   standing R rules. §2 is the dispatch board, and §3 holds the decisions. §6
   is the owner-gate registry. For ordering and ownership, it wins over every
   spec.
3. **The owning spec** that your board row names. Its status header is the
   completion record for that feature (R4).
4. **`HANDOFF.md`** is the queue of cross-session actions. It holds actions,
   never state.

This file owns no status, no spec list and no decision range. Those live in
the files above. A copy in this file would go stale.

## The two documentation roots

- **`project-docs/`** (this folder): the plan, the product and app specs, and
  the architecture.
- **`docs/`** (repo root): engineering reference tied to code. Do not put a
  product spec there.

## Rules for this folder

- **A new spec enters `INDEX.md` in the PR that creates it.** A spec that
  INDEX does not name is a defect. Fence: `tests/unit/test_index_completeness.py`.
- **A spec that stops binding gets a banner**, and INDEX moves it to DEFERRED
  or HISTORICAL.
- **The global constraints live in the root `AGENTS.md`.** Do not copy them
  here.
