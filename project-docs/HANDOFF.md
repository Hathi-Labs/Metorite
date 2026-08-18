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

### H-11 · Stand up Metorite production: DNS, box, Supabase, secrets · [OWNER]
- **Check:** ALL of: `nslookup app.metorite.com` resolves ·
  `nslookup anycustomer.metorite.com` resolves (the D41.3 wildcard record) ·
  Actions secrets in `Hathi-Labs/Metorite` show `HOSTINGER_*` ·
  `gh workflow list --all` shows `deploy` enabled · the box `.env` carries
  `ACB_MASTER_KEY` and `AUTH_MICROSOFT_ENTRA_ID_ID`. Any one missing
  → still pending.
- **Why:** Topology and identity are **D40 + D41** (`work_plan.md` §3); the ops
  runbook is `docs/EXTERNAL_POSTGRES.md`. What remains is owner-only: DNS
  records (`app.` · `api.` · the `*.metorite.com` wildcard, D41.3), wiping the
  old CommandCenter deployment off the VPS (D41.4 — rotate the SSH
  credentials, H-3, in the same session), GitHub secrets, the Google OAuth app
  (redirect `https://app.metorite.com/api/auth/callback/google`), the
  **multi-tenant** Entra app registration (redirect
  `https://app.metorite.com/api/auth/callback/microsoft-entra-id`; leave
  `AUTH_MICROSOFT_ENTRA_ID_TENANT` unset — D41.2), the box `.env`, and
  re-enabling the three workflows an agent turned off with
  `gh workflow disable` on 2026-08-17 — GitHub-side state; re-derive with
  `gh workflow list --all`, never from this file.
- **Authority:** `work_plan.md` §6 · D40 · D41
- **Added:** 2026-08-16 · updated 2026-08-17 (review round 1: decisions moved
  out of this entry into D40 — this file must not restate state) · updated
  2026-08-18 (D41: wildcard record · Entra registration · VPS wipe)

### H-13 · Apply the two plan-guard-gated patches (deploy.sh, .env.example) · [OWNER]
- **Check:** `rg -n "3a83c19d" deploy/hostinger/deploy.sh` → a hit means still
  pending. `rg -n "127.0.0.1:8000" .env.example` → a hit means still pending.
- **Why:** plan-guard forbids agent writes under `deploy/` and to `.env*`, so
  two fixes travel as ready-made blocks in the production-enablement PR
  description instead of commits: (a) `deploy/hostinger/deploy.sh` still seeds
  one company's Entra directory GUID into every fresh box
  (`scripts/vps_apply.sh`, the CI path, is already fixed); (b) `.env.example`
  ships `GATEWAY_BASE_URL` on port 8000 while the gateway listens on 8080,
  lacks `ACB_MASTER_KEY` entirely (load-bearing — encrypts stored provider
  keys), and carries the dead `AUTH_ALLOWED_DOMAIN` variable.
- **Authority:** plan-guard.mjs (D29) · `work_plan.md` §6
- **Added:** 2026-08-17 · production-enablement session

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

---

# DONE — deleted, not archived

Nothing lives here. When an entry's Check passes, **delete the block**. Git
history is the archive; a "done" section in a loaded-into-context file is just
tokens that make the open items harder to find.
