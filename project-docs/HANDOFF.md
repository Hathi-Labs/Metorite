# HANDOFF — what the last session left unfinished

**This file is a QUEUE OF ACTIONS, not a status board.** It is injected into
every session by `.claude/hooks/session-handoff.mjs` (D39) so nobody has to
remember what was in flight.

---

## The one rule that keeps this file honest

> ⚠️ **Never restate state here. Point at it, and carry the command that
> re-derives it.**

`work_plan.md` §2 is **the only current-state authority** (CLAUDE.md §1). A
handoff file that also described state would be a second board, and a second
board is the CLAUDE.md §5 defect by construction: two descriptions of one truth,
one of which stops being updated first, and the stale one is trusted because it
is the one loaded into the prompt.

So every entry below carries a **Check** — a command whose output tells you
whether the entry is still real. You do not trust this file. You run the Check.

That inverts the usual failure. A stale entry here costs one command and gets
deleted; it can never *quietly* be believed, because believing it requires
running something that would have contradicted it.

## The protocol

**At the start of a session** — before any other work, and before picking up
whatever you were asked to do:

1. Run the **Check** for every entry.
2. **Delete every entry whose Check shows it is done.** In your first commit.
   Deleting a finished entry is not optional housekeeping — an entry that
   outlives its work is how this file starts lying.
3. Report what is left to the user in one short list, then proceed.

**During a session** — add an entry the moment you create the obligation, not at
the end when context is short. The entry you write while you still remember why
is worth five you reconstruct.

**Before ending a session** — add an entry for anything you are handing over:
work you started, an owner gate you hit, a finding you scoped out, a decision
you are owed. If you are not sure it matters, add it. A one-line entry costs
nothing; the thing nobody wrote down costs a session.

**`/handoff`** does all of this — see `.claude/commands/handoff.md`.

## The shape of an entry

```
### H-<n> · <one line, imperative> · [AGENT|OWNER]
- **Check:** `<command>` → <what output means STILL PENDING>
- **Why:** <one or two sentences — the reason, not the status>
- **Authority:** <file §section, or board row>
- **Added:** <date> · <session or PR that created it>
```

`[OWNER]` marks an entry an agent **must refuse by name** (`work_plan.md` §6).
An agent may verify an OWNER entry's Check and report it; it may never do it.

Ids are never reused. Delete the whole block — do not tick it off in place, or
this file grows a graveyard and the graveyard is what goes stale.

**Fence: `tests/unit/test_handoff_queue.py` (R7).** Mint the next id against
**`origin/main`**, not against your branch — two branches in flight each pick
"the next free id" from the base they were cut from, and whichever merges second
carries an id `main` has since taken. That merge is CLEAN, so nothing surfaces
it: it happened to H-27 and again to H-28 on 2026-08-25. If the fence fails,
renumber the entry that merged **second** and note the move in its `Added:`
line — never reclaim a number by deleting the other entry.

---

# OPEN


### H-22 · Decide the development & delivery framework (D-A…D-G) and mint WS-38 · [OWNER]
- **Check:** `rg -n "WS-38" project-docs/work_plan.md` → no hit means the board
  row was never minted and the seven decisions in
  `specs/development_and_delivery_framework.md` §9 are still owed. (Renumbered
  from WS-35 at merge 2026-08-24 — WS-35 was already minted for subdomain
  workspaces / D51, so the original Check hit the wrong workstream and would
  have read as done on arrival.)
- **Why:** The framework spec was written 2026-08-24 and is deliberately
  **PROPOSED, binding nothing**. Its central item amends
  `engineering_practice.md` §1's "we do not run a staging environment, and should
  not" — sound for the premise it names (one box, single-digit customers, one
  developer) and that is exactly the premise being changed. Overruling recorded
  doctrine is not an agent's act. Until D-A is recorded, T-1…T-15 have no board
  row to dispatch from.
- **Authority:** `specs/development_and_delivery_framework.md` §9 · CLAUDE.md §5
  (do not re-litigate decisions) · `work_plan.md` §3
- **Added:** 2026-08-24 · delivery-framework planning session

### H-23 · `main` reads `protected: false` — CI is advisory at merge · [OWNER]
- **Check:** `gh api repos/Hathi-Labs/Metorite/branches/main/protection` → a
  `404 Branch not protected` means still pending. ⚠️ Read it back from the API,
  never from the settings page; and note a *ruleset* may protect the branch
  without the branches API reporting `protected: true`.
- **Why:** `work_plan.md` §2 exceptions row 1 records protection being enabled
  2026-08-03 with `enforce_admins: true` — but that was the **pre-rebrand**
  repository, and this one's workflows are dated 2026-08-16, so the protection
  most likely did not travel. Measured 2026-08-24: `list_branches` reports
  `"name":"main","protected":false`. Every gate in `pr-check.yml` is therefore
  decorative at merge, which is survivable with one disciplined developer and is
  not survivable with two. ⚠️ It cannot simply be switched on: `pr-check.yml`
  carries `paths-ignore` for `**.md` and `project-docs/**`, so a docs-only PR
  produces **zero** check-runs and requiring those contexts would make every docs
  PR unmergeable. The always-runs sentinel job (T-1) lands first, then require
  **that** one context.
