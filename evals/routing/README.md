# The routing eval of the one assistant (NS-6 slice 6d)

This folder holds the routing eval of the one assistant. D99 sends every chat
to one general agent, `orchestrator`, and the orchestrator calls the
specialist of each app. This eval asks: for each member question, does the
orchestrator call the right specialist? The owning spec is
`project-docs/specs/navigation_shell.md`, §7.1 and slice 6d of NS-6.

**Status.** Built on 2026-10-14. The scripted run passes. Nobody has run the
live sweep, so there is no baseline yet.

## What it checks

The scripted run calls no model and no network, and CI runs it. It checks
three things against real code:

1. **The catalog.** Each expected agent is in the real registry
   (`gateway.routes.agent._AGENT_REGISTRY`). It has an in-repo factory, and
   it is a tool of the real orchestrator agent. Each app is a live pane of
   `nav.ts`. Each specialist that the orchestrator holds has a case. Each
   live pane that names an agent has a case asked from that pane. Each pair
   of `ALSO_SERVES` has one too.
2. **The delegation.** One case of each expected answer runs through the real
   `run_agent_stream("orchestrator")`. A scripted model makes the choice of
   the case. The run must reach the real factory of that specialist, and its
   model must get the member's words. A case with no specialist must call
   nobody. A case that asks must show a question card, and the harness
   answers it as the member does.
3. **The request.** The first model request of each run must offer
   `call_agent`, `ask_questions` and the specialist's tool. It must hold the
   persona, the page context and the member's words.

⚠️ **A scripted run proves the path, never the choice.** The script makes
the choice. Only the live run measures it.

## What it reuses, and what it adds

The eval drives the real executor, as `projects_ops` and `coding_engine` do.
It cannot reuse their `Harness`, because that harness loads only one agent,
and a delegation loads two. `harness.py` is a harness for many agents. It reuses
`load_agent_module` and `parse_frames` of `tests/unit/_native_maf_harness.py`.

`harness.py` replaces these, and nothing more:

- The loader. It imports each `agents.py` by file path, from a copy of the
  agent dir.
- The dynamic registry and the skill toggles, which read the database. Both
  answer empty.
- The state root, and the scope. The scope covers no run.
- The CRM mode read of run assembly. It answers `native`.
- The Custom Apps granted to an agent. It answers none.
- The Redis relay. The harness records each line with its thread.
- Scripted: the model of each agent. Each other host HTTP send fails the run.
- Live: the specialists. A stand-in records the agent and the message, and
  answers with a fixed line. No specialist runs, writes or spends.

Two reads still try the database: the member's access and the tier
overrides. With no database, as in CI, each one fails closed and logs a
warning.

`shell.py` reads the persona, the pane labels and `ALSO_SERVES` from the
TypeScript of the shell. It ports `surfaceContext` to Python, and the catalog
check fails when a line of that function changes. So the eval sends what the
dock sends: `persona + "\n\n" + surfaceContext`, as `system_context`.

## The files

| File | What it holds |
|---|---|
| `dataset.py` | 27 member questions. Each one has an id, the words, the app, the facts of the page and the expected specialist |
| `shell.py` | The persona, the pane labels and `ALSO_SERVES`, read from the shell. The port of `surfaceContext` |
| `harness.py` | The harness that drives the real orchestrator run and records where it delegated |
| `run.py` | The runner: the catalog checks, the scripted delegation and the live sweep |

## The 27 questions

| Expected | Cases | Asked from |
|---|---|---|
| `projects-assistant` | P1 to P4 | Projects, and My Day |
| `email-assistant` | E1 to E4 | My Email, Projects and CRM |
| `crm-assistant` | C1 to C4 | CRM, My Email and Projects |
| `task-manager` | T1 to T4 | My Tasks, Calendar and CRM |
| `whatsapp-assistant` | W1 to W3 | My WhatsApp and My Email |
| `apis-config` | I1 | Integrations |
| `app-builder` | B1 | Chat |
| The orchestrator itself | N1 to N3 | Chat, My Day and People |
| Ask the member first | A1 to A3 | My Day, Chat and Artifacts |

Six cases are cross-app: the member is in one app and asks about another.
An `asks` case expects no specialist. The right act there is to ask one short
question with `ask_questions`, as the persona says.

Five cases get the catalog check only: T1 to T4 and B1. Their specialists
run on the Copilot SDK, and the sub-run starts the Copilot CLI, which a
hermetic run cannot start.

## Run it with no model

```bash
uv run python -m evals.routing.run --scripted
uv run python -m evals.routing.run --scripted --all
uv run python -m evals.routing.run --scripted --cases P1,A1
```

`--scripted` drives one case of each expected answer. `--all` drives every
case that a script can drive. `--cases` drives only the cases it names. The
catalog check always reads the whole set. The `skill-eval.yml` job runs the
first line.

## Run the live sweep

Do this on a local stack only. A sweep on the production Router spends
credits, so it is an owner gate, as it is for `projects_ops`.

1. Make the local gateway serve `/v1` through the Router. The steps are in
   "The stack that serves the Router" of `maf_coding_engine.md`, WS-43a.
2. Set `LITELLM_BASE_URL` to the local gateway, and set the LLM API key that
   the gateway accepts.
3. Run the sweep:

   ```bash
   uv run python -m evals.routing.run --live --tier tier-balanced
   uv run python -m evals.routing.run --live --tier tier-balanced --repeat 3
   ```

4. Read the table, and commit `baseline.json`. Record the accuracy, the
   date and the SHA in slice 6d of the spec.

Before the first case, the runner checks the Router with
`evals.coding_engine.preflight.router`. The address must be on this machine,
and one call on the tier must answer. If a check fails, the runner exits
with code 2.

The live score of a case:

- A case with a specialist passes when the first delegate of the run is that
  specialist.
- A case with no specialist passes when the run calls nobody.
- An `asks` case passes when the run calls nobody and calls `ask_questions`.
  A question in plain text does not count.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | The scripted run passed, or the live run finished |
| 1 | A catalog check or a scripted run failed |
| 2 | The live run cannot start: the Router check failed |

## The fences

`tests/unit/test_routing_eval.py` is the fence (R7). It runs the scripted
mode. Then it makes each check fail, with one mutation each:

- an unknown agent id, and an agent that leaves the registry
- a specialist with no case, and a preview pane as the app
- a changed line of `surfaceContext`
- a wrong delegate, and a case that must ask and does not
- a message without the member's words
- a page context that the model never sees
- a sub-run that never reaches its agent

It also runs the live path, with a script in place of the Router.

`tests/unit/test_delegated_confirmation_card.py` proves the confirm card of
§7.1. A card of projects-assistant, called by the orchestrator, is on the
parent's stream, and the parent's thread owns it. The member's answer opens
the specialist's gate.

## Findings of the build

- **No live pane names `whatsapp-assistant`.** My WhatsApp is live, and its
  pane in `nav.ts` has no `agent`. So when the orchestrator calls
  `whatsapp-assistant`, the page does not move to My WhatsApp. `apis-config`
  has no pane with an agent either, and App Workshop is a preview pane. The
  runner prints this note.
- **FIXED by #978: the orchestrator's instructions named one customer.**
  `_PULL_INSTRUCTIONS` no longer names a tenant, and
  `tests/unit/test_no_tenant_name_in_prompts.py` fences every shared prompt.
- **Two instructions may disagree.** `_PULL_INSTRUCTIONS` rule 2 says "Do not
  ask the user which agent to use". The persona says "ask one short
  question" when the record is not clear. The live run of the `asks` cases
  measures which one the model follows.
