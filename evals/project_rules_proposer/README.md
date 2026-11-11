# The rule proposer eval (WS-ALN ALN-5b)

This folder holds the eval of the rule proposer. The owning spec is
`project-docs/specs/project_alignment.md`, §4.2 and the ALN-5b row of §10.

**Status.** Built on 2026-11-07. The scripted run passes each bar. Nobody has
done the live run yet. A live run over USD 0.50 is OWNER-GATE.

## What it proves

A lead asks Mira for the rules of one pinned page. The route
`POST /project-rules/projects/{id}/propose` makes one model call with no
tools, and the server checks each answer. The eval runs the real prompt, the
real call shape and the real checks of
`apps/services/gateway/gateway/routes/project_rules_propose.py`.

## The files

| File | What it holds |
|---|---|
| `cases.json` | Six synthetic pages. Each page has a scripted answer and the expected result |
| `checkers.py` | The score of a run, and its six bars |
| `run.py` | The runner: `--scripted`, `--live` and `--score` |

An agent wrote each page and each answer by hand. No model wrote them.

## The cases of the set

- A page with CRLF line ends. The answer holds an invented quote, a quote of
  6 characters and a quote with LF line ends. It also holds a blank sentence
  and a sentence that it gives twice.
- A page with 14 rules, and an answer with 14 rules. The checks keep 12.
- A page that tells the model to call `create_task`, with a forged end marker.
  The answer holds a `tool_calls` key. The checks ignore that key.
- A page with 10 proposals that wait already, and a rule that is active
  already. The checks keep 2.
- A page with no rule, and an answer that is not JSON.

## The bars

| Bar | What passes |
|---|---|
| `quotes` | Each kept quote is an exact substring of the page, after one form of line end. Each quote has 20 characters or more. The checker uses its own test, not the one of the route |
| `cap` | A case keeps at most 12 proposals, less the proposals that wait on the page version |
| `no_tools` | No request holds `tools`, `tool_choice`, `functions`, `function_call` or `parallel_tool_calls` |
| `framed` | The user message is one block of the untrusted frame. The system message holds the note of that frame |
| `one_call` | Each case makes one request |
| `expected` | A scripted run only. The counts equal the counts of the case |

## How to run it

The scripted run calls no model and costs nothing:

```bash
uv run python -m evals.project_rules_proposer.run --scripted
```

The live run calls the model through the Router, and the Router must be on.
It costs credits, so the owner starts it:

```bash
uv run python -m evals.project_rules_proposer.run --live \
  --page project-docs/specs/project_alignment.md \
  --member <lead address> --out <file> --budget-usd 0.50
```

The runner estimates the cost of each call before it sends it. It stops
before a call that could pass the budget. A budget over USD 0.50 needs
`--owner-approved`. Each call has its own `client_ref`. A refusal or an error
stops the run, and the file still records the spend so far.

The live run must measure the time of each call against `CALL_TIMEOUT_S`
(30 seconds), because an answer of up to 6,000 tokens can take longer. Each
row of the file records its `seconds`.

A lead then marks each proposal in the file as `"accepted": true` or `false`.
Then run:

```bash
uv run python -m evals.project_rules_proposer.run --score <file>
```

The score is the share of proposals that the lead accepted. The bar is one
half. The share is a report, not a test, and it counts no person (ALN-Q5).
