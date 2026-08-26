---
description: Read, prune and extend the cross-session handoff queue (project-docs/HANDOFF.md)
argument-hint: "[check | add <what> | done <H-n> | end]"
---

# /handoff — the queue that outlives a session

`project-docs/HANDOFF.md` is what one session leaves the next. It is injected at
session start by `.claude/hooks/session-handoff.mjs` (D39), so nothing pending
depends on the owner remembering it.

Argument: `$ARGUMENTS` (default: `check`).

---

## The rule underneath all four verbs

> **Never restate state in HANDOFF.md. Point at it, and carry the command that
> re-derives it.**

`work_plan.md` §2 is the only current-state authority (CLAUDE.md §1). If this
file also described state it would be a second board — two descriptions of one
truth, the stale one trusted precisely because it is the one loaded into the
prompt. Every entry therefore carries a **Check**, and no entry is believed
without running it.

---

## `check` — the default, and what a session should do first

1. Read `project-docs/HANDOFF.md`.
2. **Run every entry's Check.** Not a skim: run it.
3. **Delete the whole block for every entry whose Check shows it is done.** Do
   not tick it off, do not move it to a "done" section — delete it. Git history
   is the archive; a done-list in a file loaded into every prompt is tokens that
   bury the live items.
4. If a Check no longer runs (a file moved, a command changed), the entry is
   **not** thereby done — repair the Check, or say plainly that you could not
   verify it. An unverifiable entry stays open.
5. Report what remains in one short list. Then get on with what the user asked
   for — this is a preamble to the session, not the session.

⚠️ `[OWNER]` entries are `work_plan.md` §6 gates. Verify the Check and report;
**never do the thing**. Confirming an owner entry is still pending is help;
doing it is the refusal §6 exists for.

## `add <what>` — record an obligation the moment it exists

Write it when you create it, not at the end when context is short. The entry
written while you still remember why is worth five reconstructed later.

Append to the `# OPEN` section, next free id, ids never reused:

```
### H-<n> · <one line, imperative> · [AGENT|OWNER]
- **Check:** `<command>` → <what output means STILL PENDING>
- **Why:** <one or two sentences — the reason, not the status>
- **Authority:** <file §section, or board row>
- **Added:** <today> · <session or PR>
```

Getting the **Check** right is the whole job. It must be:

- **cheap and read-only** — a `rg`, a `ls`, a `git log`, a single SELECT;
- **specific about which output means PENDING**, so a later session cannot read
  the result the convenient way;
- **honest when it cannot be run** — for an entry that needs a box an agent
  cannot reach, say so in the Check itself (see H-1), rather than writing a
  command that will silently fail and look like a pass.

A Check you cannot write usually means the entry is really two entries, or that
you have not yet decided what "done" is. Both are worth finding out now.

## `done <H-n>` — close one

Run its Check first. If it passes, delete the block and say so in the commit
body. If it does not pass, do not delete it — say what is still outstanding.

## `end` — the close-out sweep, before finishing a session

Walk these and add an entry for each hit:

- work **started and not finished** — where it got to, and the next step;
- an **owner gate** you refused — by name, so the owner sees it rather than
  learning it from a stalled deploy;
- a **finding you scoped out** — CLAUDE.md §5 says record, do not refactor;
  this is where the record goes if it does not yet deserve a board row;
- a **decision you are owed** — with what you would recommend, so the answer is
  a yes/no rather than a fresh analysis;
- anything **time-sensitive or ordered** — say what it is ordered against
  (H-2 must precede H-1, and says so).

Then run `check` once more so you hand over a queue that is true at the moment
you hand it over.

⚠️ **Bias toward adding.** A one-line entry costs nothing and is deleted in
seconds by the next session's `check`. The obligation nobody wrote down costs a
session — that is the failure this file exists for, and it has already happened
here.


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
