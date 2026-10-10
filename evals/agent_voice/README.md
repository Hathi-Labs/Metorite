# The voice eval (WS-52 S1)

This folder holds the eval set of the house voice. The owning spec is
`project-docs/specs/agent_writing_voice.md`, §7.

**Status.** Built on 2026-10-10. The scripted run passes. Nobody has recorded
the live baseline yet, because no model was reachable from the build machine.

## What it measures

Each case asks for text on one surface: chat, title, description, email or
summary. `acb_llm.voice.voice_lint` scores each answer, by rule. The score is
a count of findings, so a lower count is better. The checker cannot see every
rule. Spec §6.5 lists what it misses.

## The files

| File | What it holds |
|---|---|
| `cases.json` | Twelve cases: three chat, two title, two description, two email and three summary. Each case has its records and its ask |
| `scripted_answers.json` | One hand-written answer for each case, and the rules that the checker must find in it |
| `run.py` | The runner |

An agent wrote each case and each scripted answer. Every name is made up, and
every address ends in `.example`. Some scripted answers break the voice on
purpose, so the checker has work to do.

## How to run it

1. Run the scripted check. It calls no model, and the unit job runs it too:

   ```bash
   uv run python -m evals.agent_voice.run --scripted
   ```

2. Record the live baseline by hand. Start a local stack or point at the
   Router, then run:

   ```bash
   LITELLM_BASE_URL=http://127.0.0.1:8080 uv run python -m evals.agent_voice.run --live --model tier-balanced
   ```

   The live run asks each case twice: once with a plain system prompt, and
   once with `voice_prompt(surface)` added. It writes both answers and both
   scores to `baseline.json`. Commit that file.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | The scripted run matched, or the live run finished |
| 1 | A scripted answer did not match its expected rules |
| 2 | The live run has no `LITELLM_BASE_URL` |
