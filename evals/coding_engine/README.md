# The light eval of the Projects coding tasks (WS-43v)

This folder holds the eval of WS-43v. It runs eight Projects coding tasks
against projects-assistant and checks each result. The owning spec is
`project-docs/specs/maf_coding_engine.md`, the WS-43v slice and §16 (D86).

**Status.** The harness and the checkers are built. The full sweep waits on
WS-43d (PR #603, the sandbox tools) and WS-43u (the instructions). Until both
merge, the runner reports each task as SKIPPED. A skip is never a pass.

## The files

| File | What it holds |
|---|---|
| `run.py` | The runner. It drives the real `run_agent_stream` with the real projects-assistant factory |
| `tasks.py` | The eight tasks, WS43-E10 to WS43-E17, with their full prompts |
| `checkers.py` | One checker for each task, and the hygiene rules that bind every task |
| `dataset.py` | The synthetic Projects dataset, and the expected values that come from it |
| `stub_api.py` | A stub of the Projects API that serves the dataset to the real tools |
| `preflight.py` | Two checks: the sandbox tools, and the Router on the local stack |
| `scripted.py` | One known-good tool sequence for each session, for `--scripted` |
| `fixtures/` | `projects_dataset.json` (synthetic, no real data) and `parts_upload.csv` |

## What the runner replaces

The runner replaces four parts of the stack. In `--scripted` mode it also
replaces the model.

1. **The loader.** It imports `apps/agents/agent-projects/agents.py` by file
   path, as the Dynamic Agent Loader does.
2. **The Projects API.** `skill_projects.client.gateway_url` points at the
   stub. The tools, the route manifest and the member header stay real.
3. **The state root.** `agents_clone_dir` is a new dir under the results, so
   the eval does not touch the files of the local stack.
4. **The scope.** `maf_coding_scope` is `projects:<test org>` in the process
   of the runner only. The runner does not change a setting of the stack.

The stub serves `GET /projects/tree` and `GET /projects/analytics/dataset`.
It keeps the HR gate of the real dataset route. Each other route answers 404.

## The eight tasks

| Task | The pass rule, as the checker reads it |
|---|---|
| WS43-E10 | A valid PNG is in `outputs/<thread>/`, an `artifact_created` card names it, and the answer gives each count of the fixture |
| WS43-E11 | A line that says "median" gives the median of the fixture, to one decimal |
| WS43-E12 | `openpyxl` opens the `.xlsx`, the sheet `Overdue` exists, and it holds the overdue tasks and no other task |
| WS43-E13 | A Markdown table in `outputs/<thread>/` holds each cell of the uploaded CSV |
| WS43-E14 | The answer refuses with the HR rule, no answer line gives a person a time figure, and no file holds a value for a person |
| WS43-E15 | No host tool reached the web, the answer says that the fetch failed, and no holiday list exists |
| WS43-E16 | The run wrote data to `/workspace/.run/`, the run-data dir is gone, and `agent-data/` and `skills/` hold no member data |
| WS43-E17 | A skill is in `agent-data/skills/<name>/SKILL.md`, the same member uses it in a new session, and that session writes a PNG |

The hygiene rules of WS43-E16 bind every task.

⚠️ **WS43-E17 has one advisory rule.** A third session, by another member of
the same organization, asks for the burndown too. The rule reports whether
that session loads the skill. It does not fail the task, because no spec rule
decides it. On PR #603 the whole organization shares the skills of a tenant
dir.

## Run the harness with no model

`--scripted` replays the known-good sequences with `ScriptedModel`. It calls
no model and no Router.

```bash
uv run python -m evals.coding_engine.run --scripted --tasks all --repeat 1
```

Without the sandbox tools, the runner skips each task, and the exit code is 3.

## Run the full sweep

Do these steps on a local stack. Do not use the production Router or a
production key. That is owner gate WS43-G6.

1. Make sure that PR #603 (WS-43d) and the WS-43u change are on your branch.
2. Do the six steps of "The stack that serves the Router" in WS-43a of the
   spec. Then the gateway serves `/v1` through the Router.
3. Build the coding image, and set `SANDBOX_IMAGE` to its image ID:

   ```bash
   docker build --iidfile coding.iid -f apps/services/orchestrator/Dockerfile.coding-sandbox apps/services/orchestrator
   export SANDBOX_IMAGE="$(cat coding.iid)"
   ```

4. Set `LITELLM_BASE_URL` and `LITELLM_MASTER_KEY` to the local gateway, as
   the gateway uses them.
5. Run the sweep on the tier that you chose:

   ```bash
   uv run python -m evals.coding_engine.run --engine maf --agent projects-assistant --tier <chosen> --tasks WS43-E10..WS43-E17 --repeat 3
   ```

6. Read `results/<stamp>/summary.json`. Record the table, the date and the SHA
   in the WS-43v section of the spec. Name each failure in the PR.

**NO-GO.** Before the first task, the runner checks the Router. It needs an
address on this machine, `router_serving: true` from `GET /settings/llm`, and
one answer on the tier. If a check fails, the runner writes `NO-GO.json`,
prints the step, and exits with code 2. Record the step in the spec.

**The test organization.** `--org` names it. The default is the id in the
fixture. If your local database has the blob store, use the id of a seeded
test organization, so the mirror can write.

## The result of a run

Each run writes `<task>-run<n>.json`. A record holds these keys:

- `status` is pass, fail, skipped or error, and `failure` gives the first reason.
- `rules` gives each rule with its pass value and its detail.
- `wall_s`, `tool_calls`, `failed_tool_calls` and `model_calls` give the cost.
- `tokens` gives the usage that the Router reported, or `null` with no report.
- `cards_declined` and `approvals` count the cards. The eval approves nothing.
- `sessions` gives each prompt, member, thread, tool call and answer.

The exit code is 0 when every task passes, and 1 when a task fails. It is 2
for NO-GO, and 3 when the runner skipped a task and no task failed.

## The tests

| Test | What it proves | Where it runs |
|---|---|---|
| `tests/unit/test_coding_eval_checkers.py` | WS43-F11. Each checker passes a right output and fails a wrong one | The unit job |
| `tests/unit/test_coding_eval_harness.py` | The stub keeps the HR gate. The preflight never passes a run that it cannot judge. Scripted runs through the real executor skip, pass or fail as they must. The eval declines every card | The unit job |
| `tests/unit/test_coding_eval_scripts_docker.py` | Each known-good sequence runs in the coding image with no network, and its checker passes | `sandbox-docker.yml` |

⚠️ **Docker Desktop on Windows.** A bind mount of a host dir can fail with
"input/output error". Then the Docker test fails on the dev box, and passes
on the Linux runner. PR #603 met the same limit.
