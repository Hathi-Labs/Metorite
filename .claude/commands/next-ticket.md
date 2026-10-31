---
description: Run one full supervisor cycle against the work plan — audit, build, verify, review by risk tier, PR, merge — for the next dispatchable WS ticket (or the one named).
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
Metorite holds the state (D118). Call `my_work` through the Metorite MCP and
take the top row, then `claim_task` on it. If the user named a task or a WS,
use it. Run `uv run python scripts/board.py --row WS-n` to read the row of its
workstream: the owning spec and the Metorite epic. If `my_work` is empty, pick
the highest-priority open task in that epic that is not owner-gated end to
end. Say which you picked and why, in one line.

**1b — Set the risk tier.** The tier sets how much review the slice gets.
Owner decision, 2026-10-16. Say the tier and the reason in one line.
- **HIGH**: tenancy, RLS, auth, identity, a migration, money, credits,
  secrets, a member or role write, or the deploy path. Verifier, then
  reviewer. At most two repair rounds.
- **STANDARD**: other product code. Verifier only while the feature is in
  build (step 1d). One repair round. The reviewer runs ONCE, on the whole
  feature, in the hardening pass. Record each deferred guardrail in
  `project-docs/hardening_backlog.md`, and land the slice.
- **LOW**: tooling, tests, docs, or a style change with no data path.
  Verifier only. Skip the reviewer. One repair round.
If you are not sure, take the higher tier. The verifier is never skipped.

**1c — Pick the model for each dispatch.** Owner directive, 2026-10-19:
Sonnet does the work, and Opus is for the hard cases. The agent files default
to Sonnet. Pass `model: "opus"` on the Agent call only where this table says
so. Say the models in the same line as the tier.

| Role | LOW | STANDARD | HIGH |
|---|---|---|---|
| `spec-auditor` | sonnet | sonnet | sonnet |
| `ws-implementer` | sonnet | sonnet | **opus** |
| `ws-verifier` | sonnet | sonnet | **opus** |
| `diff-reviewer` | skipped | **opus**, at the hardening pass | **opus** |

Two escalations override the table:
- **A repair round after a FAIL on the same finding**: dispatch the next
  `ws-implementer` with `model: "opus"`. Sonnet already missed it once.
- **A slice that crosses three or more services, or a concurrency, cache or
  ordering bug**: take `opus` for the implementer, at any tier.

Use `model: "haiku"` for a pure lookup with no judgement (find a file, list
callers, count rows). Never use haiku to build, verify or review.

**1d — Build or harden.** Owner directive, 2026-10-19. The contract is
`project-docs/specs/engineering_practice.md` §7a.
- **Build** (the default): ship working slices fast. Process fences and STE
  lint are advisory. UI diffs still run the four-capture visual review in
  build mode, and the UI gate fences block (owner, 2026-10-27,
  `workbench/control_plane/DESIGN_SYSTEM.md` §11). A UI diff also gets item 8
  of `diff-reviewer`, the UI checklist, even when step 6 skips the full
  review. Skip the board-row note,
  the spec header (R4) and new fence tests (R7). Run `bash scripts/ci_local.sh`
  (or `bash scripts/prepush.sh` when the PC is busy) before each push.
- **Harden** (when the feature works): one PR, labelled `harden`. Run
  `STE_LINT_BLOCK=1` for the session, `bash scripts/prepush.sh --harden`,
  `diff-reviewer` on the WHOLE feature diff, and the visual review. Write the
  deferred fences and the backlog items as Metorite tasks. Update the spec
  header.
- A HIGH-tier slice keeps the full loop in both modes.

**2 — Audit.** Dispatch `spec-auditor` on that WS.
- **A small slice skips the audit** (owner request, 2026-10-30, LD-2). A small
  slice is LOW, or STANDARD in build (1d). Its Metorite task must state the
  acceptance criteria and must not start a new spec. Then quote the task text
  verbatim as the contract, under the line `AUDIT: skipped (small slice)`.
  A HIGH-tier slice, and the first slice of a spec, always get the audit.
- `NO-GO` → stop the cycle. If the blocker is documentation, that *is* the next
  ticket: report it as such and offer to run a §5-style remediation instead.
  Never proceed to implementation on a NO-GO.
- `GO` / `GO-NARROWED` → carry the verdict block forward **verbatim**. It is the
  contract for every agent below.

**3 — Branch.**
`bash scripts/worktree.sh new <slug>`, off `origin/main`. Put the task's
`short_id` in the slug. Confirm the tree is clean first. If it is not, stop
and report. Never stash the work of another session.

