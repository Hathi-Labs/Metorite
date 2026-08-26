# `.claude/` — the autonomous dispatch loop

**Scope:** the Claude Code harness for this repo. It is the supervisor-worker
loop that runs `project-docs/work_plan.md` on its own, under supervision. The
parent contract is the root `AGENTS.md` (DOX). Nothing here is application code.
Nothing here ships to the VPS.

## What lives here

| Path | Kind | Purpose |
|---|---|---|
| `commands/next-ticket.md` | slash command | One supervisor cycle: select → audit → build → verify → review → PR |
| `agents/spec-auditor.md` | subagent | Is this WS dispatchable at all? Gatekeeper, read-only |
| `agents/ws-implementer.md` | subagent | Builds one cleared slice on a branch |
| `agents/ws-verifier.md` | subagent | Re-runs acceptance on its own, read-only |
| `agents/diff-reviewer.md` | subagent | Adversarial defect hunt on the diff, read-only |
| `hooks/plan-guard.mjs` | PreToolUse hook | Deterministic owner-gate + branch enforcement |
| `hooks/plan-guard.test.mjs` | test | `node .claude/hooks/plan-guard.test.mjs` |
| `hooks/rtk-bash.sh` | PreToolUse hook | Compresses noisy shell output before it reaches context |
| `hooks/session-handoff.mjs` | SessionStart hook | Injects the open `HANDOFF.md` entries (D39). Fails open |
| `hooks/ste-lint.mjs` | PostToolUse hook and CLI | Simplified Technical English, on the text just written |
| `hooks/ste-words.json` | data | The not-approved word list of record. One copy, no mirror |
| `hooks/ste-lint.test.mjs` | test | `node .claude/hooks/ste-lint.test.mjs` |
| `skills/ste/` | skill | Rewrite text into STE. Contract: `docs/style_ste.md` |
| `../.github/skills/impeccable/` | skill | Frontend design review (PostToolUse detector) |

## The three load-bearing ideas

1. **State lives in git, never in an agent.** `work_plan.md` §2 is the queue.
   The status header of each spec is the completion record. A cycle that dies
   part way through loses nothing, because the next cycle rebuilds its picture
   from the repo. An agent that "remembers" instead of writing it down has
   failed.

2. **Enforcement is a hook, not a prompt.** Instructions decay over a long run
   that nobody watches. `plan-guard.mjs` does not. It is why the loop can run
   with nobody watching. Every agent prompt *also* states the owner gates. That
   repetition is deliberate. It lets the agent refuse for a reason it can name,
   instead of finding the wall by hitting it.

3. **The verifier must not be the implementer.** Self-reported success is the
   main way an unattended loop fails. `ws-verifier` reads the diff before it
   reads the claims. It re-runs the commands the spec names, not the ones the
   builder chose.

## Rules for changing this directory

- **`work_plan.md` §6 and `plan-guard.mjs` change together, in the same PR.** A
  gate that exists in prose but not in the hook is not a gate. Add the case to
  `plan-guard.test.mjs` at the same time.
- **The loop stops at "PR opened".** A person does the merge and the deploy. The
  deploy applies migrations before the gateway restarts, and it resets the tree.
  An agent cannot undo either one. Do not add a deploy step here.
- **Subagents are one level deep.** A worker cannot dispatch workers, so all
  orchestration lives in `commands/next-ticket.md` or in the main session.
- **Keep worker tool grants narrow.** The read-only agents stay read-only. That
  is a correctness property, not a precaution. Cost matters too. The measured
  floor for injected tools is 19.3k tokens for each agent.
- **Every file here is STRICT-tier Simplified Technical English.** The contract
  is `docs/style_ste.md`. `ste-lint.mjs` reads the text you write and returns
  exit 2 on an error. Add a domain word to `ste-words.json`. Do not soften a rule
  to make a message pass.
- **Prompts here encode the standing rules** (R1 no absolute future migration
  numbers, R3 nomenclature, R4 status propagation). When those change in
  `work_plan.md`, update the agent prompts that quote them.
