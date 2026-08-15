# Metorite migration board

**What this owns:** every step of moving from `FracktalWorks/CommandCenter` to
`Hathi-Labs/Metorite`, from repo creation to the brand being fully landed.
This is the **only current-state authority for the migration**. When a row closes,
close it here in the same PR (R4).

States: 🟢 ready to dispatch · 🟡 dispatchable after the named gate ·
🔴 blocked on owner/decision · ✅ done.

Related: [`specs/rebrand_metorite.md`](specs/rebrand_metorite.md) (what to rename and
what to freeze) · [`../docs/history/README.md`](../docs/history/README.md) (inherited
history) · CommandCenter `work_plan.md` **WS-2** (the secrets P0 this migration inherits).

---

## 0. State of play — verified 2026-08-16

Everything below was measured, not assumed.

| Fact | Value | How verified |
|---|---|---|
| Target repo | `Hathi-Labs/Metorite`, **public**, created | `gh repo create`, returned URL |
| Target contents | **empty** — 0 KB, zero refs | `git ls-remote` returned nothing |
| Your org role | **admin** on Hathi-Labs (free plan) | `gh api orgs/Hathi-Labs/memberships/vjvarada` |
| Mirror staged | 72 branches · 2 tags · **449** `refs/archive/pr/*` | `git for-each-ref` on the bare mirror |
| Mirror remote | `git@github.com:Hathi-Labs/Metorite.git` | `git remote get-url origin` |
| PR archive built | **449 of 449** PRs, 437 merged, 12 MB | export run exit 0, file count |
| Source history | 2042 commits, 91 MB, `main` = `526471770` | `git rev-list --count`, `ls-remote` |

**One incident, closed.** The first push attempt went to `FracktalWorks/CommandCenter`
instead of Metorite — a `git remote set-url` was chained into a hook-blocked command,
so it never ran and `origin` was still the clone's inherited source URL. Verified
non-destructive: `main` unchanged at `526471770`, all 72 branches and both tags intact,
all 449 `refs/pull/*` intact, repo size unchanged. Net effect: **449 stray
`refs/archive/pr/*` refs now exist on CommandCenter** — see MG-9. The push script now
sets the URL itself and aborts if it does not match.

---

## 1. The board

### Blocking — nothing else lands until these clear

| # | Item | State | Gate · next |
|---|---|---|---|
| **MG-1** | **Push the mirror to Metorite** | 🔴 | OWNER-GATE. `plan-guard.mjs` blocks agent pushes under both the force-push rule and the on-`main` rule (§6, BO-8/WS-2), and no override exists in the hook — I read all 172 lines; the only `process.env` is line 85 reading the project dir. Run `scratchpad/OWNER-RUN-mirror-push.sh`; it asserts the remote URL, prints ref counts, waits for `y`, pushes, then verifies. Everything else on this board is downstream of this row. |
| **MG-2** | **Decide what to do about secrets in history** | 🔴 | OWNER-GATE + **decision owed**. Inherits CommandCenter **WS-2 / BO-8**, a standing P0 since 2026-07-11. See §2 below — this is the one that needs your eyes before MG-1, not after. |
| **MG-3** | **Licensing / IP between the two entities** | 🔴 | OWNER-GATE. **There is no `LICENSE`, `COPYING`, or `NOTICE` file anywhere in the tree.** Code authored under FracktalWorks is being published under Hathi-Labs. Pick a license and record the assignment. Public repo, so this gets asked. |

### GitHub-side setup — none of it mirrors, all of it is hand-work