**4 — Build.** Dispatch `ws-implementer` with the contract from step 2 pasted
in full. If it reports BLOCKED, stop the cycle and report — do not dispatch a
second implementer to route around a block.
- **A slice that changes what the app does or draws runs locally first.** The
  implementer starts `bash scripts/dev_up.sh` in its worktree. It checks each
  criterion in the running app before it reports, and it leaves the stack up
  for the verifier. `engineering_practice.md` §1.2 owns the stack.

**5 — Verify.** Dispatch `ws-verifier`. It checks each behaviour criterion on
the local stack itself (`bash scripts/dev_up.sh --check`, then the criterion
through the API or a capture), and quotes the evidence.
- `FAIL` → send the findings back to a fresh `ws-implementer` with the original
  verdict block plus the findings. **The tier (1b) sets the repair rounds.**
  For a STANDARD or LOW slice, land it if no remaining finding can reach a
  customer, and record those findings in `HANDOFF.md`. If it still
  fails, stop, leave the branch in place, and report what is unresolved. A loop
  that grinds on a stuck ticket burns budget and produces nothing.

**6 — Review.** Skip this step for a LOW slice, and for a STANDARD slice in
build (1d). Otherwise dispatch `diff-reviewer`. For a UI diff that skips this
step, dispatch `diff-reviewer` on item 8 only (1d).
- Any `P0` → repair round (counts against the same tier budget).
- `P1`/`P2` → carry into the PR body as known findings; do not block on them.

**7 — Land.**
- In build (1d), a comment on the Metorite task is enough: what shipped. Move
  the task with `set_task_status`. The spec header (R4) waits for the
  hardening pass.
- In harden, comment on the task: a dated note saying what shipped and what
  remains. Add a task for each remaining item. This is the handoff to the
  next cycle — write it for an agent with no memory of this one. Change the
  `work_plan.md` §2 row only when the workstream's spec or epic changes. Confirm the
  owning spec's status header (R4) is current. If it is not, do it now.
- Commit, push the branch, open a PR with `gh`. The PR body: WS id, the slice
  built, acceptance evidence, verification output, reviewer findings, and what
  was deferred.
- **Merge it and watch it** (root `CLAUDE.md` §3a and §4). Wait for `pr-check`
  to go green. Then run `bash scripts/premerge.sh` in the worktree, in the
  same command as the merge:
  `bash scripts/premerge.sh && gh pr merge <n> --squash`. It tests the merge
  with today's main (migration numbers, H-ids, D-numbers, files both sides
  changed). Exit 1 means merge `origin/main` into the branch, fix, push, and
  wait for `pr-check` again. Then watch the `deploy` run to green.
- **Ship it (D144, owner directive, 2026-10-31).** Staging is retired. A merge
  moves `main` only. Production moves at 21:30 UTC, or when you dispatch it.
- Dispatch only after the unit-test job on the tip of `main` passes (see the
  next bullet).
- Check the range: `git log <prod applied_sha>..origin/main`. List the
  migrations under `infra/postgres/` in it.
- Dispatch: `gh workflow run deploy.yml --ref main -f reason="<why>"`. The run
  ships every merge in the range. Name them in your report.
- Watch the run. Confirm that `/version` `applied_sha` shows the tip and that
  the smoke jobs pass. For a migration, read the pre-migration backup line in
  the production journal.
- Report "shipped to production at `<sha>`". Do not call it done before you see
  that SHA.
- `RELEASE_HOLD` stops the daily release only. Set it to `1` to hold that
  night's run. `specs/staging_and_daily_release.md` owns the design.
- Watch the job `Unit tests (a signal on a push, a gate on a release)` in that
  same `deploy` run to its end. On a push it does not gate, so two PRs that
  pass alone and fail together can land on `main`. A red `main` also blocks
  that night's release. If the job is red, fix forward or revert at once, and
  say so in the report. (No merge queue. Owner decision, 2026-10-16.)
- Deploy auto-applies migrations and you cannot undo a bad one (R6). Before a
  migration reaches production, confirm the pre-migration backup completed.

## Refusals

**⚠️ Read root `CLAUDE.md` §3a first.** The window ends on the date of the
`ALLOW-UNTIL` lines in `.claude/OWNER_GRANTS.md`. Check that file for a dated
line that covers the gate id. If a line covers it, **do the work**. Do not
refuse, and do not ask the owner again.

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
AUDIT:    GO | GO-NARROWED | NO-GO | skipped (small slice) — <one line>
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
