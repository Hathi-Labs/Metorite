# Metorite — read this before your first tool call

You are working on **Metorite**: an AI-driven company operating system,
now also sold as multi-tenant SaaS. This file is loaded into every session,
including cloud and headless ones. It is a **router plus the few facts you must
know before reading anything else** — it deliberately duplicates nothing.

---

## 0. How you write — Simplified Technical English

**Every message you send, and every document you write, is in Simplified
Technical English (ASD-STE100).** Owner directive, 2026-08-26. The contract is
**`docs/style_ste.md`**. Do not restate its rules anywhere else.

The five that bind hardest:

1. **20 words** in a procedural sentence, **25** in a descriptive one, **6**
   sentences in a paragraph, and one topic per paragraph.
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
(PostToolUse) and on every commit (pre-commit, added lines only). Tests:
`node .claude/hooks/ste-lint.test.mjs`. The 196 markdown files measured on
2026-08-26 carry 19094 errors, and every one of them is grandfathered. Do not
open a pull request that only lowers the count.

---

## 1. Where truth lives (read in this order)

| # | File | What it owns |
|---|---|---|
| 1 | **`project-docs/INDEX.md`** | Which specs are **ACTIVE** (you may build from these), which are deferred/historical (you may not). A spec missing from INDEX is a defect — say so. |
| 2 | **`project-docs/work_plan.md` §1** | The agent-ready spec contract + standing rules **R1–R8**. Binding on every PR. |
| 3 | **`project-docs/work_plan.md` §2** | The dispatch board: every workstream, its state, its gates. **This is the only current-state authority.** ⚠️ Start at **§2.0** — the product roadmap (M0 customer zero · **M1 a second org can exist safely** · M2 self-serve and money · M3 operations · M4 the apps we sell) reads the same rows as a product instead of as a build tree. It adds no authority: where §2.0 and a row disagree, **the row wins**. |
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
  **Exactly nine panes are live** (eight until 2026-08-24; D54 added Calendar);
  every other pane is `preview` — routes,
  API and tests intact, nav entry absent. The allowlist of record is
  `specs/launch_surface.md` §2, mirrored in `src/lib/nav.ts`, and `nav.test.ts`
  fails if the two disagree. `preview` is **not** a permission: never revoke a
  feature to hide an app.
