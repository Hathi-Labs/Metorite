# The mapping eval (WS-53 CRM-Q20-1)

This folder holds the eval set of the CRM mapping pipeline. The owning spec
is `project-docs/specs/crm_platform.md`, §18.9.6, CRM-Q20 item 4.

**Status.** Built on 2026-10-16. The scripted run passes each bar. The model
sweep is not built yet.

## What it proves

The pipeline answers what code can first, and asks the decide tier about the
rest. It asks which module plays an empty core slot, and which field fills
each target. It also asks which stage value is won, lost or open. The eval
runs the real `Pipeline` of
`apps/services/gateway/gateway/crm_sync/map_pipeline.py` on synthetic
schemas. The real `detail_module` and `detail_slot` read the synthetic
records, so step 1 is the step of the sync. A scripted asker answers each
question, and the checkers score the items.

## The files

| File | What it holds |
|---|---|
| `schemas.json` | 3 synthetic schemas, with the expected role, targets and stage of each |
| `scripted_answers.json` | The answers that `--scripted` replays, for each schema and mode |
| `dataset.py` | The loader. It makes the records of each module with a fixed rule |
| `checkers.py` | The score of a run, and its seven bars |
| `run.py` | The runner. Only `--scripted` works in this slice |
| `__main__.py` | So that `python -m evals.crm_mapping` runs it |

An agent invented each module, field and value. Every name is made up.

## The schemas

- `dewin`: the shape of DEWiN after its import. It has 17 generic modules,
  and the org turned Accounts and Deals off in Zoho. Commercials, Quotes and
  Sales Orders hold 0 records. One module has 127 fields. The run also goes in `shadow`.
- `opportunities`: a module named like the Deals slot, so code answers the
  role. Code maps Won and Lost, and the decide tier maps the other stages.
- `commercials`: Commercials plays Deals, and the decide tier answers the
  role. Code finds the stage field by its name.

## The scripted answers

Nobody recorded `scripted_answers.json` from a model. An agent wrote the
answers by hand. An entry names its pick by its name in the state. Some
entries hold a probability under 0.75, so the run must re-ask them. A
question with no entry gets a sure `none`.

## The bars

| Bar | What passes |
|---|---|
| `roles` | Each empty slot gets the expected module, or no role |
| `targets` | Each module that plays a slot gets the expected field for each target, and no other target item |
| `stages` | The stage field and the map of its values are as expected |
| `shape` | Each request holds 16 questions or fewer. Each option names an object of its state. A first round holds no sample |
| `no_value` | No state holds a string that looks like an email address or a phone number |
| `cost` | The run keeps under the token cap and the request cap |
| `cache` | A second run on the same schema makes no decide call, and gives the same items |

The `dewin` run in `shadow` must write no item, ask no role question, and
give the agreement counts that the scripted answers hold.

## The cost that it reports

Each schema reports its requests, its questions and its estimated input
tokens. For `dewin` in `on`, the first setup is 3 requests, 7 questions and
about 2,700 tokens. In `shadow` it is 2 requests and about 900 tokens. A
measured count needs the post-flip probe of §18.9.6.

## How to run it

```bash
uv run python -m evals.crm_mapping --scripted
```

Exit code 0 means that each bar passed. Exit code 1 means that a bar failed.
Exit code 2 means that the run cannot start.

`tests/unit/test_crm_map_pipeline.py::TestTheEval` runs the scripted set, and
it proves that a wrong answer fails a bar.
