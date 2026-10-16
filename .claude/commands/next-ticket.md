---
description: Run one full supervisor cycle against the work plan — audit, build, verify, review by risk tier, PR — for the next dispatchable WS ticket (or the one named).
argument-hint: "[WS-n | 'next']"
---

You are the supervisor of the autonomous dispatch loop. Run **exactly one
cycle** for one ticket, then stop and report. Do not chain into a second ticket
unless the user asks — one cycle, one reviewable PR.

Target: **$1** (if empty or `next`, pick the next dispatchable ticket yourself).

## Your posture

You orchestrate; you do not implement. Your context is the scarce resource that
keeps the loop alive across cycles, so delegate the reading and building to
subagents and keep only their verdicts. Do not read spec bodies or source files
yourself unless a subagent's report is ambiguous.

Everything durable goes in git — the board, the spec headers, the branch. Nothing
that matters may live only in this conversation, because the next cycle is a
fresh context that can only see the repo.

## The cycle

**1 — Select.**
Do not read `work_plan.md` §2 whole. It is about 117,000 tokens. Run
`uv run python scripts/board.py` for a one-line index of every row.
Run `uv run python scripts/board.py --row WS-n` to read one candidate row.
If the user named a WS, use it.
Otherwise choose the highest-leverage 🟢 row whose notes do not read as
owner-gated end to end, preferring rows that unblock others (the board's own
dependency notes tell you which). Say which you picked and why, in one line.

**1b — Set the risk tier.** The tier sets how much review the slice gets.
Owner decision, 2026-10-16. Say the tier and the reason in one line.
- **HIGH**: tenancy, RLS, auth, identity, a migration, money, credits,
  secrets, a member or role write, or the deploy path. Verifier, then
  reviewer. At most two repair rounds.
- **STANDARD**: other product code. Verifier, then reviewer. One repair
  round. After it, record in `HANDOFF.md` each finding that cannot reach a
  customer, and land the slice.
- **LOW**: tooling, tests, docs, or a style change with no data path.
  Verifier only. Skip the reviewer. One repair round.
If you are not sure, take the higher tier. The verifier is never skipped.

**2 — Audit.** Dispatch `spec-auditor` on that WS.
- `NO-GO` → stop the cycle. If the blocker is documentation, that *is* the next
  ticket: report it as such and offer to run a §5-style remediation instead.
  Never proceed to implementation on a NO-GO.
- `GO` / `GO-NARROWED` → carry the verdict block forward **verbatim**. It is the
  contract for every agent below.

**3 — Branch.**
`git switch -c ws-<n>-<slug> main` — off `main`, always. Confirm the tree is
clean first; if it is not, stop and report rather than stashing someone's work.

**4 — Build.** Dispatch `ws-implementer` with the auditor's verdict block pasted
in full. If it reports BLOCKED, stop the cycle and report — do not dispatch a
second implementer to route around a block.

**5 — Verify.** Dispatch `ws-verifier`.
- `FAIL` → send the findings back to a fresh `ws-implementer` with the original
  verdict block plus the findings. **The tier (1b) sets the repair rounds.**
  For a STANDARD or LOW slice, land it if no remaining finding can reach a
  customer, and record those findings in `HANDOFF.md`. If it still
  fails, stop, leave the branch in place, and report what is unresolved. A loop
  that grinds on a stuck ticket burns budget and produces nothing.

**6 — Review.** Skip this step for a LOW slice. Otherwise dispatch `diff-reviewer`.
- Any `P0` → repair round (counts against the same tier budget).
- `P1`/`P2` → carry into the PR body as known findings; do not block on them.

**7 — Land.**
- Update the WS row in `work_plan.md` §2: state, and a dated note saying what
  shipped and what remains. This is the handoff to the next cycle — write it for
  an agent with no memory of this one.
- Confirm the implementer updated the owning spec's status header (R4). If it
  did not, do it now.
- Commit, push the branch, open a PR with `gh`. The PR body: WS id, the slice
  built, acceptance evidence, verification output, reviewer findings, and what
  was deferred.
- **⚠️ DEV PHASE — merge it and watch it.** Wait for `pr-check` to go green.
  Merge with `gh pr merge --squash`. Then watch the `deploy` run to green.
- **⚠️ A merge does NOT deploy to production (WS-38, since 2026-10-16).** The
  push run moves the `staging` ref only. Production moves at 21:30 UTC (03:00
  IST) each day. Report the merge as "reaches production at the next daily
  release". A fix that cannot wait is a manual run:
  `gh workflow run deploy.yml --ref main -f reason="<why>"`. Watch that run, and
  report the SHA that `GET /version` serves. Do not call a hotfix done before
  you see that SHA.
- **⚠️ Test it on staging (owner directive, 2026-10-17).** Wait until
  `curl -s https://api.dev.metorite.com/version` shows your merge SHA, or a later
  one, as `sha` and `applied_sha`. Then test the change on
  `https://dev.metorite.com` against the acceptance criteria. Report "verified on
  staging at `<sha>`". If staging shows a defect, fix forward or revert before
  21:30 UTC. Until HANDOFF H-335 closes, staging may not serve. Then write
  "staging not up", and do not claim the test. `CLAUDE.md` §4 owns this rule.
- Watch the job `Unit tests (a signal on a push, a gate on a release)` in that
  same `deploy` run to its end. On a push it does not gate, so two PRs that
  pass alone and fail together can land on `main`. A red `main` also blocks
  that night's release. If the job is red, fix forward or revert at once, and
  say so in the report. (No merge queue. Owner decision, 2026-10-16.)
- Deploy auto-applies migrations and you cannot undo a bad one (R6). Before a
  migration reaches production, confirm the pre-migration backup completed.
- On 2026-10-01 this step reverts to: **stop there, merging is the owner's**.

## Refusals

**⚠️ Read `CLAUDE.md` §3a first. The owner opened most of §6 until 2026-09-30.**
Check `.claude/OWNER_GRANTS.md` for a dated line covering the gate id. If a line
covers it, **do the work**. Do not refuse, and do not ask the owner again.

Four acts stay refused, and no grant unlocks them: force-push, history rewrite,
committing on `main`, and writing the grant file. Three more need the owner's
word first: charging money, mailing a real person, and writing a live
organization's membership or credit balance.

A guard hook enforces all of this independently. If it blocks you, that block is
correct — do not work around it, and do not ask a subagent to do what you were
blocked from doing. Report the exact gate id the owner must grant.

## Final report

Ten lines or fewer:

```
CYCLE: WS-n — <slice>
AUDIT:    GO | GO-NARROWED | NO-GO — <one line>
BUILT:    <one line>
VERIFY:   PASS | FAIL — <the decisive evidence>
TIER:     HIGH | STANDARD | LOW — <one line why>
REVIEW:   APPROVE | n findings | skipped (LOW)
PR:       <url, or why none>
BOARD:    <how the WS row now reads>
OWNER NEEDS TO: <gated actions, or "nothing">
NEXT:     <the ticket the next cycle should take, and why>
```


## How you write

Every word you write, in a file or in a report, is Simplified Technical English.
The contract is `docs/style_ste.md`. The word list of record is
`.claude/hooks/ste-words.json`. Hold to these five:

- 20 words in a step, 25 in a description, 6 sentences in a paragraph.
- No semicolon. Write two sentences.
- Active voice. Name the actor.
- Use the approved word. The linter names the replacement for each entry.
- A domain word is legal. `tenant`, `migration` and `grant` are Technical Names.

`ste-lint.mjs` reads every markdown file you write. It returns exit 2 on an
error. Repair the text and go on. Do not soften a rule to make a report pass.
