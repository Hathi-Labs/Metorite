# The note-labels eval (WS-53 CRM-Q26)

This folder holds the eval set of the CRM note labels. The owning spec is
`project-docs/specs/crm_platform.md`, §18.11.2, CRM-Q26.

**Status.** Built on 2026-10-16. The scripted run passes each bar. The model
sweep is not built yet.

## What it proves

The label step asks the decide tier three questions about each note: the
kind, "needs action" and the urgency. Code builds each option, and code reads
each answer. The eval runs the real `pack` and the real `reader` of
`apps/services/gateway/gateway/crm_sync/note_labels.py` on a set of answers,
and it scores the rows.

## The files

| File | What it holds |
|---|---|
| `notes.json` | 18 synthetic notes. Each note has its expected kind, action and urgency |
| `scripted_answers.json` | The answers that `--scripted` replays, for each note |
| `dataset.py` | The loader |
| `checkers.py` | The score of a run, and its five bars |
| `run.py` | The runner. Only `--scripted` works in this slice |

An agent invented each note. Every name, place and address is made up.

## The cases of the set

- Each of the six kinds, and each of the three levels of urgency.
- Notes that need action, and notes that only record work that is done.
- A note whose text tells the model to label it as a payment of high urgency.
- A long note. The state cuts its body to 2,000 characters.
- A note with no subject, and a note in Hindi and English.

## The scripted answers

Nobody recorded `scripted_answers.json` from a model. An agent wrote the
answers by hand, in the shape of the typed answers of `acb_llm`. Some answers
hold a quirk that a vendor answer can hold, so the reader has work to do:

- A probability key that is not one of our keys. The reader drops it.
- A score with no level. The reader takes the level from the position.
- A probability above 1. The reader clamps it.

## The bars

| Bar | Model sweep | Scripted run |
|---|---|---|
| `kind` | 0.80 or more | 1.0 |
| `action` | 0.85 or more | 1.0 |
| `urgency` | 0.70 or more | 1.0 |
| `shape` | Each request holds 5 notes and 16 questions or fewer. Each body is 2,000 characters or fewer. Each question names a note of its state | The same |
| `no_text` | Each label column holds one of our keys, a number or NULL | The same |

## How to run it

```bash
uv run python -m evals.crm_note_labels.run --scripted
```

Exit code 0 means that each bar passed. Exit code 1 means that a bar failed.
Exit code 2 means that the run cannot start.

`tests/unit/test_crm_note_labels.py::TestTheEval` runs the scripted set, and
it proves that each bar fails on a run that breaks it.
