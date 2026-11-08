# The image-need eval of the one assistant (WS-VOX VOX-5)

This folder holds the image-need eval. A member attaches an image in the
chat. The one assistant has the tool `analyze_image`. Each call sends the
image to a model and draws AI credits. This eval asks: does the assistant call
the tool only when the answer needs what the image shows? The owning spec is
`project-docs/specs/projects_ai_chat.md`, §31.5 VOX-5.

**Status.** Built on 2026-11-09. The scripted run passes. Nobody has run the
live sweep, so there is no baseline yet.

## The two outcomes

| Outcome | What the assistant does | Example words |
|---|---|---|
| `call` | It calls `analyze_image` exactly once. `file_name` names the attached file | "Read the invoice number" |
| `no_call` | It calls `analyze_image` zero times. A tool moves the file by its name | "Attach this to task X" |

The set holds 12 cases, 6 of each outcome. `dataset.py` lists them.

## What it checks

The scripted run calls no model and no network, and CI runs it. It checks
these things against real code:

1. **The reply.** A scripted model gives the right answer for each case. The
   scorer reads the tool calls of the reply. It counts the calls of
   `analyze_image` and reads the `file_name` argument.
2. **The request.** The first model request must offer `analyze_image`. The
   tool description must hold `IMAGE_CALL_RULE`. The message must hold the
   member's words and the upload note.
3. **The set.** Both outcomes have six cases, and the ids are unique. The port
   of the upload note must match `chatUpload.ts`.

⚠️ **A scripted run proves the path, never the choice.** The script makes the
choice. Only the live run measures it.

## What it reuses, and what it adds

`harness.py` subclasses `evals/routing/harness.py`. So it runs the real
`run_agent_stream("orchestrator")`, with the real prompt and the real tool
list. It writes no second prompt.

It adds three things:

- `IMAGE_ANALYSIS_ORGS` is on for the eval org. With the flag off, the tool
  is absent and the run fails.
- The upload note. The chat adds it to the typed words when a member attaches
  a file. `harness.upload_note` ports `uploadNote` of `chatUpload.ts`, and
  `harness.drift` fails when the TypeScript changes.
- A stand-in for the body of `analyze_image`. It keeps the real name,
  docstring and signature, so the model sees the real schema. It sends no
  image, so no run draws credits.

The assistant reads the rule of the tool in its description, because the one
assistant gets no addendum for this tool.

## How to run it

```bash
uv run python -m evals.image_need.run --scripted
uv run python -m evals.image_need.run --live --budget-usd 0.50
```

`--live` is manual. It asks the real model through the Router. The default
tier is `tier-balanced`, the chat tier. It needs a local stack with a Router
key. `--tier` picks another tier, and `--cases N1,C2` picks cases.

`--budget-usd` caps the spend. The default is 0.50. The run stops before a
case that could pass the cap. It prints the cost and the score of each case,
and it writes `baseline.json`.

⚠️ A live run spends money. The owner must approve a run above USD 0.50.
Each live case sends about 21,000 input tokens. A `call` case sends two
requests, and a `no_call` case sends one. A live run answers a confirmation
card with a refusal, so no write happens.

## The files

| File | What it holds |
|---|---|
| `dataset.py` | 12 cases. Each has an id, the attached file name, the words and the outcome |
| `harness.py` | The harness with the image flag, the upload note, the stand-in and the scorer |
| `run.py` | The runner: the scripted run and the live run |

The unit suite `tests/unit/test_image_need_eval.py` proves that each check
can fail. It runs a scorer that ignores the call count, a script that calls
twice, and a harness with the flag off.
