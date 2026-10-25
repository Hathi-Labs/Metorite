# The OA-5 measure: an email tool pack against delegation (WS-67)

This folder compares two shapes of the one assistant on the same email
chats. The owning spec is `project-docs/specs/one_assistant_context.md`,
§4 and §5, slice OA-5 (D116). The report of the first live run is
`docs/one_assistant_measure_2026-10-25.md`.

**Status.** Built on 2026-10-25. The scripted run passes on both arms. The
live run of 2026-10-25 is in `results_2026-10-25.json`.

## The two arms

| Arm | What the orchestrator holds |
|---|---|
| `delegation` | Today's code. The orchestrator gives each email task to the real email assistant, which runs as a sub-agent. |
| `pack` | An eval fixture (`pack.py`). The orchestrator holds the email assistant's own tools and a short rules block, and it has no email delegation. |

The pack is an eval fixture, and no production code changes. `pack.py`
changes the agents that the real factory built, through the
`after_build` hook of the routing harness. It does these five things:

1. It takes away the `email_assistant` tool.
2. It adds the email assistant's own tools, kept to `own_tool_scope`.
3. It removes each persona bullet that names `email_assistant`, and it adds
   `RULES`.
4. It refuses each delegation to the email assistant, and the check
   `no_email_delegation` fails that turn.
5. It records each email tool call in the chat's working set, through the
   executor's own two functions.

## What it reuses

The measure uses ONE harness (H-412): `evals/routing/harness.py`. The
harness drives the real orchestrator run, the real email factory, the real
delegation and the real cards. The email gateway is the only fake.
`mailbox.py` extends the `Gateway` fake of
`tests/unit/test_one_assistant_email.py` with 13 synthetic mails.

The harness gives the measure three things:

- `model_calls`: each model request, with its time, its tokens and its cost.
- `after_build`: a hook that changes the built agents.
- A live mode with real specialists. Each host send that is not the Router
  fails.

## The scenarios

`scenarios.py` holds 13 chats and 18 member turns. All the data is
synthetic.

| Scenario | What it asks |
|---|---|
| `five_turns` | The five turns of `test_one_assistant_email.py`. Turn 4 names the rule, so that a live model knows what to make. |
| `navy_fresh`, `navy_replay` | The shape of the Navy-order chat (§1 of the spec). The order body names the parts, and two scanned files have names that look like parts. The replay starts from a wrong guess and the member's correction. |
| `read_invoice`, `find_priya` | Find and read one email. |
| `summarise_unread`, `needs_reply`, `count_invoices` | Questions over many emails. |
| `thread_summary` | Read a thread. |
| `reply_correction` | The member corrects a date in turn 1, and asks for the reply in turn 2. |
| `reference_file` | A readable file that is only a reference. |
| `scanned_file` | A scanned file: the name is all there is. |
| `exact_reply` | A reply with the member's exact text. |

Each turn has machine checks (`checks.py`) and a known-good plan. Four
checks read prose, and they are heuristics: `no_name_claim`,
`no_reference_subject`, `admits_unreadable` and `no_content_claim`.

## Run it with no model

```bash
uv run python -m evals.one_assistant_measure.run --scripted
uv run python -m evals.one_assistant_measure.run --scripted --scenarios five_turns
```

Each plan plays on both arms, and every check must pass. A scripted run
proves the path and the checks. It does not prove the model's choice.

## Run it live

A live run spends credits on the Router. Open the tunnel to the Console
first, and close it after the run.

1. Put `LITELLM_BASE_URL` and `LITELLM_API_KEY` in the environment or in
   `~/.metorite/secrets/eval.env`. The runner never prints the key.
2. Run the chats in chunks. Each chunk must end in less than 10 minutes:

   ```bash
   uv run python -m evals.one_assistant_measure.run --live \
     --scenarios five_turns --repeat 1 --first-repeat 1 \
     --spent-before 0 --budget-usd 5 --timeout 200 --out chunk_r1_a.json
   ```

3. Merge the chunk files, and print the tables:

   ```bash
   uv run python -m evals.one_assistant_measure.run --merge a.json,b.json \
     --out evals/one_assistant_measure/results_<date>.json
   ```

The runner checks the Router port before each chat. When the Router does
not answer, the runner stops. When a chat loses the Router, the runner
drops that chat and stops.

The cost of a streamed call is the vendor's `usage.cost`. A buffered call
has no such field, so `rate_card.json` prices it. The card reproduces each
probe of 2026-10-25 within 1 percent.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | The scripted run passed, or the live run finished |
| 1 | A scripted check failed |
| 2 | The live run cannot start |

## The fence

`tests/unit/test_one_assistant_measure.py` is the fence (R7). It runs every
plan on both arms. It makes each checker fail with one mutation. It also
proves three more facts:

- The pack holds the email tools and no delegation.
- The live mode refuses a send that is not the Router.
- The card reproduces the Router's figures.
