# Engineering practice — how we build with live customers and agent authors

**Status:** DOCTRINE — binding · **Date:** 2026-08-10 · **Owner:** vjvarada ·
**Decision:** D28 (`work_plan.md` §3) · **Enforced rules:** R6, R7, R8
(`work_plan.md` §1) · **Classification:** CONTRACTS & DOCTRINE (`INDEX.md`)

**What this owns:** *how* we build — environments, release, migrations, the
division of work between agents, what a test has to be worth, and the security
posture once the users are not colleagues. It owns **no tickets**; the board owns
order, the owning specs own scope. Where it states a rule that binds, that rule
is R6/R7/R8 in `work_plan.md` §1 and this file is its reasoning.

**Written from measured failures in this repository, not from general advice.**
Every claim below cites something that actually happened here. That is
deliberate: an agent reading generic best practice will apply it generically.

---

## 0. The two premises everything else follows from

**0.1 — Our failures are delivery and integration failures, not coding
failures.** The evidence, all from a two-week window:

| What failed | What would have caught it |
|---|---|
| Migration number collisions, **three times** (WS-10, #403's 158, #399's 157–159) | Short-lived branches; a collision check |
| Two PRs green alone, **red together** (#399's cascade-map fence × #403's new table) — twice in one day | Continuous integration into main, not long branches |
| Four deploys reported success **while shipping nothing** (2026-08-07) | A deployed-SHA check, not a health check |
| `ALTER TABLE if` sat in the hand-run production promotion script | A fence comparing the artifact to reality (added 2026-08-10) |
| Migration 148 broke three write routes that predated it | Expand/contract (R6) |
| Five SQL bugs invisible to hermetic fakes, instant against real Postgres | Real-database verification (R8) |

None of these is a unit-test gap. **Spend effort on the seams between changes,
not on more coverage inside them.**

**0.2 — A rule binds an agent only when a test refuses to break it.** R5 already
says this out loud ("each enforced by an existing test, not by prose"). Generalise
it: prose in a spec is advisory and will be violated by the next agent who does
not read that paragraph; a red build is not negotiable. This is why the
structural fences — the cascade map derived from the migrations, the tenancy
ratchets, the AST path-fence checks — are the highest-value tests in the tree.
They catch *other agents' future work*, which is the entire alignment problem.

That premise is now **R7**: an architectural rule must name the test that
enforces it, or be labelled advisory. "We should always…" is not a rule.

---

## 1. Environments and the test database

> ⚠️ **AMENDED 2026-08-26 by D55 — this section's conclusion is superseded; its
> reasoning is not.** The paragraph below is correct **for the premise it names**:
> *one box, single-digit customers, one developer.* The owner is changing all three,
> so it is retired **with its premise** rather than overruled on taste. The ladder we
> run is now local/CI → **staging** → production, and staging is built the way this
> section's own **P-1** describes — a nightly re-derivation from production's
> anonymised dump, not a maintained copy — which is what answers the drift objection
> instead of ignoring it. **P-1 and "a staging environment" are the same object.**
> Owning spec: `specs/development_and_delivery_framework.md` §3 (ADOPTED, board
> **WS-38**); the parity contract is its §3.4.
>
> ⏳ **In force until it isn't: PHASE 0.** Until customer #2, a second contributor, or
> H3 — whichever comes first — **we work directly in production** (D55.2). So for
> today the paragraph below still describes what we run; §3.5 of the framework carries
> the three end-triggers. ⚠️ Everything else in *this* document binds throughout, and
> §2's **ship dark** binds harder: with no staging in front of production, a flag
> defaulting OFF is the only thing between a merge and an incident.

**We do not run a classic dev → staging → prod triple, and should not.** At one
box and single-digit customers it buys drift (a staging environment nobody keeps
truthful) more than safety.

**What we run instead:**

1. **Local hermetic** — the unit suite, no database. Fast, and where most tests
   live. ⚠️ Its fakes agree with whatever SQL they are handed (see §4).
2. **Local scratch Postgres** — the `mt-scratch` pattern (:5433, full ladder
   applied). This is where migrations get proven before they are believed.
3. **Production** — one box, with the box's own 5-minute pull timer as a second
   delivery path beside the deploy workflow.

**The missing environment, and the one worth building: a production-SHAPED
restore.** CI replays the migration ladder from zero, which proves the ladder is
*self-consistent*. It does not prove the next migration survives contact with
real data — and that is exactly where the failures came from (148's cast, the
tags migration's `LATERAL` scope error, the People write routes). We already own
every ingredient: a nightly dump, a rehearsed restore (2026-08-05), and the
scratch-database pattern.

> **Practice item P-1 (proposed, not yet a board row): rehearse pending
> migrations against a restore of last night's production dump**, in CI or on a
> schedule. Anonymise before it leaves the box. This single job would have caught
> more of this month's incidents than any other change on this page.

## 2. Release: deploy is not release

**Deploy** = the code is on the box. **Release** = a customer can see the
behaviour. Keep them separate, because that separation is what makes a bad merge
a non-event.

- **Deploy continuously on merge to main.** Already true.
- **Ship dark.** Every feature lands behind a flag, defaulting OFF, and the flip
  is a deliberate act — for anything outward-facing or spend-bearing, an
  **owner-gated** one (`work_plan.md` §6). This is already our habit and it is
  the main reason merges rarely become incidents. Keep it.
- **Release in rings, not broadcasts.** Ring 0 is **us** — Fracktal is customer
  #0 and dogfooding is a free canary. Ring 1 is one friendly customer. Ring 2 is
  everyone.

  > ⚠️ **"Silo" is about PLACEMENT, never about architecture. We are
  > multi-tenant from customer #1.** `saas_multitenancy.md` §5.1 condition 2 is
  > unambiguous: *every silo runs the pooled schema, with `organization_id`
  > populated and **RLS enabled from day one**, even though the database holds
  > one tenant — a silo is a pooled deployment with N=1, and cutover is a data
  > move rather than a migration. Skipping this is what turns the bridge into a
  > rewrite.* There is **no phase in which single-tenant code is acceptable**;
  > D15 demoted the deployment from "the tenant boundary" to "a placement, a
  > priced tier", and R5 binds every PR regardless of customer count. What
  > changes at the 8–12 cutover is only N.

  While customers 1–5 are separately *placed*, the rings are physical and cost
  nothing — an advantage of the bridge rather than overhead to escape. After the
  pooled cutover a ring is a per-org feature flag, which **D25.2** already fixed
  as the only version-skew mechanism we support (one codebase, one schema, always
  latest).
- **A fixed clock is optional; the ring order is not.** "Midday every day" is
  fine, and matters far less than "us first, then one, then all".

## 3. Migrations — the sharpest edge we have

**⚠️ The deploy applies migrations BEFORE restarting the services.** There is
therefore always a window in which the *old* code runs against the *new* schema.
With one internal user that is a shrug. With customers it is an outage. So:

**R6 — expand/contract, always.** A migration must be compatible with the code
that is *currently running*:

- Add columns **nullable with a default**; never `NOT NULL` in the same deploy as
  the code that fills them.
- **Never rename in place.** Add the new name, backfill, switch readers, drop the
  old one in a *later* release.
- A unique constraint or CHECK over existing data lands `NOT VALID` and is
  validated in a guarded block — migration 148 already does this correctly and is
  the reference (it quarantines duplicates rather than failing
  `CREATE UNIQUE INDEX` and blocking the deploy).
- The contract half (drop, tighten) is a **separate, later** migration. Two
  releases, never one.

**Rollback: we cannot, and should stop implying we can.** A forward-only numbered
ladder with no blue/green means recovery is **roll forward or restore from
backup** — not `git revert`. That is an acceptable choice at this scale, and it
makes two things non-negotiable before customer #1: an off-box backup copy, and a
deploy whose result we can actually verify (§6).

**Numbering.** Take the next free number **at build time**, never earlier (R1),
and re-check it at merge. Three collisions in two weeks is a signal about branch
lifetime (§5), not about carelessness.

**The hand-run promotion set** (`infra/postgres/generated/`) is applied by hand
against production in a maintenance window. It is therefore held to a higher
standard than a numbered migration, not a lower one: regenerate it in the PR that
adds a table, and let
`test_tenant_coverage.py::test_the_generated_set_on_disk_matches_the_tables_that_exist`
prove the artifact matches reality. That fence exists because the committed set
contained a statement that could not run.

## 4. Testing — what a test is worth when an agent wrote it

**An agent will always produce a passing test.** Coverage is therefore close to
meaningless as a signal here, and the suite's job changes: it must be *hard to
satisfy vacuously*.

- **Verified red first.** A test that has never failed has never been shown to
  test anything. Required for every fence and every bug fix.
- **Mutation testing** for anything touching money, auth, tenancy or an outward
  write: change the logic, watch the test go red, revert. We already do this
  ("N mutants red and reverted") more rigorously than most commercial teams —
  it is the correct answer to "the tests pass, but do they test anything?", and
  it is also how we found tests that were passing for the wrong reason.
- **R8 — verify SQL against a real database.** Hermetic fakes agree with whatever
  SQL they are handed, which is why five live bugs shipped green: a `CAST(:param
  AS timestamptz)` asyncpg refuses to encode, a fake matching `lower(col) =
  :param` against NULL (which SQL never does), an implicit-comma `LEFT JOIN`
  whose `ON` clause could not see its table. Any change whose subject is a query,
  a migration, or a predicate is verified against a real Postgres before it is
  believed.
- **Prefer structural fences to example tests** when the rule is general. A test
  that reads the whole tree and asserts an invariant ("every module declaring a
  route is imported", "the cascade map equals what the migrations declare",
  "no new engine outside the seam") defends against agents who have not read the
  spec — which is all future agents.
- **Keep the eval locks.** `propose()`, the capability ranking, and the golden
  cases are EVAL-LOCKED because an LLM-shaped "fix" can silently degrade quality
  in a way no assertion catches.
- **Fakes are a convenience, never the fence.** If the seam under test is
  mocked, the test proves the mock. (The attachment suite passed 25 green while
  every upload answered 422, because `record_activity` was monkeypatched.)

## 5. Dividing work between agents

**Partition by seam, not by feature size or file count.** Two agents may run in
parallel only when neither can touch the other's contract. When they must share
one — the work plan, the entitlement seam, a fence that enumerates tables —
**serialise them**. The merge cost exceeds the parallelism gain, and the failure
mode is not a conflict marker but two changes that are each correct and jointly
wrong (#399 × #403, twice in one day, both caught only at merge).

**Keep branches short and integrate into main constantly.** The branch that
carried WS-27 o–z lived for days and grew its own parallel tenancy thread, its
own migration numbers, and its own handover document. It merged, but only after a
human sat and reconciled it. **Long branches are the root cause behind the
renumber trap, the cross-PR fences, and the duplicated multi-tenancy design** —
three distinct symptoms, one cause.

**Cap work in flight.** The constraint is not agent capacity; it is integration
bandwidth, and that is one person. Three or four open branches is the ceiling. A
queue of six (2026-08-09) is above it.

**The supervisor loop's shape is right and should not be diluted:** audit the
spec (is it dispatchable?) → implement one narrowed slice → **independently**
verify against the acceptance criteria → adversarially review the diff → open a
PR and **stop before merge**. Two properties make it work:

1. **The verifier must not be the implementer**, and re-derives its facts from
   the code rather than trusting the write-up.
2. **The reviewer is adversarial by construction.** An agent asked "is this
   correct?" answers yes. An agent asked "find the input where this is wrong"
   finds it. Prompt for refutation, never for confirmation.

**Anchors are re-verified at dispatch, never trusted from authoring time** (the
§1 contract already says this; it is here because it is the most common way an
agent builds against a file that moved).

## 5.1 The harness travels with the repo (D29)

**The development philosophy is only real where it is installed.** Until
2026-08-10 `.claude/` was entirely gitignored, so every checkout that was not the
owner's laptop — **a cloud Claude instance, a fresh clone, a headless run, an
agent's own worktree** — ran with *no* `plan-guard`, *no* supervisor-worker agent
definitions and *no* `/next-ticket`. The owner-gate registry (§6 of the work
plan) was being enforced by a file that travelled by accident.

**Tracked, because it is doctrine in executable form:** `.claude/AGENTS.md` ·
`.claude/agents/` (spec-auditor, ws-implementer, ws-verifier, diff-reviewer) ·
`.claude/commands/` · `.claude/hooks/` (`plan-guard.mjs` + its test, `rtk-bash.sh`)
· `.claude/settings.json` (wires the hooks; contains no secrets).

**Still ignored, because it is local or derived:** `settings.local.json`
(per-machine permissions), `worktrees/` (ephemeral full checkouts),
`scheduled_tasks.lock`, `skill-observations/`, and `skills/` (third-party skill
bundles — tooling a developer installs, not project doctrine).

**Derived indexes are rebuilt, never committed.** The CodeGraph symbol index
(`.codegraph/`) stays ignored: it is a per-checkout SQLite database that would be
large, churn on every commit and conflict on every merge. Its *config*
(`.mcp.json`, `codegraph.json`) is tracked, so a fresh environment gets the tool
and builds its own index. **Anything derivable from the source is rebuilt on the
new machine; only the sources of truth are tracked.**

**The fence (R7):** `node .claude/hooks/plan-guard.test.mjs` runs blocking in
`pr-check`. Break the guard and CI is red — rather than the guard quietly
permitting everything on a machine nobody is watching.

## 6. Security once the users are not colleagues

The threat model changes completely at customer #1, and
`saas_multitenancy.md` §0.9 already states the conclusion correctly: **our agents
execute model-generated tool calls over content ingested from email and
WhatsApp** — content an attacker can author. Therefore:

- **Isolation must fail closed and be impossible to forget.** Row-level security
  returns zero rows when the tenant is unset; a hand-written
  `WHERE organization_id = ?` fails *open* the one time an agent forgets it, and
  that omission is invisible until it leaks. This is why the isolation budget
  belongs on the database and the execution plane, not in application predicates.
- **Anything irreversible or outward-facing is owner-gated** and an agent must
  refuse it **by name** (`work_plan.md` §6, enforced by `plan-guard.mjs`). Live
  credentials, member/role writes, enforcement flips, cutovers, production
  one-offs. This list gets longer with customers, not shorter.
- **Per-tenant credentials, never a deployment singleton** (MT-0d), and never a
  run's secret in process-global state (MT-0a).
- **The container/microVM tier stops being optional at the pooled cutover**
  (D16). Hold that line; "trusted colleagues" is exactly the premise selling
  externally retires.
- **Least-privilege applies to agents too**: read-only transports where reads
  suffice, method allow-lists, confirm-before-send fail-closed, and a path fence
  that is itself tested against synthetic sources so "the fence went blind" is a
  red test rather than a silent gap.

## 7. Definition of done, with customers

A change is done when **all** of these hold — not when CI is green:

1. Acceptance criteria in the owning spec are met, and the **verification
   commands in that spec were run** (not equivalents).
2. Any rule it introduces names its enforcing fence (R7).
3. Migrations follow expand/contract (R6) and were applied against a real
   database (R8).
4. It is behind a flag if it changes behaviour a customer can see.
5. **Delivery is verified by evidence, not by a green job** — the migration
   ledger lines, the deployed SHA, the log line. "The deploy job passed" has been
   wrong four times in one day.
6. The owning spec's status header is updated in the same PR (R4).

## 8. What to fix before customer #1 — in order

These are named here so they are not rediscovered; each belongs to an existing
board row.

| # | Item | Why it is a business risk, not tech debt | Home |
|---|---|---|---|
| 1 | **SHA in `/health`** | Without it "deployed" is a guess; four deploys were blessed while shipping nothing | WS-25 |
| 2 | **`BACKUP_REMOTE`** — an off-box copy | One copy on the same provider account is not a backup; losing the account loses the customer's data | BO-23 / `backup_and_restore.md` §4.2 (owner-deferred 2026-08-05 — **revisit at customer #1**) |
| 3 | **Migration rehearsal against a prod-shaped restore** (P-1, §1) | The ladder-from-zero replay cannot see the failures we actually get | WS-5 (CI gates) |
| 4 | **Promote and rehearse the RLS set** — ⚠️ **a hard gate, not a nice-to-have** | §5.1 condition 2 requires RLS **enabled from day one on customer #1's silo**, so "the policies are still un-promoted" and "we have a paying customer" cannot both be true. It is hand-applied in a window, and until 2026-08-10 it contained a statement that could not run | WS-29 (MT-1b) |
| 5 | **`run_lifecycle_sweep` tenant binding** | Sweeps every tenant's roots with no predicate, on a path H2 never reaches | WS-29 MT-1d |

---

## 9. What this document is not

It is not a substitute for the board (`work_plan.md` owns order and gates), not a
substitute for an owning spec (they own scope and acceptance), and not a licence
to refactor: an agent may **not** rewrite existing code to conform to §3 or §4
without a ticket. These rules bind **new and changed** work. Where the tree
already violates them, that is a finding for the board, not a mandate to fix in
passing.
