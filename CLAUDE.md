# Metorite — read this before your first tool call

You are working on **Metorite**: an AI-driven company operating system, sold as
multi-tenant SaaS. This file loads into every session, cloud and headless ones
too. It is a **router plus the few facts you must know first**. It points to a
fact instead of copying it.

---

## 0. How you write — Simplified Technical English

**Every message you send, and every document you write, is in Simplified
Technical English (ASD-STE100).** Owner directive, 2026-08-26. The contract is
**`docs/style_ste.md`**. Do not restate its rules anywhere else.

The five that bind hardest:

1. **20 words** in a procedural sentence and **25** in a descriptive one.
   **6** sentences in a paragraph, and one topic per paragraph.
2. **No semicolon.** Write two sentences.
3. **Active voice.** Name the actor.
4. **Use the approved word.** `.claude/hooks/ste-words.json` holds the list, and
   a replacement for each entry.
5. **A domain word is legal.** `tenant`, `migration`, `schema` and `grant` are
   Technical Names. A rewrite that strips one makes the text worse, not plainer.

Two tiers. **STRICT** binds anything that says what to do, and that includes your
replies to the owner. **INFORMED** binds rationale prose, where the hedging words
drop to a warning. `docs/style_ste.md` §2 lists the paths.

This composes with BLUF and does not fight it. BLUF sets the **order**, so the
answer comes first. STE sets the **language**.

**The fence:** `.claude/hooks/ste-lint.mjs` runs on every markdown write
(PostToolUse) and on every commit (pre-commit, added lines only). Its test is
`node .claude/hooks/ste-lint.test.mjs`. In build, the hook only warns. A
`harden` PR sets `STE_LINT_BLOCK=1`, and then it blocks (§4). Old text is
grandfathered. Do not open a pull request that only repairs old text.

---

## 1. Where truth lives (read in this order)

| # | File | What it owns |
|---|---|---|
| 1 | **`project-docs/INDEX.md`** | Which specs are **ACTIVE** (you may build from these), and which are deferred or historical (you may not). A spec missing from INDEX is a defect. Say so. |
| 2 | **`project-docs/work_plan.md` §1** | The agent-ready spec contract and the standing **R** rules. Binding on every PR. |
| 3 | **The HathiLabs project Metorite**, then **`project-docs/work_plan.md` §2** | **Metorite holds the state (D118):** the status, the priority, the owner and the order of each work item. Call `my_work` through the Metorite MCP, then `claim_task`. §2 is the index: one row per workstream, with its owning spec and its Metorite epic. Start at **§2.0**, the roadmap (M0 to M4). For scope, the spec wins. For order and owner, Metorite wins. |
| 4 | **`project-docs/work_plan.md` §6** | The owner-gate registry. Actions you must **refuse by name**, unless §3a opens them. |
| 5 | **`project-docs/work_plan.md` §3** | The decisions. Recorded once, never re-litigated. Cite them, and do not reopen them. |
| 5a | **`project-docs/HANDOFF.md`** | The cross-session **queue of actions** (D39), injected at session start. ⚠️ **Actions, never state.** Metorite stays the state authority (row 3). Run each entry's **Check** first and **delete** the ones that pass. `/handoff` is the workflow. |
| 6 | **`project-docs/specs/engineering_practice.md`** | *How* we build: environments, release, migrations, testing, agent work-partitioning, security, definition of done. |
| 7 | The **owning spec** named by your board row | Scope and acceptance for the thing you are building. |

For ordering and ownership, `work_plan.md` wins over every spec.
`project-docs/` = the plan and the product specs. `docs/` = engineering
reference tied to code. Do not put product specs in `docs/`.

## 2. The architecture, in one screen

- **⚠️ Centers are WITHDRAWN FROM THE SURFACE (D49).** Nothing navigates to a
  Center. **The code stays:** `lib/centers.ts`, `/centers/<slug>`, the
  `center.*` features and the `group:<slug>` slice grants (D12). The Center is
  still the *scoping primitive* of the Projects grant model. Do not delete
  them, and do not link to them.
- **Apps** are the surfaces, in four sections: **Personal Center**, **Apps**,
  **AI Studio** and **Admin**. The live set is `specs/launch_surface.md` §2,
  mirrored in `src/lib/nav.ts` and fenced by `nav.test.ts`. Every other pane is
  `preview`: routes, API and tests intact, nav entry absent. `preview` is
  **not** a permission. Never revoke a feature to hide an app.