| # | Item | State | Gate · next |
|---|---|---|---|
| **MG-4** | Actions secrets | 🔴 | OWNER-GATE (live credentials, §6). Every CI and deploy workflow is red until these exist. |
| **MG-5** | Branch protection on `main` | 🔴 | OWNER-GATE. Re-create CommandCenter's exact shape: PRs required, `required_approving_review_count: 0`, `enforce_admins: true`, force-push and deletion blocked, and **`required_status_checks: null` deliberately** — `pr-check.yml` has `paths-ignore: ["**.md", "project-docs/**"]`, so a docs-only PR produces zero check-runs and requiring contexts makes it permanently unmergeable. |
| **MG-6** | Require 2FA on the Hathi-Labs org | 🔴 | OWNER-GATE. Free plan, new org, one member. Cheapest control you will ever set. |
| **MG-7** | **Disable the deploy workflows before first push** | 🟡 | After MG-1. The mirrored `.github/workflows` still target Fracktal's VPS. The box runs a **5-minute pull timer against `/opt/acb/app`** — two repos self-deploying into one path will fight on every push. Disable, then decide separately where Metorite deploys. |
| **MG-8** | Expect secret-scanning alerts | 🟡 | Informational. Pushing those blobs to a public repo will fire GitHub secret scanning; providers may auto-revoke. Not a failure — treat as confirmation of MG-2. |

### Cleanup owed to the source repo

| # | Item | State | Gate · next |
|---|---|---|---|
| **MG-9** | Delete the 449 stray `refs/archive/pr/*` from CommandCenter | 🔴 | OWNER-GATE (ref deletion on a live repo). Harmless — they point at commits the repo already had and are invisible in the UI. Low urgency, but it is residue from the misdirected push and should not be forgotten. `git push origin --delete` over the ref list, or leave them; either is defensible. |

### The first PR on Metorite — bundle is built and waiting

| # | Item | State | Gate · next |
|---|---|---|---|
| **MG-10** | Commit `docs/history/**` (449 PR files + index + raw JSON) | 🟡 | After MG-1. Built and verified in `scratchpad/history-export/`. |
| **MG-11** | Commit `project-docs/specs/rebrand_metorite.md` and this board | 🟡 | After MG-1. Add both to `project-docs/INDEX.md` **in the same PR** — a spec outside INDEX is a defect. |
| **MG-12** | Rewrite `CLAUDE.md` for the new repo | 🟡 | After MG-1. Line 3 (`You are working on **CommandCenter**`) is the single highest-leverage string in the tree — every agent session reads it. |

### The rebrand itself — six piles, one PR each

Scope measured: **~850 occurrences across 275 files.** Full detail and the
freeze list in [`specs/rebrand_metorite.md`](specs/rebrand_metorite.md).

| # | Pile | State | Notes |
|---|---|---|---|
| **MG-13** | UI strings (`workbench/control_plane/src/**`) | 🟡 | The only pile a customer sees. Do it first. |
| **MG-14** | Repo URLs (`.github/workflows`, `README.md`, `scripts/**`) | 🟡 | Anything that clones or `gh api`s `FracktalWorks/CommandCenter` breaks silently otherwise. |
| **MG-15** | Product docs (`project-docs/**`, 39 files under `specs/`) | 🟡 | **Do not rewrite D1–D31.** They were taken under the old name; rewriting destroys the audit trail. Banner them instead. |
| **MG-16** | Engineering docs (`docs/**`, `learning-resources/**`, `AGENTS.md`) | 🟡 | `learning-resources/` is 16 first-party Markdown files — no third-party licensing concern (verified: zero vendored dirs in the tree). |
| **MG-17** | Test fixtures (`tests/live`, `tests/unit`, 30 files) | 🟡 | Some assert on the literal string. Change fixture and assertion together or the suite goes red. |
| **MG-18** | **Brand fence** — `tests/unit/test_brand_surface.py` | 🟡 | **R7.** Assert no `CommandCenter` / `Command Center` under `workbench/control_plane/src/`. Without a fence the brand rots back in. Everything outside `src/` stays advisory. |

### Housekeeping

| # | Item | State | Gate · next |
|---|---|---|---|
| **MG-19** | Prune the 72 inherited branches | 🟡 | After MG-10. Mostly `worktree-agent-*` scratch from the old dispatch loop. Their commits stay reachable through `refs/archive/pr/*` regardless, so pruning loses nothing. |

