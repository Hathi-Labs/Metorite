# Staged guard — apply this to close H-17 and H-53

**Two files here replace two files in `.claude/hooks/`. Copy both, or neither.**
The tests fail against the old guard, and the guard has cases the old tests do
not cover. They are one change.

```bash
cp project-docs/staged/guard/plan-guard.mjs      .claude/hooks/plan-guard.mjs
cp project-docs/staged/guard/plan-guard.test.mjs .claude/hooks/plan-guard.test.mjs
node .claude/hooks/plan-guard.test.mjs     # expect: all 55 cases passed
git rm -r project-docs/staged/guard
```

⚠️ **An agent cannot do the copy.** The harness classifier refuses an agent edit
to `plan-guard.mjs`, and that refusal is correct — it is the same one **H-17**
records in its own words. The work is staged here so you review a diff instead
of trusting a scratchpad.

---

## Why this exists

I built `plan-guard.mjs` from the file on `main`, by script, and changed four
things. Everything the script did not touch cannot drift from `main`.
`build_guard.py` fails loudly if any anchor it edits has moved.

### 1. Grants are finally READ — this is the whole speed change

**The owner wrote 23 `ALLOW` lines between 2026-08-19 and 2026-08-26. Every one
was ignored.** `plan-guard.mjs` on `main` never opens `OWNER_GRANTS.md`. The
writing half of D45 shipped and the reading half did not.

That is worse than a strict gate. It is a lock with no key cut, and it fails
**silently** — nothing tells the owner their grant went unread. From the owner's
chair it looks like an agent refusing work it was plainly authorised to do.

Measured against the real file, today:

| path | guard on `main` | this guard |
|---|---|---|
| `deploy/hostinger/probe.timer` | BLOCKED | **allowed** — today's `deploy-write` grant |
| `apps/services/.env` | BLOCKED | BLOCKED — no `env-write` grant today |

Every gate keeps its teeth. The owner now unlocks any one of them for one local
day with one line.

### 2. `.env.example` stops being a secret

It is a committed template. Git guarantees it holds no credential, and the
`Secret scan` CI check enforces that on every PR. Treating it as `.env` bought
nothing and cost two hand-offs — **H-30** and **H-34** — that sat open for days.

`.env`, `.env.local` and `.env.production` stay shut.

### 3. `2>&1` and `2>/dev/null` stop reading as file writes

The redirect arm was `>\s*\S`. That matches both. Neither writes a file. `2>&1`
duplicates a file descriptor, and `/dev/null` is the bit bucket.

**Measured 2026-08-26, inside one hour of that rule landing: four pure READS
refused** — an `ls` of `deploy/`, two `grep`s, and a probe of the guard itself.
This is the failure the rule's own comment predicts. A guard that fires on
ordinary work gets routed around on purpose, and the real block leaves with it.

### 4. The grant file is never agent-writable, and that is not grantable

A grant an agent can write is not a grant. Two tests carry a live `deploy-write`
grant and prove it does not help — neither `Write` nor a shell append reaches
`OWNER_GRANTS.md`.

---

## What the tests fix

The suite on `governance-d45-owner-grants` has **no grant isolation at all**. It
reads whatever the owner wrote this morning, so it would have started failing
the first day a grant existed. That is one reason the branch stalled unpushed.

Every case here runs against a synthetic project directory holding exactly the
grant lines that case declares. The suite is hermetic and date-safe — it builds
"today" from the clock, so it cannot rot.

**55 cases: the original 35, plus 5 for descriptor redirects, 5 for templates,
and 10 for grants.**

## What did NOT change

Force-push, history rewrite, `ssh`, deploy scripts, migration runners,
enforcement flips and `AGENT_PERMISSION_MODE` all still block. Branch discipline
still blocks a commit on `main`. `deploy/` and `.env` are still protected. The
difference is only that a named grant now opens one of them for one day.