- **Authority:** `work_plan.md` §2 exceptions row 1 · §6 (GitHub settings) ·
  `specs/development_and_delivery_framework.md` §1.3 · T-1/T-2
- **Added:** 2026-08-24 · delivery-framework planning session

### H-24 · The Customer Console ladder does not travel with the deploy · [AGENT]
- **Check:** `rg -n "customer_console" scripts/vps_apply.sh` → no hit means the
  delivery path still applies only the tenant ladder and the gap is open.
- **Why:** `infra/customer_console/` has its own DSN-driven applier
  (`scripts/apply_customer_console_migrations.sh`) that **nothing invokes
  automatically**, so the Console service can be delivered carrying code that
  expects a schema its database does not have — the board's own "`platform_api`
  is on the box but inert". **D47 named closing this as the obligation on the way
  in.** Two clauses matter and neither is optional: the applier runs **before**
  the Console unit restarts (the R6 window), and it **fails the deploy** when its
  DSN is unset rather than skipping — a silent skip is how four deploys once
  reported success while shipping nothing. Building it is AGENT-SAFE; running it
  anywhere real is OWNER-GATE.
- **Authority:** `specs/customer_console_infrastructure.md` §5 item 4 (D47) ·
  `specs/development_and_delivery_framework.md` §4.1 · T-5
- **Added:** 2026-08-24 · delivery-framework planning session

### H-25 · The Console ladder's idempotency is an unchecked claim · [AGENT]
- **Check:** `rg -n "infra/customer_console" .github/workflows/pr-check.yml` →
  no hit means the gap is open.
- **Why:** A cheap hole with the same shape as one this repo has already been
  bitten by: `apply_customer_console_migrations.sh`'s header asserts every
  ladder file is additive and re-runnable; **nothing verifies it** — the tenant
  ladder earned its triple-replay job precisely because "idempotent" was a claim
  about files nobody had checked together. (This entry originally also carried
  "the Operator Console has no CI"; that half landed the same day it was
  measured — PR #80, 2026-08-24, added the `frontend-operator` job to
  `pr-check.yml` — so only the ladder half remains.)
- **Authority:** `specs/development_and_delivery_framework.md` §4.2 · §5 · T-3
- **Added:** 2026-08-24 · delivery-framework planning session

### H-19 · WS-34: theme-switch the new Organisation surface by eye · [AGENT]
- **Check:** nothing in the repo can answer this — that is the point. Ask whether
  anybody has switched the org theme to Fluent → Material → Graphite and LOOKED at
  **Organisation → Seat assignments** and at a neighbouring app. Unanswered → pending.
- **Why:** `workbench/control_plane/AGENTS.md` is explicit that the conformance
  suite checks eight regexes and **nothing in this tree tests layout or cross-app
  continuity**, so the theme switch is the real gate. WS-34 added two surfaces
  (the seat roster, the four-tab strip) and moved a third (branding into a tab).
  The suite is green and that proves no hardcoded colour, not that the surface
  looks like the product beside it.
- **Authority:** `specs/launch_surface.md` §10 · `workbench/control_plane/AGENTS.md`
- **Added:** 2026-08-24 · WS-34 build session

### H-20 · WS-34 LS-11: decide the fate of seats held on plans D49 retired · [OWNER]
- **Check:** `SELECT o.slug, sa.plan_slug, count(*) FROM seat_assignment sa
  JOIN organization o ON o.id = sa.organization_id
  WHERE sa.released_at IS NULL AND sa.plan_slug <> 'core' GROUP BY 1, 2;` on the
  Console database. Any row means a customer holds a seat on a retired plan and the
  decision is still owed. Zero rows → delete this entry.
- **Why:** Migration 008 deactivates every Center package, add-on and bundle so the
  checkout cannot SELL them, and deliberately **touches no `seat_assignment` or
  `seat_grant` row** — repricing, converting, refunding or prorating a seat somebody
  already holds is money on a live system. Their seats keep working meanwhile; what
  they should cost is the owner's call. Expected to be empty or Fracktal-only (D42's
  ₹0 onboarding) today, which is why 008 could land as data now.
- **Authority:** `specs/launch_surface.md` §4.4 · LS-11 · `work_plan.md` §6
- **Added:** 2026-08-24 · WS-34 build session

### H-21 · Promoting a `preview` app to `live` is an owner decision, not a code change · [OWNER]
- **Check:** compare `specs/launch_surface.md` §2's live table against
  `rg -n 'launch: "live"' workbench/control_plane/src/lib/nav.ts | wc -l` → 8 means
  nothing has been promoted. This entry never "completes"; it is the standing rule
  for the next person who finishes an app.
- **Why:** Sixteen panes are `preview` — routes, API and tests intact, nav entry
  absent. Turning one on is the judgement "this is finished enough to sell", which
  is the owner's; the registry edit plus its `nav.test.ts` line is trivial once the
  call is made. ⚠️ **Never promote an app by granting its feature** — `preview` is
  not a permission (§3.4), and confusing the two makes a product decision into a
  data migration and makes `/access` lie about why a pane is missing.
