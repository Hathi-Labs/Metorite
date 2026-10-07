# The email narrowing eval (WS-48 N2)

This folder holds the eval of WS-48 N2. It asks five questions of
email-assistant, two ways, on one synthetic mailbox. It records the tokens and
the credits on each tier, and it checks that no answering message is lost.
The owning spec is `project-docs/specs/data_narrowing_pipeline.md`, §7.2 and
slice N2 of §9.

**Status.** Built on 2026-10-07. The scripted run passes. Nobody has run
`--compare`, because no box has a bound `tier-decide` yet (HANDOFF H-267).

⚠️ **Do not read the gated ratio alone.** The gated ratio is 0.335, under the
bar of 0.40. It rests on an assumption: today's path reads 5 emails in one
request. With every read in ONE request, the best case for today, the ratio
is 0.696, OVER the bar. If `tier-powerful` costs 1.39 times the eval card or
more, the saving is gone. The run prints both weak cases under its first line.

## What it runs

Each question runs two ways:

- **Before.** Today's email-assistant, with no `narrow_and_read`. It lists the
  mail with `query_inbox`, reads each listed email with `read_email`, and
  writes the answer.
- **After.** The same agent with `NARROWING_AGENTS=email-assistant`. It calls
  `narrow_and_read` one time, and writes the answer.

Both paths call the real tools of `apps/agents/agent-email-assistant`
through the agent's own `_request`, against a gateway stub on `127.0.0.1`. The
after path runs the real pipeline of `acb_skills/narrowing.py`, with the real
decide facade and the real Console client. Only the transport under that
client is a stub.

A third run of the before path reads every listed email in ONE request. That
run is the best case for today's path. The eval reports its ratio, and does
not gate on it.

## The files

| File | What it holds |
|---|---|
| `run.py` | The runner, the rules and the pass rule |
| `dataset.py` | The generator of the mailbox, the five questions, and the stub verdicts |
| `fixtures/mailbox.json` | The output of `dataset.generate()`. A test fails when the two differ |
| `fixtures/rate_card.json` | An eval card of credits for each tier. It is not the production card |
| `stub_api.py` | The gateway stub, the stub decide door and the stub Router counters |
| `scripted.py` | The tool sequence of each path, played one model request at a time |

## The mailbox

The mailbox holds 300 messages of one member, from 40 senders, over 60 days.
The generator invents every name and address, and every domain ends in `.test`. It also
holds 12 messages of another member of the same org. They match every
question, so a leak across members shows as a failed rule.

To change the mailbox, change `dataset.py`, then run:

```bash
uv run python -m evals.email_narrowing.dataset --write
```

## The questions

| Id | The question | Answers | Gated |
|---|---|---|---|
| Q1 | Which customers asked about pricing in the last 30 days? | 11. Four of them say "quote" or "rates", not "pricing" | Yes |
| Q2 | Summarise everything from Acme about the Q4 order in the last 7 weeks | 8 | Yes |
| Q3 | Which suppliers told us that a delivery will be late? | 6 | Yes |
| Q4 | Which emails with an attachment ask me to sign a contract or an agreement? | 5 | Yes |
| Q5 | Which customers complained about our service or support? | 5. Two of them share no word with any search | No, the expected miss |

**Q5 measures the gap of spec Q3.** The search is lexical, and `hybrid=true`
only re-orders the full-text matches. Two answers of Q5 hold no word of the
search, so no path finds them. The eval reports a recall of 0.6 for Q5, and
marks it `xfail`. If a later change finds them, the eval marks it `xpass`.

## The pass rule

1. **Recall.** For Q1 to Q4, the after path reads in full every answering
   message. So PICK dropped no answer, and READ left no answer unread.
2. **Cost.** On Q1 to Q4, the after path's credits are at most 40 percent of
   the before path's credits.

Six rules bind every question too:

- Each request acts as the member.
- No message of the other member reaches a response.
- Each search parameter is a real parameter of the route. The runner reads
  the route's own signature.
- Each NARROW call sends `light=true` and `hybrid=true`.
- READ reads only messages that NARROW found, and at most 25.
- READ sends `mark_read=false`, so it changes no read state.
- No PICK request holds a body past its snippet.

## Run it with no model

```bash
uv run python -m evals.email_narrowing.run --scripted
uv run python -m evals.email_narrowing.run --scripted --out <dir>
```

The scripted run calls no model and no Router. The `skill-eval.yml` job runs
it. The unit job runs `tests/unit/test_email_narrowing_eval.py`, which breaks
each side of the pass rule and checks that the eval fails.

## The result of 2026-10-07

The scripted run, with 5 reads in one request on the before path:

| Q | Before: listed and read | After: found and read | Recall | Ratio | Ratio, best case for today |
|---|---|---|---|---|---|
| Q1 | 24 and 24 | 24 and 13 | 1.0 | 0.29 | 0.70 |
| Q2 | 29 and 29 | 30 and 11 | 1.0 | 0.25 | 0.68 |
| Q3 | 14 and 14 | 14 and 8 | 1.0 | 0.42 | 0.70 |
| Q4 | 8 and 8 | 8 and 6 | 1.0 | 0.53 | 0.71 |
| Q5 | 11 and 11 | 11 and 3 | 0.6, expected | 0.41 | 0.70 |

On Q1 to Q4 the ratio is **0.335**, under the bar of 0.40. In the best case
for today it is 0.696, over the bar. The break-even factor of `tier-powerful`
is 1.39. With a card where `tier-powerful` costs more than 1.39 times the eval
card, the after path is over the bar.

### What the numbers say, and what they do not

- **Every number of the scripted run is an estimate.** The tokens are 4
  characters each. The credits use the eval card. The PICK verdicts come from
  the fixture. The full-text match is an approximation of `to_tsvector`.
- **The turns carry most of the saving.** A request carries the agent's
  instructions and its 45 tool schemas, about 14,000 tokens, each time. The
  after path makes 2 requests, and the before path makes 3 or more. When the
  stub door keeps every message, the ratio is 0.35. So PICK alone saves about
  2 points on this mailbox. The mean body is 789 characters. Longer mail makes
  PICK save more.
- **The before path is an assumption.** Nobody measured how many reads one
  request of today's model asks for. The gated number uses 5. With all reads
  in one request, the after path is over the bar.
- **Prompt caching is not modelled.** A cached prompt costs less, and that
  cuts the saving of fewer requests.
- **The prompt leaves out the platform tools and the addendum.** Each request
  of both paths lacks the same part, so both totals are low.

## A live run (`--compare`)

`--compare` sends PICK to the real decide door of the Console that
`CUSTOMER_CONSOLE_URL` names. The runner refuses an address that is not on
this machine, and it refuses when `DECIDE_ENABLED` is off. It exits with code 2.

A live run measures the PICK verdicts, so its recall is real. It prints each
decide `request_id`. Join those ids to `usage_event` on the Console to read the
real credits of PICK. The model turns are still played from the sequences, so
their tokens are still estimates.

What a live run needs:

1. A dev box with the Router, `tier-decide` bound, and `DECIDE_ENABLED` on.
2. A priced card for `tier-decide`, `tier-balanced` and `tier-powerful`.
3. For real model turns, a sweep through the executor with a real model, as
   `evals/projects_ops` does. This eval does not build that sweep yet.

A run on the production Router spends credits. It is an owner gate.
