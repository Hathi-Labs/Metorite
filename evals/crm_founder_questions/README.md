# The founder question set (WS-53 CRM-Q30)

This folder holds an eval of the assistant over CRM data. A founder asks a
question in the chat, and the assistant must answer with the right numbers.
The owning spec is `project-docs/specs/crm_platform.md`, §18.11.2, CRM-Q30.

**Status.** Built on 2026-10-17. The scripted run passes each bar, and the
R8 test shows that each fact agrees with the real route. Nobody has run the
live mode yet.

## What it proves

This eval proves three facts.

1. The assistant picks the right tool and the right arguments.
2. It combines modules correctly, for example invoices and payments by
   client.
3. Its answer holds the right numbers, and it does not invent a number for
   an empty field.

The numbers come from SQL (§18.11.1). So the set also proves that its own
ground truth and the route give the same numbers.

## The files

| File | What it holds |
|---|---|
| `dataset.py` | The synthetic CRM. A seeded script makes it, so each build gives the same rows |
| `questions.py` | The 31 questions. Each has its category, the calls that it needs and its facts. Plain Python computes each fact |
| `scripted_answers.json` | The tool calls and the answer that `--scripted` replays. An answer holds placeholders, not numbers |
| `recorded_routes.json` | The answers of the real routes on the seeded dataset. The R8 test records them |
| `checkers.py` | The seven bars and the score of a run |
| `numbers.py` | The number check of an answer. It is local until CRM-Q29 merges |
| `seed.py` | The dataset as SQL statements for one org. It opens no connection |
| `run.py` | The runner: `--scripted`, `--live` and `--seed` |

An agent invented each name, place and number. No row comes from a real
customer. The owners use the domain `founder-evals.test`.

## The dataset

The dataset has the shape of DEWiN after its import. It holds about 800 rows.

- Custom modules: Company, Clients, Operator_Associates, Invoices,
  Payment_Received, Payment_Made and Commercials. Commercials has fields and
  no record.
- Lookups: a client names its company, and an invoice names its client. A
  payment received names its invoice and its client. A payment made names
  its operator associate.
- Values in the shapes of Zoho: a currency in INR, sometimes as text. A
  `date` is `YYYY-MM-DD`. A `datetime` has the offset `+05:30`, and one in 6
  is in UTC, with `Z`.
- Empty fields: `Annual_Revenue` is empty in each Company row. `Discount`,
  `TDS_Deducted` and some text fields are empty in some rows.
- The core Contacts carry the label "Existing Sites" in the profile.
- Notes hang off Clients and Invoices records.
- The profile names the zone `Asia/Kolkata`. Some records were made just
  after midnight on the first day of a month. In UTC, that time is in the
  month before.
- "Today" is 15 October 2026 (`AS_OF`). No invoice falls due on that day.

## The questions

| Category | Questions | What they need |
|---|---|---|
| `total` | Q01, Q06, Q07, Q08, Q09, Q24, Q29 | A sum, an average, a minimum, a maximum or a count over a date range |
| `trend_month` | Q02 | A sum by month of a `date` field |
| `trend_quarter` | Q03 | A sum by calendar quarter |
| `zone` | Q04, Q05 | A month of a `datetime` field, and the created month, in the CRM zone |
| `top_n` | Q11, Q12, Q13 | A sum by a lookup, largest first |
| `overdue` | Q14, Q15, Q16, Q17, Q18 | The balance and the status of invoices, and a due date before today |
| `cross_module` | Q19 | Invoices and payments received, by client |
| `two_calls` | Q20, Q21 | Two aggregate calls on two modules |
| `empty_field` | Q22, Q23 | An empty field and an empty module. The answer says that Zoho holds no value |
| `relabel` | Q25, Q26 | The core Contacts, with the label "Existing Sites" |
| `picklist` | Q10, Q27, Q28 | A group by a picklist or a boolean |
| `note_search` | Q30 | `crm_search_notes` (CRM-Q27). The R8 test does not run it, because it needs the embed door |
| `short_read` | Q31 | The count of Payment Made. The tool output must hold the short-read line of CRM-Q30b, and the answer must give both counts of that line |