- **ONE task store, three lenses** *(D52–D54, board `WS-39`)*. `pm_tasks` and
  `pm_task_personal` hold every task in the product. **Projects** (`/projects`)
  is the company board, **Tasks** (`/tasks`) is my lens, and **Calendar**
  (`/calendar`) is my time. None is a copy, so no sync exists. A personal task
  is a **private task in my own personal project**. The per-member overlay is
  `pm_task_personal`, keyed `(task_id, member_email)`.
- **⚠️ `gtd_*` is gone.** Migration 217 dropped the old task store, and
  `tests/unit/test_no_gtd_table_names.py` refuses a new `gtd_` table. The tables
  that stayed are `people`, `people_skills`, `people_credentials`,
  `people_absences`, `people_resumes`, `calendar_day_state`,
  `calendar_rollover_log`, `user_settings`, `attachments`,
  `my_tasks_horizons` and `my_tasks_reviews`.
- **ClickUp is gone.** Metorite is the PM system of record (root `AGENTS.md`
  constraint 8).
- **⚠️ The shell is ONE seam** *(D87–D89, board `WS-44`)*. Every app joins it
  through a manifest on its `NavPane`. No app builds its own ⌘K handler,
  palette, bell or assistant rail. A team view is a filter, never a Center.
  `specs/navigation_shell.md` owns it, and `workbench/control_plane/AGENTS.md`
  rule 10 carries it to UI work.
- **Pricing is FLAT: ₹500/user/month + AI credits**, one sellable seat
  (`core`), everything live included. `specs/launch_surface.md` §4 is the
  shape of record. `saas_multitenancy.md` §2.4b is the superseded D23/D24
  record. Still binding: D19.3's hard cap, D32.5's three counts, the
  entitlement seam.
- **Tenancy is a ROW, not a deployment.** `organization_id` + Postgres FORCE ROW
  LEVEL SECURITY, bound at the `get_db()` seam. We are **multi-tenant from
  customer #1**. "Silo" describes *placement* only. No phase accepts
  single-tenant code. (D15)
- **Visibility inside a tenant** is private → Center → org, plus `group:<slug>`
  grants. Tenancy is *which company*. Visibility is *who inside it*. Two
  mechanisms, two axes, no third. (D12)

## 3. Non-negotiables — these bind before you touch anything

1. **Never commit or push on `main`.** Cut a branch first. (`plan-guard.mjs`
   enforces this and will block you.)
2. **Refuse owner-gated work by name** (`work_plan.md` §6), unless a dated
   grant in `.claude/OWNER_GRANTS.md` opens it (§3a). Owner-gated work is live
   credentials, VPS and deploy reach, force-push, member and role writes,
   enforcement flips, cutovers, and production one-offs. Build the thing,
   write it up, stop, hand it over.
3. **R5 — tenant-ready by construction.** New tables are tenant-scoped. Add no
   DB connection site outside the seam. Redis keys go through the prefix
   wrapper. Use the existing session idiom. Never trust a tenant or identity
   from request input.
4. **R6 — expand/contract migrations.** The deploy applies migrations *before*
   it restarts services, so old code always meets new schema. Nullable with
   defaults. Never rename in place. Tighten in a later release. **We cannot
   roll back.** We can only roll forward or restore.
5. **R7 — name the fence.** A rule you introduce must name the test that makes
   breaking it fail, or be labelled advisory.
6. **R8 — verify SQL against a real database.** Hermetic fakes agree with
   whatever SQL they get. Five live bugs shipped green that way.
7. **R1 — migration numbers are taken at build time and re-checked at merge.**
   Three collisions in two weeks. `bash scripts/premerge.sh` does the re-check,
   right before `gh pr merge`.
8. **Verify delivery by evidence, never by a green job.** Migration ledger
   lines, the deployed SHA, the log line. Four deploys once reported success
   while shipping nothing.

## 3a. Dev phase — the autonomy window

The owner relaxes §3 rule 2 by date. **The window ends on the date of the
`ALLOW-UNTIL` lines in `.claude/OWNER_GRANTS.md`.** After that date, §3 rule 2
binds in full, and `plan-guard.mjs` refuses again with no further action.

### What you may do without asking

Check `.claude/OWNER_GRANTS.md` first. If a dated line covers the gate id,
**do the work. Do not refuse, and do not ask again in chat.** The file is
committed, so every checkout, worktree and cloud session reads the same window.