- **Authority:** `specs/launch_surface.md` §2 · §3 · §11 item 4
- **Added:** 2026-08-24 · WS-34 build session


### H-2 · Count archived projects on prod BEFORE migration 171 applies · [OWNER]
- **Check:** `SELECT count(*) FROM pm_projects WHERE status = 'archived';` on
  prod. If 171 has already applied, this number is no longer recoverable this
  way and the query becomes `WHERE archived_root_id = id` — which answers a
  *different* question. Unanswered → still pending.
- **Why:** ⚠️ **Time-sensitive and ordered against H-1.** 171 changes what
  "archived" means; the pre-migration count is the only baseline that can tell
  us whether the lifecycle sweep behaved. Not a deploy blocker — if H-1 happens
  first, record that this number was lost rather than substituting the other one.
- **Authority:** `work_plan.md` §2 WS-27 row
- **Added:** 2026-08-14 · session that built WS-27bj

### H-3 · Rotate the production SSH credentials pasted into a session · [OWNER]
- **Check:** can the old password still authenticate? If nobody has rotated it,
  it can. Treat as pending until rotation is confirmed.
- **Why:** 🔴 Root credentials for the production VPS were pasted into an agent
  transcript. They were **refused and never used** (`work_plan.md` §6), but a
  secret in a transcript is a disclosed secret. Rotate, and replace root password
  auth with a key while you are there.
- **Authority:** `work_plan.md` §6 · `specs/engineering_practice.md` (security)
- **Added:** 2026-08-14 · carried from the session that refused them

### H-4 · WS-27bj: build the admin surface for org-wide vocabularies · [AGENT]
- **Check:** `rg -n "refuse_org_wide_write" apps/services/gateway/gateway/routes/projects/`
  → still present on the patch/delete/merge paths means still pending.
- **Why:** An org-wide tag, task type or custom field can currently be
  **created but never edited or retired** — `refuse_org_wide_write` answers 409
  rather than letting those routes 500 on `CAST('None' AS uuid)`. That is the
  conservative half of ship-dark and it is real debt: the affordance to fix a
  typo in an org-wide row does not exist.
- **Authority:** `specs/project_management_app.md` §9.11 ("Not in scope" —
  the seam lands first) and §9.11.1
- **Added:** 2026-08-14 · session that built WS-27bj

### H-5 · Flip `PROJECTS_ORG_VOCABULARIES` when org-wide creates should go live · [OWNER]
- **Check:** the variable's value on the box → unset or `0`/`off`/`false` means
  still dark.
- **Why:** Default OFF and it gates **only** the affordance that *creates* an
  org-wide row, never the read union — which is already on and inert until a row
  exists. Flipping it is a restart, not a release. Requires H-1 first.
- **Authority:** `specs/project_management_app.md` §9.11 · `work_plan.md` §6
- **Added:** 2026-08-14 · session that built WS-27bj

### H-6 · `TagRow` is declared twice on the frontend · [AGENT]
- **Check:** `rg -n "interface TagRow" workbench/control_plane/src/app/projects/lib/`
  → two hits means still pending.
- **Why:** `lib/tags.ts` and `lib/api.ts` each declare it, and `page.tsx` passes
  rows between them, so they are assignable only while they agree. Widening one
  for org-wide vocabularies is what surfaced it; both were widened to keep the
  build green. Collapsing two public wire types is its own change (CLAUDE.md §5).
- **Authority:** `specs/project_management_app.md` §9.11.1 ("findings for the board")
- **Added:** 2026-08-14 · session that built WS-27bj

### H-7 · `now()` can move backwards, and migration 168's keyset cursor assumes it cannot · [AGENT]
- **Check:** `rg -n "updated_at, id" infra/postgres/168*.sql` → the delta feed's
  cursor still ordering on `(updated_at, id)` with no monotonic guarantee means
  still pending. Needs its own board row and a decision before anyone builds.
- **Why:** `now()` is the **transaction-start** timestamp, so a transaction that
  opens early and commits late stamps a time earlier than a row already written
  by a newer, faster-committing transaction — reproduced on a real database, 201
  ms backwards. Harmless to the `If-Match` precondition (an exact comparison
  still differs). **A real gap in the delta feed**: a client whose cursor has
  passed the newer value never receives the row stamped behind it, and that
  change leaves the stream silently.
- **Authority:** `specs/project_management_app.md` §9.10.2 · already-merged code,
  so CLAUDE.md §5 says record it, do not refactor it
- **Added:** 2026-08-14 · PR #439

### H-8 · Still owed on WS-27bg slice 2, and WS-27bg slice 3 / WS-27bh unbuilt · [AGENT]
- **Check:** the WS-27 row in `work_plan.md` §2 — it names what is built. Read
  it rather than this entry; this entry only says *look there*.
- **Why:** A project still cannot be **renamed**; the bulk-close-on-Stop offer
  and the Delete affordance are unbuilt. Slice 3 (overdue suppression across four
  predicates) and WS-27bh (task-type chip, derived urgency + the "Urgent" →
  "Critical" relabel, recurring indicator, source badge) are queued behind them.
  ⚠️ WS-27bh's source badge must **promote** `/tasks`' existing `SourceBadge`,
  not author a fourth copy.
