# Development & delivery framework — how we ship to customers while still building

**Status:** 🟠 **PROPOSED — 2026-08-24.** Nothing here binds until the owner
records §9's decisions. · **Owner:** vjvarada · **Classification:** CONTRACTS &
DOCTRINE (`INDEX.md`) — no board row yet; §8 proposes **WS-35** ·
**Verified against code and against the GitHub API on 2026-08-24** (§1 is a
measurement, not a recollection).

**What this owns:** the *shape* of development and delivery once there is more
than one customer and more than one developer — branches, environments, what CI
gates, how the Customer Console's separate plane is delivered, and how a second
developer joins without the two of them breaking each other.

**What this does NOT own.** `engineering_practice.md` (D28) owns *how we build*
— expand/contract, what a test is worth, the supervisor loop, security posture —
and every rule in it still binds. This document sits **on top of** it and changes
exactly one of its conclusions (§1's "no staging", see §3), because the premise
that conclusion rested on is the premise the owner is now changing.
`deploy_delivery_path.md` (WS-25) owns the *transport*; this document owns what
that transport is asked to carry. `customer_console.md` (WS-31) owns *what* the
Console does; this owns *how its code and schema reach customers*.

---

## 0. The question, and the short answer

> *"Do we have a separate development branch and a production branch on the same
> repository?"*

**You already do — and the production one is a ref, not a branch, which is
better.** `main` is development; `release` is production; the box converges on
`release`. What is missing is not a third branch. It is:

1. **A gate at the boundary** — `main` is currently unprotected and CI is
   advisory at merge (§1.3).
2. **Somewhere to prove a release before customers meet it** (§3).
3. **A promotion that is an act rather than a side effect** (§2.3).
4. **A second plane nobody wired** — the Customer Console's schema does not
   travel with the deploy (§4).

A long-lived `develop` branch would make things **worse**, and this repository
has already paid to learn why (§2.2).

---

## 1. What is true today — measured 2026-08-24

Every row was re-derived today. Do not quote it after this week without re-running
the command in the right-hand column.

| # | Fact | How it was measured |
|---|---|---|
| 1.1 | **There is no automatic delivery path at all.** `deploy`, `vps-health` and `vps-forensics` are all `disabled_manually` (since 2026-08-17). `publish-release` lives *inside* `deploy.yml`, so with that workflow disabled **`release` cannot move on its own** — and the box's 5-minute pull timer polls `release`. Both delivery paths are therefore dark, and every deploy is a hand-run over SSH (H-11). | GitHub Actions API, `list_workflows` |
| 1.2 | **`origin/main` is 21 commits ahead of `origin/release`**, and the gap includes a Console ladder file (`infra/customer_console/008_flat_plan_d49.sql`). | `git rev-list --count origin/release..origin/main` |
| 1.3 | **`main` reads `protected: false`.** The board's exceptions row 1 records protection being enabled 2026-08-03 with `enforce_admins: true` — but that was on the pre-rebrand repository, and this repository's workflows are dated 2026-08-16. The protection very likely did not travel. ⚠️ The branches API does not always reflect *rulesets*, so this is a finding to confirm in settings, not a proven regression. | GitHub `list_branches` |
| 1.4 | **Required status checks cannot be turned on as things stand.** `pr-check.yml` carries `paths-ignore: ["**.md", "project-docs/**"]`, so a docs-only PR produces **zero check-runs**; requiring those contexts would make every docs PR permanently unmergeable. The recorded fix is an always-runs sentinel job — then require *that*. | `pr-check.yml:9-16` · `work_plan.md` §2 exceptions row 1 |
| 1.5 | **The Operator Console has no CI whatsoever.** `workbench/operator_console/` ships `typecheck`, `typecheck:lib` and `test` scripts; no workflow references the directory. The `frontend` job covers `control_plane` only. | `rg operator_console .github/workflows/` → no hits |
| 1.6 | **The deploy does not apply the Console ladder.** `scripts/vps_apply.sh` calls `scripts/apply_migrations.sh`, which is bolted to the tenant Postgres. `infra/customer_console/` has its own DSN-driven applier that **nothing invokes automatically**. D47 named closing this as the obligation on the way in; it is still open. | `rg -n 'apply_migrations' scripts/vps_apply.sh` |
| 1.7 | **124 remote branches.** `engineering_practice.md` §5 puts the work-in-flight ceiling at three or four. | `git ls-remote --heads origin \| wc -l` |
| 1.8 | **What IS strong**, and must not be rebuilt: the tenant ladder replays from empty three times in CI and asserts the repeats are no-ops; the backup/restore rehearsal runs on every PR; the Console suites run against **two** real Postgres services (R8 honoured after an independent verification caught them silently skipping); `plan-guard` and the supervisor agents travel in `.claude/` (D29). | `pr-check.yml` jobs `migrations`, `backup-restore`, `test` |

**Read 1.1 and 1.3 together.** Today the only thing standing between an unreviewed
commit and production is one person's discipline. That is a workable arrangement
for exactly one developer and it is the arrangement the rest of this document
exists to replace.

---

## 2. Branching — trunk, plus two promotion refs

### 2.1 The model

```
feature branch  ──PR──►  main  ──auto, on green──►  staging  ──owner promotion──►  release
  (hours-days)          trunk                        staging box              production box
                        "develop"                    (Ring 0)                 (Ring 1 → 2)
```

- **`main` is development.** Every PR merges here. It is the only merge target.
- **`staging` is a fast-forward-only ref** published by CI whenever `main` goes
  green, exactly as `publish-release` does today. A staging box's pull timer
  converges on it.
- **`release` is a fast-forward-only ref** moved by an **owner-gated
  `workflow_dispatch` naming an exact SHA** that has soaked in staging. The
  production box's pull timer converges on it, unchanged.

**Nothing ever merges *into* `staging` or `release`, and nothing is ever
cherry-picked onto them.** They are pointers at commits that already exist on
`main`. This is the property that makes the whole scheme cheap: a
fast-forward-only ref cannot diverge, cannot accumulate its own commits, cannot
grow a merge conflict, and cannot create the "this fix has to be applied twice"
problem that sinks GitFlow shops.

**Hotfixes take the same road, faster.** Fix on a branch → PR → `main` → promote
that SHA. There is no hotfix lane, because a hotfix lane is a second delivery
path and this repository has already recorded what two delivery paths that can
drift cost (`deploy_delivery_path.md` §3).

### 2.2 Why NOT a long-lived `develop` branch

Not a matter of taste. `engineering_practice.md` §5 names long-lived branches as
the **single root cause** behind three separate symptoms measured here in one
fortnight:

- three migration-number collisions (WS-10, #403's 158, #399's 157–159),
- two PRs green alone and red together, twice in one day (#399 × #403),
- a duplicated multi-tenancy design that a human had to sit and reconcile.

A permanent `develop` branch does not remove those; it *institutionalises* them,
because every feature branch is then born from a base that is itself drifting
from production. The bottleneck here is integration bandwidth — one person today
— and GitFlow spends that bandwidth on merges instead of on review.

The thing a `develop` branch is actually wanted for is **"somewhere green that
isn't customers yet."** That is an *environment*, not a branch, and §3 gives it
to you without the drift.

### 2.3 What changes versus today

| | Today | Proposed |
|---|---|---|
| Trunk | `main` | unchanged |
| Pre-production | none | `staging` ref + staging box |
| Production ref | `release`, auto-published on every green `main` | `release`, moved only by an owner-run promotion naming a SHA |
| Promotion evidence | none | the staging soak (§3.3) and the lifecycle rehearsal (§6.3) |

Making the promotion deliberate is not a slowdown dressed up as rigour — it is
the direct consequence of R6. **We cannot roll back.** A forward-only migration
ladder with no blue/green means the only recovery is roll-forward or restore, so
the last cheap moment to say "not this one" is *before* the ref moves.

---

## 3. Environments — staging as a restore, not as a maintained copy

### 3.1 The objection, stated fairly

`engineering_practice.md` §1 says, in terms: *"We do not run a classic dev →
staging → prod triple, and should not. At one box and single-digit customers it
buys drift (a staging environment nobody keeps truthful) more than safety."*

That reasoning is sound **for the world it describes**, and it is not being
re-litigated here on aesthetics. Its stated premise is *one box, single-digit
customers, one developer*. This document exists because the owner is changing all
three at once. Recording that premise change is §9's D-A.

### 3.2 The version that cannot drift

The doctrine's fear is a staging environment nobody keeps truthful. So do not
keep one truthful — **re-derive it**:

> **Staging is rebuilt nightly from production's own backup, anonymised on the
> box before it leaves, with the pending migration ladder replayed on top.**

A staging environment that is regenerated from production cannot drift from
production, because it is not maintained — it is *derived*. That answers the
objection instead of overruling it.

And this is not new work invented here. It is **P-1**, already proposed in
`engineering_practice.md` §1 and already ranked **item 3** of its §8 "fix before
customer #1" list: *"rehearse pending migrations against a restore of last
night's production dump… This single job would have caught more of this month's
incidents than any other change on this page."*

**P-1 and "a staging environment" are the same object.** Build it once and get
both: the migration rehearsal that catches what a ladder-from-zero replay
structurally cannot, and the place a release soaks before customers meet it.

Every ingredient already exists: the nightly dump (BO-23), a rehearsed restore
(2026-08-05), `scripts/rehearse_restore.sh` running in CI on every PR, and the
`mt-scratch` pattern.

### 3.3 The environment ladder, then

| Ring | What it is | Data | Who is hurt when it breaks |
|---|---|---|---|
| **Local** | `docker compose -f infra/docker-compose.yml` + local Console Postgres | seeded demo (`scripts/seed_demo.py`) | one developer |
| **CI** | ephemeral service containers, ladder replayed from empty ×3 | none | a PR |
| **Staging** | one box + one staging Supabase Console project, both rebuilt nightly from prod's anonymised dump | prod-shaped, anonymised | nobody |
| **Production** | the box + the live Console project | real | every customer |

Rings 0/1/2 from `engineering_practice.md` §2 are unchanged and sit *inside*
production: Fracktal first, then one friendly customer, then everyone. Staging is
a rung below Ring 0, not a replacement for it.

⚠️ **Staging is a real Metorite deployment, which means R5 binds it identically.**
`organization_id` populated, RLS on, pooled schema, N≥1. There is no phase in
which single-tenant code is acceptable (D15) and "it's only staging" does not
create one.

⚠️ **Anonymise on the box, before the dump leaves it.** Staging holds customer
data shaped like production; under DPDP that is customer data unless the
identifiers are gone first. This is a named acceptance clause in §8 T-3, not a
best-effort step.

---

## 4. Two delivery planes, and the one nobody wired

This is the part the question about the operator dashboard runs into, and it is
the single largest gap in the current pipeline.

Metorite delivers **two independent planes** with different failure shapes:

| | Tenant plane | Console plane |
|---|---|---|
| Code | gateway, workbench, services on the box | `apps/services/customer_console/` (own systemd unit, own env — D47) + `workbench/operator_console/` |
| Schema | `infra/postgres/`, **numbered, forward-only**, ledger-tracked | `infra/customer_console/`, **additive and idempotent by construction** |
| Applier | `scripts/apply_migrations.sh` — runs on every deploy | `scripts/apply_customer_console_migrations.sh` — **wired to nothing** (1.6) |
| Database | the box's Postgres | a **separate** Supabase project (D34) |
| Blast radius of a bad change | one deployment's users | **every customer at once** — sign-in and metering both stop (`customer_console_infrastructure.md` R-e) |
| What a bad migration costs | an outage | **a billing incident** |
| CI today | strong (1.8) | service suites yes; **ladder replay no; Operator Console UI nothing at all** (1.5) |

Three consequences follow, and they are the spine of §8's tickets:

**4.1 — The Console ladder must travel with the deploy, ordered and fail-closed.**
Right now the Console service can be deployed carrying code that expects a schema
its database does not have. The board's own words for the state after #442 are
*"`platform_api` is on the box but inert"*, and D47 named the fix. The applier
must run **before** the Console unit restarts (same R6 reasoning as the tenant
ladder) and must **fail the deploy** when its DSN is unset — never skip quietly.
A silent skip here is the exact failure mode that let four deploys report success
while shipping nothing.

**4.2 — The Console ladder's idempotency is a claim nobody checks.** The applier's
header asserts every file is additive / `IF NOT EXISTS` and that re-running is the
intended way to apply new files. Nothing verifies it. The tenant ladder earned its
`migrations` job precisely because *"'idempotent' was a claim about 152 files
nobody had checked together."* The Console ladder is that same claim, eight files
in, unchecked. This is cheap, AGENT-SAFE, and the highest value-per-line item in
this document.

**4.3 — The Console's blast radius earns a stricter gate, not an equal one.**
Promotion of a Console change should require the lifecycle rehearsal in §6.3 to
have passed on staging. A tenant-plane regression annoys one customer's users; a
Console regression stops sign-in for all of them and can mis-bill.

---

## 5. What CI gates, and where

Jobs marked **(new)** do not exist today.

| Job | Runs on | Blocking? | Why |
|---|---|---|---|
| `sentinel` **(new)** | every PR, **no `paths-ignore`** | ✅ | The one context that can be *required*, because it always runs. Unblocks 1.4. Trivial job — checkout and echo. |
| `lint` · `test` | PR + main | ✅ | as today, incl. both real Postgres services |
| `migrations` (tenant ladder ×3) | PR | ✅ | as today |
| `console-migrations` **(new)** | PR | ✅ | Replay `infra/customer_console/` from empty **twice**; assert the second run changes nothing. Closes 4.2. |
| `backup-restore` | PR | ✅ | as today |
| `frontend` (control_plane) | PR | ✅ | as today |
| `operator-console` **(new)** | PR | ✅ | `npm ci && npm run typecheck && npm test` in `workbench/operator_console/`. Closes 1.5. A near-copy of `frontend`. |
| `secret-scan` | PR | report-only | graduate to blocking once proven |
| `migration-collision` | PR | ✅ | `test_migration_prefixes.py` exists and works — but is **blind on a conflicted PR** (H-10). Fix the trigger, not the test. |
| `staging-promote` **(new)** | push to `main`, on green | — | fast-forwards `staging` |
| `release-promote` **(new)** | `workflow_dispatch` with a SHA | 🔴 OWNER-GATE | fast-forwards `release` |

**Two structural points about this table.**

*The sentinel job is the keystone.* Until one context always runs, branch
protection can require nothing, and every gate above is decorative at merge —
which is precisely the wording the board used about this repository's CI before
2026-08-03.

*Fix H-10 by changing the trigger.* A conflicted PR gets `check_runs: 0` because
GitHub never computes `refs/pull/N/merge`. So the window in which a cross-branch
migration collision is most likely is exactly the window in which nothing is
watching. Add a `merge_group` trigger (or a job that checks out the head ref and
merges base itself). With one developer this was a theoretical hole; with three
it is a weekly event.

---

## 6. The Console: operator dashboard, seats and AI credits

> *"How does this account for the operator dashboard, AI credits, etc.? How do we
> ensure it works?"*

Tests are necessary and not sufficient here, because the failures that matter in
a billing system are **operational**, not logical: a manual grant, a restored
backup, a retried webhook, a crash between two writes. So this splits three ways.

### 6.1 Fences — invariants a future agent cannot break silently (R7)

Each needs a **named** test, verified red first, and — because this is money —
mutation-tested per `engineering_practice.md` §4.

| Invariant | Where it comes from | The fence |
|---|---|---|
| **One call is billed once.** | `usage_event.request_id UNIQUE` — *"a customer billed twice for one call is a credibility event, and the constraint is what makes it impossible rather than unlikely"* | Record the same `request_id` twice, concurrently; assert exactly one `usage_event` **and** exactly one `credit_ledger` row. Retries, stream reconnects and `v1_compat.py`'s usage-rebuild path all create this opportunity. |
| **Balance is never a column.** | *"Never `UPDATE` a balance column. Balance is `SUM(credit_ledger.delta)`."* | A **structural** fence over the tree (the §4 preference): assert no statement anywhere updates a balance field on `credit_ledger`. Structural, because the point is to catch the *next* agent, who has not read this paragraph. |
| **A seat cannot be double-assigned.** | the partial unique index `(org, plan, member) WHERE released_at IS NULL` | Two concurrent assignments of one member; assert one wins and one is refused by the database, not by application logic. |
| **The balance gate fails closed.** | CP-4 | A zero-balance org gets refused, not served-and-unbilled. |
| **Metering is scoped.** | D15 · R5 | Org A's usage can never appear in org B's ledger or rollups. Already the shape of CP-3's cross-tenant fences — extend, don't parallel. |

### 6.2 Reconciliation — the part tests cannot cover

A scheduled, idempotent pass asserting, per organization:

- `SUM(credit_ledger.delta)` equals the cached Redis balance;
- every `usage_event` has exactly one `credit_ledger` row and vice versa;
- seats assigned never exceed seats purchased (D19.3's hard cap);
- every active `seat_assignment` names a live member on the tenant plane.

Divergence is **reported, never auto-corrected.** An automatic corrector on a
billing ledger destroys the audit trail at exactly the moment a customer disputes
a charge — which is the same reason the balance is not a column.

⚠️ **Extend the existing seam.** `scripts/reconciler.py` and
`scripts/reconcile_console_mirror.py` already exist; CP-2e's reconciler is
explicitly *"a single idempotent pass… wired into nothing"*. Adding a second
reconciliation mechanism beside them is a defect, not a feature (CLAUDE.md §5).
Give the existing pass a cadence and a report; do not author a third.

### 6.3 The lifecycle rehearsal — what makes a promotion evidence-backed

One scripted end-to-end run against **staging**, on every promotion:

> provision an org → invite and assign a seat → grant credits → burn credits
> through the Router → hit the balance gate → top up via **test-mode** Razorpay
> → release the seat → confirm the operator dashboard shows every one of those
> states.

This is the only thing that proves the operator dashboard *works*, as opposed to
compiling. It is also the natural home for WS-30 SC-4g clause 2's capture
rehearsal, which is blocked today on nothing but the test-mode account existing
(H-14).

⚠️ **The rehearsal runs against staging, never production.** Its production
equivalent — running it against a live organization — is `customer_console.md`
§8 gate 4 (*"editing any live organization's entitlements, seats or credit
balance"*) and an agent must refuse it by name.

---

## 7. Multiple developers

The current model is one owner plus supervised agents, and the harness for it is
already excellent and already travels with the repo (D29): `plan-guard`,
spec-auditor → implementer → verifier → diff-reviewer, `/next-ticket`. **A second
human inherits all of that on clone.** What a second human does *not* inherit is
the discipline that is currently enforced by there being only one of them.

Six things, in the order they stop hurting:

**7.1 — Make CI binding (🔴 OWNER-GATE — GitHub settings).** Re-establish
protection on `main` (1.3) and require the `sentinel` context (§5). Everything
else here is optional; this is not. Advisory CI is a personal habit, and a habit
does not survive a second person.

**7.2 — Close the conflicted-PR blind window (H-10).** See §5.

**7.3 — Decide how migration numbers are assigned.** Three collisions in a
fortnight with **one** developer. R1's "take the next free number at build time"
is a coordination protocol with no coordinator, and it fails outright at N>1.
Two options, and this is a real decision (§9 D-C):

- **(a) Keep numbers, add a coordinator.** The collision fence runs on every PR
  including conflicted ones (7.2), plus a re-check against `origin/main` at merge.
  Cheapest; keeps the ledger keyed on filename exactly as it is.
- **(b) Stop colliding by construction** — timestamp- or ULID-prefixed filenames,
  which two developers cannot pick identically. Removes the class of failure, but
  touches the ledger, the replay job, the `[0-9]*` globs and the promotion set.

Recommend **(a) now, (b) only if (a) still collides** — (b) is a schema-adjacent
change and this is not the quarter for one.

**7.4 — Partition by seam, and write the seams down.** `engineering_practice.md`
§5 already says partition by seam and serialise on shared contracts. Make it
mechanical with a `CODEOWNERS` mapping each shared seam to a required reviewer:
the DB engine seam (`acb_common.db`), the entitlement intersect, the nav registry
(`src/lib/nav.ts`), `lib/centers.ts`, `src/lib/statusAccent.ts`, the Console
ladder, `work_plan.md`. Two agents or two humans may run in parallel only when
neither can touch the other's contract; CODEOWNERS turns that from a rule people
remember into a review nobody can skip.

**7.5 — Cap and prune work in flight.** 124 remote branches against a stated
ceiling of three or four (1.7). Turn on auto-delete-on-merge, and prune what is
merged or abandoned. The count is not cosmetic — it is the same long-branch
pathology that caused the collisions, made visible.

**7.6 — Make onboarding one command.** Today a new developer needs the README,
Docker, `uv`, npm in two workbench apps, and a Console Postgres nobody documents
standing up locally. `scripts/bootstrap_local.ps1` is Windows-only.
`make dev-up` should stand up **both planes** — tenant Postgres with the ladder
replayed, a local Console Postgres with `infra/customer_console/` applied, Redis,
a seeded demo org — and print what it started. CP-1 was deliberately built on
plain Postgres 16 with no vendor extensions; that property is what makes a local
Console possible, and it should be used.

⚠️ **One thing must not change with a second developer.** The verifier is never
the implementer, and the reviewer hunts for the case where the change is wrong.
Two humans reviewing each other's agent output is *more* of that discipline, not
a licence to relax it.

---

## 8. Proposed tickets — WS-35

Not minted. The board row is the owner's act (§9 D-A). Gate labels per
`work_plan.md` §1.7.

| # | Ticket | Done when | Gate |
|---|---|---|---|
| **T-1** | `sentinel` job in `pr-check.yml` | A job with no `paths-ignore` runs on every PR, including a docs-only one; a docs-only PR shows exactly one check-run instead of zero | 🟢 AGENT-SAFE |
| **T-2** | Require `sentinel`; restore protection on `main` | Protection reads back with the `sentinel` context required and `enforce_admins: true`; verified by reading it back, not by the settings page | 🔴 OWNER-GATE (GitHub settings) |
| **T-3** | `console-migrations` CI job | `infra/customer_console/` replays from empty twice against Postgres 16; the second run is proven to change nothing (mirrors the tenant `migrations` job's assertion) | 🟢 AGENT-SAFE |
| **T-4** | `operator-console` CI job | `npm ci && npm run typecheck && npm test` green in `workbench/operator_console/`; verified red first by breaking a type on a scratch branch | 🟢 AGENT-SAFE |
| **T-5** | Console ladder in the delivery path | The applier runs **before** the Console unit restarts, from the same versioned script a human would hand-run; **fails the deploy** when its DSN is unset; the applied file list appears in the deploy log. Closes D47's named obligation | 🟢 AGENT-SAFE to build · 🔴 OWNER-GATE to run anywhere real |
| **T-6** | Close H-10's blind window | A PR that is conflicted with `main` still runs the migration-collision fence; proven by opening a deliberately conflicted PR carrying a duplicate migration number and watching it go red | 🟢 AGENT-SAFE |
| **T-7** | `staging` ref + `staging-promote` | `staging` fast-forwards on every green `main`; it is never a merge target; a non-fast-forward push is refused | 🟢 AGENT-SAFE |
| **T-8** | `release-promote` (SHA-parameterised `workflow_dispatch`) | `release` moves only by this workflow, only to a SHA already on `main`, and refuses a SHA that has not been on `staging` | 🟢 AGENT-SAFE to build · 🔴 OWNER-GATE to run |
| **T-9** | Nightly anonymised prod→staging rebuild (**P-1**) | Last night's dump restores into staging with identifiers anonymised **on the box before the dump leaves it**; pending migrations replay on top; failure is visible to the operator | 🟢 AGENT-SAFE to build · 🔴 OWNER-GATE to run (touches prod backups + a real box) |
| **T-10** | The five §6.1 fences | Each named, each verified red first, each mutation-tested | 🟢 AGENT-SAFE |
| **T-11** | Reconciliation cadence + report | The four §6.2 checks run on a schedule through the **existing** reconciler seam; divergence is reported, never auto-corrected | 🟢 AGENT-SAFE to build · 🔴 OWNER-GATE to schedule against live data |
| **T-12** | Lifecycle rehearsal script (§6.3) | The full provision→seat→credits→gate→top-up→release run passes against staging and asserts the operator dashboard reflects every state | 🟢 AGENT-SAFE against fixtures/staging · 🔴 OWNER-GATE against any live org (§8 gate 4) |
| **T-13** | `CODEOWNERS` for the shared seams (7.4) | Every seam in 7.4 maps to a required reviewer; a PR touching one cannot merge without it | 🟢 build AGENT-SAFE · 🔴 enabling the requirement is a settings change |
| **T-14** | `make dev-up` — both planes, one command | A clean checkout reaches a working local Metorite **and** a working local Console, with a seeded demo org, from one command on Linux and Windows | 🟢 AGENT-SAFE |
| **T-15** | Branch hygiene | Auto-delete-on-merge on; merged/abandoned branches pruned; the count is reported | 🔴 OWNER-GATE (settings + deleting others' branches) |

**Suggested order.** T-1 → T-2 (nothing else is enforceable until CI binds) →
T-3, T-4, T-6 (cheap, close measured holes) → T-5 (the Console gap) → T-10, T-11
(money invariants) → T-7, T-8, T-9 (the staging ladder) → T-12 → T-13, T-14,
T-15.

⚠️ **T-9 and T-5 both touch the box and both are downstream of H-11.** While
`deploy`, `vps-health` and `vps-forensics` are disabled and the deploy secrets are
absent (1.1), *nothing* in this document's delivery half can be exercised for
real. **Re-enabling those workflows is the unblocking act, and it is the owner's.**

---

## 9. Decisions this needs from the owner

Proposed, not recorded. An agent must not mint these.

| # | Decision | Why it cannot be defaulted |
|---|---|---|
| **D-A** | **Adopt this framework and mint WS-35.** Explicitly amends `engineering_practice.md` §1's "no staging" on the ground that its premise (one box, single-digit customers, one developer) is the premise being changed | §1's conclusion is D28-recorded doctrine; overriding it silently is exactly the CLAUDE.md §5 failure |
| **D-B** | **Trunk + two promotion refs**, and **no long-lived `develop` branch** (§2) | The question was asked directly; recording the answer stops it being re-asked every quarter |
| **D-C** | **Migration numbering at N>1 developers** — 7.3 (a) or (b) | R1's protocol has no coordinator; three collisions at N=1 |
| **D-D** | **Where staging runs, and what it costs** — a second VPS, or a second Supabase pair, or both | Money, and it is an external account (owner-side by `customer_console_infrastructure.md` §7) |
| **D-E** | **Promotion cadence.** `engineering_practice.md` §2 says a fixed clock is optional but the ring order is not. Name the soak: hours, or a working day? | Sets how long a fix takes to reach a customer, which is a business call |
| **D-F** | **Who reviews what** — the CODEOWNERS map (7.4), once there is a second developer to name | Cannot be written before the people exist |

---

## 10. Verification commands

```bash
# 1.1 — are the workflows still disabled?
gh workflow list --all

# 1.2 — how far is production behind trunk?
git fetch origin main release staging
git rev-list --count origin/release..origin/main
git diff --name-only origin/release origin/main -- infra/postgres/ infra/customer_console/

# 1.3 — is main actually protected?
gh api repos/Hathi-Labs/Metorite/branches/main/protection

# 1.4 — does a docs-only PR produce any check-runs?
gh pr checks <a docs-only PR number>

# 1.5 — does the Operator Console have CI?
rg -n "operator_console" .github/workflows/

# 1.6 — does the delivery path apply the Console ladder?
rg -n "customer_console" scripts/vps_apply.sh

# 1.7 — work in flight
git ls-remote --heads origin | wc -l

# the ladders, locally
bash scripts/apply_migrations.sh          # tenant, needs the docker Postgres
CUSTOMER_CONSOLE_DATABASE_URL=… bash scripts/apply_customer_console_migrations.sh

# the suites this document proposes to gate on
uv run pytest tests/unit/test_migration_prefixes.py
uv run pytest tests/unit/test_customer_console_sql.py     # needs CUSTOMER_CONSOLE_DATABASE_URL
cd workbench/operator_console && npm run typecheck && npm test
cd workbench/control_plane   && npx tsc --noEmit && npx vitest run
```

⚠️ Per CLAUDE.md §6, do not run `tests/unit/` as a bare directory without
deselecting the memory and calendar suites.

---

## 11. References

- `specs/engineering_practice.md` — §1 environments (the P-1 proposal and the
  "no staging" conclusion this amends) · §2 deploy≠release and rings · §3
  migrations · §4 what a test is worth · §5 dividing work · §8 the ordered
  pre-customer list
- `specs/deploy_delivery_path.md` — WS-25; the transport, the pull timer, and the
  four recorded defects that make "the job passed" untrustworthy
- `specs/customer_console.md` — WS-31; §3.4 AI credits · §4 the Router · §8 gate
  labels (gates 2, 4 and 7 all bind this document's delivery half)
- `specs/customer_console_infrastructure.md` — D34 Supabase · D47 the service on
  the VPS and its named ladder-apply obligation · R-e the single point of failure
- `specs/subscription_console.md` — WS-30; the customer-facing half of what §6.3
  rehearses
- `specs/backup_and_restore.md` — BO-23; the dump T-9 restores from
- `work_plan.md` §1 R1/R5/R6/R7/R8 · §2 exceptions row 1 (branch protection) · §6
  owner gates
- `HANDOFF.md` H-10 (the blind window), H-11 (disabled workflows and deploy
  secrets), H-14 (the Razorpay test account §6.3 needs)
