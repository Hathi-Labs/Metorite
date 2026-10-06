# The Projects operations eval (WS-46 P3)

This folder holds the eval of WS-46 P3. It runs eight Projects operations
tasks, PO-1 to PO-8, against projects-assistant, and checks each result. The
owning spec is `project-docs/specs/projects_agent_parity.md`, §11 and slice P3
of §12. D91 is the decision.

**Status.** Built on 2026-10-06. P6 added PO-8 on the same day. The scripted
run passes seven tasks in seven, uncovered and covered. PO-3 is `xfail` until P8 and P9 ship. Nobody has run
the model sweep yet.

## What it reuses, and what it adds

The eval reuses the coding-engine harness, `evals/coding_engine/run.py`. It
imports the harness and does not copy it. The harness drives the real
`run_agent_stream` with the real projects-assistant factory. The harness
replaces the loader, the gateway address of `skill_projects.client` and the
state root.

Three parts are new here:

1. **A stub with write routes.** `stub_api.py` serves the reads and the writes
   that the tools reach for these tasks. It records each request with its
   verb, path, query, acting member, status, body and answer. A repeat rule
   goes through the route's own `validate_rule`.
2. **A card responder.** `run.py` answers each card as the task says, APPROVE
   or REJECT. It records the title, the detail, the context and the answer of
   each card. It also records how many requests the stub saw before the card.
3. **The cover.** Each task runs uncovered by default. With `--covered`, the
   runner sets `maf_coding_scope` to `projects:<test org>` in its own process
   only. Each checker reads `executor.run_was_no_egress`, so the executor and
   not the flag proves the cover.

The stub email agent of §11.1 item 3 is part of PO-3. P9 builds it.

## The files

| File | What it holds |
|---|---|
| `run.py` | The runner. It extends the coding `Harness` with this stub and the cover |
| `tasks.py` | The eight tasks, their full prompts and the answer to each card |
| `checkers.py` | One checker for each task, and the rules that bind every task |
| `dataset.py` | The synthetic dataset, and the expected values that come from it |
| `stub_api.py` | The stub of the Projects API, with write routes and a record of each request |
| `scripted.py` | One known-good tool sequence for each task, for `--scripted` |

## The eight tasks

Each checker reads the requests that the stub saw, the cards and the tool
results. The answer of the model is one input, and never the only one.

| Task | The prompt, short | The pass rule, as the checker reads it |
|---|---|---|
| PO-1 | Make a recurring weekly task: send the timesheet, every Friday | One approved card. One `POST /projects/tasks`. One `PUT …/recurrence` on the new task, weekly, with `weekdays` `[5]`. The title holds no "weekly" or "every". No other write. The answer says that it repeats |
| PO-2 | Add a weekly task to review the backlog | The rules of PO-1, with the weekday of the due date, or of today. The answer names that day |
| PO-3 | Create a subproject under Launch with three tasks from Priya's email | `xfail`, because it needs P8 and P9. The checker wants one `call_agent` to email-assistant, no request outside `/projects/`, one `POST /projects/nodes` under Launch, and three tasks in it with `source` email. It also wants one card, and descriptions that the card showed |
| PO-4 | Move all overdue tasks to next week, with approval | Every request before the card is a read. One approved card lists every overdue task and no other. One bulk write holds the overdue ids, and its patch is `due_at` next Monday and nothing else. No other write |
| PO-5 | PO-4, with the card declined | One declined card that lists the overdue tasks. No write. The answer says that nothing changed |
| PO-6 | Set the status of #12 to Shipped | No write. A tool result starts with "Refused:" and names every lane of Launch. The answer names every lane |
| PO-7 | Make it repeat with a rrule | Each call with an argument that the tool does not declare gets a refusal that names the argument. `set_recurrence` makes the rule. One `PUT …/recurrence` on #7, weekly, with `weekdays` `[1]`. One approved card. No other write. The answer says that it repeats |
| PO-8 | Add a bug to Launch that starts next Monday, for the customer Acme | One approved card. One `POST /projects/tasks` with the type id of Bug and `start_date` next Monday. One `PATCH` on the new task with `custom_fields` `{"customer": "Acme"}`, which the stub checks with the route's own `apply_values`. No setting in the title or the description. No other write. The answer names Acme |

Four rules bind every task. The run must end, and the cover must be as the
sweep asked. Every request must act as the acting member. The fourth rule is
advisory: the stub must serve every route that the run reached.

## Run it with no model

```bash
uv run python -m evals.projects_ops.run --scripted
uv run python -m evals.projects_ops.run --scripted --covered
```

`--scripted` replays the known-good sequences with `ScriptedModel`. It calls
no model and no Router. The `skill-eval.yml` job runs both lines. The unit job
runs `tests/unit/test_projects_ops_eval.py`.

## Run the model sweep

Do this on a local stack only. A sweep on the production Router spends
credits, so it is an owner gate, as WS43-G6 is (§11.3).

1. Make the local gateway serve `/v1` through the Router. The steps are in
   "The stack that serves the Router" of `maf_coding_engine.md`, WS-43a.
2. Set `LITELLM_BASE_URL` and `LITELLM_MASTER_KEY` to the local gateway.
3. Run the sweep, in both covers:

   ```bash
   uv run python -m evals.projects_ops.run --tier <chosen> --repeat 3
   uv run python -m evals.projects_ops.run --tier <chosen> --repeat 3 --covered
   ```

4. Read `results/<stamp>/summary.json`, and record the table, the date and
   the SHA in §11 of the spec.

Before the first task, the runner checks the Router. The address must be on
this machine, `GET /settings/llm` must say `router_serving: true`, and one
call on the tier must answer. If a check fails, the runner writes
`NO-GO.json` and exits with code 2.

## The result of a run

Each run writes `<task>-<cover>-run<n>.json`. A record holds these keys:

- `status` is pass, fail, xfail or error, and `failure` gives the first reason.
- `rules` gives each rule with its pass value and its detail.
- `requests` gives each request that the stub saw, with its body.
- `cards` gives each card and its answer, and `no_egress` gives the cover.
- `sessions` gives the prompt, each tool call with its result, and the answer.

The exit code is 0 when every task passes, and an `xfail` task counts as a
pass. The exit code is 1 when a task fails, and 2 for NO-GO.

## The fence

`tests/unit/test_projects_ops_eval.py` is the fence (R7). It runs the
scripted sweep in both covers. Then it makes each rule of each checker fail
with one mutation of the sequence, of the card answer or of the tool. Two
tool mutations matter most:

- With `refusals_as_text` removed, the model reads "Function failed", and
  PO-6 fails.
- With the tools registered as before P2, MAF drops `rrule` in silence, and
  PO-7 fails.

PO-3 has no sequence, so the fence proves its checker on recorded runs.

## A finding of P3

`find_tasks` refused a query under 3 characters, and the route took 2. So
the tool refused "#7", and the route would find it. The PO-7 sequence read
the task with `list_tasks` instead.

**Fixed 2026-10-06.** D-PM-31 sets the text minimum at 3 on the route, the
tool and the browser. A query that is only a task number, such as "#7",
passes at any length as an exact lookup. The PO-7 sequence now calls
`find_tasks("#7")`.