---

## 2. MG-2 in full — the decision that gates the push

The 2026-07-11 commit `f976d52d`, *"security: purge committed secrets/junk from tree
+ gitignore (audit F2)"*, **removed four files from the working tree and left every one
of them reachable in history.** All four are public in CommandCenter today.

| File | First committed | Size | Status |
|---|---|---|---|
| `acb_dump.bak` | 2026-06-11 `bbdc54eb` | **1.7 MB** — a database dump | contents unexamined |
| `.zoho_token_cache.json` | 2026-05-26, initial commit | 197 bytes — OAuth token cache | contents unexamined |
| `_test_byok_final.py` | 2026-06-09 `6a96e8dc` | 30 lines — BYOK test, likely holds a key | contents unexamined |
| `gateway.pid` | — | 1 line | harmless |

Contents were deliberately not read. What they hold decides the path:

- **Schema or synthetic fixtures** → push the mirror verbatim, rotate, move on.
- **Real rows** (mailboxes, `integration_credentials`, org or personal data) → this stops
  being a credential rotation and becomes a data-disclosure question. Scrub before Metorite
  gets a copy.

```bash
git cat-file -p bbdc54eb:acb_dump.bak | head -c 4000
```

**Rotate the Zoho token and the BYOK key regardless of the outcome.** They have been in a
public repo for two to three months — scraped, forked, and sitting in GitHub's dangling-object
storage. Deletion does nothing at this point; rotation is the only fix that works. Scrubbing
history without rotating is the security-theatre version of this task. This is exactly what
**WS-2** has said since 2026-07-11.

**Scrubbing costs you the thing you asked for.** `git filter-repo` rewrites every SHA from
2026-05-26 onward. The 449 archive refs survive the rewrite, but they stop sharing SHAs with
CommandCenter — cross-referencing the old repo breaks, and every merge SHA in
`docs/history/pull-requests/index.md` becomes wrong. History fidelity was the point of this
exercise. Only pay that cost if the dump justifies it.

---

## 3. Runbook — the order these actually go in

1. **Inspect `acb_dump.bak`** (MG-2). Decide verbatim-vs-scrub.
2. **Rotate** the Zoho token and BYOK key (MG-2 / WS-2). Independent of 1 — do it either way.
3. **Push the mirror** (MG-1): `bash scratchpad/OWNER-RUN-mirror-push.sh`.
4. **Verify**: 72 heads, 2 tags, 449 pr refs on the remote — the script prints all three.
5. **Set org and repo controls** (MG-4, MG-5, MG-6) *before* inviting anyone.
6. **Open PR #1** on Metorite: `docs/history/**` + `rebrand_metorite.md` + this board +
   `INDEX.md` entries + `LICENSE` (MG-3, MG-10, MG-11), and disable the deploy
   workflows in the same PR (MG-7).
7. **Rebrand, one PR per pile**, UI first (MG-13 → MG-18).
8. **Prune branches** (MG-19), **clean up CommandCenter's stray refs** (MG-9).

## 4. What the mirror does and does not carry

**Carries:** all 2042 commits, 72 branches, 2 tags, and the head commit of every one of
the 449 pull requests as `refs/archive/pr/<n>`.

**Does not carry, because git does not hold it:** PR and issue conversations (archived
as data in `docs/history/` instead — GitHub can only move these natively through
Enterprise Importer, which needs GitHub Enterprise Cloud and Hathi-Labs is on the free
plan), Actions secrets, branch protection, deploy keys, environments, webhooks, stars,
watchers.

## 5. One note on how you work from here

You said you would **fork** Metorite. **Clone it instead.** A personal fork makes every PR
you open default its base branch to the Hathi-Labs repo — the same footgun that made
mirroring the right call over forking in the first place. Clone, branch, PR.
