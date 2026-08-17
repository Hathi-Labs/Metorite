# Evals — Metorite harness & skill evaluation (ADR-017, HH-1)

Three complementary layers, from cheapest/most-deterministic to most realistic:

| Layer | What it locks | Needs LLM? | CI |
|---|---|---|---|
| [`trajectories/`](trajectories/) | Harness invariants as golden trajectories: HITL round-trips, stream replay/reconnect semantics, delegation guards, tool-failure recovery, the Workflows engine (compile → run → approval pause → replay-resume) | No | **blocking** (`skill-eval.yml`) |
| [`inspect/scenarios.py`](inspect/scenarios.py) | Skill scenarios scored on the structural contract (citations, JSON shape) via Inspect AI | mockllm smoke in CI; live model locally | blocking (smoke) |
| [`promptfoo.yaml`](promptfoo.yaml) + per-skill `skills/**/evals/cases.yaml` | Golden-case outputs of each skill against a real model | Yes (`LITELLM_BASE_URL`) | opt-in until CI secrets are wired |

## Golden workflow fixtures (`trajectories/workflows/*.json`)

A corpus of whole workflows paired with their expected outcome, executed by one
generic runner (`test_workflow_fixtures.py`). The pattern is n8n's: **adding
coverage is adding a JSON file**, not writing a test.

Each fixture carries its own catalog (agents, module source), its stubs (what
each agent/tool returns), the edit-model graph, the trigger payload, and an
`expect` block. Two shapes:

- `"expect": {"publishable": false, "issues": ["tool_args"]}` — compiling MUST
  fail with those issue codes. These pin the publish gates.
- otherwise — run status, per-node status/output, yielded outputs, and **which
  seams were crossed** (`agent_calls`, `tool_calls`, `tool_args`). That last
  part is the point: a workflow that reports "succeeded" while never calling
  the integration is exactly what a shallower assertion would miss.

Tool argument schemas and destructive-action names come from the **real**
registry, not the fixture — so a fixture breaks when the shipped catalog
changes, which is the early warning you want.

## Layout

```
evals/
  _runner.py            shared promptfoo provider (SKILL.md prompt + fixtures + LiteLLM call)
  promptfoo.yaml        top-level curated golden set (what CI invokes)
  fixtures/entities.json  entity records standing in for graph.read.* results in CI
  inspect/scenarios.py  Inspect AI tasks
  trajectories/         offline pytest golden trajectories (no network, no DB)
```

## Running locally

```bash
# Harness trajectories (fast, offline)
uv run pytest evals/trajectories/ -v

# Inspect smoke (offline) / live
uv run inspect eval evals/inspect/scenarios.py --model mockllm/model --limit 1
uv run inspect eval evals/inspect/scenarios.py --model openai/tier-fast -M base_url=$LITELLM_BASE_URL/v1

# Promptfoo golden cases (needs a reachable LiteLLM endpoint)
npx promptfoo@latest eval --config evals/promptfoo.yaml
npx promptfoo@latest eval --config skills/triage/email_classify/evals/cases.yaml
```

## Conventions

- **One golden case per behaviour**, not per prompt-wording — cases assert the
  contract downstream code depends on (citation tokens, JSON keys, bounds),
  not exact strings.
- **Fixtures over DB**: cases reference stable UUIDs; `fixtures/entities.json`
  supplies the entity data so CI needs no graph database. Add fixture records
  when adding cases.
- **Trajectory tests live here, not in `tests/unit/`**, when they lock a
  cross-component agent-visible behaviour (tool → event → user → tool-result)
  rather than a single helper. Unit conventions still apply (no network/DB,
  `asyncio_mode=auto`).
- No `agents.py` / `SKILL.md` change should merge without a passing golden
  case (project plan §9 Quality Gates).
