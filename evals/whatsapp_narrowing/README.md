# The WhatsApp narrowing eval (WS-48 N4)

This folder holds the eval of WS-48 N4. It asks five questions of
whatsapp-assistant, two ways, on one set of synthetic chats. It records the
tokens and the credits on each tier, and it checks that no answering message
is lost. The owning spec is `project-docs/specs/data_narrowing_pipeline.md`,
§7.2 and slice N4 of §9. It follows `evals/email_narrowing/`.

**Status.** Built on 2026-10-08. The scripted run passes. Nobody has run
`--compare`, because the live run needs owner approval.

⚠️ **This eval does not show a saving of the email size.** The email eval
gates at a ratio of 0.40. Here the gated ratio is 0.650, OVER that bar. With
every chat read in ONE request, the best case for today, the ratio is 0.918.
On Q2 the new path costs 1.45 times today's path.

So the bar here is 1.00. On Q1 to Q4, the new path must cost no more than
today's path, and it must find every answer. The run prints the email bar and
each question that costs more under its first line.

## Why WhatsApp saves less than email

A WhatsApp message is short. The mean text of the fixture is 58 characters,
about 15 tokens. PICK asks one question for each candidate, and each question
carries its guidance, so PICK spends about 160 tokens on each candidate. That
is ten times the message. So PICK costs more than it saves on these messages.

The saving that remains comes from fewer requests. Today's path asks one
search for each search word, then one request for each five chats it reads.
The new path asks one request, then the answer.

The weak case is a question on the filters only, such as Q2: all messages of
one group in three weeks. Today's path reads the group in one request, and
the new path pays PICK for 27 short messages.

## What it runs

Each question runs two ways:

- **Before.** Today's whatsapp-assistant, with no `narrow_and_read`. It calls
  `search_whatsapp` one time for each search word, because the route finds a
  message only when it holds ALL the words of one search. Then it reads each
  chat that the searches list with `read_whatsapp_chat`, and it writes the
  answer. For Q2 it finds the group with `list_whatsapp_chats`, and reads it.
- **After.** The same agent with `NARROWING_AGENTS=whatsapp-assistant`. It
  calls `narrow_and_read` one time, and writes the answer.

Both paths call the real tools of `apps/agents/agent-whatsapp-assistant`
through the agent's own `_request`, against a gateway stub on `127.0.0.1`. The
after path runs the real pipeline of `acb_skills/narrowing.py`, with the real
decide facade and the real Console client. Only the transport under that
client is a stub.

A third run of the before path reads every chat in ONE request. That run is
the best case for today's path. The eval reports its ratio, and does not gate
on it.

## The files

| File | What it holds |
|---|---|
| `run.py` | The runner, the rules and the pass rule |
| `dataset.py` | The generator of the chats, the five questions, and the stub verdicts |
| `fixtures/chats.json` | The output of `dataset.generate()`. A test fails when the two differ |
| `stub_api.py` | The gateway stub of the three WhatsApp routes, and the stub decide door |
| `scripted.py` | The tool sequence of each path, played one model request at a time |

The Router counters, the eval card of credits and the model session are the
email eval's own, so the two evals price one way.

## The chats

The member holds 21 chats with one person and 6 groups, with 410 messages over
60 days. The generator invents every name and text. No message holds a phone
number, and each WhatsApp id starts with `test-`. Voice notes have an empty
body and a transcript.

Another member of the same org holds three chats with the SAME names as chats
of the member. One of them is the group "Dealers North". Their 12 messages
match every question. So a leak across members shows as a failed rule, also
through the `contact` filter.

To change the chats, change `dataset.py`, then run:

```bash
uv run python -m evals.whatsapp_narrowing.dataset --write
```

## The questions

