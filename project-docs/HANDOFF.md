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

---

# OPEN

### H-26 · Decide D-SEAT-1…6 and mint CP-2h (seat-assignment UX) · [OWNER]
- **Check:** `rg -n "CP-2h" project-docs/work_plan.md` → no hit means no board
  row exists and the six decisions in `specs/customer_console.md` §6 CP-2h are
  still owed.
- **Why:** The owner's live E2E (2026-08-24) hit Settings → Organization →
  Seat assignments reading "not configured for this deployment" and asked for
  the whole seat flow to be ironed out. The proposal is written (CP-2h,
  PROPOSED): the load-bearing item is **D-SEAT-4** — the customer seat surface
  must reach the Console through the GATEWAY's existing deployment-key
  `seat_admin` door instead of the BFF's per-org `CUSTOMER_CONSOLE_ORG_KEY`
  env, which no shared multi-tenant box can carry (why the tab is dark).
  D-SEAT-1…3/5/6 fix invite-at-zero-seats, the waiting-for-a-seat card,
  release/remove/suspend semantics and the owner-takes-a-seat rule. Until
  minted, the working seat path is the OPERATOR console only.
- **Authority:** `specs/customer_console.md` §6 CP-2h · `routes/seats.py` ·
  `api/billing/_console.ts` · CLAUDE.md §5 (no second seam)
- **Added:** 2026-08-24 · onboarding E2E session

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


### H-1 · Deploy: `main` is many migrations ahead of every box · [OWNER]
- **Check:** compare `ls infra/postgres/[0-9]*.sql | sort -V | tail -1` against
  `SELECT max(filename) FROM schema_migrations;` on a box. A gap means still
  pending. ⚠️ From a clean checkout with no box access an agent can only get the
  first half — report the gap as unverified rather than closing this.
  ⚠️ The `[0-9]*` glob and `sort -V` are both load-bearing: a bare `*.sql | tail
  -1` answers `schema.generated.sql`, which sorts after every numbered migration
  and is not one. That is what the first draft of this Check did.
- **Why:** #437 merged 2026-08-13 and was never deployed; everything since has
  stacked behind it, and the pile grows every day. **We cannot roll back** (R6),
  so the longer the gap the more lands at once. Deploy applies migrations before
  restarting services, so the ORDER is safe — the risk is volume.
  ⚠️ Deliberately does not name a migration range: a range here would be state,
  which this file must never restate. It was written as "171–175" for one hour
  and 176 landed inside it.
- **Authority:** `work_plan.md` §2 WS-27 row · §6 (deploy is owner-gated)
- **Added:** 2026-08-14

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

### H-10 · A conflicted PR runs NO checks — the R1 guard's blind window · [AGENT]
- **Check:** `rg -n "pull_request" .github/workflows/pr-check.yml` → if the
  migration-prefix guard still runs only on `pull_request` (which checks out
  `refs/pull/N/merge`, a ref GitHub does not compute while a PR is conflicted),
  the window is still open. A `merge_group`/`push`-on-branch trigger, or a job
  that checks out the head ref and merges the base itself, would close it.
- **Why:** ⚠️ Measured, not guessed. `test_migration_prefixes.py` DOES catch two
  migrations at one number — verified by putting the duplicate back. It was
  simply never run: #439 sat `dirty` and reported `check_runs: 0`, **no jobs at
  all**. So the window in which a cross-branch collision is most likely is
  exactly the window in which nothing is watching, and the collision surfaces
  only when somebody hand-resolves the conflict — i.e. while editing the very
  tree that hides it.
- **Authority:** `work_plan.md` §2 WS-27 row (the R1-collision record)
- **Added:** 2026-08-14 · session that built WS-27bj

### H-11 · Finish production enablement: GitHub deploy secrets + re-enable workflows · [OWNER]
- **Check:** ALL of: Actions secrets in `Hathi-Labs/Metorite` show `HOSTINGER_*` ·
  `gh workflow list --all` shows the three workflows an agent disabled on
  2026-08-17 re-enabled (GitHub-side state; re-derive with that command, never
  from this file). Any one missing → still pending.