| You may | Gate id | Notes |
|---|---|---|
| SSH to the box, read logs, restart units | `deploy` | Diagnose and fix, do not wait |
| Run `deploy/*.sh` and the migration runner | `deploy` | ⚠️ Read the migration rule below |
| Read `.env` on the box | `secrets` | Never paste a value into chat |
| Write `.env` on the box | `env-write` | |
| Write under `deploy/` | `deploy-write` | |
| Flip a feature flag | `enforcement-flip` | Say which flag, and where. A `*_ORGS` or `*_MODES` flag reloads with no restart: `docs/flag_reload.md` |
| Query and migrate the database | — | No gate ever blocked this |
| **Merge your own PR** | — | See §4. The loop ends at merge |
| Edit `plan-guard.mjs` or `settings.json` | `guard-write` | ⚠️ Read the note below |

### What you must still refuse

Four acts. `plan-guard.mjs` blocks all four, and **no grant unlocks them**.

1. **Force-push**, and any history rewrite — `filter-branch`, `filter-repo`, and
   a `git reset --hard` onto a local ref, which orphans committed work.
   ⚠️ **`git reset --hard origin/<ref>` is NOT gated.** It moves a local
   pointer to a ref that is already published, so it cannot rewrite shared
   history. It is how you sync a branch after a squash merge. Check
   `git status` first. It still discards uncommitted work, and no regex can
   see your working tree.
2. **Commit or push on `main`.** Cut a branch.
3. **Write `.claude/OWNER_GRANTS.md`.** Only the owner writes a grant.
   ⚠️ `git add` and `git commit` of that file are fine. They carry what the
   owner wrote. `git checkout --` and `git restore` are **not**, because they
   can revive a grant the calendar already retired.
   ⚠️ **When the owner must add a line, open the file for them.** Run
   `code -g <path>:<line>` on the exact copy that the pull request uses. That
   is often a worktree and not the main checkout. Then give the exact line and
   the line number. Do not only name the path.
4. **Destroy or restore infrastructure.** The VPS, the DNS zone, the domain.

### `guard-write` — the guard protects itself

**Editing `plan-guard.mjs`, `plan-guard.test.mjs` or `.claude/settings.json`
needs the `guard-write` grant.** In this window the hook is the ONLY
enforcement layer for ssh, deploy, `.env` and flag flips. A guard that can edit
itself, or that six deleted lines in `settings.json` can switch off, is not a
guard.

**Grantable on purpose, never sealed.** Guard work is legitimate and frequent.
A guard no agent can repair rots, and a false positive that nobody can fix
teaches people to delete the whole guard.

### Three rules that replace the refusals

The gates bought care, not only delay. Keep the care and drop the delay.

1. **A production migration is still one-way (R6).** Before you apply one to
   production, confirm the pre-migration backup completed. Then apply it. You
   do not need to ask, and you do need the evidence.
2. **Report every production act in the same message.** Name the act, the box,
   and the evidence. "I restarted `acb-gateway`, `/version` now serves `abc123`."
   Autonomy without a record is the failure this replaces.
3. **Money, identity and third parties still stop you.** Do not charge a card,
   and do not send mail to a real person. Do not write a live organization's
   membership or credit balance. Ask first. These are cheap to ask about and
   expensive to get wrong.

### The failure mode to watch

This window does not let you skip verification. **Rule 8 of §3 still binds**:
verify by the migration ledger, the deployed SHA and the log line. Faster
delivery makes a silent failed deploy more likely, not less.

---

## 4. How development proceeds

**Feature by feature, app by app — one narrowed slice at a time.** The loop
(`/next-ticket`, defined in `.claude/commands/next-ticket.md`):

> **audit** the spec is dispatchable → **implement** one narrowed slice and
> **run it locally** (`scripts/dev_up.sh`) → **verify** independently, on the
> local stack → **review** the diff adversarially, by risk tier. Then **open a
> PR**, **merge it**, **watch the run**, and **ship it**.

**Merging is not skipping the loop.** Verify always runs, and the verifier is
never the implementer. A small slice (LOW, or STANDARD in build) skips the
audit when its Metorite task states the acceptance criteria (owner request,
2026-10-30). Review depth follows the risk tier in `next-ticket.md` step 1b. A
LOW slice skips the reviewer. What you drop is the wait.

