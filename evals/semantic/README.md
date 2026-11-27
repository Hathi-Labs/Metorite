# The scripted replay of the CRM refusal shapes (SEM-6)

This folder holds a replay with no model. It sends the query shapes that the
CRM refused on 2026-11-17 to the real `/crm` routes. It runs on a synthetic org
and checks each answer. It also asks 20 fixed questions on the same data.

The owning text is the SEM-6 row of the semantic layer design. The shapes come
from `project-docs/specs/crm_platform.md` §18.17 (CRM-Q34-1 to CRM-Q34-3).

**Status.** Built on 2026-11-17. The scripted run passes 28 of 28 on a tree that
holds CRM-Q34-1 to CRM-Q34-3. On the tree from before Rung 0, 14 fail.

## What it proves

- The routes answer each refused shape with an answer or with a refusal that
  names a step that works.
- Each check fails on a broken answer. A 422 where a count was due fails. A
  silent zero fails. A refusal that names no working step fails.
- Each expected value of the fixture equals a recomputation from its own rows.

## What it does not prove

A scripted run plays queries that are known to be good. It proves the routes and the checks.
It never proves which query a model chooses. No model runs, no vendor key is
read, and no call leaves this machine.

## The files

| File | What it holds |
|---|---|
| `fixtures/synthetic.json` | Two orgs, 21 records, 4 contacts, 20 questions and 5 cases |
| `questions.py` | The loaders, the check that the set is whole, `recompute`, and `params_of` |
| `checks.py` | The pure checks of an answer. No I/O |
| `seed.py` | The rows as SQL for one org, and `purge`. It opens no connection |
| `run.py` | The runner: `--check` and `--scripted` |

An agent invented each name, id and number. No row comes from a real customer.
The loader refuses a string that looks real: an email at a domain that is not
`.test`, `.example` or `.invalid`, or a phone number.

## The five cases

| Case | Class of 2026-11-17 | What the replay sends | The answer it needs |
|---|---|---|---|
| C1 | M3b, a number range | A range on a number field. Then a range on a text field | A count of plain numbers only, and a 422 for the text range that names `field.<key>=<value>` |
| C2 | M3a, a field of another module | `field.Grand_Total.from` on a module that has no such field | A 422 that names `module:Invoices` first |
| C3 | M4, a guessed name | A sum of `Fiber_Length_Meter_Placeholder` | A 422 that lists the real fields, nearest first |
| C4 | M2, `group_by=status` | `group_by=status` on a module with no status key. Then `group_field=Status` | A 422 that names `group_field=<key>` and the select fields. Then the groups |
| C5 | The silent zero | A lookup filter with the Metorite id of a core contact | The count of that contact's records. The id of an org B contact counts none |

M1 (`q` on a module) has no case here. Its refusal is a Rung 1 fix, and the
unit fence `test_crm_rung0_r8.py` covers it.

The tool text of CRM-Q34-3 has three more checks. They call the real
`crm_tools` functions with a stub gateway. T1: a zero says that it matched
nothing. T2: a refused total spends no total budget. T3: `status=` on a module
names `field_filters {'@status': ...}`.

## The 20 questions

The questions Q01 to Q20 cover these shapes:

- Counts and sums of three modules.
- A group by month, a date range and a number range.
- A lookup by name, and a lookup by the id of a core contact.
- A group by status.
- A zero by design.
- Two isolation questions on org B.

Each question holds its text, its query and its expected value.

## Run it

```bash
uv run python -m evals.semantic --check
```

`--check` needs no database. It loads the fixture, recomputes each value and
prints the plan.

```bash
bash scripts/dev_db.sh
export TENANT_LADDER_DATABASE_URL=<the URL of a role that row level security binds>
uv run python -m evals.semantic --scripted
```

`--scripted` seeds two fresh orgs, replays everything, prints one PASS or FAIL
line for each case, and removes the rows. It exits 0 when all pass, 1 when one
fails, and 2 when it cannot start. A superuser role skips row-level
security, so the run refuses it. Make a role with `CREATE ROLE <name> LOGIN
PASSWORD '<pw>' NOSUPERUSER NOBYPASSRLS`. Then grant it `USAGE` on schema
`public`, and `SELECT, INSERT, UPDATE, DELETE` on all tables.

## The fence

`tests/unit/test_semantic_eval_scripted.py` is the fence (R7). Its first half
needs no database and never skips. It checks these facts:

- The set is whole, and each value recomputes.
- A row that looks real fails the load.
- Each check fails on a mutation of its answer.
- No module of this folder names a model or a vendor key.

Its second half (R8) replays everything on a real Postgres, as the
non-privileged role. A skip of the second half is not a pass.