- **Why:** The rest of the original entry was DONE 2026-08-19 and its clauses
  are deleted rather than ticked (this file's rule): DNS (`app.` · `api.` ·
  wildcard) resolves, the box serves TLS on a **new** VPS (D44, in flight on
  the governance branch — the old VPS keeps CommandCenter for demos, so
  D41.4's "wipe" clause is superseded), both Supabase planes are bootstrapped,
  the box `.env` carries `ACB_MASTER_KEY`, and Google OAuth signs in. The
  Entra clause is **suspended by owner direction 2026-08-19** ("leave
  Microsoft Entra for now") — restore it when an M365 customer needs it, per
  D41.2's registration shape. Until the secrets + workflows land, every deploy
  is a hand-run of `deploy/hostinger/deploy.sh` over SSH.
- **Authority:** `work_plan.md` §6 · D40 · D41 · D44 (in flight)
- **Added:** 2026-08-16 · updated 2026-08-17, 2026-08-18 · trimmed 2026-08-19
  (VPS bring-up session: done clauses deleted, Entra suspended)

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

### H-27 · Revoke the ClickUp tokens at ClickUp · [OWNER]
- **Check:** `SELECT count(*) FROM integration_credentials WHERE service = 'clickup';`
  on the box, **and** whether the token still authenticates against
  `https://api.clickup.com/api/v2/user`. Either one alive → still pending.
  ⚠️ A repo-side grep cannot answer this and never could: WS-39 S1 deleted every
  *reader* of the token, which is not the same as the token being dead. A
  credential nobody reads is still a live credential at the vendor.
- **Why:** D52 retires ClickUp outright. `work_plan.md` §6 WS-27 **(c-1)** — a
  credential act, so an agent must refuse it by name. Also drop the `CLICKUP_*`
  values from the box `.env` while you are there (env-write, gated).
- **Authority:** `work_plan.md` §6 WS-27 (c-1) · D52.1
- **Added:** 2026-08-24 · WS-39 S1 session

### H-28 · WS-39: slices S2, S3a still to build · [AGENT]
- **Check:** `rg -n '"/calendar"' workbench/control_plane/src/lib/nav.ts` → no hit
  means **S2** unbuilt. `rg -l "/api/tasks/items" workbench/control_plane/src/app/tasks/`
  → any hit means **S3a** unbuilt.
- **Why:** S1 (ClickUp excision) landed; the re-plumbing it clears the way for did
  not. S2 lifts the calendar to its own `live` pane (nav count fence 8→9); S3a
  re-points the `/tasks` UI onto `/projects/my/*` so `pm_*` is the one task store.
  ⚠️ **Read `routes/projects/personal.py` before designing anything** — S3a's server
  side shipped in 2026-08-06 under D-PM-6, and an agent who starts writing endpoints
  is building a second one.
- **Authority:** `work_plan.md` §2 WS-39 row · `project_management_app.md` §12.7 ·
  `task_manager_app.md` §13.5 · `calendar_focus_os.md` §10.6
- **Added:** 2026-08-24 · WS-39 S1 session

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

### H-29 · WS-39 S3b/S3c: the `gtd_*` backfill and drop · [OWNER]
- **Check:** `psql -c "\dt gtd_items"` on the box → table present means S3c is still
  pending. For S3b, `SELECT count(*) FROM gtd_items WHERE deleted_at IS NULL;` → a
  non-zero count after S3a shipped means rows are still stranded in the old store.
- **Why:** D53.5 retires `gtd_*` in three releases; (2) the backfill into members'
  personal `pm_project`s and (3) the drop are **data moves against live customer
  data** — the WS-29 cutover class, on a ladder that cannot roll back (R6). Building
  and R8-testing them against scratch databases is agent-safe; running them is not.
  ⚠️ **Prove the mapping two-org on real Postgres first.** The failure mode is not
  lost data — it is a mis-mapped `member_email` publishing one member's *private*
  task into somebody else's lens.
  ⚠️ **`gtd_settings` / `gtd_day_state` / `gtd_rollover_log` are NOT part of this** —
  they are the Calendar app's per-member state and survive (D53.6).
- **Authority:** `work_plan.md` §6 (f) · D53.5 · `project_management_app.md` §12.8
- **Added:** 2026-08-24 · WS-39 S1 session

---

# DONE — deleted, not archived

Nothing lives here. When an entry's Check passes, **delete the block**. Git
history is the archive; a "done" section in a loaded-into-context file is just
tokens that make the open items harder to find.
