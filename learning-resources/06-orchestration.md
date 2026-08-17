# 06 · The Orchestration System

This is the heart of the platform — the part that takes an event and turns it into an agent doing work,
safely. If chapter 01's "core loop" was the skeleton, this chapter is the muscle: **event routing → the
dynamic agent loader → running on MAF → streaming → HITL governance → self-mutation on failure.**

---

## 1. What "orchestration" actually means here

Orchestration is *not* the LLM loop (that's chapter 07/08). Orchestration is everything *around* the LLM
loop that makes it a production system:

- deciding **which** agent handles an event,
- **loading** that agent's code and credentials just-in-time,
- **running** it (single agent, or a multi-agent workflow),
- **streaming** every step out to the UI and the audit log,
- **pausing** for human approval on risky actions,
- and **recovering** when the agent's own code throws.

In Metorite this lives in `apps/orchestrator` (the runner) and `packages/acb_skills` (the loader
and tools), fronted by the gateway's `/agent/*` routes.

---

## 2. Event routing — the front of the loop

Work enters three ways, all converging on the same runner:

- **Webhooks** (`/webhooks/clickup`, `/webhooks/zoho`, `/webhooks/gmail`) — an external system fires; the
  receiver validates the signature, drops the event onto a **Redis Stream**, and returns `200` immediately
  (fire-and-forget). Normalisation and agent dispatch happen out-of-band.
- **Cron** — scheduled runs (e.g. the nightly reconciler).
- **Interactive chat** — a person in the Control Plane, via the streaming `/agent/run/stream` endpoint.

The router picks a target agent from event metadata (the agent registry declares `webhook_routes` like
`{"source": "clickup", "event_type": "taskCreated"}`) and calls the runner. **Decoupling ingestion from
execution via a queue** is a deliberate reliability choice: a burst of webhooks can't overwhelm the agent
runtime, and a redelivered webhook can be made idempotent.

---

## 3. The Dynamic Agent Loader — agents as data, not code

The defining architectural move: **agent logic is not in the platform binary.** Each agent is its own
Git repo (`agent-<name>`), and the loader (`acb_skills/loader.py`) fetches and imports it at runtime.

```python
async with load_agent(agent_name, run_id=run_id, repo_name=..., local_path=...) as loaded:
    agents = loaded.build_agents()   # calls the repo's build_agents() → list[Agent]
    agent = agents[0]
```

What `load_agent` does, in order:

1. **Persistent clone cache.** *First* time: `git clone` into `~/.acb/agents/repos/<name>/` (5–20 s).
   *Every subsequent* run: `git pull --ff-only` (~0.5 s). The clone persists across runs and reboots
   (it's under `~`, not `/tmp` — chapter 03).
2. **Install declared deps** (the repo's `requirements.txt` / `pyproject.toml`) into the shared venv.
3. **Unique-name import.** `importlib.import_module` under a *per-run* module name
   (`_agent_<hash>_agents`) so concurrent runs of the same agent never share module state.
4. **Resolve + inject credentials.** The repo's `config.json` lists integrations *by name*
   (`"integrations": ["clickup"]`); the loader pulls those (and only those) from the encrypted
   Integration Registry and hands them to the agent — never via ambient env vars, never from the repo.
5. **Clean up** `sys.path` after the run; the disk clone stays for next time.

**Why this shape matters:** you ship the platform once and evolve behaviour continuously. A new agent, or
a fix to an existing one, is a Git push to *that agent's* repo — no platform redeploy. It's also what
makes self-mutation (§7) possible: the running clone *is* a normal Git working tree the agent can commit
to.

> A subtle guardrail: the loader installs a **pre-push git hook** in each clone that blocks direct
> pushes. Agent-authored commits are held locally and surfaced for human approval before they ever reach
> the remote (see §7). Version control is the audit mechanism.

---

## 4. Running the agent on MAF

With an `Agent` in hand, the runner has two public entry points (`orchestrator/executor.py`):

```python
async def run_agent(name, event_payload, *, run_id=None, thread_id=None, model=None) -> dict          # batch
async def run_agent_stream(name, event_payload, *, run_id=None, thread_id=None, model=None) -> AsyncIterator[str]  # SSE
```

Between load and run, the runner **injects platform tools** onto the agent (`_inject_agent_tools`) — the
cross-agent-delegation, memory, web-search, file-write, and error-checking tools from `acb_skills`. An
agent's `config.json` can set a `tool_scope` to whitelist only the tools it needs; injecting *fewer*
tools measurably improves model accuracy, so scoping is a feature, not a limitation. (Details in
chapter 08.)

Then it runs the agent's think→tool→observe loop (chapter 07) on the **Microsoft Agent Framework**,
translating every internal event into an AG-UI SSE frame (chapter 13). MAF is the *sole* agent runtime;
even Copilot-SDK-backed agents run wrapped inside MAF (chapter 11/12).

---

## 5. Multi-agent orchestration

A single agent is the common case, but MAF provides builders for coordinating several. Metorite's
primary mechanism is **agent-as-tool delegation**: the injected `call_agent(name, message)` /
`call_agents_parallel(...)` tools let an orchestrator agent hand a subtask to a specialist and stream its
progress back. MAF also offers declarative builders for fixed topologies:

- **`HandoffBuilder`** — a triage agent routes to specialists (`triage → sales | ops`).
- **`ConcurrentBuilder`** — fan out to several agents at once, fan in the results.
- **`GroupChatBuilder`** — agents converse to reach an answer (e.g. writer + reviewer).

Choose delegation-as-tool when the orchestrator should *decide dynamically* who to call; choose a builder
when the topology is *known up front*. Sub-agent results are truncated (default 8000 chars) before being
fed back, to keep the parent's context from bloating.

---

## 6. Human-in-the-loop — the governance gate

The platform's founding constraint: **no autonomous writes to source systems.** Every risky action pauses
for a human. The pattern (the "Action Broker" pattern) is deliberately *stateless and durable*:

```
agent decides to write → calls submit_for_approval(action_type, data)
   → row inserted into a Postgres approval queue → the workflow step completes normally
      → Control Plane shows the pending action in the HITL queue
         → human approves → a fresh run reads the approved row and executes the write
```

Because the pending action is a **row in Postgres**, not a suspended process, a paused workflow survives
restarts indefinitely — you don't need a durable-workflow engine for Phase-0 HITL. The same idea drives
two other approval surfaces exposed to the UI as interactive cards (chapter 13):

- **Elicitation** — the agent needs more info (`ask_questions` / `ask_user`) and renders a question card.
- **Confirmation** — the agent wants explicit yes/no before an action.

Everything an agent does is also written to an **append-only audit log**. The same event stream that
renders the live UI *is* the audit trail — observability isn't bolted on afterward.

---

## 7. Self-mutation — recovering from the agent's own bugs

When an agent's code *throws* (not "the LLM was wrong" — an actual exception), the orchestrator can try to
**fix the agent's source**. This is the platform's most distinctive feature; chapter 12 covers the
Copilot-SDK mechanics. The orchestration-level flow (`orchestrator/mutation.py`):

```
agent run raises
   → (first, try _self_anneal: up to 2 in-process retries for transient errors)
   → still failing → attempt_self_mutation():
        • spawn an ephemeral Docker sandbox (acb-mutation-runner) with the failure telemetry
          and the agent's persistent clone mounted at /workspace/repo
        • a Copilot-SDK coding agent reads the error, writes a fix, runs pytest
        • tests pass → commit to the local clone (fix is LIVE next run) + open a PR as an audit record
        • register the commit in a `pending_commit` table for human approve/reject in the Control Plane
   → max_mutation_attempts = 1  (no loops)
```

Two governance rules make this safe rather than terrifying:
- **`max_mutation_attempts = 1` per failure** — a broken fix can't trigger an infinite self-editing loop.
- **A human must merge the PR** (or approve the pending commit) to canonicalize the change; rejecting it
  rolls the local clone back to `origin/main`. The fix is fast (live immediately), but the *canonical*
  change is still gated by a person.

---

## 8. Streaming & resilience: detached runs + Redis relay

Interactive runs are **detached**: the agent runs in a background asyncio task and every SSE frame it
emits is also pushed to a per-thread **Redis Stream** (`cc:stream:{thread_id}`, 1-hour TTL) with an
`cc:active:{thread_id}` flag. Consequences worth copying:

- A **client disconnect doesn't kill the run** — it keeps going, buffered in Redis.
- On reconnect, the frontend **replays missed events** from its last-seen ID; if the stream has expired,
  it falls back to polling the persisted messages in Postgres.

This is what makes a long agent run survive a flaky laptop connection or a page refresh. (See chapter 02
§3 for the frontend half.)

---

## 9. The whole loop, annotated

```
EVENT ─▶ ROUTE ─▶ LOAD ───────────────▶ RUN ──────────────────────▶ STREAM ──▶ GOVERN
webhook  registry  git pull agent repo   MAF think→tool→observe       AG-UI SSE   HITL queue
/cron/   picks     inject creds+tools     (+ call_agent delegation)    +Redis      (Postgres)
chat     agent     unique-name import                                 relay       + audit log
                                              │
                                        code throws?
                                              ▼
                                    self-anneal → self-mutation
                                    (sandbox fixes repo, opens PR,
                                     max 1 attempt, human merges)
```

Every arc of this is a chapter. Next up is the middle box — what actually happens *inside* "RUN":
**[07 · How an Agent Works](./07-agents.md)**.
