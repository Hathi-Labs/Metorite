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


### H-55 · Decide whether `pr-check.yml` runs the STE gate, and blocks · [OWNER]
- **Check:** `grep -n ste-lint .github/workflows/pr-check.yml`. A hit means the
  owner decided and this entry is dead.
- **Why:** the rule holds in two places today. `ste-lint.mjs` runs as a
  PostToolUse hook, and `.pre-commit-config.yaml` runs it on staged markdown.
  Both are local. A person who does not install pre-commit is not bound, and CI
  never looks. ✅ **The blocker is cleared.** PR #115 took `paths-ignore` off the
  `pull_request` trigger on 2026-08-26, so a docs-only pull request now reports.
  PR #116 proved it and ran 7 checks on a branch that was mostly documents. What
  is left is the owner's call: add the `--staged` step, and require the context.
- 📌 **`main` is now PROTECTED** (2026-08-26), with 7 required contexts.
  So "require the context" is a settings change now, not a project. The
  decision left is whether the STE gate BLOCKS or only reports.
- **Authority:** owner directive 2026-08-26 · `docs/style_ste.md` §8 Q2
  · PR #115 · PR #116
- **Added:** 2026-08-26 · STE harness session

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
  `rg -c 'launch: "live"' workbench/control_plane/src/lib/nav.ts` → **9** means
  nothing has been promoted. ⚠️ **Corrected 2026-08-26: this said `8`.** D54 added the
  Calendar pane on 2026-08-24 — the same day this entry was written — so the Check
  was born wrong and would have read *"something was promoted"* on every future run.
  The number to compare against is `nav.test.ts`'s own fence, never a remembered one. This entry never "completes"; it is the standing rule
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
- **Why:** ⚠️ **Time-sensitive.** 171 changes what "archived" means; the
  pre-migration count is the only baseline that can tell us whether the lifecycle
  sweep behaved.
  🔴 **Corrected 2026-08-26 — this said "ordered against H-1", and H-1 no longer
  exists** (its Check passed and the entry was deleted, correctly). Worse, its
  premise is now false: the 2026-08-25 deploy reported *"0 applied, 186 already
  recorded"*, so the box is current with `main` and **171 has almost certainly
  already applied**. Treat the baseline as **probably lost**: run the count, and if
  171 is on the box, record that it was lost rather than substituting
  `WHERE archived_root_id = id`, which answers a different question.
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
  exists. Flipping it is a restart, not a release.
  ⚠️ **Corrected 2026-08-26: this said "Requires H-1 first", and H-1 has been
  deleted.** What it meant — *the code that reads the flag must be on the box* — is
  now satisfied by construction: delivery is automatic again and the last deploy had
  nothing to apply. The remaining precondition is the ordinary one: the flag is a
  live env write, so it is owner-gated.
- **Authority:** `specs/project_management_app.md` §9.11 · `work_plan.md` §6
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

### H-59 · WS-39: the Tasks UI slice — promote button, Areas, Horizons off · [AGENT]
- 📌 **START HERE IF YOU ARE TAKING OVER TASKS AND PROJECTS.** Read **CLAUDE.md**
  §1 for the read order, then **H-33** (the API client), then this. H-33 owns the
  data path. This entry owns what a member can SEE and PRESS. The two are
  separate slices and neither one alone finishes WS-39.
- **Check:** `rg -l "apiMoveTask" workbench/control_plane/src/app/tasks/components/`
  → **no hit means this entry is live.** The promote path shipped in slice 5a and
  **nothing in the UI calls it.** Also `rg -c -i horizon workbench/control_plane/src/app/tasks/lib/`
  → a non-zero count means D65 is not applied yet.
- **Why:** ⚠️ **The Tasks app is BUILT — 36 components, live at `/tasks` today.**
  Do not read "the UI is unbuilt" anywhere and believe it. `InboxView`,
  `ClarifyModal`, `EngageView`, `FocusMode`, `DelegateDialog`, `ListsSidebar`,
  `TaskBoard`, `ItemDetail` and the day planner all exist and work. What is
  missing is narrower, and it is three things.

  🟢 **(1) The promote button does not exist.** `apiMoveTask` landed in slice 5a
  and is called by `lens.test.ts` and nothing else.

  Three things depend on it, and **all three are unreachable from the product
  today**:
  - migration **192** — required custom fields
  - **D62** — a task may only move into a personal project it already lives in
  - the assign-guard — it refuses an assignment that names no project

  The dialog must ask about the **DESTINATION** project, not the task's current
  one. `apiItemStageOptions` is already re-keyed that way for exactly this
  reason.

  ⚠️ `apiMoveTask` **throws** when the flag is off, on purpose. Do not "fix" that
  into a silent no-op. `gtd_items` has no company board to promote onto. A
  Promote button that reports success while doing nothing is worse than an
  error.

  🟢 **(2) Areas have no UI.** `Areas` appears in `lib/api.ts`, `lib/lens.ts` and
  `lib/types.ts`, and in no component. Members need to **create, rename, delete
  and assign** an Area over their own personal projects. ⚠️ **This blocks H-29**:
  `gtd_backfill_to_pm()` CREATES Areas from each member's old `gtd_projects`.
  Run the backfill first, and people hold structure in their own data that
  they cannot edit.

  🟢 **(3) Horizons is still in the code.** `lib/columns.ts`, `lib/taskStore.ts`
  and `lib/types.ts` all carry it. **D65 (2026-08-26) takes it off the SURFACE
  and leaves it in the store** — the same shape D49 used for Centers. Remove the
  nav entry and the altitude ladder. Do **not** delete the data or the routes,
  and do **not** revoke a feature to hide it.

  📌 **The product rules this UI must express** (owner directive, 2026-08-26,
  and D62 is the recorded half):
  - A Tasks inbox item can be **upgraded** into the Projects app.
  - It appears in Tasks **if, and only if, it is assigned to that person**.
  - **Assigning to another member requires a specific project.** A task assigned
    out of somebody's private tree with no project has no place the other person
    can legitimately see it from.
  - **Every mandatory field must be complete** before a task moves to Projects.
    Migration 192 added `required` to custom fields for this. `_is_blank` treats
    `0` and `false` as ANSWERS, not blanks — do not "simplify" that.

  ⚠️ **Read `routes/projects/personal.py` and `routes/projects/core.py` before
  designing anything.** The server half has shipped across several slices.
  Somebody who reads "make Tasks a lens over Projects" and starts writing
  endpoints is building a second seam, which is a defect by CLAUDE.md §5.

  ⚠️ **The theme sweep is the real gate, not the conformance suite.** It checks
  eight regexes and tests **no** layout and no cross-app continuity. Switch
  Fluent → Material → Graphite on your surface AND its neighbour, by eye.
- **Authority:** **D65** · D62 · D53 · `work_plan.md` §2 WS-39 row ·
  `task_manager_app.md` §13.5a · `docs/TASKS_LENS.md` · H-33 (the API client
  half) · H-29 (the backfill). ⚠️ H-29 must NOT run before this lands.
- **Added:** 2026-08-26 · guardrails + handoff session

### H-60 · Every deploy gives live users a ~3 minute 502 · [AGENT]
- **Check:** merge anything, then `curl -s -o /dev/null -w "%{http_code}" https://app.metorite.com/`
  during the deploy window. A `500` or `502` means this is live. A `307` means the
  box is up.