**⚠️ Staging is retired** *(D144, owner directive, 2026-10-31)*. A merge moves
`main` and nothing else. Production moves at 21:30 UTC each day. An agent may
also dispatch `deploy.yml` after the merge, and the run ships the tip of `main`
at once. This replaces the next rule. Verify by evidence: `/version`
`applied_sha` and the smoke jobs. `.claude/commands/next-ticket.md` step 7 holds
the commands.

**⚠️ Do not push to production yourself** *(owner directive, 2026-10-27)*. Do
not run `gh workflow run deploy.yml`. Production takes the 21:30 UTC release.
Run a manual dispatch only when the owner asks for it in your session, or when
production is down. A dark feature never needs one. If you think a change
cannot wait, ask the owner.

**⚠️ WORK UNDER A METORITE TASK** *(owner directive, 2026-10-28, D135)*.
The owner watches the board, so work that is not on it is invisible. Every
session that changes the repo or a box follows these steps:

1. **Find or make the task.** Call `my_work`. For work that the owner asks for
   in chat, search for its task with `search_tasks`, or make one with
   `create_task`. Do this before the first edit.
2. **Claim it.** Call `claim_task`. A 409 means another session holds it.
   Take another task.
3. **Show progress.** Post a short note at least once a minute with
   `post_agent_activity`, or a comment at each step.
4. **Link the work.** Put the task URL in the pull request body. Call
   `add_task_reference` for the branch and the pull request.
5. **Move the status.** Move the task to review when the pull request opens.
   Move it to done after the merge, with the evidence in a comment.
6. **Hand off when you stop.** Call `release_task`, with a note of what is
   done, what is left and the next step.

A session that only reads or answers needs no task.

Rules that make it work:

- **The verifier is never the implementer**, and re-derives facts from the code
  instead of trusting the write-up.
- **Sonnet does the work. Opus takes the hard cases.** Owner directive,
  2026-10-19. This binds every dispatch: the Agent tool, `/next-ticket` and a
  Workflow stage. `settings.json` sets `CLAUDE_CODE_SUBAGENT_MODEL=sonnet`, and
  the agent files in `.claude/agents/` say their own model. Pass
  `model: "opus"` only for one of these:
  - Adversarial review. `diff-reviewer` is Opus by its file.
  - A HIGH-tier slice: tenancy, RLS, auth, identity, a migration, money,
    credits, secrets, a member or role write, or the deploy path.
  - A second try after Sonnet failed on the same finding.
  - A hard problem: three or more services, or a concurrency, cache or
    ordering bug.

  Use `haiku` for a lookup with no judgement. `/next-ticket` step 1c holds the
  table per role. Fence: `tests/unit/test_agent_model_routing.py`.
- **Build first. Harden when the feature works.** Owner directive,
  2026-10-19. In build, only the product tests and the SAFETY fences block.
  The PROCESS fences and STE lint warn, and the loop is implement then
  verify. One `harden` PR per feature runs the full review and makes them
  block. A HIGH-tier slice keeps the full loop. Contract:
  `engineering_practice.md` §7a. Lists: `tests/fences.json`.
- **The reviewer hunts for the case where this is wrong.** An agent asked "is
  this correct?" says yes.
- **Re-verify every anchor at dispatch** (file paths, line numbers, migration
  numbers). Specs go stale. The code is the fact.
- **Respect the seams.** Extend the shared seam, and never add a parallel one.
  There is one DB engine (`acb_common.db`), one entitlement intersect, one
  subject-grammar validator and one task store. There is one Center registry
  (`lib/centers.ts`) and one status colour vocabulary
  (`src/lib/statusAccent.ts`). A second implementation of an existing seam is a
  defect, not a feature.
- **The UI is one product with ONE look.** Every app is a projection, with no
  app-local palette, no second colour vocabulary and no hand-rolled control.
  `workbench/control_plane/DESIGN_SYSTEM.md` is the contract, and `AGENTS.md`
  beside it carries the rules and their fences. Both load when you touch UI
  code. The owner retired the theming engine, and `src/app/globals.css` is the
  source of the one look. The conformance suite checks eight regexes and **nothing
  tests layout or cross-app continuity**. So **look at your surface in light
  mode, at compact density, under a changed accent, and beside its neighbour.**
