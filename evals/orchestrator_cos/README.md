# The chief-of-staff eval (orchestrator)

This folder holds the eval set of the orchestrator's chief-of-staff role. The
owning spec is `project-docs/specs/agent_writing_voice.md`, §11.

**Status.** Built on 2026-10-13. The scripted run passes. Nobody has recorded
the live arms yet, because the build machine had no Router.

## What it measures

Each case is one member turn. It holds an ask, and the reads that the
orchestrator already ran, as tool calls with their results. The model writes
the reply. A card that the reply draws counts as folded detail. Its text
counts for a request for detail, and never for the cap.

| Check | It passes when |
|---|---|
| `bluf` | The first sentence holds a lead term of the case as a whole word, has 35 words or fewer, and opens with no preamble ("Here is", "I checked", "Let me", "Based on") |
| `length` | A routine reply has 120 words of prose or fewer, or the `max_words` of the case. A request for detail has at least `min_words`, with the card |
| `no_dump` | No JSON, no fenced block, no UUID outside an `[entity:...]` citation, no raw id, no ISO time stamp, no `[agent-name]` header |
| `no_relay` | A routine reply copies fewer than 20 words in a row from any one read |
| `recommendation` | A decision case holds a recommendation. One sentence holds a recommending word ("recommend", "suggest", "go with", "counter at") or a verb with the pick ("Hire Tomas"), and it names a pick of `recommend_any` |
| `mentions` | Each group of terms of the case shows at least once. These are the facts that matter, and the items that the member did not ask about |
| `keeps_deliverable` | The draft of case C11 is still whole |
| `voice` | `acb_llm.voice.voice_lint` finds nothing |
| `no_tool_name` | The prose names no tool and no agent: no snake_case word, and no `*-assistant` name |
| `no_guillemets` | The prose holds no « or » |
| `one_ask` | A case with `one_ask` asks the member at most one question |

`--judge-model` adds a model rubric: five scores from 1 to 5 for
`bottom_line`, `distilled`, `proactive`, `decision_support` and `length_fit`.

## The files

| File | What it holds |
|---|---|
| `cases.json` | Fifteen cases, and the checks of each |
| `scripted_answers.json` | A good answer for each case, and a bad answer that fails named checks |
| `run.py` | The runner |
| `prompt_before.txt` | The prompt of the orchestrator before the role, for the `before` arm |
| `results.json` | The live arms, after somebody runs them |

An agent wrote each case and each answer. Every name is made up, and every
address ends in `.example`.

## How to run it

1. Run the scripted check. It calls no model, and the unit job runs it too:

   ```bash
   uv run python -m evals.orchestrator_cos.run --scripted
   ```

2. Record the live arms by hand, through the Router. Put the key that the
   Router accepts in `LITELLM_API_KEY`. `prompt_before.txt` is the prompt of
   the orchestrator before the role, built on 2026-10-13 from `6ea6b3e8c`.
   The `after` arm builds the current prompt itself:

   ```bash
   export LITELLM_BASE_URL=http://127.0.0.1:8080
   uv run python -m evals.orchestrator_cos.run --live --arm before --prompt-file evals/orchestrator_cos/prompt_before.txt --judge-model tier-balanced
   uv run python -m evals.orchestrator_cos.run --live --arm after --judge-model tier-balanced
   uv run python -m evals.orchestrator_cos.run --report
   ```

   One arm makes about 24 model calls and 12 judge calls. Commit
   `results.json`. When you can, give `--judge-model` a model that is not
   the model under test, because a model grades its own text too kindly.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | The scripted run matched, or the live run finished |
| 1 | A scripted answer did not match what it expects |
| 2 | The live run has no `LITELLM_BASE_URL` |
