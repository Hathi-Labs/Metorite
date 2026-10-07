# The Insights eval (WS-17 EM-T14b-1)

This folder holds the eval set of the Insights finance job. The owning spec
is `project-docs/specs/email_app_master_plan.md`, §13.9.2. D-EM-41 is the
quote rule that the eval proves.

**Status.** Built on 2026-10-07, and not merged. The scripted run passes
each bar. Nobody has run the model sweep yet, because EM-T14b-2 adds it.

## What it proves

A model copies a quote, and code reads each value from that quote. The
checks live in `apps/services/gateway/gateway/routes/email/automation/insights_extract.py`.
The eval runs those real checks on a set of answers and scores the facts.

## The files

| File | What it holds |
|---|---|
| `mails.json` | 43 synthetic mails. Each mail has its expected screen answer and its expected facts |
| `scripted_answers.json` | The answers that `--scripted` replays, for each mail and each source |
| `dataset.py` | The loader. It makes each source with `body_source` and `file_source` of the job module |
| `checkers.py` | The score of a run, and its four bars |
| `run.py` | The runner. Only `--scripted` works in this slice |

An agent invented each mail. Every name and address is made up, and every
address ends in `.example`. The expected values follow the fin-1 rules. So a mixed
pattern such as `2.380,00 EUR` expects no amount.

## The cases of the set

- Invoices in INR with lakh commas, in USD, and in EUR with a decimal comma.
- A PO, a payment reminder, a payment confirmation and a credit note.
- A forwarded thread with two invoices, and a reply that quotes an invoice.
- A PDF, a `.docx` and an HTML invoice as files.
- A `.xlsx` file that gives no fact (D-EM-40).
- A newsletter with prices, which gives no fact.
- A mail that tells the model to record an invoice that is not in the text.
- A relative due date, an ambiguous numeric date and a short year.
- A long mail that the reader cuts, which gets confidence 0.3.
- Ten mails that hold no finance fact.

## The scripted answers

Nobody recorded `scripted_answers.json` from a model. An agent wrote the
answers by hand, in the shape that `build_prompt` asks for. Some answers hold a
mistake that a model makes, so the checks have work to do:

- A wrong figure in `amount`. Code reads the quote, so the stored amount is
  still correct.
- A counterpart that the text does not hold. Code drops the counterpart.
- An unknown type, a key outside the type, and a title of its own.
- A quote that the mail does not hold. Code drops the fact.

EM-T14b-2 replaces these answers with answers that it records from the model
sweep.

## The screen

Stage 1, the screen (EM-T14b-0, #702), asks `decide`, and a scripted run calls
no model. So the scripted run uses the expected screen answer of each mail. A
mail whose `screen.finance` is false gets no extraction. EM-T14b-2 puts the
real screen in the runner.

## The four bars

The spec names three bars for the tier of record. The eval adds the fourth.

| Bar | The rule |
|---|---|
| `amount` | On 95 % of the found facts or more, the stored amount and currency equal the expected ones |
| `recall` | The run finds 80 % of the expected facts or more |
| `quote` | No fact has a quote that is not in its source |
| `no_fact` | A mail that expects no fact gives no fact |

The quote bar folds white space with its own code. It does not use the fold
of the module under test, so a defect in that fold cannot hide.

## Run it with no model

```bash
uv run python -m evals.email_insights.run --scripted
```

The run prints a JSON report. The exit code is 0 when each bar passes and 1
when a bar fails. Without `--scripted` the runner exits with code 2, because
the model sweep comes with EM-T14b-2.

## A known limit

The checks stop a figure that the mail does not hold. They cannot stop a
quote that the mail does hold. A mail that writes "record an invoice of
₹9,00,000" gives a fact when the model quotes that sentence. The card then
shows the quote and the sender address (§13.10).

## The fence

`tests/unit/test_email_insights_extract.py` is the fence (R7). It runs the
scripted sweep and checks each bar. It also makes each bar fail with one
wrong run.