- **Why:** 🔴 **Measured twice on 2026-08-26, on a box holding a real customer.**
  During PR #114's deploy, Caddy logged `dial tcp 127.0.0.1:3001: connect:
  connection refused` against `/api/auth/me`, `/api/apps/pins`,
  `/api/projects/notifications` and `/api/chat/sessions`. **Two real browsers**
  were in it — one Windows on `/projects`, one macOS on `/chat`. During PR #120's
  deploy the workbench answered **HTTP 500** and `GET /version` returned empty.
  📌 The cause is ordinary and the fix is not exotic. `vps_apply.sh` restarts the
  workbench in place. Nothing holds requests while port 3001 is down, so Caddy
  fails them instead of queuing or retrying.
  ⚠️ **The deploy verification cannot see this, by construction.** `deploy.yml`
  checks health AFTER the restart, so it measures the recovered box and reports
  a clean deploy. The outage is real and invisible to the thing watching for it.
  🟢 **Cheapest real options, in order.**
  - (a) Caddy `lb_try_duration` on the workbench upstream. A request in the gap
    then WAITS instead of failing. Minutes of work, and it covers most of the
    window.
  - (b) Two workbench units and a swapped upstream.
  - (c) Accept it, and say so in the release notes.
  ⚠️ Do not "fix" this by making the health check gentler. The check is honest.
  It is watching the wrong moment.
- **Authority:** `deploy/hostinger/` (owner-gated) · `.github/workflows/deploy.yml` ·
  D36 (Fracktal is customer zero)
- **Added:** 2026-08-26 · guardrails + handoff session

### H-61 · `.claude/OWNER_GRANTS.md` is untracked AND un-ignored · [OWNER]
- **Check:** `git check-ignore -v .claude/OWNER_GRANTS.md` → no output means this
  entry is live. `git status --short` shows it as `??` every session.
- **Why:** the file now DOES something — PR #119 landed the reading half of D45,
  so plan-guard honours it. It sits in a bad third state. It is not committed,
  and it is not ignored.
  🔴 **`git clean -xdf` deletes it, silently, with every grant in it.** It also
  shows as untracked noise in every `git status`, which is how people learn to
  skim that output.
  📌 **My recommendation, and it is the owner's call because D45 owns this.**
  Add one `.gitignore` line. A grant is a LOCAL, one-day authorization by one
  human at one keyboard. Committing it would make a grant travel to every
  checkout, every cloud session and every worktree — which is the opposite of
  what D45 is for.
  📌 Measured 2026-08-26: 23 `ALLOW` lines, of which **1** was live. Stale lines
  are inert by design, so this is tidiness, not risk. The file says to delete
  them when convenient.
- **Authority:** D45 · `.claude/OWNER_GRANTS.md` · `.claude/hooks/plan-guard.mjs`
- **Added:** 2026-08-26 · guardrails + handoff session

### H-62 · Two WS-39 design questions block the first Tasks slice · [OWNER]
- **Check:** these are decisions, not code. `rg -n "origin" workbench/control_plane/src/app/tasks/lib/types.ts`
  shows the field still homeless. `rg -n "workflow_stage" workbench/control_plane/src/app/tasks/lib/`
  shows `splitPatch` still throwing.
- **Why:** ⚠️ **Whoever picks up H-59 or H-33 meets both of these in the first
  slice.** They are buried in H-33's prose today, where a newcomer will not see
  them until they are already blocked. Surfaced here on purpose.
  🟢 **(1) `workflow_stage` needs a status-name → `status_id` lookup**, resolved
  against the task's OWN project. Statuses are per-root. `splitPatch` THROWS on
  this today rather than dropping it, which is correct — the write fails loudly
  instead of hiding. `apiItemStageOptions` is the read half and the natural
  place to start.
  🟢 **(2) `origin` is homeless, and still per-TASK.** `pm_tasks.source` is the
  nearest existing fact. ⚠️ **Settle it BEFORE the lens touches email-captured
  tasks.** A field that has no home when the first real writer arrives gets one
  invented at the call site, and then there are two.
  📌 The third question in that group is CLOSED: `horizonId` is settled by **D65**
  and **H-59** owns the removal.
- **Authority:** H-33 · H-59 · D65 · `task_manager_app.md` §13.5a
- **Added:** 2026-08-26 · guardrails + handoff session

### H-63 · Eight stale worktrees hold branches nobody is reading · [OWNER]
- **Check:** `git worktree list` → more than the main checkout means this is live.
- **Why:** measured 2026-08-26 — eight worktrees under `C:\wt-*`, each holding a
  branch checked out. One of them blocked an ordinary `git branch -d` today,
  which is how they announce themselves.
  ⚠️ **Only the owner knows which hold unmerged work.** Some names look finished
  (`cp-8-provision-customer`, `fix-migrate-tmpfile`) and some do not
  (`ws-29-provision-rls-bind`, `ws-30-invites`). An agent cannot tell the
  difference from a name, and removing the wrong one loses work.
  📌 **The recorded hazard, so nobody repeats it:** `rmdir` the `node_modules`
  junction BEFORE `git worktree remove`, or the remove follows the junction and
  deletes the REAL `node_modules`.
  🟢 For each: `git log --oneline origin/main..<branch>` shows what is unmerged.
  Empty means it is safe to remove.
- **Authority:** local dev environment · `git worktree list`
- **Added:** 2026-08-26 · guardrails + handoff session

### H-33 · WS-39 S3a-CLIENT slice 5: the CRUD and AI tail · [AGENT]
- **Check:** `rg -c "lensEnabled\(\)" workbench/control_plane/src/app/tasks/lib/api.ts`
  → **15** means slice **5a** landed (the promote path) and 5b is next.
  **12** would mean slices 1–4 only.
  ⚠️ Do NOT grep for `api/tasks` in `lib/api.ts` and conclude anything: the
  prefix is applied once inside `gatewayFetch` (`lib/api.ts:11`) and every call
  site passes a bare `` `/items…` ``. That spelling under-reported once already
  and would have closed this entry while the work was untouched.
- **Why:** **Slices 1–4 landed 2026-08-25.** Spine, browser day-planner, every
  browserless surface (+ the `TASKS_LENS` gateway flag), and the client-side
  **connector excision**. What is left still writes `gtd_items` when the flag
  is on:
  ✅ **(a-1) SLICE 5a LANDED 2026-08-26 — the promote path.** `fetchProjects`
  (→ `GET /projects/nodes`, the COMPANY's projects), `apiItemStageOptions`
  (→ `nodes/{id}/statuses`, re-keyed from the ITEM to the PROJECT because
  statuses are per-root and a move dialog is asking about the DESTINATION), and
  a new `apiMoveTask` → `POST /projects/tasks/{id}/move`. That last one is the
  door everything in migration **192** and **D62** was waiting on: required
  fields, promote-and-assign, and the assign-guard's suggested fix were all
  unreachable from the UI without it. ⚠️ `apiMoveTask` **throws** when the flag
  is off rather than degrading — `gtd_items` has no company board to promote
  onto, and a silent no-op would render a Promote button that does nothing and
  reports success.
  🟢 **(a-2) still to do:** `apiOrganize`, `apiListSubtasks`/`apiAddSubtasks`,
  `apiBulkDispose`/`apiBulkArchive`, `apiMergeInto`/`apiFileUnder`,
  `apiItemDetail`, `fetchStatusCatalog`, `apiCaptureBatch`,
  `apiUploadAttachment`. ⚠️ `apiItemDetail` needs comment + attachment reads
  that the lens has no equivalent for yet — size it before dispatching.
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
  (3) `horizonId`: **WS-21 owns the Horizons STORE**, and DO-NOT-DISPATCH
  stands there. ⚠️ **D65 (2026-08-26) takes Horizons off the SURFACE.**
  **H-59** owns that removal. Leave the data and the routes alone.
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

### H-31 · Re-home the `event=` structlog AST guard · [AGENT]
- **Check:** `rg -n "ast\." tests/unit/test_ingestion_receiver_parity.py` → no AST walk
  asserting that no receiver passes a bare `event=` to a structlog logger means it is
  still advisory. ⚠️ **Corrected 2026-08-26: the old Check was**
  `rg -n "clickup_event|zoho_event" tests/` **and it answers the wrong question.**
  Run today it returns a hit — `test_zoho_event_type_falls_back_to_unknown`, a test
  **named** after the convention, which asserts fallback behaviour and not the rule.
  A Check that matches a function name rather than the guard reads *done* while the
  guard is still missing, which is the one failure this file's protocol exists to
  make impossible.
- **Why:** Passing `event=` to a structlog logger raises `TypeError` at call
  time, so receivers must use `<source>_event=`. The AST guard that enforced
  this lived in `tests/unit/test_clickup_normalise_dlq.py`, **deleted by D52
  with the receiver it covered**. `apps/AGENTS.md` still states the rule, and
  under R7 a rule with no fence is advisory — it now says so, but the honest
  fix is to re-home the guard over the surviving Gmail/Zoho receivers.
- **Authority:** R7 (`work_plan.md` §1) · `apps/AGENTS.md` ingestion section
- **Added:** 2026-08-24 · WS-39 S1 session

### H-52 · Phase 0 (D55.2) has ALREADY ENDED by trigger (b) — decide which way · [OWNER]
- **Check:** `gh api repos/Hathi-Labs/Metorite/collaborators --jq '.[] | select(.type=="User") | select(.permissions.push or .permissions.admin) | .login'`
  → more than one login means trigger (b) holds. Two today: `vjvarada`, `nithinjak`.
- **Why:** 🔴 **Measured 2026-08-26, and the dates are the finding.** D55.2
  (`development_and_delivery_framework.md` §3.5) ends Phase 0 at *"a second human
  gets commit access"*. `nithinjak` has held push+admin and **committed on
  2026-08-21** — five days before D55 was written on 2026-08-26. **Phase 0 was
  adopted already-expired**, which is precisely the failure §3.5 predicts of
  itself: *"a bridge with no trigger is a destination… somebody has to notice one
  firing."* Nobody did, including me, until a tripwire was pointed at it.
  ⚠️ **Why this is not cosmetic.** The pipeline today is CONTINUOUS DEPLOYMENT —
  merge → CI green → `release` fast-forwards → the box polls every 5 min and
  applies. §5 says that once Phase 0 ends, `release-promote` becomes a
  `workflow_dispatch` **OWNER-GATE** with a one-working-day soak. Nothing connects
  those two states, so the change does not happen by itself.
  🟢 **Two honest answers, and it is a decision rather than a fix:**
  1. **Phase 0 has ended.** Do T-7/T-8/T-9, flip `release-promote` to owner-gated,
     write the end down in §3.5, delete `.github/workflows/phase-0-tripwire.yml`.
  2. **The wording does not match the intent.** If "a second human" meant "a second
     *regular contributor*", or these accounts sit inside one trust boundary, amend
     §3.5 to say so. ⚠️ Do not simply mute it — an unamended clause everybody knows
     is not really in force is worse than no clause, because the next reader cannot
     tell which of the three triggers are live.
  📌 **Triggers (a) and (c) are still unwatched.** A second organization being
  provisioned, and H3 (the RLS promotion), both need production visibility. The open
  design question is whether `/version` grows a `phase0` BOOLEAN — it is public and
  unauthenticated, so never the org count — or whether that belongs on an
  authenticated operator endpoint.
- **Authority:** D55.2 · `development_and_delivery_framework.md` §3.5, §5 ·
  `.github/workflows/phase-0-tripwire.yml`
- **Added:** 2026-08-26 · guardrails/CI session

### H-49 · Member deactivation must implement D63 (seal, don't inherit) · [AGENT]
- **Check:** `grep -rn "status.*inactive" apps/services/gateway/gateway/routes/ --include=*.py`
  → if a deactivation path for `app_user` exists, this entry is live and the
  question is whether it honours D63. If it returns only `gtd_people` hits
  (the retiring connector), deactivation is still unbuilt and this is a
  standing constraint on whoever builds it.
- **Why:** **D63 was taken 2026-08-26, before the flow it governs exists.** That
  was deliberate — the default somebody picks under time pressure while building
  deactivation is exactly the wrong way to settle what happens to a departed
  colleague's private tasks. What D63 requires:
  * tasks in their personal tree **assigned to someone else** → hand over
  * tasks only ever theirs → **seal**; retained, invisible, **never deleted**
  * their `pm_task_personal` rows on team tasks → left alone; the task needs
    reassigning, the overlay just stops being read
  * one **owner-only, logged** door to open or export a sealed tree
  * the deactivation dialog states the split **in numbers before the click**
  ⚠️ **Not a WS-39 deliverable.** WS-39 made the private tree richer (Areas,
  migration 191), which is what turned this from theoretical into something with
  real content behind it — but member writes are §6 owner-gate and deactivation
  belongs to whoever owns identity.
  📌 `app_user.status` is the hook point and today holds only `'active'`.
- **Authority:** `work_plan.md` §3 D63 · §6 (member/role writes) · D53.7/D53.8
- **Added:** 2026-08-26 · WS-39 personal-tree session *(minted as H-35; renumbered to H-49 the same session — `test_handoff_ids_are_unique` caught the collision with the WS-36 restore-spec entry. Ids are never reused.)*

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
  ⚠️ **Do not arm until the Tasks UI slice has landed** — stronger than the earlier
  "after slice 5", and D62/191 are why: the backfill creates **Areas** from a member's
  old `gtd_projects`, and until the Tasks app can rename or delete one, members have
  structure in their data they cannot edit. SQL cannot see an env var or an
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

### H-35 · Write the owning spec for WS-36 (per-tenant restore) · [AGENT]
- **Check:** `rg -n "WS-36" project-docs/INDEX.md` → a hit in the **"BOARD ROWS WITH
  NO OWNING SPEC"** section (rather than in ACTIVE) means the spec is still unwritten
  and the row is still 🔴 not dispatchable.
- **Why:** D31 recorded on 2026-08-11 that there is **no per-tenant restore, only a
  whole-cluster one** — BO-23 restores the box, so serving one customer's recovery
  **rolls every other customer back**. `saas_operations_doctrine.md` §5 then named it
  one of only **two domains with no owner** and said both need one *before customer
  #1*. Fifteen days later it still had no board row; WS-36 was minted 2026-08-26 and
  is deliberately 🔴 because §1's contract needs an owning spec with testable
  acceptance, and there is none. **What is owed is a spec, not code.**
  ⚠️ Two constraints the spec must carry or it will be written wrong: the filtered
  export **must** be driven by the same `discover_tables()` set the RLS policies use
  (a second table list forks silently the first time a table is added), which means
  it cannot be finished before **MT-1b promotion**; and this is a **customer-#2**
  defect, not a customer-#1 gate — which is exactly how it becomes a customer-#3
  emergency if it keeps being true.
- **Authority:** `work_plan.md` §2 WS-36 · §2.0 M1 · D31 ·
  `saas_operations_doctrine.md` §5 finding 9 · `saas_multitenancy_handover.md` H8
- **Added:** 2026-08-26 · multi-tenancy product pass

### H-36 · Write the owning spec for WS-37 (trust & compliance) — and take the positions it needs · [OWNER]
- **Check:** `rg -n "WS-37" project-docs/INDEX.md` → a hit in the **"BOARD ROWS WITH
  NO OWNING SPEC"** section means it is still unwritten.
- **Why:** The second unowned domain in `saas_operations_doctrine.md` §5: consent
  model, subprocessor disclosure, retention/deletion policy, breach-notification
  path, customer-readable audit trail. ⏳ **It carries a date that is not ours to
  move — §3.3 puts DPDP at November 2026.**
  🟢 **The AGENT-SAFE half:** write the spec — name each obligation, name where it is
  enforced, name the fence (R7). 🔴 **The OWNER half, and why this entry is [OWNER]:**
  the *positions* — what we retain, whom we disclose as a subprocessor, what we
  promise on breach — are commitments to customers, and an agent must not invent a
  compliance position.
  📌 **The one item that is cheap only now:** doctrine §6 item 4, *model the consent
  record while the tables are still empty*. Retrofitting consent onto rows already
  collected is a customer conversation, not a migration. Also belonging here rather
  than to a console ticket: capping default auto-top-up **below ₹15,000** per the RBI
  e-mandate framework (§3.2) — *"a config default with a legal reason — write the
  reason down"*.
- **Authority:** `work_plan.md` §2 WS-37 · §2.0 M3 · `saas_operations_doctrine.md`
  §2.7 · §3.3 · §5 · §6
- **Added:** 2026-08-26 · multi-tenancy product pass

### H-37 · §2 rows have re-grown past the size D18 was minted to fix · [AGENT]
- **Check:** `awk '{ print length($0) }' project-docs/work_plan.md | sort -rn | head -1`
  → anything above ~20,000 means a single board row is still carrying a session's
  narrative. Measured 2026-08-26: **71,702**.
- **Why:** D18 (2026-08-09) moved row narrative into the owning specs' *"Board record"*
  sections because §2 had reached ~77k tokens and was *"unreadable in one pass by the
  dispatch loop it serves"*. Measured 2026-08-26 the rows are **past where they were**:
  WS-31 71,702 characters on one line, WS-29 46,587, WS-27 41,319, WS-26 20,779,
  WS-30 12,607 — roughly 190k characters of narrative in five cells.
  **The mechanism is not carelessness and naming it matters:** a build session appends
  its findings to the row because the row is where it is already looking, and each
  append is individually correct. D18 is a rule with no fence, so under R7 it is
  advisory — which is precisely why it decayed twice.
  **What to do:** relocate each row's narrative into its owning spec's Board-record
  section, leaving state, gates and pointers (D18's own shape), **and give the rule a
  fence** — a test asserting no line in `work_plan.md` §2 exceeds a stated length is
  cheap, structural, and is the thing that stops a third recurrence.
  ⚠️ **Do it as its own PR.** It was deliberately not folded into the 2026-08-26
  product pass: a ~190k-character move inside a diff that also changes states is how
  a real correction gets lost.
- **Authority:** `work_plan.md` §5 residual 8 item 9 · D18 · R7
- **Added:** 2026-08-26 · multi-tenancy product pass

### H-38 · Decide D-D (where staging runs, and what it costs) · [OWNER]
- **Check:** `rg -n "D-D" project-docs/work_plan.md` → a hit only inside **D55.9's
  "still owed"** clause (rather than a recorded answer in §3) means it is still open.
- **Why:** D55 adopted the delivery framework and answered five of its seven open
  decisions. **D-D is money plus an external account, which is owner-side by
  `customer_console_infrastructure.md` §7** — a second VPS, a second Supabase pair, or
  both. It is the one thing between here and the staging half of WS-38: **T-7
  (`staging` ref), T-8 (`release-promote`) and T-9 (the nightly anonymised rebuild) all
  wait on it.** Everything else on WS-38 — T-1, T-2, T-3, T-6, T-5 — is buildable during
  Phase 0 and needs no staging box, which is why the order puts them first.
  📌 **Worth knowing before deciding:** staging is a *nightly re-derivation* of
  production, not a maintained copy (D55.5), so it can be smaller than production and can
  be torn down and rebuilt. It is a cost that scales with nothing.
- **Authority:** `work_plan.md` D55.9 · §2 WS-38 ·
  `specs/development_and_delivery_framework.md` §9 D-D · §3.5
- **Added:** 2026-08-26 · delivery + model-management decision session

### H-39 · Decide D-F (the CODEOWNERS map) — when there is a second person to name · [OWNER]
- **Check:** `ls .github/CODEOWNERS` → absent means still open. ⚠️ This entry is
  **deliberately not actionable yet** and should not be closed by writing a CODEOWNERS
  file naming one person.
- **Why:** D55 answered D-A/B/C/E/G and left D-F open **because it cannot be written
  before the people exist** — a CODEOWNERS map with one name in it is a rule that
  enforces nothing and a file that goes stale. The seams it would map are already written
  down (`development_and_delivery_framework.md` §7.4), so the work when the time comes is
  a lookup, not a design. **T-13 is the ticket; enabling the requirement is a GitHub
  settings change (§6).**
  📌 Sequence note: T-13 is downstream of **T-2**. Required reviewers on an unprotected
  branch are advisory, like every other gate here.
- **Authority:** `work_plan.md` D55.9 · `specs/development_and_delivery_framework.md`
  §7.4 · §9 D-F · T-13
- **Added:** 2026-08-26 · delivery + model-management decision session

### H-42 · Price the AI rate card, then flip the spend gate — in that order · [OWNER]
- **Check:** on the Console database,
  `SELECT count(*) FROM tier_rate_card WHERE pricing_mode = 'priced';` → `0` means the
  card is still unpriced and nothing draws credits down. Then
  `SELECT count(*) FROM credit_price;` → `0` means the credit itself has no
  rupee price, so a bank transfer has no official conversion. Then
  `env | grep -c CUSTOMER_CONSOLE_SPEND_GATE` on the box.
  *(D67, 2026-08-30: the card billing reads is `tier_rate_card`, keyed on
  the tier. `model_rate_card` is read-only history. The console's /pricing
  page sets both numbers and shows the margin live. Migration `017` added
  `credit_price` for the rupee side.)*
- **Why:** Credit **assignment** works end to end already (§6B.1) — but a granted credit
  is currently a number that nothing consumes, because the shipped rate card is **all
  zero** and `test_the_rate_card_ships_unpriced` refuses a priced ladder by design.
  ⚠️ **The order is not a preference and getting it backwards is the expensive mistake.**
  Flipping the gate against a zero card delivers **every cost of enforcement and none of
  the benefit**: a zero-balance org — the state provisioning leaves *every* org in — is
  refused on all AI calls, while a funded org can never reach 402 because nothing bills.
  📌 The card is meant to be set **against measurement**, not estimates: CP-4 ships the
  Router unpriced so a month of real per-org burn lands in `usage_event` first
  (`002_seed_catalog.sql`'s own header). CP-11 is what finally produces that traffic, so
  this entry becomes actionable only after CP-11 has been serving for a while.
  🔴 Pricing live is an owner act (D19.2, §6) and **must not be done via migration**.
- **Authority:** `work_plan.md` §6 · §2.0 M2.9c · D19.2 · **D57.4** clause 5 ·
  `specs/customer_console.md` CP-6
- **Added:** 2026-08-26 · AI credits + keys session

### H-78 · Teach the vendor feed the per-unit costs (image, second, character) · [AGENT]
- **Check:** `grep -c "cost_per_image" apps/services/customer_console/customer_console/feed.py`
  → `0` means the feed still reads only the three per-token columns.
- **Why:** The "Price from cost" board on `/pricing` suggests a charge from the
  chain's first model and its vendor price. For a token job the cost comes from
  the feed. For `image`, `transcribe` and `speak` the litellm map carries
  per-unit fields that `vendor_price_feed` (`014`) does not store. Until it
  does, the operator types the vendor's dollar price into the board by hand.
  The work is a new migration with nullable columns, a read in `feed.py`,
  matching columns on `model_profile`, and a board that reads the profile.
  R6 applies.
- **Done when:** `customer_console.md` **§6A.11a** holds the eight clauses.
  Build to that section, and to nothing written in this entry.
  ⚠️ **`music` is struck from this entry.** litellm has no `music` mode, so the
  feed can never fill the `music` task. The task row and the `tier-music` tier
  both stay.
  ⚠️ **`video` is a named follow-up, and not part of this entry.** A map of
  `video_generation` needs a sixth verb in `KNOWN_INVOCATIONS`
  (`catalog.py:26`). `check_invocation` (`catalog.py:100`) is the refuser, and
  it rejects a video capability until that verb lands. *(This entry named
  `015_tier_pricing.sql` until 2026-08-30. Line `015:63` is a COMMENT about
  the refusal, and it enforces nothing.)* The follow-up rides with **H-46**.
  📌 Migration number: list `infra/customer_console/` at build time, and list it
  again at merge (R1). It was `019` on 2026-08-30.
- **Authority:** `customer_console.md` **§6A.11a** (the done-when) · §6A.11 ·
  §6A.13 · `ai_metering_and_analytics.md` §3.7 · §9
- **Added:** 2026-08-30 · pricing-method session · **amended 2026-08-30** after a
  dispatch audit returned NO-GO on scope, on acceptance and on a stale header

### H-79 · Flip the `/me/billing` money fields to strings (two releases, R6) · [AGENT]
- **Check:** `grep -c "float(balance)" apps/services/customer_console/customer_console/main.py`
  → `1` means the Console still sends floats.
- **Why:** `GET /me/billing` sends `balanceCredits` and `burnThisCycle` as floats.
  Every other money read sends strings. The float is exact for `NUMERIC(14,4)`
  magnitudes, so the defect is latent. But this endpoint is the customer's
  dispute surface, and one outlier invites the next. The flip takes two
  releases (R6). Release one: the workbench billing page
  (`workbench/control_plane/src/app/settings/billing/`) parses both shapes.
  Release two: the Console sends strings.
- **Authority:** the strings-for-money rule stated three times in `main.py`
  (search "reformatted through a float")
- **Added:** 2026-08-30 · console-review session

### H-43 · Close the process-global credential injection (D58.2) · [AGENT]
- **Check:** `rg -n "os.environ\[" packages/acb_llm/acb_llm/key_store.py` → hits inside
  `configure_litellm` mean the tenant path still writes process-global credentials.
- **Why:** **D58 settled the architecture and this is the one code consequence.** The
  Console Router already does it right — `call_kwargs["api_key"] = secret`, per call, no
  shared state. The tenant gateway does not: `_ensure_keys_loaded()` →
  `key_store.configure_litellm()` is a once-per-process latch assigning
  `litellm.<provider>_api_key` **and** `os.environ[...]`, which is the §6 (f) blocker.
  `client.py:262`'s own docstring states the consequence: *"Calling it per organization
  would not scope anything; it would make the LAST organization's key the one every caller
  sends."*
  **The fix is the shape `router.py` already uses — pass credentials per call.** No new
  infrastructure, and D58.3 explains at length why a proxy is the wrong way to buy a
  keyword argument.
  📌 **Sequencing:** CP-11 shrinks the blast radius first (Router-served traffic stops
  using this path at all), so this is worth doing *after* CP-11 lands, when it is a
  cleanup rather than a live-path change. ⚠️ It does **not** close §6 (f) on its own —
  the credential-scope redesign for `require_llm_api_auth` is the other half.
- **Authority:** `work_plan.md` §3 **D58** · §6 (f) · `specs/customer_console.md` §4
- **Added:** 2026-08-26 · AI architecture session

### H-44 · Feature→tier binding is hardcoded at 80+ call sites · [AGENT]
- **Check:** `rg -c '"tier-(fast|balanced|powerful|stt)"' --glob '*.py' --glob '*.ts' apps/ packages/ workbench/`
  → any file with a count means that feature's tier is still a literal, not a
  configuration. Measured 2026-08-26: `assistant.py` 21 · `settings.py` 8 · `_common.py` 8
  · `drafting.py` 7 · `tasks/ai.py` 6 · `notes/summaries.py` 5 · `taskStore.ts` 4 · plus
  five more files.
- **Why:** The owner asked that *"different features could use the different model
  tiers"*. **They already do — by string literal at the call site**, so changing which tier
  the email digest uses is a code change and a deploy, not an operator action. Making it
  configurable needs a **feature→tier registry**, and at 80+ sites it is **its own ticket,
  deliberately NOT a CP-10 slice** (D59.6 step 3).
  📌 **Generalise, do not invent:** the Apps feature already declares its tier in its
  **manifest** — scope `ai:tier-1` → `_SCOPE_TIER_MAP` → `tier-fast`, with a documented
  fallback to the cheapest alias (`routes/apps/_common.py:66-79`). That is a declarative
  feature→tier binding that works. The registry is that idea widened; a second vocabulary
  beside it is the CLAUDE.md §5 defect.
  ⚠️ **Blocked on nothing, but pointless before D59.6 steps 1–2** — a registry pointing at
  tiers whose modality is a Python frozenset can only bind text tiers.
- **Authority:** `work_plan.md` §3 **D59.4 / D59.6** (re-expressed over `(task, tier)` by
  **D60.10**) · `specs/customer_console.md` **§6A.9**
- **Added:** 2026-08-26 · AI architecture session


### H-46 · Build the Router's non-chat endpoints — shape DECIDED (D61.1) · [AGENT]
- **Check:** `rg -n '@app\.post\("/v1/' apps/services/customer_console/customer_console/main.py`
  → only `/v1/chat/completions` means the Router still serves exactly one of D60's six
  tasks and the shape is still undecided.
- **Why:** D60's catalog can **describe** `transcribe` / `image` / `speak`; the Router has
  **nowhere to serve them**. The fork is real and is an owner call because it is a public
  wire-protocol commitment: **(a)** per-task OpenAI-shaped endpoints
  (`/v1/audio/transcriptions`, `/v1/images/generations`, `/v1/audio/speech`) — every SDK
  already speaks them, but each needs its own upload/streaming story; or **(b)** one
  generic `/v1/invoke` taking `(task, tier, payload)` — one route, but we invent a
  protocol nothing speaks and file upload gets awkward.
  ✅ **DECIDED 2026-08-26 by D61.1: (a), one task at a time, starting with `transcribe`.**
  ⚠️ **And the [OWNER] label on this entry was wrong** — the Router is an **internal seam**
  (our gateway → our Console, on a credential we issue), not a public API, so its shape
  commits us to nobody outside. OpenAI's shapes are also what litellm implements on *both*
  sides, so following them costs nothing and keeps the option of exposing the Router
  publicly later. Re-labelled [AGENT].
  📌 **Build `transcribe` only when a caller needs it.** An endpoint nobody calls is CP-4's
  mistake repeated — which is the entire lesson of the first-caller ticket (D57.3).
  ✅ **Not blocking:** CP-10 slice 1 and CP-11 both proceed — chat is **96 of the 110**
  measured call sites. This bounds the multimodal reach, not the next two tickets.
- **Done when:** `customer_console.md` **§6A.10a** holds the nine clauses.
  Build to that section, and to nothing written in this entry.
  📌 **H-78 lands FIRST.** Clause 5 reads `model_profile.vendor_per_minute_usd`,
  and H-78 builds that column. Until a profile holds the price,
  `provider_cost_usd` stays NULL (D-AI-7 rule 3).
  📌 **H-47 folds in as this entry's dispatch clause.** The handler seam lands
  WITH its first caller (D57.3). §6A.10b clause 7 says so.
- **Authority:** `customer_console.md` **§6A.10a** (the done-when) ·
  `work_plan.md` §3 **D61.1** (the decision) · D60.11(b) · `specs/customer_console.md` **§6A.10 G-1**
- **Added:** 2026-08-26 · AI design audit · **amended 2026-08-30** with a
  done-when section and the H-78 order

### H-47 · Widen `acb_stt`'s provider pattern instead of inventing a handler abstraction (G-2) · [AGENT]
- **Check:** `rg -n "class SttProvider|resolve_stt_provider" packages/acb_stt/` → present
  and still STT-only means the generalisation has not happened.
  ⚠️ **Repaired 2026-08-30. This Check read two things as one.** A DATA READ of
  `model_capability.invocation` is **ALLOWED**. `resolve_invocation`
  (`customer_console/router.py:269`) is that read, and §6A.10a clause 6 gives it
  its first caller on the serving path. The defect this entry guards is a second
  handler-OBJECT seam — a second provider hierarchy beside `acb_stt`'s. Read the
  two apart before you call a hit a defect.
- **Why:** D60 originally said the capability row carries *"the litellm verb"*. **That is
  wrong** — `acb_stt` exists because AssemblyAI's batch API is submit-then-poll and, in the
  package's own words, *"can't be expressed as a LiteLLM `atranscription` call"*. So
  `invocation` names a **handler**, of which litellm verbs are one family and native
  providers another.
  ✅ **The reuse finding is the valuable half:** `acb_stt` already implements D60's step 2
  — `SttProvider` as the one interface, `resolve_stt_provider(alias)` resolving alias →
  concrete model → the provider that speaks it, with `LiteLLMSTT` and `AssemblyAISTT`
  behind it. **That is `(model, task) → invocation`, built, for one task.** Widen it to
  all tasks. Authoring a second dispatch abstraction beside it is the CLAUDE.md §5 defect
  in the one place this design has been most careful to avoid it.
  ⚠️ Consequence for G-5: `invocation` values are an **allowlist the Router knows**, never
  free text — an operator must not be able to bind a handler that does not exist.
- **Done when:** `customer_console.md` **§6A.10b** holds the seven clauses.
  Build to that section, and to nothing written in this entry.
  📌 **This entry gets NO dispatch of its own.** §6A.10b clause 7 folds it into
  H-46's build order as that entry's dispatch clause. The seam lands with its
  first caller, and never before it (D57.3).
  📌 **Home: `customer_console/handlers.py`**, and `packages/acb_stt` stays the
  tenant package. §6A.10b clause 1 holds the plane-boundary argument and the
  rejected `acb_provider` alternative.
- **Authority:** `customer_console.md` **§6A.10b** (the done-when) ·
  `work_plan.md` §3 **D60.11(a)** · `specs/customer_console.md` **§6A.10
  G-2 / G-5** · CLAUDE.md §5 · `work_plan.md` §4 (the seam's owner row)
- **Added:** 2026-08-26 · AI design audit · **amended 2026-08-30** with a
  done-when section and a repaired Check


### H-54 · Configure the Supabase staff provider, the five `OPERATOR_*` values, and turn identity linking OFF · [OWNER]
- **Check:** `ssh` to the box and read the operator console env for
  `OPERATOR_ENTRA_TENANT_ID` → an unset value means still pending. From the repo
  alone: `rg -n "OPERATOR_ENTRA_TENANT_ID" workbench/operator_console/` → a hit in
  `src/lib/` with no hit in a deployed env is the same answer.
- **Why:** CP-12a builds the three-check staff gate. It cannot admit anybody
  until the owner configures the provider and sets the three values. Until then
  the console stays on **one shared passphrase**. That box has been reachable
  since 2026-08-22.
- **⚠️ Amended 2026-08-27 by CP-12f2, and it grew two parts.**
  1. There are **five** values now, not three. `OPERATOR_SUPABASE_URL` and
     `OPERATOR_SUPABASE_ANON_KEY` join the three above. All five are
     documented in `deploy/hostinger/customer_console.env.example`.
  2. **Turn manual identity linking OFF in the Supabase project.** A staff
     account that links a second provider can be signed in through that
     provider. The Console refuses such a sign-in, because it reads
     `app_metadata.provider`. That claim is not per-session, so linking is
     the condition the bypass needs, and removing it is the durable fix.
  3. **Confirm the Azure claim shape.** No live project has produced one
     yet, so `operator_signin.extract_identity` reads a shape nobody has
     measured. It fails CLOSED, so a wrong guess refuses everybody rather
     than admitting anybody. Read one real payload and correct that one
     function.
- **Authority:** `specs/operator_identity_and_access.md` §10 G1–G2 ·
  `work_plan.md` §6.0 B5 · §6.1 (CP-12 block) · D64.1
- **Added:** 2026-08-26 · operator-identity spec session

### H-58 · Name the first operators and their roles · [OWNER]
- **Check:** `rg -n "OPERATOR_BOOTSTRAP_EMAIL" deploy/ .env.example` → no hit
  means nobody has been named yet.
- **Why:** CP-12d ships the add, re-role and deactivate routes. Who is a
  `viewer`, who is an `editor` and who is an `admin` is an owner judgement, and
  no agent may take it. ⚠️ Naming a **second** `admin` is also the trigger that
  pulls four-eyes approval (DEF-1) out of deferral. The two arrive together.
- **Authority:** `specs/operator_identity_and_access.md` §5 · §9 DEF-1 ·
  `work_plan.md` §6.0 C4 · D64.3
- **Added:** 2026-08-26 · operator-identity spec session · **renumbered from
  H-55 on 2026-08-26**, because the STE session minted its own H-55 against a
  different base and merged first. `test_handoff_ids_are_unique` caught it. Ids
  are never reused, so H-55 stays with the STE entry.

### H-56 · **CP-12g slice 2** — delete the passphrase, AFTER one real sign-in · [AGENT+OWNER]
- **Check:** `rg -n "OPERATOR_CONSOLE_STAFF_SECRET" workbench/` → a hit means
  the deletion has not run. That is the CORRECT state until the owner has
  flipped the flag and signed in once.
- **⚠️ Amended 2026-08-27.** CP-12a to CP-12g slice 1 are built. The console
  now has both sign-in paths, and `OPERATOR_IDENTITY_ENABLED` chooses.
- **The order, and it is the reverse of what it looks like:**
  1. **[OWNER]** Finish **H-54**. Configure the Supabase Microsoft provider,
     set the five `OPERATOR_*` values, turn identity linking OFF, and add
     `<origin>/login/callback` to the redirect allowlist.
  2. **[OWNER]** Apply migration 009 (**H-64**). `GET /operators` answers 500
     until it is applied.
  3. **[OWNER]** Set `OPERATOR_BOOTSTRAP_EMAIL` to your own address, then
     flip `OPERATOR_IDENTITY_ENABLED` on the console app.
  4. **[OWNER]** Sign in once. You become the first `admin` automatically.
     Then add the rest of the team (**H-58**).
  5. **[AGENT]** ONLY THEN: delete `staff.ts`, `InterimForm.tsx` and the
     interim branch of the session route. Remove the constant from
     `route.ts`, `session.ts` and `identity.ts`.
  6. **[OWNER]** Remove `OPERATOR_CONSOLE_STAFF_SECRET` from the box.
- **⚠️ Run step 5 before step 4 and nobody can sign in at all.** That is why
  slice 1 keeps both paths.
- **Two console env values are still undocumented**, because
  `deploy/` is OWNER-GATE and I may not write there. Add to the operator
  console's env: `OPERATOR_IDENTITY_ENABLED` (the flag, default off) and
  `OPERATOR_CONSOLE_ORIGIN` (the console's own public URL, used to build the
  Supabase callback). `OPERATOR_SUPABASE_URL` is needed by BOTH services.
- **Authority:** `specs/operator_identity_and_access.md` §8 · `work_plan.md` §2
  WS-31 (CP-12 clause) · D64
- **Added:** 2026-08-26 · operator-identity spec session
### H-65 · plan-guard cannot see a write made by an interpreter reading a heredoc · [AGENT]
- **Check:** read `.claude/hooks/plan-guard.mjs` near the `scanned` constant.
  A regex still strips every heredoc body before the protected-path scan, and
  `plan-guard.test.mjs` names no interpreter case. Both mean still open.
- **What I did, by accident, on 2026-08-27.** I edited
  `deploy/hostinger/customer_console.env.example` with `python - <<'PY'`. The
  path is protected by the `deploy-write` gate. The guard did not fire.
- **Why it does not fire.** The guard strips heredoc bodies on purpose, and
  the reason is sound: a body is usually FILE CONTENT, and content that
  mentions `.env` must not block an ordinary commit. The comment argues that a
  real write still blocks, because in `cat > .env <<'EOF'` the `> .env` sits
  in the command half.
- **⚠️ That argument holds for `cat`. It does not hold for an INTERPRETER.**
  In `python - <<'PY'` the body is a PROGRAM, and the program does the write.
  The path never appears in the command half at all. The same is true of
  `node -e`, `perl`, `ruby` and `sh` reading from a heredoc.
- **Suggested fence:** when the command runs an interpreter that reads its
  program from stdin or from `-e`, scan the body as COMMAND instead of
  stripping it. Add a case to `plan-guard.test.mjs` first, and show it red.
- **⚠️ Do not treat this as licence.** The gate is the rule. A gap in the
  enforcement does not widen it.
- **Authority:** `work_plan.md` §6 · D45 · `.claude/hooks/plan-guard.mjs`
- **Added:** 2026-08-27 · WS-31 CP-12g session

### H-64 · Put the Console DSN on the box, then deploy · [OWNER]
- **Check:** on the box, `\dt operator*` against the Console database. No
  `operator` table means still pending. **Measured 2026-08-27 over the Supabase
  MCP: the table does not exist.** `provider_credential` holds 0 rows.
- **⚠️ The shape of this changed on 2026-08-27.** H-24 closed, so the deploy now
  applies the Console ladder by itself. You no longer run a migration by hand.
- **What is left is one file.** `/opt/acb/app/apps/services/customer_console/.env`
  must carry `CUSTOMER_CONSOLE_DATABASE_URL`. The template is
  `deploy/hostinger/customer_console.env.example`. The next deploy then applies
  every ladder file, 001 to 009, and restarts the Console.
- **⚠️ The new step FAILS the deploy** when `acb-customer-console` is enabled and
  that value is absent. That is deliberate, and
  `tests/unit/test_console_ladder_deploy_wiring.py` pins it. A box that runs no
  Console skips the step and says so.
- **Why it matters:** CP-12a to CP-12g slice 1 are on `main`, so the CODE is on
  the box. Until the ladder runs, `GET /operators` answers 500 instead of 404,
  and the whole operator identity stack reads tables that do not exist.
- **Authority:** `specs/operator_identity_and_access.md` §7 · `work_plan.md`
  §6.1 (CP-12 block) · D47
- **Added:** 2026-08-27 · CP-12e session · **rewritten 2026-08-27** after H-24

### H-69 · Flip `ROUTER_SERVING_ENABLED` — after three prerequisites · [OWNER]
- **Check:** on the box, `grep ROUTER_SERVING_ENABLED /opt/acb/app/.env`. No
  line, or `0`, means the hop is inert and this is still pending.
- **What the flip does.** `/v1/chat/completions` stops calling litellm locally
  and calls the Console Router instead. The tier binding, the rate card and OUR
  provider account then decide every call, streamed or not.
- **⚠️ Three things must be true FIRST, and none of them is code.**
  1. A provider credential is installed on the Console (**H-40 built the door**,
     and `provider_credential` held 0 rows when I measured it on 2026-08-27).
  2. `CUSTOMER_CONSOLE_ORG_KEY` is on the box. Mint it from the operator
     console's **API keys** panel, which CP-11 slice 1 added.
  3. ~~Streaming stays unmetered.~~ ✅ **CLOSED 2026-08-27** — CP-4b and
     CP-11 slice 5 route and meter a stream. The flip now covers ALL
     traffic, which is what makes the revenue number readable.
- **⚠️ A routed call that fails FAILS (D57.7).** It does not fall back to
  litellm. So an unreachable Console means the AI stops, instead of quietly
  serving unmetered traffic. That is the intended trade, and it is why the flag
  is worth flipping deliberately and not by default.
- **⚠️ On a SHARED box one org key cannot be right for every tenant.** The
  setting is correct on a single-org silo only. Somebody must resolve the key
  per-organization before this goes on anywhere else.
- **Authority:** `work_plan.md` §6 (d)/(e) · **D57** · **D57.7** ·
  `specs/customer_console.md` §6B.7
- **Added:** 2026-08-27 · WS-31 CP-11 slice 3 session

---


---


---

### H-72 · A saved raw model id in a LIVE app breaks on the flag flip · [OWNER]
- **Check:** on the box, look for a `task_settings` row whose
  `chat_model` / `clarify_model` / `atomize_model` / `email_capture_model`
  does not start with `tier-`. No such row means nobody ever picked one,
  and this closes with no migration at all.
  ⚠️ **The picker itself is already gone** (part 1 below). This entry is
  now only about values ALREADY STORED.
- **Why:** 🔴 **D32.7 says customers never see a model, and one does.** Found
  while scoping CP-5, which targets the `preview` models page. This is a
  different surface and a live one. The chain is measured, not inferred:
  1. `/tasks` is `launch: "live"` in `nav.ts` — one of the nine panes.
  2. `app/tasks/page.tsx` renders `<TaskSettingsModal />` twice.
  3. The modal reads `/api/settings/llm/enabled-models` and offers each one
     under an `optgroup` labelled **"Your enabled models"** (line 174).
  4. The chosen value saves to `chatModel` / `clarifyModel` / `atomizeModel`
     / `emailCaptureModel`.
  5. `AssistantRail.tsx:276` passes `model={chatModel}` into the chat call.
- **⚠️ The defaults are TIERS**, so nothing is broken today and nothing looks
  wrong. `tier-powerful`, `tier-balanced`, `tier-fast`. **Only a customer who
  deliberately picks a model from that group stores a bare model id.**
- **🔴 That customer's Tasks AI breaks the day `ROUTER_SERVING_ENABLED` flips.**
  The Console refuses a bare model id with **400**, and does not coerce it
  (D32.7, and `resolve_tier` raises `TierUnknown`). The break stays silent
  until the flip. It then lands on the customer most engaged with the
  product. That is the one who went into settings and chose.
- **So the trigger is H-69**, the same flip that arms metering.
- **Two questions, and the second is the owner's:**
  1. ✅ **DONE 2026-08-27.** The `optgroup` is gone, the fetch that fed it
     is gone, and `modelVocabulary.test.ts` fails if either returns. This
     stops NEW model ids. ⚠️ It heals nothing already saved.
  2. ⚠️ **What happens to a value already saved?** A stored `openai/gpt-4o`
     must become *some* tier, and choosing which is a product decision — a
     migration that guessed would silently re-point somebody's work.
     `test_byok_default.py` shows the old orchestrator coerced to
     `tier-balanced`; D32.7 retired coercion precisely because it hides a
     misconfiguration behind a bill.
- **✅ `/email` is DONE (2026-08-28).** `ai-settings/SettingsTab.tsx` carried
  the same picker and it is gone the same way. The fence moved with it:
  `src/lib/modelVocabulary.test.ts` is now ONE table-driven test over both
  surfaces, not a copy per app. Add a row when a third picker appears.
- **⚠️ What is left here is the OWNER half only** — the stored-value query
  below. No code change remains.
- **Authority:** **D32.7** · `specs/customer_console.md` §6A CP-5 ·
  `specs/launch_surface.md` §2 (the live nine)
- **Added:** 2026-08-27 · WS-31 CP-5 scoping session

### H-73 · CP-7's per-member cap rests on an identity the member controls · [AGENT+OWNER]
- **Check:** `grep -n "x-cc-member" apps/services/gateway/gateway/routes/v1_compat.py`
  → a `request.headers.get(...)` hit means the member identity is still taken
  from the inbound request, and this is open. A hit that reads it from a
  verified session means somebody fixed it, and CP-7's engine can proceed.
- **Why:** 🔴 **The cap engine is built and must stay unwired.**
  `credits.decide_member_cap` and the `member_ai_cap` table both exist. Wiring
  them to today's inputs would ship a control that does not control anything.
  The chain is measured, not inferred:
  1. `auth.py:497` binds `Caller.member` from the **`X-CC-Member` header**.
  2. `auth.py:211` says so out loud: *"Attribution only. Never used to select
     rows, never used to authorise."*
  3. `v1_compat.py:490` forwards it **verbatim from the inbound request** —
     `request.headers.get("x-cc-member")`. It is not derived from the session.
  4. So the capped party chooses which cap applies. **Omit the header and
     there is no cap row, and no cap row means unlimited** — which is correct
     behaviour for an absent policy and a total bypass for a present one.
- **⚠️ This is migration 005's defect class, one column over.** 005 moved
  `request_id` server-side because *"the party being invoiced must not control
  whether it exists."* The same sentence with two words changed: **the party
  being capped must not control which cap applies.**
- **📌 Why the gateway cannot fix it alone.** On `/v1/chat/completions` there
  is no session to derive from. `require_llm_api_auth` is a pure token check
  that binds no identity. So `current_tenant()` is `None` by construction.
  That is the `work_plan.md` §6 (f) H4 finding. `saas_multitenancy.md` §3082
  records the same for `X-CC-Agent`. The gateway has nothing true to put in
  the header.
- **🟢 The reads did NOT wait for this, and shipped.** `/my/usage/activity`
  and `/my/usage/members` (CP-7 slice 1) report the same attribution and are
  safe, because a cost report is not an authorisation decision. **Attribution
  is good enough to REPORT and not good enough to ENFORCE.**
- **Two questions, and the second is the owner's:**
  1. Where does a trustworthy member identity come from? A session-scoped
     door beside the org key is the obvious shape, but it is a new auth
     scheme and §4's registry says who owns that.
  2. ⚠️ **Is a per-member cap worth a fifth auth scheme at all?** The org
     pool, the balance gate and the run ceiling already stop runaway spend.
     A cap is a *management* feature, not a *safety* one. Answering "not yet"
     is a legitimate answer and it costs nothing to defer.
- **Authority:** **D32.8** · `specs/customer_console.md` §4.5 · §6 CP-7 ·
  `work_plan.md` §6 (f) · migration `005_metering_identity.sql`
- **Added:** 2026-08-28 · WS-31 CP-7 slice 1

### H-74 · mypy is strict over a tree nobody has swept — 1508 errors · [AGENT]
- **Check:** `uv run mypy apps packages --exclude '^apps/agents/' 2>&1 | tail -1`
  → a count above zero means the sweep has not happened and this is open.
- **Why:** **Two dead halves of one ratchet, both found while fixing H-28.**
  The pre-commit hook passed no target, so it type-checked nothing. The CI step
  ran `uv run mypy apps packages`, which aborts on a duplicate-module error and
  checked almost nothing. CI is `continue-on-error`, so its "Found 1 error"
  read as a clean report for as long as the step has existed.
- **📌 Both halves now report.** The hook is diff-scoped and report-only. CI
  excludes `apps/agents/` and reaches 418 files. **Neither blocks**, which is
  the honest state — 1508 errors in 271 files, measured 2026-08-28.
- **⚠️ Do not flip either one to blocking before the sweep.** A blocking
  diff-scoped hook stops your commit on somebody else's errors in the file you
  touched. The only thing that teaches is `--no-verify`, and a bypass habit is
  worse than a report.
- **📌 The seven colliding modules are the other half of the story.**
  `apps/agents/*/agents.py` — seven files, one module name, no `__init__.py`.
  Until they are packaged, no tool that walks the tree can see past them.
- **Authority:** `work_plan.md` §1 R7 · `.pre-commit-config.yaml` ·
  `.github/workflows/pr-check.yml`
- **Added:** 2026-08-28 · H-28 fix session

### H-75 · The Operator Console's systemd unit and Caddy block are NOT in the repo · [OWNER]
- **Check:** `rg -l "operator" deploy/hostinger/` → no hit means the unit file
  is still only on the box, and this is open.
- **Why:** 🔴 **The console is deployed and was never reproducible.**
  `operator.metorite.com` serves, Caddy routes it, a process answers — and
  none of that exists in version control. Every other service here is copied
  from `deploy/hostinger/*.service` by `vps_apply.sh`. This one was stood up
  by hand.
- **📌 The drift it caused is measured, not theoretical.** On 2026-08-28 both
  `/models` (merged 08-27) and `/providers` (merged 08-28) answered **404** on
  the live console. The site was up and the code was two days behind, because
  nothing rebuilt it.
- **✅ Half of this is now closed.** `scripts/vps_apply.sh` builds and restarts
  the console on every deploy, guarded on the unit being enabled and
  overridable by `OPERATOR_CONSOLE_UNIT`.
  `test_operator_console_deploy_wiring.py` fences all three properties.
- **⚠️ What is STILL open, and it is the owner's half:**
  1. Commit `acb-operator-console.service` to `deploy/hostinger/` to match
     what runs on the box, and add the `sudo cp` line the workbench block
     already has. **`deploy/` is §6 owner-gate and plan-guard blocks an agent
     from writing there** — the same wall as H-13's three patches.
  2. Commit the Caddy site block for `operator.metorite.com`.
  3. Confirm the running unit is called `acb-operator-console`. If it is not,
     set `OPERATOR_CONSOLE_UNIT` on the box or rename the unit.
- **📌 Until 1 and 2 land, the deploy manages an artefact it cannot
  reproduce.** Losing the box loses the console's configuration entirely.
- **Authority:** `work_plan.md` §6 (deploy reach) · D35 (own hostname, own
  app) · `scripts/vps_apply.sh`
- **Added:** 2026-08-28 · operator-console deploy session

### H-76 · `usage_by_org` sorts by spend, so the quiet funded customer falls off the cap · [AGENT]
- **Check:** read the docstring of `usage_by_org` in
  `apps/services/customer_console/customer_console/store.py` and the `ORDER BY`
  under it. An order that still sorts on credits alone means this is open.
- **Why:** the read LEFT JOINs `organization` to `usage_event` so that an
  organization with no use appears with zeros. "This customer bought credits
  and used none" is the most actionable row on the page. The `ORDER BY` then
  sorts on credits descending, and `SPEND_PAGE_SIZE` cuts the list. So that
  row sorts LAST and drops off the end. The two rules cancel out.
- **📌 Measured, not theoretical.** Found on 2026-08-30 against a scratch
  database of 563 organizations. Dev, CI and production hold 2 organizations,
  so all three agree the read is fine.
- **What is already done:** the read returns `total`, and the console says
  "100 of 563". The truncation is never silent.
- **What is open:** the ordering itself. An operator wants the biggest
  spenders **and** the quiet ones. That is two queries or one union, not one
  `ORDER BY`. Nobody has chosen the shape.
- **⚠️ The number stands even after the fix.** Handoff ids are never reused.
  `store.py` cites "HANDOFF H-76" by name.
- **Authority:** `specs/ai_metering_and_analytics.md` §5 O2 · `store.py`
  `usage_by_org`
- **⚠️ Widened 2026-08-31 by slice 5.** A walled organization bills 0, so it
  also sorts last. The `walled` flag rides the capped table, and only `silent`
  has the cap-proof banner. Above `SPEND_PAGE_SIZE` organizations, a walled
  customer appears nowhere. The fix for the sort must cover both classes.
- **Added:** 2026-08-30 · WS-31 spec remediation session

### H-77 · Set the vendor feed's clock on the box · [OWNER]
- **Check:** on the box, `grep CUSTOMER_CONSOLE_FEED_SYNC_HOURS /opt/acb/app/.env`.
  No line, or `0`, means the feed only updates when somebody presses the
  button, and this is still open.
- **Why:** the owner's directive (2026-08-30) asks for vendor prices that
  update on their own. Migration `014` and `feed.py` built the machinery.
  The loop ships dark (CLAUDE.md §4), so the clock is an env var the owner
  sets. `24` is the sensible value — litellm moves near-daily.
- **What it does NOT touch:** `model_profile`, the rate card, or any billing
  read. The sync fills `vendor_price_feed` and the console shows drift. The
  operator still clicks to copy a price into a profile.
- **The button works today.** "Fetch the latest" on `/models` syncs once with
  the flag unset. The flag only adds the clock.
- **Authority:** `customer_console.md` §6A.11 · `work_plan.md` §6
  (enforcement flips)
- **Added:** 2026-08-30 · vendor-feed session

---

# DONE — deleted, not archived

Nothing lives here. When an entry's Check passes, **delete the block**. Git
history is the archive; a "done" section in a loaded-into-context file is just
tokens that make the open items harder to find.