| Id | The question | Filters | Answers | Gated |
|---|---|---|---|---|
| Q1 | Which dealers asked for the price of the X200 pump in the last 30 days? | `from_me: false`, `after` | 8. One says "quote", one "kitna", one is a voice note | Yes |
| Q2 | Summarise what the Dealers North group said about the October stock in the last 3 weeks | `contact`, `group: true`, `after`, `words: ""` | 7 | Yes |
| Q3 | Which customers sent a photo, a video or a file of a damaged delivery? | `has_media: true`, `from_me: false` | 5. One is a voice note | Yes |
| Q4 | What did I promise to send to customers this week? | `from_me: true`, `after` | 5. One is a voice note in Hinglish | Yes |
| Q5 | Which customers were angry about late service? | `from_me: false` | 5. Two share no word with any search | No, the expected miss |

## The pass rule

1. **Recall.** For Q1 to Q4, the after path reads in full every answering
   message.
2. **Cost.** On Q1 to Q4, the after path's credits are at most 1.00 times the
   before path's credits. This is NOT the email bar (see the top).
3. **The rules.** These bind every question:
   - Each request acts as the member.
   - No message of the other member reaches a response.
   - Each query parameter is a real parameter of its route. The runner reads
     each route's own signature.
   - Each NARROW call sends `hybrid=true`, `websearch=true` and `limit=201`: 200
     candidates and one probe row that says more matched.
   - READ reads only messages that NARROW found, and at most 25.
   - READ changes no state. Every request of both paths is a GET, and each
     READ is the thread route with `around` and the adapter's window.
   - Each PICK item holds one message, and no message past its clip.

## Run it with no model

```bash
uv run python -m evals.whatsapp_narrowing.run --scripted
uv run python -m evals.whatsapp_narrowing.run --scripted --out <dir>
```

The scripted run calls no model and no Router. The `skill-eval.yml` job runs
it. The unit job runs `tests/unit/test_whatsapp_narrowing_eval.py`, which
breaks each side of the pass rule and checks that the eval fails.

## The result of 2026-10-08

The scripted run, with 5 chat reads in one request on the before path. Every
number is a stub estimate.

| Q | Before: found and read | Before recall | After: found and read | After recall | Ratio | Ratio, best case for today |
|---|---|---|---|---|---|---|
| Q1 | 155 and 155 | 1.0 | 14 and 9 | 1.0 | 0.44 | 0.78 |
| Q2 | 57 and 57 | 1.0 | 27 and 8 | 1.0 | 1.45 | 1.45 |
| Q3 | 102 and 102 | 1.0 | 7 and 6 | 1.0 | 0.60 | 0.81 |
| Q4 | 117 and 117 | 1.0 | 7 and 5 | 1.0 | 0.57 | 0.77 |
| Q5 | 67 and 67 | 0.6, expected | 5 and 3 | 0.6, expected | 0.79 | 0.79 |

On Q1 to Q4 the ratio is **0.650**, under the bar of 1.00 and over the email
bar of 0.40. In the best case for today it is 0.918. The break-even factor of
`tier-powerful` is 2.15, and of `tier-decide` 3.83. With a card where
`tier-powerful` costs more than 2.15 times the eval card, the after path costs
more than today's path.

### What the numbers say, and what they do not

- **Every number of the scripted run is an estimate.** The tokens are 4
  characters each. The credits use the eval card. The PICK verdicts come from
  the fixture. The full-text match is an approximation of
  `to_tsvector('simple')`.
- **The before path is an assumption.** Nobody measured what today's model
  asks for. The gated number uses one search for each word and 5 chat reads
  in one request.
- **Today's recall is 1.0 here because the fixture chats are short.** Each
  chat of the fixture holds 8 to 17 messages, so `read_whatsapp_chat`, which
  reads the OLDEST 20 messages, reads all of them. A real chat over 60 days
  holds more. Then today's path misses the recent answers, and its cost stays
  the same. The eval does not model that, so it is kind to today's path.
- **Prompt caching is not modelled.** A cached prompt costs less, and that
  cuts the saving of fewer requests.
- **The prompt leaves out the platform tools and the addendum.** Each request
  of both paths lacks the same part, so both totals are low.

## A live run (`--compare`)

`--compare` sends PICK to the real decide door of the Console that
`CUSTOMER_CONSOLE_URL` names. The runner refuses an address that is not on
this machine, and it refuses when `DECIDE_ENABLED` is off. It exits with code
2. A run on the production Router spends credits. It is an owner gate.