- **ONE task store, three lenses** *(D52/D53/D54, 2026-08-24 — board `WS-39`)*.
  `pm_tasks` + `pm_task_personal` hold every task in the product. **Projects**
  (`/projects`) is the company board, **Tasks** (`/tasks`) is my personal lens,
  **Calendar** (`/calendar`) is my time — none is a copy, so completing a task in
  one completes it in all, with no sync. A "personal task" is a **private task in
  my own personal project**, not a row in a second table; publishing it is a
  `PATCH` of `project_id`+`visibility`. The per-member overlay
  (disposition/context/energy/defer) is `pm_task_personal`, keyed
  `(task_id, member_email)`, because two people assigned one task legitimately
  disagree about its disposition. ⚠️ **`gtd_*` is retired** — it is the old Tasks
  store, still on disk during expand/contract; do not build against it, and do not
  sweep `gtd_settings`/`gtd_day_state`/`gtd_rollover_log` with it (they are the
  Calendar's). ⚠️ **ClickUp is gone** — no connector, no importer, no sync; Metorite
  is the PM system of record and root `AGENTS.md` constraint 8 is amended to say so.
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
2. **⚠️ DEV PHASE — THE OWNER RELAXED THIS RULE UNTIL 2026-09-30.** Owner
   directive, 2026-09-01. Read §3a before you refuse anything as OWNER-GATE.

   The standing rule, which returns on 2026-10-01: **refuse owner-gated work by
   name** (§6). That means live credentials, VPS and deploy reach, force-push,
   member and role writes, enforcement flips, cutovers, and production one-offs.
   Build the thing, write it up, stop, hand it over.
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

## 3a. Dev phase — autonomy window (expires 2026-09-30)

⚠️ **Owner directive, 2026-09-01. Delete this whole section on 2026-10-01.**
On that date §3 rule 2 returns to full force with no further action.

**Why this exists.** The gates did their job and then kept charging for it. The
owner wrote 22 day-scoped grant lines in eight days. A permission you always
give is not a decision, and it stopped carrying information. The cost showed as
stalled sessions, not as safety.

### What you may now do without asking

The owner grants these by date in `.claude/OWNER_GRANTS.md`. Check that file
first. If a dated line covers the gate id, **do the work — do not refuse, and
do not ask again in chat**.

| You may | Gate id | Notes |
|---|---|---|
| SSH to the box, read logs, restart units | `deploy` | Diagnose and fix, do not wait |
| Run `deploy/*.sh` and the migration runner | `deploy` | ⚠️ Read the migration rule below |
| Read `.env` on the box | `secrets` | Never paste a value into chat |
| Write `.env` on the box | `env-write` | |
| Write under `deploy/` | `deploy-write` | |
| Flip a feature flag | `enforcement-flip` | Say which flag, and where |
| Query and migrate the database | — | No gate ever blocked this |
| **Merge your own PR** | — | See §4, the loop now ends at merge |
| Edit `plan-guard.mjs` or `settings.json` | `guard-write` | ⚠️ Read the note below |

### ⚠️ D45 changed shape, and you should know how

**A grant now TRAVELS.** The owner committed `.claude/OWNER_GRANTS.md` to `main`
on 2026-09-01, so every checkout, worktree and cloud session reads the same
window. D45 wrote grants as local, one-day and one-keyboard. Two of those three
are gone for the duration.

**H-61 recommended the opposite** — gitignore the file, because a travelling
grant is "the opposite of what D45 is for". The owner overruled that, on
purpose. A cloud session that cannot read the grant refuses the work, which is
the exact stall this window exists to remove.

**What this costs.** The blast radius of one line is now every session, not one.
So the date does more work than it used to. On 2026-10-01, delete the five
`ALLOW-UNTIL` lines, and decide again whether the file stays committed.

### What you must still refuse

Four acts. `plan-guard.mjs` blocks all four, and **no grant unlocks them**.

1. **Force-push**, and any history rewrite — `filter-branch`, `filter-repo`, and
   a `git reset --hard` onto a local ref, which orphans committed work.
   ⚠️ **`git reset --hard origin/<ref>` is NOT gated** (narrowed 2026-09-02). It
   moves a local pointer to a ref that is already published, so it cannot
   rewrite shared history. It is how you sync a branch after a squash merge,
   which is every merge here. Check `git status` first — it still discards
   uncommitted work, and no regex can see your working tree.
2. **Commit or push on `main`.** Cut a branch.
3. **Write `.claude/OWNER_GRANTS.md`.** Only the owner writes a grant.
   ⚠️ `git add` and `git commit` of that file are fine — they carry what the
   owner wrote. `git checkout --` and `git restore` are **not**, because they
   can revive a grant the calendar already retired.
4. **Destroy or restore infrastructure.** The VPS, the DNS zone, the domain.

### `guard-write` — the guard protects itself

**Editing `plan-guard.mjs`, `plan-guard.test.mjs` or `.claude/settings.json`
needs the `guard-write` grant.** Added 2026-09-01, and it closed the cheapest
bypass in the design: the guard could rewrite itself, and deleting six lines
from `settings.json` disabled every rule without touching the guard at all.

**Why it matters now and did not before.** This window made the hook the ONLY
enforcement layer for ssh, deploy, `.env` and flag flips. A sole guard that can
edit itself is not a guard.

**Grantable on purpose, never sealed.** Guard work is legitimate and frequent.
A guard no agent can repair is a guard that rots, and a false positive that
nobody can fix is what teaches people to delete the whole thing.

### Three rules that replace the refusals

The gates bought care, not only delay. Keep the care and drop the delay.

1. **A production migration is still one-way (R6).** Before you apply one to
   production, confirm the pre-migration backup completed. Then apply it. You
   do not need to ask, and you do need the evidence.
2. **Report every production act in the same message.** Name the act, the box,
   and the evidence. "I restarted `acb-gateway`, `/version` now serves `abc123`."
   Autonomy without a record is the failure this replaces.
3. **Money, identity and third parties still stop you.** Do not charge a card,
   do not send mail to a real person, and do not write a live organization's
   membership or credit balance. Ask first. These are cheap to ask about and
   expensive to get wrong.

### The failure mode to watch

Do not treat this section as permission to skip verification. **Rule 8 of §3
still binds**: verify by the migration ledger, the deployed SHA and the log
line. Four deploys once reported success while they shipped nothing. Faster
delivery makes that failure more likely, not less.

---

## 4. How development proceeds

**Feature by feature, app by app — one narrowed slice at a time.** The loop
(`/next-ticket`, defined in `.claude/commands/next-ticket.md`):

> **audit** the spec is dispatchable → **implement** one narrowed slice →
> **verify** independently against the acceptance criteria → **review** the diff
> adversarially → **open a PR** → **merge it, and watch it deploy**.
>
> ⚠️ **The last step changed on 2026-09-01, and only until 2026-09-30.** It read
> "stop before merge". That was the single largest brake on delivery, because
> merging to `main` IS the deploy — `.github/workflows/deploy.yml` reaches the
> box by itself. Stopping at the PR stopped the release.
>
> **Merging is not skipping the loop.** Audit, verify and review still run, and
> the verifier is still not the implementer. What you drop is the wait. After
> the merge, **watch the run to green and report the serving SHA** — an
> unwatched merge is worse than an unmerged PR. On 2026-10-01 this reverts to
> "stop before merge".

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
- **The UI is one product with ONE look.** Every app is a projection, never a
  surface with its own look: no app-local palette, no second colour
  vocabulary, no hand-rolled control. `workbench/control_plane/DESIGN_SYSTEM.md`
  is the contract and `AGENTS.md` beside it carries the eight rules and their
  fences — both are auto-loaded when you touch UI code. Owner directives
  2026-08-10 and 2026-08-31.
  ⚠️ **The theming engine is RETIRED (2026-08-31).** Four switchable themes,
  the `data-theme` attribute and the per-theme icon packs are gone.
  `src/app/globals.css` is the source of the one look — edit it to change how
  the app looks. `lib/theme/themes.ts` keeps the same values as a mirror for
  three data consumers (the app sandbox, Monaco/Shiki, the contrast gate), and
  `themes.test.ts` fails if the two drift. A member may still change **colour
  mode, density and accent**, which adjust the look and never replace it.
  Categorical hues (contexts, tags, labels) go through the `--cat-1…12` ramp
  and `src/lib/categorical.ts`, never a raw Tailwind palette class — and a
  HASH may only reach the first eight (`HASH_SLOTS`), or every assigned
  context repaints. Headless primitives come from `src/components/ui/` —
  `@base-ui/react` is the one substrate (D-PM-15) and `Modal.tsx` is the only
  file allowed to import it. The conformance suite checks eight regexes and
  **nothing tests layout or cross-app continuity**. The theme-switch check used
  to be the real gate and no longer exists, so the gate is now: **look at your
  surface in light mode, at compact density, and under a changed accent — and
  at its neighbour.**
- **Keep branches short and integrate often.** Long branches are the root cause
  behind the migration-renumber collisions, the green-alone/red-together PRs and
  a duplicated tenancy design. Three or four in flight is the ceiling.
- **Ship dark.** New behaviour lands behind a flag, default OFF. ⚠️ **Until
  2026-09-30 you may flip that flag yourself** (§3a, gate `enforcement-flip`).
  Name the flag and the box in the same message. The three flags that move money
  or reach a real person stay owner-only — see §3a rule 3.

## 5. What not to do

- **Do not re-litigate decisions.** **D1–D69** are taken *(this read "D1–D31" until 2026-08-26, and "D1–D54" until 2026-08-31)*. If one looks wrong, say so
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
- **R8 needs a database, and without one 843 tests SKIP while the run reads
  green.** Measured 2026-08-30. Run `bash scripts/dev_db.sh` first, then
  `eval "$(bash scripts/dev_db.sh --export)"`. `engineering_practice.md` §1.1
  owns the loop.
- `.claude/` (agents, commands, hooks, settings) is **tracked** — every checkout
  inherits the same review loop and the same refusals (D29). Local-only and
  derived paths stay ignored; the CodeGraph index is rebuilt, never committed.