## The bars

| Bar | What passes |
|---|---|
| `tools` | Each call that the question needs has a GET request that matches one of its alternatives |
| `tool_text` | The output of a tool holds each line that the question needs. Q31 needs the short-read line (CRM-Q30b) |
| `numbers` | The answer holds each number fact, in a written form that is not coarser than 0.5 percent |
| `names` | The answer holds each name fact, and the names of a ranked list in rank order |
| `flags` | The answer holds one of the flag phrases of the question |
| `forbid` | The answer holds no forbidden number, for example the Zoho count, and no "INR 0" for an empty field |
| `agree` | Each fact equals the route answer that it names. Only the scripted run scores it |

A scripted run must pass each bar of each question. A live run passes when
80 percent of the questions pass each bar (`LIVE_BAR`).

## How to run it

The scripted run needs no model, no network and no database:

```bash
uv run python -m evals.crm_founder_questions --scripted
```

Exit code 0 means that each bar passed. Exit code 1 means that a bar failed.
Exit code 2 means that the run cannot start.

`tests/unit/test_crm_founder_questions.py` runs the scripted set. Its
mutations prove that each of these fails a bar:

- A tool with no short-read line (Q31 fails on `tool_text`).
- A wrong measure, a missing filter or a missing second call.
- A wrong number, a rounded total or a wrong order.
- A missing flag or a zero for an empty field.
- A route answer or a short read that disagrees with a fact.

## How to record the routes again

Do this after a change to `dataset.py`, `questions.py` or the calls of
`scripted_answers.json`. Use a private database, never the shared
`acb_tenant`.

1. Start a private copy of `scripts/dev_db.sh`, with its own container names
   and ports.
2. Set `TENANT_LADDER_DATABASE_URL` to that database.
3. Run `CRM_FOUNDER_RECORD=1 uv run pytest tests/unit/test_crm_founder_questions_r8.py`.
4. Run the same test again without `CRM_FOUNDER_RECORD`. It must pass with
   no skip.
5. Remove the private containers.

## How to run `--live`

The live run asks the real assistant. It costs credits, so a person runs it
on a local stack. CI never runs it.

1. Start the local stack with the Router (`docs/` and the memory note
   "local dev stack recipe").
2. Make an empty org with one member. Put the org in `CRM_ORGS`, and put it
   in `CRM_FILL_RANK_ORGS` so the chat reads the label "Existing Sites". Put
   it in `CRM_MIRROR_ORGS`, so the tools can read the short reads.
3. Seed the org:

   ```bash
   uv run python -m evals.crm_founder_questions --seed --org <org uuid>
   ```

   The seed refuses a database that is not on this machine. It also refuses
   an org that holds CRM module records.
4. Run the questions:

   ```bash
   GATEWAY_URL=http://localhost:8080 uv run python -m evals.crm_founder_questions \
       --live --org <org uuid> --member <member email> --tier tier-balanced
   ```

   Add `--questions Q01,Q19` to run some of them.
5. Read `live_results.json`. Each row holds the requests of the tools, the
   answer and the problems of each bar.

The member talks to `orchestrator`, as the chat does (D99). The real
specialists run, and each CRM tool reads the real gateway. Q30 needs
`CRM_NOTE_SEARCH_ORGS` and the embed door. Without them the tool says that
the search is off, and Q30 fails.

## Known limits

- The list tools and the aggregate read `GET /crm/sync/short-reads` once in
  each call (CRM-Q30b). The scripted run replays its recorded answer, and the
  agreement bar does not count it as a call of the question.
- The scripted answer of the note search is synthetic. It holds the notes
  that hold each word of the query.
- `numbers.py` is local. When CRM-Q29 merges, `checkers.py` must call the
  shared number check, and this file goes.