- **Authority:** `work_plan.md` §2 WS-27 row · `specs/project_management_app.md`
  §9.9
- **Added:** 2026-08-14 · session that built WS-27bj

### H-10 · HALF the R1 blind window is still open — the cross-branch collision · [AGENT]
- **Check:** `rg -n "merge_group|merge-base origin/main" .github/workflows/pr-check.yml`
  → only the secret-scan's `merge-base` hit (no `merge_group:` trigger, and no job
  that checks out the head ref and merges the base before running the fence) means
  the remaining half is still open.
- **Why:** ⚠️ **This entry is NOT closed by the `push` trigger — it is halved, and
  the surviving half is the one that actually bites.** What `push` fixed: a
  CONFLICTED PR used to run zero jobs, because `pull_request` builds check out
  `refs/pull/N/merge` and GitHub does not compute that ref while a PR is dirty
  (#439 sat `dirty` at `check_runs: 0`). A `push` build has no merge ref to
  compute, so the branch is now checked whatever its mergeability.
  **What remains:** `test_migration_prefixes.py` globs the **working tree**, so it
  only ever sees one branch's migrations. Two branches that each add `172_*.sql`
  are individually valid and both go green; the duplicate exists **only in the
  merge result**, which no build in this repo ever materialises. The fence cannot
  see the collision it exists to catch. Closing it needs a `merge_group:` trigger
  (checks the queued merge commit) or a job that checks out the head and merges
  the base itself before running the fence — **which is exactly ticket T-6**, whose
  acceptance is the right proof: open a deliberately conflicted PR carrying a
  duplicate migration number and watch it go **red**.
  ⚠️ Do not delete this entry on the strength of the `push` trigger. Delete it when
  T-6's acceptance has been demonstrated.
- **Authority:** `specs/development_and_delivery_framework.md` §5 and §8 **T-6**
  (🟢 AGENT-SAFE; sequenced with T-3 as a cheap measured hole) · `work_plan.md` §2
  WS-27 row (the R1-collision record) · R1
- **Added:** 2026-08-14 · session that built WS-27bj · **halved 2026-08-25** by the
  push trigger (PR #46); re-pointed at T-6

### H-13 · Commit the three plan-guard-gated patches (deploy.sh, .env.example, health-watchdog.sh) · [OWNER]
- **Check:** `rg -n "3a83c19d" deploy/hostinger/deploy.sh` → a hit means still
  pending. `rg -n "127.0.0.1:8000" .env.example` → a hit means still pending.
  `rg -n "ActiveEnterTimestamp" deploy/hostinger/health-watchdog.sh` → NO hit
  means still pending.
- **Why:** plan-guard forbids agent writes under `deploy/` and to `.env*`, so
  three fixes exist only where the owner (or a granted session) applied them:
  (a) `deploy/hostinger/deploy.sh` still seeds one company's Entra directory
  GUID into every fresh box (`scripts/vps_apply.sh`, the CI path, is already
  fixed); (b) `.env.example` ships `GATEWAY_BASE_URL` on port 8000 while the
  gateway listens on 8080, lacks `ACB_MASTER_KEY` entirely (load-bearing —
  encrypts stored provider keys), and carries the dead `AUTH_ALLOWED_DOMAIN`
  variable; (c) `health-watchdog.sh` needs the **180-second startup grace**
  that is live on the box but absent from the repo — the gateway cold-starts
  in ~90–105 s (awaited warm-clone timeouts) while the watchdog probes after
  15 s, and without the grace it killed the gateway seconds before "startup
  complete", in a permanent loop. ⚠️ (a) and (c) are patched **on the box
  only** — the next git-reset deploy REVERTS them unless committed first.
- **Authority:** plan-guard.mjs (D29) · `work_plan.md` §6
- **Added:** 2026-08-17 · updated 2026-08-19 (VPS bring-up: watchdog grace)

### H-14 · Create the Razorpay TEST-mode account; set the three payment env vars · [OWNER]
- **Check:** ask the owner whether a Razorpay test account exists; on the box or
  in CI, `env | grep -c CUSTOMER_CONSOLE_RAZORPAY` → `3` means done. (No repo
  command can see this — it is an external account plus deployment env.)
- **Why:** CP-9's provider seam fails closed: `POST /billing/orders` answers 503
  until `CUSTOMER_CONSOLE_RAZORPAY_KEY_ID` / `_KEY_SECRET` / `_WEBHOOK_SECRET`
  are set — **and the ₹0 discount path also goes through order creation**, so
  Fracktal's free onboarding purchase (D42) is blocked on this account existing.
  Test-mode keys suffice for the whole rehearsal; live keys stay §6(b). Creating
  any external account is owner-side (`customer_console_infrastructure.md` §7).
- **Authority:** `work_plan.md` §6(b) · `customer_console.md` §9.4/§8 gate 3
- **Added:** 2026-08-19 · CP-9 substrate session

### H-12 · Decide fate of FracktalWorks satellite-repo references · [OWNER]
- **Check:** `rg -n "FracktalWorks/" apps/services/gateway/agents.json README.md`
  → hits mean still pending.
- **Why:** The rebrand moved this repo's slug to `Hathi-Labs/Metorite`, but
  the satellite agent/skill repos (`FracktalWorks/agent-sales-assistant`,
  `FracktalWorks/agent-*`, `FracktalWorks/skill-*`) referenced in
  `agents.json`, `README.md` and `system_architecture.md` are separate real
  repositories that were NOT forked. An agent cannot know whether they stay
  upstream, get forked into Hathi-Labs, or get dropped — that is an org
  decision.
- **Authority:** CLAUDE.md §3 (never trust/invent external repo identity)
- **Added:** 2026-08-16 · rebrand session (branch `rebrand/metorite`)

### H-16 · Rotate the go-live secret set; put GITHUB_TOKEN on the box · [OWNER]
- **Check:** ask the owner — no repo command can see this. The set: both
  Supabase database passwords (Console + tenant planes) · the Google OAuth
  client secret · the DeepSeek API key. All four transited agent chat
  2026-08-18/19. Plus: `grep -c GITHUB_TOKEN /opt/acb/app/.env` on the box →
  `0` means still pending.
- **Why:** A secret in a transcript is a disclosed secret (the H-3 principle;
  these are its four new instances). None is rotated yet; the owner said "I
  will change it later on" — this entry is the *later*. `GITHUB_TOKEN` is
  separate: without it the gateway's agent warm-clones fail on every restart
  and account for most of the ~90 s cold start.
- **Authority:** `work_plan.md` §6 (credentials) · `specs/engineering_practice.md`
- **Added:** 2026-08-19 · VPS bring-up session

### H-17 · Land the governance branch (D44 · D45 · OWNER_GRANTS protocol) · [OWNER]
- **Check:** `git log main --oneline -- .claude/OWNER_GRANTS.md` → empty means
  still pending.
- **Why:** The main checkout sits dirty on `governance-d45-owner-grants`:
  plan-guard's grant mechanism, CLAUDE.md's D45 exception, D44/D45 in
  `work_plan.md` §3, and H-15 in this file — live-verified but uncommitted,
  because the harness classifier (correctly) blocks an agent finalizing its
  own guardrail changes. Commit message is staged at the session scratchpad
  (`commit_d45.txt`); the 15-case grants test travels beside it
  (`plan-guard-grants-test-content.txt` → save as
  `.claude/hooks/plan-guard.grants.test.mjs`, then run both guard suites).
  Both files are regenerable from the diff if the scratchpad is gone.
- **Authority:** `work_plan.md` §6 · D45 (in flight)
- **Added:** 2026-08-19 · VPS bring-up session

### H-18 · Verify the first production sign-in by evidence (session → owner chain) · [AGENT]
- **Check:** on the box, `journalctl -u acb-gateway | grep -i "owner bootstrap"`
  plus a session row for `vjvarada@gmail.com` after a real browser sign-in at
  `app.metorite.com` → both present means done. Needs box reach, i.e. a dated
  D45 `ALLOW <date> deploy` grant or the owner running it.
- **Why:** `ensure_owner_bootstrap()` fired at startup (log-verified), but no
  one has verified the full chain — Google OAuth → NextAuth session →
  gateway identity → owner of `default` — from a real browser. CLAUDE.md §3.8:
  verify by evidence, never by a green job.
- **Authority:** `work_plan.md` §2 WS-31 row (CP-2b) · CLAUDE.md §3.8
- **Added:** 2026-08-19 · VPS bring-up session

### H-32 · Revoke the ClickUp tokens at ClickUp · [OWNER]
- **Check:** on the box, **both** credential homes plus the vendor:
  `SELECT count(*) FROM provider_keys WHERE credential_type = 'integration' AND service = 'clickup';`
  **and** `SELECT count(*) FROM task_accounts WHERE provider = 'clickup';` —
  that second table's `credentials_encrypted` is where the real per-workspace
  ClickUp tokens live (`48_task_manager_gtd.sql:24-44`), so a check that asks
  only `provider_keys` can read `0` while live tokens remain. Then whether the
  token still authenticates against `https://api.clickup.com/api/v2/user`. Any
  one alive → still pending.
  ⚠️ There is no `integration_credentials` table — migration
  `11_integration_credentials.sql` **adds columns to `provider_keys`**
  (`credential_type`, `service`). An earlier draft of this entry queried the
  non-existent table and would have errored rather than answered.
  ⚠️ A repo-side grep cannot answer this and never could: WS-39 S1 deleted every
  *reader* of the token, which is not the same as the token being dead. A
  credential nobody reads is still a live credential at the vendor.
- **Why:** D52 retires ClickUp outright. `work_plan.md` §6 WS-27 **(c-1)** — a
  credential act, so an agent must refuse it by name. Also drop the `CLICKUP_*`
  values from the box `.env` while you are there (env-write, gated).
- **Authority:** `work_plan.md` §6 WS-27 (c-1) · D52.1
- **Added:** 2026-08-24 · WS-39 S1 session *(renumbered H-27→H-32 on 2026-08-25:
  `main` took H-27 for the e2e entry via PR #47; ids are never reused)*

### H-33 · WS-39 S3a-CLIENT slice 5: the CRUD and AI tail · [AGENT]
- **Check:** `rg -c "lensEnabled\(\)" workbench/control_plane/src/app/tasks/lib/api.ts`
  → **12** means slices 1–4 only, and slice 5 is unbuilt.
  ⚠️ Do NOT grep for `api/tasks` in `lib/api.ts` and conclude anything: the
  prefix is applied once inside `gatewayFetch` (`lib/api.ts:11`) and every call
  site passes a bare `` `/items…` ``. That spelling under-reported once already
  and would have closed this entry while the work was untouched.
- **Why:** **Slices 1–4 landed 2026-08-25.** Spine, browser day-planner, every
  browserless surface (+ the `TASKS_LENS` gateway flag), and the client-side
  **connector excision**. What is left still writes `gtd_items` when the flag
  is on:
  **(a) CRUD** — `apiOrganize`, `apiListSubtasks`/`apiAddSubtasks`,
  `apiBulkDispose`/`apiBulkArchive`, `apiMergeInto`/`apiFileUnder`,
  `apiItemDetail`, `apiItemStageOptions`, `fetchProjects`,
  `fetchStatusCatalog`, `apiCaptureBatch`, `apiUploadAttachment`.
  **(b) The LOCAL project tree** — `/hierarchy` · `/spaces` · `/folders` ·
  `/local-projects` (`routes/tasks/hierarchy.py`) and the store actions over
  them (`loadLocalHierarchy`, `createLocalSpace`, `createLocalFolder`,
  `createLocalProject`).
  🔴 **CORRECTION, 2026-08-25.** An earlier spelling of this entry filed that
  family under "DELETION, not porting — D52 leaves them with no destination."
  **That is wrong and acting on it would have deleted a live feature.** They are
  the LOCAL Space→Folder→Project tree, not a connector surface: the module's own
  header says "SYNCED projects are NOT here". They write `gtd_spaces` /
  `gtd_folders` / `gtd_projects`, and under D53 their destination is
  `pm_projects`, which already nests via `parent_project_id` (`tree.py`). It is
  a PORT. The Clarify "Where" picker is their one consumer.
  **(c) AI** — `apiAtomize`, `apiClarifyPropose`, `apiEnrichItem`,
  `apiSuggestTitle`, `apiBackfillContext`, `apiPlanProject`/`apiApplyPlan`.
  ⚠️ These need GATEWAY work, not just client wiring: `routes/tasks/ai.py`
  names `gtd_items` **12 times**. Probably its own slice.
  📌 **Measured and still true:** `fetchTaskSettings` needs **no** work —
  `gtd_settings`/`gtd_day_state`/`gtd_rollover_log` SURVIVE (D53.6).
  📌 **Three decisions still open:**
  (1) `workflow_stage` writes need a status name → `status_id` lookup against
  the task's own project; `splitPatch` THROWS on it today rather than dropping
  it, so the to-do fails loudly instead of hiding. `apiItemStageOptions` is the
  read half and is the natural place to start.
  (2) `origin` is still homeless and still per-TASK — `pm_tasks.source` is the
  nearest existing fact. Settle it before the lens touches email-captured tasks.
  (3) `horizonId`: **WS-21 owns Horizons**, DO-NOT-DISPATCH stands.
  📌 **Left standing on purpose by slice 4, for a later UI pass:** the Clarify
  destination picker still renders, with exactly one option ("Local"), and
  `GtdItem.source` / `syncState` / `providerUrl` still display for rows imported
  BEFORE the retirement. Deleting a picker is a product decision slice 4 did not
  take; the frozen rows' provenance is deliberately kept read-only, which is the
  same line S1's repair round drew.
  ⚠️ **Read `routes/projects/personal.py` and `routes/projects/planning.py`
  before designing anything.** The server side has shipped in five slices since
  2026-08-06; an agent who reads "make Tasks a lens over Projects" and starts
  writing endpoints is building a second one.
- **Authority:** `work_plan.md` §2 WS-39 row · `project_management_app.md` §12.7 ·
  `task_manager_app.md` §13.5a · `calendar_focus_os.md` §10.7-10.8 ·
  `docs/TASKS_LENS.md`
- **Added:** 2026-08-24 · WS-39 S1 session *(renumbered H-28→H-33 on 2026-08-25;
  ids are never reused, and `test_handoff_queue.py` now fences it. Re-cut to
  slices 2, 3, 4 and 5 as each landed.)*

### H-34 · Add the two Tasks-lens vars to `.env.example` (and the box) · [OWNER]
- **Check:** `rg -c "TASKS_LENS" .env.example` → `0` means still pending.
  Both `NEXT_PUBLIC_TASKS_LENS` and `TASKS_LENS` must appear, adjacent, with the
  comment that they are a pair.
- **Why:** WS-39 S3a-client slice 3 introduced the gateway half of the cutover
  switch. The pair is documented in **`docs/TASKS_LENS.md`**; what is missing is
  the entry in `.env.example`, and `.env*` is an owner-gated path
  (`work_plan.md` §6 `secrets`) — plan-guard refuses it by name, so an agent
  cannot add it.
  ```dotenv
  # The Tasks/Calendar store cutover (WS-39, D53). BOTH or NEITHER — see
  # docs/TASKS_LENS.md. Do not enable before the S3b backfill has run.
  NEXT_PUBLIC_TASKS_LENS=0
  TASKS_LENS=0
  ```
  ⚠️ **Adding them as `0` is the whole ask — do NOT turn them on.** `gtd_items`
  still holds every existing task and the S3b backfill is owner-gated and has
  not run, so enabling them early does not break the app, it **empties** it, on
  a 200, with no error to notice.
  📌 Worth doing anyway rather than waiting for the flip: a variable that exists
  and reads `0` is a variable somebody can find. One that appears for the first
  time on the day of a cutover is one that gets set on the gateway and forgotten
  in the workbench build — which is the exact mismatch `/version`'s `tasks_lens`
  field was added to make visible.
  *(H-30 is the other pending `.env.example` edit — the three dead `CLICKUP_*`
  vars. Same file, same gate; doing them in one pass costs nothing extra.)*
- **Authority:** `work_plan.md` §2 WS-39 row · §6 (`secrets`) ·
  `docs/TASKS_LENS.md` · `task_manager_app.md` §13.5a
- **Added:** 2026-08-25 · WS-39 S3a-client slice 3

### H-30 · Strip the three `CLICKUP_*` vars from `.env.example` · [OWNER]
- **Check:** `rg -n "CLICKUP" .env.example` → any hit means still pending.
- **Why:** D52 deleted every reader of these vars; the example file still offers
  `CLICKUP_API_TOKEN`, `CLICKUP_WORKSPACE_ID`, `CLICKUP_WEBHOOK_SECRET` (lines
  68–70) and mentions `AGENT_WEBHOOK_SECRET_CLICKUP` (line 193), so a fresh box
  is still told to configure a retired integration. **`.env*` writes are an
  agent refusal** (`work_plan.md` §6, the H-13 precedent), which is why this is
  a handover and not a commit. ⚠️ Measured 2026-08-24: `plan-guard.mjs` on
  `main` did **not** actually block a `sed -i` against `.env.example` — the
  refusal was honoured by the agent, not enforced by the hook. That is a
  **fence gap worth closing** (R7) and it is the more useful half of this entry.
  The D45 branch's plan-guard may already cover it; check there first.
- **Ready-made patch:** delete lines 68–70 and the `AGENT_WEBHOOK_SECRET_CLICKUP`
  clause on line 193.
- **Authority:** `work_plan.md` §6 · D52 · H-13 precedent
- **Added:** 2026-08-24 · WS-39 S1 session

### H-31 · Re-home the `event=` structlog AST guard · [AGENT]
- **Check:** `rg -n "clickup_event|zoho_event" tests/` → no test asserting the
  rule means it is still advisory.
- **Why:** Passing `event=` to a structlog logger raises `TypeError` at call
  time, so receivers must use `<source>_event=`. The AST guard that enforced
  this lived in `tests/unit/test_clickup_normalise_dlq.py`, **deleted by D52
  with the receiver it covered**. `apps/AGENTS.md` still states the rule, and
  under R7 a rule with no fence is advisory — it now says so, but the honest
  fix is to re-home the guard over the surviving Gmail/Zoho receivers.
- **Authority:** R7 (`work_plan.md` §1) · `apps/AGENTS.md` ingestion section
- **Added:** 2026-08-24 · WS-39 S1 session

### H-29 · WS-39 S3b/S3c: RUN the `gtd_*` backfill, then the drop · [OWNER]
- **Check:** `SELECT count(*) FROM gtd_items WHERE migrated_task_id IS NULL;` on the
  box → non-zero means S3b has not run (or has stragglers). `\dt gtd_items` → still
  present means S3c has not run. ⚠️ Both columns exist only once migration **189** has
  applied; if `migrated_task_id` is missing, the deploy has not carried 189 yet and
  that is the real finding.
- **Why:** ✅ **BUILT 2026-08-26 — the code half is DONE.** Migrations **189**
  (backfill) and **190** (drop) are merged and R8-verified two-org on real Postgres
  (`tests/live/live_ws39_s3b.sql`, 37 checks; `live_ws39_s3c.sql`, 22). What remains
  is exactly the part §6 (f) reserves: **running them.**
  📌 **They ship INERT.** 189 defines `gtd_backfill_to_pm()` and never calls it;
  190 refuses unless armed AND every row carries `migrated_task_id`. Deploying them
  moves nothing and drops nothing, so there is no rush and no hazard in them sitting
  applied.
  **The order, in full, is `docs/TASKS_LENS.md` → "The cutover runbook".** Short form:
  slice 5 lands → `SELECT * FROM gtd_backfill_plan;` → `gtd_backfill_to_pm(false)`
  → `gtd_backfill_to_pm(true)` → flip BOTH flags → **re-run** `gtd_backfill_to_pm(true)`
  to sweep the window → wait days → `INSERT INTO gtd_retirement_arm` → next deploy drops.
  ⚠️ **Do not arm until slice 5 has landed.** SQL cannot see an env var or an
  unported route; arming is your assertion that both flags are on and nothing still
  writes `gtd_items`. `routes/tasks/ai.py` alone names `gtd_*` 33 times today.
  ⚠️ **Rows reading `unmappable` block the drop, on purpose.** They have no
  resolvable owner (including the literal `'anonymous'` that `_uid` writes for an
  unauthenticated capture). Decide each deliberately — give the address an `app_user`,
  or delete the row — rather than widening the guard. The failure being avoided is
  not lost data; it is one member's private task published into another's lens.
  ⚠️ **`gtd_settings` / `gtd_day_state` / `gtd_rollover_log` are NOT part of this** —
  Calendar state, they survive (D53.6). Nor are the five `gtd_people*` tables, nor
  `gtd_horizons` (WS-21), nor `gtd_reviews` (WS-18), nor the local project tree
  (waits on slice 5). All pinned by name in `test_gtd_backfill.py`.
- **Authority:** `work_plan.md` §6 (f) · D53.5 · `project_management_app.md` §12.8 ·
  `docs/TASKS_LENS.md`
- **Added:** 2026-08-24 · WS-39 S1 session *(re-cut 2026-08-26 when 189/190 landed:
  this is now a RUN entry, not a BUILD one.)*

### H-27 · Nothing runs `e2e/`, and it was silently dead for an unknown period · [AGENT]
- **Check:** `rg -n "playwright|e2e" .github/workflows/pr-check.yml` → no hit means
  CI still never runs the browser suite. Separately, `rg -n "127.0.0.1" workbench/
  control_plane/playwright.config.ts` → a hit means the hydration trap is back.
- **Why:** D-PM-21 makes a real browser the **only** fence for UI behaviour here —
  `vitest.config.ts` is `environment: "node"` and does not even collect `.tsx`, and
  jsdom is refused by decision. That fence was **completely dead** and nothing said
  so: `playwright.config.ts` addressed the dev server as `127.0.0.1`, Next 16 blocks
  `/_next/*` as cross-origin from the IP, so the server-rendered shell arrived, the
  client bundle did not, hydration never completed, **no fetch was ever issued**, and
  every spec timed out against a page reading "Loading …". Measured 2026-08-21 with
  hostname as the only variable; fixed on `ws-27bg-project-rename` by pointing the
  config at `localhost`. ⚠️ **The failure mode is the point**: a page that renders
  its whole shell looks alive, so this reads as a backend fault and cost most of a
  session to isolate. The specs' own as-builts record them running green, so the rot
  set in after they were written and **no job would ever have reported it** — the
  suite is absent from `pr-check.yml` entirely. Wanted: either e2e in CI (it needs a
  browser image and ~13 s per spec), or an explicit board decision that it stays a
  local-only gate, recorded so the next person does not assume CI has their back.
- **Authority:** `specs/project_management_app.md` §8 D-PM-21 · CLAUDE.md §3.8
  (verify by evidence, never by a green job)
- **Added:** 2026-08-21 · session that built WS-27bg slice 2's rename

### H-28 · The `mypy` pre-commit hook cannot run — it names no target · [AGENT]
- **Check:** `rg -n "pass_filenames" -A 3 .pre-commit-config.yaml` → `pass_filenames:
  false` with an `entry: uv run mypy` carrying **no** `args:` means still pending.
  Reproduce: stage any `.py` file and commit — mypy exits 2 with *"Missing target
  module, package, files, or command."*
- **Why:** `pass_filenames: false` tells pre-commit not to append the staged paths,
  and nothing supplies targets in their place, so the hook fails on **every** commit
  that touches Python — it has never been able to pass. The comment above it says it
  is "diff-scoped", which is the opposite of what `pass_filenames: false` does; the
  intent and the setting disagree and only the setting runs. ⚠️ The fix is not
  simply `args: [apps, packages]`: that type-checks the whole tree on every commit,
  which is slow and would surface the existing strict-mode backlog as a block on
  unrelated work. Wanted: the diff-scoped behaviour the comment describes, which
  probably means dropping `pass_filenames: false` and letting the staged paths
  through. Discovered 2026-08-21 while committing the H-10 fix, which had to be
  landed with `SKIP=mypy` (every other hook ran and passed).
  ⚠️ This hook failing on every Python commit is *why* `SKIP=mypy` gets typed by
  habit, which is the second-order cost: a skip nobody questions is not a gate.
- **Authority:** `specs/engineering_practice.md` (testing / definition of done) ·
  CLAUDE.md §5 (an existing violation is a finding for the board, not a refactor
  smuggled into an unrelated PR)
- **Added:** 2026-08-21 · session that halved H-10 (PR #46)

---

# DONE — deleted, not archived

Nothing lives here. When an entry's Check passes, **delete the block**. Git
history is the archive; a "done" section in a loaded-into-context file is just
tokens that make the open items harder to find.