- **⚠️ START A DEVELOPMENT SESSION IN ITS OWN WORKTREE.** Owner directive,
  2026-09-03. Two sessions in one checkout fight over one working tree. One
  switches the branch under the other, and a `reset --hard` by either destroys
  the other's uncommitted work. None of it announces itself.

      bash scripts/worktree.sh new <slug>      # then cd into the path it prints
      bash scripts/worktree.sh list
      bash scripts/worktree.sh remove <slug>

  `worktree-notice.mjs` reports at session start which checkout you are in. It
  informs and never blocks. **Reading, answering a question, or a one-file fix
  the owner is watching needs no worktree.**
  ⚠️ **Use `remove`, never `git worktree remove` by hand.** Some worktrees carry
  a `node_modules` junction into the main checkout, and the bare command
  follows it and deletes the real one. The script unlinks the junction first.
  ⚠️ Frontend work needs its own `npm install` per worktree. Turbopack resolves
  through a junction to the wrong root, so do not link it.
- **Keep branches short and integrate often.** Long branches caused the
  migration-renumber collisions, the green-alone/red-together PRs and a
  duplicated tenancy design. Three or four in flight is the ceiling.
- **Ship dark.** New behaviour lands behind a flag, default OFF. You may flip
  it when a dated `enforcement-flip` grant covers it (§3a). Name the flag and
  the box in the same message. The flags that move money or reach a real
  person stay owner-only (§3a rule 3).

## 5. What not to do

- **Do not re-litigate decisions.** The decisions live in `work_plan.md` §3. If
  one looks wrong, say so and stop. Do not build against your own alternative.
- **Do not refactor the tree to conform** to R6/R7/R8. Those bind *new and
  changed* work. Existing violations are findings for the board.
- **Do not invent a second way to do an existing thing.** That includes a
  second session idiom, a second grant vocabulary and a second status doc.
  Mirrors go stale and then lie. That is why `work_plan.md` §4 names one owner per topic.
- **Do not mark work done from the write-up.** Run the owning spec's own
  verification commands.
- **Do not add product specs to `docs/`**, and do not leave a new spec out of
  `INDEX.md`. A spec enters the index in the PR that creates it.

## 6. Environment notes that will bite you

- Secrets go through `scripts/secrets.sh` and the drop folder
  (`docs/secrets_drop.md`). Never cat or paste a secret.
- Windows is the main dev box. Pass `encoding="utf-8"` explicitly when you read
  files in tests and scripts. cp1252 is the default, and it crashes.
- **Two pytest hazards:** `test_memory_integration.py` hangs (WS-9), and so
  does `pytest tests/unit -k calendar` (WS-21). A directory run with a `-k`
  filter that deselects those suites completes. Prefer naming the files you
  mean.
- `uv run pytest …` is the runner. Frontend: `npx tsc --noEmit && npx vitest run`
  in `workbench/control_plane`.
- **Run the whole pr-check suite on this PC before you push:**
  `bash scripts/ci_local.sh`. It runs in the preset `metorite-ci` WSL2
  distro. It tests the COMMIT, so commit first. Setup and isolation:
  `engineering_practice.md` §7a.
  ⚠️ **When WSL or Docker hangs** (`HCS_E_CONNECTION_TIMEOUT`, Docker
  answers 500), run `bash scripts/ci_pc/doctor.sh`. `--fix` repairs it in a
  known order, and it restarts SHARED things. Use it only when the report says
  they are broken. Do not fall back to GitHub-only CI in silence. Say the
  doctor's result.
- **Do not run all of `tests/unit/` locally.** It takes about 50 minutes.
  Run `uv run python scripts/affected_tests.py --run`. It picks the tests your
  diff can affect, and it prints the `vitest related` command for frontend
  files. pr-check runs every test on each PR.
- **R8 needs a database. Without one, 843 tests SKIP while the run reads
  green.** Run `bash scripts/dev_db.sh` first, then
  `eval "$(bash scripts/dev_db.sh --export)"`. `engineering_practice.md` §1.1
  owns the loop.
- **Run the whole app before you push:** `bash scripts/dev_up.sh` in your
  worktree. It prints the URL, with two seeded tenants and outbound off. Look
  at your change there first. `engineering_practice.md` §1.2 owns it.
- `.claude/` (agents, commands, hooks, settings) is **tracked**, so every
  checkout inherits the same review loop and the same refusals (D29).
  Local-only and derived paths stay ignored. The CodeGraph index is rebuilt,
  never committed.
